from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from intake import detect_provider, process_intake  # noqa: E402


class HarnessWorkitemIntakeTests(unittest.TestCase):
    def test_routes_yunxiao_and_creates_sanitized_intake_record(self):
        calls = {}

        def fake_collect(**kwargs):
            calls["collect"] = kwargs
            output = Path(kwargs["output_dir"])
            output.mkdir(parents=True, exist_ok=True)
            (output / "requirement_evidence.v2.json").write_text(
                "{}",
                encoding="utf-8",
            )
            return {
                "decision_gate": "ready_for_analysis",
                "completeness": "complete",
            }

        def fake_archive(**kwargs):
            calls["archive"] = kwargs
            run_dir = (
                Path(kwargs["history_root"])
                / "YUNXIAO/DFHIS-90001/runs/20260724-180000"
            )
            run_dir.mkdir(parents=True)
            task_dir = run_dir.parents[1]
            return {
                "task_dir": str(task_dir),
                "run_dir": str(run_dir),
                "evidence_dir": str(run_dir / "evidence"),
                "worktree_dir": str(
                    task_dir / "worktrees/20260724-180000"
                ),
                "decision_gate": "ready_for_analysis",
                "completeness": "complete",
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            history_root = Path(temp_dir) / "HarnessHistory"
            result = process_intake(
                source=(
                    "https://devops.aliyun.com/projex/bug/"
                    "DFHIS-90001?token=must-not-be-archived"
                ),
                history_root=history_root,
                run_id="20260724-180000",
                credential_kind="write",
                credentials_file="/private/credentials.json",
                collect_adapter=fake_collect,
                archive_adapter=fake_archive,
            )

            self.assertEqual(
                ("YUNXIAO", "DFHIS-90001"),
                detect_provider("DFHIS-90001"),
            )
            self.assertEqual("ready_for_analysis", result["status"])
            self.assertEqual("write", calls["collect"]["credential_kind"])
            self.assertEqual("DFHIS-90001", calls["collect"]["source"])
            self.assertEqual(
                "/private/credentials.json",
                calls["collect"]["credentials_file"],
            )
            self.assertEqual("YUNXIAO", calls["archive"]["provider"])

            intake = json.loads(
                (
                    Path(result["run_dir"]) / "intake/request.json"
                ).read_text(encoding="utf-8")
            )
            self.assertNotIn(
                "must-not-be-archived",
                json.dumps(intake, ensure_ascii=False),
            )
            self.assertNotIn(
                "/private/credentials.json",
                json.dumps(intake, ensure_ascii=False),
            )
            self.assertEqual("accepted", intake["intake_status"])
            self.assertEqual("write", intake["credential_kind"])
            self.assertEqual("DFHIS-90001", intake["source"])
            self.assertEqual(
                [],
                list((history_root / ".staging").iterdir()),
            )

    def test_blocks_processing_when_original_requirement_is_incomplete(self):
        stage_calls = []

        def fake_collect(**kwargs):
            output = Path(kwargs["output_dir"])
            output.mkdir(parents=True, exist_ok=True)
            (output / "requirement_evidence.v2.json").write_text(
                "{}",
                encoding="utf-8",
            )
            return {
                "decision_gate": "needs_requirement_confirmation",
                "completeness": "partial",
            }

        def fake_archive(**kwargs):
            run_dir = (
                Path(kwargs["history_root"])
                / "YUNXIAO/DFHIS-90002/runs/20260724-180100"
            )
            run_dir.mkdir(parents=True)
            task_dir = run_dir.parents[1]
            return {
                "task_dir": str(task_dir),
                "run_dir": str(run_dir),
                "evidence_dir": str(run_dir / "evidence"),
                "worktree_dir": str(
                    task_dir / "worktrees/20260724-180100"
                ),
                "decision_gate": "needs_requirement_confirmation",
                "completeness": "partial",
            }

        def fake_stage(**kwargs):
            stage_calls.append(kwargs)
            return kwargs

        with tempfile.TemporaryDirectory() as temp_dir:
            result = process_intake(
                source="DFHIS-90002",
                history_root=Path(temp_dir) / "HarnessHistory",
                run_id="20260724-180100",
                collect_adapter=fake_collect,
                archive_adapter=fake_archive,
                stage_adapter=fake_stage,
            )

            self.assertEqual(
                "needs_requirement_confirmation",
                result["status"],
            )
            self.assertEqual("blocked", result["intake_status"])
            self.assertEqual("analysis", stage_calls[0]["stage"])
            self.assertEqual("blocked", stage_calls[0]["status"])

    def test_rejects_unknown_provider(self):
        with self.assertRaisesRegex(
            ValueError,
            "unsupported work item provider",
        ):
            detect_provider("https://example.com/ticket/123")
        with self.assertRaisesRegex(
            ValueError,
            "unsupported work item provider",
        ):
            detect_provider(
                "https://example.com/ticket/DFHIS-90001?token=SENTINEL"
            )
        with self.assertRaisesRegex(
            ValueError,
            "unsupported work item provider",
        ):
            detect_provider(
                "file://devops.aliyun.com/ticket/DFHIS-90001"
            )
        with self.assertRaisesRegex(
            ValueError,
            "unsupported work item provider",
        ):
            detect_provider(
                "http://devops.aliyun.com/ticket/DFHIS-90001"
            )
        with self.assertRaisesRegex(
            ValueError,
            "credentials",
        ):
            detect_provider(
                "https://user:SENTINEL@devops.aliyun.com/"
                "ticket/DFHIS-90001"
            )
        with self.assertRaisesRegex(
            ValueError,
            "unsupported work item provider",
        ):
            detect_provider(
                "https://devops.aliyun.com:444/ticket/DFHIS-90001"
            )
        with self.assertRaisesRegex(
            ValueError,
            "could not be parsed",
        ):
            detect_provider(
                "https://devops.aliyun.com/ticket?item=DFHIS-90001"
            )

    def test_rejects_non_url_input_with_extra_text_or_secret(self):
        with self.assertRaisesRegex(
            ValueError,
            "invalid work item input",
        ):
            detect_provider("DFHIS-90001 token=SENTINEL")

    def test_cleans_staging_when_collection_fails(self):
        def failing_collect(**kwargs):
            output = Path(kwargs["output_dir"])
            (output / "partial.txt").write_text(
                "partial",
                encoding="utf-8",
            )
            raise RuntimeError("collection failed")

        with tempfile.TemporaryDirectory() as temp_dir:
            history_root = Path(temp_dir) / "HarnessHistory"
            with self.assertRaisesRegex(RuntimeError, "collection failed"):
                process_intake(
                    source="DFHIS-90003",
                    history_root=history_root,
                    run_id="20260724-180200",
                    collect_adapter=failing_collect,
                )
            self.assertEqual(
                [],
                list((history_root / ".staging").iterdir()),
            )

    def test_skill_documents_routing_and_external_write_boundary(self):
        skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("TODO", skill_text)
        self.assertIn("yunxiao-workitem-evidence", skill_text)
        self.assertIn("harness-history", skill_text)
        self.assertIn("intake.py", skill_text)
        self.assertIn("持有读写凭证不等于云效写入授权", skill_text)
        catalog = (SKILL_DIR.parent / "README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("`harness-workitem-intake`", catalog)


if __name__ == "__main__":
    unittest.main()
