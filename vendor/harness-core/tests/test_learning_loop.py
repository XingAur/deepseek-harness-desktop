from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app import database
from app.learning_candidate_repository import LearningCandidateRepository
from app.learning_loop import (
    derive_learning_candidates,
    persist_learning_candidates,
    persist_manager_learning_candidates,
)
from app.manager_provider_repository import ManagerProviderRepository


class LearningLoopTests(unittest.TestCase):
    def test_failure_sample_becomes_inert_candidates_without_auto_promote(self) -> None:
        sample = {
            "run_id": "run-123",
            "task_key": "DFHIS-31333",
            "failure_kind": "verification_failed",
            "summary": "金额汇总规则缺少按明细四舍五入后的回归样本。",
            "evidence_refs": [
                "runs/run-123/verification_matrix.json",
                "runs/run-123/diff_review.md",
            ],
            "scope": {
                "module": "门诊收费",
                "repo": "df-web-guahaosf",
            },
        }

        result = derive_learning_candidates(sample)

        self.assertEqual("his-learning-candidates.v1", result["schema_version"])
        self.assertFalse(result["changed"])
        self.assertFalse(result["auto_promote"])
        self.assertEqual("not_written", result["persistence"])
        self.assertEqual(
            ["eval.sample", "contract_plugin.draft", "rule_pack.draft", "knowledge.candidate"],
            [item["kind"] for item in result["candidates"]],
        )
        self.assertTrue(all(item["status"] == "candidate" for item in result["candidates"]))
        self.assertTrue(all(item["promotion_allowed"] is False for item in result["candidates"]))
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertIn("DFHIS-31333", rendered)
        self.assertNotIn("auto_promote=true", rendered)

    def test_learning_candidate_rejects_secret_shaped_evidence(self) -> None:
        sample = {
            "run_id": "run-124",
            "failure_kind": "verification_failed",
            "summary": "失败样本含凭证形态证据。",
            "evidence_refs": ["Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"],
        }

        with self.assertRaisesRegex(ValueError, "sensitive evidence"):
            derive_learning_candidates(sample)

    def test_failed_sample_can_be_persisted_as_reviewable_candidates(self) -> None:
        sample = {
            "run_id": "run-125",
            "task_key": "DFHIS-31351",
            "failure_kind": "contract_regression",
            "summary": "日报操作员全部选项缺少回放样本。",
            "evidence_refs": ["runs/run-125/replay.json"],
            "scope": {"module": "日报", "repo": "df-web-yewugymk"},
        }
        with TemporaryDirectory() as temp_dir:
            result = persist_learning_candidates(sample, Path(temp_dir) / "candidates")

            self.assertTrue(result["changed"])
            self.assertFalse(result["auto_promote"])
            self.assertEqual("written", result["persistence"])
            candidate_set_path = Path(result["candidate_set_path"])
            self.assertTrue(candidate_set_path.is_file())

            stored = json.loads(candidate_set_path.read_text(encoding="utf-8"))
            self.assertEqual("his-learning-candidates.v1", stored["schema_version"])
            self.assertEqual("review_required", stored["review_state"])
            self.assertEqual(4, stored["candidate_count"])
            self.assertTrue(all(item["requires_review"] for item in stored["candidates"]))
            self.assertTrue(all(item["promotion_allowed"] is False for item in stored["candidates"]))

    def test_secret_shaped_sample_is_rejected_before_candidate_directory_is_created(self) -> None:
        sample = {
            "run_id": "run-126",
            "failure_kind": "verification_failed",
            "summary": "失败样本含凭证形态证据。",
            "evidence_refs": ["sk-abcdefghijklmnopqrstuvwxyz123456"],
        }
        with TemporaryDirectory() as temp_dir:
            candidate_home = Path(temp_dir) / "candidates"

            with self.assertRaisesRegex(ValueError, "sensitive evidence"):
                persist_learning_candidates(sample, candidate_home)

            self.assertFalse(candidate_home.exists())

    def test_manager_learning_persistence_uses_database_not_offline_candidate_files(self) -> None:
        sample = {
            "run_id": "run-manager-1",
            "task_key": "DFHIS-31351",
            "failure_kind": "contract_regression",
            "summary": "日报权限回放需要补充审核样本。",
            "evidence_refs": ["runs/run-manager-1/replay.json"],
            "scope": {"module": "日报", "repo": "df-web-yewugymk"},
        }
        with TemporaryDirectory() as temp_dir:
            previous = database.DB_PATH
            database.DB_PATH = Path(temp_dir) / "manager.sqlite"
            try:
                repository = LearningCandidateRepository()
                source_action_audit_id = ManagerProviderRepository().record_action(
                    profile_id=None,
                    action_type="controlled.test.action",
                    status="failed",
                    details={"result": "redacted"},
                )
                result = persist_manager_learning_candidates(
                    sample,
                    repository=repository,
                    source_action_audit_id=source_action_audit_id,
                )
                rendered = json.dumps(result, ensure_ascii=False)
                self.assertEqual("manager_database", result["persistence"])
                self.assertFalse(result["auto_promote"])
                self.assertNotIn("candidate_set_path", result)
                self.assertNotIn("runs/run-manager-1", rendered)
                self.assertEqual(4, len(repository.list_candidates()))
            finally:
                database.DB_PATH = previous


if __name__ == "__main__":
    unittest.main()
