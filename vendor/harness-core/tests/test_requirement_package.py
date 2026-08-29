from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.requirement_package import export_requirement_package


class RequirementPackageTests(unittest.TestCase):
    def test_exports_complete_task_package_with_sources_and_required_planning_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ticket_dir = Path(temp_dir) / "archive" / "DFHIS-32178"
            yunxiao = ticket_dir / "yunxiao"
            (yunxiao / "attachments").mkdir(parents=True)
            (yunxiao / "inline-assets").mkdir(parents=True)
            (ticket_dir / "runs").mkdir(parents=True)
            (ticket_dir / "requirement.md").write_text("原始需求：医保退费完成后退款。\n", encoding="utf-8")
            (yunxiao / "snapshot.json").write_text(
                json.dumps(
                    {
                        "work_item": {"serial_number": "DFHIS-32178", "title": "线上医保退费"},
                        "description": {"clean_text": "正文需求"},
                        "comments": [{"content": "评论中的补充规则", "images": ["inline-1"]}],
                        "parent_work_items": [{"serial_number": "ORIGIN-10910", "title": "原始方案"}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (yunxiao / "comments_raw.json").write_text("评论原文", encoding="utf-8")
            (yunxiao / "attachments" / "source.docx").write_bytes(b"source-docx")
            (yunxiao / "inline-assets" / "inline-1.png").write_bytes(b"image")
            artifacts = [
                {"id": 1, "kind": "requirement_understanding_markdown", "title": "需求理解", "content": "已理解"},
                {"id": 2, "kind": "technical_decision_json", "title": "技术决策", "content": "{}"},
                {"id": 3, "kind": "behavior_test_plan_markdown", "title": "验证计划", "content": "验证退款路径"},
            ]
            with patch("app.requirement_package.database.get_artifacts", return_value=artifacts), patch(
                "app.requirement_package.database.get_step_runs", return_value=[]
            ), patch("app.requirement_package.database.build_json_payload", create=True, return_value="{}"):
                result = export_requirement_package(ticket_dir=ticket_dir, run_id=42)

            package = Path(result["package_dir"])
            self.assertTrue((package / "source" / "yunxiao" / "attachments" / "source.docx").is_file())
            self.assertTrue((package / "source" / "yunxiao" / "inline-assets" / "inline-1.png").is_file())
            self.assertTrue((package / "analysis" / "requirement_understanding.md").is_file())
            self.assertTrue((package / "analysis" / "prd.md").is_file())
            self.assertTrue((package / "analysis" / "requirement_plan.md").is_file())
            self.assertTrue((package / "analysis" / "project_understanding.md").is_file())
            self.assertTrue((package / "analysis" / "project_plan.md").is_file())
            self.assertTrue((package / "engineering" / "technical_decision.json").is_file())
            self.assertTrue((package / "execution" / "verification_plan.md").is_file())
            manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
            paths = {entry["path"] for entry in manifest["files"]}
            self.assertIn("source/yunxiao/attachments/source.docx", paths)
            self.assertIn("source/yunxiao/inline-assets/inline-1.png", paths)
            self.assertEqual("harness-task-package.v1", manifest["schema"])
            self.assertEqual(42, manifest["run_id"])

    def test_marks_missing_generated_documents_pending_instead_of_fabricating_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ticket_dir = Path(temp_dir) / "DFHIS-32179"
            (ticket_dir / "yunxiao").mkdir(parents=True)
            with patch("app.requirement_package.database.get_artifacts", return_value=[]), patch(
                "app.requirement_package.database.get_step_runs", return_value=[]
            ):
                result = export_requirement_package(ticket_dir=ticket_dir, run_id=43)

            prd = (Path(result["package_dir"]) / "analysis" / "prd.md").read_text(encoding="utf-8")
            self.assertIn("状态：pending", prd)
            self.assertIn("没有足够的已确认证据", prd)
            manifest = json.loads((Path(result["package_dir"]) / "manifest.json").read_text(encoding="utf-8"))
            pending = [entry for entry in manifest["files"] if entry["path"] == "analysis/prd.md"][0]
            self.assertEqual("pending", pending["status"])


if __name__ == "__main__":
    unittest.main()
