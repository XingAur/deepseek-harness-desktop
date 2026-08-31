from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Protocol

from app import database
from app.manager_credential_crypto import CIPHER_VERSION, credential_aad
from app.manager_provider_repository import (
    CredentialResolutionUnavailable,
    ManagerProviderRepository,
)
from app.provider_action_authorization import (
    ProviderActionAuthorization,
    ProviderActionAuthorizer,
    canonical_json_hash,
)
from app.sensitive_text import contains_sensitive_text, redact_sensitive_mapping


PROVIDER_EXECUTION_RESULT_SCHEMA_VERSION = "his-provider-execution-result.v1"
ProviderActionRisk = Literal["read", "local_mutation", "remote_write", "model_smoke"]
ProviderReadBackResult = bool | Literal[
    "verified_applied", "verified_not_applied", "unknown"
]
ProviderWriteEffectStatus = Literal[
    "not_attempted",
    "not_applicable",
    "verified_applied",
    "verified_not_applied",
    "unknown",
]

_CREDENTIAL_MATCH_DOMAIN = b"his-provider-credential-match.v1\0"
_CREDENTIAL_REDACTION_MAX_DEPTH = 16
_CREDENTIAL_REDACTION_ITEM_BUDGET = 4_096
_REDACTED_CREDENTIAL_VALUE = "REDACTED"
GITLAB_CODE_EVIDENCE_READ_ACTIONS = frozenset((
    "gitlab.repository.file.read",
    "gitlab.commit.read",
    "gitlab.commit.diff.read",
    "gitlab.compare.read",
    "gitlab.merge_request.commits.read",
    "gitlab.merge_request.diffs.read",
    "gitlab.pipeline.jobs.read",
))
GITHUB_CODE_EVIDENCE_READ_ACTIONS = frozenset((
    "github.repository.file.read",
    "github.commit.read",
    "github.commit.diff.read",
    "github.compare.read",
    "github.pull_request.commits.read",
    "github.pull_request.diffs.read",
    "github.actions.run.jobs.read",
))


@dataclass(frozen=True)
class ProviderActionDescriptor:
    action: str
    provider: str
    risk: ProviderActionRisk
    max_timeout_seconds: int
    max_result_bytes: int
    required_credential_fields: tuple[str, ...]
    read_back_verifier: str | None
    network_allowed: bool


@dataclass(frozen=True)
class ProviderExecutionRequest:
    plan_id: int
    actor: str
    action: str
    parameters: Mapping[str, object]


class ProviderExecutionContext:
    """Capabilities granted to one adapter only after authorization consumption."""

    __slots__ = (
        "_credential_fingerprints",
        "_credential_resolver",
        "_credential_resolver_uses_context",
        "_credential_resolver_called",
        "_profile_key",
        "_profile_connection",
        "_profile_id",
        "_read_back_references",
        "_required_credential_fields",
        "_network_dispatches",
        "_network_dispatch_incidents",
        "_local_mutation_unknown",
        "_authorization_consumed",
        "network_allowed",
    )

    def __init__(
        self,
        *,
        profile_id: int,
        profile_key: str = "",
        profile_connection: Mapping[str, object] | None = None,
        required_credential_fields: tuple[str, ...],
        network_allowed: bool,
        credential_resolver: Callable[[int, str], str] | None = None,
        execution_credential_resolver: (
            Callable[["ProviderExecutionContext", int, str], str] | None
        ) = None,
    ) -> None:
        if credential_resolver is not None and execution_credential_resolver is not None:
            raise ValueError("provider_credential_resolver_ambiguous")
        self._profile_id = profile_id
        self._profile_key = profile_key
        self._profile_connection = dict(profile_connection or {})
        self._required_credential_fields = required_credential_fields
        self.network_allowed = network_allowed
        # The legacy two-argument resolver remains for isolated fake adapter
        # tests.  Production receives a locally created three-argument closure
        # that independently verifies this Context's active identity.
        self._credential_resolver = execution_credential_resolver or (
            credential_resolver
            if credential_resolver is not None
            else _credential_resolution_unavailable
        )
        self._credential_resolver_uses_context = execution_credential_resolver is not None
        self._credential_fingerprints: set[bytes] = set()
        self._credential_resolver_called = False
        self._read_back_references: dict[str, str] = {}
        self._network_dispatches: list[tuple[str, bool]] = []
        self._network_dispatch_incidents: list[str] = []
        self._local_mutation_unknown = False
        self._authorization_consumed = False

    @property
    def required_credential_fields(self) -> tuple[str, ...]:
        return self._required_credential_fields

    @property
    def profile_id(self) -> int:
        """The already-authorized Profile identity, never its credential value."""

        return self._profile_id

    @property
    def profile_key(self) -> str:
        """The authorization-bound Manager Profile alias, never a credential."""

        return self._profile_key

    @property
    def profile_connection(self) -> dict[str, object]:
        """Return the public typed connection fields bound to this execution."""

        return dict(self._profile_connection)

    @property
    def credential_resolver_called(self) -> bool:
        return self._credential_resolver_called

    def credential(self, field: str) -> str:
        if field not in self._required_credential_fields:
            raise PermissionError("provider_credential_field_not_authorized")
        self._credential_resolver_called = True
        if self._credential_resolver_uses_context:
            value = self._credential_resolver(self, self._profile_id, field)
        else:
            value = self._credential_resolver(self._profile_id, field)
        self.register_resolved_credential(field, value)
        return value

    def register_resolved_credential(self, field: str, value: str) -> None:
        """Retain only a match digest for a value resolved in this Context."""

        if field not in self._required_credential_fields:
            raise PermissionError("provider_credential_field_not_authorized")
        if not isinstance(value, str) or not value:
            raise RuntimeError("provider_credential_unavailable")
        self._credential_resolver_called = True
        self._credential_fingerprints.add(_credential_fingerprint(value))

    def redact_resolved_credentials(self, value: object) -> object:
        """Remove exact values resolved by this Context without retaining them."""

        if not self._credential_fingerprints:
            return value
        return _redact_resolved_credential_values(
            value,
            fingerprints=frozenset(self._credential_fingerprints),
            depth=0,
            budget=[_CREDENTIAL_REDACTION_ITEM_BUDGET],
        )

    def _matches_resolved_credential(self, value: object) -> bool:
        if not self._credential_fingerprints or not isinstance(value, str):
            return False
        try:
            return _credential_fingerprint(value) in self._credential_fingerprints
        except UnicodeEncodeError:
            return True

    def set_read_back_reference(self, action: str, reference: str) -> None:
        """Keep a safe write receipt in memory only; it is never audited."""

        if not isinstance(action, str) or not isinstance(reference, str):
            raise TypeError("provider_read_back_reference_invalid")
        self._read_back_references[action] = reference

    def read_back_reference(self, action: str) -> str:
        return self._read_back_references.get(action, "")

    def validate_network_target(self, target_alias: str) -> str:
        """Validate an audit identity before a provider can start a process."""

        if not self.network_allowed:
            raise PermissionError("provider_network_not_allowed")
        if self._matches_resolved_credential(target_alias):
            raise ValueError("provider_network_target_contains_resolved_credential")
        gitlab_identity = isinstance(target_alias, str) and bool(re.fullmatch(r"gl-h[1-9][0-9]*-[a-z0-9_-]+-g[1-9][0-9]*-[a-z0-9._-]+-p[1-9][0-9]*-[a-z0-9._-]+(?:-m[1-9][0-9]*)?", target_alias))
        github_identity = isinstance(target_alias, str) and bool(re.fullmatch(r"gh-o[1-9][0-9]*-[a-z0-9-]+-r[1-9][0-9]*-[a-z0-9._-]+(?:-[piw][1-9][0-9]*)?", target_alias))
        if (not isinstance(target_alias, str) or target_alias.startswith("gitlab.") or not re.fullmatch(r"[a-z][a-z0-9._-]{0,127}", target_alias)
                or (not gitlab_identity and not github_identity and contains_sensitive_text(target_alias))):
            raise ValueError("provider_network_target_invalid")
        return target_alias

    def record_network_dispatch(self, target_alias: str, *, simulated: bool) -> None:
        self.validate_network_target(target_alias)
        self._network_dispatches.append((target_alias, bool(simulated)))

    def record_network_dispatch_incident(self, target_alias: str) -> None:
        """Preserve the fact that a started live process could not be audited."""

        self.validate_network_target(target_alias)
        self._network_dispatch_incidents.append(target_alias)

    def mark_local_mutation_unknown(self) -> None:
        """A local publish phase started; failures can no longer mean no write."""

        self._local_mutation_unknown = True

    def mark_authorization_consumed(self) -> None:
        """Mark the one-use Provider authorization consumed for this context."""

        self._authorization_consumed = True

    @property
    def authorization_consumed(self) -> bool:
        return self._authorization_consumed

    def clear_local_mutation_unknown(self) -> None:
        self._local_mutation_unknown = False

    @property
    def network_call_count(self) -> int:
        return sum(1 for _target, simulated in self._network_dispatches if not simulated)

    @property
    def simulated_dispatch_count(self) -> int:
        return sum(1 for _target, simulated in self._network_dispatches if simulated)

    @property
    def network_dispatch_incident_count(self) -> int:
        return len(self._network_dispatch_incidents)

    @property
    def local_mutation_unknown(self) -> bool:
        return self._local_mutation_unknown

    @property
    def network_targets(self) -> tuple[str, ...]:
        raw_targets = tuple(
            target for target, _simulated in self._network_dispatches
        ) + tuple(self._network_dispatch_incidents)
        # A target can be recorded before an adapter resolves its credential.
        # Re-evaluate the accumulated metadata on every read so a later
        # resolution cannot turn an earlier ordinary value into an output leak.
        return tuple(
            value
            if isinstance(value := self.redact_resolved_credentials(target), str)
            else _REDACTED_CREDENTIAL_VALUE
            for target in raw_targets
        )

    @property
    def network_simulated(self) -> bool:
        return bool(self._network_dispatches) and all(simulated for _target, simulated in self._network_dispatches)


