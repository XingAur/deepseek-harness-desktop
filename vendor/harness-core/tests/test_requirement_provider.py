from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path

from app.requirement_provider import (
    build_local_change_evidence_exception,
    normalize_requirement_evidence,
    normalize_requirement_evidence_file,
)


class RequirementProviderTests(unittest.TestCase):
    def test_normalizes_yunxiao_manual_and_file_evidence_as_readonly(self) -> None:
        yunxiao = normalize_requirement_evidence(
            source_type="yunxiao",
            payload={
                "work_item": {"id": "DFHIS-1", "title": "云效标题", "description": "云效正文"},
                "comments": [{"author": "张三", "content": "评论证据"}],
                "attachments": [{"name": "需求.docx", "url": "https://example.test/need"}],
            },
            fetched_at="2026-07-27T00:00:00+08:00",
        )
        manual = normalize_requirement_evidence(
            source_type="manual",
            payload={
                "title": "手工标题",
                "description_text": "手工正文",
                "comments": [{"content": "手工评论"}],
                "attachments": [{"name": "手工附件.txt"}],
            },
            fetched_at="2026-07-27T00:00:00+08:00",
        )
        file_evidence = normalize_requirement_evidence(
            source_type="file",
            payload={
                "title": "文件标题",
                "content": "文件正文",
                "comments": [{"content": "文件评论"}],
                "attachments": [{"name": "文件附件.txt"}],
            },
            fetched_at="2026-07-27T00:00:00+08:00",
        )

        self.assertEqual(("云效标题", "云效正文"), (yunxiao["title"], yunxiao["description_text"]))
        self.assertEqual("评论证据", yunxiao["comments"][0]["content"])
        self.assertEqual("需求.docx", yunxiao["attachments"][0]["name"])
        self.assertEqual(("手工标题", "手工正文"), (manual["title"], manual["description_text"]))
        self.assertEqual("手工评论", manual["comments"][0]["content"])
        self.assertEqual("手工附件.txt", manual["attachments"][0]["name"])
        self.assertEqual(("文件标题", "文件正文"), (file_evidence["title"], file_evidence["description_text"]))
        self.assertEqual("文件评论", file_evidence["comments"][0]["content"])
        self.assertEqual("文件附件.txt", file_evidence["attachments"][0]["name"])
        for evidence in (yunxiao, manual, file_evidence):
            self.assertTrue(evidence["readonly"])
            self.assertFalse(evidence["external_writes_enabled"])

    def test_unknown_provider_warns_without_losing_readonly_boundary(self) -> None:
        evidence = normalize_requirement_evidence(
            source_type="unknown-provider",
            payload={"title": "标题", "description": "正文"},
            fetched_at="2026-07-27T00:00:00+08:00",
        )

        self.assertEqual("unknown_provider", evidence["source_type"])
        self.assertIn("unsupported_source_type", [item["code"] for item in evidence["warnings"]])
        self.assertTrue(evidence["readonly"])
        self.assertFalse(evidence["external_writes_enabled"])

    def test_expired_inline_screenshot_does_not_block_readonly_analysis(self) -> None:
        evidence = normalize_requirement_evidence(
            source_type="yunxiao",
            payload={
                "work_item": {
                    "title": "保孕业务改造",
                    "description": "正文规则仍然完整，截图只是页面示意。",
                },
                "decision_gate": {
                    "state": "needs_requirement_confirmation",
                    "reasons": ["inline_file_detail_failed"],
                },
                "completeness": {"status": "partial"},
                "warnings": [
                    {
                        "code": "inline_file_detail_failed",
                        "message": "文件不存在，fileId: expired",
                        "http_status": 400,
                    }
                ],
            },
        )

        quality = evidence["evidence_quality"]
        self.assertTrue(quality["analysis_ready"])
        self.assertEqual("ready_with_warnings", quality["analysis_status"])
        self.assertFalse(quality["mutation_ready"])
        self.assertEqual(["inline_file_detail_failed"], quality["optional_warning_codes"])
        self.assertEqual([], quality["blocking_warning_codes"])

    def test_user_confirmed_optional_inline_failure_creates_local_only_exception(self) -> None:
        evidence = normalize_requirement_evidence(
            source_type="yunxiao",
            payload={
                "work_item": {
                    "title": "患者建档联系人维护",
                    "description": "正文规则完整，失效截图不是唯一需求来源。",
                },
                "decision_gate": {"state": "needs_requirement_confirmation"},
                "completeness": {"status": "partial"},
                "warnings": [
                    {
                        "code": "inline_file_detail_failed",
                        "message": "文件不存在，fileId: expired",
                    }
                ],
            },
        )

        exception = build_local_change_evidence_exception(
            normalized_evidence=evidence,
            user_confirmation="按已确认合同继续本地实现",
            confirmed_at="2026-08-24T10:30:00+08:00",
        )

        self.assertEqual("approved", exception["status"])
        self.assertEqual("local_implementation_only", exception["scope"])
        self.assertFalse(exception["external_writes_authorized"])
        self.assertEqual("needs_requirement_confirmation", exception["provider_gate"])
        self.assertEqual("partial", exception["provider_completeness"])
        self.assertEqual(["inline_file_detail_failed"], exception["excepted_warning_codes"])
        self.assertTrue(exception["provider_evidence_sha256"].startswith("sha256:"))

    def test_granular_expired_inline_image_does_not_block_readonly_analysis(self) -> None:
        evidence = normalize_requirement_evidence(
            source_type="yunxiao",
            payload={
                "work_item": {"title": "保孕业务改造", "description": "正文规则完整。"},
                "decision_gate": {"state": "ready_for_analysis_with_warnings"},
                "completeness": {"status": "partial"},
                "warnings": [{"code": "inline_image_detail_failed", "message": "图片失效"}],
            },
        )

        quality = evidence["evidence_quality"]
        self.assertTrue(quality["analysis_ready"])
        self.assertEqual("ready_with_warnings", quality["analysis_status"])
        self.assertFalse(quality["mutation_ready"])

    def test_high_risk_error_screenshot_failure_blocks_analysis_before_project_discovery(self) -> None:
        evidence = normalize_requirement_evidence(
            source_type="yunxiao",
            payload={
                "work_item": {
                    "title": "医保门诊退费预结算失败",
                    "description": "点击退费后截图提示国家医保挂号预结算失败。",
                },
                "warnings": [{"code": "inline_image_download_failed", "message": "截图下载失败"}],
            },
        )

        quality = evidence["evidence_quality"]
        self.assertFalse(quality["analysis_ready"])
        self.assertEqual("blocked", quality["analysis_status"])
        self.assertIn("visual_evidence_unavailable", quality["blocking_warning_codes"])
        self.assertEqual("required", evidence["visual_evidence"]["status"])

    def test_archived_snapshot_rehydrates_relative_screenshot_before_visual_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = root / "inline-assets" / "error.jpeg"
            asset.parent.mkdir()
            asset.write_bytes(b"image")
            snapshot = root / "snapshot.json"
            snapshot.write_text(
                json.dumps(
                    {
                        "source_type": "yunxiao",
                        "title": "医保门诊退费预结算失败",
                        "description_text": "点击退费后截图提示国家医保挂号预结算失败。",
                        "images": [{
                            "name": "error.jpeg",
                            "path": "inline-assets/error.jpeg",
                            "identifier": "archived-screenshot",
                            "content_type": "image/jpeg",
                            "status": "success",
                        }],
                        "warnings": [
                            {"code": "source_warning", "message": "inline_image_download_failed"}
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            evidence = normalize_requirement_evidence_file(snapshot, source_type="yunxiao")

        self.assertEqual("ready_for_extraction", evidence["visual_evidence"]["status"])
        self.assertEqual([str(asset.resolve())], evidence["visual_evidence"]["available_image_paths"])
        self.assertNotIn("source_warning", evidence["evidence_quality"]["blocking_warning_codes"])

    def test_body_read_failure_still_blocks_analysis(self) -> None:
        evidence = normalize_requirement_evidence(
            source_type="yunxiao",
            payload={
                "title": "保孕业务改造",
                "description_text": "已返回的局部正文。",
                "warnings": [
                    {"code": "source_read_failed", "message": "详情接口失败"},
                    {"code": "inline_file_detail_failed", "message": "附件失效"},
                ],
            },
        )

        quality = evidence["evidence_quality"]
        self.assertFalse(quality["analysis_ready"])
        self.assertEqual("blocked", quality["analysis_status"])
        self.assertIn("source_read_failed", quality["blocking_warning_codes"])

    def test_v2_provider_field_is_used_when_loading_evidence_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "requirement_evidence.v2.json"
            path.write_text(
                json.dumps(
                    {
                        "provider": "yunxiao",
                        "work_items": [
                            {
                                "title": "云效需求",
                                "description": {"text": "正文"},
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            evidence = normalize_requirement_evidence_file(path)

        self.assertEqual("yunxiao", evidence["source_type"])

    def test_capability_result_envelope_is_accepted_without_host_side_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "capability-result.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "his-capability-result.v1",
                        "provider": "yunxiao",
                        "status": "partial",
                        "summary": "YUNXIAO_READ_PARTIAL",
                        "data": {
                            "provider": "yunxiao",
                            "source": {"requested_id": "DFHIS-32190"},
                            "work_items": [
                                {
                                    "id": "work-item-id",
                                    "serial_number": "DFHIS-32190",
                                    "role": "requested",
                                    "title": "医保退费报错",
                                    "description": {"text": "退药后点击退费按钮报患者在院不能进行医保登记。"},
                                }
                            ],
                            "warnings": [{"code": "inline_file_detail_failed", "message": "截图失效"}],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            evidence = normalize_requirement_evidence_file(path)

        self.assertEqual("yunxiao", evidence["source_type"])
        self.assertEqual("DFHIS-32190", evidence["external_id"])
        self.assertIn("患者在院不能", evidence["description_text"])
        self.assertIn("inline_file_detail_failed", [item["code"] for item in evidence["warnings"]])

    def test_v2_yunxiao_archive_uses_requested_item_plain_text_and_inline_images(self) -> None:
        # The provider's historical `description.text` field is a JSON-like
        # envelope whose htmlValue contains literal newlines, so it is not
        # valid nested JSON even though the outer archive file is valid JSON.
        rich_text = (
            '{"htmlValue":"\n需求或问题描述：\n'
            '挂号收费列表中，每个挂号医生后面加上诊室\n'
            '1、挂号界面增加每个排班对应的诊室信息显示\n",'
            '"jsonMLValue":["root",{},["p",{},"不得输出这个结构名"]]}'
        )
        payload = {
            "provider": "yunxiao",
            "source": {
                "requested_id": "DFHIS-32109",
                "resolved_work_item_id": "child-id",
                "fetched_at": "2026-08-23T11:17:02+00:00",
            },
            "work_items": [
                {
                    "id": "parent-id",
                    "serial_number": "ORIGIN-1",
                    "title": "父需求",
                    "description": {"text": "父需求正文"},
                },
                {
                    "id": "child-id",
                    "serial_number": "DFHIS-32109",
                    "title": "挂号卡片显示诊室",
                    "status": "待开发",
                    "assignee": "王哲宏",
                    "description": {"format": "RICHTEXT", "text": rich_text},
                    "comments": [{"content": "诊室取当前排班维护值"}],
                    "inline_files": [
                        {
                            "file_id": "image-id",
                            "name": "image.png",
                            "local_path": "files/child-id/image.png",
                            "content_type": "image/png",
                            "download_status": "success",
                        }
                    ],
                    "role": "requested",
                },
            ],
            "decision_gate": {"state": "ready_for_analysis", "reasons": []},
            "completeness": {"status": "complete"},
        }

        evidence = normalize_requirement_evidence(source_type="yunxiao", payload=payload)

        self.assertEqual("DFHIS-32109", evidence["external_id"])
        self.assertEqual("挂号卡片显示诊室", evidence["title"])
        self.assertEqual("待开发", evidence["status"])
        self.assertEqual("王哲宏", evidence["assignee"])
        self.assertIn("挂号界面增加每个排班对应的诊室信息显示", evidence["description_text"])
        self.assertNotIn("htmlValue", evidence["description_text"])
        self.assertNotIn("jsonMLValue", evidence["description_text"])
        self.assertEqual("诊室取当前排班维护值", evidence["comments"][0]["content"])
        self.assertEqual("files/child-id/image.png", evidence["images"][0]["path"])

    def test_evidence_file_resolves_downloaded_relative_media_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "files/child-id/image.png"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"png")
            evidence_path = root / "requirement_evidence.v2.json"
            evidence_path.write_text(
                json.dumps(
                    {
                        "provider": "yunxiao",
                        "source": {"resolved_work_item_id": "child-id"},
                        "work_items": [
                            {
                                "id": "child-id",
                                "serial_number": "DFHIS-32109",
                                "title": "挂号卡片显示诊室",
                                "description": {"text": "挂号界面增加诊室显示"},
                                "inline_files": [
                                    {
                                        "file_id": "image-id",
                                        "name": "image.png",
                                        "local_path": "files/child-id/image.png",
                                        "content_type": "image/png",
                                    }
                                ],
                                "role": "requested",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            evidence = normalize_requirement_evidence_file(evidence_path)

        self.assertEqual(str(image_path.resolve()), evidence["images"][0]["path"])


if __name__ == "__main__":
    unittest.main()
