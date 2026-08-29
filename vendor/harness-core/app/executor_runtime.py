from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app import database
from app.dynamic_plan_registry import MAX_CONTRACT_CONTENT_BYTES, find_credential_field
from app.node_runtime import (
    ControlledNodeRuntime,
    normalize_tools,
    parse_fixture_payload,
    sha256_json,
    validate_fixture_boundary,
)


SANDBOX_EXECUTOR_SCHEMA_VERSION = "1.0-sandbox-executor-runtime"
SANDBOX_ADAPTER_KIND = "sandbox_fixture_worker"
SANDBOX_WORKER_INPUT_SCHEMA = "1.0-sandbox-worker-input"
SANDBOX_WORKER_RESULT_SCHEMA = "1.0-sandbox-node-result"
MAX_LEASE_TTL_SECONDS = 300
MAX_ADAPTER_TIMEOUT_SECONDS = 5.0
PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXED_WORKER_PATH = PROJECT_ROOT / "tools" / "fixture_node_worker.py"
SAFE_WORKER_ERROR_CODES = {
    "fixture_worker_failure",
    "fixture_worker_behavior_invalid",
    "forbidden_environment_inherited",
    "worker_input_invalid",
}


class SandboxExecutorRuntime:
    def __init__(self) -> None:
        database.init_db()
        self.node_runtime = ControlledNodeRuntime()

    def issue_lease(
        self,
        context_id: int,
        *,
        capabilities: tuple[str, ...],
        ttl_seconds: int = 60,
    ) -> dict[str, Any]:
        if ttl_seconds < 1 or ttl_seconds > MAX_LEASE_TTL_SECONDS:
            raise ValueError(f"capability lease TTL 必须在 1-{MAX_LEASE_TTL_SECONDS} 秒之间")
        context = self.node_runtime.get_context(context_id)
        if not context["hash_valid"] or context["status"] != "current":
            raise ValueError("context 不完整或已失效，拒绝签发 capability lease")
        stale_reason = self.node_runtime.context_stale_reason(context_id)
        if stale_reason:
            raise ValueError(f"context 已过期，拒绝签发 capability lease：{stale_reason}")
        normalized = normalize_tools(capabilities)
        if not normalized or normalized != context["requested_tools"]:
            raise ValueError("capability 必须与 context 已裁决的 requested_tools 完全一致")
        if context["permission_status"] != "allowed":
            raise ValueError("context capability 存在拒绝项，不能签发 lease")

        lease_key = sha256_json(
            {
                "context_hash": context["envelope_hash"],
                "adapter_kind": SANDBOX_ADAPTER_KIND,
                "capabilities": normalized,
            }
        )
        existing = database.get_capability_lease_by_key(lease_key)
        if existing:
            view = self._lease_view(existing, idempotent=True)
            if view["status"] == "issued" and view["hash_valid"] and not view["expired"]:
                return view
            raise ValueError("当前 context 的 capability lease 已失效或已消费，不能自动补发")

        issued_at = datetime.now(timezone.utc)
        expires_at = issued_at + timedelta(seconds=ttl_seconds)
        policy = {
            "schema_version": SANDBOX_EXECUTOR_SCHEMA_VERSION,
            "fixture_only": True,
            "context_id": context_id,
            "context_hash": context["envelope_hash"],
            "schedule_id": context["schedule_id"],
            "plan_id": context["plan_id"],
            "node_id": context["node_id"],
            "checkpoint_hash": context["checkpoint_hash"],
            "adapter_kind": SANDBOX_ADAPTER_KIND,
            "capabilities": normalized,
            "issued_at": issued_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "max_uses": 1,
        }
        lease_id = database.add_capability_lease(
            {
                **policy,
                "lease_key": lease_key,
                "policy_hash": sha256_json(policy),
                "status": "issued",
            }
        )
        return self.get_lease(lease_id)

    def get_lease(self, lease_id: int) -> dict[str, Any]:
        lease = database.get_capability_lease(lease_id)
        if lease is None:
            raise ValueError(f"capability lease 不存在：{lease_id}")
        return self._lease_view(lease, idempotent=False)

    def execute(
        self,
        lease_id: int,
        *,
        fixture_root: Path,
        fixture_file: Path,
        timeout_seconds: float = 2.0,
    ) -> dict[str, Any]:
        if timeout_seconds <= 0 or timeout_seconds > MAX_ADAPTER_TIMEOUT_SECONDS:
            raise ValueError(
                f"sandbox adapter timeout 必须大于 0 且不超过 {MAX_ADAPTER_TIMEOUT_SECONDS:g} 秒"
            )
        lease = self.get_lease(lease_id)
        context = self.node_runtime.get_context(lease["context_id"])
        boundary = validate_fixture_boundary(fixture_root, fixture_file)
        if boundary["error_code"]:
            return self._record_execution(
                lease,
                context,
                status="blocked_fixture_boundary",
                error_code=boundary["error_code"],
                execution_key=self._blocked_key(lease, boundary["error_code"]),
            )
        fixture_bytes = boundary["fixture_file"].read_bytes()
        fixture_digest = "sha256:" + hashlib.sha256(fixture_bytes).hexdigest()
        execution_key = sha256_json(
            {"lease_key": lease["lease_key"], "fixture_digest": fixture_digest}
        )
        existing = database.get_dynamic_node_execution_by_key(execution_key)
        if existing:
            return self._execution_view(existing, idempotent=True)

        if not lease["hash_valid"]:
            return self._record_execution(
                lease,
                context,
                status="blocked_lease_integrity",
                error_code="lease_policy_hash_mismatch",
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
            )
        if lease["expired"]:
            database.update_capability_lease_status(lease_id, "expired")
            return self._record_execution(
                lease,
                context,
                status="blocked_lease_expired",
                error_code="lease_expired",
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
            )
        if lease["status"] != "issued" or lease["use_count"] >= lease["max_uses"]:
            return self._record_execution(
                lease,
                context,
                status="blocked_lease_consumed",
                error_code="lease_not_available",
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
            )
        stale_reason = self.node_runtime.context_stale_reason(context["id"])
        if stale_reason:
            database.update_capability_lease_status(lease_id, "revoked_stale_context")
            return self._record_execution(
                lease,
                context,
                status="blocked_stale_context",
                error_code=stale_reason,
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
            )

        fixture, fixture_error = parse_fixture_payload(fixture_bytes)
        if fixture_error:
            return self._record_execution(
                lease,
                context,
                status="blocked_adapter_protocol",
                error_code=fixture_error,
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
            )
        if fixture["context_hash"] != context["envelope_hash"]:
            return self._record_execution(
                lease,
                context,
                status="blocked_adapter_protocol",
                error_code="fixture_context_hash_mismatch",
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
            )
        if normalize_tools(tuple(fixture["requested_tools"])) != lease["capabilities"]:
            return self._record_execution(
                lease,
                context,
                status="blocked_adapter_protocol",
                error_code="fixture_capability_mismatch",
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
            )
        if not database.consume_capability_lease(lease_id):
            refreshed = self.get_lease(lease_id)
            return self._record_execution(
                refreshed,
                context,
                status="blocked_lease_consumed",
                error_code="lease_atomic_consume_failed",
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
            )

        worker_input = {
            "schema_version": SANDBOX_WORKER_INPUT_SCHEMA,
            "context_hash": context["envelope_hash"],
            "lease": {
                "policy_hash": lease["policy_hash"],
                "capabilities": lease["capabilities"],
            },
            "node": {
                "node_id": context["node_id"],
                "output_contract": context["envelope"]["node"]["output_contract"],
            },
            "fixture": fixture,
        }
        started_at = time.monotonic()
        try:
            process = subprocess.run(
                [sys.executable, str(FIXED_WORKER_PATH)],
                cwd=boundary["fixture_root"],
                input=json.dumps(worker_input, ensure_ascii=False),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout_seconds,
                check=False,
                shell=False,
                close_fds=True,
                env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
            )
        except subprocess.TimeoutExpired:
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            return self._record_execution(
                lease,
                context,
                status="blocked_adapter_timeout",
                error_code="adapter_timeout",
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
                runtime_details={"elapsed_ms": elapsed_ms, "timeout_seconds": timeout_seconds},
            )
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        if process.returncode != 0:
            return self._record_execution(
                lease,
                context,
                status="failed_adapter",
                error_code="worker_nonzero_exit",
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
                runtime_details={"elapsed_ms": elapsed_ms, "worker_exit_code": process.returncode},
            )
        worker_result, protocol_error = validate_worker_result(process.stdout)
        if protocol_error:
            return self._record_execution(
                lease,
                context,
                status="blocked_adapter_protocol",
                error_code=protocol_error,
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
                runtime_details={"elapsed_ms": elapsed_ms, "worker_exit_code": process.returncode},
            )
        usage = worker_result["usage"]
        runtime_details = {
            "adapter_protocol": SANDBOX_WORKER_RESULT_SCHEMA,
            "elapsed_ms": elapsed_ms,
            "worker_exit_code": process.returncode,
            "usage": usage,
        }
        if worker_result["status"] != "success":
            error_code = str(worker_result.get("error_code") or "worker_failed")
            if error_code not in SAFE_WORKER_ERROR_CODES:
                error_code = "worker_failed"
            return self._record_execution(
                lease,
                context,
                status="failed_adapter",
                error_code=error_code,
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
                runtime_details=runtime_details,
            )
        role_policy = context["envelope"]["role_policy"]
        if (
            usage["input_tokens"] > int(role_policy["input_budget_tokens"])
            or usage["output_tokens"] > int(role_policy["output_budget_tokens"])
            or elapsed_ms > int(role_policy["timeout_seconds"]) * 1000
        ):
            return self._record_execution(
                lease,
                context,
                status="blocked_adapter_budget",
                error_code="role_budget_exceeded",
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
                runtime_details=runtime_details,
            )

        content = worker_result["contract_content"]
        if find_credential_field(content):
            return self._record_execution(
                lease,
                context,
                status="blocked_adapter_protocol",
                error_code="credential_field_forbidden",
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
                runtime_details=runtime_details,
            )
        if "_harness_sandbox_evidence" in content:
            return self._record_execution(
                lease,
                context,
                status="blocked_adapter_protocol",
                error_code="reserved_sandbox_metadata",
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
                runtime_details=runtime_details,
            )
        candidate_content = {
            **content,
            "_harness_sandbox_evidence": {
                "fixture_only": True,
                "business_valid": False,
                "context_hash": context["envelope_hash"],
                "lease_policy_hash": lease["policy_hash"],
                "fixture_digest": fixture_digest,
                "adapter_kind": SANDBOX_ADAPTER_KIND,
            },
        }
        encoded = canonical_json(candidate_content).encode("utf-8")
        if len(encoded) > MAX_CONTRACT_CONTENT_BYTES:
            return self._record_execution(
                lease,
                context,
                status="blocked_adapter_protocol",
                error_code="contract_content_too_large",
                execution_key=execution_key,
                fixture_relpath=boundary["relative_path"],
                fixture_digest=fixture_digest,
                runtime_details=runtime_details,
            )
        candidate_hash = "sha256:" + hashlib.sha256(encoded).hexdigest()
        candidate = {
            "artifact_id": f"sandbox-fixture-candidate:{lease_id}:{candidate_hash[-12:]}",
            "schema_name": context["envelope"]["node"]["output_contract"],
            "schema_version": "1.0",
            "producer": context["envelope"]["node"]["role_id"],
            "input_artifact_ids": [
                item["artifact_id"] for item in context["envelope"]["upstream_artifacts"]
            ],
            "content_hash": candidate_hash,
            "status": "sandbox_fixture_contract_candidate",
            "payload": candidate_content,
        }
        return self._record_execution(
            lease,
            context,
            status="succeeded_sandbox_fixture",
            error_code="",
            execution_key=execution_key,
            fixture_relpath=boundary["relative_path"],
            fixture_digest=fixture_digest,
            candidate=candidate,
            runtime_details=runtime_details,
        )

    def get_execution(self, execution_id: int) -> dict[str, Any]:
        execution = database.get_dynamic_node_execution(execution_id)
        if execution is None:
            raise ValueError(f"sandbox node execution 不存在：{execution_id}")
        return self._execution_view(execution, idempotent=False)

    @staticmethod
    def _lease_view(lease: dict[str, Any], *, idempotent: bool) -> dict[str, Any]:
        policy = {
            "schema_version": SANDBOX_EXECUTOR_SCHEMA_VERSION,
            "fixture_only": True,
            "context_id": int(lease["context_id"]),
            "context_hash": lease["context_hash"],
            "schedule_id": int(lease["schedule_id"]),
            "plan_id": int(lease["plan_id"]),
            "node_id": lease["node_id"],
            "checkpoint_hash": lease["checkpoint_hash"],
            "adapter_kind": lease["adapter_kind"],
            "capabilities": lease.get("capabilities") or [],
            "issued_at": lease["issued_at"],
            "expires_at": lease["expires_at"],
            "max_uses": int(lease["max_uses"]),
        }
        try:
            expired = datetime.now(timezone.utc) >= datetime.fromisoformat(lease["expires_at"])
        except ValueError:
            expired = True
        return {
            "id": int(lease["id"]),
            "schema_version": SANDBOX_EXECUTOR_SCHEMA_VERSION,
            "context_id": int(lease["context_id"]),
            "schedule_id": int(lease["schedule_id"]),
            "plan_id": int(lease["plan_id"]),
            "node_id": lease["node_id"],
            "context_hash": lease["context_hash"],
            "checkpoint_hash": lease["checkpoint_hash"],
            "lease_key": lease["lease_key"],
            "adapter_kind": lease["adapter_kind"],
            "capabilities": lease.get("capabilities") or [],
            "policy_hash": lease["policy_hash"],
            "issued_at": lease["issued_at"],
            "expires_at": lease["expires_at"],
            "max_uses": int(lease["max_uses"]),
            "use_count": int(lease["use_count"]),
            "status": lease["status"],
            "expired": expired,
            "hash_valid": sha256_json(policy) == lease["policy_hash"],
            "idempotent": idempotent,
            "fixture_only": True,
            "external_credentials": False,
        }

    def _record_execution(
        self,
        lease: dict[str, Any],
        context: dict[str, Any],
        *,
        status: str,
        error_code: str,
        execution_key: str,
        fixture_relpath: str = "",
        fixture_digest: str = "",
        candidate: dict[str, Any] | None = None,
        runtime_details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = database.get_dynamic_node_execution_by_key(execution_key)
        if existing:
            return self._execution_view(existing, idempotent=True)
        candidate = candidate or {}
        execution_id = database.add_dynamic_node_execution(
            {
                "context_id": context["id"],
                "schedule_id": context["schedule_id"],
                "plan_id": context["plan_id"],
                "node_id": context["node_id"],
                "lease_id": lease["id"],
                "execution_key": execution_key,
                "executor_kind": SANDBOX_ADAPTER_KIND,
                "status": status,
                "fixture_relpath": fixture_relpath,
                "fixture_digest": fixture_digest,
                "requested_tools": lease["capabilities"],
                "tool_decisions": context["tool_decisions"],
                "candidate_schema": candidate.get("schema_name") or "",
                "candidate_hash": candidate.get("content_hash") or "",
                "candidate_payload": candidate,
                "error_code": error_code,
                "runtime_details": runtime_details or {},
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
            "lease_id": int(execution.get("lease_id") or 0),
            "context_id": int(execution["context_id"]),
            "schedule_id": int(execution["schedule_id"]),
            "plan_id": int(execution["plan_id"]),
            "node_id": execution["node_id"],
            "executor_kind": execution["executor_kind"],
            "execution_key": execution["execution_key"],
            "status": execution["status"],
            "fixture_relpath": execution["fixture_relpath"],
            "fixture_digest": execution["fixture_digest"],
            "error_code": execution["error_code"],
            "runtime_details": execution.get("runtime_details") or {},
            "sandbox_fixture_contract_candidate": candidate,
            "candidate_hash_valid": candidate_hash_valid,
            "idempotent": idempotent,
            "fixture_only": True,
            "business_valid": False,
            "promotion_enabled": False,
            "external_actions_enabled": False,
            "created_at": execution["created_at"],
        }

    @staticmethod
    def _blocked_key(lease: dict[str, Any], error_code: str) -> str:
        return sha256_json(
            {"lease_key": lease["lease_key"], "blocked_before_fixture": error_code}
        )


def validate_worker_result(stdout: str) -> tuple[dict[str, Any], str]:
    if len(stdout.encode("utf-8")) > MAX_CONTRACT_CONTENT_BYTES + 100_000:
        return {}, "worker_output_too_large"
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        return {}, "worker_output_not_json"
    if not isinstance(result, dict) or result.get("schema_version") != SANDBOX_WORKER_RESULT_SCHEMA:
        return {}, "worker_result_schema_invalid"
    if result.get("status") not in {"success", "failure"}:
        return {}, "worker_result_status_invalid"
    if not isinstance(result.get("contract_content"), dict):
        return {}, "worker_contract_content_invalid"
    usage = result.get("usage")
    if not isinstance(usage, dict):
        return {}, "worker_usage_invalid"
    normalized_usage: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens"):
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return {}, "worker_usage_invalid"
        normalized_usage[key] = value
    result["usage"] = normalized_usage
    return result, ""


def write_executor_runtime_outputs(output_dir: Path, snapshot: dict[str, Any]) -> tuple[Path, ...]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if "lease" in snapshot:
        lease = snapshot["lease"]
        json_path = output_dir / "fixture_capability_lease.json"
        markdown_path = output_dir / "fixture_capability_lease.md"
        json_path.write_text(json.dumps(lease, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(lease_to_markdown(lease), encoding="utf-8")
        return json_path, markdown_path
    execution = snapshot["execution"]
    json_path = output_dir / "sandbox_node_execution.json"
    markdown_path = output_dir / "sandbox_node_execution.md"
    candidate_path = output_dir / "sandbox_fixture_contract_candidate.json"
    json_path.write_text(json.dumps(execution, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(execution_to_markdown(execution), encoding="utf-8")
    candidate_path.write_text(
        json.dumps(
            execution.get("sandbox_fixture_contract_candidate") or {},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return json_path, markdown_path, candidate_path


def lease_to_markdown(lease: dict[str, Any]) -> str:
    return "\n".join(
        (
            "# Fixture Capability Lease",
            "",
            f"- Lease ID：{lease.get('id')}",
            f"- Status：{lease.get('status')}",
            f"- Adapter：{lease.get('adapter_kind')}",
            f"- Capabilities：{', '.join(lease.get('capabilities') or [])}",
            f"- Uses：{lease.get('use_count')}/{lease.get('max_uses')}",
            f"- Hash valid：{lease.get('hash_valid')}",
            "- sandbox_fixture：true",
            "",
            "本 lease 仅用于 Harness 固定 fixture worker，不代表业务授权，不代表业务完成。",
        )
    )


def execution_to_markdown(execution: dict[str, Any]) -> str:
    return "\n".join(
        (
            "# Sandbox Fixture Node Execution",
            "",
            f"- Execution ID：{execution.get('id')}",
            f"- Lease ID：{execution.get('lease_id')}",
            f"- Status：{execution.get('status')}",
            f"- Candidate hash valid：{execution.get('candidate_hash_valid')}",
            "- sandbox_fixture：true",
            "- business_valid：false",
            "- promotion_enabled：false",
            "",
            "固定 worker 的结果不代表真实模型执行、代码修改或测试通过，也不代表业务完成。",
        )
    )


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
