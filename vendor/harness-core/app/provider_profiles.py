from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


PROVIDER_PROFILE_SCHEMA_VERSION = "his-provider-profiles.v1"
PROVIDER_CONNECTION_TEST_PLAN_SCHEMA_VERSION = "his-provider-connection-test-plan.v1"
PROVIDER_PROFILE_STORE_SCHEMA_VERSION = "his-provider-profile-store.v1"
DEFAULT_PROVIDER_PROFILE_STORE = Path("/Users/lym/WorkCode/ai/his-knowledge/config/provider_profiles.json")
# GitHub is a fixed-host, read-only evidence provider.  Its token remains in
# the Manager credential store; profile metadata only records public identity.
SUPPORTED_PROVIDERS = frozenset(("yunxiao", "gitlab", "git", "github", "database", "model", "knowledge"))
DATABASE_CONNECTION_IDENTITY_FIELDS = ("driver", "host", "port", "database", "schema")
_SECRET_PATTERNS = (
    re.compile(r"Authorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/=-]{24,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE),
)
_SENSITIVE_FIELD_NAMES = frozenset(
    (
        "authorization",
        "api_key",
        "apikey",
        "credential",
        "credentials",
        "password",
        "passwd",
        "secret",
        "client_secret",
        "secret_key",
        "token",
        "access_token",
        "refresh_token",
        "private_key",
    )
)


def default_provider_profiles() -> list[dict[str, Any]]:
    """Return safe local profile templates for Manager rendering.

    The templates only carry credential reference names and connection identity
    placeholders. They are not executable connection settings and they never
    contain secret values.
    """

    database_identity = {
        "driver": "",
        "host": "",
        "port": "",
        "database": "",
        "schema": "",
    }
    return [
        {
            "provider": "yunxiao",
            "profile_key": "default-yunxiao",
            "credential_ref": "aliyun_devops_pat",
            "connection": {
                "organization_id_ref": "aliyun_devops_organization_id",
                "project_key": "",
            },
        },
        {
            "provider": "gitlab",
            "profile_key": "default-gitlab",
            "credential_ref": "gitlab_token",
            "connection": {
                "host": "",
                "group": "",
            },
        },
        {
            "provider": "github",
            "profile_key": "default-github",
            "credential_ref": "github_access_token",
            "connection": {
                "owner": "",
                "repository": "",
            },
        },
        {
            "provider": "git",
            "profile_key": "default-git",
            "credential_ref": "local_git_identity",
            "connection": {
                "remote": "origin",
                "branch_policy": "protected-branch-block",
            },
        },
        {
            "provider": "database",
            "profile_key": "default-database",
            "credential_ref": "his_db_readonly",
            "connection": dict(database_identity),
            "test_connection": dict(database_identity),
        },
        {
            "provider": "knowledge",
            "profile_key": "default-knowledge",
            "credential_ref": "HIS_KNOWLEDGE_HOME",
            "connection": {
                "home_ref": "HIS_KNOWLEDGE_HOME",
            },
        },
        {
            "provider": "model",
            "profile_key": "default-model",
            "credential_ref": "model_provider_api_key_ref",
            "connection": {
                "provider": "",
                "model": "",
            },
        },
    ]


def resolve_provider_profile_store_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()
    configured = os.environ.get("HARNESS_PROVIDER_PROFILE_STORE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_PROVIDER_PROFILE_STORE


def load_provider_profiles(path: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    from app.manager_provider_repository import ManagerProviderRepository

    repository = ManagerProviderRepository()
    records = repository.list_profiles()
    if records:
        return [_compatibility_profile(record) for record in records]
    store_path = resolve_provider_profile_store_path(path)
    if store_path.exists():
        import_legacy_provider_profiles(store_path, repository)
        records = repository.list_profiles()
        if records:
            return [_compatibility_profile(record) for record in records]
    return default_provider_profiles()


def import_legacy_provider_profiles(path: str | os.PathLike[str], repository: Any) -> dict[str, Any]:
    """Import a v1 JSON store once without changing it or migrating credential values."""

    from app.provider_field_schema import validate_provider_connection

    source_path = Path(path).expanduser().resolve()
    source_bytes = source_path.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    try:
        payload = json.loads(source_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("provider legacy import source must be valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("provider profile store must be a mapping")
    if payload.get("schema_version") != PROVIDER_PROFILE_STORE_SCHEMA_VERSION:
        raise ValueError("unsupported provider profile store schema")
    profiles = payload.get("profiles")
    if not isinstance(profiles, Sequence) or isinstance(profiles, (str, bytes)):
        raise ValueError("provider profile store profiles must be a sequence")

    sanitized: list[dict[str, Any]] = []
    for profile in profiles:
        if not isinstance(profile, Mapping):
            raise ValueError("provider profile must be a mapping")
        _reject_sensitive_values(profile)
        provider = _required_text(profile.get("provider"), "provider")
        profile_key = _required_text(profile.get("profile_key"), "profile_key")
        _required_text(profile.get("credential_ref"), "credential_ref")
        connection = _mapping(profile.get("connection", {}), "connection")
        allowed_connection = validate_provider_connection(
            provider,
            {
                field: value
                for field, value in connection.items()
                if field in _legacy_allowed_connection_fields(provider)
            },
        )
        sanitized.append(
            {
                "provider": provider,
                "profile_key": profile_key,
                "display_name": profile_key,
                "enabled": True,
                "connection": allowed_connection,
            }
        )

    result = repository.import_profiles_once(
        source_sha256=source_sha256,
        profiles=sanitized,
    )
    return _legacy_import_result(result.status, source_sha256, result.imported_count)


def _legacy_allowed_connection_fields(provider: str) -> frozenset[str]:
    from app.provider_field_schema import PROVIDER_CONNECTION_FIELDS

    fields = PROVIDER_CONNECTION_FIELDS.get(provider)
    if fields is None:
        raise ValueError("unsupported provider")
    return frozenset(fields)


def _legacy_import_result(status: str, source_sha256: str, imported_count: int) -> dict[str, Any]:
    return {
        "status": status,
        "source_sha256": source_sha256,
        "imported_count": imported_count,
        "credentials_imported": False,
        "source_changed": False,
        "secret_values_rendered": False,
    }


def _compatibility_profile(record: Any) -> dict[str, Any]:
    profile = {
        "provider": record.provider,
        "profile_key": record.profile_key,
        "credential_ref": f"manager_provider_credentials:{record.provider}",
        "connection": dict(record.connection),
    }
    if record.provider == "database":
        profile["test_connection"] = dict(record.connection)
    return profile


def save_provider_profiles(
    profiles: Sequence[Mapping[str, Any]],
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    store_path = resolve_provider_profile_store_path(path)
    sanitized = [_sanitize_profile_for_store(profile) for profile in profiles]
    build_provider_profile_status(sanitized)
    payload = {
        "schema_version": PROVIDER_PROFILE_STORE_SCHEMA_VERSION,
        "profiles": sanitized,
    }
    store_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = store_path.with_name(f".{store_path.name}.tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(store_path)
    return {
        "schema_version": PROVIDER_PROFILE_STORE_SCHEMA_VERSION,
        "changed": True,
        "path": str(store_path),
        "profile_count": len(sanitized),
        "secret_values_rendered": False,
    }


def upsert_provider_profile(
    profile: Mapping[str, Any],
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    store_path = resolve_provider_profile_store_path(path)
    existing = _read_stored_provider_profiles(store_path) if store_path.exists() else []
    sanitized = _sanitize_profile_for_store(profile)
    profiles = [
        item
        for item in existing
        if not (
            item.get("provider") == sanitized["provider"]
            and item.get("profile_key") == sanitized["profile_key"]
        )
    ]
    profiles.append(sanitized)
    return save_provider_profiles(profiles, store_path)


def provider_profile_from_form(data: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    provider = _form_text(data, "provider")
    profile = {
        "provider": provider,
        "profile_key": _form_text(data, "profile_key"),
        "credential_ref": _form_text(data, "credential_ref"),
        "connection": _form_json_mapping(data, "connection_json"),
    }
    test_connection = _form_json_mapping(data, "test_connection_json", default={})
    if test_connection or provider == "database":
        profile["test_connection"] = test_connection
    return _sanitize_profile_for_store(profile)


def build_provider_profile_status(profiles: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build a redacted, read-only status for external provider profiles.

    This function does not test network connections and does not read credential
    values. It only validates profile shape and exposes whether a later test
    connection would use the same connection identity as the runtime profile.
    """

    if not isinstance(profiles, Sequence) or isinstance(profiles, (str, bytes)):
        raise ValueError("provider profiles must be a sequence")
    normalized = [_normalize_profile(profile) for profile in profiles]
    return {
        "schema_version": PROVIDER_PROFILE_SCHEMA_VERSION,
        "changed": False,
        "secret_values_rendered": False,
        "profile_count": len(normalized),
        "profiles": normalized,
        "next_actions": [
            "在 Manager 中维护 credential_ref，不显示 secret 原文。",
            "测试连接必须复用同一 profile 的连接身份字段。",
            "真实写动作仍需 dry-run、审核确认和审计。",
        ],
    }


def build_provider_connection_test_plan(profiles: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build an inert connection-test plan for Manager review.

    This is a planning artifact only: no credentials are resolved, no network
    connection is attempted, and no provider state is changed. The later read
    executor uses the Profile token or readonly endpoint/credential as its
    technical authority and does not require a separate Harness confirmation.
    """

    status = build_provider_profile_status(profiles)
    tests = [
        _build_connection_test_item(profile)
        for profile in status["profiles"]
    ]
    return {
        "schema_version": PROVIDER_CONNECTION_TEST_PLAN_SCHEMA_VERSION,
        "changed": False,
        "credentials_read": False,
        "external_calls": False,
        "execution_allowed": False,
        "confirmation_required": False,
        "tests": tests,
        "next_actions": [
            "由一次性计划绑定 Provider、Profile、目标、请求人和参数。",
            "由 personal token 或 readonly endpoint/credential 决定技术访问权限。",
            "连接测试结果必须落审计记录，不能自动升级为写权限。",
        ],
    }


def _build_connection_test_item(profile: Mapping[str, Any]) -> dict[str, Any]:
    blockers = [str(issue) for issue in profile.get("issues") or []]
    return {
        "provider": str(profile.get("provider") or ""),
        "profile_key": str(profile.get("profile_key") or ""),
        "status": "blocked" if blockers else "planned",
        "blockers": blockers,
        "confirmation_required": False,
        "credentials_read": False,
        "external_calls": False,
        "execution_allowed": not blockers,
        "required_before_execution": [
            "credential_ref_resolves",
            "network_allowlist_or_local_driver_available",
            "target_actor_and_parameter_bound_plan",
            "redacted_audit",
        ],
    }


def _read_stored_provider_profiles(store_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("provider profile store must be a mapping")
    profiles = payload.get("profiles")
    if not isinstance(profiles, Sequence) or isinstance(profiles, (str, bytes)):
        raise ValueError("provider profile store profiles must be a sequence")
    return [_sanitize_profile_for_store(profile) for profile in profiles]


def _sanitize_profile_for_store(profile: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, Mapping):
        raise ValueError("provider profile must be a mapping")
    _reject_sensitive_values(profile)
    provider = _required_text(profile.get("provider"), "provider")
    profile_key = _required_text(profile.get("profile_key"), "profile_key")
    credential_ref = _required_text(profile.get("credential_ref"), "credential_ref")
    connection = dict(_mapping(profile.get("connection", {}), "connection"))
    test_connection = dict(_mapping(profile.get("test_connection", {}), "test_connection"))
    sanitized = {
        "provider": provider,
        "profile_key": profile_key,
        "credential_ref": credential_ref,
        "connection": connection,
    }
    if test_connection or provider == "database":
        sanitized["test_connection"] = test_connection
    build_provider_profile_status([sanitized])
    return sanitized


def _normalize_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, Mapping):
        raise ValueError("provider profile must be a mapping")
    _reject_sensitive_values(profile)
    provider = _required_text(profile.get("provider"), "provider")
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError("unsupported provider")
    profile_key = _required_text(profile.get("profile_key"), "profile_key")
    credential_ref = _required_text(profile.get("credential_ref"), "credential_ref")
    connection = _mapping(profile.get("connection", {}), "connection")
    test_connection = _mapping(profile.get("test_connection", {}), "test_connection")
    issues: list[str] = []
    test_connection_matches_runtime = True
    if provider == "database":
        test_connection_matches_runtime = _database_test_connection_matches_runtime(
            connection,
            test_connection,
        )
        if not test_connection_matches_runtime:
            issues.append("database_test_connection_drift")
    return {
        "provider": provider,
        "profile_key": profile_key,
        "credential_ref": credential_ref,
        "credential_value_rendered": False,
        "connection": _redacted_mapping(connection),
        "test_connection": _redacted_mapping(test_connection),
        "test_connection_status": "not_run",
        "test_connection_matches_runtime": test_connection_matches_runtime,
        "issues": issues,
    }


def _database_test_connection_matches_runtime(
    connection: Mapping[str, Any],
    test_connection: Mapping[str, Any],
) -> bool:
    if not test_connection:
        return False
    return all(
        str(connection.get(field, "")).strip() == str(test_connection.get(field, "")).strip()
        for field in DATABASE_CONNECTION_IDENTITY_FIELDS
    )


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _form_text(data: Mapping[str, Sequence[str]], name: str) -> str:
    values = data.get(name)
    value = values[0] if values else ""
    return _required_text(value, name)


def _form_json_mapping(
    data: Mapping[str, Sequence[str]],
    name: str,
    *,
    default: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    values = data.get(name)
    raw = values[0].strip() if values and values[0].strip() else ""
    if not raw:
        return default if default is not None else {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be valid JSON") from exc
    return _mapping(parsed, name)


def _redacted_mapping(value: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(key): str(item)
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, (str, int, float, bool))
    }


def _reject_sensitive_values(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_field_name(key_text):
                raise ValueError("sensitive field is not accepted")
            _reject_sensitive_values(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            _reject_sensitive_values(item)
        return
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if any(pattern.search(rendered) for pattern in _SECRET_PATTERNS):
        raise ValueError("sensitive value is not accepted")


def _is_sensitive_field_name(name: str) -> bool:
    normalized = name.strip().lower().replace("-", "_")
    if normalized.endswith("_ref"):
        return False
    return normalized in _SENSITIVE_FIELD_NAMES
