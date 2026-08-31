#!/usr/bin/env python3
"""Dependency-free, read-only Yunxiao MCP server."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, TextIO


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from yunxiao_evidence import (  # noqa: E402
    YunxiaoClient,
    collect_evidence,
    load_credentials,
    redact_for_output,
)


SERVER_NAME = "yunxiao"
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
MCP_RESULT_SCHEMA_VERSION = "his-mcp-result-envelope.v1"
MAX_ENVELOPE_BYTES = 262_144
MAX_DATA_DEPTH = 24
MAX_DATA_NODES = 40_000
ARGUMENT_FIELDS = frozenset(
    {
        "work_item_id",
        "include_comments",
        "include_attachments",
        "page_cursor",
        "page_size",
    }
)
METADATA_FIELDS = frozenset({"request_id", "trace_id"})
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
WORK_ITEM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CURSOR = re.compile(r"^v1:(0|[1-9][0-9]*)$")
MOBILE_NUMBER = re.compile(r"(?<!\d)(?:\+86)?1[3-9](?:[\s.-]?\d){9}(?!\d)")
IDENTITY_CARD = re.compile(
    r"(?<!\d)\d{6}[\s-]?\d{8}[\s-]?\d{3}[\s-]?[\dXx](?![\dXx])"
)
SENSITIVE_KEY = re.compile(
    r"^(?:authorization|access(?:token)?|refreshtoken|token|pat|apikey|secret|"
    r"password|passwd|credential|privatekey|aliyundevopspat|[a-z][a-z0-9]*pat)$",
    re.IGNORECASE,
)


class UnsafeResult(ValueError):
    """Provider data cannot enter the governed MCP envelope."""


def _tool_annotations() -> dict[str, bool]:
    return {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }


TOOLS = (
    {
        "name": "workitem_get",
        "description": "Read one Yunxiao work item and bounded related evidence. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "work_item_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
                },
                "include_comments": {"type": "boolean"},
                "include_attachments": {"type": "boolean"},
                "page_cursor": {
                    "type": "string",
                    "maxLength": 32,
                    "pattern": "^$|^v1:(0|[1-9][0-9]*)$",
                },
                "page_size": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": [
                "work_item_id",
                "include_comments",
                "include_attachments",
                "page_cursor",
                "page_size",
            ],
            "additionalProperties": False,
        },
        "annotations": _tool_annotations(),
    },
)


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise UnsafeResult("result is not canonical JSON") from exc


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _safe_identity(value: object) -> str:
    if isinstance(value, str) and value == value.strip() and IDENTIFIER.fullmatch(value):
        return value
    return uuid.uuid4().hex


def _validate_metadata(value: object) -> tuple[str, str]:
    if value is None:
        values: dict[str, object] = {}
    elif isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
        values = dict(value)
    else:
        raise ValueError("invalid metadata")
    if not set(values).issubset(METADATA_FIELDS):
        raise ValueError("invalid metadata")
    request_id = values.get("request_id")
    trace_id = values.get("trace_id")
    if request_id is not None and _safe_identity(request_id) != request_id:
        raise ValueError("invalid metadata")
    if trace_id is not None and _safe_identity(trace_id) != trace_id:
        raise ValueError("invalid metadata")
    return (
        str(request_id) if request_id is not None else uuid.uuid4().hex,
        str(trace_id) if trace_id is not None else uuid.uuid4().hex,
    )


def _fallback_identities(value: object) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        return uuid.uuid4().hex, uuid.uuid4().hex
    return _safe_identity(value.get("request_id")), _safe_identity(value.get("trace_id"))


def _validate_arguments(value: object) -> tuple[dict[str, object], int]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError("invalid arguments")
    arguments = dict(value)
    if set(arguments) != ARGUMENT_FIELDS:
        raise ValueError("invalid arguments")
    work_item_id = arguments["work_item_id"]
    if (
        not isinstance(work_item_id, str)
        or work_item_id != work_item_id.strip()
        or WORK_ITEM_ID.fullmatch(work_item_id) is None
    ):
        raise ValueError("invalid work item id")
    for field in ("include_comments", "include_attachments"):
        if not isinstance(arguments[field], bool):
            raise ValueError("invalid boolean argument")
    cursor = arguments["page_cursor"]
    if not isinstance(cursor, str) or len(cursor) > 32:
        raise ValueError("invalid cursor")
    if cursor:
        match = CURSOR.fullmatch(cursor)
        if match is None:
            raise ValueError("invalid cursor")
        offset = int(match.group(1))
    else:
        offset = 0
    page_size = arguments["page_size"]
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 50:
        raise ValueError("invalid page size")
    return arguments, offset


def _sanitize_data(
    value: object,
    *,
    secrets: list[str],
    path: str,
    redacted: set[str],
    depth: int,
    budget: list[int],
) -> object:
    if depth > MAX_DATA_DEPTH or budget[0] <= 0:
        raise UnsafeResult("result exceeds structural limits")
    budget[0] -= 1
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise UnsafeResult("result contains an invalid key")
            compact = re.sub(r"[^a-z0-9]", "", key.lower())
            if SENSITIVE_KEY.search(compact):
                raise UnsafeResult("result contains a sensitive key")
            child_path = f"{path}.{key}" if path else key
            if key == "local_path":
                redacted.add(child_path)
                continue
            result[key] = _sanitize_data(
                item,
                secrets=secrets,
                path=child_path,
                redacted=redacted,
                depth=depth + 1,
                budget=budget,
            )
        return result
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_data(
                item,
                secrets=secrets,
                path=f"{path}[{index}]",
                redacted=redacted,
                depth=depth + 1,
                budget=budget,
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, str):
        safe = redact_for_output(value, secrets)
        safe = IDENTITY_CARD.sub("[REDACTED_IDENTITY_CARD]", safe)
        safe = MOBILE_NUMBER.sub("[REDACTED_MOBILE]", safe)
        if safe != value:
            redacted.add(path or "data")
        return safe
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float) and value == value and value not in (float("inf"), float("-inf")):
        return value
    raise UnsafeResult("result contains a non-JSON value")


def _paginate_evidence(
    evidence: Mapping[str, Any], offset: int, page_size: int
) -> tuple[dict[str, Any], bool]:
    result = copy.deepcopy(dict(evidence))
    work_items = result.get("work_items")
    if not isinstance(work_items, list):
        raise UnsafeResult("work_items must be a list")
    truncated = False
    for item in work_items:
        if not isinstance(item, dict):
            raise UnsafeResult("work item must be an object")
        for field in ("comments", "attachments", "inline_files"):
            values = item.get(field, [])
            if not isinstance(values, list):
                raise UnsafeResult("nested evidence must be a list")
            end = offset + page_size
            if len(values) > end:
                truncated = True
            item[field] = values[offset:end]
    return result, truncated


def _rehash_evidence(evidence: dict[str, Any]) -> str:
    integrity = evidence.get("integrity")
    if not isinstance(integrity, dict):
        integrity = {}
        evidence["integrity"] = integrity
    integrity["algorithm"] = "sha256"
    integrity["evidence_sha256"] = ""
    payload = copy.deepcopy(evidence)
    payload.pop("integrity", None)
    digest = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
    integrity["evidence_sha256"] = digest
    return digest


def _primary_object_id(evidence: Mapping[str, Any]) -> str:
    source = evidence.get("source")
    resolved = source.get("resolved_work_item_id") if isinstance(source, Mapping) else ""
    if not isinstance(resolved, str) or IDENTIFIER.fullmatch(resolved) is None:
        return ""
    work_items = evidence.get("work_items")
    if not isinstance(work_items, list):
        return ""
    if not any(isinstance(item, Mapping) and item.get("id") == resolved for item in work_items):
        return ""
    return resolved


def _envelope(
    *,
    request_id: str,
    trace_id: str,
    status: str,
    data: Mapping[str, Any],
    evidence_ref: str,
    object_id: str,
    version: str,
    observed_at: str,
    expires_at: str,
    truncated: bool,
    next_cursor: str,
    redaction_fields: list[str],
    error_code: str,
    retryable: bool,
    recovery: str,
) -> dict[str, Any]:
    return {
        "schema_version": MCP_RESULT_SCHEMA_VERSION,
        "request_id": request_id,
        "capability": "workitem.read",
        "provider": "yunxiao",
        "status": status,
        "data": dict(data),
        "evidence_ref": evidence_ref,
        "source": {
            "system": "yunxiao" if object_id else "",
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
            "fields": sorted(set(redaction_fields)),
        },
        "error": {"code": error_code, "retryable": retryable, "recovery": recovery},
        "trace": {
            "mcp_server": SERVER_NAME,
            "tool": "workitem_get",
            "server_version": SERVER_VERSION,
            "trace_id": trace_id,
        },
    }


def _failure_envelope(
    request_id: str,
    trace_id: str,
    *,
    status: str,
    code: str,
    recovery: str,
    retryable: bool = False,
) -> dict[str, Any]:
    return _envelope(
        request_id=request_id,
        trace_id=trace_id,
        status=status,
        data={},
        evidence_ref="",
        object_id="",
        version="",
        observed_at="",
        expires_at="",
        truncated=False,
        next_cursor="",
        redaction_fields=[],
        error_code=code,
        retryable=retryable,
        recovery=recovery,
    )


def _tool_result(envelope: Mapping[str, Any]) -> dict[str, object]:
    status = str(envelope.get("status") or "failed")
    data = envelope.get("data")
    decision_gate = ""
    if isinstance(data, Mapping):
        gate = data.get("decision_gate")
        if isinstance(gate, Mapping) and isinstance(gate.get("state"), str):
            decision_gate = str(gate["state"])
    pagination = envelope.get("pagination")
    error = envelope.get("error")
    summary = {
        "status": status,
        "request_id": envelope.get("request_id", ""),
        "evidence_ref": envelope.get("evidence_ref", ""),
        "decision_gate": decision_gate,
        "truncated": pagination.get("truncated", False) if isinstance(pagination, Mapping) else False,
        "error_code": error.get("code", "") if isinstance(error, Mapping) else "",
    }
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            }
        ],
        "structuredContent": dict(envelope),
        "isError": status != "success",
    }


class YunxiaoMcpServer:
    """Small MCP stdio server whose complete external surface is read-only."""

    def __init__(
        self,
        *,
        credential_loader: Optional[Callable[..., Mapping[str, Any]]] = None,
        client_factory: Optional[Callable[[Mapping[str, Any]], Any]] = None,
        collector: Optional[Callable[..., Mapping[str, Any]]] = None,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.credential_loader = credential_loader or load_credentials
        self.client_factory = client_factory or (
            lambda credentials: YunxiaoClient(
                token=str(credentials["token"]),
                organization_id=str(credentials["organization_id"]),
            )
        )
        self.collector = collector or collect_evidence
        self.now = now or (lambda: datetime.now(timezone.utc))

    def call_tool(
        self,
        name: str,
        arguments: object,
        metadata: object = None,
    ) -> dict[str, object]:
        if name != "workitem_get":
            raise ValueError("unknown tool")
        request_id, trace_id = _fallback_identities(metadata)
        try:
            request_id, trace_id = _validate_metadata(metadata)
            checked, offset = _validate_arguments(arguments)
        except (TypeError, ValueError):
            return _tool_result(
                _failure_envelope(
                    request_id,
                    trace_id,
                    status="invalid",
                    code="INVALID_TOOL_ARGUMENTS",
                    recovery="Provide the exact documented read-only arguments.",
                )
            )

        try:
            credentials = self.credential_loader(credential_kind="read")
            if not isinstance(credentials, Mapping):
                raise ValueError("credentials unavailable")
            token = credentials.get("token")
            organization_id = credentials.get("organization_id")
            if not isinstance(token, str) or not token or not isinstance(organization_id, str) or not organization_id:
                raise ValueError("credentials unavailable")
            client = self.client_factory(credentials)
        except Exception:
            return _tool_result(
                _failure_envelope(
                    request_id,
                    trace_id,
                    status="unavailable",
                    code="YUNXIAO_CREDENTIAL_UNAVAILABLE",
                    recovery="Configure the governed Yunxiao read credential and retry.",
                )
            )

        observed = self.now()
        try:
            evidence = self.collector(
                source=str(checked["work_item_id"]),
                client=client,
                include_comments=bool(checked["include_comments"]),
                include_attachments=bool(checked["include_attachments"]),
                output_dir=None,
                download_files=False,
                fetched_at=_utc_timestamp(observed),
                secrets=[token, organization_id],
            )
        except Exception:
            return _tool_result(
                _failure_envelope(
                    request_id,
                    trace_id,
                    status="failed",
                    code="YUNXIAO_READ_FAILED",
                    recovery="Retry after the read-only Yunxiao provider is available.",
                    retryable=True,
                )
            )
        if not isinstance(evidence, Mapping) or not _primary_object_id(evidence):
            return _tool_result(
                _failure_envelope(
                    request_id,
                    trace_id,
                    status="failed",
                    code="YUNXIAO_READ_FAILED",
                    recovery="Verify the work-item ID and read access, then retry.",
                )
            )

        try:
            paged, truncated = _paginate_evidence(evidence, offset, int(checked["page_size"]))
            redacted_paths: set[str] = set()
            safe_data = _sanitize_data(
                paged,
                secrets=[token, organization_id],
                path="data",
                redacted=redacted_paths,
                depth=0,
                budget=[MAX_DATA_NODES],
            )
            if not isinstance(safe_data, dict):
                raise UnsafeResult("evidence must be an object")
            version = _rehash_evidence(safe_data)
        except UnsafeResult:
            return _tool_result(
                _failure_envelope(
                    request_id,
                    trace_id,
                    status="failed",
                    code="MCP_RESULT_UNSAFE",
                    recovery="Narrow the requested evidence or remove unsafe provider fields.",
                )
            )

        object_id = _primary_object_id(safe_data)
        next_cursor = f"v1:{offset + int(checked['page_size'])}" if truncated else ""
        observed_at = _utc_timestamp(observed)
        expires_at = _utc_timestamp(observed + timedelta(minutes=5))
        envelope = _envelope(
            request_id=request_id,
            trace_id=trace_id,
            status="success",
            data=safe_data,
            evidence_ref=f"yunxiao:{object_id}:{version}",
            object_id=object_id,
            version=version,
            observed_at=observed_at,
            expires_at=expires_at,
            truncated=truncated,
            next_cursor=next_cursor,
            redaction_fields=sorted(redacted_paths),
            error_code="",
            retryable=False,
            recovery="",
        )
        try:
            size = len(_canonical_json_bytes(envelope))
        except UnsafeResult:
            size = MAX_ENVELOPE_BYTES + 1
        if size > MAX_ENVELOPE_BYTES:
            envelope = _failure_envelope(
                request_id,
                trace_id,
                status="failed",
                code="MCP_RESULT_TOO_LARGE",
                recovery="Use a smaller page or omit optional evidence.",
            )
        return _tool_result(envelope)

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
        if not isinstance(method, str):
            return self._error(identifier, -32600, "Invalid Request")
        params = message.get("params", {})
        if method == "initialize":
            if not isinstance(params, Mapping):
                return self._error(identifier, -32602, "Invalid params")
            protocol_version = params.get("protocolVersion", DEFAULT_PROTOCOL_VERSION)
            if not isinstance(protocol_version, str):
                return self._error(identifier, -32602, "Invalid params")
            return self._response(
                identifier,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            )
        if method == "ping":
            return self._response(identifier, {})
        if method == "tools/list":
            return self._response(identifier, {"tools": list(TOOLS)})
        if method == "tools/call":
            if (
                not isinstance(params, Mapping)
                or set(params) - {"name", "arguments", "_meta"}
                or not isinstance(params.get("name"), str)
                or params.get("name") != "workitem_get"
            ):
                return self._error(identifier, -32602, "Invalid params")
            result = self.call_tool(
                "workitem_get",
                params.get("arguments", {}),
                params.get("_meta"),
            )
            return self._response(identifier, result)
        return self._error(identifier, -32601, "Method not found")

    def serve(self, source: TextIO, target: TextIO) -> None:
        for raw_line in source:
            if not raw_line.strip():
                continue
            try:
                message = json.loads(raw_line)
            except json.JSONDecodeError:
                response = self._error(None, -32700, "Parse error")
            else:
                response = self.handle(message)
            if response is not None:
                target.write(
                    json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                target.flush()


def main() -> int:
    YunxiaoMcpServer().serve(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