def _credential_fingerprint(value: str | bytes) -> bytes:
    encoded = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(_CREDENTIAL_MATCH_DOMAIN + encoded).digest()


def _redact_resolved_credential_values(
    value: object,
    *,
    fingerprints: frozenset[bytes],
    depth: int,
    budget: list[int],
) -> object:
    """Recursively redact values that exactly match this Context's credentials.

    The Context keeps only digests, so this traversal cannot reconstruct a
    credential while it protects result summaries and ephemeral query rows.
    """

    budget[0] -= 1
    if depth > _CREDENTIAL_REDACTION_MAX_DEPTH or budget[0] < 0:
        return _REDACTED_CREDENTIAL_VALUE
    if isinstance(value, str):
        try:
            return (
                _REDACTED_CREDENTIAL_VALUE
                if _credential_fingerprint(value) in fingerprints
                else value
            )
        except UnicodeEncodeError:
            return _REDACTED_CREDENTIAL_VALUE
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            raw = bytes(value)
            return (
                _REDACTED_CREDENTIAL_VALUE
                if _credential_fingerprint(raw) in fingerprints
                else value
            )
        except (TypeError, ValueError):
            return _REDACTED_CREDENTIAL_VALUE
    if isinstance(value, Mapping):
        result: dict[object, object] = {}
        for key, item in value.items():
            safe_key = _redact_resolved_credential_values(
                key,
                fingerprints=fingerprints,
                depth=depth + 1,
                budget=budget,
            )
            result[safe_key] = _redact_resolved_credential_values(
                item,
                fingerprints=fingerprints,
                depth=depth + 1,
                budget=budget,
            )
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _redact_resolved_credential_values(
                item,
                fingerprints=fingerprints,
                depth=depth + 1,
                budget=budget,
            )
            for item in value
        ]
    return value


class ProviderAdapter(Protocol):
    def execute(
        self,
        request: ProviderExecutionRequest,
        context: ProviderExecutionContext,
    ) -> Mapping[str, object]: ...

    def verify(
        self,
        verifier_action: str,
        original_write_action: str,
        request: ProviderExecutionRequest,
        target_alias: str,
        context: ProviderExecutionContext,
    ) -> ProviderReadBackResult: ...


def _descriptor(
    action: str,
    risk: ProviderActionRisk,
    *,
    timeout: int,
    result_bytes: int,
    credentials: tuple[str, ...] = (),
    verifier: str | None = None,
    network: bool = True,
) -> ProviderActionDescriptor:
    return ProviderActionDescriptor(
        action=action,
        provider=_provider_for_action(action),
        risk=risk,
        max_timeout_seconds=timeout,
        max_result_bytes=result_bytes,
        required_credential_fields=credentials,
        read_back_verifier=verifier,
        network_allowed=network,
    )


