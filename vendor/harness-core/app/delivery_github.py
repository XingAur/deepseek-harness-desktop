from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Protocol

from app.manager_provider_repository import ManagerProviderRepository, ProviderProfileRecord
from app.provider_action_authorization import ProviderActionAuthorizer
from app.provider_execution import ProviderExecutionRequest, ProviderExecutionService
from app.providers.github import GitHubProviderAdapter, canonical_github_target


class _ProfileRepository(Protocol):
    def list_profiles(self) -> Sequence[ProviderProfileRecord]: ...


class _ActionAuthorizer(Protocol):
    def create_plan(self, **kwargs: object) -> object: ...

    def confirm(self, plan_id: int, **kwargs: object) -> object: ...


class _ExecutionService(Protocol):
    def execute(self, authorization: object, request: ProviderExecutionRequest) -> Mapping[str, object]: ...


class GitHubDeliveryExecutor:
    """Turn one declared GitHub delivery action into one bound execution."""

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
        github_action: Mapping[str, object],
        plan: Mapping[str, object],
    ) -> dict[str, object]:
        if (
            not isinstance(transaction_id, int)
            or transaction_id < 1
            or not isinstance(approved_plan_hash, str)
            or plan.get("plan_hash") != approved_plan_hash
        ):
            raise ValueError("github_delivery_plan_mismatch")
        action = github_action.get("action")
        declared_parameters = github_action.get("parameters")
        if action not in {
            "github.pull_request.create",
            "github.pull_request.comment.write",
        } or not isinstance(declared_parameters, Mapping):
            raise ValueError("github_delivery_action_invalid")
        owner, repository = declared_parameters.get("owner"), declared_parameters.get("repository")
        if not isinstance(owner, str) or not isinstance(repository, str):
            raise ValueError("github_delivery_action_invalid")
        profile = self._profile_for_repository(owner, repository)
        parameters = dict(declared_parameters)
        parameters["timeout_seconds"] = 20 if action == "github.pull_request.create" else 15
        target_alias = canonical_github_target(action, parameters)
        provider_plan = self._authorizer.create_plan(
            profile_id=profile.id,
            action=action,
            target_alias=target_alias,
            parameters=parameters,
            requested_by=f"delivery-{transaction_id}",
        )
        provider_plan_id = getattr(provider_plan, "id", None)
        if not isinstance(provider_plan_id, int) or provider_plan_id < 1:
            raise ValueError("github_delivery_provider_plan_invalid")
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
        actual_target = result.get("actual_target_alias")
        if result.get("status") == "succeeded" and not isinstance(actual_target, str):
            raise ValueError("github_delivery_readback_missing")
        return {
            "action": action,
            "status": "success" if result.get("status") == "succeeded" else "failed",
            "write_effect_status": result.get("write_effect_status"),
            "target_alias": actual_target if isinstance(actual_target, str) else target_alias,
            "remote_dispatch_attempted": result.get("external_calls") is not False,
            "provider_plan_id": provider_plan_id,
        }

    def _profile_for_repository(self, owner: str, repository: str) -> ProviderProfileRecord:
        matches = [
            profile
            for profile in self._profiles.list_profiles()
            if (
                profile.provider == "github"
                and profile.enabled
                and str(profile.connection.get("owner") or "").lower() == owner.lower()
                and str(profile.connection.get("repository") or "").lower() == repository.lower()
            )
        ]
        if len(matches) != 1:
            raise ValueError("github_delivery_profile_missing")
        return matches[0]


def build_github_delivery_executor() -> GitHubDeliveryExecutor:
    repository = ManagerProviderRepository()
    authorizer = ProviderActionAuthorizer(repository, clock=lambda: datetime.now(timezone.utc))

    def execution_service(_profile: ProviderProfileRecord) -> ProviderExecutionService:
        return ProviderExecutionService(
            repository,
            authorizer,
            adapters={"github": GitHubProviderAdapter()},
        )

    return GitHubDeliveryExecutor(
        repository,
        authorizer,
        execution_service_factory=execution_service,
    )
