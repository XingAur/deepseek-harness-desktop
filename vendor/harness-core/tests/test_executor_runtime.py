from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import database
from app.dynamic_plan_registry import DynamicPlanRegistry
from app.dynamic_planning import DynamicPlanningRequest, PlanningSignals, build_dynamic_plan
from app.dynamic_scheduler import DynamicDryRunScheduler
from app.executor_runtime import (
    SANDBOX_EXECUTOR_SCHEMA_VERSION,
    SandboxExecutorRuntime,
    write_executor_runtime_outputs,
)
from app.node_runtime import ControlledNodeRuntime
from tools.self_check import run_sandbox_executor_checks


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SandboxExecutorRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = self.root / "harness.sqlite"
        database.init_db()
        self.registry = DynamicPlanRegistry()
        self.scheduler = DynamicDryRunScheduler()
        self.node_runtime = ControlledNodeRuntime()
        self.runtime = SandboxExecutorRuntime()
        self.plan_id = self.register_plan()
        self.schedule = self.scheduler.start(self.plan_id)
        self.schedule_id = int(self.schedule["schedule"]["id"])
        self.context = self.node_runtime.prepare_context(
            self.schedule_id,
            "requirement_analysis",
            requested_tools=("read_artifacts",),
        )

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_lease_is_context_bound_persistent_and_idempotent(self) -> None:
        first = self.issue_lease()
        second = self.issue_lease()

        self.assertEqual(
            SANDBOX_EXECUTOR_SCHEMA_VERSION,
            database.get_schema_meta("sandbox_executor_runtime"),
        )
        self.assertEqual(first["id"], second["id"])
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertTrue(first["hash_valid"])
        self.assertEqual(self.context["envelope_hash"], first["context_hash"])
        self.assertEqual(["read_artifacts"], first["capabilities"])
        self.assertEqual(1, first["max_uses"])
        self.assertEqual(0, first["use_count"])
        self.assertEqual("issued", first["status"])

    def test_lease_rejects_capability_escalation_and_invalid_ttl(self) -> None:
        with self.assertRaisesRegex(ValueError, "capability"):
            self.runtime.issue_lease(
                self.context["id"],
                capabilities=("git_push",),
                ttl_seconds=60,
            )
        with self.assertRaisesRegex(ValueError, "TTL"):
            self.runtime.issue_lease(
                self.context["id"],
                capabilities=("read_artifacts",),
                ttl_seconds=0,
            )
        with self.assertRaisesRegex(ValueError, "TTL"):
            self.runtime.issue_lease(
                self.context["id"],
                capabilities=("read_artifacts",),
                ttl_seconds=301,
            )

    def test_fixed_worker_success_consumes_lease_without_promoting_business_state(self) -> None:
        lease = self.issue_lease()
        fixture_root, fixture_file = self.write_fixture()

        result = self.runtime.execute(
            lease["id"],
            fixture_root=fixture_root,
            fixture_file=fixture_file,
            timeout_seconds=1,
        )

        candidate = result["sandbox_fixture_contract_candidate"]
        self.assertEqual("succeeded_sandbox_fixture", result["status"])
        self.assertEqual("sandbox_fixture_contract_candidate", candidate["status"])
        self.assertEqual("RequirementContract", candidate["schema_name"])
        self.assertTrue(result["candidate_hash_valid"])
        self.assertFalse(result["business_valid"])
        self.assertFalse(result["promotion_enabled"])
        consumed = self.runtime.get_lease(lease["id"])
        self.assertEqual("consumed", consumed["status"])
        self.assertEqual(1, consumed["use_count"])
        latest = database.get_latest_contract_artifact(self.plan_id, "requirement_analysis")
        self.assertEqual("planned", latest["status"])
        state = states_by_node(self.scheduler.get_schedule(self.schedule_id))["requirement_analysis"]
        self.assertEqual("running_simulated", state["state"])

    def test_same_lease_and_fixture_is_idempotent_but_other_fixture_is_blocked(self) -> None:
        lease = self.issue_lease()
        fixture_root, fixture_file = self.write_fixture(content={"scope": "first"})
        first = self.runtime.execute(
            lease["id"], fixture_root=fixture_root, fixture_file=fixture_file, timeout_seconds=1
        )
        repeated = self.runtime.execute(
            lease["id"], fixture_root=fixture_root, fixture_file=fixture_file, timeout_seconds=1
        )
        _, other_file = self.write_fixture(filename="other.json", content={"scope": "second"})
        other = self.runtime.execute(
            lease["id"], fixture_root=fixture_root, fixture_file=other_file, timeout_seconds=1
        )

        self.assertEqual(first["id"], repeated["id"])
        self.assertTrue(repeated["idempotent"])
        self.assertEqual("blocked_lease_consumed", other["status"])
        self.assertEqual({}, other["sandbox_fixture_contract_candidate"])

    def test_expired_lease_is_blocked_without_worker_call(self) -> None:
        lease = self.issue_lease()
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with database.connect() as conn:
            conn.execute(
                "update harness_capability_leases set expires_at = ? where id = ?",
                (expired, lease["id"]),
            )
        fixture_root, fixture_file = self.write_fixture()

        result = self.runtime.execute(
            lease["id"], fixture_root=fixture_root, fixture_file=fixture_file, timeout_seconds=1
        )

        self.assertEqual("blocked_lease_integrity", result["status"])
        self.assertEqual(0, self.runtime.get_lease(lease["id"])["use_count"])

    def test_worker_failure_is_isolated_and_lease_remains_consumed(self) -> None:
        lease = self.issue_lease()
        fixture_root, fixture_file = self.write_fixture(worker_behavior="failure")

        result = self.runtime.execute(
            lease["id"], fixture_root=fixture_root, fixture_file=fixture_file, timeout_seconds=1
        )

        self.assertEqual("failed_adapter", result["status"])
        self.assertEqual("fixture_worker_failure", result["error_code"])
        self.assertEqual("consumed", self.runtime.get_lease(lease["id"])["status"])
        self.assertEqual({}, result["sandbox_fixture_contract_candidate"])

    def test_worker_timeout_isolated_without_retry(self) -> None:
        lease = self.issue_lease()
        fixture_root, fixture_file = self.write_fixture(
            worker_behavior="sleep",
            sleep_seconds=0.25,
        )

        result = self.runtime.execute(
            lease["id"], fixture_root=fixture_root, fixture_file=fixture_file, timeout_seconds=0.05
        )

        self.assertEqual("blocked_adapter_timeout", result["status"])
        self.assertEqual(1, self.runtime.get_lease(lease["id"])["use_count"])

    def test_worker_protocol_error_is_blocked(self) -> None:
        lease = self.issue_lease()
        fixture_root, fixture_file = self.write_fixture(worker_behavior="protocol_error")

        result = self.runtime.execute(
            lease["id"], fixture_root=fixture_root, fixture_file=fixture_file, timeout_seconds=1
        )

        self.assertEqual("blocked_adapter_protocol", result["status"])
        self.assertNotIn("Traceback", json.dumps(result, ensure_ascii=False))

    def test_worker_usage_over_role_budget_is_blocked(self) -> None:
        lease = self.issue_lease()
        fixture_root, fixture_file = self.write_fixture(
            usage={"input_tokens": 12001, "output_tokens": 1},
        )

        result = self.runtime.execute(
            lease["id"], fixture_root=fixture_root, fixture_file=fixture_file, timeout_seconds=1
        )

        self.assertEqual("blocked_adapter_budget", result["status"])
        self.assertEqual({}, result["sandbox_fixture_contract_candidate"])

    def test_worker_does_not_inherit_parent_secret_environment(self) -> None:
        lease = self.issue_lease()
        fixture_root, fixture_file = self.write_fixture(assert_env_absent="HARNESS_SECRET_SENTINEL")
        previous = os.environ.get("HARNESS_SECRET_SENTINEL")
        os.environ["HARNESS_SECRET_SENTINEL"] = "must-not-be-inherited"
        try:
            result = self.runtime.execute(
                lease["id"], fixture_root=fixture_root, fixture_file=fixture_file, timeout_seconds=1
            )
        finally:
            if previous is None:
                os.environ.pop("HARNESS_SECRET_SENTINEL", None)
            else:
                os.environ["HARNESS_SECRET_SENTINEL"] = previous

        self.assertEqual("succeeded_sandbox_fixture", result["status"])
        self.assertNotIn("must-not-be-inherited", json.dumps(result, ensure_ascii=False))

    def test_executor_outputs_are_explicitly_sandbox_fixture_only(self) -> None:
        lease = self.issue_lease()
        lease_files = write_executor_runtime_outputs(self.root / "outputs", {"lease": lease})
        fixture_root, fixture_file = self.write_fixture()
        execution = self.runtime.execute(
            lease["id"], fixture_root=fixture_root, fixture_file=fixture_file, timeout_seconds=1
        )
        execution_files = write_executor_runtime_outputs(
            self.root / "outputs", {"execution": execution}
        )
        rendered = "\n".join(
            path.read_text(encoding="utf-8") for path in (*lease_files, *execution_files)
        )

        self.assertIn("sandbox_fixture", rendered)
        self.assertIn("不代表业务完成", rendered)
        self.assertNotIn('"business_valid": true', rendered)

    def test_sandbox_executor_self_check_is_repeatable_with_retained_database(self) -> None:
        output_dir = self.root / "self_check"

        first = run_sandbox_executor_checks(output_dir=output_dir)
        second = run_sandbox_executor_checks(output_dir=output_dir)

        self.assertTrue(all(item["status"] == "pass" for item in first))
        self.assertTrue(all(item["status"] == "pass" for item in second))

    def issue_lease(self) -> dict:
        return self.runtime.issue_lease(
            self.context["id"],
            capabilities=("read_artifacts",),
            ttl_seconds=60,
        )

    def write_fixture(
        self,
        *,
        filename: str = "node.json",
        content: dict | None = None,
        worker_behavior: str = "success",
        sleep_seconds: float = 0,
        usage: dict | None = None,
        assert_env_absent: str = "",
    ) -> tuple[Path, Path]:
        fixture_root = self.root / "fixtures"
        fixture_root.mkdir(exist_ok=True)
        (fixture_root / ".harness-fixture-root.json").write_text(
            json.dumps({"schema_version": "1.0", "fixture_only": True}),
            encoding="utf-8",
        )
        fixture_file = fixture_root / filename
        fixture_file.write_text(
            json.dumps(
                {
                    "schema_version": "1.0-fixture-node-input",
                    "fixture_only": True,
                    "context_hash": self.context["envelope_hash"],
                    "requested_tools": ["read_artifacts"],
                    "contract_content": content or {"scope": "sandbox fixture"},
                    "worker_behavior": worker_behavior,
                    "sleep_seconds": sleep_seconds,
                    "usage": usage or {"input_tokens": 1, "output_tokens": 1},
                    "assert_env_absent": assert_env_absent,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return fixture_root, fixture_file

    def register_plan(self) -> int:
        request = DynamicPlanningRequest(
            requirement_id="DFHIS-SANDBOX-EXECUTOR",
            title="sandbox fixture executor",
            demand_text="通过固定 worker 验证节点执行协议。",
            signals=PlanningSignals(
                affected_layers=("frontend",),
                estimated_file_count=2,
                evidence_status="complete",
                allowed_paths={"frontend": ("src/query.vue",)},
            ),
        )
        plan = build_dynamic_plan(request, enabled=True)
        return int(self.registry.register_plan(plan.to_dict())["plan_id"])


class SandboxExecutorRuntimeCliTests(unittest.TestCase):
    def test_task_manager_cli_issues_shows_and_executes_sandbox_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "harness.sqlite"
            plan_file = root / "plan.json"
            request = DynamicPlanningRequest(
                requirement_id="DFHIS-SANDBOX-CLI",
                title="sandbox CLI",
                demand_text="验证固定 worker CLI。",
                signals=PlanningSignals(
                    affected_layers=("frontend",),
                    estimated_file_count=1,
                    evidence_status="complete",
                    allowed_paths={"frontend": ("src/query.vue",)},
                ),
            )
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
            prepared = run_task_manager(
                [
                    "prepare-dynamic-node-context", "--schedule-id", str(schedule_id),
                    "--node-id", "requirement_analysis", "--requested-tool", "read_artifacts",
                    "--output-dir", str(output_dir), "--json",
                ],
                env,
            )
            context = json.loads(prepared.stdout)["context"]
            issued = run_task_manager(
                [
                    "issue-fixture-capability-lease", "--context-id", str(context["id"]),
                    "--capability", "read_artifacts", "--ttl-seconds", "60",
                    "--output-dir", str(output_dir), "--json",
                ],
                env,
            )
            lease = json.loads(issued.stdout)["lease"]
            shown = run_task_manager(
                [
                    "show-fixture-capability-lease", "--lease-id", str(lease["id"]),
                    "--output-dir", str(output_dir), "--json",
                ],
                env,
            )
            fixture_root = root / "fixtures"
            fixture_root.mkdir()
            (fixture_root / ".harness-fixture-root.json").write_text(
                json.dumps({"schema_version": "1.0", "fixture_only": True}), encoding="utf-8"
            )
            fixture_file = fixture_root / "node.json"
            fixture_file.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0-fixture-node-input",
                        "fixture_only": True,
                        "context_hash": context["envelope_hash"],
                        "requested_tools": ["read_artifacts"],
                        "contract_content": {"scope": "sandbox CLI"},
                        "worker_behavior": "success",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            executed = run_task_manager(
                [
                    "execute-sandbox-fixture-node", "--lease-id", str(lease["id"]),
                    "--fixture-root", str(fixture_root), "--fixture-file", str(fixture_file),
                    "--timeout-seconds", "1", "--output-dir", str(output_dir), "--json",
                ],
                env,
            )

            self.assertEqual("issued", json.loads(shown.stdout)["lease"]["status"])
            self.assertEqual(
                "succeeded_sandbox_fixture",
                json.loads(executed.stdout)["execution"]["status"],
            )
            self.assertTrue((output_dir / "fixture_capability_lease.json").exists())
            self.assertTrue((output_dir / "sandbox_node_execution.json").exists())


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
