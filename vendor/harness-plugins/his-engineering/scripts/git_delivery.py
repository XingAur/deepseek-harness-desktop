#!/usr/bin/env python3
"""Strict stdin/stdout entrypoint for the L3 local-commit capability."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Mapping

from delivery_closure import DeliveryClosure, DeliveryError, DeliveryPolicy
from delivery_store import SQLiteDeliveryStore


_FIELDS = frozenset(("schema_version", "request_id", "capability", "provider", "mode", "mutation_level", "authorization", "input", "context"))
_INPUT_FIELDS = frozenset(("delivery_db", "transaction_id", "approved_plan_hash"))
MAX_STDIN_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 128


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
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(normalized.anchor, flags)
    try:
        for part in normalized.parent.parts[1:]:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise ValueError("unsafe protocol ancestry")
        parent_info = os.fstat(descriptor)
        if not stat.S_ISDIR(parent_info.st_mode):
            raise ValueError("unsafe protocol parent")
        if os.name != "nt" and (
            parent_info.st_uid != os.geteuid() or parent_info.st_mode & 0o022
        ):
            raise ValueError("unsafe protocol parent permissions")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor, normalized.name


def _read_request_file(path: Path) -> bytes:
    parent_fd, name = _open_parent(path)
    descriptor = -1
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STDIN_BYTES:
            raise ValueError("unsafe request file")
        chunks: list[bytes] = []
        remaining = MAX_STDIN_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_STDIN_BYTES:
            raise ValueError("oversize request file")
        return raw
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)


def _reserve_output(path: Path) -> tuple[int, int, str]:
    parent_fd, name = _open_parent(path)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(name, flags, 0o600, dir_fd=parent_fd)
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


def _result(request_id: str, status: str, summary: str, *, changed: bool, data: Mapping[str, Any], blockers: list[str], mutation_attempted: bool = False) -> dict[str, Any]:
    return {"schema_version": "his-capability-result.v1", "request_id": request_id, "capability": "git.commit-local", "provider": "his-engineering", "status": status, "mutation_level": "L3", "changed": changed, "summary": summary, "data": dict(data), "evidence": [], "warnings": [], "blockers": list(blockers), "audit": {"credential_class": "none", "external_write_attempted": False, "repository_mutation_attempted": mutation_attempted}}


def _validate(payload: object) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        return None, "invalid_capability_request"
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        return None, "invalid_capability_request"
    if payload.get("schema_version") != "his-capability-request.v1" or payload.get("capability") != "git.commit-local" or payload.get("provider") != "his-engineering" or payload.get("mode") != "apply" or payload.get("mutation_level") != "L3" or payload.get("authorization") != {"explicit": True, "scope": ["repository:commit-local"]} or payload.get("context") != {}:
        return None, "invalid_capability_request"
    data = payload.get("input")
    if not isinstance(data, dict) or set(data) != _INPUT_FIELDS or not isinstance(data.get("delivery_db"), str) or not Path(data["delivery_db"]).is_absolute() or not isinstance(data.get("transaction_id"), int) or isinstance(data["transaction_id"], bool) or data["transaction_id"] <= 0 or not isinstance(data.get("approved_plan_hash"), str) or len(data["approved_plan_hash"]) != 64:
        return None, "invalid_capability_request"
    return payload, ""


def execute_request(payload: object) -> dict[str, Any]:
    request_id = payload.get("request_id", "") if isinstance(payload, dict) and isinstance(payload.get("request_id", ""), str) else ""
    request, error = _validate(payload)
    if request is None:
        return _result(request_id, "blocked", "GIT_COMMIT_LOCAL_BLOCKED", changed=False, data={}, blockers=[error])
    input_data = request["input"]
    database_path = Path(input_data["delivery_db"])
    # This capability consumes an already initialized plugin store.  It never
    # creates a database as a side effect of a user-supplied capability call.
    if not database_path.is_file() or database_path.is_symlink():
        return _result(request["request_id"], "blocked", "GIT_COMMIT_LOCAL_BLOCKED", changed=False, data={}, blockers=["delivery_store_unavailable"])
    try:
        store = SQLiteDeliveryStore(database_path)
        closure = DeliveryClosure(store=store, policy=DeliveryPolicy.from_payload({}))
        outcome = closure.execute_stage_one(int(input_data["transaction_id"]), approved_plan_hash=input_data["approved_plan_hash"])
    except DeliveryError as exc:
        published = exc.details.get("published_commit") if isinstance(exc.details, dict) else None
        if isinstance(published, dict):
            return _result(request["request_id"], "failed", "GIT_COMMIT_LOCAL_RECOVERY_REQUIRED", changed=True, data={"error_code": exc.code, "commit": published, "recovery_persisted": bool(exc.details.get("recovery_persisted", False))}, blockers=[exc.code, "recovery_required"], mutation_attempted=True)
        changed = bool(exc.details.get("repository_changed", False))
        return _result(request["request_id"], "failed" if changed else "blocked", "GIT_COMMIT_LOCAL_RECOVERY_REQUIRED" if changed else "GIT_COMMIT_LOCAL_BLOCKED", changed=changed, data={"error_code": exc.code, "recovery": bool(exc.details.get("repository_mutation_attempted", False))}, blockers=[exc.code], mutation_attempted=bool(exc.details.get("repository_mutation_attempted", False)))
    except Exception:
        # Capability stdout is a strict protocol boundary: never leak an
        # unexpected store/runtime exception or emit a second result.
        return _result(request["request_id"], "blocked", "GIT_COMMIT_LOCAL_BLOCKED", changed=False, data={}, blockers=["delivery_runtime_unavailable"])
    idempotent = bool(outcome.get("idempotent"))
    return _result(request["request_id"], "success", "GIT_COMMIT_LOCAL_OK", changed=not idempotent, data=outcome, blockers=[], mutation_attempted=bool(outcome.get("repository_mutation_attempted", not idempotent)))


def _read_request_payload() -> object:
    try:
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        raw = stream.read(MAX_STDIN_BYTES + 1)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if not isinstance(raw, bytes) or len(raw) > MAX_STDIN_BYTES:
            return None
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def _json_depth(value: object, depth: int = 0) -> bool:
    if depth > MAX_JSON_DEPTH:
        return False
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_depth(item, depth + 1) for key, item in value.items())
    if isinstance(value, list):
        return all(_json_depth(item, depth + 1) for item in value)
    return True


def main() -> int:
    if "--request" in sys.argv or "--output" in sys.argv:
        if len(sys.argv) != 5 or sys.argv[1] != "--request" or sys.argv[3] != "--output":
            return 2
        request_path, output_path = Path(sys.argv[2]), Path(sys.argv[4])
        descriptor = -1
        parent_fd = -1
        output_name = ""
        execution_started = False
        try:
            raw = _read_request_file(request_path)
            descriptor, parent_fd, output_name = _reserve_output(output_path)
            payload = json.loads(raw.decode("utf-8"))
            if not _json_depth(payload):
                raise ValueError("request JSON is too deep")
            execution_started = True
            try:
                result = execute_request(payload)
            except Exception:
                request_id = (
                    payload.get("request_id", "")
                    if isinstance(payload, dict)
                    and isinstance(payload.get("request_id", ""), str)
                    else ""
                )
                result = _result(
                    request_id,
                    "failed",
                    "GIT_COMMIT_LOCAL_RECOVERY_REQUIRED",
                    changed=True,
                    data={},
                    blockers=["delivery_runtime_uncertain", "recovery_required"],
                    mutation_attempted=True,
                )
            encoded = json.dumps(
                result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
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
    payload = _read_request_payload()
    try:
        result = execute_request(payload)
    except Exception:
        request_id = (
            payload.get("request_id", "")
            if isinstance(payload, dict)
            and isinstance(payload.get("request_id", ""), str)
            else ""
        )
        result = _result(
            request_id,
            "failed",
            "GIT_COMMIT_LOCAL_RECOVERY_REQUIRED",
            changed=True,
            data={},
            blockers=["delivery_runtime_uncertain", "recovery_required"],
            mutation_attempted=True,
        )
    sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
