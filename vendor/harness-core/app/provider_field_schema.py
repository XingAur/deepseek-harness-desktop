from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.provider_profiles import DATABASE_CONNECTION_IDENTITY_FIELDS, _reject_sensitive_values
from app.sensitive_text import contains_sensitive_scalar_text


PROVIDER_CONNECTION_FIELDS = {
    "yunxiao": ("organization_id", "project_id", "project_key", "workitem_scope"),
    "git": ("repository_path", "remote", "branch_policy", "allowed_paths"),
    "gitlab": ("host", "group", "project", "target_branch"),
    "github": ("owner", "repository"),
    "database": (
        "driver",
        "host",
        "port",
        "database",
        "schema",
        "username",
        "readonly_policy",
    ),
    "model": (
        "provider_kind",
        "base_url",
        "model",
        "allowed_endpoint_host",
        "timeout_seconds",
        "max_output_tokens",
    ),
    "knowledge": ("knowledge_home", "obsidian_vault", "index_path", "allowed_sources"),
}

PROVIDER_CREDENTIAL_FIELDS = {
    "yunxiao": ("pat",),
    "git": ("https_token", "ssh_private_key"),
    "gitlab": ("access_token",),
    "github": ("access_token",),
    "database": ("password",),
    "model": ("api_key",),
    "knowledge": (),
}

_COMMON_FORM_FIELDS = frozenset(("provider", "profile_key", "display_name", "enabled"))
_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


@dataclass(frozen=True)
class ProviderFieldSpec:
    name: str
    secret: bool


@dataclass(frozen=True)
class TypedProviderProfile:
    provider: str
    profile_key: str
    display_name: str
    enabled: bool
    connection: dict[str, str]
    credential_inputs: dict[str, str] = field(repr=False)


def provider_field_specs(provider: str) -> tuple[ProviderFieldSpec, ...]:
    provider_name = _provider_name(provider)
    return tuple(
        ProviderFieldSpec(name=name, secret=False)
        for name in PROVIDER_CONNECTION_FIELDS[provider_name]
    ) + tuple(
        ProviderFieldSpec(name=name, secret=True)
        for name in PROVIDER_CREDENTIAL_FIELDS[provider_name]
    )


def provider_profile_from_typed_form(
    data: Mapping[str, Sequence[str]],
) -> TypedProviderProfile:
    if not isinstance(data, Mapping):
        _reject("form_must_be_mapping")
    provider_input = _form_value(data, "provider", required=True)
    profile_key = _form_value(data, "profile_key", required=True)
    display_name = _form_value(data, "display_name") or profile_key
    _reject_sensitive_public_fields(
        {
            "provider": provider_input,
            "profile_key": profile_key,
            "display_name": display_name,
        }
    )
    provider = _provider_name(provider_input)
    allowed = (
        _COMMON_FORM_FIELDS
        | frozenset(PROVIDER_CONNECTION_FIELDS[provider])
        | frozenset(PROVIDER_CREDENTIAL_FIELDS[provider])
    )
    if any(key not in allowed for key in data):
        _reject("unknown_fields")

    connection = {
        field: value
        for field in PROVIDER_CONNECTION_FIELDS[provider]
        if (value := _form_value(data, field))
    }
    connection = validate_provider_connection(provider, connection)
    _reject_sensitive_public_fields(
        {
            "provider": provider,
            "profile_key": profile_key,
            "display_name": display_name,
            "connection": connection,
        }
    )
    credential_inputs = {
        field: value
        for field in PROVIDER_CREDENTIAL_FIELDS[provider]
        if (value := _form_value(data, field, preserve_whitespace=True))
    }
    return TypedProviderProfile(
        provider=provider,
        profile_key=profile_key,
        display_name=display_name,
        enabled=_form_boolean(data, "enabled"),
        connection=connection,
        credential_inputs=credential_inputs,
    )


def validate_provider_connection(
    provider: str,
    connection: Mapping[str, object],
) -> dict[str, str]:
    provider_name = _provider_name(provider)
    if not isinstance(connection, Mapping):
        _reject("connection_must_be_mapping")
    if any(key not in PROVIDER_CONNECTION_FIELDS[provider_name] for key in connection):
        _reject("unknown_connection_fields")
    try:
        _reject_sensitive_values(connection)
    except ValueError as exc:
        _reject(str(exc))
    normalized: dict[str, str] = {}
    for field, value in connection.items():
        if not isinstance(field, str) or not isinstance(value, (str, int, float, bool)):
            _reject("connection_field_must_be_scalar")
        normalized_value = str(value).strip()
        if field == "host" or (
            provider_name == "model" and field == "allowed_endpoint_host"
        ):
            _validate_provider_host(normalized_value)
        normalized[field] = normalized_value
    return normalized


def _validate_provider_host(value: str) -> None:
    if not value or len(value) > 253 or contains_sensitive_scalar_text(value):
        _reject("provider_host_must_be_hostname_or_ipv4")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        labels = value.split(".")
        if not labels or any(_HOST_LABEL.fullmatch(label) is None for label in labels):
            _reject("provider_host_must_be_hostname_or_ipv4")
        if len(labels) == 4 and all(label.isdigit() for label in labels):
            _reject("provider_host_must_be_hostname_or_ipv4")
    else:
        if address.version != 4:
            _reject("provider_host_must_be_hostname_or_ipv4")


def _provider_name(value: object) -> str:
    if not isinstance(value, str) or value.strip() not in PROVIDER_CONNECTION_FIELDS:
        _reject("unsupported_provider")
    return value.strip()


def _reject_sensitive_public_fields(value: Mapping[str, object]) -> None:
    try:
        _reject_sensitive_values(value)
    except ValueError:
        _reject("sensitive_public_field")


def _form_value(
    data: Mapping[str, Sequence[str]],
    name: str,
    *,
    required: bool = False,
    preserve_whitespace: bool = False,
) -> str:
    values = data.get(name)
    if values is None:
        value = ""
    elif isinstance(values, str):
        value = values
    elif isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        value = values[0] if values else ""
    else:
        _reject(f"invalid_form_field:{name}")
    if not isinstance(value, str):
        _reject(f"invalid_form_field:{name}")
    result = value if preserve_whitespace else value.strip()
    if required and not result:
        _reject(f"required_field:{name}")
    return result


def _form_boolean(data: Mapping[str, Sequence[str]], name: str) -> bool:
    value = _form_value(data, name).lower()
    if not value:
        return False
    if value not in {"1", "true", "on", "yes"}:
        _reject(f"invalid_boolean:{name}")
    return True


def _reject(reason: str) -> None:
    raise ValueError(f"provider_field_schema:{reason}")
