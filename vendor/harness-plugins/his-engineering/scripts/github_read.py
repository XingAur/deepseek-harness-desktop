#!/usr/bin/env python3
"""Offline GitHub-read capability protocol with an injected GET transport.

The executable never discovers a token or opens a network connection itself.
The Manager-side adapter owns fixed-host credentialed GitHub reads; this
capability package validates and redacts the portable preview contract.
"""
from __future__ import annotations

import json
import math
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


MAX_REQUEST_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 262_144
MAX_RESPONSE_DEPTH = 32
MAX_RESPONSE_NODES = 2_048
MAX_RESPONSE_STRING_BYTES = 65_536
_REQUEST_FIELDS = frozenset((
    "schema_version", "request_id", "capability", "provider", "mode",
    "mutation_level", "authorization", "input", "context",
))
_SENSITIVE_FIELD_PARTS = ("authorization", "credential", "header", "password", "secret", "token")
_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
_CREDENTIAL_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}\Z")


class GitHubTransport(Protocol):
    def get_repository(self, owner: str, repository: str) -> dict[str, Any]: ...

    def get_issue(self, owner: str, repository: str, number: int) -> dict[str, Any]: ...

    def get_pull_request(self, owner: str, repository: str, number: int) -> dict[str, Any]: ...


class GitHubReadConfiguration:
    """Metadata only.  This class does not hold or load a credential value."""

    def __init__(self, *, credential_key_name: str) -> None:
        self.credential_key_name = credential_key_name


def _result(
    request_id: str,
    *,
    status: str,
    summary: str,
    data: Mapping[str, Any] | None = None,
    blockers: Sequence[str] = (),
    operation: str | None = None,
) -> dict[str, Any]:
    audit: dict[str, Any] = {
        "credential_class": "github_read",
        "external_write_attempted": False,
    }
    if operation is not None:
        audit["operation"] = operation
    return {
        "schema_version": "his-capability-result.v1",
        "request_id": request_id,
        "capability": "github.read",
        "provider": "his-engineering",
        "status": status,
        "mutation_level": "L1",
        "changed": False,
        "summary": summary,
        "data": dict(data or {}),
        "evidence": ([{"kind": f"github_{operation}", "read_only": True}] if operation else []),
        "warnings": [],
        "blockers": list(blockers),
        "audit": audit,
    }


def _request_id(payload: object) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("request_id"), str):
        return payload["request_id"][:128]
    return ""


def _valid_owner(value: object) -> str | None:
    return value if isinstance(value, str) and value == value.strip() and _OWNER.fullmatch(value) else None


def _valid_repository(value: object) -> str | None:
    if not isinstance(value, str) or value != value.strip() or _REPOSITORY.fullmatch(value) is None:
        return None
    return None if value.startswith((".", "-")) or ".." in value else value


