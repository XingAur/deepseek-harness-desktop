from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from app.sensitive_text import (
    contains_sensitive_scalar_text,
    contains_sensitive_text,
    is_sensitive_mapping_key,
)


MCP_RESULT_SCHEMA_VERSION = "his-mcp-result-envelope.v1"
_RESULT_STATUSES = frozenset({"success", "failed", "denied", "unavailable", "invalid"})
_FRESHNESS_STATUSES = frozenset({"fresh", "stale", "unknown", "not_applicable"})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_FIELD_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\[\]-]{0,255}$")
_LOWER_HEX_IDENTIFIER = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_EXTRA_SENSITIVE_KEYS = frozenset({"dsn", "connection_string", "connectionstring"})
_MAX_DATA_DEPTH = 16
_MAX_DATA_NODES = 20_000


class McpContractError(ValueError):
    """An MCP result violates the strict envelope or safety contract."""


@dataclass(frozen=True)
class McpSource:
    system: str
    object_id: str
    version: str
    observed_at: str


@dataclass(frozen=True)
class McpFreshness:
    status: str
    expires_at: str


@dataclass(frozen=True)
class McpPagination:
    truncated: bool
    next_cursor: str


@dataclass(frozen=True)
class McpRedaction:
    applied: bool
    fields: tuple[str, ...]


@dataclass(frozen=True)
class McpError:
    code: str
    retryable: bool
    recovery: str


@dataclass(frozen=True)
class McpTrace:
    mcp_server: str
    tool: str
    server_version: str
    trace_id: str


@dataclass(frozen=True)
class McpResultEnvelope:
    schema_version: str
    request_id: str
    capability: str
    provider: str
    status: str
    data: Mapping[str, Any]
    evidence_ref: str
    source: McpSource
    freshness: McpFreshness
    pagination: McpPagination
    redaction: McpRedaction
    error: McpError
    trace: McpTrace


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise McpContractError(f"invalid {label}")
    return value


