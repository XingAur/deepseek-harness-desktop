"""Runtime-facing adapter session for provider-neutral Harness hosts.

The Harness Core owns authorization and orchestration.  A host supplies only
the handler that knows how to ask its local Agent runtime for a result.  This
module is intentionally independent of Codex, DeepSeek, credentials, and
network clients so the same session can be embedded by any host adapter.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from app.agent_backend_protocol import AgentBackendRequest, AgentBackendResult, parse_request


_MAX_JSON_LINE_BYTES = 256 * 1024


class HostAdapterSession:
    """Convert one host request into one bounded, provider-neutral result."""

    def __init__(self, handler: Callable[..., Any]) -> None:
        if not callable(handler):
            raise TypeError("host_adapter_invalid")
        self._handler = handler

    def handle(self, value: object, sink: Any = None) -> AgentBackendResult:
        try:
            request = value if isinstance(value, AgentBackendRequest) else parse_request(_decode_json_input(value))
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            return _error_result("worker_request_invalid", exit_code=2)

        try:
            result = self._handler(request, sink)
        except Exception:
            return _error_result("worker_backend_rejected", exit_code=1)

        if not isinstance(result, AgentBackendResult):
            return _error_result("worker_backend_rejected", exit_code=1)
        return result

    def handle_json_line(self, value: object, sink: Any = None) -> str:
        """Return exactly one compact JSON result for a JSONL request."""

        result = self.handle(value, sink=sink)
        return json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode_json_input(value: object) -> object:
    if isinstance(value, bytes):
        if not value or len(value) > _MAX_JSON_LINE_BYTES:
            raise ValueError("host_adapter_request_invalid")
        return value
    if isinstance(value, str):
        if not value or len(value.encode("utf-8")) > _MAX_JSON_LINE_BYTES:
            raise ValueError("host_adapter_request_invalid")
        return json.loads(value)
    if isinstance(value, Mapping):
        return value
    raise ValueError("host_adapter_request_invalid")


def _error_result(error_code: str, *, exit_code: int) -> AgentBackendResult:
    return AgentBackendResult(
        exit_code=exit_code,
        error_code=error_code,
        event_count=0,
        final_response_sha256="",
        canonical_final_response_sha256="",
        final_response_validated=False,
    )
