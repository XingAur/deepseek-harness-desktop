#!/usr/bin/env python3
"""Offline-only GitLab read capability with an injected GET-style transport."""
from __future__ import annotations

import json
import math
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlparse


MAX_REQUEST_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_PROJECT_REF_LENGTH = 512
MAX_RESPONSE_DEPTH = 32
MAX_RESPONSE_NODES = 2048
MAX_RESPONSE_STRING_BYTES = 64 * 1024
MAX_RESULT_BYTES = 256 * 1024
_REQUEST_FIELDS = frozenset((
    "schema_version", "request_id", "capability", "provider", "mode",
    "mutation_level", "authorization", "input", "context",
))
_SENSITIVE_FIELD_PARTS = ("authorization", "credential", "header", "password", "secret", "token")


class GitLabTransport(Protocol):
    def get_project(self, project_ref: str) -> dict[str, Any]: ...

    def get_merge_request(self, project_ref: str, iid: int) -> dict[str, Any]: ...

    def list_pipeline_jobs(self, project_ref: str, pipeline_id: int) -> list[dict[str, Any]]: ...


class GitLabReadConfiguration:
    """Configuration metadata only; this capability never loads a credential value."""

    def __init__(self, *, base_url: str, credential_key_name: str) -> None:
        self.base_url = base_url
        self.credential_key_name = credential_key_name


def _result(
    request_id: str,
    *,
    status: str,
    summary: str,
    data: Mapping[str, Any] | None = None,
    evidence: Sequence[Mapping[str, Any]] = (),
    blockers: Sequence[str] = (),
    operation: str | None = None,
) -> dict[str, Any]:
    audit: dict[str, Any] = {
        "credential_class": "gitlab_read",
        "external_write_attempted": False,
    }
    if operation is not None:
        audit["operation"] = operation
    return {
        "schema_version": "his-capability-result.v1",
        "request_id": request_id,
        "capability": "gitlab.read",
        "provider": "his-engineering",
        "status": status,
        "mutation_level": "L1",
        "changed": False,
        "summary": summary,
        "data": dict(data or {}),
        "evidence": [dict(item) for item in evidence],
        "warnings": [],
        "blockers": list(blockers),
        "audit": audit,
    }


def _request_id(payload: object) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("request_id"), str):
        return payload["request_id"]
    return ""


def _json_depth(value: object, depth: int = 0) -> bool:
    if depth > MAX_JSON_DEPTH:
        return False
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_depth(item, depth + 1) for key, item in value.items())
    if isinstance(value, list):
        return all(_json_depth(item, depth + 1) for item in value)
    return True


def _validated_configuration_values(config: object) -> tuple[str, str] | None:
    """Return one safe metadata snapshot; never inspect subclasses or proxies."""
    if type(config) is not GitLabReadConfiguration:
        return None
    try:
        base_url = config.base_url
        credential_key_name = config.credential_key_name
        if not isinstance(base_url, str) or not isinstance(credential_key_name, str):
            return None
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", credential_key_name):
            return None
        parsed = urlparse(base_url)
        if not (
            parsed.scheme == "https"
            and bool(parsed.netloc)
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
        ):
            return None
        return base_url, credential_key_name
    except Exception:
        return None


def _validate_request(payload: object) -> tuple[dict[str, Any] | None, str]:
    if not _json_depth(payload) or not isinstance(payload, dict) or set(payload) != _REQUEST_FIELDS:
        return None, "invalid_capability_request"
    if (
        payload.get("schema_version") != "his-capability-request.v1"
        or payload.get("capability") != "gitlab.read"
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
    required_fields = {
        "project": {"operation", "project_ref"},
        "merge_request": {"operation", "project_ref", "iid"},
        "pipeline_jobs": {"operation", "project_ref", "pipeline_id"},
    }
    if operation not in required_fields or set(input_data) != required_fields[operation]:
        return None, "invalid_capability_request"
    project_ref = input_data.get("project_ref")
    if (
        not isinstance(project_ref, str)
        or not project_ref.strip()
        or project_ref != project_ref.strip()
        or len(project_ref) > MAX_PROJECT_REF_LENGTH
    ):
        return None, "invalid_capability_request"
    numeric_name = "iid" if operation == "merge_request" else "pipeline_id"
    if operation != "project":
        numeric = input_data.get(numeric_name)
        if not isinstance(numeric, int) or isinstance(numeric, bool) or numeric <= 0:
            return None, "invalid_capability_request"
    return payload, ""


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_FIELD_PARTS)


