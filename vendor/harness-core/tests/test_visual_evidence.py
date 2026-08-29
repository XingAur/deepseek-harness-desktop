from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.requirement_provider import normalize_requirement_evidence, requirement_evidence_to_markdown
from app.visual_evidence import (
    CodexCliVisualEvidenceAnalyzer,
    FileVisualEvidenceAnalyzer,
    HostVisualEvidenceAnalyzer,
    analyze_requirement_visual_evidence,
    configured_visual_evidence_analyzer,
)
from app.visual_evidence_protocol import VisualEvidenceHostSession


class _Analyzer:
    def analyze(self, *, title, description, image_paths):
        return {
            "facts": [{
                "error_text": "调用国家医保的挂号预结算失败",
                "menu": "门诊退费",
                "action": "退费",
                "business_scene": "门诊退费时触发国家医保预结算",
                "target_module": "门诊退费",
            }]
        }


class _WorkerResult:
    error_code = ""
    final_response = {
        "schema_version": "his-visual-evidence.v1",
        "facts": [{
            "fact_type": "document",
            "image_path": "",
            "target_module": "线上医保退费",
            "document_type": "医保退费接口参数表",
            "visible_text": "ecToken payAuthNo",
            "key_facts": "ecToken 与 payAuthNo 不能同时为空",
        }],
        "blockers": [],
    }


class _VisualWorker:
    def __init__(self):
        self.request = None

    def start(self, request, sink):
        self.request = request
        return _WorkerResult()


