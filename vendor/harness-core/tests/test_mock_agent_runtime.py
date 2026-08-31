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
from app.executor_runtime import SandboxExecutorRuntime
from app.mock_agent_runtime import (
    MOCK_AGENT_RUNTIME_SCHEMA_VERSION,
    DeterministicMockAgentRuntime,
    write_mock_agent_runtime_outputs,
)
from tools.self_check import run_mock_agent_checks


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DeterministicMockAgentRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = self.root / "harness.sqlite"
        database.init_db()
        self.registry = DynamicPlanRegistry()
        self.scheduler = DynamicDryRunScheduler()
        self.runtime = DeterministicMockAgentRuntime()
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

    def test_full_fixture_dag_is_persistent_deterministic_and_parallel_observed(self) -> None:
        schedule = self.scheduler.start(self.plan_id)
        schedule_id = int(schedule["schedule"]["id"])

        result = self.runtime.run(
            schedule_id,
            fixture_root=self.fixture_root,
            max_parallel=2,
        )

        self.assertEqual(MOCK_AGENT_RUNTIME_SCHEMA_VERSION, database.get_schema_meta("mock_agent_runtime"))
        self.assertEqual("completed_fixture", result["run"]["status"])
        self.assertEqual("completed_simulated", result["schedule"]["schedule"]["status"])
        self.assertEqual(4, result["metrics"]["node_count"])
        self.assertEqual(3, result["metrics"]["wave_count"])
        self.assertGreaterEqual(result["metrics"]["max_observed_concurrency"], 2)
        self.assertTrue(any(trace["parallel_observed"] for trace in result["traces"]))
        self.assertTrue(all(trace["fixture_only"] for trace in result["traces"]))
        self.assertFalse(result["business_valid"])
        self.assertFalse(result["promotion_enabled"])

    def test_downstream_candidates_reference_upstream_candidates(self) -> None:
        schedule = self.scheduler.start(self.plan_id)
        result = self.runtime.run(
            int(schedule["schedule"]["id"]),
            fixture_root=self.fixture_root,
            max_parallel=2,
        )
        executor = SandboxExecutorRuntime()
        executions = {
            trace["node_id"]: executor.get_execution(trace["execution_id"])
            for trace in result["traces"]
        }
        requirement_candidate = executions["requirement_analysis"][
            "sandbox_fixture_contract_candidate"
        ]
        frontend_candidate = executions["frontend_implementation"][
            "sandbox_fixture_contract_candidate"
        ]
        backend_candidate = executions["backend_implementation"][
            "sandbox_fixture_contract_candidate"
        ]
        verify_candidate = executions["verify"]["sandbox_fixture_contract_candidate"]

        self.assertEqual(
            [requirement_candidate["artifact_id"]],
            frontend_candidate["input_artifact_ids"],
        )
        self.assertEqual(
            [requirement_candidate["artifact_id"]],
            backend_candidate["input_artifact_ids"],
        )
        self.assertEqual(
            {frontend_candidate["artifact_id"], backend_candidate["artifact_id"]},
            set(verify_candidate["input_artifact_ids"]),
        )

    def test_fixture_candidates_never_promote_registry_contracts(self) -> None:
        schedule = self.scheduler.start(self.plan_id)
        result = self.runtime.run(
            int(schedule["schedule"]["id"]),
            fixture_root=self.fixture_root,
            max_parallel=2,
        )

        for trace in result["traces"]:
            contract = database.get_latest_contract_artifact(self.plan_id, trace["node_id"])
            self.assertEqual("planned", contract["status"])
        self.assertFalse(result["external_actions_enabled"])

    def test_failure_is_isolated_and_does_not_auto_retry(self) -> None:
        schedule = self.scheduler.start(self.plan_id)
        schedule_id = int(schedule["schedule"]["id"])

        result = self.runtime.run(
            schedule_id,
            fixture_root=self.fixture_root,
            max_parallel=2,
            behavior_overrides={"backend_implementation": "failure"},
        )

        traces = {trace["node_id"]: trace for trace in result["traces"]}
        states = {
            item["node_id"]: item["state"]
            for item in result["schedule"]["node_states"]
        }
        self.assertEqual("failed_fixture", result["run"]["status"])
        self.assertEqual("succeeded_sandbox_fixture", traces["frontend_implementation"]["status"])
        self.assertEqual("failed_adapter", traces["backend_implementation"]["status"])
        self.assertEqual("retry_wait", states["backend_implementation"])
        self.assertNotIn("verify", traces)
        self.assertEqual(3, len(traces))

    def test_terminal_run_is_idempotent(self) -> None:
        schedule = self.scheduler.start(self.plan_id)
        schedule_id = int(schedule["schedule"]["id"])
        first = self.runtime.run(
            schedule_id,
            fixture_root=self.fixture_root,
            max_parallel=2,
        )
        repeated = self.runtime.run(
            schedule_id,
            fixture_root=self.fixture_root,
            max_parallel=2,
        )

        self.assertEqual(first["run"]["id"], repeated["run"]["id"])
        self.assertEqual(first["metrics"], repeated["metrics"])
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(4, len(database.list_mock_agent_traces(first["run"]["id"])))

    def test_invalid_parallel_root_and_unknown_behavior_are_rejected(self) -> None:
        schedule = self.scheduler.start(self.plan_id)
        schedule_id = int(schedule["schedule"]["id"])

        with self.assertRaisesRegex(ValueError, "max_parallel"):
            self.runtime.run(
                schedule_id,
                fixture_root=self.fixture_root,
                max_parallel=0,
            )
        invalid_root = self.root / "invalid-root"
        invalid_root.mkdir()
        with self.assertRaisesRegex(ValueError, "marker"):
            self.runtime.run(
                schedule_id,
                fixture_root=invalid_root,
                max_parallel=1,
            )
        with self.assertRaisesRegex(ValueError, "未知节点"):
            self.runtime.run(
                schedule_id,
                fixture_root=self.fixture_root,
                max_parallel=1,
                behavior_overrides={"not-a-node": "failure"},
            )

    def test_runtime_outputs_are_explicitly_fixture_only(self) -> None:
        schedule = self.scheduler.start(self.plan_id)
        result = self.runtime.run(
            int(schedule["schedule"]["id"]),
            fixture_root=self.fixture_root,
            max_parallel=2,
        )
        files = write_mock_agent_runtime_outputs(self.root / "outputs", result)
        rendered = "\n".join(path.read_text(encoding="utf-8") for path in files)

        self.assertEqual(3, len(files))
        self.assertTrue(all(path.exists() for path in files))
        self.assertIn("fixture-only", rendered)
        self.assertIn("business_valid", rendered)
        self.assertNotIn('"business_valid": true', rendered)

    def test_mock_agent_self_check_is_repeatable_with_retained_database(self) -> None:
        output_dir = self.root / "self_check"

        first = run_mock_agent_checks(output_dir=output_dir)
        second = run_mock_agent_checks(output_dir=output_dir)

        self.assertTrue(all(item["status"] == "pass" for item in first))
        self.assertTrue(all(item["status"] == "pass" for item in second))

    def register_parallel_plan(self) -> int:
        request = DynamicPlanningRequest(
            requirement_id="DFHIS-MOCK-AGENT",
            title="前后端并行 fixture 编排",
            demand_text="验证候选契约交接和并行 trace。",
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
        plan = build_dynamic_plan(request, enabled=True)
        return int(self.registry.register_plan(plan.to_dict())["plan_id"])


class DeterministicMockAgentRuntimeCliTests(unittest.TestCase):
    def test_task_manager_cli_runs_and_shows_fixture_schedule(self) -> None:
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
                requirement_id="DFHIS-MOCK-CLI",
                title="mock-agent CLI",
                demand_text="验证 fixture DAG CLI。",
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
            registered = run_task_manager(
                ["register-dynamic-plan", "--plan-file", str(plan_file), "--output-dir", str(output_dir), "--json"],
                env,
            )
            plan_id = json.loads(registered.stdout)["registration"]["plan_id"]
            started = run_task_manager(
                ["start-dynamic-schedule", "--plan-id", str(plan_id), "--output-dir", str(output_dir), "--json"],
                env,
            )
            schedule_id = json.loads(started.stdout)["snapshot"]["schedule"]["id"]

            executed = run_task_manager(
                [
                    "run-mock-agent-fixture-schedule", "--schedule-id", str(schedule_id),
                    "--fixture-root", str(fixture_root), "--max-parallel", "2",
                    "--output-dir", str(output_dir), "--json",
                ],
                env,
            )
            run = json.loads(executed.stdout)["run"]
            shown = run_task_manager(
                [
                    "show-mock-agent-fixture-run", "--run-id", str(run["id"]),
                    "--output-dir", str(output_dir), "--json",
                ],
                env,
            )
            shown_payload = json.loads(shown.stdout)

            self.assertEqual("completed_fixture", run["status"])
            self.assertEqual(run["id"], shown_payload["run"]["id"])
            self.assertFalse(shown_payload["business_valid"])
            self.assertTrue((output_dir / "mock_agent_fixture_run.json").exists())
            self.assertTrue((output_dir / "mock_agent_fixture_traces.json").exists())


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
