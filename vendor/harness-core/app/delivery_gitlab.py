from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Protocol

from app.manager_provider_repository import ManagerProviderRepository, ProviderProfileRecord
from app.provider_action_authorization import ProviderActionAuthorizer
from app.provider_execution import ProviderExecutionRequest, ProviderExecutionService
from app.providers.gitlab import GitLabProviderAdapter, canonical_gitlab_target


class _ProfileRepository(Protocol):
    def list_profiles(self) -> Sequence[ProviderProfileRecord]: ...


class _ActionAuthorizer(Protocol):
    def create_plan(self, **kwargs: object) -> object: ...

    def confirm(self, plan_id: int, **kwargs: object) -> object: ...


class _ExecutionService(Protocol):
    def execute(self, authorization: object, request: ProviderExecutionRequest) -> Mapping[str, object]: ...


class GitLabDeliveryExecutor:
    """Turn one declared delivery action into one bound Provider execution."""

    def __init__(
        self,
        profiles: _ProfileRepository,
        authorizer: _ActionAuthorizer,
        *,
        execution_service_factory: Callable[[ProviderProfileRecord], _ExecutionService],
    ) -> None:
        self._profiles = profiles
        self._authorizer = authorizer
        self._execution_service_factory = execution_service_factory

    def __call__(
        self,
        *,
        transaction_id: int,
        approved_plan_hash: str,
        gitlab_action: Mapping[str, object],
        plan: Mapping[str, object],
    ) -> dict[str, object]:
        if (
            not isinstance(transaction_id, int)
            or transaction_id < 1
            or not isinstance(approved_plan_hash, str)
            or plan.get("plan_hash") != approved_plan_hash
        ):
            raise ValueError("gitlab_delivery_plan_mismatch")
        action = gitlab_action.get("action")
        declared_parameters = gitlab_action.get("parameters")
        if (
            action not in {"merge_request.create", "merge_request.comment.write"}
            or not isinstance(declared_parameters, Mapping)
        ):
            raise ValueError("gitlab_delivery_action_invalid")
        host_alias = declared_parameters.get("host_alias")
        if not isinstance(host_alias, str):
            raise ValueError("gitlab_delivery_action_invalid")
        parameters = dict(declared_parameters)
        declared_gitlab_host = parameters.pop("gitlab_host", None)
        if declared_gitlab_host is not None and not isinstance(declared_gitlab_host, str):
            raise ValueError("gitlab_delivery_action_invalid")
        profile = self._profile_for_host_alias(host_alias, declared_gitlab_host)
        parameters["host_alias"] = profile.profile_key
        parameters["timeout_seconds"] = 15
        target_alias = canonical_gitlab_target(action, parameters)
        provider_plan = self._authorizer.create_plan(
            profile_id=profile.id,
            action=action,
            target_alias=target_alias,
            parameters=parameters,
            requested_by=f"delivery-{transaction_id}",
        )
        provider_plan_id = getattr(provider_plan, "id", None)
        if not isinstance(provider_plan_id, int) or provider_plan_id < 1:
            raise ValueError("gitlab_delivery_provider_plan_invalid")
        authorization = self._authorizer.confirm(
            provider_plan_id,
            actor=f"delivery-{transaction_id}",
            ttl_seconds=300,
        )
        result = self._execution_service_factory(profile).execute(
            authorization,
            ProviderExecutionRequest(
                plan_id=provider_plan_id,
                actor=f"delivery-{transaction_id}",
                action=action,
                parameters=parameters,
            ),
        )
        status = "success" if result.get("status") == "succeeded" else "failed"
        write_effect_status = result.get("write_effect_status")
        actual_target = result.get("actual_target_alias")
        receipt_target = self._receipt_target_alias(
            action,
            declared_parameters,
            target_alias,
            actual_target,
        )
        return {
            "action": action,
            "status": status,
            "write_effect_status": write_effect_status,
            "target_alias": receipt_target,
            "remote_dispatch_attempted": result.get("external_calls") is not False,
            "provider_plan_id": provider_plan_id,
        }

    def _profile_for_host_alias(
        self,
        host_alias: str,
        declared_gitlab_host: str | None,
    ) -> ProviderProfileRecord:
        matches = [
            profile
            for profile in self._profiles.list_profiles()
            if (
                profile.provider == "gitlab"
                and profile.enabled
                and (
                    profile.profile_key == host_alias
                    if declared_gitlab_host is None
                    else profile.connection.get("host") == declared_gitlab_host
                )
            )
        ]
        if len(matches) != 1:
            raise ValueError("gitlab_delivery_profile_missing")
        return matches[0]

    @staticmethod
    def _receipt_target_alias(
        action: str,
        declared_parameters: Mapping[str, object],
        provider_target: str,
        actual_target: object,
    ) -> str:
        resolved_target = actual_target if isinstance(actual_target, str) else provider_target
        if "gitlab_host" not in declared_parameters:
            return resolved_target
        target_parameters = {
            key: value
            for key, value in declared_parameters.items()
            if key != "gitlab_host"
        }
        declared_target = canonical_gitlab_target(action, target_parameters)
        if action == "merge_request.comment.write":
            if resolved_target != provider_target:
                raise ValueError("gitlab_delivery_target_mismatch")
            return declared_target
        prefix = provider_target + "-m"
        suffix = resolved_target[len(prefix):] if resolved_target.startswith(prefix) else ""
        if not suffix.isdecimal() or str(int(suffix)) != suffix or int(suffix) < 1:
            raise ValueError("gitlab_delivery_target_mismatch")
        return declared_target + "-m" + suffix


def build_gitlab_delivery_executor() -> GitLabDeliveryExecutor:
    """Bind delivery host aliases to configured GitLab profile keys."""
    repository = ManagerProviderRepository()
    authorizer = ProviderActionAuthorizer(
        repository,
        clock=lambda: datetime.now(timezone.utc),
    )

    def execution_service(profile: ProviderProfileRecord) -> ProviderExecutionService:
        host = profile.connection.get("host")
        if not isinstance(host, str) or not host:
            raise ValueError("gitlab_delivery_profile_host_missing")
        adapter = GitLabProviderAdapter(
            {profile.profile_key: "https://" + host},
        )
        return ProviderExecutionService(
            repository,
            authorizer,
            adapters={"gitlab": adapter},
        )

    return GitLabDeliveryExecutor(
        repository,
        authorizer,
        execution_service_factory=execution_service,
    )