def _provider_for_action(action: str) -> str:
    for provider, prefixes in (
        ("yunxiao", ("yunxiao.", "workitem.")),
        ("gitlab", ("gitlab.", "project.", "merge_request.")),
        ("github", ("github.",)),
        ("git", ("git.", "repo.", "branch.", "commit.", "remote.", "reset.", "cherry-pick.", "merge.")),
        ("database", ("database.",)),
        ("model", ("model.",)),
        ("knowledge", ("knowledge.",)),
    ):
        if action.startswith(prefixes):
            return provider
    raise ValueError("provider action has no registered provider")


ACTION_DESCRIPTORS = MappingProxyType(
    {
        # Connection health and local smoke seams.
        "yunxiao.connection_test": _descriptor(
            "yunxiao.connection_test", "read", timeout=10, result_bytes=16_384,
            credentials=("pat",),
        ),
        "git.connection_test": _descriptor(
            "git.connection_test", "read", timeout=5, result_bytes=16_384,
            network=False,
        ),
        "gitlab.connection_test": _descriptor(
            "gitlab.connection_test", "read", timeout=10, result_bytes=16_384,
            credentials=("access_token",),
        ),
        "github.connection_test": _descriptor(
            "github.connection_test", "read", timeout=10, result_bytes=16_384,
            credentials=("access_token",),
        ),
        "database.connection_test": _descriptor(
            "database.connection_test", "read", timeout=10, result_bytes=16_384,
            credentials=("password",),
        ),
        "model.connection_test": _descriptor(
            "model.connection_test", "model_smoke", timeout=30, result_bytes=32_768,
            credentials=("api_key",),
        ),
        "knowledge.connection_test": _descriptor(
            "knowledge.connection_test", "read", timeout=5, result_bytes=16_384,
            network=False,
        ),
        "git.readonly_smoke": _descriptor(
            "git.readonly_smoke", "read", timeout=5, result_bytes=32_768,
            network=False,
        ),
        # Stage B/C adapters register against these fixed actions in later tasks.
        "workitem.read": _descriptor(
            "workitem.read", "read", timeout=15, result_bytes=65_536,
            credentials=("pat",),
        ),
        "workitem.comments.read": _descriptor(
            "workitem.comments.read", "read", timeout=15, result_bytes=65_536,
            credentials=("pat",),
        ),
        "workitem.comment.write": _descriptor(
            "workitem.comment.write", "remote_write", timeout=15, result_bytes=32_768,
            credentials=("pat",), verifier="workitem.comments.read",
        ),
        "workitem.owner.update": _descriptor(
            "workitem.owner.update", "remote_write", timeout=15, result_bytes=32_768,
            credentials=("pat",), verifier="workitem.read",
        ),
        "workitem.status.update": _descriptor(
            "workitem.status.update", "remote_write", timeout=15, result_bytes=32_768,
            credentials=("pat",), verifier="workitem.read",
        ),
        "repo.status.read": _descriptor(
            "repo.status.read", "read", timeout=5, result_bytes=65_536, network=False,
        ),
        "repo.log.read": _descriptor(
            "repo.log.read", "read", timeout=5, result_bytes=65_536, network=False,
        ),
        "repo.diff.read": _descriptor(
            "repo.diff.read", "read", timeout=10, result_bytes=131_072, network=False,
        ),
        "branch.create": _descriptor(
            "branch.create", "local_mutation", timeout=5, result_bytes=16_384,
            verifier="repo.status.read", network=False,
        ),
        "commit.create": _descriptor(
            "commit.create", "local_mutation", timeout=15, result_bytes=16_384,
            verifier="repo.log.read", network=False,
        ),
        "remote.fetch": _descriptor(
            "remote.fetch", "local_mutation", timeout=30, result_bytes=32_768,
        ),
        "remote.push": _descriptor(
            "remote.push", "remote_write", timeout=30, result_bytes=32_768,
        ),
        # A mutation plan is deliberately read-only.  It validates the exact
        # reset/cherry-pick/merge/pull/push scope but never invokes Git.
        "git.operation.plan": _descriptor(
            "git.operation.plan", "read", timeout=5, result_bytes=65_536,
            network=False,
        ),
        "reset.local": _descriptor(
            "reset.local", "local_mutation", timeout=30, result_bytes=32_768,
            verifier="repo.status.read", network=False,
        ),
        "cherry-pick.local": _descriptor(
            "cherry-pick.local", "local_mutation", timeout=30, result_bytes=32_768,
            verifier="repo.status.read", network=False,
        ),
        "merge.local": _descriptor(
            "merge.local", "local_mutation", timeout=30, result_bytes=32_768,
            verifier="repo.status.read", network=False,
        ),
        "project.read": _descriptor(
            "project.read", "read", timeout=15, result_bytes=65_536,
            credentials=("access_token",),
        ),
        "merge_request.read": _descriptor(
            "merge_request.read", "read", timeout=15, result_bytes=65_536,
            credentials=("access_token",),
        ),
        "github.repository.read": _descriptor(
            "github.repository.read", "read", timeout=15, result_bytes=65_536,
            credentials=("access_token",),
        ),
        "github.issue.read": _descriptor(
            "github.issue.read", "read", timeout=15, result_bytes=65_536,
            credentials=("access_token",),
        ),
        "github.pull_request.read": _descriptor(
            "github.pull_request.read", "read", timeout=15, result_bytes=65_536,
            credentials=("access_token",),
        ),
        "github.repository.file.read": _descriptor(
            "github.repository.file.read", "read", timeout=20,
            result_bytes=65_536, credentials=("access_token",),
        ),
        "github.commit.read": _descriptor(
            "github.commit.read", "read", timeout=15,
            result_bytes=65_536, credentials=("access_token",),
        ),
        "github.commit.diff.read": _descriptor(
            "github.commit.diff.read", "read", timeout=20,
            result_bytes=65_536, credentials=("access_token",),
        ),
        "github.compare.read": _descriptor(
            "github.compare.read", "read", timeout=20,
            result_bytes=65_536, credentials=("access_token",),
        ),
        "github.pull_request.commits.read": _descriptor(
            "github.pull_request.commits.read", "read", timeout=20,
            result_bytes=65_536, credentials=("access_token",),
        ),
        "github.pull_request.diffs.read": _descriptor(
            "github.pull_request.diffs.read", "read", timeout=20,
            result_bytes=65_536, credentials=("access_token",),
        ),
        "github.actions.run.jobs.read": _descriptor(
            "github.actions.run.jobs.read", "read", timeout=20,
            result_bytes=65_536, credentials=("access_token",),
        ),
        "github.pull_request.comment.write": _descriptor(
            "github.pull_request.comment.write", "remote_write", timeout=15,
            result_bytes=32_768, credentials=("access_token",),
            verifier="github.pull_request.read",
        ),
        "github.pull_request.create": _descriptor(
            "github.pull_request.create", "remote_write", timeout=20,
            result_bytes=32_768, credentials=("access_token",),
            verifier="github.pull_request.read",
        ),
        "gitlab.repository.file.read": _descriptor(
            "gitlab.repository.file.read", "read", timeout=20,
            result_bytes=65_536, credentials=("access_token",),
        ),
        "gitlab.commit.read": _descriptor(
            "gitlab.commit.read", "read", timeout=15,
            result_bytes=65_536, credentials=("access_token",),
        ),
        "gitlab.commit.diff.read": _descriptor(
            "gitlab.commit.diff.read", "read", timeout=20,
            result_bytes=65_536, credentials=("access_token",),
        ),
        "gitlab.compare.read": _descriptor(
            "gitlab.compare.read", "read", timeout=20,
            result_bytes=65_536, credentials=("access_token",),
        ),
        "gitlab.merge_request.commits.read": _descriptor(
            "gitlab.merge_request.commits.read", "read", timeout=20,
            result_bytes=65_536, credentials=("access_token",),
        ),
        "gitlab.merge_request.diffs.read": _descriptor(
            "gitlab.merge_request.diffs.read", "read", timeout=20,
            result_bytes=65_536, credentials=("access_token",),
        ),
        "gitlab.pipeline.jobs.read": _descriptor(
            "gitlab.pipeline.jobs.read", "read", timeout=20,
            result_bytes=65_536, credentials=("access_token",),
        ),
        "merge_request.comment.write": _descriptor(
            "merge_request.comment.write", "remote_write", timeout=15,
            result_bytes=32_768, credentials=("access_token",),
            verifier="merge_request.read",
        ),
        "merge_request.create": _descriptor(
            "merge_request.create", "remote_write", timeout=20,
            result_bytes=32_768, credentials=("access_token",),
            verifier="merge_request.read",
        ),
        "database.schema.read": _descriptor(
            "database.schema.read", "read", timeout=10, result_bytes=65_536,
            credentials=("password",),
        ),
        "database.query.read": _descriptor(
            "database.query.read", "read", timeout=10, result_bytes=65_536,
            credentials=("password",),
        ),
        "model.single_node.smoke": _descriptor(
            "model.single_node.smoke", "model_smoke", timeout=30,
            result_bytes=32_768, credentials=("api_key",),
        ),
    }
)