def _exact(value: Mapping[str, Any], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise McpContractError(f"invalid {label} fields")


def _text(value: Any, label: str, *, allow_empty: bool = False, maximum: int = 512) -> str:
    if not isinstance(value, str) or len(value) > maximum or value != value.strip():
        raise McpContractError(f"invalid {label}")
    if not allow_empty and not value:
        raise McpContractError(f"invalid {label}")
    if value and contains_sensitive_text(value):
        raise McpContractError(f"sensitive {label}")
    return value


def _identifier(value: Any, label: str, *, allow_empty: bool = False) -> str:
    text = _text(value, label, allow_empty=allow_empty, maximum=256)
    if text and _IDENTIFIER.fullmatch(text) is None:
        raise McpContractError(f"invalid {label}")
    return text


def _capability(value: Any, label: str) -> str:
    text = _text(value, label, maximum=128)
    if _CAPABILITY.fullmatch(text) is None:
        raise McpContractError(f"invalid {label}")
    return text


def _timestamp(value: Any, label: str, *, allow_empty: bool = False) -> str:
    text = _text(value, label, allow_empty=allow_empty, maximum=40)
    if not text:
        return text
    if not text.endswith("Z"):
        raise McpContractError(f"invalid {label}")
    try:
        datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise McpContractError(f"invalid {label}") from exc
    return text


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise McpContractError(f"invalid {label}")
    return value


def _data_scalar_safe(value: str) -> bool:
    if contains_sensitive_text(value):
        return False
    if _LOWER_HEX_IDENTIFIER.fullmatch(value):
        return True
    return not contains_sensitive_scalar_text(value)


def _freeze_safe_data(value: Any, *, depth: int, budget: list[int]) -> Any:
    if depth > _MAX_DATA_DEPTH or budget[0] <= 0:
        raise McpContractError("MCP result data exceeds safety limits")
    budget[0] -= 1
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or not key
                or len(key) > 128
                or is_sensitive_mapping_key(key)
                or key.lower().replace("-", "_") in _EXTRA_SENSITIVE_KEYS
                or contains_sensitive_text(key)
            ):
                raise McpContractError("MCP result data contains a sensitive key")
            result[key] = _freeze_safe_data(item, depth=depth + 1, budget=budget)
        return MappingProxyType(result)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(
            _freeze_safe_data(item, depth=depth + 1, budget=budget) for item in value
        )
    if isinstance(value, str):
        if len(value) > 65_536 or not _data_scalar_safe(value):
            raise McpContractError("MCP result data contains sensitive text")
        return value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise McpContractError("MCP result data contains a non-JSON value")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def parse_mcp_result_envelope(
    payload: Mapping[str, Any],
    *,
    expected_request_id: str,
    expected_capability: str,
    expected_provider: str,
) -> McpResultEnvelope:
    root = _mapping(payload, "MCP result envelope")
    _exact(
        root,
        {
            "schema_version",
            "request_id",
            "capability",
            "provider",
            "status",
            "data",
            "evidence_ref",
            "source",
            "freshness",
            "pagination",
            "redaction",
            "error",
            "trace",
        },
        "MCP result envelope",
    )
    if root["schema_version"] != MCP_RESULT_SCHEMA_VERSION:
        raise McpContractError("unsupported MCP result schema version")
    request_id = _identifier(root["request_id"], "request_id")
    capability = _capability(root["capability"], "capability")
    provider = _capability(root["provider"], "provider")
    if (
        request_id != expected_request_id
        or capability != expected_capability
        or provider != expected_provider
    ):
        raise McpContractError("MCP result identity mismatch")
    status = _text(root["status"], "status", maximum=32)
    if status not in _RESULT_STATUSES:
        raise McpContractError("unsupported MCP result status")
    data = _freeze_safe_data(_mapping(root["data"], "data"), depth=0, budget=[_MAX_DATA_NODES])
    evidence_ref = _identifier(root["evidence_ref"], "evidence_ref", allow_empty=True)

    source_payload = _mapping(root["source"], "source")
    _exact(source_payload, {"system", "object_id", "version", "observed_at"}, "source")
    source = McpSource(
        system=_identifier(source_payload["system"], "source.system", allow_empty=True),
        object_id=_identifier(source_payload["object_id"], "source.object_id", allow_empty=True),
        version=_identifier(source_payload["version"], "source.version", allow_empty=True),
        observed_at=_timestamp(
            source_payload["observed_at"], "source.observed_at", allow_empty=True
        ),
    )

    freshness_payload = _mapping(root["freshness"], "freshness")
    _exact(freshness_payload, {"status", "expires_at"}, "freshness")
    freshness_status = _text(freshness_payload["status"], "freshness.status", maximum=32)
    if freshness_status not in _FRESHNESS_STATUSES:
        raise McpContractError("unsupported freshness status")
    freshness = McpFreshness(
        freshness_status,
        _timestamp(freshness_payload["expires_at"], "freshness.expires_at", allow_empty=True),
    )

    pagination_payload = _mapping(root["pagination"], "pagination")
    _exact(pagination_payload, {"truncated", "next_cursor"}, "pagination")
    pagination = McpPagination(
        _boolean(pagination_payload["truncated"], "pagination.truncated"),
        _identifier(pagination_payload["next_cursor"], "pagination.next_cursor", allow_empty=True),
    )
    if pagination.truncated != bool(pagination.next_cursor):
        raise McpContractError("pagination cursor contract invalid")

    redaction_payload = _mapping(root["redaction"], "redaction")
    _exact(redaction_payload, {"applied", "fields"}, "redaction")
    applied = _boolean(redaction_payload["applied"], "redaction.applied")
    raw_fields = redaction_payload["fields"]
    if not isinstance(raw_fields, list):
        raise McpContractError("invalid redaction fields")
    fields = tuple(
        _text(item, "redaction field", maximum=256) for item in raw_fields
    )
    if (
        fields != tuple(sorted(fields))
        or len(fields) != len(set(fields))
        or any(_FIELD_PATH.fullmatch(item) is None for item in fields)
        or applied != bool(fields)
    ):
        raise McpContractError("invalid redaction fields")
    redaction = McpRedaction(applied, fields)

    error_payload = _mapping(root["error"], "error")
    _exact(error_payload, {"code", "retryable", "recovery"}, "error")
    error = McpError(
        _identifier(error_payload["code"], "error.code", allow_empty=True),
        _boolean(error_payload["retryable"], "error.retryable"),
        _text(error_payload["recovery"], "error.recovery", allow_empty=True, maximum=512),
    )

    trace_payload = _mapping(root["trace"], "trace")
    _exact(trace_payload, {"mcp_server", "tool", "server_version", "trace_id"}, "trace")
    trace = McpTrace(
        _identifier(trace_payload["mcp_server"], "trace.mcp_server"),
        _identifier(trace_payload["tool"], "trace.tool"),
        _identifier(trace_payload["server_version"], "trace.server_version"),
        _identifier(trace_payload["trace_id"], "trace.trace_id"),
    )

    if status == "success":
        if (
            not evidence_ref
            or not source.system
            or not source.object_id
            or not source.observed_at
            or error.code
            or error.recovery
            or error.retryable
        ):
            raise McpContractError("success envelope contract invalid")
    elif not error.code or not error.recovery:
        raise McpContractError("failure envelope contract invalid")

    return McpResultEnvelope(
        schema_version=MCP_RESULT_SCHEMA_VERSION,
        request_id=request_id,
        capability=capability,
        provider=provider,
        status=status,
        data=data,
        evidence_ref=evidence_ref,
        source=source,
        freshness=freshness,
        pagination=pagination,
        redaction=redaction,
        error=error,
        trace=trace,
    )


def mcp_envelope_to_dict(envelope: McpResultEnvelope) -> dict[str, Any]:
    return {
        "schema_version": envelope.schema_version,
        "request_id": envelope.request_id,
        "capability": envelope.capability,
        "provider": envelope.provider,
        "status": envelope.status,
        "data": _thaw(envelope.data),
        "evidence_ref": envelope.evidence_ref,
        "source": {
            "system": envelope.source.system,
            "object_id": envelope.source.object_id,
            "version": envelope.source.version,
            "observed_at": envelope.source.observed_at,
        },
        "freshness": {
            "status": envelope.freshness.status,
            "expires_at": envelope.freshness.expires_at,
        },
        "pagination": {
            "truncated": envelope.pagination.truncated,
            "next_cursor": envelope.pagination.next_cursor,
        },
        "redaction": {
            "applied": envelope.redaction.applied,
            "fields": list(envelope.redaction.fields),
        },
        "error": {
            "code": envelope.error.code,
            "retryable": envelope.error.retryable,
            "recovery": envelope.error.recovery,
        },
        "trace": {
            "mcp_server": envelope.trace.mcp_server,
            "tool": envelope.trace.tool,
            "server_version": envelope.trace.server_version,
            "trace_id": envelope.trace.trace_id,
        },
    }


def canonical_json_size(payload: Mapping[str, Any]) -> int:
    if not isinstance(payload, Mapping):
        raise McpContractError("canonical JSON payload must be an object")
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise McpContractError("canonical JSON payload is invalid") from exc
    return len(encoded)
