from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app import database
from app.harness import RequirementWorkflowRunner
from app.precommit_verifier import build_verification_matrix
from app.run_scheduler import RunScheduler
from app.runtime_preflight import run_runtime_preflight


class EndToEndReliabilityTests(unittest.TestCase):
    """Deterministic replay of the local reliability boundaries.

    These tests deliberately stay local: they prove fallback, warning,
    background failure and verification gating without pretending to validate
    a hospital runtime or perform an external provider write.
    """

    def test_bad_control_database_degrades_without_mutation(self) -> None:
        report = run_runtime_preflight(
            database_path="/proc/harness.sqlite",
            mutation_requested=False,
        )
        self.assertEqual("degraded_readonly", report["status"])
        self.assertIn("database", report["failed_checks"])
        self.assertTrue(report["read_only"])

    def test_bad_project_path_still_returns_readonly_evidence_warning(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            previous_db_path = database.DB_PATH
            database.DB_PATH = Path(root) / "harness.sqlite"
            try:
                result = RequirementWorkflowRunner(mode="mock", allow_mock=True).run(
                    title="坏项目路径回放",
                    demand_text="分析一个不存在项目的需求，保持只读。",
                    project_path=Path(root) / "does-not-exist",
                    yunxiao_output_dir=Path(root) / "outputs",
                )
                artifacts = database.get_artifacts(result.run_id)
            finally:
                database.DB_PATH = previous_db_path
            self.assertEqual("success", result.status)
            warnings = [
                artifact
                for artifact in artifacts
                if artifact.get("kind") == "evidence_warnings_json"
            ]
            self.assertEqual(1, len(warnings))
            self.assertIn("project_context_unavailable", warnings[0]["content"])

    def test_background_worker_failure_is_terminal_and_recoverable(self) -> None:
        scheduler = RunScheduler(max_workers=1)
        try:
            with mock.patch(
                "app.harness.RequirementWorkflowRunner",
                side_effect=OSError("temporary runner failure"),
            ):
                job_id = scheduler.submit(
                    title="后台失败回放",
                    demand_text="验证后台失败会形成可恢复记录。",
                )
                deadline = time.time() + 5
                record = scheduler.get(job_id)
                while record and record["status"] not in {"failed", "success"} and time.time() < deadline:
                    time.sleep(0.01)
                    record = scheduler.get(job_id)
            self.assertIsNotNone(record)
            self.assertEqual("failed", record["status"])
            self.assertEqual("failed", record["stage"])
            self.assertTrue(record["recovery_action"])
        finally:
            scheduler._executor.shutdown(wait=True)

    def test_baseline_failure_stays_outside_modify_gate(self) -> None:
        matrix = build_verification_matrix(
            status="success",
            summary="基线命令失败",
            targets=[
                {
                    "name": "repo",
                    "status": "success",
                    "verification_status": "baseline_failed",
                }
            ],
        )
        self.assertEqual("baseline_failed", matrix["verification_status"])
        self.assertFalse(matrix["can_commit"])


if __name__ == "__main__":
    unittest.main()
