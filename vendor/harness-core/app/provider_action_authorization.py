from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.manager_provider_repository import ManagerProviderRepository
from app.provider_profiles import _reject_sensitive_values
from app.sensitive_text import (
    contains_sensitive_scalar_text,
    contains_sensitive_text,
    is_sensitive_mapping_key,
    redact_sensitive_mapping,
    redact_sensitive_text,
)


DEFAULT_AUTHORIZATION_TTL_SECONDS = 300
MAX_AUTHORIZATION_TTL_SECONDS = 900
MAX_CANONICAL_JSON_BYTES = 65_536
MAX_SAFE_SUMMARY_BYTES = 4_096
MAX_SAFE_SUMMARY_DEPTH = 8
MAX_SAFE_SUMMARY_ITEMS = 64
MAX_SAFE_SUMMARY_TEXT = 512
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|pat|token|api[_-]?key|secret|password|credential|private[_-]?key)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProviderActionPlan:
    id: int
    profile_id: int
    scope_type: str
    scope_key: str
    provider: str
    profile_key: str
    action: str
    target_alias: str
    parameter_hash: str
    reviewed_parameter_summary: dict[str, object]
    requested_by: str
    confirmed_by: str
    state: str
    rejection_reason: str
    created_at: datetime
    confirmed_at: datetime | None
    expires_at: datetime | None
    consumed_at: datetime | None
    rejected_at: datetime | None


@dataclass(frozen=True)
class ProviderActionAuthorization:
    plan_id: int
    token: str
    authorization_hash: str
    actor: str
    issued_at: datetime
    expires_at: datetime

    def __repr__(self) -> str:
        return (
            "ProviderActionAuthorization("
            f"plan_id={self.plan_id!r}, token='[REDACTED]', "
            f"authorization_hash={self.authorization_hash!r}, actor={self.actor!r}, "
            f"issued_at={self.issued_at!r}, expires_at={self.expires_at!r})"
        )


@dataclass(frozen=True)
class ProviderActionDecision:
    allowed: bool
    status: str
    reason: str
    plan_id: int
    audit_id: int


