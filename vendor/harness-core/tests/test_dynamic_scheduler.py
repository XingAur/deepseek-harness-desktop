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
from app.dynamic_scheduler import (
    DYNAMIC_SCHEDULER_SCHEMA_VERSION,
    DynamicDryRunScheduler,
    write_dynamic_schedule_outputs,
)
from tools.self_check import run_dynamic_scheduler_checks


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DynamicDryRunSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "harness.sqlite"
        database.init_db()
        self.registry = DynamicPlanRegistry()
        self.scheduler = DynamicDryRunScheduler()

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_start_dispatches_only_first_ready_node_as_simulated(self) -> None:
        plan_id = self.register(simple_request())

        snapshot = self.scheduler.start(plan_id)
        states = states_by_node(snapshot)

        self.assertEqual(DYNAMIC_SCHEDULER_SCHEMA_VERSION, database.get_schema_meta("dynamic_dry_run_scheduler"))
        self.assertTrue(snapshot["dry_run"])
        self.assertFalse(snapshot["execution_enabled"])
        self.assertEqual("active", snapshot["schedule"]["status"])
        self.assertEqual("running_simulated", states["requirement_analysis"]["state"])
        self.assertEqual(1, states["requirement_analysis"]["attempt_count"])
        self.assertEqual("planned", states["frontend_implementation"]["state"])
        self.assertTrue(snapshot["checkpoint"]["hash_valid"])

    def test_start_rejects_plan_that_still_needs_evidence(self) -> None:
        request = DynamicPlanningRequest(
            requirement_id="DFHIS-SCHEDULER-BLOCKED",
            title="前端路径缺失",
            demand_text="修改前端默认值。",
            signals=PlanningSignals(affected_layers=("frontend",), evidence_status="partial"),
        )
        plan_id = self.register(request)

        with self.assertRaisesRegex(ValueError, "needs_evidence"):
            self.scheduler.start(plan_id)

    def test_current_registry_contract_is_reused_as_completed_fact(self) -> None:
        plan_id = self.register(simple_request())
        self.registry.record_contract(
            plan_id=plan_id,
            node_id="requirement_analysis",
            schema_name="RequirementContract",
            schema_version="1.0",
            producer="product_analyst",
            content={"scope": "confirmed"},
            input_artifact_ids=(),
        )

        snapshot = self.scheduler.start(plan_id)
        states = states_by_node(snapshot)

        self.assertEqual("completed_from_contract", states["requirement_analysis"]["state"])
        self.assertEqual("running_simulated", states["frontend_implementation"]["state"])

    def test_registry_stale_contract_blocks_running_schedule_on_next_tick(self) -> None:
        plan_id = self.register(parallel_request())
        requirement = self.record_contract(
            plan_id,
            "requirement_analysis",
            "RequirementContract",
            "product_analyst",
            {"scope": "confirmed"},
        )
        architecture = self.record_contract(
            plan_id,
            "architecture",
            "ArchitectureContract",
            "architect",
            {"modules": ["web", "service"]},
            (requirement["artifact_id"],),
        )
        frontend = self.record_contract(
            plan_id,
            "frontend_implementation",
            "ImplementationResult",
            "frontend_developer",
            {"files": ["web/src/query.vue"]},
            (architecture["artifact_id"],),
        )
        backend = self.record_contract(
            plan_id,
            "backend_implementation",
            "ImplementationResult",
            "backend_developer",
            {"files": ["service/src/Query.java"]},
            (architecture["artifact_id"],),
        )
        self.record_contract(
            plan_id,
            "code_review",
            "ReviewDecision",
            "code_reviewer",
            {"decision": "pass"},
            (frontend["artifact_id"], backend["artifact_id"]),
        )
        snapshot = self.scheduler.start(plan_id)
        schedule_id = snapshot["schedule"]["id"]

        self.record_contract(
            plan_id,
            "frontend_implementation",
            "ImplementationResult",
            "frontend_developer",
            {"files": ["web/src/query.vue"], "revision": 2},
            (architecture["artifact_id"],),
        )
        refreshed = self.scheduler.advance(schedule_id)

        self.assertEqual("blocked_stale", states_by_node(refreshed)["code_review"]["state"])
        self.assertEqual("blocked", refreshed["schedule"]["status"])

    def test_success_events_unlock_parallel_nodes(self) -> None:
        snapshot = self.scheduler.start(self.register(parallel_request()))
        schedule_id = snapshot["schedule"]["id"]

        snapshot = self.scheduler.advance(schedule_id, success_event("req-ok", "requirement_analysis"))
        self.assertEqual("running_simulated", states_by_node(snapshot)["architecture"]["state"])

        snapshot = self.scheduler.advance(schedule_id, success_event("arch-ok", "architecture"))
        states = states_by_node(snapshot)
        self.assertEqual("running_simulated", states["frontend_implementation"]["state"])
        self.assertEqual("running_simulated", states["backend_implementation"]["state"])

    def test_failure_retry_tick_and_retry_exhaustion_are_distinct(self) -> None:
        snapshot = self.scheduler.start(self.register(simple_request()))
        schedule_id = snapshot["schedule"]["id"]

        first_failure = self.scheduler.advance(
            schedule_id,
            {
                "event_id": "req-fail-1",
                "node_id": "requirement_analysis",
                "outcome": "failure",
                "elapsed_seconds": 10,
                "input_tokens": 100,
                "output_tokens": 50,
            },
        )
        repeated = self.scheduler.advance(
            schedule_id,
            {
                "event_id": "req-fail-1",
                "node_id": "requirement_analysis",
                "outcome": "failure",
                "elapsed_seconds": 10,
                "input_tokens": 100,
                "output_tokens": 50,
            },
        )
        self.assertEqual("retry_wait", states_by_node(first_failure)["requirement_analysis"]["state"])
        self.assertEqual(1, states_by_node(repeated)["requirement_analysis"]["attempt_count"])
        self.assertTrue(repeated["last_action"]["idempotent"])

        retried = self.scheduler.advance(schedule_id)
        self.assertEqual("running_simulated", states_by_node(retried)["requirement_analysis"]["state"])
        self.assertEqual(2, states_by_node(retried)["requirement_analysis"]["attempt_count"])

        exhausted = self.scheduler.advance(
            schedule_id,
            {
                "event_id": "req-fail-2",
                "node_id": "requirement_analysis",
                "outcome": "failure",
                "elapsed_seconds": 10,
                "input_tokens": 100,
                "output_tokens": 50,
            },
        )
        self.assertEqual(
            "blocked_retry_exhausted",
            states_by_node(exhausted)["requirement_analysis"]["state"],
        )
        self.assertEqual("blocked", exhausted["schedule"]["status"])

    def test_budget_violation_blocks_without_retry(self) -> None:
        snapshot = self.scheduler.start(self.register(simple_request()))
        schedule_id = snapshot["schedule"]["id"]

        blocked = self.scheduler.advance(
            schedule_id,
            {
                "event_id": "req-over-budget",
                "node_id": "requirement_analysis",
                "outcome": "timeout",
                "elapsed_seconds": 301,
                "input_tokens": 12001,
                "output_tokens": 100,
            },
        )

        state = states_by_node(blocked)["requirement_analysis"]
        self.assertEqual("blocked_budget", state["state"])
        self.assertIn("input_tokens", state["last_decision"])
        self.assertIn("elapsed_seconds", state["last_decision"])
        self.assertEqual("blocked", blocked["schedule"]["status"])

    def test_high_risk_human_gate_can_only_pause(self) -> None:
        snapshot = self.scheduler.start(self.register(high_risk_request()))
        schedule_id = snapshot["schedule"]["id"]
        event_number = 0

        for _ in range(30):
            running_nodes = [
                item["node_id"]
                for item in snapshot["node_states"]
                if item["state"] == "running_simulated"
            ]
            if not running_nodes:
                break
            for node_id in running_nodes:
                event_number += 1
                snapshot = self.scheduler.advance(
                    schedule_id,
                    success_event(f"high-risk-{event_number}", node_id),
                )

        states = states_by_node(snapshot)
        self.assertEqual("paused_human", states["human_gate"]["state"])
        self.assertEqual("paused_human", snapshot["schedule"]["status"])
        self.assertFalse(snapshot["execution_enabled"])

    def test_outputs_are_readonly_and_checkpoint_is_hash_verified(self) -> None:
        snapshot = self.scheduler.start(self.register(simple_request()))

        files = write_dynamic_schedule_outputs(Path(self.temp_dir.name) / "outputs", snapshot)

        self.assertEqual(3, len(files))
        self.assertTrue(all(path.exists() for path in files))
        rendered = "\n".join(path.read_text(encoding="utf-8") for path in files)
        self.assertIn("running_simulated", rendered)
        self.assertIn('"execution_enabled": false', rendered)
        self.assertNotIn("真实节点执行成功", rendered)
        self.assertTrue(snapshot["checkpoint"]["hash_valid"])

        with database.connect() as conn:
            conn.execute(
                "update harness_dynamic_checkpoints set payload = '{}' where schedule_id = ?",
                (snapshot["schedule"]["id"],),
            )
        self.assertFalse(
            self.scheduler.get_schedule(snapshot["schedule"]["id"])["checkpoint"]["hash_valid"]
        )
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            self.scheduler.advance(snapshot["schedule"]["id"])

    def test_scheduler_self_check_is_repeatable_with_retained_database(self) -> None:
        output_dir = Path(self.temp_dir.name) / "self_check"

        first = run_dynamic_scheduler_checks(output_dir=output_dir)
        second = run_dynamic_scheduler_checks(output_dir=output_dir)

        self.assertTrue(all(item["status"] == "pass" for item in first))
        self.assertTrue(all(item["status"] == "pass" for item in second))

    def register(self, request: DynamicPlanningRequest) -> int:
        plan = build_dynamic_plan(request, enabled=True)
        return int(self.registry.register_plan(plan.to_dict())["plan_id"])

    def record_contract(
        self,
        plan_id: int,
        node_id: str,
        schema_name: str,
        producer: str,
        content: dict,
        input_artifact_ids: tuple[str, ...] = (),
    ) -> dict:
        return self.registry.record_contract(
            plan_id=plan_id,
            node_id=node_id,
            schema_name=schema_name,
            schema_version="1.0",
            producer=producer,
            content=content,
            input_artifact_ids=input_artifact_ids,
        )


