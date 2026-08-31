from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from os import PathLike
from typing import Any

from app.manager_provider_repository import ManagerProviderRepository
from app.provider_action_authorization import (
    ProviderActionAuthorization,
    ProviderActionAuthorizer,
)
from app.provider_execution import (
    ACTION_DESCRIPTORS,
    ProviderExecutionRequest,
    ProviderExecutionService,
)
from app.provider_field_schema import PROVIDER_CONNECTION_FIELDS
from app.provider_profiles import build_provider_profile_status
from app.sensitive_text import validate_public_identifier


PROVIDER_CONNECTION_TEST_RESULT_SCHEMA_VERSION = "his-provider-connection-test-result.v2"
PROVIDER_CONNECTION_TEST_AUDIT_SCHEMA_VERSION = "his-provider-connection-test-audit.v3"


def run_provider_connection_test(
    profiles: Sequence[Mapping[str, Any]],
    *,
    provider: str,
    profile_key: str,
    requested_by: str,
    audit_path: str | PathLike[str] | None = None,
    authorization: ProviderActionAuthorization | None = None,
    repository: ManagerProviderRepository | None = None,
    authorizer: ProviderActionAuthorizer | None = None,
    execution_service: ProviderExecutionService | None = None,
    plan_id: int | None = None,
) -> dict[str, Any]:
    """Create a connection-health plan or delegate its execution to the service."""

    del audit_path
    safe_provider = validate_public_identifier(
        provider, allowed_values=PROVIDER_CONNECTION_FIELDS
    )
    safe_profile_key = validate_public_identifier(profile_key)
    safe_requested_by = validate_public_identifier(requested_by)
    normalized = build_provider_profile_status(profiles)["profiles"]
    public_profile = _find_profile(normalized, safe_provider, safe_profile_key)
    if public_profile is None:
        return _blocked_result(
            provider=safe_provider,
            profile_key=safe_profile_key,
            requested_by=safe_requested_by,
            reason="provider_profile_not_found",
        )
    issues = [str(issue) for issue in public_profile.get("issues") or []]
    if issues:
        return _blocked_result(
            provider=safe_provider,
            profile_key=safe_profile_key,
            requested_by=safe_requested_by,
            reason=issues[0],
        )

    manager_repository = repository or ManagerProviderRepository()
    manager_profile = _manager_profile(
        manager_repository, provider=safe_provider, profile_key=safe_profile_key
    )
    if manager_profile is None:
        return _blocked_result(
            provider=safe_provider,
            profile_key=safe_profile_key,
            requested_by=safe_requested_by,
            reason="manager_provider_profile_not_found",
        )
    action = f"{safe_provider}.connection_test"
    descriptor = ACTION_DESCRIPTORS.get(action)
    if descriptor is None:
        return _blocked_result(
            provider=safe_provider,
            profile_key=safe_profile_key,
            requested_by=safe_requested_by,
            reason="provider_action_not_registered",
        )
    parameters: dict[str, object] = {"timeout_seconds": descriptor.max_timeout_seconds}
    target_alias = f"{safe_provider}.{safe_profile_key}"
    if safe_provider == "database":
        # The database adapter binds every connection and query to a non-secret
        # database alias; it never derives a target from a host/path string.
        target_alias = f"db-{safe_profile_key}"
        parameters["database_alias"] = target_alias
    elif safe_provider == "github":
        # GitHub health is account/token connectivity only. Repository identity
        # is bound later by each individual repository, issue, or PR read plan.
        target_alias = "github.connection"
    action_authorizer = authorizer or ProviderActionAuthorizer(
        manager_repository, clock=lambda: datetime.now(timezone.utc)
    )

    if authorization is not None and plan_id is None:
        return _blocked_result(
            provider=safe_provider,
            profile_key=safe_profile_key,
            requested_by=safe_requested_by,
            reason="provider_execution_plan_required",
        )
    if plan_id is not None:
        service = execution_service or ProviderExecutionService(
            manager_repository, action_authorizer
        )
        return service.execute(
            authorization,
            ProviderExecutionRequest(
                plan_id=plan_id,
                actor=safe_requested_by,
                action=action,
                parameters=parameters,
            ),
        )

    plan = action_authorizer.create_plan(
        profile_id=manager_profile.id,
        action=action,
        target_alias=target_alias,
        parameters=parameters,
        requested_by=safe_requested_by,
    )
    return {
        "schema_version": PROVIDER_CONNECTION_TEST_RESULT_SCHEMA_VERSION,
        "plan_id": plan.id,
        "provider": safe_provider,
        "profile_key": safe_profile_key,
        "requested_by": safe_requested_by,
        "action": action,
        "risk": descriptor.risk,
        "status": "ready_to_execute",
        "reason": "provider_technical_authority_required",
        "credentials_read": False,
        "external_calls": False,
        "execution_allowed": True,
        "confirmation_required": False,
    }


def load_provider_connection_test_audit(
    _legacy_path: str | PathLike[str] | None = None,
    *,
    repository: ManagerProviderRepository | None = None,
) -> dict[str, Any]:
    """Read service-produced connection execution results from Manager DB."""

    del _legacy_path
    rows = (repository or ManagerProviderRepository()).list_action_audits(limit=100)
    records = [
        dict(row["details"])
        for row in rows
        if str(row["action_type"]).endswith(".connection_test")
        and "plan_id" in row["details"]
        and "verification_status" in row["details"]
    ]
    return {
        "schema_version": PROVIDER_CONNECTION_TEST_AUDIT_SCHEMA_VERSION,
        "changed": False,
        "record_count": len(records),
        "records": records,
        "credentials_read": False,
        "external_calls": False,
    }


def _blocked_result(
    *, provider: str, profile_key: str, requested_by: str, reason: str
) -> dict[str, Any]:
    return {
        "schema_version": PROVIDER_CONNECTION_TEST_RESULT_SCHEMA_VERSION,
        "plan_id": None,
        "provider": provider,
        "profile_key": profile_key,
        "requested_by": requested_by,
        "action": f"{provider}.connection_test",
        "risk": "",
        "status": "blocked",
        "reason": reason,
        "credentials_read": False,
        "external_calls": False,
        "execution_allowed": False,
    }


def _manager_profile(
    repository: ManagerProviderRepository, *, provider: str, profile_key: str
):
    return next(
        (
            item
            for item in repository.list_profiles()
            if item.provider == provider and item.profile_key == profile_key
        ),
        None,
    )


def _find_profile(
    profiles: Sequence[Mapping[str, Any]], provider: str, profile_key: str
) -> Mapping[str, Any] | None:
    return next(
        (
            item
            for item in profiles
            if item.get("provider") == provider and item.get("profile_key") == profile_key
        ),
        None,
    )