class ProviderActionAuthorizer:
    """Create, confirm and atomically consume one-use Provider action plans."""

    def __init__(
        self,
        repository: ManagerProviderRepository,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._repository = repository
        self._clock = clock

    def create_plan(
        self,
        *,
        profile_id: int,
        action: str,
        target_alias: str,
        parameters: Mapping[str, object],
        requested_by: str,
    ) -> ProviderActionPlan:
        safe_action = _public_alias(action, "action")
        safe_target_alias = _public_alias(target_alias, "target_alias")
        safe_requested_by = _public_alias(requested_by, "requested_by")
        from app.provider_execution import ACTION_DESCRIPTORS

        descriptor = ACTION_DESCRIPTORS.get(safe_action)
        if descriptor is None:
            raise ValueError("provider_action_not_registered")
        profile_status = self._repository.profile_status(profile_id)
        if profile_status.get("provider") != descriptor.provider:
            raise ValueError("provider_action_provider_mismatch")
        if safe_action in {
            "project.read",
            "merge_request.read",
            "merge_request.comment.write",
            "merge_request.create",
            "gitlab.repository.file.read",
            "gitlab.commit.read",
            "gitlab.commit.diff.read",
            "gitlab.compare.read",
            "gitlab.merge_request.commits.read",
            "gitlab.merge_request.diffs.read",
            "gitlab.pipeline.jobs.read",
        }:
            # Keep plan identity on the GitLab adapter's length-delimited
            # grammar.  Import lazily to avoid the execution/adapter import
            # cycle during module initialization.
            from app.providers.gitlab import canonical_gitlab_target

            canonical_target = canonical_gitlab_target(safe_action, parameters)
            if safe_target_alias != canonical_target:
                raise ValueError("gitlab_target_invalid")
        if safe_action.startswith("github.") and safe_action != "github.connection_test":
            from app.providers.github import canonical_github_target

            canonical_target = canonical_github_target(safe_action, parameters)
            if safe_target_alias != canonical_target:
                raise ValueError("github_target_invalid")
        from app.providers.git import REPOSITORY_BOUND_GIT_ACTIONS

        if safe_action in REPOSITORY_BOUND_GIT_ACTIONS:
            # This is intentionally scope-free: plan creation has no adapter
            # instance or repository access, but it can still reject malformed
            # refs and a repository alias that differs from the reviewed target.
            from app.providers.git import validate_git_action_parameters

            validate_git_action_parameters(
                safe_action, safe_target_alias, parameters
            )
        if safe_action == "model.single_node.smoke":
            connection = profile_status.get("connection")
            if (
                profile_status.get("provider") == "model"
                and isinstance(connection, Mapping)
                and all(
                    bool(str(connection.get(field) or "").strip())
                    for field in (
                        "provider_kind",
                        "base_url",
                        "allowed_endpoint_host",
                        "model",
                        "timeout_seconds",
                        "max_output_tokens",
                    )
                )
            ):
                from app.providers.model_smoke import (
                    ManagerModelSmokeProviderAdapter,
                    validate_model_smoke_parameters,
                )

                normalized_target = validate_model_smoke_parameters(
                    target_alias=safe_target_alias,
                    parameters=parameters,
                )
                expected_target = ManagerModelSmokeProviderAdapter.normalize_target_alias(
                    f"model.{profile_status.get('profile_key') or ''}"
                )
                if normalized_target != expected_target:
                    raise ValueError("model_smoke_target_invalid")
        parameter_hash = canonical_json_hash(parameters)
        reviewed_parameter_summary = redact_safe_result_summary(parameters)
        if safe_action in {"database.connection_test", "database.schema.read", "database.query.read"}:
            from app.providers.database_readonly import canonical_database_target

            expected_target = canonical_database_target(profile_status.get("profile_key"))
            if (
                safe_target_alias != expected_target
                or parameters.get("database_alias") != expected_target
            ):
                raise ValueError("database_target_invalid")
        record = self._repository.create_action_plan(
            profile_id=profile_id,
            action_type=safe_action,
            target_alias=safe_target_alias,
            parameter_hash=parameter_hash,
            reviewed_parameter_summary=reviewed_parameter_summary,
            requested_by=safe_requested_by,
            created_at=self._now().isoformat(),
        )
        return _plan_from_record(record)

    def get_plan(self, plan_id: int) -> ProviderActionPlan:
        return _plan_from_record(self._repository.get_action_plan(plan_id))

    def confirm(
        self,
        plan_id: int,
        *,
        actor: str,
        ttl_seconds: int = DEFAULT_AUTHORIZATION_TTL_SECONDS,
    ) -> ProviderActionAuthorization:
        safe_actor = _public_alias(actor, "actor")
        if (
            not isinstance(ttl_seconds, int)
            or isinstance(ttl_seconds, bool)
            or ttl_seconds < 1
            or ttl_seconds > MAX_AUTHORIZATION_TTL_SECONDS
        ):
            raise ValueError("ttl_seconds is outside the allowed range")
        issued_at = self._now()
        expires_at = issued_at + timedelta(seconds=ttl_seconds)
        token = secrets.token_urlsafe(32)
        authorization_hash = _authorization_hash(token)
        self._repository.confirm_action_plan(
            plan_id=plan_id,
            actor=safe_actor,
            authorization_hash=authorization_hash,
            confirmed_at=issued_at.isoformat(),
            expires_at=expires_at.isoformat(),
        )
        return ProviderActionAuthorization(
            plan_id=plan_id,
            token=token,
            authorization_hash=authorization_hash,
            actor=safe_actor,
            issued_at=issued_at,
            expires_at=expires_at,
        )

    def consume(
        self,
        *,
        plan_id: int,
        authorization: ProviderActionAuthorization | None,
        actor: str,
        parameters: Mapping[str, object],
    ) -> ProviderActionDecision:
        if authorization is not None and not isinstance(
            authorization, ProviderActionAuthorization
        ):
            _validate_untrusted_authorization(authorization)
        safe_actor = _public_alias(actor, "actor")
        parameter_hash = canonical_json_hash(parameters)
        attempted_at = self._now().isoformat()

        if authorization is not None and not isinstance(
            authorization, ProviderActionAuthorization
        ):
            rejected = self._repository.record_action_plan_rejection(
                plan_id=plan_id,
                parameter_hash=parameter_hash,
                reason="trusted_authorization_required",
                attempted_at=attempted_at,
            )
            return _decision_from_record(rejected)

        authorization_hash = ""
        if authorization is not None:
            if authorization.plan_id != plan_id:
                rejected = self._repository.record_action_plan_rejection(
                    plan_id=plan_id,
                    parameter_hash=parameter_hash,
                    reason="authorization_plan_mismatch",
                    attempted_at=attempted_at,
                )
                return _decision_from_record(rejected)
            authorization_hash = _authorization_hash(authorization.token)
        decision = self._repository.consume_action_plan(
            plan_id=plan_id,
            actor=safe_actor,
            authorization_hash=authorization_hash,
            parameter_hash=parameter_hash,
            attempted_at=attempted_at,
        )
        return _decision_from_record(decision)

    def _now(self) -> datetime:
        current = self._clock()
        if not isinstance(current, datetime):
            raise TypeError("clock must return a datetime")
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return current.astimezone(timezone.utc)


def canonical_json_hash(value: Mapping[str, object]) -> str:
    """Hash bounded, secret-free JSON using stable key and separator rules."""

    if not isinstance(value, Mapping):
        raise ValueError("provider_action_authorization:parameters_must_be_mapping")
    _validate_safe_public_value(value)
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise ValueError("provider_action_authorization:parameters_not_canonical_json") from None
    if len(encoded) > MAX_CANONICAL_JSON_BYTES:
        raise ValueError("provider_action_authorization:parameters_too_large")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def redact_safe_result_summary(value: Mapping[str, object]) -> dict[str, object]:
    """Return a bounded summary with all sensitive keys and values redacted."""

    if not isinstance(value, Mapping):
        raise ValueError("provider_action_authorization:summary_must_be_mapping")
    return redact_sensitive_mapping(value)


def _validate_safe_public_value(value: object) -> None:
    try:
        _reject_sensitive_values(value)
        _walk_safe_public_value(value, depth=0, budget=[MAX_SAFE_SUMMARY_ITEMS * 8])
    except (MemoryError, RecursionError, ValueError):
        raise ValueError("provider_action_authorization:sensitive_public_input") from None


def _validate_untrusted_authorization(value: object) -> None:
    try:
        _walk_untrusted_authorization(
            value,
            depth=0,
            budget=[MAX_SAFE_SUMMARY_ITEMS * 8],
        )
    except Exception:
        raise ValueError(
            "provider_action_authorization:sensitive_public_input"
        ) from None


def _walk_untrusted_authorization(
    value: object,
    *,
    depth: int,
    budget: list[int],
) -> None:
    if depth > MAX_SAFE_SUMMARY_DEPTH:
        raise ValueError("authorization input is too deeply nested")
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError("authorization input has too many items")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or is_sensitive_mapping_key(key)
                or contains_sensitive_scalar_text(key)
            ):
                raise ValueError("authorization input key is unsafe")
            _walk_untrusted_authorization(
                item,
                depth=depth + 1,
                budget=budget,
            )
        return
    if isinstance(value, str):
        if (
            len(value) > MAX_CANONICAL_JSON_BYTES
            or contains_sensitive_scalar_text(value)
        ):
            raise ValueError("authorization input text is unsafe")
        return
    if isinstance(value, (bytes, bytearray)):
        if len(value) > MAX_CANONICAL_JSON_BYTES:
            raise ValueError("authorization input bytes are too large")
        _walk_untrusted_authorization(
            bytes(value).decode("utf-8", "strict"),
            depth=depth,
            budget=budget,
        )
        return
    if isinstance(value, Sequence):
        for item in value:
            _walk_untrusted_authorization(
                item,
                depth=depth + 1,
                budget=budget,
            )
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise ValueError("authorization input type is unsupported")


