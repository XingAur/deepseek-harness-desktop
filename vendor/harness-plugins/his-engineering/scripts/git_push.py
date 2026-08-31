#!/usr/bin/env python3
"""Strict L4 entrypoint for immutable-plan Git branch delivery."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from delivery_closure import DeliveryClosure, DeliveryError, DeliveryPolicy
from delivery_store import SQLiteDeliveryStore
from git_delivery import _json_depth, _read_request_file, _remove_reserved_output, _reserve_output


_FIELDS = frozenset(("schema_version", "request_id", "capability", "provider", "mode", "mutation_level", "authorization", "input", "context"))
_INPUT_FIELDS = frozenset(("delivery_db", "transaction_id", "approved_plan_hash", "phase"))
_PHASES = frozenset(("pre_rc", "rc"))


def _result(request_id: str, status: str, summary: str, *, changed: bool, data: Mapping[str, Any], blockers: list[str], attempted: bool = False) -> dict[str, Any]:
    return {
        "schema_version": "his-capability-result.v1",
        "request_id": request_id,
        "capability": "git.push",
        "provider": "his-engineering",
        "status": status,
        "mutation_level": "L4",
        "changed": changed,
        "summary": summary,
        "data": dict(data),
        "evidence": [],
        "warnings": [],
        "blockers": list(blockers),
        "audit": {"credential_class": "git_remote_write", "external_write_attempted": attempted},
    }


def _validate(payload: object) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        return None, "invalid_capability_request"
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        return None, "invalid_capability_request"
    if (
        payload.get("schema_version") != "his-capability-request.v1"
        or payload.get("capability") != "git.push"
        or payload.get("provider") != "his-engineering"
        or payload.get("mode") != "apply"
        or payload.get("mutation_level") != "L4"
        or payload.get("authorization") != {"explicit": True, "scope": ["repository:push", "capability:git.push"]}
        or payload.get("context") != {}
    ):
        return None, "invalid_capability_request"
    data = payload.get("input")
    if (
        not isinstance(data, dict)
        or set(data) != _INPUT_FIELDS
        or not isinstance(data.get("delivery_db"), str)
        or not Path(data["delivery_db"]).is_absolute()
        or not isinstance(data.get("transaction_id"), int)
        or isinstance(data["transaction_id"], bool)
        or data["transaction_id"] <= 0
        or not isinstance(data.get("approved_plan_hash"), str)
        or len(data["approved_plan_hash"]) != 64
        or data.get("phase") not in _PHASES
    ):
        return None, "invalid_capability_request"
    return payload, ""


def _remote_attempted(outcome: Mapping[str, Any]) -> bool:
    return bool(
        (outcome.get("task_push") or {}).get("pushed")
        or (outcome.get("rc_push") or {}).get("pushed")
    )


def execute_request(payload: object) -> dict[str, Any]:
    request_id = payload.get("request_id", "") if isinstance(payload, dict) and isinstance(payload.get("request_id", ""), str) else ""
    request, error = _validate(payload)
    if request is None:
        return _result(request_id, "blocked", "GIT_PUSH_BLOCKED", changed=False, data={}, blockers=[error])
    data = request["input"]
    database_path = Path(data["delivery_db"])
    if not database_path.is_file() or database_path.is_symlink():
        return _result(request["request_id"], "blocked", "GIT_PUSH_BLOCKED", changed=False, data={}, blockers=["delivery_store_unavailable"])
    try:
        closure = DeliveryClosure(
            store=SQLiteDeliveryStore(database_path),
            policy=DeliveryPolicy.from_payload({}),
        )
        if data["phase"] == "pre_rc":
            outcome = closure.execute_pre_rc_remote_phase(
                data["transaction_id"],
                approved_plan_hash=data["approved_plan_hash"],
            )
        else:
            outcome = closure.execute_stage_two(
                data["transaction_id"],
                approved_plan_hash=data["approved_plan_hash"],
            )
    except DeliveryError as exc:
        attempted = bool(exc.details.get("remote_dispatch_attempted"))
        return _result(
            request["request_id"],
            "failed" if attempted else "blocked",
            "GIT_PUSH_RECOVERY_REQUIRED" if attempted else "GIT_PUSH_BLOCKED",
            changed=attempted,
            data={"error_code": exc.code},
            blockers=[exc.code],
            attempted=attempted,
        )
    except Exception:
        return _result(request["request_id"], "blocked", "GIT_PUSH_BLOCKED", changed=False, data={}, blockers=["delivery_runtime_unavailable"])
    attempted = _remote_attempted(outcome)
    return _result(request["request_id"], "success", "GIT_PUSH_OK", changed=attempted, data=outcome, blockers=[], attempted=attempted)


def _write_all(descriptor: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("result write failed")
        view = view[written:]


def main() -> int:
    if len(sys.argv) != 5 or sys.argv[1] != "--request" or sys.argv[3] != "--output":
        return 2
    descriptor = parent_fd = -1
    output_name = ""
    try:
        payload = json.loads(_read_request_file(Path(sys.argv[2])).decode("utf-8"))
        if not _json_depth(payload):
            return 2
        descriptor, parent_fd, output_name = _reserve_output(Path(sys.argv[4]))
        _write_all(descriptor, json.dumps(execute_request(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        os.fsync(descriptor)
        return 0
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, RecursionError):
        if descriptor >= 0 and parent_fd >= 0:
            _remove_reserved_output(parent_fd, output_name)
        return 2
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_fd >= 0:
            os.close(parent_fd)


if __name__ == "__main__":
    raise SystemExit(main())