class ProviderExecutionService:
    """The only boundary allowed to turn an authorization into adapter capabilities."""

    def __init__(
        self,
        repository: ManagerProviderRepository,
        authorizer: ProviderActionAuthorizer,
        *,
        adapters: Mapping[str, ProviderAdapter] | None = None,
        credential_resolver: Callable[[int, str], str] | None = None,
        action_descriptors: Mapping[str, ProviderActionDescriptor] = ACTION_DESCRIPTORS,
    ) -> None:
        self._repository = repository
        self._authorizer = authorizer
        self._adapters = dict(adapters or {})
        # Test adapters can inject an in-memory resolver.  Production always
        # binds Manager decryption to a just-consumed plan below.
        self._credential_resolver = credential_resolver
        self._action_descriptors = dict(action_descriptors)

    def execute(
        self,
        authorization: ProviderActionAuthorization | None,
        request: ProviderExecutionRequest,
    ) -> dict[str, object]:
        if not isinstance(request, ProviderExecutionRequest):
            raise TypeError("request must be a ProviderExecutionRequest")
        plan = self._authorizer.get_plan(request.plan_id)
        if authorization is not None and not isinstance(
            authorization, ProviderActionAuthorization
        ):
            decision = self._authorizer.consume(
                plan_id=plan.id,
                authorization=authorization,  # type: ignore[arg-type]
                actor=request.actor,
                parameters=request.parameters,
            )
            return self._record_result(
                plan=plan,
                descriptor=self._action_descriptors.get(request.action),
                status="blocked",
                reason=decision.reason,
            )
        descriptor = self._action_descriptors.get(request.action)
        if descriptor is None:
            return self._record_result(
                plan=plan, descriptor=None, status="blocked",
                reason="provider_action_not_registered",
            )
        if request.action != plan.action:
            return self._record_result(
                plan=plan, descriptor=descriptor, status="blocked",
                reason="provider_action_plan_mismatch",
            )
        if descriptor.provider != plan.provider:
            return self._record_result(
                plan=plan, descriptor=descriptor, status="blocked",
                reason="provider_action_provider_mismatch",
            )
        adapter = self._adapters.get(plan.provider)
        if adapter is None:
            return self._record_result(
                plan=plan, descriptor=descriptor, status="blocked",
                reason="provider_adapter_not_registered",
            )
        try:
            profile_status = self._repository.profile_status(plan.profile_id)
            profile_enabled = profile_status.get("enabled") is True
        except Exception:
            return self._record_result(
                plan=plan, descriptor=descriptor, status="blocked",
                reason="provider_profile_unavailable",
            )
        if not profile_enabled:
            return self._record_result(
                plan=plan, descriptor=descriptor, status="blocked",
                reason="provider_profile_disabled",
            )
        bound_target_alias = _bound_target_alias(adapter, plan.target_alias, request)
        if bound_target_alias is None:
            return self._record_result(
                plan=plan,
                descriptor=descriptor,
                status="blocked",
                reason="provider_target_mismatch",
            )
        if not _profile_target_is_bound(adapter, plan.profile_id, bound_target_alias):
            return self._record_result(
                plan=plan, descriptor=descriptor, status="blocked",
                reason="provider_target_mismatch",
            )
        from app.providers.git import REPOSITORY_BOUND_GIT_ACTIONS

        if request.action == "model.single_node.smoke":
            from app.providers.model_smoke import (
                ManagerModelSmokeProviderAdapter,
                validate_model_smoke_parameters,
            )

            if isinstance(adapter, ManagerModelSmokeProviderAdapter):
                try:
                    validate_model_smoke_parameters(
                        target_alias=bound_target_alias,
                        parameters=request.parameters,
                    )
                except ValueError:
                    return self._record_result(
                        plan=plan,
                        descriptor=descriptor,
                        status="blocked",
                        reason="provider_parameters_invalid",
                    )

        if request.action in REPOSITORY_BOUND_GIT_ACTIONS:
            # Re-check the pure Git input/target contract after adapter target
            # binding but before the one-use authorization can be consumed.
            # This protects previously persisted plans if their request input is
            # stale or malformed under a stricter Git ref grammar.
            try:
                from app.providers.git import validate_git_action_parameters

                validate_git_action_parameters(
                    request.action, bound_target_alias, request.parameters
                )
            except ValueError:
                return self._record_result(
                    plan=plan,
                    descriptor=descriptor,
                    status="blocked",
                    reason="provider_parameters_invalid",
                )
        timeout = request.parameters.get("timeout_seconds")
        if (
            timeout is not None
            and (
                not isinstance(timeout, int)
                or isinstance(timeout, bool)
                or timeout < 1
                or timeout > descriptor.max_timeout_seconds
            )
        ):
            return self._record_result(
                plan=plan, descriptor=descriptor, status="blocked",
                reason="provider_timeout_not_allowed",
            )

        decision = self._authorizer.consume(
            plan_id=plan.id,
            authorization=authorization,
            actor=request.actor,
            parameters=request.parameters,
        )
        if not decision.allowed:
            return self._record_result(
                plan=plan, descriptor=descriptor, status="blocked",
                reason=decision.reason,
            )

        # This grant and its closure are created only after `consume()` has
        # succeeded.  Neither is stored on the repository/service, and the
        # closure rejects calls once the execution finally block revokes it.
        execution_grant = object()
        active_grant: list[object | None] = [execution_grant]
        active_context: list[ProviderExecutionContext | None] = [None]

        def revoke_credential_grant() -> None:
            active_context[0] = None
            active_grant[0] = None

        injected_resolver = self._credential_resolver
        if injected_resolver is not None:
            def execution_credential_resolver(
                requesting_context: ProviderExecutionContext,
                bound_profile_id: int,
                field: str,
            ) -> str:
                if (
                    active_grant[0] is not execution_grant
                    or requesting_context is not active_context[0]
                    or bound_profile_id != plan.profile_id
                    or field not in descriptor.required_credential_fields
                ):
                    raise CredentialResolutionUnavailable(
                        "credential_resolution_unavailable"
                    )
                try:
                    value = injected_resolver(bound_profile_id, field)
                except Exception:
                    raise CredentialResolutionUnavailable(
                        "credential_resolution_unavailable"
                    ) from None
                if not isinstance(value, str) or not value:
                    raise CredentialResolutionUnavailable(
                        "credential_resolution_unavailable"
                    )
                if (
                    active_grant[0] is not execution_grant
                    or requesting_context is not active_context[0]
                ):
                    raise CredentialResolutionUnavailable(
                        "credential_resolution_unavailable"
                    )
                requesting_context.register_resolved_credential(field, value)
                return value
        elif descriptor.required_credential_fields:
            def execution_credential_resolver(
                requesting_context: ProviderExecutionContext,
                bound_profile_id: int,
                field: str,
            ) -> str:
                if (
                    active_grant[0] is not execution_grant
                    or requesting_context is not active_context[0]
                    or bound_profile_id != plan.profile_id
                    or field not in descriptor.required_credential_fields
                ):
                    raise CredentialResolutionUnavailable(
                        "credential_resolution_unavailable"
                    )
                try:
                    profile = self._repository._profile_by_id(plan.profile_id)
                    if (
                        not profile.enabled
                        or profile.provider != descriptor.provider
                        or profile.id != plan.profile_id
                    ):
                        raise ValueError("credential profile is unavailable")
                    with database.connect() as db:
                        row = db.execute(
                            """
                            select cipher_version, ciphertext
                            from manager_provider_credentials
                            where profile_id = ? and credential_field = ?
                            """,
                            (profile.id, field),
                        ).fetchone()
                    if row is None or str(row["cipher_version"]) != CIPHER_VERSION:
                        raise ValueError("credential is unavailable")
                    ciphertext = row["ciphertext"]
                    if not isinstance(ciphertext, str) or not ciphertext:
                        raise ValueError("credential is unavailable")
                    plaintext = self._repository._credential_cipher().decrypt(
                        ciphertext,
                        aad=credential_aad(
                            scope_type=profile.scope_type,
                            scope_key=profile.scope_key,
                            provider=profile.provider,
                            profile_key=profile.profile_key,
                            field=field,
                        ),
                    )
                    if not plaintext:
                        raise ValueError("credential is unavailable")
                    if (
                        active_grant[0] is not execution_grant
                        or requesting_context is not active_context[0]
                    ):
                        raise ValueError("credential resolver is no longer active")
                    requesting_context.register_resolved_credential(field, plaintext)
                    return plaintext
                except Exception:
                    # 桌面宿主注入回退：Core 自己的加密凭证库没有该字段时，从受信
                    # 的宿主进程环境取值（与 DeepSeek key 同一通道）。取值仍发生在
                    # 授权发放之内，指纹登记照常，不会绕过审计。
                    env_value = _desktop_env_credential(descriptor.provider, field)
                    if env_value:
                        requesting_context.register_resolved_credential(field, env_value)
                        return env_value
                    raise CredentialResolutionUnavailable(
                        "credential_resolution_unavailable"
                    ) from None
        else:
            execution_credential_resolver = None
            revoke_credential_grant()

        context = ProviderExecutionContext(
            profile_id=plan.profile_id,
            profile_key=plan.profile_key,
            profile_connection=(
                profile_status.get("connection")
                if isinstance(profile_status.get("connection"), Mapping)
                else {}
            ),
            required_credential_fields=descriptor.required_credential_fields,
            network_allowed=descriptor.network_allowed,
            execution_credential_resolver=execution_credential_resolver,
        )
        context.mark_authorization_consumed()
        active_context[0] = context
        try:
            return self._execute_consumed_context(
                plan=plan,
                descriptor=descriptor,
                adapter=adapter,
                request=request,
                bound_target_alias=bound_target_alias,
                context=context,
            )
        finally:
            revoke_credential_grant()

    def _execute_consumed_context(
        self,
        *,
        plan,
        descriptor: ProviderActionDescriptor,
        adapter: ProviderAdapter,
        request: ProviderExecutionRequest,
        bound_target_alias: str,
        context: ProviderExecutionContext,
    ) -> dict[str, object]:
        status = "succeeded"
        reason = "provider_action_succeeded"
        safe_output: Mapping[str, object] | None = None
        local_response: Mapping[str, object] | None = None
        try:
            output = adapter.execute(request, context)
            audit_output, raw_local_response = _split_local_provider_response(
                output, descriptor=descriptor
            )
            redacted_audit_output = context.redact_resolved_credentials(audit_output)
            if not isinstance(redacted_audit_output, Mapping):  # pragma: no cover - helper preserves mappings
                raise ValueError("provider_adapter_result_must_be_mapping")
            if raw_local_response is not None:
                redacted_local_response = context.redact_resolved_credentials(
                    raw_local_response
                )
                if not isinstance(redacted_local_response, Mapping):  # pragma: no cover - helper preserves mappings
                    raise ValueError("provider_local_response_invalid")
                local_response = _bounded_local_provider_response(
                    redacted_local_response, descriptor=descriptor
                )
            safe_output = _bounded_safe_output(
                redacted_audit_output,
                descriptor.max_result_bytes,
                action=descriptor.action,
            )
        except _ProviderResultTooLarge:
            status = "failed"
            reason = "provider_result_too_large"
        except Exception as error:
            status = "failed"
            candidate = getattr(error, "provider_reason", "")
            if isinstance(candidate, str) and re.fullmatch(
                r"[a-z][a-z0-9_.-]{0,80}", candidate
            ):
                reason = candidate
            else:
                reason = "provider_adapter_failed"

        verification_status = "not_required"
        write_effect_status: ProviderWriteEffectStatus = "not_applicable"
        if descriptor.read_back_verifier is not None and (
            status == "succeeded" or descriptor.risk in {"remote_write", "local_mutation"}
        ):
            verification_status, write_effect_status = self._run_read_back_once(
                adapter=adapter,
                descriptor=descriptor,
                request=request,
                target_alias=bound_target_alias,
                context=context,
            )
        write_performed: bool | None
        if context.network_dispatch_incident_count or context.local_mutation_unknown:
            write_performed = None
            write_effect_status = "unknown"
        elif descriptor.risk == "remote_write":
            if write_effect_status == "verified_applied":
                write_performed = True
            elif write_effect_status == "verified_not_applied":
                write_performed = False
            else:
                write_performed = None
        elif descriptor.risk == "local_mutation" and descriptor.read_back_verifier is not None:
            if write_effect_status == "verified_applied":
                write_performed = True
            elif write_effect_status == "verified_not_applied":
                write_performed = False
            else:
                write_performed = None
        elif request.action == "remote.fetch" and isinstance(safe_output, Mapping) and isinstance(safe_output.get("tracking_ref_updated"), bool):
            write_performed = safe_output["tracking_ref_updated"]
            write_effect_status = "verified_applied" if write_performed else "verified_not_applied"
        else:
            write_performed = descriptor.risk == "local_mutation" if status == "succeeded" else False
        if context.simulated_dispatch_count and context.network_call_count == 0 and descriptor.risk in {"remote_write", "local_mutation"}:
            write_performed = None
            write_effect_status = "unknown"
        actual_target_alias: str | None = None
        read_back_target = getattr(adapter, "read_back_target_alias", None)
        if write_effect_status == "verified_applied" and callable(read_back_target):
            try:
                candidate = read_back_target(request.action, request.parameters, context)
                normalizer = getattr(adapter, "normalize_target_alias", None)
                if callable(normalizer) and isinstance(candidate, str) and normalizer(candidate) == candidate:
                    context.validate_network_target(candidate)
                    actual_target_alias = candidate
            except Exception:
                pass
        safe_target_alias = context.redact_resolved_credentials(bound_target_alias)
        safe_planned_target_alias = context.redact_resolved_credentials(plan.target_alias)
        if not isinstance(safe_target_alias, str) or not isinstance(
            safe_planned_target_alias, str
        ):  # pragma: no cover - scalar redaction preserves strings
            safe_target_alias = _REDACTED_CREDENTIAL_VALUE
            safe_planned_target_alias = _REDACTED_CREDENTIAL_VALUE
        safe_actual_target_alias = context.redact_resolved_credentials(
            actual_target_alias
        )
        if safe_actual_target_alias is not None and not isinstance(
            safe_actual_target_alias, str
        ):  # pragma: no cover - scalar redaction preserves strings
            safe_actual_target_alias = _REDACTED_CREDENTIAL_VALUE
        safe_network_targets = context.redact_resolved_credentials(
            context.network_targets
        )
        if (
            not isinstance(safe_network_targets, Sequence)
            or isinstance(safe_network_targets, (str, bytes, bytearray))
            or not all(isinstance(target, str) for target in safe_network_targets)
        ):  # pragma: no cover - Context exposes a tuple of safe strings
            safe_network_targets = tuple(
                _REDACTED_CREDENTIAL_VALUE for _target in context.network_targets
            )
        return self._record_result(
            plan=plan,
            descriptor=descriptor,
            target_alias=safe_target_alias,
            planned_target_alias=safe_planned_target_alias,
            actual_target_alias=safe_actual_target_alias,
            status=status,
            reason=reason,
            result_summary=safe_output,
            local_response=local_response,
            credentials_read=context.credential_resolver_called,
            external_calls=None if context.network_dispatch_incident_count else context.network_call_count > 0,
            network_call_count=context.network_call_count,
            simulated_dispatch_count=context.simulated_dispatch_count,
            network_dispatch_incident_count=context.network_dispatch_incident_count,
            network_targets=tuple(safe_network_targets),
            execution_provenance="unknown" if context.network_dispatch_incident_count else ("live" if context.network_call_count else ("simulated" if context.simulated_dispatch_count else "none")),
            write_performed=write_performed,
            verification_status=verification_status,
            write_effect_status=write_effect_status,
        )

    def render_plan(self, request: ProviderExecutionRequest) -> dict[str, object]:
        """Render a provider change only when its plan and target are bound."""

        if not isinstance(request, ProviderExecutionRequest):
            raise TypeError("request must be a ProviderExecutionRequest")
        plan = self._authorizer.get_plan(request.plan_id)
        if request.action != plan.action:
            raise ValueError("provider_action_plan_mismatch")
        descriptor = self._action_descriptors.get(request.action)
        if descriptor is None or descriptor.provider != plan.provider:
            raise ValueError("provider_action_provider_mismatch")
        adapter = self._adapters.get(plan.provider)
        if adapter is None:
            raise ValueError("provider_adapter_not_registered")
        try:
            profile_enabled = self._repository.profile_status(plan.profile_id).get("enabled") is True
        except Exception:
            raise ValueError("provider_profile_unavailable") from None
        if not profile_enabled:
            raise PermissionError("provider_profile_disabled")
        try:
            parameter_hash = canonical_json_hash(request.parameters)
        except ValueError:
            raise ValueError("provider_parameters_plan_mismatch") from None
        if parameter_hash != plan.parameter_hash:
            raise ValueError("provider_parameters_plan_mismatch")
        bound_target_alias = _bound_target_alias(adapter, plan.target_alias, request)
        if bound_target_alias is None:
            raise ValueError("provider_target_mismatch")
        if not _profile_target_is_bound(adapter, plan.profile_id, bound_target_alias):
            raise ValueError("provider_target_mismatch")
        renderer = getattr(adapter, "render_plan", None)
        if not callable(renderer):
            raise ValueError("provider_plan_renderer_not_registered")
        rendered = renderer(request)
        if not isinstance(rendered, Mapping):
            raise ValueError("provider_plan_renderer_invalid")
        result = dict(rendered)
        if result.get("target_alias") != bound_target_alias:
            raise ValueError("provider_target_mismatch")
        result["plan_id"] = plan.id
        result["parameter_hash"] = plan.parameter_hash
        return result

    def _run_read_back_once(
        self,
        *,
        adapter: ProviderAdapter,
        descriptor: ProviderActionDescriptor,
        request: ProviderExecutionRequest,
        target_alias: str,
        context: ProviderExecutionContext,
    ) -> tuple[str, ProviderWriteEffectStatus]:
        verifier_action = descriptor.read_back_verifier
        verifier_descriptor = self._action_descriptors.get(verifier_action or "")
        if (
            verifier_action is None
            or verifier_descriptor is None
            or verifier_descriptor.provider != descriptor.provider
            or verifier_descriptor.risk != "read"
        ):
            return "failed", "unknown"
        read_back_parameters = dict(request.parameters)
        requested_timeout = read_back_parameters.get("timeout_seconds")
        if (
            not isinstance(requested_timeout, int)
            or isinstance(requested_timeout, bool)
            or requested_timeout < 1
        ):
            requested_timeout = verifier_descriptor.max_timeout_seconds
        read_back_parameters["timeout_seconds"] = min(
            requested_timeout, verifier_descriptor.max_timeout_seconds
        )
        read_back_request = ProviderExecutionRequest(
            plan_id=request.plan_id,
            actor=request.actor,
            action=verifier_action,
            parameters=read_back_parameters,
        )
        try:
            verified = adapter.verify(
                verifier_action,
                request.action,
                read_back_request,
                target_alias,
                context,
            )
        except Exception:
            return "failed", "unknown"
        if verified is True or verified == "verified_applied":
            return "verified", "verified_applied"
        if verified == "verified_not_applied":
            return "verified", "verified_not_applied"
        return "unverified", "unknown"

    def _record_result(
        self,
        *,
        plan,
        descriptor: ProviderActionDescriptor | None,
        target_alias: str | None = None,
        planned_target_alias: str | None = None,
        actual_target_alias: str | None = None,
        status: str,
        reason: str,
        result_summary: Mapping[str, object] | None = None,
        local_response: Mapping[str, object] | None = None,
        credentials_read: bool = False,
        external_calls: bool | None = False,
        network_call_count: int = 0,
        simulated_dispatch_count: int = 0,
        network_dispatch_incident_count: int = 0,
        network_targets: tuple[str, ...] = (),
        execution_provenance: str = "none",
        write_performed: bool | None = False,
        verification_status: str = "not_run",
        write_effect_status: ProviderWriteEffectStatus = "not_attempted",
    ) -> dict[str, object]:
        result = {
            "schema_version": PROVIDER_EXECUTION_RESULT_SCHEMA_VERSION,
            "plan_id": plan.id,
            "provider": plan.provider,
            "profile_key": plan.profile_key,
            "action": plan.action,
            "target_alias": target_alias if target_alias is not None else plan.target_alias,
            "planned_target_alias": (
                planned_target_alias
                if planned_target_alias is not None
                else plan.target_alias
            ),
            "actual_target_alias": actual_target_alias,
            "risk": descriptor.risk if descriptor is not None else "",
            "status": status,
            "reason": reason,
            "credentials_read": credentials_read,
            "external_calls": external_calls,
            "network_call_count": network_call_count,
            "simulated_dispatch_count": simulated_dispatch_count,
            "network_dispatch_incident_count": network_dispatch_incident_count,
            "network_targets": list(network_targets),
            "execution_provenance": execution_provenance,
            "write_performed": write_performed,
            "verification_status": verification_status,
            "write_effect_status": write_effect_status,
            "learning_candidate_status": "not_applicable",
            "learning_candidate_reason": "",
            "result_summary": dict(result_summary or {}),
        }
        if local_response is not None:
            # Deliberately returned only to this in-process caller.  The audit
            # record below is built without it, so query rows cannot become
            # durable Manager evidence by accident.
            result["local_response"] = dict(local_response)
        audit_result = {
            key: value for key, value in result.items() if key != "local_response"
        }
        audit_id = self._repository.record_action(
            profile_id=plan.profile_id,
            action_type=plan.action,
            status=status,
            target_alias=target_alias if target_alias is not None else plan.target_alias,
            parameter_hash=plan.parameter_hash,
            details=audit_result,
        )
        if status == "failed":
            # A failed controlled execution is useful only as a non-executable,
            # reviewer-owned candidate set.  The sample deliberately consists
            # of fixed failure metadata and the just-written audit ID; it never
            # copies adapter exceptions, raw outputs, credentials or target
            # aliases into Manager learning storage.
            try:
                from app.learning_loop import persist_manager_learning_candidates
                from app.learning_candidate_repository import LearningCandidateRepository

                persist_manager_learning_candidates(
                    {
                        "run_id": f"provider-audit-{audit_id}",
                        "task_key": plan.action,
                        "failure_kind": reason,
                        "summary": "受控 Provider 执行失败，需要人工补充和审核可复用证据。",
                        "evidence_refs": [f"provider-action-audit-{audit_id}"],
                        "scope": {"provider": plan.provider, "profile": plan.profile_key},
                    },
                    repository=LearningCandidateRepository(),
                    source_action_audit_id=audit_id,
                )
                result["learning_candidate_status"] = "candidate_set_created"
            except Exception:
                # The action audit and consumed authorization are already durable.
                # Candidate persistence must never re-raise an adapter failure or
                # expose its text after that one-use action boundary.
                result["learning_candidate_status"] = "candidate_persistence_failed"
                result["learning_candidate_reason"] = "learning_candidate_persistence_failed"
        return result


