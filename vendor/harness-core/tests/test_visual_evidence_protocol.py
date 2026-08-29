from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.visual_evidence_protocol import (
    VisualEvidenceExtractionRequest,
    VisualEvidenceExtractionResult,
    VisualEvidenceHostSession,
    parse_visual_evidence_request,
    parse_visual_evidence_result,
)


class VisualEvidenceProtocolTests(unittest.TestCase):
    def test_host_session_delivers_archived_image_and_returns_only_visible_facts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "refund-error.jpeg"
            image.write_bytes(b"image")
            received = []

            def handler(request):
                received.append(request)
                return VisualEvidenceExtractionResult(
                    facts=({
                        "image_path": str(image),
                        "error_text": "调用国家医保的挂号预结算失败",
                        "menu": "门诊退费",
                        "action": "退费",
                        "business_scene": "门诊退费时触发国家医保预结算",
                        "target_module": "",
                    },),
                    blockers=(),
                )

            result = VisualEvidenceHostSession(handler).extract(
                VisualEvidenceExtractionRequest(
                    title="门诊退费失败",
                    description="截图中的可见事实优先于背景文字。",
                    image_paths=(image,),
                )
            )

        self.assertEqual(1, len(received))
        self.assertEqual((image,), received[0].image_paths)
        self.assertEqual("调用国家医保的挂号预结算失败", result.facts[0]["error_text"])

    def test_host_session_fails_closed_when_adapter_claims_success_without_complete_facts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "refund-error.jpeg"
            image.write_bytes(b"image")

            result = VisualEvidenceHostSession(
                lambda request: {"facts": [{"error_text": "只有错误文本"}], "blockers": []}
            ).extract(
                VisualEvidenceExtractionRequest("门诊退费失败", "截图取证", (image,))
            )

        self.assertEqual((), result.facts)
        self.assertIn("visual_evidence_adapter_invalid", result.blockers)

    def test_request_and_result_use_a_portable_json_contract_bound_to_archived_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "refund-error.jpeg"
            image.write_bytes(b"image")
            request = VisualEvidenceExtractionRequest("门诊退费失败", "截图取证", (image,))
            restored_request = parse_visual_evidence_request(request.to_dict())
            result = VisualEvidenceExtractionResult(({
                "image_path": str(image),
                "error_text": "调用国家医保的挂号预结算失败",
                "menu": "门诊退费",
                "action": "退费",
                "business_scene": "门诊退费时触发国家医保预结算",
                "target_module": "",
            },), ())
            restored_result = parse_visual_evidence_result(
                result.to_dict(),
                image_paths=restored_request.image_paths,
            )

        self.assertEqual(request.image_paths, restored_request.image_paths)
        self.assertEqual(result.facts, restored_result.facts)


if __name__ == "__main__":
    unittest.main()
