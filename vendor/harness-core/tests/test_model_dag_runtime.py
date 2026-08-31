from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app import database
from app.dynamic_plan_registry import DynamicPlanRegistry
from app.dynamic_planning import DynamicPlanningRequest, PlanningSignals, build_dynamic_plan
from app.dynamic_scheduler import DynamicDryRunScheduler
from app.model_dag_runtime import (
    MODEL_DAG_RUNTIME_SCHEMA_VERSION,
    OfflineModelDagRuntime,
    write_model_dag_outputs,
)
from app.model_invocation_runtime import OfflineModelInvocationRuntime
from tools.self_check import run_model_dag_checks


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class OfflineModelDagRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = self.root / "harness.sqlite"
        database.init_db()
        self.registry = DynamicPlanRegistry()
        self.scheduler = DynamicDryRunScheduler()
        self.runtime = OfflineModelDagRuntime()
        self.plan_id = self.register_parallel_plan()
        self.fixture_root = self.root / "fixtures"
        self.fixture_root.mkdir()
        (self.fixture_root / ".harness-fixture-root.json").write_text(
            json.dumps({"schema_version": "1.0", "fixture_only": True}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_full_model_fixture_dag_is_persistent_parallel_and_candidate_only(self) -> None:
        schedule = self.scheduler.start(self.plan_id)
        result = self.runtime.run(
            int(schedule["schedule"]["id"]),
            fixture_root=self.fixture_root,
            max_parallel=2,
            adapter_policy={
                "schema_version": "1.0-offline-model-dag-adapters",
                "default": {"mode": "mock", "record_cassette": True},
                "nodes": {},
            },
        )

        self.assertEqual(MODEL_DAG_RUNTIME_SCHEMA_VERSION, database.get_schema_meta("model_dag_runtime"))
        self.assertEqual("completed_fixture", result["run"]["status"])
        self.assertEqual("completed_simulated", result["schedule"]["schedule"]["status"])
        self.assertEqual(4, result["metrics"]["node_count"])
        self.assertEqual(3, result["metrics"]["wave_count"])
        self.assertGreaterEqual(result["metrics"]["max_observed_concurrency"], 2)
        self.assertTrue(all(trace["mode"] == "mock" for trace in result["traces"]))
        self.assertTrue(all(trace["cassette_relpath"] for trace in result["traces"]))
        self.assertTrue(all(database.get_latest_contract_artifact(self.plan_id, trace["node_id"])["status"] == "planned" for trace in result["traces"]))
        self.assertFalse(result["business_valid"])
        self.assertFalse(result["promotion_enabled"])

    def test_downstream_model_candidates_reference_upstream_candidates(self) -> None:
        schedule = self.scheduler.start(self.plan_id)
        result = self.runtime.run(
            int(schedule["schedule"]["id"]),
            fixture_root=self.fixture_root,
            max_parallel=2,
        )
        invocations = {
            trace["node_id"]: database.get_model_invocation(trace["invocation_id"])
            for trace in result["traces"]
        }
        requirement = invocations["requirement_analysis"]["candidate_payload"]
        frontend = invocations["frontend_implementation"]["candidate_payload"]
        backend = invocations["backend_implementation"]["candidate_payload"]
        verify = invocations["verify"]["candidate_payload"]

        self.assertEqual([requirement["artifact_id"]], frontend["input_artifact_ids"])
        self.assertEqual([requirement["artifact_id"]], backend["input_artifact_ids"])
        self.assertEqual(
            {frontend["artifact_id"], backend["artifact_id"]},
            set(verify["input_artifact_ids"]),
        )

    def test_node_adapter_can_replay_a_matching_cassette(self) -> None:
        single_plan_id = self.register_single_plan("DFHIS-MODEL-DAG-REPLAY")
        schedule = self.scheduler.start(single_plan_id)
        schedule_id = int(schedule["schedule"]["id"])
        invocation_runtime = OfflineModelInvocationRuntime()
        prepared = invocation_runtime.build_request(schedule_id, "requirement_analysis")
        cassette = self.fixture_root / "replay.json"
        cassette.write_text(
            json.dumps(
                {
                    "schema_version": "1.0-offline-model-cassette",
                    "fixture_only": True,
                    "request_hash": prepared["request_hash"],
                    "response": invocation_runtime.build_mock_response(prepared["request"]),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = self.runtime.run(
            schedule_id,
            fixture_root=self.fixture_root,
            adapter_policy={
                "schema_version": "1.0-offline-model-dag-adapters",
                "default": {"mode": "mock", "record_cassette": False},
                "nodes": {
                    "requirement_analysis": {
                        "mode": "replay",
                        "cassette_file": "replay.json",
                    }
                },
            },
        )

        self.assertEqual("completed_fixture", result["run"]["status"])
        self.assertEqual("replay", result["traces"][0]["mode"])
        self.assertEqual("replay.json", result["traces"][0]["cassette_relpath"])

    def test_failed_replay_is_isolated_and_not_retried(self) -> None:
        single_plan_id = self.register_single_plan("DFHIS-MODEL-DAG-FAIL")
        schedule = self.scheduler.start(single_plan_id)
        schedule_id = int(schedule["schedule"]["id"])
        invocation_runtime = OfflineModelInvocationRuntime()
        prepared = invocation_runtime.build_request(schedule_id, "requirement_analysis")
        response = invocation_runtime.build_mock_response(prepared["request"])
        response["output"].pop("contract_name")
        cassette = self.fixture_root / "invalid.json"
        cassette.write_text(
            json.dumps(
                {
                    "schema_version": "1.0-offline-model-cassette",
                    "fixture_only": True,
                    "request_hash": prepared["request_hash"],
                    "response": response,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = self.runtime.run(
            schedule_id,
            fixture_root=self.fixture_root,
            adapter_policy={
                "schema_version": "1.0-offline-model-dag-adapters",
                "default": {"mode": "mock", "record_cassette": False},
                "nodes": {
                    "requirement_analysis": {
                        "mode": "replay",
                        "cassette_file": "invalid.json",
                    }
                },
            },
        )

        self.assertEqual("failed_fixture", result["run"]["status"])
        self.assertEqual(1, len(result["traces"]))
        self.assertEqual("blocked_structured_output", result["traces"][0]["status"])
        state = next(item for item in result["schedule"]["node_states"] if item["node_id"] == "requirement_analysis")
        self.assertEqual("retry_wait", state["state"])

    def test_terminal_model_dag_run_is_idempotent(self) -> None:
        schedule = self.scheduler.start(self.plan_id)
        schedule_id = int(schedule["schedule"]["id"])
        first = self.runtime.run(schedule_id, fixture_root=self.fixture_root, max_parallel=2)
        repeated = self.runtime.run(schedule_id, fixture_root=self.fixture_root, max_parallel=2)

        self.assertEqual(first["run"]["id"], repeated["run"]["id"])
        self.assertEqual(first["metrics"], repeated["metrics"])
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(4, len(database.list_model_dag_traces(first["run"]["id"])))

    def test_invalid_adapter_policy_is_rejected(self) -> None:
        schedule = self.scheduler.start(self.plan_id)
        schedule_id = int(schedule["schedule"]["id"])
        base = {
            "schema_version": "1.0-offline-model-dag-adapters",
            "default": {"mode": "mock", "record_cassette": False},
            "nodes": {},
        }
        invalid_cases = [
            {**base, "nodes": {"unknown": {"mode": "mock"}}},
            {**base, "default": {"mode": "real", "record_cassette": False}},
            {**base, "nodes": {"requirement_analysis": {"mode": "replay", "cassette_file": "/tmp/outside.json"}}},
        ]
        for policy in invalid_cases:
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                self.runtime.run(
                    schedule_id,
                    fixture_root=self.fixture_root,
                    adapter_policy=policy,
                )

    def test_model_dag_outputs_and_self_check_are_repeatable(self) -> None:
        schedule = self.scheduler.start(self.plan_id)
        result = self.runtime.run(
            int(schedule["schedule"]["id"]),
            fixture_root=self.fixture_root,
            max_parallel=2,
        )
        files = write_model_dag_outputs(self.root / "outputs", result)
        rendered = "\n".join(path.read_text(encoding="utf-8") for path in files)
        self.assertEqual(3, len(files))
        self.assertIn("fixture-only", rendered)
        self.assertNotIn('"business_valid": true', rendered)

        first = run_model_dag_checks(output_dir=self.root / "self_check")
        second = run_model_dag_checks(output_dir=self.root / "self_check")
        self.assertTrue(all(item["status"] == "pass" for item in first))
        self.assertTrue(all(item["status"] == "pass" for item in second))

    def register_parallel_plan(self) -> int:
        request = DynamicPlanningRequest(
            requirement_id="DFHIS-MODEL-DAG",
            title="前后端并行离线模型 DAG",
            demand_text="验证结构化候选交接和并行 trace。",
            signals=PlanningSignals(
                affected_layers=("frontend", "backend"),
                estimated_file_count=6,
                dependency_mode="parallel",
                evidence_status="complete",
                verification_mode="targeted",
                allowed_paths={
                    "frontend": ("fixture/web/Query.vue",),
                    "backend": ("fixture/service/Query.java",),
                },
            ),
        )
        return int(self.registry.register_plan(build_dynamic_plan(request, enabled=True).to_dict())["plan_id"])

    def register_single_plan(self, requirement_id: str) -> int:
        request = DynamicPlanningRequest(
            requirement_id=requirement_id,
            title="单节点 replay",
            demand_text="验证单节点 cassette replay。",
            signals=PlanningSignals(
                affected_layers=("frontend",),
                estimated_file_count=1,
                evidence_status="complete",
                allowed_paths={"frontend": ("fixture/web/Query.vue",)},
            ),
        )
        return int(self.registry.register_plan(build_dynamic_plan(request, enabled=True).to_dict())["plan_id"])


class OfflineModelDagRuntimeCliTests(unittest.TestCase):
    def test_task_manager_cli_runs_and_shows_model_fixture_dag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "harness.sqlite"
            fixture_root = root / "fixtures"
            fixture_root.mkdir()
            (fixture_root / ".harness-fixture-root.json").write_text(
                json.dumps({"schema_version": "1.0", "fixture_only": True}),
                encoding="utf-8",
            )
            request = DynamicPlanningRequest(
                requirement_id="DFHIS-MODEL-DAG-CLI",
                title="离线模型 DAG CLI",
                demand_text="验证模型 DAG CLI。",
                signals=PlanningSignals(
                    affected_layers=("frontend",),
                    estimated_file_count=1,
                    evidence_status="complete",
                    allowed_paths={"frontend": ("fixture/web/Query.vue",)},
                ),
            )
            plan_file = root / "plan.json"
            plan_file.write_text(
                json.dumps(build_dynamic_plan(request, enabled=True).to_dict(), ensure_ascii=False),
                encoding="utf-8",
            )
            output_dir = root / "outputs"
            env = dict(os.environ)
            env["HARNESS_DB_PATH"] = str(db_path)
            plan_id = json.loads(run_task_manager(
                ["register-dynamic-plan", "--plan-file", str(plan_file), "--output-dir", str(output_dir), "--json"], env
            ).stdout)["registration"]["plan_id"]
            schedule_id = json.loads(run_task_manager(
                ["start-dynamic-schedule", "--plan-id", str(plan_id), "--output-dir", str(output_dir), "--json"], env
            ).stdout)["snapshot"]["schedule"]["id"]

            executed = json.loads(run_task_manager(
                [
                    "run-model-fixture-schedule", "--schedule-id", str(schedule_id),
                    "--fixture-root", str(fixture_root), "--max-parallel", "2",
                    "--record-cassettes", "--output-dir", str(output_dir), "--json",
                ], env
            ).stdout)
            shown = json.loads(run_task_manager(
                [
                    "show-model-fixture-schedule-run", "--run-id", str(executed["run"]["id"]),
                    "--output-dir", str(output_dir), "--json",
                ], env
            ).stdout)

            self.assertEqual("completed_fixture", executed["run"]["status"])
            self.assertEqual(executed["run"]["id"], shown["run"]["id"])
            self.assertFalse(shown["business_valid"])
            self.assertTrue((output_dir / "model_fixture_dag_run.json").exists())
            self.assertTrue((output_dir / "model_fixture_dag_traces.json").exists())


def run_task_manager(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "tools" / "task_manager.py"), *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )


if __name__ == "__main__":
    unittest.main()
