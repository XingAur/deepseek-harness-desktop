from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app import database
from app.task_manager import (
    TaskCreateOptions,
    TaskExistingRunOptions,
    TaskManager,
    TaskManualVerificationOptions,
    read_existing_output_summary,
    stage_for_execution_mode,
    load_structured_evidence_file,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TaskManagerCoreClosureCliTests(unittest.TestCase):
    def test_multi_service_evidence_file_is_structured_and_rejects_non_object(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "evidence.json"
            path.write_text('{"runtime_validation": {"status": "ready"}}', encoding="utf-8")
            self.assertEqual({"runtime_validation": {"status": "ready"}}, load_structured_evidence_file(str(path)))
            path.write_text("[]", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_structured_evidence_file(str(path))

    def test_register_run_accepts_core_closure_trial_mode(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "tools/task_manager.py",
                "register-run",
                "--execution-mode",
                "core-closure-trial",
                "--help",
            ],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_core_closure_trial_has_stable_task_stage(self) -> None:
        self.assertEqual(stage_for_execution_mode("core-closure-trial"), "core_closure_trial")

    def test_auto_local_has_stable_task_stage(self) -> None:
        self.assertEqual(stage_for_execution_mode("auto-local"), "auto_local")

    def test_manual_runtime_verification_keeps_source_gate_and_commit_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            previous_db_path = database.DB_PATH
            database.DB_PATH = root / "harness.sqlite"
            try:
                manager = TaskManager()
                task = manager.create_task(
                    TaskCreateOptions(
                        title="DFHIS-31551",
                        entity_kind="requirement",
                        entity_id="DFHIS-31551",
                    )
                )
                source_run_id = database.create_run(
                    team_key="his-harness-default-team",
                    title="DFHIS-31551 source gate",
                    source_type="yunxiao",
                    demand_text="sortField",
                    total_steps=0,
                )
                database.update_run(
                    source_run_id,
                    status="blocked",
                    evaluation_status="source_contract_missing",
                    evaluation_summary="local server source unavailable",
                    finished_at=database.now_iso(),
                )
                source_task_run_id = database.add_task_run(
                    {
                        "task_id": task["id"],
                        "run_id": source_run_id,
                        "stage": "core_closure_trial",
                        "execution_mode": "core-closure-trial",
                        "status": "blocked",
                        "evaluation_status": "source_contract_missing",
                        "verification_status": "blocked",
                        "output_dir": str(root / "source"),
                        "summary": "local server source unavailable",
                    }
                )

                recorded_task, recorded_run, output_dir = manager.record_manual_verification(
                    TaskManualVerificationOptions(
                        task_id=int(task["id"]),
                        source_task_run_id=source_task_run_id,
                        status="passed",
                        verifier="user",
                        summary="用户已在真实环境验证默认排序生效。",
                        scenarios=["多字段默认排序分页查询"],
                        output_root=str(root / "outputs"),
                    )
                )

                evidence = json.loads((output_dir / "manual_runtime_verification.json").read_text(encoding="utf-8"))
                self.assertEqual(recorded_task["status"], "manual_verified")
                self.assertEqual(recorded_task["verification_status"], "manual_passed")
                self.assertFalse(recorded_task["can_commit"])
                self.assertEqual(recorded_run["execution_mode"], "manual-runtime-verification")
                self.assertEqual(evidence["source_task_run_id"], source_task_run_id)
                self.assertTrue(evidence["safety_boundaries"]["does_not_override_source_contract_gate"])
                self.assertTrue(evidence["safety_boundaries"]["does_not_enable_auto_apply"])
                self.assertFalse(evidence["safety_boundaries"]["can_commit"])
                source_after = database.get_task_run(source_task_run_id)
                self.assertEqual(source_after["status"], "blocked")
            finally:
                database.DB_PATH = previous_db_path

    def test_existing_core_closure_output_keeps_passed_technical_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            (output_dir / "run.json").write_text(
                """{
  "run": {
    "title": "DFHIS-31557 core closure",
    "demand_text": "default parity",
    "status": "success",
    "evaluation_status": "ready_for_manual_review",
    "evaluation_summary": "core closure ready"
  },
  "artifacts": [
    {
      "kind": "core_diff_review_json",
      "content": "{\\"status\\": \\"pass\\"}"
    }
  ]
}
""",
                encoding="utf-8",
            )

            summary = read_existing_output_summary(output_dir)

        self.assertEqual(summary["status"], "success")
        self.assertEqual(summary["evaluation_status"], "ready_for_manual_review")
        self.assertEqual(summary["verification_status"], "passed")
        self.assertFalse(summary["can_commit"])

    def test_register_existing_run_reuses_matching_source_run_for_manual_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            previous_db_path = database.DB_PATH
            database.DB_PATH = root / "harness.sqlite"
            try:
                manager = TaskManager()
                source_run_id = database.create_run(
                    team_key="his-harness-default-team",
                    title="DFHIS-31528 挂号病人查询切换标签页不要刷新",
                    source_type="manual",
                    demand_text="切换顶部业务页签后保留查询条件和结果。",
                    total_steps=0,
                )
                database.update_run(
                    source_run_id,
                    status="success",
                    evaluation_status="ready_for_manual_review",
                    evaluation_summary="core closure ready",
                    finished_at=database.now_iso(),
                )
                output_dir = root / "run"
                output_dir.mkdir()
                (output_dir / "run.json").write_text(
                    json.dumps(
                        {
                            "run": database.get_run(source_run_id),
                            "artifacts": [{"kind": "core_diff_review_json", "content": {"status": "pass"}}],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                task, task_run = manager.record_existing_run(
                    TaskExistingRunOptions(
                        title="DFHIS-31528 挂号病人查询切换标签页不要刷新",
                        entity_kind="requirement",
                        entity_id="DFHIS-31528",
                        output_dir=str(output_dir),
                        execution_mode="auto-local",
                    )
                )
                _, manual_run, _ = manager.record_manual_verification(
                    TaskManualVerificationOptions(
                        task_id=int(task["id"]),
                        source_run_id=source_run_id,
                        summary="用户已验证切换顶部业务页签后查询状态未被刷新。",
                    )
                )

                self.assertEqual(source_run_id, task_run["run_id"])
                self.assertEqual("manual-runtime-verification", manual_run["execution_mode"])
            finally:
                database.DB_PATH = previous_db_path

    def test_register_existing_run_rejects_source_run_id_that_differs_from_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            previous_db_path = database.DB_PATH
            database.DB_PATH = root / "harness.sqlite"
            try:
                manager = TaskManager()
                output_run_id = database.create_run(
                    team_key="his-harness-default-team",
                    title="DFHIS-31528 output",
                    source_type="manual",
                    demand_text="output",
                    total_steps=0,
                )
                other_run_id = database.create_run(
                    team_key="his-harness-default-team",
                    title="other run",
                    source_type="manual",
                    demand_text="other",
                    total_steps=0,
                )
                output_dir = root / "run"
                output_dir.mkdir()
                (output_dir / "run.json").write_text(
                    json.dumps(
                        {
                            "run": {**database.get_run(output_run_id), "status": "success", "evaluation_status": "ready_for_manual_review"},
                            "artifacts": [{"kind": "core_diff_review_json", "content": {"status": "pass"}}],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                with self.assertRaisesRegex(ValueError, "不一致"):
                    manager.record_existing_run(
                        TaskExistingRunOptions(
                            title="DFHIS-31528 output",
                            output_dir=str(output_dir),
                            source_run_id=other_run_id,
                        )
                    )
            finally:
                database.DB_PATH = previous_db_path

    def test_register_existing_run_rejects_source_run_id_without_output_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            previous_db_path = database.DB_PATH
            database.DB_PATH = root / "harness.sqlite"
            try:
                manager = TaskManager()
                source_run_id = database.create_run(
                    team_key="his-harness-default-team",
                    title="source run",
                    source_type="manual",
                    demand_text="source",
                    total_steps=0,
                )
                output_dir = root / "legacy-output"
                output_dir.mkdir()

                with self.assertRaisesRegex(ValueError, "原始 run.id"):
                    manager.record_existing_run(
                        TaskExistingRunOptions(
                            title="legacy output",
                            output_dir=str(output_dir),
                            source_run_id=source_run_id,
                        )
                    )
            finally:
                database.DB_PATH = previous_db_path
