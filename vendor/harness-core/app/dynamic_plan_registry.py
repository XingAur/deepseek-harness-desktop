from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app import database
from app.dynamic_planning import DYNAMIC_PLANNING_SCHEMA_VERSION


DYNAMIC_PLAN_REGISTRY_SCHEMA_VERSION = "1.0-dynamic-plan-registry"
SUPPORTED_CONTRACT_SCHEMA_VERSIONS = {"1.0"}
MAX_CONTRACT_CONTENT_BYTES = 1_000_000
FORBIDDEN_CREDENTIAL_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "database_url",
    "dsn",
    "password",
    "pat",
    "secret",
    "token",
}


class DynamicPlanRegistry:
    def __init__(self) -> None:
        database.init_db()

    def register_plan(self, plan_payload: dict[str, Any], *, task_key: str = "") -> dict[str, Any]:
        validated = validate_dynamic_plan_payload(plan_payload)
        requirement = validated["request"]
        requirement_id = str(requirement.get("requirement_id") or "LOCAL-REQUIREMENT")
        resolved_task_key = task_key.strip() or f"requirement-{requirement_id.lower()}"
        task = database.get_task_by_key(resolved_task_key)
        if task is None:
            task_id = database.upsert_task(
                {
                    "task_key": resolved_task_key,
                    "entity_kind": "requirement",
                    "entity_id": requirement_id,
                    "entity_title": str(requirement.get("title") or requirement_id),
                    "source_type": "dynamic-plan",
                    "current_stage": "dynamic_plan",
                    "status": validated.get("status") or "planned",
                    "risk_level": (validated.get("assessment") or {}).get("level") or "",
                    "can_commit": False,
                    "can_yunxiao_transition": False,
                    "metadata": {"dynamic_planning": True},
                }
            )
            task = database.get_task(task_id)
        if task is None:
            raise RuntimeError("Task Manager 父任务创建失败")
        task_id = int(task["id"])

        plan_hash = build_plan_hash(validated)
        existing = database.get_dynamic_plan_by_hash(task_id, plan_hash)
        if existing:
            return {
                "task_id": task_id,
                "plan_id": int(existing["id"]),
                "plan_hash": plan_hash,
                "idempotent": True,
                "snapshot": self.get_plan(int(existing["id"])),
            }

        previous = database.get_latest_dynamic_plan(task_id)
        plan_id = database.add_dynamic_plan(
            {
                "task_id": task_id,
                "plan_hash": plan_hash,
                "schema_version": validated["schema_version"],
                "status": validated["status"],
                "complexity_level": validated["assessment"]["level"],
                "total_score": validated["assessment"]["total_score"],
                "plan_payload": validated,
                "supersedes_plan_id": int(previous["id"]) if previous else None,
            }
        )
        if previous:
            database.update_dynamic_plan(int(previous["id"]), superseded_by_plan_id=plan_id)

        nodes = validated["graph"]["nodes"]
        for node in nodes:
            database.add_dynamic_subtask(
                {
                    "plan_id": plan_id,
                    "task_id": task_id,
                    "node_id": node["node_id"],
                    "title": node["title"],
                    "node_kind": node["node_kind"],
                    "role_id": node["role_id"],
                    "status": "planned",
                    "output_contract": node["output_contract"],
                    "allowed_paths": node.get("allowed_paths") or [],
                    "parallel_group": node.get("parallel_group") or "",
                    "human_confirmation_required": bool(node.get("human_confirmation_required")),
                    "metadata": {
                        "input_contracts": node.get("input_contracts") or [],
                        "completion_criteria": node.get("completion_criteria") or [],
                    },
                }
            )
        for edge in validated["graph"]["edges"]:
            database.add_dynamic_edge(
                {
                    "plan_id": plan_id,
                    "source_node_id": edge["source"],
                    "target_node_id": edge["target"],
                    "dependency_type": edge["dependency_type"],
                    "artifact_schema": edge["artifact_schema"],
                    "reason": edge.get("reason") or "",
                }
            )
        handoff_by_node = {item["node_id"]: item for item in validated["handoffs"]}
        for node in nodes:
            handoff = handoff_by_node[node["node_id"]]
            database.add_contract_artifact(
                {
                    "plan_id": plan_id,
                    "task_id": task_id,
                    "node_id": node["node_id"],
                    "artifact_id": f"planned:{plan_hash[:16]}:{node['node_id']}",
                    "artifact_version": 0,
                    "schema_name": handoff["schema_name"],
                    "schema_version": handoff["schema_version"],
                    "producer": handoff["producer"],
                    "input_artifact_ids": handoff.get("input_artifact_ids") or [],
                    "content_hash": handoff["content_hash"],
                    "status": "planned",
                    "payload": {"contract_definition": handoff},
                }
            )
        metadata = dict(task.get("metadata") or {})
        plan_ids = [int(item) for item in metadata.get("dynamic_plan_ids") or []]
        if plan_id not in plan_ids:
            plan_ids.append(plan_id)
        metadata.update({"dynamic_planning": True, "dynamic_plan_ids": plan_ids, "latest_dynamic_plan_id": plan_id})
        database.update_task(task_id, metadata=metadata)
        database.add_dynamic_audit_event(
            {
                "task_id": task_id,
                "plan_id": plan_id,
                "action": "register_dynamic_plan",
                "status": "recorded",
                "details": {
                    "plan_hash": plan_hash,
                    "node_count": len(nodes),
                    "edge_count": len(validated["graph"]["edges"]),
                },
            }
        )
        return {
            "task_id": task_id,
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "idempotent": False,
            "snapshot": self.get_plan(plan_id),
        }

    def get_plan(self, plan_id: int) -> dict[str, Any]:
        plan = database.get_dynamic_plan(plan_id)
        if plan is None:
            raise ValueError(f"dynamic plan 不存在：{plan_id}")
        task = database.get_task(int(plan["task_id"]))
        subtasks = database.list_dynamic_subtasks(plan_id)
        edges = database.list_dynamic_edges(plan_id)
        contracts = database.list_contract_artifacts(plan_id)
        latest_contracts = database.list_contract_artifacts(plan_id, latest_only=True)
        contracts_by_node = {item["node_id"]: item for item in latest_contracts}
        recovery_preview = build_recovery_preview(subtasks, edges, contracts_by_node)
        return {
            "schema_version": DYNAMIC_PLAN_REGISTRY_SCHEMA_VERSION,
            "readonly": True,
            "execution_enabled": False,
            "task": task,
            "plan": plan,
            "subtasks": subtasks,
            "edges": edges,
            "contracts": contracts,
            "contracts_by_node": contracts_by_node,
            "recovery_preview": recovery_preview,
            "audit_events": database.list_dynamic_audit_events(plan_id),
            "boundaries": [
                "只登记和展示动态计划，不执行节点。",
                "恢复信息是只读预览，不创建 worktree 或修改业务代码。",
                "提交、推送、发布和外部需求系统写入保持关闭。",
            ],
        }

    def record_contract(
        self,
        *,
        plan_id: int,
        node_id: str,
        schema_name: str,
        schema_version: str,
        producer: str,
        content: dict[str, Any],
        input_artifact_ids: tuple[str, ...],
    ) -> dict[str, Any]:
        if not isinstance(content, dict):
            raise ValueError("contract content 必须是 JSON 对象")
        credential_path = find_credential_field(content)
        if credential_path:
            raise ValueError(f"contract content 禁止保存凭证字段：{credential_path}")
        encoded_content = canonical_json(content).encode("utf-8")
        if len(encoded_content) > MAX_CONTRACT_CONTENT_BYTES:
            raise ValueError("contract content 超过 1 MB 限制")
        if schema_version not in SUPPORTED_CONTRACT_SCHEMA_VERSIONS:
            raise ValueError(f"不支持的 contract schema version：{schema_version}")

        plan = database.get_dynamic_plan(plan_id)
        if plan is None:
            raise ValueError(f"dynamic plan 不存在：{plan_id}")
        subtasks = database.list_dynamic_subtasks(plan_id)
        node_by_id = {item["node_id"]: item for item in subtasks}
        node = node_by_id.get(node_id)
        if node is None:
            raise ValueError(f"dynamic node 不存在：{node_id}")
        if schema_name != node["output_contract"]:
            raise ValueError(f"contract schema 与节点输出不一致：expected={node['output_contract']}")
        if producer != node["role_id"]:
            raise ValueError(f"contract producer 与节点角色不一致：expected={node['role_id']}")

        edges = database.list_dynamic_edges(plan_id)
        predecessor_ids = [edge["source_node_id"] for edge in edges if edge["target_node_id"] == node_id]
        expected_inputs: list[str] = []
        for predecessor_id in predecessor_ids:
            upstream = database.get_latest_contract_artifact(plan_id, predecessor_id)
            if upstream is None or upstream["status"] != "current":
                raise ValueError(f"input_artifact 上游尚未形成 current 契约：{predecessor_id}")
            expected_inputs.append(str(upstream["artifact_id"]))
        if set(input_artifact_ids) != set(expected_inputs):
            raise ValueError(
                "input_artifact_ids 与当前上游契约不一致："
                f"expected={sorted(expected_inputs)} actual={sorted(input_artifact_ids)}"
            )

        latest = database.get_latest_contract_artifact(plan_id, node_id)
        next_version = max(1, int((latest or {}).get("artifact_version") or 0) + 1)
        content_hash = "sha256:" + hashlib.sha256(encoded_content).hexdigest()
        if (
            latest
            and int(latest.get("artifact_version") or 0) >= 1
            and latest.get("content_hash") == content_hash
            and set(latest.get("input_artifact_ids") or []) == set(input_artifact_ids)
            and latest.get("status") == "current"
        ):
            return latest

        supersedes_artifact_id = ""
        if latest and int(latest.get("artifact_version") or 0) >= 1:
            supersedes_artifact_id = str(latest["artifact_id"])
            database.update_contract_artifact_status(supersedes_artifact_id, "superseded")
        artifact_id = f"artifact:{plan_id}:{node_id}:v{next_version}:{content_hash[-12:]}"
        database.add_contract_artifact(
            {
                "plan_id": plan_id,
                "task_id": int(plan["task_id"]),
                "node_id": node_id,
                "artifact_id": artifact_id,
                "artifact_version": next_version,
                "schema_name": schema_name,
                "schema_version": schema_version,
                "producer": producer,
                "input_artifact_ids": list(input_artifact_ids),
                "content_hash": content_hash,
                "status": "current",
                "payload": content,
                "supersedes_artifact_id": supersedes_artifact_id,
            }
        )
        database.update_dynamic_subtask_status(plan_id, node_id, "succeeded")
        stale_nodes: list[str] = []
        if supersedes_artifact_id:
            stale_nodes = mark_reachable_downstream_stale(plan_id, node_id, edges)
        database.add_dynamic_audit_event(
            {
                "task_id": int(plan["task_id"]),
                "plan_id": plan_id,
                "node_id": node_id,
                "action": "record_contract_version",
                "status": "current",
                "details": {
                    "artifact_id": artifact_id,
                    "artifact_version": next_version,
                    "schema_name": schema_name,
                    "content_hash": content_hash,
                    "stale_nodes": stale_nodes,
                },
            }
        )
        created = database.get_contract_artifact(artifact_id)
        if created is None:
            raise RuntimeError("contract artifact 写入失败")
        return created