def _walk_safe_public_value(value: object, *, depth: int, budget: list[int]) -> None:
    if depth > MAX_SAFE_SUMMARY_DEPTH:
        raise ValueError("public input is too deeply nested")
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError("public input has too many items")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or is_sensitive_mapping_key(key)
                or contains_sensitive_text(key)
            ):
                raise ValueError("public input key is unsafe")
            _walk_safe_public_value(item, depth=depth + 1, budget=budget)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _walk_safe_public_value(item, depth=depth + 1, budget=budget)
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("public input number is not finite")
        return
    if isinstance(value, str):
        if len(value) > MAX_CANONICAL_JSON_BYTES or contains_sensitive_text(value):
            raise ValueError("public input text is unsafe")
        return
    raise ValueError("public input type is unsupported")


def _redact_value(value: object, *, depth: int, budget: list[int]) -> object:
    if depth > MAX_SAFE_SUMMARY_DEPTH:
        return "[REDACTED_DEPTH_LIMIT]"
    budget[0] -= 1
    if budget[0] < 0:
        return "[REDACTED_ITEM_LIMIT]"
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            raw_key = str(key)
            if _SENSITIVE_KEY.search(raw_key) or contains_sensitive_text(raw_key):
                result["[REDACTED_SENSITIVE_KEY]"] = "[REDACTED_SENSITIVE_FIELD]"
            else:
                key_text = raw_key[:128]
                result[key_text] = _redact_value(item, depth=depth + 1, budget=budget)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_redact_value(item, depth=depth + 1, budget=budget) for item in value]
    if isinstance(value, str):
        bounded = value[:MAX_SAFE_SUMMARY_TEXT]
        redacted = redact_sensitive_text(bounded)
        if len(value) > MAX_SAFE_SUMMARY_TEXT:
            return f"{redacted}[TRUNCATED]"
        return redacted
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "[REDACTED_NON_FINITE_NUMBER]"
    return "[REDACTED_UNSUPPORTED_VALUE]"


