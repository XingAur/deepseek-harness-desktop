from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any, Protocol

from app.sensitive_text import contains_sensitive_text, is_sensitive_mapping_key


AUDIT_FIELDS = frozenset(
    {
        "request_id",
        "capability",
        "provider",
        "mutation_level",
        "status",
        "trace_id",
        "server",
        "tool",
        "duration_ms",
        "evidence_ref",
        "error_code",
        "retryable",
        "timestamp",
        "task_id",
        "run_id",
        "project_id",
        "repository_id",
        "context_pack_id",
    }
)


class McpAuditError(ValueError):
    """Evidence or audit data is not safe for a Harness MCP sink."""


class McpEvidenceSink(Protocol):
    def store(
        self,
        *,
        request_id: str,
        capability: str,
        provider: str,
        payload: Mapping[str, Any],
    ) -> str:
        """Return an opaque Harness-owned evidence reference."""


class McpAuditSink(Protocol):
    def record(self, event: Mapping[str, Any]) -> None:
        """Record metadata-only audit data."""


def prepare_mcp_evidence(
    *,
    request_id: str,
    capability: str,
    provider: str,
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], bytes, str]:
    """Return an immutable-ready snapshot, canonical bytes and Harness reference."""

    if not all(isinstance(value, str) and value for value in (request_id, capability, provider)):
        raise McpAuditError("invalid evidence identity")
    if not isinstance(payload, Mapping):
        raise McpAuditError("invalid evidence payload")
    try:
        snapshot = copy.deepcopy(dict(payload))
        encoded = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (MemoryError, TypeError, ValueError, UnicodeError) as exc:
        raise McpAuditError("invalid evidence payload") from exc
    digest = hashlib.sha256(encoded).hexdigest()
    return snapshot, encoded, f"mcp-evidence:{request_id}:{digest[:16]}"


def prepare_mcp_audit_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and copy one metadata-only MCP audit event."""

    if not isinstance(event, Mapping) or set(event) != AUDIT_FIELDS:
        raise McpAuditError("invalid MCP audit fields")
    snapshot: dict[str, Any] = {}
    for key, value in event.items():
        if is_sensitive_mapping_key(key):
            raise McpAuditError("sensitive MCP audit field")
        if key == "duration_ms":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise McpAuditError("invalid MCP audit duration")
        elif key == "retryable":
            if not isinstance(value, bool):
                raise McpAuditError("invalid MCP audit retry flag")
        elif not isinstance(value, str) or len(value) > 512 or contains_sensitive_text(value):
            raise McpAuditError("invalid MCP audit scalar")
        if isinstance(value, float) and not math.isfinite(value):
            raise McpAuditError("invalid MCP audit number")
        snapshot[key] = value
    return snapshot


class InMemoryMcpEvidenceSink:
    """Deterministic test-only evidence storage with immutable read snapshots."""

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        return tuple(copy.deepcopy(self._records))

    def store(
        self,
        *,
        request_id: str,
        capability: str,
        provider: str,
        payload: Mapping[str, Any],
    ) -> str:
        snapshot, _, reference = prepare_mcp_evidence(
            request_id=request_id,
            capability=capability,
            provider=provider,
            payload=payload,
        )
        self._records.append(
            {
                "request_id": request_id,
                "capability": capability,
                "provider": provider,
                "payload": snapshot,
                "evidence_ref": reference,
            }
        )
        return reference


class InMemoryMcpAuditSink:
    """Strict test-only metadata sink; result bodies and credentials are rejected."""

    allowed_fields = AUDIT_FIELDS

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        return tuple(copy.deepcopy(self._events))

    def record(self, event: Mapping[str, Any]) -> None:
        self._events.append(prepare_mcp_audit_event(event))
