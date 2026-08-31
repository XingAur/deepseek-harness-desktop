"""Shared strict JSON-RPC and result-envelope helpers for readonly MCP servers."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, TextIO


MCP_RESULT_SCHEMA_VERSION = "his-mcp-result-envelope.v1"
DEFAULT_PROTOCOL_VERSION = "2025-03-26"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|credential|password|passwd|secret|token|api.?key|dsn|connection.?string)",
    re.IGNORECASE,
)
_SENSITIVE_SCALAR = re.compile(
    r"(?:authorization\s*:|bearer\s+|password\s*=|passwd\s*=|secret\s*=|token\s*=)",
    re.IGNORECASE,
)


def utc_timestamp(value: datetime) -> str:
    current = value.astimezone(timezone.utc)
    return current.isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_metadata(metadata: object) -> tuple[str, str]:
    if not isinstance(metadata, Mapping) or set(metadata) != {"request_id", "trace_id"}:
        raise ValueError("invalid metadata")
    request_id = metadata.get("request_id")
    trace_id = metadata.get("trace_id")
    if (
        not isinstance(request_id, str)
        or _IDENTIFIER.fullmatch(request_id) is None
        or not isinstance(trace_id, str)
        or _IDENTIFIER.fullmatch(trace_id) is None
    ):
        raise ValueError("invalid metadata")
    return request_id, trace_id


def fallback_metadata(metadata: object) -> tuple[str, str]:
    if isinstance(metadata, Mapping):
        request_id = metadata.get("request_id")
        trace_id = metadata.get("trace_id")
        if isinstance(request_id, str) and _IDENTIFIER.fullmatch(request_id):
            if isinstance(trace_id, str) and _IDENTIFIER.fullmatch(trace_id):
                return request_id, trace_id
            return request_id, request_id
    return "invalid-request", "invalid-request"


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def content_version(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sanitize_json(
    value: object,
    *,
    secrets: tuple[str, ...] = (),
    depth: int = 0,
    budget: Optional[list[int]] = None,
) -> object:
    remaining = [10_000] if budget is None else budget
    remaining[0] -= 1
    if depth > 12 or remaining[0] < 0:
        raise ValueError("unsafe result")
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128 or _SENSITIVE_KEY.search(key):
                continue
            result[key] = sanitize_json(
                item,
                secrets=secrets,
                depth=depth + 1,
                budget=remaining,
            )
        return result
    if isinstance(value, (list, tuple)):
        return [
            sanitize_json(item, secrets=secrets, depth=depth + 1, budget=remaining)
            for item in value[:1_001]
        ]
    if isinstance(value, str):
        text = value[:65_536]
        for secret in secrets:
            if secret:
                text = text.replace(secret, "[REDACTED]")
        if _SENSITIVE_SCALAR.search(text):
            return "[REDACTED]"
        return text
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float) and value == value and value not in (float("inf"), float("-inf")):
        return value
    return str(value)[:1_024]


def result_envelope(
    *,
    request_id: str,
    trace_id: str,
    capability: str,
    provider: str,
    server: str,
    tool: str,
    server_version: str,
    status: str,
    data: Mapping[str, object],
    object_id: str = "",
    version: str = "",
    observed_at: str = "",
    truncated: bool = False,
    next_cursor: str = "",
    redaction_fields: tuple[str, ...] = (),
    error_code: str = "",
    retryable: bool = False,
    recovery: str = "",
) -> dict[str, object]:
    expires_at = ""
    if observed_at:
        observed = datetime.fromisoformat(observed_at.removesuffix("Z") + "+00:00")
        expires_at = utc_timestamp(observed + timedelta(minutes=5))
    evidence_ref = ""
    if status == "success" and object_id and version:
        evidence_ref = f"{provider}:{object_id}:{version}"
    return {
        "schema_version": MCP_RESULT_SCHEMA_VERSION,
        "request_id": request_id,
        "capability": capability,
        "provider": provider,
        "status": status,
        "data": dict(data),
        "evidence_ref": evidence_ref,
        "source": {
            "system": provider if object_id else "",
            "object_id": object_id,
            "version": version,
            "observed_at": observed_at,
        },
        "freshness": {
            "status": "fresh" if status == "success" else "unknown",
            "expires_at": expires_at,
        },
        "pagination": {"truncated": truncated, "next_cursor": next_cursor},
        "redaction": {
            "applied": bool(redaction_fields),
            "fields": list(redaction_fields),
        },
        "error": {
            "code": error_code,
            "retryable": retryable,
            "recovery": recovery,
        },
        "trace": {
            "mcp_server": server,
            "tool": tool,
            "server_version": server_version,
            "trace_id": trace_id,
        },
    }


def tool_result(envelope: Mapping[str, object]) -> dict[str, object]:
    error = envelope.get("error")
    pagination = envelope.get("pagination")
    summary = {
        "status": envelope.get("status", "failed"),
        "request_id": envelope.get("request_id", ""),
        "evidence_ref": envelope.get("evidence_ref", ""),
        "truncated": pagination.get("truncated", False)
        if isinstance(pagination, Mapping)
        else False,
        "error_code": error.get("code", "") if isinstance(error, Mapping) else "",
    }
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    summary,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        ],
        "structuredContent": dict(envelope),
        # The strict envelope carries business failure. Keeping the protocol
        # result successful lets the Harness validate and persist that failure.
        "isError": False,
    }


class JsonRpcReadonlyServer:
    server_name = ""
    server_version = ""
    tools: tuple[Mapping[str, object], ...] = ()

    def call_tool(self, name: str, arguments: object, metadata: object = None) -> dict[str, object]:
        raise NotImplementedError

    @staticmethod
    def _response(identifier: object, result: Mapping[str, object]) -> dict[str, object]:
        return {"jsonrpc": "2.0", "id": identifier, "result": dict(result)}

    @staticmethod
    def _error(identifier: object, code: int, message: str) -> dict[str, object]:
        return {
            "jsonrpc": "2.0",
            "id": identifier,
            "error": {"code": code, "message": message},
        }

    def handle(self, message: object) -> Optional[dict[str, object]]:
        if not isinstance(message, Mapping) or message.get("jsonrpc") != "2.0":
            return self._error(None, -32600, "Invalid Request")
        identifier = message.get("id")
        if identifier is None:
            return None
        method = message.get("method")
        params = message.get("params", {})
        if method == "initialize":
            if not isinstance(params, Mapping):
                return self._error(identifier, -32602, "Invalid params")
            protocol = params.get("protocolVersion", DEFAULT_PROTOCOL_VERSION)
            if not isinstance(protocol, str):
                return self._error(identifier, -32602, "Invalid params")
            return self._response(
                identifier,
                {
                    "protocolVersion": protocol,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": self.server_name, "version": self.server_version},
                },
            )
        if method == "ping":
            return self._response(identifier, {})
        if method == "tools/list":
            return self._response(identifier, {"tools": list(self.tools)})
        if method == "tools/call":
            names = {item.get("name") for item in self.tools}
            if (
                not isinstance(params, Mapping)
                or set(params) - {"name", "arguments", "_meta"}
                or params.get("name") not in names
            ):
                return self._error(identifier, -32602, "Invalid params")
            return self._response(
                identifier,
                self.call_tool(
                    str(params["name"]),
                    params.get("arguments", {}),
                    params.get("_meta"),
                ),
            )
        return self._error(identifier, -32601, "Method not found")

    def serve(self, source: TextIO, target: TextIO) -> None:
        for line in source:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                response = self._error(None, -32700, "Parse error")
            else:
                response = self.handle(message)
            if response is not None:
                target.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
                target.flush()