def _public_alias(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 256
        or any(character.isspace() or ord(character) < 32 for character in normalized)
        or "://" in normalized
    ):
        raise ValueError(f"{name} must be a safe alias")
    _validate_safe_public_value(normalized)
    return normalized


def _authorization_hash(token: str) -> str:
    if not isinstance(token, str) or not token:
        return ""
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _plan_from_record(record: Mapping[str, object]) -> ProviderActionPlan:
    return ProviderActionPlan(
        id=int(record["id"]),
        profile_id=int(record["profile_id"]),
        scope_type=str(record["scope_type"]),
        scope_key=str(record["scope_key"]),
        provider=str(record["provider"]),
        profile_key=str(record["profile_key"]),
        action=str(record["action_type"]),
        target_alias=str(record["target_alias"]),
        parameter_hash=str(record["parameter_hash"]),
        reviewed_parameter_summary=dict(record["reviewed_parameter_summary"]),
        requested_by=str(record["requested_by"]),
        confirmed_by=str(record["confirmed_by"]),
        state=str(record["state"]),
        rejection_reason=str(record["rejection_reason"]),
        created_at=_required_datetime(record["created_at"]),
        confirmed_at=_optional_datetime(record["confirmed_at"]),
        expires_at=_optional_datetime(record["authorization_expires_at"]),
        consumed_at=_optional_datetime(record["consumed_at"]),
        rejected_at=_optional_datetime(record["rejected_at"]),
    )


def _decision_from_record(record: Mapping[str, object]) -> ProviderActionDecision:
    return ProviderActionDecision(
        allowed=bool(record["allowed"]),
        status=str(record["status"]),
        reason=str(record["reason"]),
        plan_id=int(record["plan_id"]),
        audit_id=int(record["audit_id"]),
    )


def _required_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored provider action timestamp is invalid")
    return parsed.astimezone(timezone.utc)


def _optional_datetime(value: object) -> datetime | None:
    return _required_datetime(value) if str(value or "") else None
