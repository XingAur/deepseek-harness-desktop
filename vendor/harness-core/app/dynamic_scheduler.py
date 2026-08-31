from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app import database


DYNAMIC_SCHEDULER_SCHEMA_VERSION = "1.0-dynamic-dry-run-scheduler"
ALLOWED_OUTCOMES = {"success", "failure", "timeout"}
COMPLETED_STATES = {"succeeded_simulated", "completed_from_contract"}
BLOCKED_STATES = {"blocked_budget", "blocked_retry_exhausted", "blocked_stale"}


class DynamicDryRunScheduler:
    def __init__(self) -> None:
        database.init_db()

    def start(self, plan_id: int) -> dict[str, Any]:
        plan = database.get_dynamic_plan(plan_id)
        if plan is None:
            raise ValueError(f"dynamic plan 不存在：{plan_id}")
        if plan["status"] not in {"ready", "needs_human_confirmation"}:
            raise ValueError(f"dynamic plan 状态不允许启动 dry-run：{plan['status']}")
        payload = plan.get("plan_payload") or {}
        roles = {
            str(item.get("role_id")): item
            for item in (payload.get("team") or {}).get("roles") or []
        }
        subtasks = database.list_dynamic_subtasks(plan_id)
        if not subtasks:
            raise ValueError("dynamic plan 没有可调度节点")
        latest_contracts = {
            str(item["node_id"]): item
            for item in database.list_contract_artifacts(plan_id, latest_only=True)
        }
        schedule_id = database.add_dynamic_schedule(
            {
                "plan_id": plan_id,
                "task_id": int(plan["task_id"]),
                "mode": "dry_run",
                "status": "active",
                "policy_snapshot": {
                    "schema_version": DYNAMIC_SCHEDULER_SCHEMA_VERSION,
                    "roles": roles,
                    "execution_enabled": False,
                },
            }
        )
        for subtask in subtasks:
            node_id = str(subtask["node_id"])
            role = roles.get(str(subtask["role_id"])) or {}
            contract = latest_contracts.get(node_id) or {}
            state = "planned"
            if contract.get("status") == "current" and int(contract.get("artifact_version") or 0) >= 1:
                state = "completed_from_contract"
            elif contract.get("status") == "stale":
                state = "blocked_stale"
            database.add_dynamic_node_state(
                {
                    "schedule_id": schedule_id,
                    "plan_id": plan_id,
                    "node_id": node_id,
                    "role_id": subtask["role_id"],
                    "state": state,
                    "max_retries": int(role.get("max_retries") or 0),
                    "input_budget_tokens": int(role.get("input_budget_tokens") or 0),
                    "output_budget_tokens": int(role.get("output_budget_tokens") or 0),
                    "timeout_seconds": int(role.get("timeout_seconds") or 0),
                    "parallel_allowed": bool(role.get("parallel_allowed", True)),
                    "human_only": bool(role.get("human_only"))
                    or bool(subtask.get("human_confirmation_required")),
                    "last_decision": {"reason": "initialized_from_registered_plan"},
                }
            )
        dispatched = self._reconcile_and_dispatch(schedule_id, include_retry=False)
        database.add_dynamic_schedule_event(
            {
                "schedule_id": schedule_id,
                "event_key": f"system:start:{schedule_id}",
                "event_type": "start",
                "decision": {
                    "idempotent": False,
                    "dry_run": True,
                    "dispatched_nodes": dispatched,
                },
            }
        )
        self._save_checkpoint(schedule_id, last_event_key=f"system:start:{schedule_id}")
        return self.get_schedule(schedule_id)

    def advance(self, schedule_id: int, event: dict[str, Any] | None = None) -> dict[str, Any]:
        schedule = self._require_schedule(schedule_id)
        if schedule.get("mode") != "dry_run":
            raise ValueError("只允许推进 dry_run schedule")
        if not self._checkpoint_is_valid(schedule_id):
            raise ValueError("dynamic schedule checkpoint 校验失败，拒绝继续推进")
        self._sync_registry_contracts(schedule_id)
        if event is None:
            event_key = f"system:tick:{schedule_id}:{int(schedule['tick']) + 1}"
            dispatched = self._reconcile_and_dispatch(schedule_id, include_retry=True)
            database.add_dynamic_schedule_event(
                {
                    "schedule_id": schedule_id,
                    "event_key": event_key,
                    "event_type": "tick",
                    "decision": {
                        "idempotent": False,
                        "dry_run": True,
                        "dispatched_nodes": dispatched,
                    },
                }
            )
            self._save_checkpoint(schedule_id, last_event_key=event_key)
            return self.get_schedule(schedule_id)

        normalized = validate_schedule_event(event)
        event_key = normalized["event_id"]
        existing = database.get_dynamic_schedule_event(schedule_id, event_key)
        if existing:
            snapshot = self.get_schedule(schedule_id)
            snapshot["last_action"] = {
                "event_key": event_key,
                "event_type": existing["event_type"],
                "idempotent": True,
                "dry_run": True,
            }
            return snapshot
        states = {
            str(item["node_id"]): item
            for item in database.list_dynamic_node_states(schedule_id)
        }
        node = states.get(normalized["node_id"])
        if node is None:
            raise ValueError(f"schedule node 不存在：{normalized['node_id']}")
        if node["state"] != "running_simulated":
            raise ValueError(f"事件只能作用于 running_simulated 节点：{node['state']}")

        decision = self._resolve_event(node, normalized)
        database.update_dynamic_node_state(
            schedule_id,
            normalized["node_id"],
            state=decision["state"],
            last_event_id=event_key,
            last_decision=decision,
        )
        database.add_dynamic_schedule_event(
            {
                "schedule_id": schedule_id,
                "event_key": event_key,
                "event_type": "outcome",
                "node_id": normalized["node_id"],
                "payload": normalized,
                "decision": {**decision, "idempotent": False, "dry_run": True},
            }
        )
        self._reconcile_and_dispatch(schedule_id, include_retry=False)
        self._save_checkpoint(schedule_id, last_event_key=event_key)
        return self.get_schedule(schedule_id)

    def get_schedule(self, schedule_id: int) -> dict[str, Any]:
        schedule = self._require_schedule(schedule_id)
        node_states = database.list_dynamic_node_states(schedule_id)
        events = database.list_dynamic_schedule_events(schedule_id)
        checkpoint = database.get_latest_dynamic_checkpoint(schedule_id)
        checkpoint_view: dict[str, Any] = {}
        if checkpoint:
            checkpoint_view = {
                **checkpoint,
                "hash_valid": self._checkpoint_is_valid(schedule_id),
            }
        last_event = events[-1] if events else {}
        return {
            "schema_version": DYNAMIC_SCHEDULER_SCHEMA_VERSION,
            "dry_run": True,
            "execution_enabled": False,
            "schedule": schedule,
            "node_states": node_states,
            "events": events,
            "checkpoint": checkpoint_view,
            "last_action": {
                "event_key": last_event.get("event_key", ""),
                "event_type": last_event.get("event_type", ""),
                "idempotent": False,
                "dry_run": True,
            },
            "boundaries": [
                "仅模拟 DAG 调度和状态转换，不执行任何节点。",
                "succeeded_simulated 不代表真实契约、代码、测试或业务验收完成。",
                "不调用模型、工具、worktree、PG、Git、云效、TAPD、发布或部署。",
            ],
        }

    def _resolve_event(self, node: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        budget_violations: dict[str, str] = {}
        if event["input_tokens"] > int(node["input_budget_tokens"]):
            budget_violations["input_tokens"] = (
                f"{event['input_tokens']}>{int(node['input_budget_tokens'])}"
            )
        if event["output_tokens"] > int(node["output_budget_tokens"]):
            budget_violations["output_tokens"] = (
                f"{event['output_tokens']}>{int(node['output_budget_tokens'])}"
            )
        if event["elapsed_seconds"] > int(node["timeout_seconds"]):
            budget_violations["elapsed_seconds"] = (
                f"{event['elapsed_seconds']}>{int(node['timeout_seconds'])}"
            )
        if budget_violations:
            return {
                "state": "blocked_budget",
                "reason": "role_budget_exceeded",
                **budget_violations,
            }
        if event["outcome"] == "success":
            return {"state": "succeeded_simulated", "reason": "explicit_simulated_success_event"}
        if int(node["attempt_count"]) <= int(node["max_retries"]):
            return {
                "state": "retry_wait",
                "reason": f"simulated_{event['outcome']}_within_retry_budget",
            }
        return {
            "state": "blocked_retry_exhausted",
            "reason": f"simulated_{event['outcome']}_retry_budget_exhausted",
        }

    def _sync_registry_contracts(self, schedule_id: int) -> None:
        schedule = self._require_schedule(schedule_id)
        contracts = database.list_contract_artifacts(int(schedule["plan_id"]), latest_only=True)
        states = {
            str(item["node_id"]): item
            for item in database.list_dynamic_node_states(schedule_id)
        }
        for contract in contracts:
            node_id = str(contract["node_id"])
            state = states.get(node_id)
            if state is None:
                continue
            if contract.get("status") == "current" and int(contract.get("artifact_version") or 0) >= 1:
                database.update_dynamic_node_state(
                    schedule_id,
                    node_id,
                    state="completed_from_contract",
                    last_decision={"reason": "current_registry_contract_detected"},
                )
            elif contract.get("status") == "stale" and state["state"] not in BLOCKED_STATES:
                database.update_dynamic_node_state(
                    schedule_id,
                    node_id,
                    state="blocked_stale",
                    last_decision={"reason": "stale_registry_contract_detected"},
                )

    def _reconcile_and_dispatch(self, schedule_id: int, *, include_retry: bool) -> list[str]:
        schedule = self._require_schedule(schedule_id)
        plan_id = int(schedule["plan_id"])
        edges = database.list_dynamic_edges(plan_id)
        predecessors: dict[str, set[str]] = {}
        for edge in edges:
            predecessors.setdefault(str(edge["target_node_id"]), set()).add(
                str(edge["source_node_id"])
            )
        states = {
            str(item["node_id"]): item
            for item in database.list_dynamic_node_states(schedule_id)
        }
        if include_retry:
            for node_id, item in states.items():
                if item["state"] == "retry_wait":
                    database.update_dynamic_node_state(
                        schedule_id,
                        node_id,
                        state="ready",
                        last_decision={"reason": "explicit_retry_tick"},
                    )
            states = {
                str(item["node_id"]): item
                for item in database.list_dynamic_node_states(schedule_id)
            }

        for node_id, item in states.items():
            if item["state"] != "planned":
                continue
            upstream = predecessors.get(node_id, set())
            if all(states[parent]["state"] in COMPLETED_STATES for parent in upstream):
                next_state = "paused_human" if item["human_only"] else "ready"
                database.update_dynamic_node_state(
                    schedule_id,
                    node_id,
                    state=next_state,
                    last_decision={
                        "reason": "human_confirmation_required"
                        if item["human_only"]
                        else "all_predecessors_completed"
                    },
                )
        states = {
            str(item["node_id"]): item
            for item in database.list_dynamic_node_states(schedule_id)
        }
        ready = [item for item in states.values() if item["state"] == "ready"]
        running = [item for item in states.values() if item["state"] == "running_simulated"]
        dispatched: list[str] = []
        if ready:
            serial_ready = [item for item in ready if not item["parallel_allowed"]]
            if serial_ready:
                selected = [] if running else [serial_ready[0]]
            elif any(not item["parallel_allowed"] for item in running):
                selected = []
            else:
                selected = ready
            for item in selected:
                database.update_dynamic_node_state(
                    schedule_id,
                    str(item["node_id"]),
                    state="running_simulated",
                    attempt_count=int(item["attempt_count"]) + 1,
                    last_decision={"reason": "dry_run_dispatch_only", "execution_enabled": False},
                )
                dispatched.append(str(item["node_id"]))
        self._refresh_schedule_status(schedule_id)
        return dispatched

    def _refresh_schedule_status(self, schedule_id: int) -> str:
        states = database.list_dynamic_node_states(schedule_id)
        values = {str(item["state"]) for item in states}
        if values & BLOCKED_STATES:
            status = "blocked"
        elif values <= COMPLETED_STATES:
            status = "completed_simulated"
        elif "paused_human" in values and not values.intersection(
            {"running_simulated", "ready", "planned", "retry_wait"}
        ):
            status = "paused_human"
        else:
            status = "active"
        database.update_dynamic_schedule(schedule_id, status=status)
        return status

    def _save_checkpoint(self, schedule_id: int, *, last_event_key: str) -> None:
        schedule = self._require_schedule(schedule_id)
        next_tick = int(schedule["tick"]) + 1
        database.update_dynamic_schedule(schedule_id, tick=next_tick)
        schedule = self._require_schedule(schedule_id)
        states = database.list_dynamic_node_states(schedule_id)
        payload = build_checkpoint_payload(schedule, states, last_event_key=last_event_key)
        database.add_dynamic_checkpoint(
            {
                "schedule_id": schedule_id,
                "tick": next_tick,
                "checkpoint_hash": checkpoint_hash(payload),
                "payload": payload,
            }
        )

    def _checkpoint_is_valid(self, schedule_id: int) -> bool:
        checkpoint = database.get_latest_dynamic_checkpoint(schedule_id)
        if checkpoint is None:
            return False
        payload = checkpoint.get("payload") or {}
        stored_hash = str(checkpoint.get("checkpoint_hash") or "")
        if checkpoint_hash(payload) != stored_hash:
            return False
        schedule = self._require_schedule(schedule_id)
        states = database.list_dynamic_node_states(schedule_id)
        current_payload = build_checkpoint_payload(
            schedule,
            states,
            last_event_key=str(payload.get("last_event_key") or ""),
        )
        return checkpoint_hash(current_payload) == stored_hash

    @staticmethod
    def _require_schedule(schedule_id: int) -> dict[str, Any]:
        schedule = database.get_dynamic_schedule(schedule_id)
        if schedule is None:
            raise ValueError(f"dynamic schedule 不存在：{schedule_id}")
        return schedule


def validate_schedule_event(event: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError("schedule event 必须是 JSON 对象")
    event_id = str(event.get("event_id") or "").strip()
    node_id = str(event.get("node_id") or "").strip()
    outcome = str(event.get("outcome") or "").strip()
    if not event_id or not node_id:
        raise ValueError("schedule event 缺少 event_id 或 node_id")
    if outcome not in ALLOWED_OUTCOMES:
        raise ValueError(f"schedule event outcome 不受支持：{outcome}")
    normalized: dict[str, Any] = {"event_id": event_id, "node_id": node_id, "outcome": outcome}
    for key in ("elapsed_seconds", "input_tokens", "output_tokens"):
        value = int(event.get(key) or 0)
        if value < 0:
            raise ValueError(f"schedule event {key} 不能为负数")
        normalized[key] = value
    return normalized


def build_checkpoint_payload(
    schedule: dict[str, Any],
    states: list[dict[str, Any]],
    *,
    last_event_key: str,
) -> dict[str, Any]:
    node_states = [
        {
            "node_id": item["node_id"],
            "state": item["state"],
            "attempt_count": item["attempt_count"],
            "last_event_id": item["last_event_id"],
        }
        for item in states
    ]
    groups: dict[str, list[str]] = {
        "completed_nodes": [],
        "running_nodes": [],
        "paused_nodes": [],
        "blocked_nodes": [],
        "retry_nodes": [],
    }
    for item in states:
        node_id = str(item["node_id"])
        state = str(item["state"])
        if state in COMPLETED_STATES:
            groups["completed_nodes"].append(node_id)
        if state == "running_simulated":
            groups["running_nodes"].append(node_id)
        if state == "paused_human":
            groups["paused_nodes"].append(node_id)
        if state in BLOCKED_STATES:
            groups["blocked_nodes"].append(node_id)
        if state == "retry_wait":
            groups["retry_nodes"].append(node_id)
    return {
        "schema_version": DYNAMIC_SCHEDULER_SCHEMA_VERSION,
        "dry_run": True,
        "execution_enabled": False,
        "schedule_id": schedule["id"],
        "plan_id": schedule["plan_id"],
        "tick": schedule["tick"],
        "schedule_status": schedule["status"],
        "last_event_key": last_event_key,
        "node_states": node_states,
        **groups,
    }


def checkpoint_hash(payload: dict[str, Any]) -> str:
    encoded = canonical_json(payload).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def write_dynamic_schedule_outputs(output_dir: Path, snapshot: dict[str, Any]) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "dynamic_schedule.json"
    markdown_path = output_dir / "dynamic_schedule.md"
    checkpoint_path = output_dir / "dynamic_schedule_checkpoint.json"
    json_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(dynamic_schedule_to_markdown(snapshot), encoding="utf-8")
    checkpoint_path.write_text(
        json.dumps(snapshot.get("checkpoint") or {}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return json_path, markdown_path, checkpoint_path


def dynamic_schedule_to_markdown(snapshot: dict[str, Any]) -> str:
    schedule = snapshot.get("schedule") or {}
    lines = [
        "# 动态调度 Dry-run",
        "",
        f"- Schedule ID：{schedule.get('id') or '-'}",
        f"- Plan ID：{schedule.get('plan_id') or '-'}",
        f"- 状态：{schedule.get('status') or '-'}",
        "- Dry-run：是",
        "- 真实节点执行：关闭",
        "",
        "## 节点状态",
        "",
    ]
    for item in snapshot.get("node_states") or []:
        lines.append(
            f"- `{item.get('node_id')}` [{item.get('role_id')}] "
            f"state={item.get('state')} attempts={item.get('attempt_count')}"
        )
    lines.extend(
        (
            "",
            "## 边界",
            "",
            "- succeeded_simulated 只代表调度测试事件，不代表真实工作完成。",
            "- 不执行模型、工具、worktree、PG、Git 或外部系统动作。",
        )
    )
    return "\n".join(lines)


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
