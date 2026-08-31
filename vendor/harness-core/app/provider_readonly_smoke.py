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
from app.provider_capability_status import build_provider_capability_status
from app.provider_execution import (
    ACTION_DESCRIPTORS,
    ProviderExecutionRequest,
    ProviderExecutionService,
)
from app.provider_field_schema import PROVIDER_CONNECTION_FIELDS
from app.provider_profiles import build_provider_profile_status
from app.sensitive_text import contains_sensitive_text, validate_public_identifier


PROVIDER_READONLY_SMOKE_PLAN_SCHEMA_VERSION = "his-provider-readonly-smoke-plan.v2"
PROVIDER_READONLY_SMOKE_RESULT_SCHEMA_VERSION = "his-provider-readonly-smoke-result.v2"
PROVIDER_READONLY_SMOKE_AUDIT_SCHEMA_VERSION = "his-provider-readonly-smoke-audit.v3"
# Compatibility value for older callers that still pass ``confirmation_text``.
# The value is descriptive context only and never grants or confirms execution.
LOCAL_READONLY_SMOKE_CONFIRMATION = "本地、只读、免凭证且离线的 Git smoke 检查"
PROVIDER_READONLY_SMOKE_ACTION_TYPE = "git.readonly_smoke"


def build_provider_readonly_smoke_plan(
    profiles: Sequence[Mapping[str, Any]], *, manifest_path: str | None = None
) -> dict[str, Any]:
    """Describe readonly smoke actions without inspecting a repository or executing Git."""

    status = build_provider_profile_status(profiles)
    capability_items = build_provider_capability_status(profiles, manifest_path)["items"]
    items = []
    for profile, capability_item in zip(status["profiles"], capability_items):
        provider = _safe_identifier(str(profile["provider"]), declared_provider=True)
        profile_key = _safe_identifier(str(profile["profile_key"]))
        issues = [str(issue) for issue in profile.get("issues") or []]
        supported = provider == "git" and not issues
        items.append(
            {
                "provider": provider,
                "profile_key": profile_key,
                "action": PROVIDER_READONLY_SMOKE_ACTION_TYPE if provider == "git" else "",
                "status": "ready_to_execute" if supported else "blocked",
                "reason": (
                    "provider_technical_authority_required"
                    if supported
                    else (issues[0] if issues else "provider_readonly_smoke_adapter_not_registered")
                ),
                "confirmation_required": False,
                "credentials_read": False,
                "external_calls": False,
                "write_performed": False,
                "adapter": "provider_execution_service" if supported else "not_registered",
                "canonical_capability_status": _project_canonical_capability_status(
                    capability_item
                ),
            }
        )
    return {
        "schema_version": PROVIDER_READONLY_SMOKE_PLAN_SCHEMA_VERSION,
        "changed": False,
        "credentials_read": False,
        "external_calls": False,
        "write_performed": False,
        "confirmation_required": False,
        "items": items,
        "next_actions": [
            "由 ProviderActionAuthorizer 创建并绑定一次性只读计划。",
            "由 ProviderExecutionService 免人工确认委派已注册的只读 adapter。",
        ],
    }


def run_provider_readonly_smoke(
    profiles: Sequence[Mapping[str, Any]],
    *,
    provider: str,
    profile_key: str,
    requested_by: str,
    confirmation_text: str,
    audit_path: str | PathLike[str] | None = None,
    repository: ManagerProviderRepository | None = None,
    authorizer: ProviderActionAuthorizer | None = None,
    execution_service: ProviderExecutionService | None = None,
    plan_id: int | None = None,
    authorization: ProviderActionAuthorization | None = None,
) -> dict[str, Any]:
    """Create a readonly smoke plan or delegate a governed plan to the service."""

    del audit_path
    safe_provider = validate_public_identifier(
        provider, allowed_values=PROVIDER_CONNECTION_FIELDS
    )
    safe_profile_key = validate_public_identifier(profile_key)
    safe_requested_by = validate_public_identifier(requested_by)
    # Retained as a non-authorizing compatibility input for older API clients.
    if not isinstance(confirmation_text, str) or contains_sensitive_text(confirmation_text):
        raise ValueError("provider_audit_input_invalid")
    if safe_provider != "git":
        return _blocked_result(
            safe_provider,
            safe_profile_key,
            safe_requested_by,
            "provider_readonly_smoke_adapter_not_registered",
        )

    normalized = build_provider_profile_status(profiles)["profiles"]
    public_profile = _find_profile(normalized, safe_provider, safe_profile_key)
    if public_profile is None:
        return _blocked_result(
            safe_provider, safe_profile_key, safe_requested_by, "provider_profile_not_found"
        )
    issues = [str(issue) for issue in public_profile.get("issues") or []]
    if issues:
        return _blocked_result(
            safe_provider, safe_profile_key, safe_requested_by, issues[0]
        )

    manager_repository = repository or ManagerProviderRepository()
    manager_profile = _manager_profile(
        manager_repository, provider=safe_provider, profile_key=safe_profile_key
    )
    if manager_profile is None:
        return _blocked_result(
            safe_provider,
            safe_profile_key,
            safe_requested_by,
            "manager_provider_profile_not_found",
        )
    descriptor = ACTION_DESCRIPTORS[PROVIDER_READONLY_SMOKE_ACTION_TYPE]
    parameters = {"timeout_seconds": descriptor.max_timeout_seconds}
    action_authorizer = authorizer or ProviderActionAuthorizer(
        manager_repository, clock=lambda: datetime.now(timezone.utc)
    )

    if authorization is not None and plan_id is None:
        return _blocked_result(
            safe_provider,
            safe_profile_key,
            safe_requested_by,
            "provider_execution_plan_required",
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
                action=PROVIDER_READONLY_SMOKE_ACTION_TYPE,
                parameters=parameters,
            ),
        )

    plan = action_authorizer.create_plan(
        profile_id=manager_profile.id,
        action=PROVIDER_READONLY_SMOKE_ACTION_TYPE,
        target_alias=f"git.{safe_profile_key}",
        parameters=parameters,
        requested_by=safe_requested_by,
    )
    return {
        "schema_version": PROVIDER_READONLY_SMOKE_RESULT_SCHEMA_VERSION,
        "plan_id": plan.id,
        "provider": safe_provider,
        "profile_key": safe_profile_key,
        "requested_by": safe_requested_by,
        "action": PROVIDER_READONLY_SMOKE_ACTION_TYPE,
        "risk": descriptor.risk,
        "status": "ready_to_execute",
        "reason": "provider_technical_authority_required",
        "credentials_read": False,
        "external_calls": False,
        "write_performed": False,
        "confirmation_required": False,
    }


