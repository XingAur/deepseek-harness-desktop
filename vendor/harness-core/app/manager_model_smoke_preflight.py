from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.manager_credential_crypto import (
    AesGcmCredentialCipher,
    CredentialEncryptionUnavailable,
)
from app.runtime_policy import runtime_policy_snapshot


MANAGER_MODEL_SMOKE_PREFLIGHT_SCHEMA_VERSION = "his-manager-model-smoke-preflight.v1"


def _build_model_smoke_preflight_without_readiness(
    profile: Mapping[str, Any] | None,
    *,
    requested_profile_key: str = "",
) -> dict[str, Any]:
    """Describe model smoke configuration readiness without executing anything.

    This boundary deliberately does not decrypt provider credentials, construct a
    provider runtime, call a transport, or open a network connection. A ``ready``
    result means configuration preparation only; the separately authorized smoke
    execution remains outside this API.
    """

    policy = runtime_policy_snapshot()
    try:
        AesGcmCredentialCipher.from_environment()
    except CredentialEncryptionUnavailable:
        encryption_available = False
    else:
        encryption_available = True
    profile_found = isinstance(profile, Mapping)
    provider = str(profile.get("provider") or "") if profile_found else ""
    profile_key = (
        str(profile.get("profile_key") or "")
        if profile_found
        else str(requested_profile_key or "")
    )
    enabled = bool(profile.get("enabled")) if profile_found else False
    connection = profile.get("connection") if profile_found else None
    safe_connection = connection if isinstance(connection, Mapping) else {}
    connection_configured = all(
        bool(str(safe_connection.get(field) or "").strip())
        for field in (
            "provider_kind",
            "base_url",
            "allowed_endpoint_host",
            "model",
            "timeout_seconds",
            "max_output_tokens",
        )
    )
    credential_statuses = profile.get("credentials") if profile_found else None
    credential_configured = (
        isinstance(credential_statuses, Mapping)
        and credential_statuses.get("api_key") == "configured"
    )
    single_node_contract = policy.real_model_smoke_allowed

    prerequisites = [
        _prerequisite("manager_encryption_key", encryption_available),
        _prerequisite("model_profile_found", profile_found),
        _prerequisite("model_profile_enabled", profile_found and provider == "model" and enabled),
        _prerequisite(
            "model_connection_configured",
            profile_found and provider == "model" and connection_configured,
        ),
        _prerequisite(
            "api_key_configured",
            profile_found and provider == "model" and credential_configured,
        ),
        _prerequisite("single_node_smoke_contract", single_node_contract),
        {
            "id": "real_model_team_runtime_frozen",
            "status": "passed" if policy.real_model_runtime_frozen else "missing",
        },
    ]

    if not encryption_available:
        reason = "encryption_unavailable"
    elif not profile_found:
        reason = "profile_not_found"
    elif provider != "model":
        reason = "profile_not_model"
    elif not enabled:
        reason = "profile_disabled"
    elif not connection_configured:
        reason = "connection_incomplete"
    elif not credential_configured:
        reason = "credential_not_configured"
    elif not single_node_contract:
        reason = "single_node_smoke_not_allowed"
    else:
        reason = "configuration_preflight_only"

    return {
        "schema_version": MANAGER_MODEL_SMOKE_PREFLIGHT_SCHEMA_VERSION,
        "status": "ready" if reason == "configuration_preflight_only" else "blocked",
        "reason": reason,
        "profile": {
            "provider": provider or "model",
            "profile_key": profile_key,
        },
        "credential_configured": bool(credential_configured),
        "prerequisites": prerequisites,
        "runtime_verified": False,
        "credentials_read": False,
        "external_calls": False,
        "write_performed": False,
        "real_model_dag_enabled": False,
    }


def build_model_smoke_preflight(
    profile: Mapping[str, Any] | None,
    *,
    requested_profile_key: str = "",
) -> dict[str, Any]:
    result = _build_model_smoke_preflight_without_readiness(
        profile,
        requested_profile_key=requested_profile_key,
    )
    readiness = build_manager_model_smoke_readiness(profile)
    result["smoke_state"] = readiness["smoke_state"]
    result["dag_state"] = readiness["dag_state"]
    return result


def build_manager_model_smoke_readiness(
    profile: Mapping[str, Any] | None,
    *,
    last_smoke: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose the finite Manager readiness states without executing a model.

    This uses preflight metadata only.  It neither decrypts a key nor copies a
    persisted smoke record wholesale, so Manager UI/API callers cannot render
    raw payloads, headers, authorization data, or upstream response text.
    """

    preflight = _build_model_smoke_preflight_without_readiness(profile)
    policy = runtime_policy_snapshot()
    configured = preflight["status"] == "ready"
    if not configured:
        smoke_state = "configuration_missing"
    elif isinstance(last_smoke, Mapping):
        smoke_state = (
            "smoke_passed"
            if last_smoke.get("status") == "passed"
            and last_smoke.get("marker_status") == "passed"
            else "smoke_failed"
        )
    else:
        smoke_state = "awaiting_confirmation"
    return {
        "schema_version": MANAGER_MODEL_SMOKE_PREFLIGHT_SCHEMA_VERSION,
        "smoke_state": smoke_state,
        "dag_state": "dag_still_frozen" if policy.real_model_runtime_frozen else "dag_not_frozen",
        "real_model_dag_enabled": False,
        "credentials_read": False,
        "external_calls": False,
        "runtime_verified": smoke_state == "smoke_passed",
    }


def _prerequisite(identifier: str, passed: bool) -> dict[str, str]:
    return {"id": identifier, "status": "passed" if passed else "missing"}
