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
from app.node_runtime import (
    CONTROLLED_NODE_RUNTIME_SCHEMA_VERSION,
    ControlledNodeRuntime,
    write_node_runtime_outputs,
)
from tools.self_check import run_node_runtime_checks


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ControlledNodeRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = self.root / "harness.sqlite"
        database.init_db()
        self.registry = DynamicPlanRegistry()
        self.scheduler = DynamicDryRunScheduler()
        self.runtime = ControlledNodeRuntime()
        self.plan_id = self.register_plan()
        self.schedule = self.scheduler.start(self.plan_id)
        self.schedule_id = int(self.schedule["schedule"]["id"])

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_prepare_context_is_immutable_hash_bound_and_persistent(self) -> None:
        context = self.runtime.prepare_context(
            self.schedule_id,
            "requirement_analysis",
            requested_tools=("read_artifacts",),
        )

        self.assertEqual(
            CONTROLLED_NODE_RUNTIME_SCHEMA_VERSION,
            database.get_schema_meta("controlled_node_runtime"),
        )
        self.assertTrue(context["hash_valid"])
        self.assertTrue(context["fixture_only"])
        self.assertFalse(context["execution_enabled"])
        self.assertEqual("running_simulated", context["envelope"]["node_state"])
        self.assertEqual(
            self.schedule["checkpoint"]["checkpoint_hash"],
            context["envelope"]["checkpoint_hash"],
        )
        self.assertEqual(context["id"], self.runtime.get_context(context["id"])["id"])

    def test_permission_adjudication_is_default_deny(self) -> None:
        context = self.runtime.prepare_context(
            self.schedule_id,
            "requirement_analysis",
            requested_tools=("read_artifacts", "search_code", "git_push", "unknown_tool"),
        )
        decisions = {item["tool"]: item for item in context["tool_decisions"]}

        self.assertEqual("allowed", decisions["read_artifacts"]["decision"])
        self.assertEqual("executor_unsupported", decisions["search_code"]["reason"])
        self.assertEqual("global_hard_guard", decisions["git_push"]["reason"])
        self.assertEqual("not_role_allowed", decisions["unknown_tool"]["reason"])
        self.assertEqual("denied", context["permission_status"])

    def test_prepare_rejects_node_that_is_not_running(self) -> None:
        with self.assertRaisesRegex(ValueError, "running_simulated"):
            self.runtime.prepare_context(
                self.schedule_id,
                "frontend_implementation",
                requested_tools=("read_artifacts",),
            )

    def test_tampered_context_is_detected_before_execution(self) -> None:
        context = self.prepare_allowed_context()
        fixture_root, fixture_file = self.write_fixture(context)
        with database.connect() as conn:
            conn.execute(
                "update harness_dynamic_context_envelopes set payload = '{}' where id = ?",
                (context["id"],),
            )

        result = self.runtime.execute_fixture(
            context["id"],
            fixture_root=fixture_root,
            fixture_file=fixture_file,
        )

        self.assertEqual("blocked_context_integrity", result["status"])
        self.assertEqual({}, result["fixture_contract_candidate"])

    def test_fixture_boundary_rejects_missing_marker_git_root_and_escape(self) -> None:
        context = self.prepare_allowed_context()
        fixture_root = self.root / "fixtures"
        fixture_root.mkdir()
        fixture_file = fixture_root / "node.json"
        fixture_file.write_text("{}", encoding="utf-8")

        missing_marker = self.runtime.execute_fixture(
            context["id"], fixture_root=fixture_root, fixture_file=fixture_file
        )
        self.assertEqual("blocked_fixture_boundary", missing_marker["status"])

        self.write_fixture_marker(fixture_root)
        (fixture_root / ".git").mkdir()
        git_root = self.runtime.execute_fixture(
            context["id"], fixture_root=fixture_root, fixture_file=fixture_file
        )
        self.assertEqual("blocked_fixture_boundary", git_root["status"])

        (fixture_root / ".git").rmdir()
        outside_file = self.root / "outside.json"
        outside_file.write_text("{}", encoding="utf-8")
        escaped = self.runtime.execute_fixture(
            context["id"], fixture_root=fixture_root, fixture_file=outside_file
        )
        self.assertEqual("blocked_fixture_boundary", escaped["status"])

    def test_fixture_execution_creates_candidate_without_promoting_contract_or_schedule(self) -> None:
        context = self.prepare_allowed_context()
        fixture_root, fixture_file = self.write_fixture(
            context,
            content={"scope": "fixture requirement analysis"},
        )

        result = self.runtime.execute_fixture(
            context["id"], fixture_root=fixture_root, fixture_file=fixture_file
        )

        candidate = result["fixture_contract_candidate"]
        self.assertEqual("succeeded_fixture", result["status"])
        self.assertTrue(result["fixture_only"])
        self.assertFalse(result["business_valid"])
        self.assertFalse(result["promotion_enabled"])
        self.assertEqual("RequirementContract", candidate["schema_name"])
        self.assertEqual("product_analyst", candidate["producer"])
        self.assertEqual("fixture_contract_candidate", candidate["status"])
        latest = database.get_latest_contract_artifact(self.plan_id, "requirement_analysis")
        self.assertEqual("planned", latest["status"])
        refreshed = self.scheduler.get_schedule(self.schedule_id)
        self.assertEqual(
            "running_simulated",
            states_by_node(refreshed)["requirement_analysis"]["state"],
        )

    def test_denied_tool_blocks_fixture_execution(self) -> None:
        context = self.runtime.prepare_context(
            self.schedule_id,
            "requirement_analysis",
            requested_tools=("git_push",),
        )
        fixture_root, fixture_file = self.write_fixture(context, requested_tools=("git_push",))

        result = self.runtime.execute_fixture(
            context["id"], fixture_root=fixture_root, fixture_file=fixture_file
        )

        self.assertEqual("blocked_policy", result["status"])
        self.assertEqual({}, result["fixture_contract_candidate"])

    def test_fixture_execution_is_idempotent(self) -> None:
        context = self.prepare_allowed_context()
        fixture_root, fixture_file = self.write_fixture(context)

        first = self.runtime.execute_fixture(
            context["id"], fixture_root=fixture_root, fixture_file=fixture_file
        )
        second = self.runtime.execute_fixture(
            context["id"], fixture_root=fixture_root, fixture_file=fixture_file
        )

        self.assertEqual(first["id"], second["id"])
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(1, len(database.list_dynamic_node_executions(context_id=context["id"])))

    def test_checkpoint_drift_marks_old_context_stale(self) -> None:
        context = self.prepare_allowed_context()
        fixture_root, fixture_file = self.write_fixture(context)
        self.scheduler.advance(
            self.schedule_id,
            {
                "event_id": "context-drift",
                "node_id": "requirement_analysis",
                "outcome": "success",
                "elapsed_seconds": 1,
                "input_tokens": 1,
                "output_tokens": 1,
            },
        )

        result = self.runtime.execute_fixture(
            context["id"], fixture_root=fixture_root, fixture_file=fixture_file
        )

        self.assertEqual("blocked_stale_context", result["status"])
        self.assertEqual("stale", self.runtime.get_context(context["id"])["status"])

    def test_credential_fields_are_rejected_from_candidate(self) -> None:
        context = self.prepare_allowed_context()
        fixture_root, fixture_file = self.write_fixture(
            context,
            content={"password": "must-not-be-stored"},
        )

        result = self.runtime.execute_fixture(
            context["id"], fixture_root=fixture_root, fixture_file=fixture_file
        )

        self.assertEqual("blocked_fixture_content", result["status"])
        self.assertNotIn("must-not-be-stored", json.dumps(result, ensure_ascii=False))

    def test_runtime_outputs_are_explicitly_fixture_only(self) -> None:
        context = self.prepare_allowed_context()
        files = write_node_runtime_outputs(self.root / "outputs", {"context": context})
        rendered = "\n".join(path.read_text(encoding="utf-8") for path in files)

        self.assertTrue(all(path.exists() for path in files))
        self.assertIn("fixture_only", rendered)
        self.assertIn("不代表业务完成", rendered)
        self.assertNotIn("business_valid\": true", rendered)

    def test_node_runtime_self_check_is_repeatable_with_retained_database(self) -> None:
        output_dir = self.root / "self_check"

        first = run_node_runtime_checks(output_dir=output_dir)
        second = run_node_runtime_checks(output_dir=output_dir)

        self.assertTrue(all(item["status"] == "pass" for item in first))
        self.assertTrue(all(item["status"] == "pass" for item in second))

    def prepare_allowed_context(self) -> dict:
        return self.runtime.prepare_context(
            self.schedule_id,
            "requirement_analysis",
            requested_tools=("read_artifacts",),
        )

    def write_fixture(
        self,
        context: dict,
        *,
        content: dict | None = None,
        requested_tools: tuple[str, ...] = ("read_artifacts",),
    ) -> tuple[Path, Path]:
        fixture_root = self.root / "fixtures"
        fixture_root.mkdir(exist_ok=True)
        self.write_fixture_marker(fixture_root)
        fixture_file = fixture_root / f"node-{context['id']}.json"
        fixture_file.write_text(
            json.dumps(
                {
                    "schema_version": "1.0-fixture-node-input",
                    "fixture_only": True,
                    "context_hash": context["envelope_hash"],
                    "requested_tools": list(requested_tools),
                    "contract_content": content or {"scope": "fixture"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return fixture_root, fixture_file

    @staticmethod
    def write_fixture_marker(fixture_root: Path) -> None:
        (fixture_root / ".harness-fixture-root.json").write_text(
            json.dumps({"schema_version": "1.0", "fixture_only": True}),
            encoding="utf-8",
        )

    def register_plan(self) -> int:
        request = DynamicPlanningRequest(
            requirement_id="DFHIS-NODE-RUNTIME",
            title="fixture 节点运行时",
            demand_text="使用脱敏 fixture 验证节点契约。",
            signals=PlanningSignals(
                affected_layers=("frontend",),
                estimated_file_count=2,
                evidence_status="complete",
                allowed_paths={"frontend": ("src/views/register.vue",)},
            ),
        )
        plan = build_dynamic_plan(request, enabled=True)
        return int(self.registry.register_plan(plan.to_dict())["plan_id"])


class ControlledNodeRuntimeCliTests(unittest.TestCase):
    def test_task_manager_cli_prepares_executes_and_shows_fixture_node(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "harness.sqlite"
            plan_file = root / "plan.json"
            plan = build_dynamic_plan(
                DynamicPlanningRequest(
                    requirement_id="DFHIS-NODE-RUNTIME-CLI",
                    title="fixture CLI",
                    demand_text="验证 fixture CLI。",
                    signals=PlanningSignals(
                        affected_layers=("frontend",),
                        estimated_file_count=1,
                        evidence_status="complete",
                        allowed_paths={"frontend": ("src/query.vue",)},
                    ),
                ),
                enabled=True,
            )
            plan_file.write_text(json.dumps(plan.to_dict(), ensure_ascii=False), encoding="utf-8")
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
            prepared = run_task_manager(
                [
                    "prepare-dynamic-node-context",
                    "--schedule-id",
                    str(schedule_id),
                    "--node-id",
                    "requirement_analysis",
                    "--requested-tool",
                    "read_artifacts",
                    "--output-dir",
                    str(output_dir),
                    "--json",
                ],
                env,
            )
            prepared_payload = json.loads(prepared.stdout)
            context = prepared_payload["context"]
            fixture_root = root / "fixtures"
            fixture_root.mkdir()
            (fixture_root / ".harness-fixture-root.json").write_text(
                json.dumps({"schema_version": "1.0", "fixture_only": True}),
                encoding="utf-8",
            )
            fixture_file = fixture_root / "node.json"
            fixture_file.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0-fixture-node-input",
                        "fixture_only": True,
                        "context_hash": context["envelope_hash"],
                        "requested_tools": ["read_artifacts"],
                        "contract_content": {"scope": "fixture CLI"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            executed = run_task_manager(
                [
                    "execute-fixture-node",
                    "--context-id",
                    str(context["id"]),
                    "--fixture-root",
                    str(fixture_root),
                    "--fixture-file",
                    str(fixture_file),
                    "--output-dir",
                    str(output_dir),
                    "--json",
                ],
                env,
            )
            execution = json.loads(executed.stdout)["execution"]
            shown = run_task_manager(
                [
                    "show-fixture-node-execution",
                    "--execution-id",
                    str(execution["id"]),
                    "--output-dir",
                    str(output_dir),
                    "--json",
                ],
                env,
            )

            self.assertEqual(0, registered.returncode, registered.stderr)
            self.assertEqual(0, started.returncode, started.stderr)
            self.assertEqual(0, prepared.returncode, prepared.stderr)
            self.assertEqual("succeeded_fixture", execution["status"])
            self.assertEqual("succeeded_fixture", json.loads(shown.stdout)["execution"]["status"])
            self.assertTrue((output_dir / "dynamic_node_context.json").exists())
            self.assertTrue((output_dir / "fixture_node_execution.json").exists())
            self.assertTrue((output_dir / "fixture_contract_candidate.json").exists())


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


if __name__ == "__main__":
    unittest.main()
