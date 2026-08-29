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
from app.model_invocation_runtime import (
    MODEL_INVOCATION_SCHEMA_VERSION,
    OfflineModelInvocationRuntime,
    write_model_invocation_outputs,
)
from tools.self_check import run_model_invocation_checks


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class OfflineModelInvocationRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = self.root / "harness.sqlite"
        database.init_db()
        self.registry = DynamicPlanRegistry()
        self.scheduler = DynamicDryRunScheduler()
        self.runtime = OfflineModelInvocationRuntime()
        self.plan_id = self.register_plan()
        self.schedule = self.scheduler.start(self.plan_id)
        self.schedule_id = int(self.schedule["schedule"]["id"])
        self.fixture_root = self.root / "fixtures"
        self.fixture_root.mkdir()
        (self.fixture_root / ".harness-fixture-root.json").write_text(
            json.dumps({"schema_version": "1.0", "fixture_only": True}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_mock_invocation_is_structured_persistent_and_candidate_only(self) -> None:
        before = self.scheduler.get_schedule(self.schedule_id)
        result = self.runtime.invoke(
            self.schedule_id,
            "requirement_analysis",
            fixture_root=self.fixture_root,
            mode="mock",
            record_cassette=True,
        )
        after = self.scheduler.get_schedule(self.schedule_id)

        self.assertEqual(MODEL_INVOCATION_SCHEMA_VERSION, database.get_schema_meta("model_invocation_runtime"))
        self.assertEqual("succeeded_fixture", result["invocation"]["status"])
        self.assertEqual("mock", result["invocation"]["mode"])
        self.assertEqual("RequirementContract", result["structured_output"]["contract_name"])
        self.assertTrue(result["structured_output"]["fixture_only"])
        self.assertFalse(result["structured_output"]["business_valid"])
        self.assertEqual("fixture_model_candidate", result["candidate"]["status"])
        self.assertTrue(result["hashes_valid"])
        self.assertTrue(result["cassette"]["recorded"])
        self.assertTrue((self.fixture_root / result["cassette"]["relative_path"]).exists())
        self.assertEqual(before["checkpoint"]["checkpoint_hash"], after["checkpoint"]["checkpoint_hash"])
        self.assertEqual("planned", database.get_latest_contract_artifact(self.plan_id, "requirement_analysis")["status"])
        self.assertEqual(["prepared", "adapter_completed", "validated", "persisted"], [event["event_type"] for event in result["events"]])
        self.assertFalse(result["promotion_enabled"])
        self.assertFalse(result["external_actions_enabled"])

    def test_recorded_mock_cassette_replays_the_same_structured_output(self) -> None:
        mocked = self.runtime.invoke(
            self.schedule_id,
            "requirement_analysis",
            fixture_root=self.fixture_root,
            mode="mock",
            record_cassette=True,
        )
        replayed = self.runtime.invoke(
            self.schedule_id,
            "requirement_analysis",
            fixture_root=self.fixture_root,
            mode="replay",
            cassette_file=self.fixture_root / mocked["cassette"]["relative_path"],
        )

        self.assertEqual("succeeded_fixture", replayed["invocation"]["status"])
        self.assertEqual("replay", replayed["invocation"]["mode"])
        self.assertEqual(mocked["structured_output"], replayed["structured_output"])
        self.assertEqual(mocked["invocation"]["response_hash"], replayed["invocation"]["response_hash"])
        self.assertEqual(2, len(database.list_model_invocations(context_id=mocked["invocation"]["context_id"])))

    def test_identical_invocation_is_idempotent(self) -> None:
        first = self.runtime.invoke(
            self.schedule_id,
            "requirement_analysis",
            fixture_root=self.fixture_root,
            mode="mock",
        )
        repeated = self.runtime.invoke(
            self.schedule_id,
            "requirement_analysis",
            fixture_root=self.fixture_root,
            mode="mock",
        )

        self.assertEqual(first["invocation"]["id"], repeated["invocation"]["id"])
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(4, len(repeated["events"]))

    def test_record_request_is_not_hidden_by_prior_non_recorded_invocation(self) -> None:
        unrecorded = self.runtime.invoke(
            self.schedule_id,
            "requirement_analysis",
            fixture_root=self.fixture_root,
            mode="mock",
        )
        recorded = self.runtime.invoke(
            self.schedule_id,
            "requirement_analysis",
            fixture_root=self.fixture_root,
            mode="mock",
            record_cassette=True,
        )

        self.assertNotEqual(unrecorded["invocation"]["id"], recorded["invocation"]["id"])
        self.assertFalse(unrecorded["cassette"]["recorded"])
        self.assertTrue(recorded["cassette"]["recorded"])

    def test_real_and_unknown_modes_are_rejected_without_credentials(self) -> None:
        for mode in ("openai", "anthropic", "real", "other"):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "mock/replay"):
                self.runtime.invoke(
                    self.schedule_id,
                    "requirement_analysis",
                    fixture_root=self.fixture_root,
                    mode=mode,
                )

    def test_replay_request_hash_mismatch_is_persisted_as_blocked(self) -> None:
        cassette = self.fixture_root / "mismatch.json"
        cassette.write_text(
            json.dumps(
                {
                    "schema_version": "1.0-offline-model-cassette",
                    "fixture_only": True,
                    "request_hash": "sha256:not-the-current-request",
                    "response": self.valid_response(),
                }
            ),
            encoding="utf-8",
        )

        result = self.runtime.invoke(
            self.schedule_id,
            "requirement_analysis",
            fixture_root=self.fixture_root,
            mode="replay",
            cassette_file=cassette,
        )

        self.assertEqual("blocked_replay", result["invocation"]["status"])
        self.assertEqual("cassette_request_hash_mismatch", result["invocation"]["error_code"])
        self.assertFalse(result["hashes_valid"])

    def test_invalid_structured_output_and_credential_fields_are_blocked(self) -> None:
        malformed = self.valid_response()
        malformed["output"].pop("contract_name")
        malformed_result = self.invoke_replay("malformed.json", malformed)
        self.assertEqual("blocked_structured_output", malformed_result["invocation"]["status"])
        self.assertEqual("structured_output_contract_name_invalid", malformed_result["invocation"]["error_code"])

        credential = self.valid_response()
        credential["output"]["content"]["api_key"] = "fixture-secret-value"
        credential_result = self.invoke_replay("credential.json", credential)
        self.assertEqual("blocked_structured_output", credential_result["invocation"]["status"])
        self.assertEqual("credential_field_forbidden", credential_result["invocation"]["error_code"])

    def test_output_evidence_is_explicitly_fixture_only(self) -> None:
        result = self.runtime.invoke(
            self.schedule_id,
            "requirement_analysis",
            fixture_root=self.fixture_root,
            mode="mock",
        )
        files = write_model_invocation_outputs(self.root / "outputs", result)
        rendered = "\n".join(path.read_text(encoding="utf-8") for path in files)

        self.assertEqual(3, len(files))
        self.assertIn("fixture-only", rendered)
        self.assertIn("business_valid", rendered)
        self.assertNotIn('"business_valid": true', rendered)

    def test_model_invocation_self_check_is_repeatable(self) -> None:
        output_dir = self.root / "self_check"
        first = run_model_invocation_checks(output_dir=output_dir)
        second = run_model_invocation_checks(output_dir=output_dir)
        self.assertTrue(all(item["status"] == "pass" for item in first))
        self.assertTrue(all(item["status"] == "pass" for item in second))

    def valid_response(self) -> dict:
        context = self.runtime.build_request(
            self.schedule_id,
            "requirement_analysis",
        )
        return self.runtime.build_mock_response(context["request"])

    def invoke_replay(self, name: str, response: dict) -> dict:
        request = self.runtime.build_request(self.schedule_id, "requirement_analysis")
        cassette = self.fixture_root / name
        cassette.write_text(
            json.dumps(
                {
                    "schema_version": "1.0-offline-model-cassette",
                    "fixture_only": True,
                    "request_hash": request["request_hash"],
                    "response": response,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return self.runtime.invoke(
            self.schedule_id,
            "requirement_analysis",
            fixture_root=self.fixture_root,
            mode="replay",
            cassette_file=cassette,
        )

    def register_plan(self) -> int:
        request = DynamicPlanningRequest(
            requirement_id="DFHIS-MODEL-FIXTURE",
            title="离线模型调用契约",
            demand_text="验证结构化 mock 和 replay，不调用真实模型。",
            signals=PlanningSignals(
                affected_layers=("frontend",),
                estimated_file_count=1,
                evidence_status="complete",
                allowed_paths={"frontend": ("fixture/web/Query.vue",)},
            ),
        )
        plan = build_dynamic_plan(request, enabled=True)
        return int(self.registry.register_plan(plan.to_dict())["plan_id"])


class OfflineModelInvocationRuntimeCliTests(unittest.TestCase):
    def test_task_manager_cli_runs_and_shows_mock_invocation(self) -> None:
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
                requirement_id="DFHIS-MODEL-CLI",
                title="离线模型 CLI",
                demand_text="验证 mock 调用 CLI。",
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
                    "run-model-fixture-node", "--schedule-id", str(schedule_id),
                    "--node-id", "requirement_analysis", "--fixture-root", str(fixture_root),
                    "--mode", "mock", "--record-cassette", "--output-dir", str(output_dir), "--json",
                ],
                env,
            )
            invocation = json.loads(executed.stdout)["invocation"]
            shown = run_task_manager(
                ["show-model-fixture-invocation", "--invocation-id", str(invocation["id"]), "--output-dir", str(output_dir), "--json"],
                env,
            )
            shown_payload = json.loads(shown.stdout)

            self.assertEqual("succeeded_fixture", invocation["status"])
            self.assertEqual(invocation["id"], shown_payload["invocation"]["id"])
            self.assertFalse(shown_payload["business_valid"])
            self.assertTrue((output_dir / "model_fixture_invocation.json").exists())
            self.assertTrue((output_dir / "model_fixture_events.json").exists())


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
