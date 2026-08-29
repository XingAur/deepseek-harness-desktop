from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app import database
from app.dynamic_plan_registry import (
    MAX_CONTRACT_CONTENT_BYTES,
    find_credential_field,
)
from app.dynamic_scheduler import DynamicDryRunScheduler


CONTROLLED_NODE_RUNTIME_SCHEMA_VERSION = "1.0-controlled-node-runtime"
FIXTURE_INPUT_SCHEMA_VERSION = "1.0-fixture-node-input"
FIXTURE_ROOT_MARKER = ".harness-fixture-root.json"
MAX_FIXTURE_BYTES = 2_000_000
FIXTURE_EXECUTOR_TOOLS = {"read_artifacts"}
GLOBAL_DENIED_TOOLS = {
    "database_execute",
    "deploy",
    "external_write",
    "git_push",
    "model_execute",
    "shell_execute",
    "worktree_edit",
}


class ControlledNodeRuntime:
    def __init__(self) -> None:
        database.init_db()
        self.scheduler = DynamicDryRunScheduler()

    def prepare_context(
        self,
        schedule_id: int,
        node_id: str,
        *,
        requested_tools: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        snapshot = self.scheduler.get_schedule(schedule_id)
        if not (snapshot.get("checkpoint") or {}).get("hash_valid"):
            raise ValueError("dynamic schedule checkpoint 校验失败，拒绝准备节点上下文")
        schedule = snapshot["schedule"]
        if schedule.get("mode") != "dry_run":
            raise ValueError("受控节点上下文只支持 dry_run schedule")
        node_states = {str(item["node_id"]): item for item in snapshot["node_states"]}
        node_state = node_states.get(node_id)
        if node_state is None:
            raise ValueError(f"schedule node 不存在：{node_id}")
        if node_state["state"] != "running_simulated":
            raise ValueError(
                "只有 running_simulated 节点可以准备 fixture 上下文："
                f"{node_state['state']}"
            )

        plan_id = int(schedule["plan_id"])
        plan = database.get_dynamic_plan(plan_id)
        if plan is None:
            raise ValueError(f"dynamic plan 不存在：{plan_id}")
        subtask = next(
            (item for item in database.list_dynamic_subtasks(plan_id) if item["node_id"] == node_id),
            None,
        )
        if subtask is None:
            raise ValueError(f"dynamic subtask 不存在：{node_id}")
        role = next(
            (
                item
                for item in ((plan.get("plan_payload") or {}).get("team") or {}).get("roles") or []
                if item.get("role_id") == subtask["role_id"]
            ),
            None,
        )
        if role is None:
            raise ValueError(f"dynamic role 不存在：{subtask['role_id']}")

        normalized_tools = normalize_tools(requested_tools)
        decisions = adjudicate_tools(role, normalized_tools)
        upstream_refs = self._resolve_upstream_refs(
            plan_id,
            node_id,
            schedule_id=schedule_id,
        )
        checkpoint_hash = str(snapshot["checkpoint"]["checkpoint_hash"])
        envelope = {
            "schema_version": CONTROLLED_NODE_RUNTIME_SCHEMA_VERSION,
            "fixture_only": True,
            "execution_enabled": False,
            "schedule_id": schedule_id,
            "plan_id": plan_id,
            "plan_hash": plan["plan_hash"],
            "checkpoint_hash": checkpoint_hash,
            "node_id": node_id,
            "node_state": node_state["state"],
            "node": {
                "title": subtask["title"],
                "node_kind": subtask["node_kind"],
                "role_id": subtask["role_id"],
                "output_contract": subtask["output_contract"],
                "allowed_paths": subtask.get("allowed_paths") or [],
                "completion_criteria": (subtask.get("metadata") or {}).get("completion_criteria") or [],
            },
            "role_policy": {
                "allowed_tools": role.get("allowed_tools") or [],
                "forbidden_tools": role.get("forbidden_tools") or [],
                "input_budget_tokens": int(role.get("input_budget_tokens") or 0),
                "output_budget_tokens": int(role.get("output_budget_tokens") or 0),
                "timeout_seconds": int(role.get("timeout_seconds") or 0),
                "max_retries": int(role.get("max_retries") or 0),
            },
            "requested_tools": normalized_tools,
            "tool_decisions": decisions,
            "upstream_artifacts": upstream_refs,
            "boundaries": [
                "只允许解析显式 fixture JSON。",
                "不执行模型、shell、worktree、数据库、Git 或外部系统动作。",
                "fixture 候选契约不代表业务完成，也不会自动晋升为 current。",
            ],
        }
        envelope_hash = sha256_json(envelope)
        existing = database.get_dynamic_context_envelope_by_hash(envelope_hash)
        if existing:
            return self._context_view(existing)
        context_id = database.add_dynamic_context_envelope(
            {
                "schedule_id": schedule_id,
                "plan_id": plan_id,
                "node_id": node_id,
                "role_id": subtask["role_id"],
                "checkpoint_hash": checkpoint_hash,
                "plan_hash": plan["plan_hash"],
                "envelope_hash": envelope_hash,
                "status": "current",
                "requested_tools": normalized_tools,
                "tool_decisions": decisions,
                "payload": envelope,
            }
        )
        return self.get_context(context_id)

    def get_context(self, context_id: int) -> dict[str, Any]:
        context = database.get_dynamic_context_envelope(context_id)
        if context is None:
            raise ValueError(f"dynamic node context 不存在：{context_id}")
        return self._context_view(context)

    def execute_fixture(
        self,
        context_id: int,
        *,
        fixture_root: Path,
        fixture_file: Path,
    ) -> dict[str, Any]:
        context = self.get_context(context_id)
        if not context["hash_valid"]:
            return self._record_blocked(
                context,
                status="blocked_context_integrity",
                error_code="context_hash_mismatch",
            )
        stale_reason = self._context_stale_reason(context)
        if stale_reason:
            database.update_dynamic_context_envelope_status(context_id, "stale")
            context = self.get_context(context_id)
            return self._record_blocked(
                context,
                status="blocked_stale_context",
                error_code=stale_reason,
            )

        boundary = validate_fixture_boundary(fixture_root, fixture_file)
        if boundary["error_code"]:
            return self._record_blocked(
                context,
                status="blocked_fixture_boundary",
                error_code=boundary["error_code"],
            )
        resolved_file = boundary["fixture_file"]
        fixture_bytes = resolved_file.read_bytes()
        fixture_digest = "sha256:" + hashlib.sha256(fixture_bytes).hexdigest()
        execution_key = build_execution_key(context["envelope_hash"], fixture_digest)
        existing = database.get_dynamic_node_execution_by_key(execution_key)
        if existing:
            return self._execution_view(existing, idempotent=True)

        fixture, error_code = parse_fixture_payload(fixture_bytes)
        if error_code:
            return self._record_blocked(
                context,
                status="blocked_fixture_content",
                error_code=error_code,
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
            )
        if fixture["context_hash"] != context["envelope_hash"]:
            return self._record_blocked(
                context,
                status="blocked_fixture_content",
                error_code="fixture_context_hash_mismatch",
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
            )
        fixture_tools = normalize_tools(tuple(fixture["requested_tools"]))
        if fixture_tools != context["requested_tools"]:
            return self._record_blocked(
                context,
                status="blocked_fixture_content",
                error_code="fixture_requested_tools_mismatch",
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
            )
        if context["permission_status"] != "allowed":
            return self._record_blocked(
                context,
                status="blocked_policy",
                error_code="requested_tool_denied",
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
            )

        content = fixture["contract_content"]
        credential_path = find_credential_field(content)
        if credential_path:
            return self._record_blocked(
                context,
                status="blocked_fixture_content",
                error_code="credential_field_forbidden",
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
            )
        if "_harness_fixture_evidence" in content:
            return self._record_blocked(
                context,
                status="blocked_fixture_content",
                error_code="reserved_fixture_metadata",
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
            )
        candidate_content = {
            **content,
            "_harness_fixture_evidence": {
                "fixture_only": True,
                "business_valid": False,
                "context_hash": context["envelope_hash"],
                "fixture_digest": fixture_digest,
            },
        }
        if len(canonical_json(candidate_content).encode("utf-8")) > MAX_CONTRACT_CONTENT_BYTES:
            return self._record_blocked(
                context,
                status="blocked_fixture_content",
                error_code="contract_content_too_large",
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
            )
        candidate_hash = sha256_json(candidate_content)
        envelope = context["envelope"]
        candidate = {
            "artifact_id": f"fixture-candidate:{context_id}:{candidate_hash[-12:]}",
            "schema_name": envelope["node"]["output_contract"],
            "schema_version": "1.0",
            "producer": envelope["node"]["role_id"],
            "input_artifact_ids": [item["artifact_id"] for item in envelope["upstream_artifacts"]],
            "content_hash": candidate_hash,
            "status": "fixture_contract_candidate",
            "payload": candidate_content,
        }
        execution_id = database.add_dynamic_node_execution(
            {
                "context_id": context_id,
                "schedule_id": context["schedule_id"],
                "plan_id": context["plan_id"],
                "node_id": context["node_id"],
                "execution_key": execution_key,
                "status": "succeeded_fixture",
                "fixture_relpath": boundary["relative_path"],
                "fixture_digest": fixture_digest,
                "requested_tools": context["requested_tools"],
                "tool_decisions": context["tool_decisions"],
                "candidate_schema": candidate["schema_name"],
                "candidate_hash": candidate_hash,
                "candidate_payload": candidate,
            }
        )
        return self.get_execution(execution_id)

    def get_execution(self, execution_id: int) -> dict[str, Any]:
        execution = database.get_dynamic_node_execution(execution_id)
        if execution is None:
            raise ValueError(f"fixture node execution 不存在：{execution_id}")
        return self._execution_view(execution, idempotent=False)

    def context_stale_reason(self, context_id: int) -> str:
        context = self.get_context(context_id)
        if not context["hash_valid"]:
            return "context_hash_mismatch"
        return self._context_stale_reason(context)

    def _resolve_upstream_refs(
        self,
        plan_id: int,
        node_id: str,
        *,
        schedule_id: int,
    ) -> list[dict[str, Any]]:
        edges = database.list_dynamic_edges(plan_id)
        refs: list[dict[str, Any]] = []
        for edge in edges:
            if edge["target_node_id"] != node_id:
                continue
            source = str(edge["source_node_id"])
            contract = database.get_latest_contract_artifact(plan_id, source)
            if contract and contract.get("status") == "current":
                refs.append(
                    {
                        "source_node_id": source,
                        "artifact_id": contract["artifact_id"],
                        "schema_name": contract["schema_name"],
                        "content_hash": contract["content_hash"],
                        "evidence_kind": "current_contract",
                    }
                )
                continue
            fixture_execution = database.get_latest_successful_node_execution(
                plan_id,
                source,
                schedule_id=schedule_id,
            )
            candidate = (fixture_execution or {}).get("candidate_payload") or {}
            if candidate:
                refs.append(
                    {
                        "source_node_id": source,
                        "artifact_id": candidate["artifact_id"],
                        "schema_name": candidate["schema_name"],
                        "content_hash": candidate["content_hash"],
                        "evidence_kind": "fixture_contract_candidate",
                    }
                )
                continue
            model_invocation = database.get_latest_successful_model_invocation(
                plan_id,
                source,
                schedule_id=schedule_id,
            )
            model_candidate = (model_invocation or {}).get("candidate_payload") or {}
            if model_candidate:
                refs.append(
                    {
                        "source_node_id": source,
                        "artifact_id": model_candidate["artifact_id"],
                        "schema_name": model_candidate["schema_name"],
                        "content_hash": model_candidate["content_hash"],
                        "evidence_kind": "fixture_model_candidate",
                    }
                )
                continue
            raise ValueError(f"上游节点尚无可用契约或 fixture 候选：{source}")
        return refs

    def _context_stale_reason(self, context: dict[str, Any]) -> str:
        snapshot = self.scheduler.get_schedule(int(context["schedule_id"]))
        checkpoint = snapshot.get("checkpoint") or {}
        if not checkpoint.get("hash_valid"):
            return "schedule_checkpoint_invalid"
        if checkpoint.get("checkpoint_hash") != context["checkpoint_hash"]:
            return "schedule_checkpoint_changed"
        node = next(
            (item for item in snapshot["node_states"] if item["node_id"] == context["node_id"]),
            None,
        )
        if node is None or node.get("state") != "running_simulated":
            return "schedule_node_state_changed"
        return ""

    @staticmethod
    def _context_view(context: dict[str, Any]) -> dict[str, Any]:
        envelope = context.get("payload") or {}
        decisions = context.get("tool_decisions") or []
        return {
            "id": int(context["id"]),
            "schema_version": CONTROLLED_NODE_RUNTIME_SCHEMA_VERSION,
            "schedule_id": int(context["schedule_id"]),
            "plan_id": int(context["plan_id"]),
            "node_id": context["node_id"],
            "role_id": context["role_id"],
            "checkpoint_hash": context["checkpoint_hash"],
            "plan_hash": context["plan_hash"],
            "envelope_hash": context["envelope_hash"],
            "status": context["status"],
            "requested_tools": context.get("requested_tools") or [],
            "tool_decisions": decisions,
            "permission_status": (
                "allowed"
                if all(item.get("decision") == "allowed" for item in decisions)
                else "denied"
            ),
            "envelope": envelope,
            "hash_valid": sha256_json(envelope) == context["envelope_hash"],
            "fixture_only": True,
            "execution_enabled": False,
            "created_at": context["created_at"],
        }

    def _record_blocked(
        self,
        context: dict[str, Any],
        *,
        status: str,
        error_code: str,
        execution_key: str = "",
        fixture_relpath: str = "",
        fixture_digest: str = "",
    ) -> dict[str, Any]:
        resolved_key = execution_key or sha256_json(
            {
                "context_hash": context["envelope_hash"],
                "status": status,
                "error_code": error_code,
                "checkpoint_hash": context.get("checkpoint_hash") or "",
            }
        )
        existing = database.get_dynamic_node_execution_by_key(resolved_key)
        if existing:
            return self._execution_view(existing, idempotent=True)
        execution_id = database.add_dynamic_node_execution(
            {
                "context_id": context["id"],
                "schedule_id": context["schedule_id"],
                "plan_id": context["plan_id"],
                "node_id": context["node_id"],
                "execution_key": resolved_key,
                "status": status,
                "fixture_relpath": fixture_relpath,
                "fixture_digest": fixture_digest,
                "requested_tools": context.get("requested_tools") or [],
                "tool_decisions": context.get("tool_decisions") or [],
                "error_code": error_code,
            }
        )
        return self.get_execution(execution_id)

    @staticmethod
    def _execution_view(execution: dict[str, Any], *, idempotent: bool) -> dict[str, Any]:
        candidate = execution.get("candidate_payload") or {}
        candidate_hash_valid = True
        if candidate:
            candidate_hash_valid = sha256_json(candidate.get("payload") or {}) == execution.get(
                "candidate_hash"
            )
        return {
            "id": int(execution["id"]),
            "context_id": int(execution["context_id"]),
            "schedule_id": int(execution["schedule_id"]),
            "plan_id": int(execution["plan_id"]),
            "node_id": execution["node_id"],
            "execution_key": execution["execution_key"],
            "executor_kind": execution["executor_kind"],
            "status": execution["status"],
            "fixture_relpath": execution["fixture_relpath"],
            "fixture_digest": execution["fixture_digest"],
            "requested_tools": execution.get("requested_tools") or [],
            "tool_decisions": execution.get("tool_decisions") or [],
            "candidate_schema": execution["candidate_schema"],
            "candidate_hash": execution["candidate_hash"],
            "fixture_contract_candidate": candidate,
            "candidate_hash_valid": candidate_hash_valid,
            "error_code": execution["error_code"],
            "idempotent": idempotent,
            "fixture_only": True,
            "business_valid": False,
            "promotion_enabled": False,
            "created_at": execution["created_at"],
            "boundaries": [
                "fixture 执行只验证节点协议，不代表真实智能体或业务执行。",
                "候选契约不会登记为 current，也不会推进 dry-run schedule。",
            ],
        }


def adjudicate_tools(role: dict[str, Any], requested_tools: list[str]) -> list[dict[str, str]]:
    allowed = set(role.get("allowed_tools") or [])
    forbidden = set(role.get("forbidden_tools") or [])
    decisions: list[dict[str, str]] = []
    for tool in requested_tools:
        if tool in GLOBAL_DENIED_TOOLS:
            decision, reason = "denied", "global_hard_guard"
        elif tool in forbidden:
            decision, reason = "denied", "role_forbidden"
        elif tool not in allowed:
            decision, reason = "denied", "not_role_allowed"
        elif tool not in FIXTURE_EXECUTOR_TOOLS:
            decision, reason = "denied", "executor_unsupported"
        else:
            decision, reason = "allowed", "role_and_executor_allowed"
        decisions.append({"tool": tool, "decision": decision, "reason": reason})
    return decisions


def normalize_tools(tools: tuple[str, ...]) -> list[str]:
    normalized = {str(tool).strip() for tool in tools if str(tool).strip()}
    return sorted(normalized)


def validate_fixture_boundary(fixture_root: Path, fixture_file: Path) -> dict[str, Any]:
    try:
        root = Path(fixture_root).expanduser().resolve(strict=True)
    except (FileNotFoundError, OSError):
        return fixture_boundary_error("fixture_root_missing")
    if not root.is_dir():
        return fixture_boundary_error("fixture_root_not_directory")
    marker_path = root / FIXTURE_ROOT_MARKER
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return fixture_boundary_error("missing_or_invalid_fixture_marker")
    if marker.get("fixture_only") is not True or marker.get("schema_version") != "1.0":
        return fixture_boundary_error("invalid_fixture_marker")
    if any((candidate / ".git").exists() for candidate in (root, *root.parents)):
        return fixture_boundary_error("fixture_root_inside_git_repository")
    try:
        resolved_file = Path(fixture_file).expanduser().resolve(strict=True)
        relative_path = resolved_file.relative_to(root)
    except (FileNotFoundError, OSError, ValueError):
        return fixture_boundary_error("fixture_path_escape_or_missing")
    if not resolved_file.is_file():
        return fixture_boundary_error("fixture_file_not_regular")
    if resolved_file.stat().st_size > MAX_FIXTURE_BYTES:
        return fixture_boundary_error("fixture_file_too_large")
    return {
        "fixture_root": root,
        "fixture_file": resolved_file,
        "relative_path": relative_path.as_posix(),
        "error_code": "",
    }


def fixture_boundary_error(error_code: str) -> dict[str, Any]:
    return {
        "fixture_root": None,
        "fixture_file": None,
        "relative_path": "",
        "error_code": error_code,
    }


def parse_fixture_payload(fixture_bytes: bytes) -> tuple[dict[str, Any], str]:
    try:
        fixture = json.loads(fixture_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}, "fixture_json_invalid"
    if not isinstance(fixture, dict):
        return {}, "fixture_must_be_object"
    if fixture.get("schema_version") != FIXTURE_INPUT_SCHEMA_VERSION:
        return {}, "fixture_schema_invalid"
    if fixture.get("fixture_only") is not True:
        return {}, "fixture_only_flag_required"
    if not str(fixture.get("context_hash") or "").startswith("sha256:"):
        return {}, "fixture_context_hash_invalid"
    if not isinstance(fixture.get("requested_tools"), list):
        return {}, "fixture_requested_tools_invalid"
    if not isinstance(fixture.get("contract_content"), dict):
        return {}, "fixture_contract_content_invalid"
    return fixture, ""


def build_execution_key(context_hash: str, fixture_digest: str) -> str:
    return sha256_json({"context_hash": context_hash, "fixture_digest": fixture_digest})


def write_node_runtime_outputs(output_dir: Path, snapshot: dict[str, Any]) -> tuple[Path, ...]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if "context" in snapshot:
        context = snapshot["context"]
        json_path = output_dir / "dynamic_node_context.json"
        markdown_path = output_dir / "dynamic_node_context.md"
        json_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(context_to_markdown(context), encoding="utf-8")
        return json_path, markdown_path
    execution = snapshot["execution"]
    json_path = output_dir / "fixture_node_execution.json"
    markdown_path = output_dir / "fixture_node_execution.md"
    candidate_path = output_dir / "fixture_contract_candidate.json"
    json_path.write_text(json.dumps(execution, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(execution_to_markdown(execution), encoding="utf-8")
    candidate_path.write_text(
        json.dumps(execution.get("fixture_contract_candidate") or {}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return json_path, markdown_path, candidate_path


def context_to_markdown(context: dict[str, Any]) -> str:
    return "\n".join(
        (
            "# 动态节点 Fixture 上下文",
            "",
            f"- Context ID：{context.get('id')}",
            f"- Node：{context.get('node_id')}",
            f"- Envelope hash：{context.get('envelope_hash')}",
            f"- Hash valid：{context.get('hash_valid')}",
            f"- Permission：{context.get('permission_status')}",
            "- fixture_only：true",
            "- 真实执行：关闭",
            "",
            "本上下文只用于脱敏 fixture 协议验证，不代表业务完成。",
        )
    )


def execution_to_markdown(execution: dict[str, Any]) -> str:
    return "\n".join(
        (
            "# Fixture 节点执行",
            "",
            f"- Execution ID：{execution.get('id')}",
            f"- Node：{execution.get('node_id')}",
            f"- Status：{execution.get('status')}",
            f"- Candidate hash valid：{execution.get('candidate_hash_valid')}",
            "- fixture_only：true",
            "- business_valid：false",
            "- promotion_enabled：false",
            "",
            "本结果不代表真实智能体执行、代码修改、测试通过或业务完成。",
        )
    )


def sha256_json(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
