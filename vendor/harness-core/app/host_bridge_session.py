"""Bidirectional, provider-neutral session for external Harness hosts.

The Harness side sends an execute-only Agent request to its host and accepts
only a versioned, correlated result.  The transport is injected so the same
session works with an in-process callback, a JSONL child process, or a desktop
host without coupling the Core to a model provider.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from typing import Any

from app.agent_backend_protocol import AgentBackendRequest, AgentBackendResult, request_hash
from app.sensitive_text import contains_sensitive_text


HOST_SESSION_SCHEMA_VERSION = "harness-host-session.v1"
_MAX_FRAME_BYTES = 256 * 1024
_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
_MESSAGE_TYPES = frozenset({"agent.result", "session.event"})
_SENSITIVE_KEYS = frozenset({
    "api_key", "apikey", "authorization", "password", "secret", "token",
    "provider_payload", "raw_payload",
})


class HostBridgeSession:
    """Execute one Harness Agent request through an injected host transport."""

    def __init__(
        self,
        *,
        send: Callable[[dict[str, object]], Any],
        receive: Callable[[], object],
        request_id: Callable[[AgentBackendRequest], str] = request_hash,
    ) -> None:
        if not callable(send) or not callable(receive) or not callable(request_id):
            raise TypeError("host_session_transport_invalid")
        self._send = send
        self._receive = receive
        self._request_id = request_id

    def execute(
        self,
        request: AgentBackendRequest,
        sink: Any = None,
    ) -> AgentBackendResult:
        if not isinstance(request, AgentBackendRequest):
            raise ValueError("host_session_request_invalid")

        correlation_id = self._request_id(request)
        if not isinstance(correlation_id, str) or _IDENTIFIER.fullmatch(correlation_id) is None:
            raise ValueError("host_session_request_invalid")
        _bind_local_session_identity(sink)
        self._send(_message(
            message_type="agent.request",
            request_id=correlation_id,
            payload=request.to_dict(),
        ))

        while True:
            message = _parse_message(self._receive())
            if message["request_id"] != correlation_id:
                raise ValueError("host_session_response_invalid")
            message_type = message["type"]
            payload = message["payload"]
            if message_type == "session.event":
                if sink is not None:
                    _deliver_event(sink, payload)
                continue
            return _parse_result(payload)


def _bind_local_session_identity(sink: Any) -> None:
    """Bind the sidecar process to the existing runner's identity guard."""

    callback = getattr(sink, "on_started", None)
    if not callable(callback):
        return
    try:
        from app.local_agent_repository import _read_process_start_identity

        identity = _read_process_start_identity(os.getpid())
    except Exception:
        raise ValueError("host_session_identity_unavailable") from None
    callback(os.getpid(), identity)


def _deliver_event(sink: Any, payload: dict[str, object]) -> None:
    callback = getattr(sink, "on_event", None)
    if callable(callback):
        callback(payload)
        return
    if callable(sink):
        sink(payload)


def _message(*, message_type: str, request_id: str, payload: Mapping[str, object]) -> dict[str, object]:
    message = {
        "schema_version": HOST_SESSION_SCHEMA_VERSION,
        "type": message_type,
        "request_id": request_id,
        "payload": dict(payload),
    }
    _validate_message(message, allowed_types={"agent.request", "agent.result", "session.event"})
    return message


def _parse_message(value: object) -> dict[str, object]:
    if isinstance(value, bytes):
        if not value or len(value) > _MAX_FRAME_BYTES:
            raise ValueError("host_session_response_invalid")
        try:
            value = json.loads(value.decode("utf-8", "strict"))
        except (UnicodeError, ValueError, json.JSONDecodeError):
            raise ValueError("host_session_response_invalid") from None
    elif isinstance(value, str):
        if not value or len(value.encode("utf-8")) > _MAX_FRAME_BYTES:
            raise ValueError("host_session_response_invalid")
        try:
            value = json.loads(value)
        except (ValueError, json.JSONDecodeError):
            raise ValueError("host_session_response_invalid") from None
    if not isinstance(value, Mapping):
        raise ValueError("host_session_response_invalid")
    message = dict(value)
    _validate_message(message, allowed_types=_MESSAGE_TYPES)
    return message


def _validate_message(message: Mapping[str, object], *, allowed_types: set[str] | frozenset[str]) -> None:
    if set(message) != {"schema_version", "type", "request_id", "payload"}:
        raise ValueError("host_session_response_invalid")
    if message["schema_version"] != HOST_SESSION_SCHEMA_VERSION:
        raise ValueError("host_session_response_invalid")
    message_type = message["type"]
    request_id = message["request_id"]
    payload = message["payload"]
    if (
        not isinstance(message_type, str)
        or message_type not in allowed_types
        or not isinstance(request_id, str)
        or _IDENTIFIER.fullmatch(request_id) is None
        or not isinstance(payload, Mapping)
        or _contains_sensitive_value(payload)
    ):
        raise ValueError("host_session_response_invalid")
    try:
        encoded = json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValueError("host_session_response_invalid") from None
    if len(encoded) > _MAX_FRAME_BYTES:
        raise ValueError("host_session_response_invalid")


def _parse_result(payload: Mapping[str, object]) -> AgentBackendResult:
    required = {
        "exit_code", "error_code", "event_count", "final_response_sha256",
        "canonical_final_response_sha256", "final_response_validated",
    }
    if (
        set(payload) - (required | {"schema_version", "final_response"}) != set()
        or not required <= set(payload)
        or payload.get("schema_version") != "his-agent-backend-result.v1"
    ):
        raise ValueError("host_session_response_invalid")
    try:
        return AgentBackendResult(
            exit_code=payload["exit_code"],  # type: ignore[arg-type]
            error_code=payload["error_code"],  # type: ignore[arg-type]
            event_count=payload["event_count"],  # type: ignore[arg-type]
            final_response_sha256=payload["final_response_sha256"],  # type: ignore[arg-type]
            canonical_final_response_sha256=payload["canonical_final_response_sha256"],  # type: ignore[arg-type]
            final_response_validated=payload["final_response_validated"],  # type: ignore[arg-type]
            final_response=payload.get("final_response"),  # type: ignore[arg-type]
        )
    except (TypeError, ValueError):
        raise ValueError("host_session_response_invalid") from None


def _contains_sensitive_value(value: object, *, key: str = "") -> bool:
    if key.casefold() in _SENSITIVE_KEYS:
        return True
    if isinstance(value, str):
        return contains_sensitive_text(value)
    if isinstance(value, Mapping):
        return any(_contains_sensitive_value(item, key=str(name)) for name, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_value(item) for item in value)
    return False
