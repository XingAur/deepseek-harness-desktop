from __future__ import annotations

import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import database
from app.dynamic_scheduler import DynamicDryRunScheduler
from app.executor_runtime import SandboxExecutorRuntime
from app.node_runtime import ControlledNodeRuntime, FIXTURE_ROOT_MARKER, sha256_json


MOCK_AGENT_RUNTIME_SCHEMA_VERSION = "1.0-deterministic-mock-agent-runtime"
MOCK_AGENT_ADAPTER_KIND = "deterministic_mock_agent"
MAX_MOCK_PARALLEL = 8
ALLOWED_BEHAVIORS = {"success", "failure", "protocol_error", "timeout"}


class DeterministicMockAgentRuntime:
    def __init__(self) -> None:
        database.init_db()
        self.scheduler = DynamicDryRunScheduler()
        self.node_runtime = ControlledNodeRuntime()
        self.executor = SandboxExecutorRuntime()

    def run(
        self,
        schedule_id: int,
        *,
        fixture_root: Path,
        max_parallel: int = 2,
        behavior_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if max_parallel < 1 or max_parallel > MAX_MOCK_PARALLEL:
            raise ValueError(f"max_parallel 必须在 1-{MAX_MOCK_PARALLEL} 之间")
        root = validate_mock_fixture_root(fixture_root)
        overrides = validate_behavior_overrides(behavior_overrides or {})
        existing = database.get_mock_agent_run_by_schedule(schedule_id)
        if existing:
            return self._snapshot(int(existing["id"]), idempotent=True)

        schedule = self.scheduler.get_schedule(schedule_id)
        if schedule["schedule"].get("mode") != "dry_run":
            raise ValueError("deterministic mock-agent 只支持 dry_run schedule")
        if not (schedule.get("checkpoint") or {}).get("hash_valid"):
            raise ValueError("dynamic schedule checkpoint 校验失败")
        plan_id = int(schedule["schedule"]["plan_id"])
        known_node_ids = {str(item["node_id"]) for item in schedule["node_states"]}
        unknown_overrides = sorted(set(overrides) - known_node_ids)
        if unknown_overrides:
            raise ValueError(
                "behavior override 包含未知节点：" + ", ".join(unknown_overrides)
            )
        run_id = database.add_mock_agent_run(
            {
                "schedule_id": schedule_id,
                "plan_id": plan_id,
                "run_key": sha256_json(
                    {
                        "schema_version": MOCK_AGENT_RUNTIME_SCHEMA_VERSION,
                        "schedule_id": schedule_id,
                        "adapter_kind": MOCK_AGENT_ADAPTER_KIND,
                    }
                ),
                "adapter_kind": MOCK_AGENT_ADAPTER_KIND,
                "status": "running",
                "max_parallel": max_parallel,
                "started_at": now_iso(),
            }
        )

        wave_index = 0
        final_status = "blocked_fixture"
        failure_codes: list[str] = []
        try:
            while True:
                schedule = self.scheduler.get_schedule(schedule_id)
                running_nodes = sorted(
                    (
                        item
                        for item in schedule["node_states"]
                        if item["state"] == "running_simulated"
                    ),
                    key=lambda item: str(item["node_id"]),
                )
                if not running_nodes:
                    schedule_status = str(schedule["schedule"]["status"])
                    if schedule_status == "completed_simulated":
                        final_status = "completed_fixture"
                    elif schedule_status == "paused_human":
                        final_status = "paused_human"
                    else:
                        final_status = "blocked_fixture"
                    break

                wave_index += 1
                prepared = [
                    self._prepare_node(
                        schedule_id,
                        plan_id,
                        wave_index,
                        node,
                        root,
                        overrides.get(str(node["node_id"]), "success"),
                    )
                    for node in running_nodes
                ]
                results = self._execute_wave(prepared, max_parallel=max_parallel)
                wave_failed = False
                for item in sorted(results, key=lambda value: value["node_id"]):
                    execution = item["execution"]
                    usage = (execution.get("runtime_details") or {}).get("usage") or {}
                    candidate = execution.get("sandbox_fixture_contract_candidate") or {}
                    trace_id = sha256_json(
                        {
                            "run_id": run_id,
                            "wave_index": wave_index,
                            "node_id": item["node_id"],
                            "execution_id": execution["id"],
                        }
                    )
                    database.add_mock_agent_trace(
                        {
                            "run_id": run_id,
                            "schedule_id": schedule_id,
                            "plan_id": plan_id,
                            "wave_index": wave_index,
                            "trace_id": trace_id,
                            "node_id": item["node_id"],
                            "role_id": item["context"]["role_id"],
                            "context_id": item["context"]["id"],
                            "lease_id": item["lease"]["id"],
                            "execution_id": execution["id"],
                            "status": execution["status"],
                            "error_code": execution.get("error_code") or "",
                            "candidate_hash": candidate.get("content_hash") or "",
                            "input_artifact_ids": [
                                ref["artifact_id"]
                                for ref in item["context"]["envelope"]["upstream_artifacts"]
                            ],
                            "input_tokens": int(usage.get("input_tokens") or 0),
                            "output_tokens": int(usage.get("output_tokens") or 0),
                            "elapsed_ms": int(
                                (execution.get("runtime_details") or {}).get("elapsed_ms")
                                or item["elapsed_ms"]
                            ),
                            "observed_concurrency": item["observed_concurrency"],
                            "parallel_observed": item["observed_concurrency"] > 1,
                            "started_at": item["started_at"],
                            "finished_at": item["finished_at"],
                            "details": {
                                "fixture_only": True,
                                "adapter_kind": MOCK_AGENT_ADAPTER_KIND,
                                "behavior": item["behavior"],
                                "checkpoint_hash": item["context"]["checkpoint_hash"],
                            },
                        }
                    )
                    outcome = execution_outcome(execution["status"])
                    if outcome != "success":
                        wave_failed = True
                        failure_codes.append(execution.get("error_code") or execution["status"])
                    elapsed_seconds = int(
                        math.ceil(
                            int((execution.get("runtime_details") or {}).get("elapsed_ms") or 0)
                            / 1000
                        )
                    )
                    self.scheduler.advance(
                        schedule_id,
                        {
                            "event_id": f"mock-agent:{run_id}:{execution['id']}",
                            "node_id": item["node_id"],
                            "outcome": outcome,
                            "elapsed_seconds": elapsed_seconds,
                            "input_tokens": int(usage.get("input_tokens") or 0),
                            "output_tokens": int(usage.get("output_tokens") or 0),
                        },
                    )
                if wave_failed:
                    final_status = "failed_fixture"
                    break
        except Exception:
            database.update_mock_agent_run(
                run_id,
                status="blocked_fixture",
                completed_at=now_iso(),
                summary={"fixture_only": True, "reason": "orchestration_exception"},
            )
            raise

        traces = database.list_mock_agent_traces(run_id)
        database.update_mock_agent_run(
            run_id,
            status=final_status,
            completed_at=now_iso(),
            summary={
                "fixture_only": True,
                "wave_count": max((int(item["wave_index"]) for item in traces), default=0),
                "node_count": len(traces),
                "failure_codes": sorted(set(failure_codes)),
            },
        )
        return self._snapshot(run_id, idempotent=False)

    def get_run(self, run_id: int) -> dict[str, Any]:
        return self._snapshot(run_id, idempotent=False)

    def _prepare_node(
        self,
        schedule_id: int,
        plan_id: int,
        wave_index: int,
        node: dict[str, Any],
        fixture_root: Path,
        behavior: str,
    ) -> dict[str, Any]:
        node_id = str(node["node_id"])
        context = self.node_runtime.prepare_context(
            schedule_id,
            node_id,
            requested_tools=("read_artifacts",),
        )
        lease = self.executor.issue_lease(
            context["id"],
            capabilities=("read_artifacts",),
            ttl_seconds=60,
        )
        plan = database.get_dynamic_plan(plan_id) or {}
        request = (plan.get("plan_payload") or {}).get("request") or {}
        contract_content = {
            "mock_agent": {
                "schema_version": MOCK_AGENT_RUNTIME_SCHEMA_VERSION,
                "adapter_kind": MOCK_AGENT_ADAPTER_KIND,
                "node_id": node_id,
                "role_id": context["role_id"],
                "node_kind": context["envelope"]["node"]["node_kind"],
                "output_contract": context["envelope"]["node"]["output_contract"],
                "result": "deterministic_fixture_candidate",
            },
            "requirement": {
                "requirement_id": str(request.get("requirement_id") or ""),
                "title": str(request.get("title") or ""),
            },
            "allowed_paths": context["envelope"]["node"]["allowed_paths"],
            "completion_criteria": context["envelope"]["node"]["completion_criteria"],
            "upstream_artifacts": context["envelope"]["upstream_artifacts"],
        }
        role_policy = context["envelope"]["role_policy"]
        input_tokens = bounded_token_estimate(
            context["envelope"], int(role_policy["input_budget_tokens"])
        )
        output_tokens = bounded_token_estimate(
            contract_content, int(role_policy["output_budget_tokens"])
        )
        worker_behavior = behavior
        sleep_seconds = 0.0
        if behavior == "success":
            worker_behavior = "sleep"
            sleep_seconds = 0.02
        elif behavior == "timeout":
            worker_behavior = "sleep"
            sleep_seconds = 2.0
        fixture_dir = fixture_root / f"mock-agent-schedule-{schedule_id}"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        fixture_file = fixture_dir / f"wave-{wave_index}-{node_id}-{context['id']}.json"
        fixture_file.write_text(
            json.dumps(
                {
                    "schema_version": "1.0-fixture-node-input",
                    "fixture_only": True,
                    "context_hash": context["envelope_hash"],
                    "requested_tools": ["read_artifacts"],
                    "contract_content": contract_content,
                    "worker_behavior": worker_behavior,
                    "sleep_seconds": sleep_seconds,
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return {
            "node_id": node_id,
            "behavior": behavior,
            "context": context,
            "lease": lease,
            "fixture_file": fixture_file,
            "fixture_root": fixture_root,
        }

    def _execute_wave(
        self,
        prepared: list[dict[str, Any]],
        *,
        max_parallel: int,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for offset in range(0, len(prepared), max_parallel):
            batch = prepared[offset : offset + max_parallel]
            barrier = threading.Barrier(len(batch)) if len(batch) > 1 else None
            lock = threading.Lock()
            active = 0

            def execute_one(item: dict[str, Any]) -> dict[str, Any]:
                nonlocal active
                if barrier:
                    barrier.wait()
                with lock:
                    active += 1
                    observed = active
                started_at = now_iso()
                started = time.monotonic()
                try:
                    execution = self.executor.execute(
                        item["lease"]["id"],
                        fixture_root=item["fixture_root"],
                        fixture_file=item["fixture_file"],
                        timeout_seconds=1.0,
                    )
                finally:
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    finished_at = now_iso()
                    with lock:
                        active -= 1
                return {
                    **item,
                    "execution": execution,
                    "observed_concurrency": observed,
                    "elapsed_ms": elapsed_ms,
                    "started_at": started_at,
                    "finished_at": finished_at,
                }

            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                results.extend(pool.map(execute_one, batch))
        return results

    def _snapshot(self, run_id: int, *, idempotent: bool) -> dict[str, Any]:
        run = database.get_mock_agent_run(run_id)
        if run is None:
            raise ValueError(f"mock-agent fixture run 不存在：{run_id}")
        traces = database.list_mock_agent_traces(run_id)
        metrics = {
            "wave_count": max((int(item["wave_index"]) for item in traces), default=0),
            "node_count": len(traces),
            "succeeded_count": sum(
                item["status"] == "succeeded_sandbox_fixture" for item in traces
            ),
            "failed_count": sum(
                item["status"] != "succeeded_sandbox_fixture" for item in traces
            ),
            "input_tokens": sum(int(item["input_tokens"]) for item in traces),
            "output_tokens": sum(int(item["output_tokens"]) for item in traces),
            "elapsed_ms": sum(int(item["elapsed_ms"]) for item in traces),
            "max_observed_concurrency": max(
                (int(item["observed_concurrency"]) for item in traces), default=0
            ),
        }
        return {
            "schema_version": MOCK_AGENT_RUNTIME_SCHEMA_VERSION,
            "run": run,
            "traces": [self._trace_view(item) for item in traces],
            "metrics": metrics,
            "schedule": self.scheduler.get_schedule(int(run["schedule_id"])),
            "idempotent": idempotent,
            "fixture_only": True,
            "business_valid": False,
            "promotion_enabled": False,
            "external_actions_enabled": False,
            "boundaries": [
                "deterministic mock-agent 只运行 Harness 固定 fixture worker。",
                "候选契约只用于 dry-run DAG 交接，不会晋升为 current contract。",
                "不调用真实模型、HIS 源码工具、worktree、PG、Git 或外部系统。",
            ],
        }

    @staticmethod
    def _trace_view(trace: dict[str, Any]) -> dict[str, Any]:
        return {
            **trace,
            "fixture_only": True,
            "business_valid": False,
            "promotion_enabled": False,
        }


def validate_mock_fixture_root(fixture_root: Path) -> Path:
    try:
        root = Path(fixture_root).expanduser().resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ValueError("fixture root 不存在") from exc
    if not root.is_dir():
        raise ValueError("fixture root 不是目录")
    try:
        marker = json.loads((root / FIXTURE_ROOT_MARKER).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("fixture root 缺少有效 marker") from exc
    if marker != {"schema_version": "1.0", "fixture_only": True}:
        raise ValueError("fixture root marker 不合法")
    if any((candidate / ".git").exists() for candidate in (root, *root.parents)):
        raise ValueError("fixture root 不能位于 Git 仓库中")
    return root


def validate_behavior_overrides(overrides: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for node_id, behavior in overrides.items():
        key = str(node_id).strip()
        value = str(behavior).strip()
        if not key or value not in ALLOWED_BEHAVIORS:
            raise ValueError(f"mock-agent behavior 不受支持：{node_id}={behavior}")
        normalized[key] = value
    return normalized


def bounded_token_estimate(payload: Any, budget: int) -> int:
    if budget <= 0:
        return 0
    estimate = max(1, len(json.dumps(payload, ensure_ascii=False, sort_keys=True)) // 32)
    return min(estimate, budget)


def execution_outcome(status: str) -> str:
    if status == "succeeded_sandbox_fixture":
        return "success"
    if status == "blocked_adapter_timeout":
        return "timeout"
    return "failure"


def write_mock_agent_runtime_outputs(
    output_dir: Path,
    snapshot: dict[str, Any],
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_path = output_dir / "mock_agent_fixture_run.json"
    trace_path = output_dir / "mock_agent_fixture_traces.json"
    markdown_path = output_dir / "mock_agent_fixture_run.md"
    run_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    trace_path.write_text(
        json.dumps(snapshot.get("traces") or [], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(mock_agent_run_to_markdown(snapshot), encoding="utf-8")
    return run_path, trace_path, markdown_path


def mock_agent_run_to_markdown(snapshot: dict[str, Any]) -> str:
    run = snapshot.get("run") or {}
    metrics = snapshot.get("metrics") or {}
    return "\n".join(
        (
            "# Deterministic Mock-Agent Fixture Run",
            "",
            f"- Run ID：{run.get('id')}",
            f"- Status：{run.get('status')}",
            f"- Waves：{metrics.get('wave_count', 0)}",
            f"- Nodes：{metrics.get('node_count', 0)}",
            f"- Max observed concurrency：{metrics.get('max_observed_concurrency', 0)}",
            "- fixture-only：true",
            "- business_valid：false",
            "- promotion_enabled：false",
            "",
            "本运行只验证 mock-agent DAG、候选契约交接和 trace，不代表真实业务完成。",
        )
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
