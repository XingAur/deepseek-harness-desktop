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
from app.model_invocation_runtime import OfflineModelInvocationRuntime
from app.mock_agent_runtime import MAX_MOCK_PARALLEL, validate_mock_fixture_root
from app.node_runtime import sha256_json


MODEL_DAG_RUNTIME_SCHEMA_VERSION = "1.0-offline-model-dag-runtime"
MODEL_DAG_ADAPTER_SCHEMA_VERSION = "1.0-offline-model-dag-adapters"
ALLOWED_ADAPTER_MODES = {"mock", "replay"}


class OfflineModelDagRuntime:
    def __init__(self) -> None:
        database.init_db()
        self.scheduler = DynamicDryRunScheduler()
        self.model_runtime = OfflineModelInvocationRuntime()

    def run(
        self,
        schedule_id: int,
        *,
        fixture_root: Path,
        max_parallel: int = 2,
        adapter_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if max_parallel < 1 or max_parallel > MAX_MOCK_PARALLEL:
            raise ValueError(f"max_parallel 必须在 1-{MAX_MOCK_PARALLEL} 之间")
        root = validate_mock_fixture_root(fixture_root)
        schedule = self.scheduler.get_schedule(schedule_id)
        if schedule["schedule"].get("mode") != "dry_run":
            raise ValueError("offline model DAG 只支持 dry_run schedule")
        if not (schedule.get("checkpoint") or {}).get("hash_valid"):
            raise ValueError("dynamic schedule checkpoint 校验失败")
        plan_id = int(schedule["schedule"]["plan_id"])
        known_node_ids = {str(item["node_id"]) for item in schedule["node_states"]}
        policy = validate_adapter_policy(adapter_policy, known_node_ids)

        existing = database.get_model_dag_run_by_schedule(schedule_id)
        if existing:
            if int(existing["max_parallel"]) != max_parallel or existing["adapter_policy"] != policy:
                raise ValueError("该 schedule 已使用不同的 model DAG 并行度或 adapter policy")
            return self._snapshot(int(existing["id"]), idempotent=True)

        run_id = database.add_model_dag_run(
            {
                "schedule_id": schedule_id,
                "plan_id": plan_id,
                "run_key": sha256_json(
                    {
                        "schema_version": MODEL_DAG_RUNTIME_SCHEMA_VERSION,
                        "schedule_id": schedule_id,
                        "adapter_policy": policy,
                    }
                ),
                "status": "running",
                "max_parallel": max_parallel,
                "adapter_policy": policy,
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
                    {
                        "node_id": str(node["node_id"]),
                        "adapter": resolve_node_adapter(policy, str(node["node_id"])),
                    }
                    for node in running_nodes
                ]
                results = self._execute_wave(
                    schedule_id,
                    prepared,
                    fixture_root=root,
                    max_parallel=max_parallel,
                )
                wave_failed = False
                for item in sorted(results, key=lambda value: value["node_id"]):
                    snapshot = item["snapshot"]
                    invocation = snapshot["invocation"]
                    usage = invocation.get("usage") or {}
                    trace_id = sha256_json(
                        {
                            "run_id": run_id,
                            "wave_index": wave_index,
                            "node_id": item["node_id"],
                            "invocation_id": invocation["id"],
                        }
                    )
                    database.add_model_dag_trace(
                        {
                            "run_id": run_id,
                            "schedule_id": schedule_id,
                            "plan_id": plan_id,
                            "wave_index": wave_index,
                            "trace_id": trace_id,
                            "node_id": item["node_id"],
                            "role_id": invocation["role_id"],
                            "context_id": invocation["context_id"],
                            "invocation_id": invocation["id"],
                            "mode": invocation["mode"],
                            "provider": invocation["provider"],
                            "model": invocation["model"],
                            "status": invocation["status"],
                            "error_code": invocation["error_code"],
                            "request_hash": invocation["request_hash"],
                            "response_hash": invocation["response_hash"],
                            "candidate_hash": invocation["candidate_hash"],
                            "cassette_relpath": invocation["cassette_relpath"],
                            "input_tokens": int(usage.get("input_tokens") or 0),
                            "output_tokens": int(usage.get("output_tokens") or 0),
                            "elapsed_ms": item["elapsed_ms"],
                            "observed_concurrency": item["observed_concurrency"],
                            "parallel_observed": item["observed_concurrency"] > 1,
                            "started_at": item["started_at"],
                            "finished_at": item["finished_at"],
                            "details": {
                                "fixture_only": True,
                                "adapter": item["adapter"],
                                "hashes_valid": snapshot["hashes_valid"],
                            },
                        }
                    )
                    succeeded = invocation["status"] == "succeeded_fixture"
                    if not succeeded:
                        wave_failed = True
                        failure_codes.append(invocation["error_code"] or invocation["status"])
                    self.scheduler.advance(
                        schedule_id,
                        {
                            "event_id": f"model-dag:{run_id}:{invocation['id']}",
                            "node_id": item["node_id"],
                            "outcome": "success" if succeeded else "failure",
                            "elapsed_seconds": int(math.ceil(item["elapsed_ms"] / 1000)),
                            "input_tokens": int(usage.get("input_tokens") or 0),
                            "output_tokens": int(usage.get("output_tokens") or 0),
                        },
                    )
                if wave_failed:
                    final_status = "failed_fixture"
                    break
        except Exception:
            database.update_model_dag_run(
                run_id,
                status="blocked_fixture",
                completed_at=now_iso(),
                summary={"fixture_only": True, "reason": "orchestration_exception"},
            )
            raise

        traces = database.list_model_dag_traces(run_id)
        database.update_model_dag_run(
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

    def _execute_wave(
        self,
        schedule_id: int,
        prepared: list[dict[str, Any]],
        *,
        fixture_root: Path,
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
                    if len(batch) > 1:
                        time.sleep(0.01)
                    adapter = item["adapter"]
                    cassette_file = (
                        fixture_root / adapter["cassette_file"]
                        if adapter.get("cassette_file")
                        else None
                    )
                    snapshot = self.model_runtime.invoke(
                        schedule_id,
                        item["node_id"],
                        fixture_root=fixture_root,
                        mode=adapter["mode"],
                        cassette_file=cassette_file,
                        record_cassette=adapter["record_cassette"],
                    )
                finally:
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    finished_at = now_iso()
                    with lock:
                        active -= 1
                return {
                    **item,
                    "snapshot": snapshot,
                    "observed_concurrency": observed,
                    "elapsed_ms": elapsed_ms,
                    "started_at": started_at,
                    "finished_at": finished_at,
                }

            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                results.extend(pool.map(execute_one, batch))
        return results

    def _snapshot(self, run_id: int, *, idempotent: bool) -> dict[str, Any]:
        run = database.get_model_dag_run(run_id)
        if run is None:
            raise ValueError(f"model fixture DAG run 不存在：{run_id}")
        traces = database.list_model_dag_traces(run_id)
        metrics = {
            "wave_count": max((int(item["wave_index"]) for item in traces), default=0),
            "node_count": len(traces),
            "succeeded_count": sum(item["status"] == "succeeded_fixture" for item in traces),
            "failed_count": sum(item["status"] != "succeeded_fixture" for item in traces),
            "input_tokens": sum(int(item["input_tokens"]) for item in traces),
            "output_tokens": sum(int(item["output_tokens"]) for item in traces),
            "elapsed_ms": sum(int(item["elapsed_ms"]) for item in traces),
            "max_observed_concurrency": max(
                (int(item["observed_concurrency"]) for item in traces), default=0
            ),
        }
        return {
            "schema_version": MODEL_DAG_RUNTIME_SCHEMA_VERSION,
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
                "所有节点只调用 v0.55 mock/replay 离线模型适配器。",
                "模型候选只用于 dry-run DAG 交接，不晋升 current contract。",
                "不读取凭证，不调用网络、HIS 源码、worktree、PG、Git 或外部系统。",
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


def validate_adapter_policy(
    policy: dict[str, Any] | None,
    known_node_ids: set[str],
) -> dict[str, Any]:
    raw = policy or {
        "schema_version": MODEL_DAG_ADAPTER_SCHEMA_VERSION,
        "default": {"mode": "mock", "record_cassette": False},
        "nodes": {},
    }
    if not isinstance(raw, dict) or raw.get("schema_version") != MODEL_DAG_ADAPTER_SCHEMA_VERSION:
        raise ValueError("model DAG adapter policy schema 不合法")
    if set(raw) - {"schema_version", "default", "nodes"}:
        raise ValueError("model DAG adapter policy 包含未知字段")
    default = normalize_adapter(raw.get("default") or {}, allow_partial=False)
    nodes = raw.get("nodes") or {}
    if not isinstance(nodes, dict):
        raise ValueError("model DAG adapter nodes 必须是对象")
    unknown = sorted(set(str(key) for key in nodes) - known_node_ids)
    if unknown:
        raise ValueError("model DAG adapter 包含未知节点：" + ", ".join(unknown))
    normalized_nodes: dict[str, dict[str, Any]] = {}
    for node_id, adapter in nodes.items():
        if not isinstance(adapter, dict):
            raise ValueError(f"节点 adapter 必须是对象：{node_id}")
        merged = {**default, **adapter}
        normalized_nodes[str(node_id)] = normalize_adapter(merged, allow_partial=False)
    return {
        "schema_version": MODEL_DAG_ADAPTER_SCHEMA_VERSION,
        "default": default,
        "nodes": normalized_nodes,
    }


def normalize_adapter(adapter: dict[str, Any], *, allow_partial: bool) -> dict[str, Any]:
    allowed_fields = {"mode", "record_cassette", "cassette_file"}
    if set(adapter) - allowed_fields:
        raise ValueError("model DAG node adapter 包含未知字段")
    mode = str(adapter.get("mode") or ("" if allow_partial else "mock")).strip().lower()
    if mode not in ALLOWED_ADAPTER_MODES:
        raise ValueError("model DAG node adapter 只允许 mock/replay")
    record_cassette = adapter.get("record_cassette", False)
    if not isinstance(record_cassette, bool):
        raise ValueError("record_cassette 必须是 boolean")
    cassette_file = str(adapter.get("cassette_file") or "").strip()
    if cassette_file:
        path = Path(cassette_file)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("cassette_file 必须是 fixture root 内的相对路径")
    if mode == "replay" and not cassette_file:
        raise ValueError("replay adapter 必须提供 cassette_file")
    if mode == "mock" and cassette_file:
        raise ValueError("mock adapter 不接受 cassette_file")
    if mode == "replay" and record_cassette:
        raise ValueError("replay adapter 不能录制 cassette")
    return {
        "mode": mode,
        "record_cassette": record_cassette,
        "cassette_file": cassette_file,
    }


def resolve_node_adapter(policy: dict[str, Any], node_id: str) -> dict[str, Any]:
    return dict((policy.get("nodes") or {}).get(node_id) or policy["default"])


def write_model_dag_outputs(
    output_dir: Path,
    snapshot: dict[str, Any],
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_path = output_dir / "model_fixture_dag_run.json"
    traces_path = output_dir / "model_fixture_dag_traces.json"
    markdown_path = output_dir / "model_fixture_dag_run.md"
    run_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    traces_path.write_text(
        json.dumps(snapshot.get("traces") or [], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(model_dag_to_markdown(snapshot), encoding="utf-8")
    return run_path, traces_path, markdown_path


def model_dag_to_markdown(snapshot: dict[str, Any]) -> str:
    run = snapshot.get("run") or {}
    metrics = snapshot.get("metrics") or {}
    return "\n".join(
        (
            "# Offline Model Fixture DAG Run",
            "",
            f"- Run ID：{run.get('id')}",
            f"- Status：{run.get('status')}",
            f"- Waves：{metrics.get('wave_count', 0)}",
            f"- Nodes：{metrics.get('node_count', 0)}",
            f"- Max observed concurrency：{metrics.get('max_observed_concurrency', 0)}",
            "- fixture-only：true",
            "- business_valid：false",
            "- promotion_enabled：false",
            "- credentials/network：disabled",
            "",
            "本运行只验证离线模型 DAG、结构化候选交接和 trace，不代表真实智能体或业务完成。",
        )
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