class _ResponseValidationError(ValueError):
    pass


def _redact_text(value: str, protected_values: Sequence[str]) -> str:
    sanitized = value
    for protected in protected_values:
        if protected:
            sanitized = sanitized.replace(protected, "[REDACTED]")
    return sanitized


def _account_response_text(value: str, state: list[int]) -> None:
    byte_length = len(value.encode("utf-8"))
    if byte_length > MAX_RESPONSE_STRING_BYTES:
        raise _ResponseValidationError("response string too large")
    state[1] += byte_length
    if state[1] > MAX_RESULT_BYTES:
        raise _ResponseValidationError("response bytes exceeded")


def _sanitize_response(
    value: object,
    protected_values: Sequence[str],
    *,
    depth: int = 0,
    state: list[int] | None = None,
    active_ids: set[int] | None = None,
) -> object:
    """Copy only bounded JSON-safe response data; reject hostile structures."""
    if depth > MAX_RESPONSE_DEPTH:
        raise _ResponseValidationError("response depth exceeded")
    if state is None:
        state = [0, 0]
    if active_ids is None:
        active_ids = set()
    state[0] += 1
    if state[0] > MAX_RESPONSE_NODES:
        raise _ResponseValidationError("response node count exceeded")
    if type(value) is str:
        _account_response_text(value, state)
        sanitized = _redact_text(value, protected_values)
        return sanitized
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _ResponseValidationError("non-finite response number")
        return value
    if type(value) is dict:
        object_id = id(value)
        if object_id in active_ids:
            raise _ResponseValidationError("cyclic response")
        active_ids.add(object_id)
        try:
            sanitized_dict: dict[str, object] = {}
            for key, item in value.items():
                if type(key) is not str:
                    raise _ResponseValidationError("response key is invalid")
                _account_response_text(key, state)
                if _is_sensitive_key(key) or _redact_text(key, protected_values) != key:
                    continue
                sanitized_dict[key] = _sanitize_response(
                    item, protected_values, depth=depth + 1, state=state, active_ids=active_ids
                )
            return sanitized_dict
        finally:
            active_ids.remove(object_id)
    if type(value) is list:
        object_id = id(value)
        if object_id in active_ids:
            raise _ResponseValidationError("cyclic response")
        active_ids.add(object_id)
        try:
            return [
                _sanitize_response(item, protected_values, depth=depth + 1, state=state, active_ids=active_ids)
                for item in value
            ]
        finally:
            active_ids.remove(object_id)
    raise _ResponseValidationError("response value is not JSON safe")


def _encode_result(payload: Mapping[str, Any]) -> bytes:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(encoded) > MAX_RESULT_BYTES:
        raise _ResponseValidationError("result bytes exceeded")
    return encoded