def _valid_number(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 2_147_483_647 else None


def _validate_request(payload: object) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(payload, dict) or set(payload) != _REQUEST_FIELDS:
        return None, "invalid_capability_request"
    if (
        payload.get("schema_version") != "his-capability-request.v1"
        or payload.get("capability") != "github.read"
        or payload.get("provider") != "his-engineering"
        or payload.get("mode") != "preview"
        or payload.get("mutation_level") != "L1"
        or payload.get("authorization") != {"explicit": False, "scope": []}
        or payload.get("context") != {}
        or not isinstance(payload.get("request_id"), str)
        or not payload["request_id"].strip()
    ):
        return None, "invalid_capability_request"
    input_data = payload.get("input")
    if not isinstance(input_data, dict) or not isinstance(input_data.get("operation"), str):
        return None, "invalid_capability_request"
    operation = input_data["operation"]
    required = {
        "repository": {"operation", "owner", "repository"},
        "issue": {"operation", "owner", "repository", "number"},
        "pull_request": {"operation", "owner", "repository", "number"},
    }
    if operation not in required or set(input_data) != required[operation]:
        return None, "invalid_capability_request"
    if _valid_owner(input_data.get("owner")) is None or _valid_repository(input_data.get("repository")) is None:
        return None, "invalid_capability_request"
    if operation != "repository" and _valid_number(input_data.get("number")) is None:
        return None, "invalid_capability_request"
    return payload, ""


def _valid_configuration(config: object) -> bool:
    return type(config) is GitHubReadConfiguration and isinstance(config.credential_key_name, str) and bool(_CREDENTIAL_KEY.fullmatch(config.credential_key_name))


def _is_sensitive_key(value: str) -> bool:
    return any(part in value.lower() for part in _SENSITIVE_FIELD_PARTS)


def _redact_text(value: str, sensitive_values: Sequence[str]) -> str:
    result = value
    for sensitive in sensitive_values:
        if isinstance(sensitive, str) and sensitive:
            result = result.replace(sensitive, "[REDACTED]")
    return result


def _sanitize_response(
    value: object,
    sensitive_values: Sequence[str],
    *,
    depth: int = 0,
    state: list[int] | None = None,
    active: set[int] | None = None,
) -> object:
    if depth > MAX_RESPONSE_DEPTH:
        raise ValueError("github_response_invalid")
    if state is None:
        state = [0, 0]
    if active is None:
        active = set()
    state[0] += 1
    if state[0] > MAX_RESPONSE_NODES:
        raise ValueError("github_response_invalid")
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("github_response_invalid")
        return value
    if type(value) is str:
        size = len(value.encode("utf-8"))
        if size > MAX_RESPONSE_STRING_BYTES or state[1] + size > MAX_RESPONSE_BYTES:
            raise ValueError("github_response_invalid")
        state[1] += size
        return _redact_text(value, sensitive_values)
    if type(value) is dict:
        marker = id(value)
        if marker in active:
            raise ValueError("github_response_invalid")
        active.add(marker)
        try:
            safe: dict[str, object] = {}
            for key, item in value.items():
                if type(key) is not str or _is_sensitive_key(key):
                    continue
                safe_key = _redact_text(key, sensitive_values)
                if safe_key != key:
                    continue
                safe[safe_key] = _sanitize_response(item, sensitive_values, depth=depth + 1, state=state, active=active)
            return safe
        finally:
            active.remove(marker)
    if type(value) is list:
        marker = id(value)
        if marker in active:
            raise ValueError("github_response_invalid")
        active.add(marker)
        try:
            return [_sanitize_response(item, sensitive_values, depth=depth + 1, state=state, active=active) for item in value]
        finally:
            active.remove(marker)
    raise ValueError("github_response_invalid")


def _encode(payload: Mapping[str, Any]) -> bytes:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise ValueError("github_response_invalid")
    return encoded


def execute_request(
    payload: object,
    *,
    config: GitHubReadConfiguration | None = None,
    transport: GitHubTransport | None = None,
    sensitive_values: Sequence[str] = (),
) -> dict[str, Any]:
    """Execute one injected GET-style read; no implicit credential or network path."""
    request_id = _request_id(payload)
    request, error = _validate_request(payload)
    if request is None:
        return _result(request_id, status="blocked", summary="GITHUB_READ_BLOCKED", blockers=[error])
    if not _valid_configuration(config) or transport is None:
        return _result(
            request["request_id"],
            status="blocked",
            summary="GITHUB_READ_UNSUPPORTED",
            blockers=["github_read_configuration_or_transport_unavailable"],
        )
    data = request["input"]
    operation = data["operation"]
    owner = data["owner"]
    repository = data["repository"]
    try:
        if operation == "repository":
            record = transport.get_repository(owner, repository)
        elif operation == "issue":
            record = transport.get_issue(owner, repository, data["number"])
        else:
            record = transport.get_pull_request(owner, repository, data["number"])
        if type(record) is not dict:
            raise ValueError("github_response_invalid")
        safe_record = _sanitize_response(record, sensitive_values)
        result = _result(
            request["request_id"],
            status="success",
            summary="GITHUB_READ_OK",
            data={"operation": operation, "record": safe_record},
            operation=operation,
        )
        _encode(result)
        return result
    except Exception:
        return _result(
            request["request_id"],
            status="failed",
            summary="GITHUB_READ_FAILED",
            blockers=["github_read_failed"],
            operation=operation,
        )


def _safe_input(path: Path) -> bytes:
    if not path.is_absolute():
        raise ValueError("unsafe_path")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_REQUEST_BYTES:
            raise ValueError("unsafe_path")
        raw = os.read(descriptor, MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("unsafe_path")
        return raw
    finally:
        os.close(descriptor)


def _safe_output(path: Path, encoded: bytes) -> None:
    if not path.is_absolute() or len(encoded) > MAX_RESPONSE_BYTES:
        raise ValueError("unsafe_path")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("result_write_failed")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 4 or args[0] != "--request" or args[2] != "--output":
        return 2
    try:
        payload = json.loads(_safe_input(Path(args[1])).decode("utf-8"))
        _safe_output(Path(args[3]), _encode(execute_request(payload)))
        return 0
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, RecursionError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
