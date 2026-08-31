#!/usr/bin/env python3
"""Strict L4 gate for one immutable-plan GitLab delivery action."""
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
_INPUT_FIELDS = frozenset(("delivery_db", "transaction_id", "approved_plan_hash"))


def _result(request_id: str, status: str, summary: str, *, data: Mapping[str, Any], blockers: list[str]) -> dict[str, Any]:
    return {
        "schema_version": "his-capability-result.v1",
        "request_id": request_id,
        "capability": "gitlab.write",
        "provider": "his-engineering",
        "status": status,
        "mutation_level": "L4",
        "changed": False,
        "summary": summary,
        "data": dict(data),
        "evidence": [],
        "warnings": [],
        "blockers": list(blockers),
        "audit": {"credential_class": "gitlab_write", "external_write_attempted": False},
    }


def _validate(payload: object) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        return None, "invalid_capability_request"
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        return None, "invalid_capability_request"
    if (
        payload.get("schema_version") != "his-capability-request.v1"
        or payload.get("capability") != "gitlab.write"
        or payload.get("provider") != "his-engineering"
        or payload.get("mode") != "apply"
        or payload.get("mutation_level") != "L4"
        or payload.get("authorization") != {"explicit": True, "scope": ["gitlab:write", "capability:gitlab.write"]}
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
    ):
        return None, "invalid_capability_request"
    return payload, ""


def execute_request(payload: object) -> dict[str, Any]:
    request_id = payload.get("request_id", "") if isinstance(payload, dict) and isinstance(payload.get("request_id", ""), str) else ""
    request, error = _validate(payload)
    if request is None:
        return _result(request_id, "blocked", "GITLAB_WRITE_BLOCKED", data={}, blockers=[error])
    data = request["input"]
    database_path = Path(data["delivery_db"])
    if not database_path.is_file() or database_path.is_symlink():
        return _result(request["request_id"], "blocked", "GITLAB_WRITE_BLOCKED", data={}, blockers=["delivery_store_unavailable"])
    try:
        closure = DeliveryClosure(
            store=SQLiteDeliveryStore(database_path),
            policy=DeliveryPolicy.from_payload({}),
        )
        current = closure.show(data["transaction_id"])
        transaction = current["transaction"]
        plan = current["plan"]
        action = (plan.get("actions") or {}).get("gitlab_write")
        if (
            transaction.get("state") != "gitlab_delivery_pending"
            or plan.get("plan_hash") != data["approved_plan_hash"]
            or not isinstance(action, Mapping)
            or action.get("action") not in {"merge_request.create", "merge_request.comment.write"}
        ):
            return _result(request["request_id"], "blocked", "GITLAB_WRITE_BLOCKED", data={}, blockers=["gitlab_delivery_not_ready"])
    except DeliveryError as exc:
        return _result(request["request_id"], "blocked", "GITLAB_WRITE_BLOCKED", data={"error_code": exc.code}, blockers=[exc.code])
    except Exception:
        return _result(request["request_id"], "blocked", "GITLAB_WRITE_BLOCKED", data={}, blockers=["delivery_runtime_unavailable"])
    return _result(request["request_id"], "success", "GITLAB_WRITE_READY", data={"gitlab_action": dict(action)}, blockers=[])


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