def record_provider_readonly_smoke_failure(
    *,
    provider: str,
    profile_key: str,
    requested_by: str,
    reason: str = "provider_readonly_smoke_execution_failed",
    repository: ManagerProviderRepository | None = None,
) -> dict[str, Any]:
    """Compatibility response only; result persistence belongs to the execution service."""

    del repository
    return _blocked_result(
        validate_public_identifier(provider, allowed_values=PROVIDER_CONNECTION_FIELDS),
        validate_public_identifier(profile_key),
        validate_public_identifier(requested_by),
        reason,
        status="failed",
    )


def build_provider_readonly_smoke_audit_failure(
    *, provider: str, profile_key: str, requested_by: str
) -> dict[str, Any]:
    return _blocked_result(
        _safe_identifier(provider, declared_provider=True),
        _safe_identifier(profile_key),
        _safe_identifier(requested_by),
        "provider_readonly_smoke_audit_failed",
        status="failed",
    )


def load_provider_readonly_smoke_audit(
    _legacy_path: str | PathLike[str] | None = None,
    *,
    repository: ManagerProviderRepository | None = None,
) -> dict[str, Any]:
    del _legacy_path
    rows = (repository or ManagerProviderRepository()).list_action_audits(
        action_type=PROVIDER_READONLY_SMOKE_ACTION_TYPE, limit=100
    )
    records = [
        dict(row["details"])
        for row in rows
        if "plan_id" in row["details"] and "verification_status" in row["details"]
    ]
    return {
        "schema_version": PROVIDER_READONLY_SMOKE_AUDIT_SCHEMA_VERSION,
        "changed": False,
        "record_count": len(records),
        "records": records,
        "credentials_read": False,
        "external_calls": False,
        "write_performed": False,
    }


def _blocked_result(
    provider: str,
    profile_key: str,
    requested_by: str,
    reason: str,
    *,
    status: str = "blocked",
) -> dict[str, Any]:
    return {
        "schema_version": PROVIDER_READONLY_SMOKE_RESULT_SCHEMA_VERSION,
        "plan_id": None,
        "provider": provider,
        "profile_key": profile_key,
        "requested_by": requested_by,
        "action": PROVIDER_READONLY_SMOKE_ACTION_TYPE if provider == "git" else "",
        "risk": "read" if provider == "git" else "",
        "status": status,
        "reason": reason,
        "credentials_read": False,
        "external_calls": False,
        "write_performed": False,
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


def _project_canonical_capability_status(
    capability_item: Mapping[str, Any],
) -> dict[str, Any]:
    projected = {
        key: capability_item[key]
        for key in (
            "provider_plugin",
            "skill",
            "inspect_capability",
            "status",
            "reason",
            "execution_status",
            "execution_reason",
        )
        if key in capability_item
    }
    projected["capabilities"] = [
        {
            key: capability[key]
            for key in (
                "name",
                "skill",
                "contract_status",
                "execution_status",
                "execution_reason",
            )
            if key in capability
        }
        for capability in capability_item.get("capabilities", [])
        if isinstance(capability, Mapping)
    ]
    return projected


def _safe_identifier(value: object, *, declared_provider: bool = False) -> str:
    try:
        return validate_public_identifier(
            value,
            allowed_values=PROVIDER_CONNECTION_FIELDS if declared_provider else None,
        )
    except ValueError:
        return "invalid"