class VisualEvidenceTests(unittest.TestCase):
    def test_visual_facts_open_only_the_evidence_gate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "error.png"
            image.write_bytes(b"image")
            evidence = normalize_requirement_evidence(
                source_type="yunxiao",
                payload={
                    "work_item": {"title": "医保门诊退费预结算失败", "description": "截图显示退费时报错。"},
                    "inline_file_downloads": [{"name": "error.png", "path": str(image), "content_type": "image/png", "status": "success"}],
                },
            )

            self.assertFalse(evidence["evidence_quality"]["analysis_ready"])
            result = analyze_requirement_visual_evidence(evidence, analyzer=_Analyzer())

        self.assertEqual("analyzed", result["visual_evidence"]["status"])
        self.assertTrue(result["evidence_quality"]["analysis_ready"])
        self.assertEqual("门诊退费", result["visual_evidence"]["facts"][0]["menu"])

    def test_missing_visual_fields_keep_gate_closed(self):
        evidence = normalize_requirement_evidence(
            source_type="yunxiao",
            payload={
                "work_item": {"title": "医保门诊退费预结算失败", "description": "截图显示退费时报错。"},
                "warnings": [{"code": "inline_image_download_failed", "message": "下载失败"}],
            },
        )

        result = analyze_requirement_visual_evidence(evidence, analyzer=_Analyzer())

        self.assertEqual("required", result["visual_evidence"]["status"])
        self.assertFalse(result["evidence_quality"]["analysis_ready"])

    def test_successful_archived_screenshot_is_not_invalidated_by_duplicate_download_warning(self):
        """One usable local image is the evidence source; a duplicate fetch failure is not."""
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "error.jpeg"
            image.write_bytes(b"image")
            evidence = normalize_requirement_evidence(
                source_type="yunxiao",
                payload={
                    "work_item": {
                        "title": "医保门诊退费预结算失败",
                        "description": "点击退费后截图提示国家医保挂号预结算失败。",
                    },
                    "inline_file_downloads": [
                        {
                            "identifier": "screenshot-1",
                            "name": "error.jpeg",
                            "path": str(image),
                            "content_type": "image/jpeg",
                            "status": "success",
                        }
                    ],
                    "warnings": [
                        {"code": "inline_image_download_failed", "message": "重复图片引用已失效"}
                    ],
                },
            )

        visual = evidence["visual_evidence"]
        self.assertEqual("ready_for_extraction", visual["status"])
        self.assertEqual([str(image.resolve())], visual["available_image_paths"])
        self.assertNotIn("visual_evidence_unavailable", evidence["evidence_quality"]["blocking_warning_codes"])

    def test_archived_snapshot_images_field_is_a_usable_visual_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "archived-error.jpeg"
            image.write_bytes(b"image")
            evidence = normalize_requirement_evidence(
                source_type="yunxiao",
                payload={
                    "title": "医保门诊退费预结算失败",
                    "description_text": "点击退费后截图提示国家医保挂号预结算失败。",
                    "images": [{
                        "identifier": "archived-screenshot",
                        "name": image.name,
                        "path": str(image),
                        "content_type": "image/jpeg",
                        "status": "success",
                    }],
                },
            )

        self.assertEqual("ready_for_extraction", evidence["visual_evidence"]["status"])
        self.assertEqual([str(image.resolve())], evidence["visual_evidence"]["available_image_paths"])

    def test_default_visual_adapter_never_uses_codex_app_or_cli_when_model_mode_is_mock(self):
        with mock.patch.dict("os.environ", {"HARNESS_LLM_MODE": "mock"}, clear=False):
            self.assertIsNone(configured_visual_evidence_analyzer())

    def test_harness_visual_adapter_sends_archived_images_to_a_readonly_structured_worker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "refund-contract.png"
            image.write_bytes(b"image")
            worker = _VisualWorker()
            analyzer = CodexCliVisualEvidenceAnalyzer(worker=worker)

            result = analyzer.analyze(
                title="线上医保退费",
                description="读取接口参数截图",
                image_paths=(image,),
            )

        self.assertEqual("document", result["facts"][0]["fact_type"])
        self.assertEqual([image], list(worker.request.image_paths))
        self.assertTrue(worker.request.visual_only)
        self.assertTrue(worker.request.skip_git_repo_check)

    def test_explicit_host_visual_adapter_opens_gate_with_archived_screenshot_facts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "error.jpeg"
            image.write_bytes(b"image")
            evidence = normalize_requirement_evidence(
                source_type="yunxiao",
                payload={
                    "work_item": {"title": "医保门诊退费预结算失败", "description": "截图显示退费时报错。"},
                    "inline_file_downloads": [{"name": image.name, "path": str(image), "content_type": "image/jpeg", "status": "success"}],
                },
            )
            analyzer = HostVisualEvidenceAnalyzer(
                VisualEvidenceHostSession(lambda request: {
                    "facts": [{
                        "error_text": "调用国家医保的挂号预结算失败",
                        "menu": "门诊退费",
                        "action": "退费",
                        "business_scene": "门诊退费时调用国家医保预结算",
                    }],
                    "blockers": [],
                })
            )

            result = analyze_requirement_visual_evidence(evidence, analyzer=analyzer)

        self.assertTrue(result["evidence_quality"]["analysis_ready"])
        self.assertEqual("门诊退费", result["visual_evidence"]["facts"][0]["menu"])

    def test_file_visual_evidence_adapter_reads_document_facts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "refund-contract.png"
            image.write_bytes(b"image")
            result_file = root / "visual-result.json"
            result_file.write_text(
                json.dumps(
                    {
                        "schema_version": "his-visual-evidence.v1",
                        "facts": [{
                            "fact_type": "document",
                            "image_path": str(image),
                            "target_module": "线上医保退费",
                            "document_type": "医保退费接口参数表",
                            "visible_text": "Q104 ecToken payAuthNo",
                            "key_facts": "ecToken 与 payAuthNo 不能同时为空",
                        }],
                        "blockers": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            evidence = normalize_requirement_evidence(
                source_type="yunxiao",
                payload={
                    "work_item": {"title": "线上医保退费", "description": "需求引用接口参数截图。"},
                    "inline_file_downloads": [{
                        "name": image.name,
                        "path": str(image),
                        "content_type": "image/png",
                        "status": "success",
                    }],
                },
            )

            result = analyze_requirement_visual_evidence(
                evidence,
                analyzer=FileVisualEvidenceAnalyzer(result_file),
            )

        self.assertTrue(result["evidence_quality"]["analysis_ready"])
        self.assertEqual("analyzed", result["visual_evidence"]["status"])
        self.assertEqual("document", result["visual_evidence"]["facts"][0]["fact_type"])
        self.assertEqual("file", result["visual_evidence"]["host"]["type"])

    def test_document_visual_facts_are_visible_in_the_requirement_report(self):
        markdown = requirement_evidence_to_markdown({
            "visual_evidence": {
                "status": "analyzed",
                "facts": [{
                    "fact_type": "document",
                    "document_type": "医保退费接口参数表",
                    "visible_text": "ecToken payAuthNo",
                    "key_facts": "ecToken 与 payAuthNo 不能同时为空",
                }],
            },
        })

        self.assertIn("文档类型：医保退费接口参数表", markdown)
        self.assertIn("关键事实：ecToken 与 payAuthNo 不能同时为空", markdown)


if __name__ == "__main__":
    unittest.main()
