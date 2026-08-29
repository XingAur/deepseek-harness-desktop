from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.agent_backend_protocol import AgentBackendRequest, AgentBackendResult
from app.requirement_intake_model import INTAKE_DOCUMENTS, INTAKE_DOCUMENT_SCHEMA, draft_intake_analysis_documents
from app.requirement_package import export_requirement_package


def _result_with_text(text: str) -> AgentBackendResult:
    return AgentBackendResult(
        exit_code=0,
        error_code="",
        event_count=0,
        final_response_sha256="",
        canonical_final_response_sha256="",
        final_response_validated=False,
        final_response={"schema_version": INTAKE_DOCUMENT_SCHEMA, "text": text},
    )


def _failed_result(error_code: str) -> AgentBackendResult:
    return AgentBackendResult(
        exit_code=1,
        error_code=error_code,
        event_count=0,
        final_response_sha256="",
        canonical_final_response_sha256="",
        final_response_validated=False,
    )


def _valid_model_text(question: str = "发票重打是否需要保留历史记录？") -> str:
    body = "业务背景与目标场景说明。" * 20
    documents = {name: body for name in INTAKE_DOCUMENTS}
    return json.dumps({"documents": documents, "open_questions": [question]}, ensure_ascii=False)


class IntakeModelGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        ticket_dir = Path(self._temp.name) / "DFHIS-32178"
        yunxiao = ticket_dir / "yunxiao"
        yunxiao.mkdir(parents=True)
        (ticket_dir / "runs").mkdir()
        (ticket_dir / "requirement.md").write_text("原始需求：门诊发票重打需要记录历史。\n", encoding="utf-8")
        (yunxiao / "snapshot.json").write_text("{}", encoding="utf-8")
        self.ticket_dir = ticket_dir
        self.package = Path(export_requirement_package(ticket_dir=ticket_dir, run_id=0)["package_dir"])

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _draft(self, host_execute) -> dict[str, object]:
        return draft_intake_analysis_documents(
            package_dir=self.package,
            ticket_dir=self.ticket_dir,
            ticket_id="DFHIS-32178",
            host_execute=host_execute,
            selected_model_id="deepseek-reasoner",
        )

    def test_generated_documents_replace_pending_markers_and_rebuild_manifest(self) -> None:
        result = self._draft(lambda request: _result_with_text(_valid_model_text()))

        self.assertEqual(result["status"], "generated")
        self.assertEqual(result["generated_count"], len(INTAKE_DOCUMENTS))
        self.assertEqual(result["open_questions"], ["发票重打是否需要保留历史记录？"])
        document = (self.package / "analysis" / "prd.md").read_text(encoding="utf-8")
        self.assertIn("- 状态：model_generated", document)
        self.assertIn("起草模型：deepseek-reasoner", document)
        manifest = json.loads((self.package / "manifest.json").read_text(encoding="utf-8"))
        entry = next(item for item in manifest["files"] if item["path"] == "analysis/prd.md")
        self.assertEqual(entry["status"], "model_generated")
        self.assertEqual(entry["generator"], "selected_model")
        self.assertGreaterEqual(manifest["model_generated_count"], len(INTAKE_DOCUMENTS))
        report = json.loads((self.package / "analysis" / "intake_generation_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "generated")

    def test_request_uses_bounded_prompt_and_named_contract(self) -> None:
        seen: list[AgentBackendRequest] = []

        def host_execute(request: AgentBackendRequest) -> AgentBackendResult:
            seen.append(request)
            return _result_with_text(_valid_model_text())

        self._draft(host_execute)
        self.assertEqual(len(seen), 1)
        request = seen[0]
        self.assertEqual(request.output_contract["schema_version"], INTAKE_DOCUMENT_SCHEMA)
        self.assertLessEqual(len(request.prompt.encode("utf-8")), 48_000)
        self.assertIn("归档证据", request.prompt)
        self.assertTrue(request.worktree_path.is_absolute())

    def test_worker_failure_keeps_pending_placeholders_and_reports_reason(self) -> None:
        result = self._draft(lambda request: _failed_result("worker_backend_unavailable"))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "worker_backend_unavailable")
        self.assertEqual(result["generated_count"], 0)
        document = (self.package / "analysis" / "prd.md").read_text(encoding="utf-8")
        self.assertIn("- 状态：pending", document)
        manifest = json.loads((self.package / "manifest.json").read_text(encoding="utf-8"))
        entry = next(item for item in manifest["files"] if item["path"] == "analysis/prd.md")
        self.assertEqual(entry["status"], "pending")

    def test_unparsable_model_output_is_reported_without_fabricating_documents(self) -> None:
        result = self._draft(lambda request: _result_with_text("这不是 JSON"))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "intake_model_output_invalid")
        self.assertIn("- 状态：pending", (self.package / "analysis" / "prd.md").read_text(encoding="utf-8"))

    def test_short_documents_are_skipped_and_long_documents_are_capped(self) -> None:
        text = json.dumps(
            {
                "documents": {
                    "prd.md": "太短",
                    "requirement_understanding.md": "很长的正文。" * 600,
                },
            },
            ensure_ascii=False,
        )
        result = self._draft(lambda request: _result_with_text(text))

        self.assertEqual(result["status"], "generated")
        self.assertEqual(result["generated_count"], 1)
        # prd.md 太短被跳过，其余 6 篇未在输出中提供，同样计入待补。
        self.assertEqual(result["skipped_count"], 7)
        understanding = (self.package / "analysis" / "requirement_understanding.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(understanding), 2_500)

    def test_full_document_set_stays_inside_protocol_scan_limits(self) -> None:
        """8 篇中文文档合计不能超过结果帧的敏感扫描上限（约 64KB UTF-8）。"""

        text = _valid_model_text()
        encoded = json.dumps(
            {"schema_version": INTAKE_DOCUMENT_SCHEMA, "text": text}, ensure_ascii=False
        ).encode("utf-8")
        self.assertLessEqual(len(encoded), 60_000)
        result = self._draft(lambda request: _result_with_text(text))
        self.assertEqual(result["status"], "generated")

    def test_open_questions_are_capped_to_five_entries(self) -> None:
        questions = [f"业务问题 {index}：边界是否包含场景 {index}？" for index in range(9)]
        text = json.dumps(
            {
                "documents": {name: "正文内容说明。" * 40 for name in INTAKE_DOCUMENTS},
                "open_questions": questions,
            },
            ensure_ascii=False,
        )
        result = self._draft(lambda request: _result_with_text(text))

        self.assertEqual(result["open_questions"], questions[:5])

    def test_protocol_rejection_of_sensitive_model_output_is_contained(self) -> None:
        """模型输出带敏感样式时协议会拒收结果帧，按可恢复失败处理。"""

        def host_execute(request: AgentBackendRequest) -> AgentBackendResult:
            # 与真实协议一致：携带敏感文本的 final_response 在构造结果时即被拒绝。
            return _result_with_text("文档内容。 password=123456 的配置方式。")

        result = self._draft(host_execute)

        self.assertEqual(result["status"], "failed")
        self.assertIn("- 状态：pending", (self.package / "analysis" / "prd.md").read_text(encoding="utf-8"))

    def test_missing_evidence_reports_skip_without_model_call(self) -> None:
        (self.package / "source" / "requirement.md").unlink()
        calls: list[object] = []

        def host_execute(request: AgentBackendRequest) -> AgentBackendResult:
            calls.append(request)
            return _result_with_text(_valid_model_text())

        result = self._draft(host_execute)

        self.assertEqual(result["status"], "skipped_no_evidence")
        self.assertEqual(calls, [])

    def test_internal_exception_is_contained_and_reported(self) -> None:
        def host_execute(request: AgentBackendRequest) -> AgentBackendResult:
            raise RuntimeError("host crashed")

        result = self._draft(host_execute)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "intake_generation_failed")
        self.assertIn("- 状态：pending", (self.package / "analysis" / "prd.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
