"""Versioned, provider-neutral protocol for external Harness hosts.

The protocol is intentionally a pure in-memory/JSON boundary.  It does not
open the Harness database, load credentials, call a network service, or
assume that the host is Codex, DeepSeek, or any other particular client.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.agent_backend import AgentBackendRole
from app.sensitive_text import contains_sensitive_text


REQUEST_SCHEMA_VERSION = "his-agent-backend-request.v1"
EVENT_SCHEMA_VERSION = "his-agent-backend-event.v1"
RESULT_SCHEMA_VERSION = "his-agent-backend-result.v1"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9._-]{1,127}$")
_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE = re.compile(r"^(?:[a-z][a-z0-9._-]{0,63})?$")
_MAX_PROMPT_BYTES = 48_000
_MAX_JSON_BYTES = 256 * 1024
_MAX_EVENT_SEQUENCE = 1_000_000
_MAX_CAPABILITIES = 128
_OPAQUE_KEYS = frozenset({
    "thread_id", "turn_id", "item_id", "provider_payload", "raw_payload",
    "model", "provider", "api_key", "token", "secret",
})
_ERROR_CODES = frozenset({
    "",
    "worker_backend_unavailable",
    "worker_backend_rejected",
    "worker_request_invalid",
    "worker_process_failed",
    "worker_protocol_invalid",
    "worker_timeout",
})


def _invalid(code: str) -> None:
    raise ValueError(code)


def _bounded_identifier(value: object, *, code: str = "agent_backend_request_invalid") -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        _invalid(code)
    return value


def _safe_json_object(value: object, *, code: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _invalid(code)
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _invalid(code)
    if not encoded or len(encoded) > _MAX_JSON_BYTES or _unsafe_json_value(value):
        _invalid(code)
    return dict(value)


def _unsafe_json_value(value: object, *, key: str = "") -> bool:
    if key.casefold() in _OPAQUE_KEYS:
        return True
    if isinstance(value, str):
        return contains_sensitive_text(value)
    if isinstance(value, Mapping):
        return any(_unsafe_json_value(item, key=str(name)) for name, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_unsafe_json_value(item) for item in value)
    return False


@dataclass(frozen=True)
class AgentBackendRequest:
    role: AgentBackendRole
    worktree_path: Path
    prompt: str
    timeout_seconds: int
    output_contract: dict[str, object]
    capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.role, AgentBackendRole)
            or not isinstance(self.worktree_path, Path)
            or not self.worktree_path.is_absolute()
            or self.worktree_path.is_symlink()
            or not isinstance(self.prompt, str)
            or not self.prompt
            or "\x00" in self.prompt
            or len(self.prompt.encode("utf-8")) > _MAX_PROMPT_BYTES
            or contains_sensitive_text(self.prompt)
            or not isinstance(self.timeout_seconds, int)
            or isinstance(self.timeout_seconds, bool)
            or not 1 <= self.timeout_seconds <= 3600
            or not isinstance(self.output_contract, dict)
            or set(self.output_contract) != {"name", "schema_version"}
            or any(not isinstance(value, str) or not value for value in self.output_contract.values())
            or not isinstance(self.capabilities, tuple)
            or len(self.capabilities) > _MAX_CAPABILITIES
            or any(_IDENTIFIER.fullmatch(item) is None for item in self.capabilities)
            or len(set(self.capabilities)) != len(self.capabilities)
        ):
            _invalid("agent_backend_request_invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": REQUEST_SCHEMA_VERSION,
            "role": self.role.value,
            "worktree_path": str(self.worktree_path),
            "prompt": self.prompt,
            "timeout_seconds": self.timeout_seconds,
            "output_contract": dict(self.output_contract),
            "capabilities": list(self.capabilities),
        }


def parse_request(value: object) -> AgentBackendRequest:
    if isinstance(value, bytes):
        if not value or len(value) > _MAX_JSON_BYTES:
            _invalid("agent_backend_request_invalid")
        try:
            value = json.loads(value.decode("utf-8", "strict"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (UnicodeError, ValueError, json.JSONDecodeError):
            _invalid("agent_backend_request_invalid")
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version", "role", "worktree_path", "prompt", "timeout_seconds",
        "output_contract", "capabilities",
    }:
        _invalid("agent_backend_request_invalid")
    if value.get("schema_version") != REQUEST_SCHEMA_VERSION:
        _invalid("agent_backend_request_invalid")
    role = value.get("role")
    try:
        role = AgentBackendRole(str(role))
        worktree_path = Path(str(value.get("worktree_path")))
    except (TypeError, ValueError, OSError):
        _invalid("agent_backend_request_invalid")
    output_contract = _safe_json_object(value.get("output_contract"), code="agent_backend_request_invalid")
    capabilities = value.get("capabilities")
    if (
        not isinstance(capabilities, list)
        or any(not isinstance(item, str) for item in capabilities)
        or len(capabilities) > _MAX_CAPABILITIES
        or len(set(capabilities)) != len(capabilities)
        or any(_IDENTIFIER.fullmatch(item) is None for item in capabilities)
    ):
        _invalid("agent_backend_request_invalid")
    return AgentBackendRequest(
        role=role,
        worktree_path=worktree_path,
        prompt=str(value.get("prompt")),
        timeout_seconds=value.get("timeout_seconds"),  # type: ignore[arg-type]
        output_contract=output_contract,
        capabilities=tuple(capabilities),
    )


@dataclass(frozen=True)
class AgentBackendEvent:
    type: str
    sequence_no: int
    item_type: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.type, str)
            or _EVENT_TYPE.fullmatch(self.type) is None
            or not isinstance(self.sequence_no, int)
            or isinstance(self.sequence_no, bool)
            or not 1 <= self.sequence_no <= _MAX_EVENT_SEQUENCE
            or self.item_type is not None
            and (not isinstance(self.item_type, str) or _EVENT_TYPE.fullmatch(self.item_type) is None)
        ):
            _invalid("agent_backend_event_invalid")

    def to_dict(self) -> dict[str, object]:
        result = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "type": self.type,
            "sequence_no": self.sequence_no,
        }
        if self.item_type is not None:
            result["item_type"] = self.item_type
        return result


@dataclass(frozen=True)
class AgentBackendResult:
    exit_code: int | None
    error_code: str
    event_count: int
    final_response_sha256: str
    canonical_final_response_sha256: str
    final_response_validated: bool
    final_response: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if (
            self.exit_code is not None
            and (
                not isinstance(self.exit_code, int)
                or isinstance(self.exit_code, bool)
                or not -255 <= self.exit_code <= 255
            )
            or not isinstance(self.error_code, str)
            or _ERROR_CODE.fullmatch(self.error_code) is None
            or self.error_code not in _ERROR_CODES
            or not isinstance(self.event_count, int)
            or isinstance(self.event_count, bool)
            or not 0 <= self.event_count <= _MAX_EVENT_SEQUENCE
            or not isinstance(self.final_response_sha256, str)
            or (self.final_response_sha256 and _SHA256.fullmatch(self.final_response_sha256) is None)
            or not isinstance(self.canonical_final_response_sha256, str)
            or (
                self.canonical_final_response_sha256
                and _SHA256.fullmatch(self.canonical_final_response_sha256) is None
            )
            or not isinstance(self.final_response_validated, bool)
        ):
            _invalid("agent_backend_result_invalid")
        if self.final_response is not None:
            _safe_json_object(self.final_response, code="agent_backend_result_invalid")
        if self.final_response_validated and self.final_response is None:
            _invalid("agent_backend_result_invalid")

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "exit_code": self.exit_code,
            "error_code": self.error_code,
            "event_count": self.event_count,
            "final_response_sha256": self.final_response_sha256,
            "canonical_final_response_sha256": self.canonical_final_response_sha256,
            "final_response_validated": self.final_response_validated,
        }
        if self.final_response is not None:
            result["final_response"] = dict(self.final_response)
        return result


def request_hash(request: AgentBackendRequest) -> str:
    return hashlib.sha256(
        json.dumps(request.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