class DynamicDryRunSchedulerCliTests(unittest.TestCase):
    def test_task_manager_cli_starts_advances_and_shows_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "harness.sqlite"
            plan_path = root / "dynamic_plan.json"
            event_path = root / "success_event.json"
            output_dir = root / "outputs"
            plan_path.write_text(
                json.dumps(build_dynamic_plan(simple_request(), enabled=True).to_dict(), ensure_ascii=False),
                encoding="utf-8",
            )
            event_path.write_text(
                json.dumps(success_event("cli-req-ok", "requirement_analysis"), ensure_ascii=False),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["HARNESS_DB_PATH"] = str(db_path)

            registered = run_task_manager(
                [
                    "register-dynamic-plan",
                    "--plan-file",
                    str(plan_path),
                    "--output-dir",
                    str(output_dir),
                    "--json",
                ],
                env,
            )
            plan_id = json.loads(registered.stdout)["registration"]["plan_id"]
            started = run_task_manager(
                [
                    "start-dynamic-schedule",
                    "--plan-id",
                    str(plan_id),
                    "--output-dir",
                    str(output_dir),
                    "--json",
                ],
                env,
            )
            schedule_id = json.loads(started.stdout)["snapshot"]["schedule"]["id"]
            advanced = run_task_manager(
                [
                    "advance-dynamic-schedule",
                    "--schedule-id",
                    str(schedule_id),
                    "--event-file",
                    str(event_path),
                    "--output-dir",
                    str(output_dir),
                    "--json",
                ],
                env,
            )
            shown = run_task_manager(
                [
                    "show-dynamic-schedule",
                    "--schedule-id",
                    str(schedule_id),
                    "--output-dir",
                    str(output_dir),
                    "--json",
                ],
                env,
            )

            advanced_payload = json.loads(advanced.stdout)
            shown_payload = json.loads(shown.stdout)
            self.assertEqual(
                "succeeded_simulated",
                states_by_node(advanced_payload["snapshot"])["requirement_analysis"]["state"],
            )
            self.assertTrue(shown_payload["snapshot"]["checkpoint"]["hash_valid"])
            self.assertTrue((output_dir / "dynamic_schedule.json").exists())
            self.assertTrue((output_dir / "dynamic_schedule.md").exists())
            self.assertTrue((output_dir / "dynamic_schedule_checkpoint.json").exists())


def run_task_manager(arguments: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "tools/task_manager.py", *arguments],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def states_by_node(snapshot: dict) -> dict[str, dict]:
    return {item["node_id"]: item for item in snapshot["node_states"]}


def success_event(event_id: str, node_id: str) -> dict:
    return {
        "event_id": event_id,
        "node_id": node_id,
        "outcome": "success",
        "elapsed_seconds": 10,
        "input_tokens": 100,
        "output_tokens": 50,
    }


def simple_request() -> DynamicPlanningRequest:
    return DynamicPlanningRequest(
        requirement_id="DFHIS-SCHEDULER-SIMPLE",
        title="挂号默认值调整",
        demand_text="前端单页面默认值与档案管理保持一致。",
        signals=PlanningSignals(
            affected_layers=("frontend",),
            estimated_file_count=2,
            evidence_status="complete",
            allowed_paths={"frontend": ("src/views/register.vue",)},
        ),
    )


def parallel_request() -> DynamicPlanningRequest:
    return DynamicPlanningRequest(
        requirement_id="DFHIS-SCHEDULER-PARALLEL",
        title="前后端查询契约调整",
        demand_text="前端请求和后端接口同步调整低风险查询参数。",
        signals=PlanningSignals(
            affected_layers=("frontend", "backend"),
            estimated_file_count=6,
            dependency_mode="parallel",
            evidence_status="complete",
            verification_mode="integration",
            allowed_paths={
                "frontend": ("web/src/query.vue",),
                "backend": ("service/src/Query.java",),
            },
        ),
    )


def high_risk_request() -> DynamicPlanningRequest:
    return DynamicPlanningRequest(
        requirement_id="DFHIS-SCHEDULER-HIGH-RISK",
        title="医保退费结算规则调整",
        demand_text="医保病人部分退费和结算规则需要调整。",
        signals=PlanningSignals(
            affected_layers=("frontend", "backend"),
            estimated_file_count=6,
            dependency_mode="serial",
            evidence_status="complete",
            verification_mode="integration",
            allowed_paths={
                "frontend": ("web/src/refund.vue",),
                "backend": ("service/src/Refund.java",),
            },
        ),
    )


if __name__ == "__main__":
    unittest.main()