def execute_request(
    payload: object,
    *,
    config: GitLabReadConfiguration | None = None,
    transport: GitLabTransport | None = None,
    sensitive_values: Sequence[str] = (),
) -> dict[str, Any]:
    """Execute one injected, GET-style read operation without network setup or secret lookup."""
    request_id = _request_id(payload)
    request, error = _validate_request(payload)
    if request is None:
        return _result(request_id, status="blocked", summary="GITLAB_READ_BLOCKED", blockers=[error])
    configuration_values = _validated_configuration_values(config)
    if configuration_values is None or transport is None:
        return _result(
            request["request_id"],
            status="blocked",
            summary="GITLAB_READ_UNSUPPORTED",
            blockers=["gitlab_read_configuration_or_transport_unavailable"],
        )
    input_data = request["input"]
    operation = input_data["operation"]
    project_ref = input_data["project_ref"]
    try:
        if operation == "project":
            record: object = transport.get_project(project_ref)
        elif operation == "merge_request":
            record = transport.get_merge_request(project_ref, input_data["iid"])
        else:
            record = transport.list_pipeline_jobs(project_ref, input_data["pipeline_id"])
    except Exception:
        return _result(
            request["request_id"],
            status="failed",
            summary="GITLAB_READ_FAILED",
            blockers=["gitlab_read_failed"],
            operation=operation,
        )
    if (operation == "pipeline_jobs" and (
        type(record) is not list or any(type(item) is not dict for item in record)
    )) or (operation != "pipeline_jobs" and type(record) is not dict):
        return _result(
            request["request_id"],
            status="failed",
            summary="GITLAB_READ_FAILED",
            blockers=["gitlab_read_invalid_response"],
            operation=operation,
        )
    try:
        protected_values = (
            *configuration_values,
            *(item for item in sensitive_values if isinstance(item, str)),
        )
        safe_record = _sanitize_response(record, protected_values)
        success = _result(
            request["request_id"],
            status="success",
            summary="GITLAB_READ_OK",
            data={"operation": operation, "record": safe_record},
            evidence=({"kind": f"gitlab_{operation}", "read_only": True},),
            operation=operation,
        )
        _encode_result(success)
        return success
    except Exception:
        return _result(
            request["request_id"],
            status="failed",
            summary="GITLAB_READ_FAILED",
            blockers=["gitlab_read_invalid_response"],
            operation=operation,
        )


def _normalize_protocol_path(path: Path) -> Path:
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise ValueError("unsafe protocol path")
    for alias_text, target_text in (("/var", "/private/var"), ("/tmp", "/private/tmp")):
        alias = Path(alias_text)
        try:
            relative = path.relative_to(alias)
        except ValueError:
            continue
        if alias.is_symlink() and alias.resolve(strict=True) == Path(target_text):
            return Path(target_text) / relative
    return path


def _open_parent(path: Path) -> tuple[int, str]:
    normalized = _normalize_protocol_path(path)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(normalized.anchor, flags)
    try:
        for part in normalized.parent.parts[1:]:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise ValueError("unsafe protocol ancestry")
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode) or (os.name != "nt" and (info.st_uid != os.geteuid() or info.st_mode & 0o022)):
            raise ValueError("unsafe protocol parent")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor, normalized.name


def _read_request_file(path: Path) -> bytes:
    parent_fd, name = _open_parent(path)
    descriptor = -1
    try:
        descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), dir_fd=parent_fd)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_REQUEST_BYTES:
            raise ValueError("unsafe request file")
        chunks: list[bytes] = []
        remaining = MAX_REQUEST_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("oversize request file")
        return raw
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)


def _reserve_output(path: Path) -> tuple[int, int, str]:
    parent_fd, name = _open_parent(path)
    try:
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent_fd)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise ValueError("unsafe result file")
        return descriptor, parent_fd, name
    except Exception:
        os.close(parent_fd)
        raise


def _remove_reserved_output(parent_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=parent_fd)
    except OSError:
        pass


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 4 or arguments[0] != "--request" or arguments[2] != "--output":
        return 2
    descriptor = parent_fd = -1
    output_name = ""
    execution_started = False
    try:
        raw = _read_request_file(Path(arguments[1]))
        descriptor, parent_fd, output_name = _reserve_output(Path(arguments[3]))
        payload = json.loads(raw.decode("utf-8"))
        if not _json_depth(payload):
            raise ValueError("request JSON is too deep")
        execution_started = True
        encoded = _encode_result(execute_request(payload))
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("result write failed")
            view = view[written:]
        os.fsync(descriptor)
        return 0
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, RecursionError):
        if descriptor >= 0 and not execution_started and parent_fd >= 0:
            _remove_reserved_output(parent_fd, output_name)
        return 2
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_fd >= 0:
            os.close(parent_fd)


if __name__ == "__main__":
    raise SystemExit(main())