class _ProviderResultTooLarge(ValueError):
    pass


def _split_local_provider_response(
    output: Mapping[str, object], *, descriptor: ProviderActionDescriptor
) -> tuple[dict[str, object], Mapping[str, object] | None]:
    """Separate the one allowed ephemeral local result from durable audit data."""

    if not isinstance(output, Mapping):
        raise ValueError("provider_adapter_result_must_be_mapping")
    prepared = dict(output)
    local_response = prepared.pop("__local_response__", None)
    if local_response is None:
        return prepared, None
    if not (
        (descriptor.action == "database.query.read" and descriptor.provider == "database")
        or (
            descriptor.action in GITLAB_CODE_EVIDENCE_READ_ACTIONS
            and descriptor.provider == "gitlab"
            and descriptor.risk == "read"
        )
        or (
            descriptor.action in GITHUB_CODE_EVIDENCE_READ_ACTIONS
            and descriptor.provider == "github"
            and descriptor.risk == "read"
        )
    ):
        raise ValueError("provider_local_response_not_allowed")
    if not isinstance(local_response, Mapping):
        raise ValueError("provider_local_response_invalid")
    return prepared, dict(local_response)


def _bounded_local_provider_response(
    local_response: Mapping[str, object], *, descriptor: ProviderActionDescriptor
) -> dict[str, object]:
    """Validate only the Context-redacted query response before returning it."""

    try:
        encoded = json.dumps(
            dict(local_response),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise ValueError("provider_local_response_invalid") from None
    if len(encoded) > descriptor.max_result_bytes:
        raise _ProviderResultTooLarge
    return dict(local_response)


def _bounded_safe_output(
    output: Mapping[str, object], max_result_bytes: int, *, action: str = ""
) -> dict[str, object]:
    if not isinstance(output, Mapping):
        raise ValueError("provider_adapter_result_must_be_mapping")
    try:
        encoded = json.dumps(
            dict(output),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise ValueError("provider_adapter_result_invalid") from None
    if len(encoded) > max_result_bytes:
        raise _ProviderResultTooLarge
    safe_output = redact_sensitive_mapping(output)
    # Git object IDs are immutable verification evidence, not credentials.  The
    # general summary redactor deliberately treats opaque strings as secrets, so
    # preserve only the exact SHA returned for the reviewed commit action.
    commit_sha = output.get("commit_sha")
    if action == "commit.create" and isinstance(commit_sha, str) and re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        safe_output["commit_sha"] = commit_sha
    if action == "model.single_node.smoke":
        usage = output.get("usage")
        if isinstance(usage, Mapping):
            safe_usage = {
                field: value
                for field, value in usage.items()
                if field in {"input_tokens", "output_tokens", "total_tokens"}
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            }
            safe_output["usage"] = safe_usage
    if action in GITLAB_CODE_EVIDENCE_READ_ACTIONS | GITHUB_CODE_EVIDENCE_READ_ACTIONS:
        payload_sha256 = output.get("payload_sha256")
        if isinstance(payload_sha256, str) and re.fullmatch(r"[0-9a-f]{64}", payload_sha256):
            safe_output["payload_sha256"] = payload_sha256
        for field in ("payload_bytes", "item_count"):
            value = output.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                safe_output[field] = value
        truncated = output.get("truncated")
        if isinstance(truncated, bool):
            safe_output["truncated"] = truncated
    return safe_output


def _bound_target_alias(
    adapter: ProviderAdapter, plan_target_alias: object, request: ProviderExecutionRequest
) -> str | None:
    """Bind a provider's real target to the confirmed plan before consumption."""

    normalizer = getattr(adapter, "normalize_target_alias", None)
    request_normalizer = getattr(adapter, "normalize_request_target", None)
    if not callable(normalizer):
        return plan_target_alias if isinstance(plan_target_alias, str) else None
    try:
        expected = normalizer(plan_target_alias)
        actual = (
            request_normalizer(request.parameters)
            if callable(request_normalizer)
            else normalizer(request.parameters.get("work_item_alias"))
        )
    except Exception:
        return None
    if not isinstance(expected, str) or expected != actual:
        return None
    return actual


def _profile_target_is_bound(
    adapter: ProviderAdapter, profile_id: int, target_alias: str
) -> bool:
    """Give profile-aware adapters one pre-consumption target identity gate."""

    validator = getattr(adapter, "validate_profile_binding", None)
    if not callable(validator):
        return True
    try:
        return validator(profile_id=profile_id, target_alias=target_alias) == target_alias
    except Exception:
        return False


def _desktop_env_credential(provider: str, field: str) -> str:
    """桌面宿主注入的凭证环境变量映射（只在授权解析失败时兜底）。"""

    mapping = {
        ("gitlab", "access_token"): "DSH_GITLAB_TOKEN",
        ("yunxiao", "pat"): "ALIYUN_DEVOPS_PAT",
    }
    name = mapping.get((provider, field))
    if name is None:
        return ""
    return os.environ.get(name, "").strip()


def _credential_resolution_unavailable(_profile_id: int, _field: str) -> str:
    raise RuntimeError("provider_credential_resolution_unavailable")