def validate_dynamic_plan_payload(plan_payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan_payload, dict):
        raise ValueError("dynamic plan 必须是 JSON 对象")
    if plan_payload.get("schema_version") != DYNAMIC_PLANNING_SCHEMA_VERSION:
        raise ValueError("dynamic plan schema_version 不受支持")
    if plan_payload.get("planning_mode") != "dynamic-plan":
        raise ValueError("只支持 dynamic-plan 产物")
    if plan_payload.get("readonly") is not True or any(
        bool(plan_payload.get(key))
        for key in ("code_write_enabled", "database_access_enabled", "external_actions_enabled")
    ):
        raise ValueError("只允许登记只读、无代码/数据库/外部写入能力的动态计划")
    if plan_payload.get("status") == "disabled":
        raise ValueError("未启用的 dynamic plan 不能登记")
    graph = plan_payload.get("graph") or {}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    handoffs = plan_payload.get("handoffs") or []
    roles = {item.get("role_id") for item in (plan_payload.get("team") or {}).get("roles") or []}
    if not nodes or len({item.get("node_id") for item in nodes}) != len(nodes):
        raise ValueError("dynamic plan node 为空或重复")
    node_by_id = {str(item.get("node_id")): item for item in nodes}
    for node in nodes:
        if node.get("role_id") not in roles:
            raise ValueError(f"node role 不在动态团队中：{node.get('node_id')}")
    for edge in edges:
        if edge.get("source") not in node_by_id or edge.get("target") not in node_by_id:
            raise ValueError("dynamic plan edge 引用了未知节点")
    if graph_has_cycle(set(node_by_id), edges):
        raise ValueError("dynamic plan DAG 存在环依赖")
    handoff_by_node = {str(item.get("node_id")): item for item in handoffs}
    if set(handoff_by_node) != set(node_by_id):
        raise ValueError("handoff 与 dynamic node 不一致")
    for node_id, node in node_by_id.items():
        handoff = handoff_by_node[node_id]
        if handoff.get("producer") != node.get("role_id"):
            raise ValueError(f"handoff producer 与节点角色不一致：{node_id}")
        if handoff.get("schema_name") != node.get("output_contract"):
            raise ValueError(f"handoff schema 与节点输出不一致：{node_id}")
        if handoff.get("schema_version") not in SUPPORTED_CONTRACT_SCHEMA_VERSIONS:
            raise ValueError(f"handoff schema version 不受支持：{node_id}")
        if not str(handoff.get("content_hash") or "").startswith("sha256:"):
            raise ValueError(f"handoff content hash 无效：{node_id}")
    return json.loads(json.dumps(plan_payload, ensure_ascii=False))


