from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.requirement_archive import (
    archive_yunxiao_requirement,
    prepare_chat_harness_package,
    prepare_yunxiao_harness_package,
    record_requirement_archive_run,
    sync_yunxiao_requirement_archive,
)


class RequirementArchiveTests(unittest.TestCase):
    def test_prepare_chat_harness_package_preserves_the_main_chat_prompt_as_source_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="harness-chat-package-") as directory:
            result = prepare_chat_harness_package(
                archive_root=directory,
                prompt="修复结算页面金额显示，并补充测试。",
                workspace_id="workspace-1",
            )

            package = Path(result["package_dir"])
            self.assertTrue((package / "source" / "requirement.md").is_file())
            self.assertIn("修复结算页面金额显示", (package / "source" / "requirement.md").read_text(encoding="utf-8"))
            self.assertTrue((package / "analysis" / "prd.md").is_file())
            self.assertTrue((package / "engineering" / "task_contract.json").is_file())
            self.assertEqual("partial", result["package_status"])

    def test_prepare_chat_harness_package_copies_selected_materials_with_hashes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="harness-chat-evidence-") as directory:
            root = Path(directory)
            image = root / "需求截图.png"
            image.write_bytes(b"chat-image")
            with patch("app.requirement_archive.collect_yunxiao_evidence", return_value={
                "status": "success",
                "mode": "readonly",
                "yunxiao_url": "https://devops.aliyun.com/projex/req/DFHIS-12345",
                "work_item_id": "DFHIS-12345",
                "work_item": {"title": "截图修复"},
                "clean_text": "根据截图修复页面。",
                "comments": [],
                "attachments": [],
                "file_details": [],
                "inline_file_downloads": [],
            }):
                result = prepare_chat_harness_package(
                    archive_root=root / "archive",
                    prompt="根据截图修复页面。",
                    yunxiao_source="https://devops.aliyun.com/projex/req/DFHIS-12345",
                    evidence_paths=[image],
                )

            package = Path(result["package_dir"])
            evidence_manifest = json.loads((package / "source/chat-evidence-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(1, evidence_manifest["count"])
            stored = package / "source" / evidence_manifest["items"][0]["path"]
            self.assertEqual(b"chat-image", stored.read_bytes())
            self.assertEqual(hashlib.sha256(b"chat-image").hexdigest(), evidence_manifest["items"][0]["sha256"])
            self.assertIn("主聊天补充说明", (package / "source/requirement.md").read_text(encoding="utf-8"))

    def _evidence(self, *, ticket_id: str, source_path: Path) -> dict:
        return {
            "status": "success",
            "mode": "readonly",
            "yunxiao_url": f"https://devops.aliyun.com/projex/req/{ticket_id}",
            "work_item_id": ticket_id,
            "work_item": {
                "title": "挂号卡片显示诊室",
                "status": "待开发",
                "assignee": "王哲宏",
            },
            "clean_text": "挂号界面增加每个排班对应的诊室信息显示。",
            "comments": [{"author": "产品", "content": "诊室取当前排班维护值"}],
            "attachments": [{"identifier": "attachment-1", "name": "原始需求.pdf"}],
            "file_details": [
                {
                    "identifier": "attachment-1",
                    "name": "原始需求.pdf",
                    "kind": "attachment",
                    "status": "success",
                    "download": {
                        "identifier": "attachment-1",
                        "name": "原始需求.pdf",
                        "kind": "attachment",
                        "status": "success",
                        "path": str(source_path),
                        "size": source_path.stat().st_size,
                        "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                        "content_type": "application/pdf",
                    },
                }
            ],
            "inline_file_downloads": [],
        }

    def test_prepare_yunxiao_harness_package_returns_user_selectable_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_root = root / "archive"
            staging = root / "staging"
            source = staging / "yunxiao_inline_files" / "requirement.pdf"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"source")
            with patch("app.requirement_archive.collect_yunxiao_evidence", return_value=self._evidence(ticket_id="DFHIS-39999", source_path=source)):
                result = prepare_yunxiao_harness_package(
                    archive_root=archive_root,
                    yunxiao_url="https://devops.aliyun.com/projex/req/DFHIS-39999",
                    demand_text="",
                    evidence_staging_root=staging,
                )

            package_dir = Path(result["package_dir"])
            self.assertEqual("DFHIS-39999", result["ticket_id"])
            self.assertTrue((package_dir / "source/yunxiao/snapshot.json").is_file())
            self.assertTrue((package_dir / "source/yunxiao/attachments/attachment-1--原始需求.pdf").is_file())
            self.assertTrue((package_dir / "manifest.json").is_file())
            self.assertTrue((package_dir / "analysis/requirement_understanding.json").is_file())
            self.assertIn('"status": "pending"', (package_dir / "analysis/requirement_understanding.json").read_text(encoding="utf-8"))

    def test_creates_stable_ticket_folder_with_hash_manifest_and_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            staging = root / "staging"
            source = staging / "yunxiao_inline_files" / "attachment-1.pdf"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"requirement-pdf")

            result = archive_yunxiao_requirement(
                archive_root=root / "archive",
                evidence=self._evidence(ticket_id="DFHIS-31861", source_path=source),
                media_staging_root=staging,
                requirement_understanding="需要在挂号卡片展示排班诊室。",
                solution_plan="确认排班数据来源后补充前端展示。",
                change_note="首次从云效同步。",
            )

            ticket_dir = root / "archive" / "DFHIS-31861"
            self.assertEqual(ticket_dir.resolve(), Path(result["ticket_dir"]))
            self.assertTrue((ticket_dir / "yunxiao/snapshot.json").is_file())
            self.assertTrue((ticket_dir / "yunxiao/source.md").is_file())
            self.assertTrue((ticket_dir / "yunxiao/attachments/attachment-1--原始需求.pdf").is_file())
            manifest = json.loads((ticket_dir / "yunxiao/manifest.json").read_text(encoding="utf-8"))
            self.assertEqual("complete", manifest["status"])
            self.assertEqual("success", manifest["items"][0]["status"])
            self.assertEqual(hashlib.sha256(b"requirement-pdf").hexdigest(), manifest["items"][0]["sha256"])
            requirement = (ticket_dir / "requirement.md").read_text(encoding="utf-8")
            self.assertIn("需要在挂号卡片展示排班诊室", requirement)
            self.assertIn("首次从云效同步", requirement)

    def test_refreshes_same_requirement_file_and_preserves_manual_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            staging = root / "staging"
            source = staging / "yunxiao_inline_files" / "attachment-1.pdf"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"requirement-pdf")
            kwargs = {
                "archive_root": root / "archive",
                "evidence": self._evidence(ticket_id="DFHIS-31861", source_path=source),
                "media_staging_root": staging,
            }
            first = archive_yunxiao_requirement(
                **kwargs,
                requirement_understanding="初版理解。",
                change_note="首次同步。",
            )
            requirement_path = Path(first["requirement_path"])
            requirement_path.write_text(
                requirement_path.read_text(encoding="utf-8") + "\n## 人工补充\n\n保留这条补充。\n",
                encoding="utf-8",
            )

            second = archive_yunxiao_requirement(
                **kwargs,
                requirement_understanding="更新后的理解。",
                change_note="用户补充：诊室字段必须来自排班。",
            )

            self.assertEqual(first["ticket_dir"], second["ticket_dir"])
            self.assertEqual(str(requirement_path), second["requirement_path"])
            requirement = requirement_path.read_text(encoding="utf-8")
            self.assertIn("更新后的理解", requirement)
            self.assertIn("保留这条补充", requirement)
            self.assertIn("用户补充：诊室字段必须来自排班", requirement)

    def test_records_untrusted_media_path_without_copying_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            staging = root / "staging"
            staging.mkdir()
            outside = root / "outside.pdf"
            outside.write_bytes(b"must-not-copy")

            result = archive_yunxiao_requirement(
                archive_root=root / "archive",
                evidence=self._evidence(ticket_id="DFHIS-31862", source_path=outside),
                media_staging_root=staging,
            )

            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual("partial", manifest["status"])
            self.assertEqual("untrusted_source_path", manifest["items"][0]["status"])
            self.assertFalse((Path(result["ticket_dir"]) / "yunxiao/attachments/attachment-1--原始需求.pdf").exists())

    def test_marks_archive_partial_when_yunxiao_evidence_itself_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            staging = root / "staging"
            source = staging / "yunxiao_inline_files" / "attachment-1.pdf"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"cached-before-failure")
            evidence = self._evidence(ticket_id="DFHIS-31865", source_path=source)
            evidence.update({"status": "failed", "error": "云效详情读取失败"})

            result = archive_yunxiao_requirement(
                archive_root=root / "archive",
                evidence=evidence,
                media_staging_root=staging,
            )

            self.assertEqual("partial", result["status"])

    def test_lists_attachment_that_cannot_be_downloaded_in_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            staging = root / "staging"
            staging.mkdir()
            evidence = {
                "status": "success",
                "work_item_id": "DFHIS-31866",
                "attachments": [{"name": "云效未提供标识的附件.docx"}],
                "file_details": [],
                "inline_file_downloads": [],
            }

            result = archive_yunxiao_requirement(
                archive_root=root / "archive",
                evidence=evidence,
                media_staging_root=staging,
            )

            manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual("partial", manifest["status"])
            self.assertEqual("云效未提供标识的附件.docx", manifest["items"][0]["original_name"])
            self.assertEqual("not_downloaded", manifest["items"][0]["status"])

    def test_reuses_historical_inline_image_when_current_yunxiao_identifier_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_name = "433b9c0de9103e954268dc4a828b197c.jpeg"
            image_content = b"historical-inline-image"
            first_staging = root / "first-staging"
            first_source = first_staging / "yunxiao_inline_files" / image_name
            first_source.parent.mkdir(parents=True)
            first_source.write_bytes(image_content)
            image_size = first_source.stat().st_size
            image_sha256 = hashlib.sha256(image_content).hexdigest()
            first_evidence = {
                "status": "success",
                "work_item_id": "DFHIS-32190",
                "work_item": {"title": "医保退费图片证据", "description": "正文"},
                "inline_files": [{
                    "identifier": "0df57fd492c77fcd5650a952be",
                    "name": image_name,
                    "kind": "inline_image",
                    "size": image_size,
                    "status": "success",
                }],
                "inline_file_downloads": [{
                    "identifier": "0df57fd492c77fcd5650a952be",
                    "name": image_name,
                    "kind": "inline_image",
                    "status": "success",
                    "path": str(first_source),
                    "size": image_size,
                    "sha256": image_sha256,
                }],
            }
            first = archive_yunxiao_requirement(
                archive_root=root / "archive",
                evidence=first_evidence,
                media_staging_root=first_staging,
            )

            second_staging = root / "second-staging"
            second_staging.mkdir()
            second_evidence = {
                "status": "partial",
                "work_item_id": "DFHIS-32190",
                "work_item": {"title": "医保退费图片证据", "description": "正文"},
                "inline_files": [{
                    "identifier": "df57fd492c77fcd5650a952be",
                    "name": image_name,
                    "kind": "inline_image",
                    "size": image_size,
                    "status": "unavailable",
                    "error": "文件不存在",
                }],
                "inline_file_downloads": [{
                    "identifier": "df57fd492c77fcd5650a952be",
                    "name": image_name,
                    "kind": "inline_image",
                    "status": "failed",
                    "size": image_size,
                    "error": "文件不存在",
                }],
            }

            second = archive_yunxiao_requirement(
                archive_root=root / "archive",
                evidence=second_evidence,
                media_staging_root=second_staging,
            )

            manifest = json.loads(Path(second["manifest_path"]).read_text(encoding="utf-8"))
            item = manifest["items"][0]
            self.assertEqual("reused", item["status"])
            self.assertEqual(
                "inline-assets/0df57fd492c77fcd5650a952be--" + image_name,
                item["stored_path"],
            )
            self.assertEqual(image_sha256, item["sha256"])
            self.assertEqual("historical_success", item["reconciliation"]["type"])
            self.assertEqual("0df57fd492c77fcd5650a952be", item["reconciliation"]["previous_identifier"])
            self.assertEqual("partial", second["status"])
            snapshot = json.loads(Path(second["snapshot_path"]).read_text(encoding="utf-8"))
            self.assertEqual(
                item["stored_path"],
                snapshot["images"][0]["path"],
            )
            self.assertTrue(
                (Path(first["ticket_dir"]) / "yunxiao" / item["stored_path"]).is_file()
            )

    def test_sync_reads_once_in_archive_mode_then_removes_its_staging_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            captured: dict[str, object] = {}

            def fake_collect(**kwargs: object) -> dict:
                captured.update(kwargs)
                staging = Path(str(kwargs["output_dir"]))
                source = staging / "yunxiao_inline_files" / "attachment-1.pdf"
                source.parent.mkdir(parents=True)
                source.write_bytes(b"downloaded-once")
                return self._evidence(ticket_id="DFHIS-31863", source_path=source)

            with patch("app.yunxiao_read.collect_yunxiao_evidence", side_effect=fake_collect):
                result = sync_yunxiao_requirement_archive(
                    archive_root=root / "archive",
                    yunxiao_url="https://devops.aliyun.com/projex/req/DFHIS-31863",
                    demand_text="DFHIS-31863",
                )

            self.assertEqual("archive", captured["download_policy"])
            self.assertEqual("complete", result["status"])
            self.assertFalse(Path(str(captured["output_dir"])).exists())

    def test_records_generated_harness_run_in_the_same_ticket_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            staging = root / "staging"
            source = staging / "yunxiao_inline_files" / "attachment-1.pdf"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"requirement-pdf")
            archive = archive_yunxiao_requirement(
                archive_root=root / "archive",
                evidence=self._evidence(ticket_id="DFHIS-31864", source_path=source),
                media_staging_root=staging,
            )

            recorded = record_requirement_archive_run(
                ticket_dir=archive["ticket_dir"],
                run_id=42,
                status="completed",
                evaluation_status="passed",
                markdown_report="# Harness 报告\n\n方案已生成。",
                requirement_understanding="已确认展示字段来自排班。",
                solution_plan="在挂号卡片增加排班诊室展示并做回归验证。",
            )

            self.assertTrue(Path(recorded["report_path"]).is_file())
            requirement = Path(recorded["requirement_path"]).read_text(encoding="utf-8")
            self.assertIn("已确认展示字段来自排班", requirement)
            self.assertIn("在挂号卡片增加排班诊室展示", requirement)
            self.assertIn("Harness run 42 已归档", requirement)


if __name__ == "__main__":
    unittest.main()