def build_plan_hash(plan_payload: dict[str, Any]) -> str:
    normalized = json.loads(json.dumps(plan_payload, ensure_ascii=False))
    normalized.pop("generated_at", None)
    for handoff in normalized.get("handoffs") or []:
        handoff.pop("created_at", None)
    return "sha256:" + hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest()


def graph_has_cycle(node_ids: set[str], edges: list[dict[str, Any]]) -> bool:
    indegree = {node_id: 0 for node_id in node_ids}
    outgoing = {node_id: [] for node_id in node_ids}
    for edge in edges:
        source = str(edge["source"])
        target = str(edge["target"])
        outgoing[source].append(target)
        indegree[target] += 1
    ready = [node_id for node_id, count in indegree.items() if count == 0]
    visited = 0
    while ready:
        current = ready.pop()
        visited += 1
        for target in outgoing[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    return visited != len(node_ids)


def find_credential_field(payload: Any, *, path: str = "content") -> str:
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = str(key).strip().lower().replace("-", "_")
            current_path = f"{path}.{key}"
            if normalized in FORBIDDEN_CREDENTIAL_KEYS:
                return current_path
            nested = find_credential_field(value, path=current_path)
            if nested:
                return nested
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            nested = find_credential_field(value, path=f"{path}[{index}]")
            if nested:
                return nested
    return ""


def mark_reachable_downstream_stale(plan_id: int, node_id: str, edges: list[dict]) -> list[str]:
    outgoing: dict[str, list[str]] = {}
    for edge in edges:
        outgoing.setdefault(str(edge["source_node_id"]), []).append(str(edge["target_node_id"]))
    stale_nodes: list[str] = []
    seen: set[str] = set()
    stack = list(outgoing.get(node_id, []))
    while stack:
        current = stack.pop(0)
        if current in seen:
            continue
        seen.add(current)
        stale_nodes.append(current)
        latest = database.get_latest_contract_artifact(plan_id, current)
        if latest and latest.get("status") != "stale":
            database.update_contract_artifact_status(str(latest["artifact_id"]), "stale")
        database.update_dynamic_subtask_status(plan_id, current, "stale")
        stack.extend(outgoing.get(current, []))
    return stale_nodes


def build_recovery_preview(
    subtasks: list[dict],
    edges: list[dict],
    contracts_by_node: dict[str, dict],
) -> dict[str, Any]:
    predecessors: dict[str, set[str]] = {item["node_id"]: set() for item in subtasks}
    for edge in edges:
        predecessors.setdefault(str(edge["target_node_id"]), set()).add(str(edge["source_node_id"]))
    completed_nodes: list[str] = []
    stale_nodes: list[str] = []
    ready_nodes: list[str] = []
    blocked_nodes: list[str] = []
    human_gate_nodes: list[str] = []
    for subtask in subtasks:
        node_id = str(subtask["node_id"])
        contract = contracts_by_node.get(node_id) or {}
        status = str(contract.get("status") or "planned")
        if status == "current":
            completed_nodes.append(node_id)
            continue
        if status == "stale":
            stale_nodes.append(node_id)
            blocked_nodes.append(node_id)
            continue
        if subtask.get("human_confirmation_required"):
            human_gate_nodes.append(node_id)
            blocked_nodes.append(node_id)
            continue
        upstream_ready = all(
            (contracts_by_node.get(predecessor) or {}).get("status") == "current"
            for predecessor in predecessors.get(node_id, set())
        )
        if upstream_ready:
            ready_nodes.append(node_id)
        else:
            blocked_nodes.append(node_id)
    return {
        "readonly": True,
        "execution_enabled": False,
        "completed_nodes": completed_nodes,
        "ready_nodes": ready_nodes,
        "stale_nodes": stale_nodes,
        "blocked_nodes": blocked_nodes,
        "human_gate_nodes": human_gate_nodes,
        "next_action": "仅展示可恢复位置；真实节点执行仍未开放。",
    }


def write_dynamic_registry_outputs(output_dir: Path, snapshot: dict[str, Any]) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "dynamic_plan_registry.json"
    markdown_path = output_dir / "dynamic_plan_registry.md"
    recovery_path = output_dir / "dynamic_plan_recovery.json"
    json_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(dynamic_registry_to_markdown(snapshot), encoding="utf-8")
    recovery_path.write_text(
        json.dumps(snapshot.get("recovery_preview") or {}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return json_path, markdown_path, recovery_path


def dynamic_registry_to_markdown(snapshot: dict[str, Any]) -> str:
    plan = snapshot.get("plan") or {}
    task = snapshot.get("task") or {}
    recovery = snapshot.get("recovery_preview") or {}
    lines = [
        "# Task Manager 动态计划登记",
        "",
        f"- Task：{task.get('task_key') or '-'}",
        f"- Plan ID：{plan.get('id') or '-'}",
        f"- 复杂度：{plan.get('complexity_level') or '-'}",
        f"- 状态：{plan.get('status') or '-'}",
        "- 执行：关闭",
        "",
        "## 子任务",
        "",
    ]
    for item in snapshot.get("subtasks") or []:
        lines.append(
            f"- `{item.get('node_id')}` [{item.get('role_id')}] "
            f"status={item.get('status')} output={item.get('output_contract')}"
        )
    lines.extend(("", "## 只读恢复预览", ""))
    for key in ("completed_nodes", "ready_nodes", "stale_nodes", "blocked_nodes", "human_gate_nodes"):
        lines.append(f"- {key}: {', '.join(recovery.get(key) or []) or '-'}")
    lines.extend(
        (
            "",
            "## 边界",
            "",
            "- 本记录只登记动态计划、契约版本和恢复位置。",
            "- 不执行节点、不创建 worktree、不修改业务代码。",
            "- 不提交、不推送、不发布、不写云效或 TAPD。",
        )
    )
    return "\n".join(lines)


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
