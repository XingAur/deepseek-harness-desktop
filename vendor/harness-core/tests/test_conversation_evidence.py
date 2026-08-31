from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.conversation_evidence import conversation_code_locator_text, load_conversation_evidence_file
from app.error_chain_closure import build_error_chain_closure


class ConversationEvidenceTests(unittest.TestCase):
    def test_reference_document_screenshot_does_not_trigger_ui_error_chain(self) -> None:
        closure = build_error_chain_closure(
            demand_text="线上医保退费需求，接口参数见相关截图和附件。",
            conversation_evidence=None,
            requirement_evidence={
                "visual_evidence": {
                    "facts": [{
                        "fact_type": "document",
                        "document_type": "医保退费接口参数表",
                        "visible_text": "ecToken payAuthNo",
                        "key_facts": "ecToken 与 payAuthNo 不能同时为空。",
                    }],
                },
            },
            technical_decision={"selected_projects": [], "field_provenance": {}},
        )

        self.assertFalse(closure["required"])
        self.assertEqual("not_required", closure["status"])
        self.assertTrue(closure["can_modify"])

    def test_explicit_non_high_risk_scope_does_not_trigger_error_chain(self) -> None:
        closure = build_error_chain_closure(
            demand_text="页面显示一个只读字段，不涉及收费、医保或结算。",
            conversation_evidence=None,
            technical_decision={"selected_projects": [], "field_provenance": {}},
        )

        self.assertFalse(closure["required"])
        self.assertEqual("not_required", closure["status"])

    def test_user_correction_is_structured_and_keeps_only_a_hashed_conversation_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "conversation.json"
            source.write_text(json.dumps({
                "host": "codex-app",
                "conversation_id": "private-thread-id",
                "messages": [
                    {"id": "u1", "role": "user", "content": "截图明确提示国家医保挂号预结算失败。"},
                    {"id": "u2", "role": "user", "content": "menZhenTfYjs 内调用医保预结算，不要猜成别的入口。"},
                ],
                "confirmed_facts": [{
                    "id": "chain", "kind": "user_correction", "statement": "menZhenTfYjs 调用国家医保挂号预结算。",
                    "source_message_ids": ["u2"], "required_code_terms": ["menZhenTfYjs"], "must_not_contradict": True,
                }],
            }, ensure_ascii=False), encoding="utf-8")

            evidence = load_conversation_evidence_file(source)

        self.assertNotEqual("private-thread-id", evidence["conversation_id"])
        self.assertEqual("menZhenTfYjs", evidence["confirmed_facts"][0]["required_code_terms"][0])
        self.assertEqual(["u2"], evidence["confirmed_facts"][0]["source_message_ids"])
        self.assertIn("menZhenTfYjs", conversation_code_locator_text(evidence))

    def test_high_risk_screenshot_chain_never_closes_when_any_source_hop_is_missing(self) -> None:
        conversation = {
            "messages": [{"id": "u1", "role": "user", "content": "截图显示国家医保挂号预结算失败。"}],
            "confirmed_facts": [{
                "id": "chain", "kind": "screenshot_observed", "statement": "menZhenTfYjs 调用国家医保挂号预结算。",
                "source_message_ids": ["u1"], "required_code_terms": ["menZhenTfYjs"], "must_not_contradict": True,
            }],
        }
        closure = build_error_chain_closure(
            demand_text="医生申请退费时提示国家医保预结算失败。",
            conversation_evidence=conversation,
            technical_decision={"selected_projects": [], "field_provenance": {}},
        )

        self.assertTrue(closure["required"])
        self.assertFalse(closure["can_modify"])
        self.assertEqual("blocked_needs_error_chain_closure", closure["status"])
        self.assertIn("click_event", {item["name"] for item in closure["steps"] if item["status"] == "blocked"})

    def test_high_risk_error_without_exported_conversation_still_fails_closed(self) -> None:
        closure = build_error_chain_closure(
            demand_text="退药后点击退费按钮报患者在院不能进行医保登记。",
            conversation_evidence=None,
            technical_decision={"selected_projects": [], "field_provenance": {}},
        )

        self.assertTrue(closure["required"])
        self.assertFalse(closure["can_modify"])
        self.assertEqual("blocked_needs_error_chain_closure", closure["status"])
        self.assertIn("screenshot_error_text", {item["name"] for item in closure["steps"] if item["status"] == "blocked"})

    def test_missing_visual_facts_fails_closed_without_crashing(self) -> None:
        closure = build_error_chain_closure(
            demand_text="退费时提示医保预结算失败。",
            conversation_evidence=None,
            requirement_evidence={"visual_evidence": {"facts": None}},
            technical_decision={"selected_projects": [], "field_provenance": {}},
        )

        self.assertTrue(closure["required"])
        self.assertFalse(closure["can_modify"])
        self.assertEqual("blocked_needs_error_chain_closure", closure["status"])

    def test_chain_closes_only_with_linked_frontend_controller_and_external_call_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "frontend"
            backend = root / "backend"
            frontend.mkdir()
            backend.mkdir()
            (frontend / "refund.vue").write_text(
                "<button @click=\"menZhenTfYjs\"/> const route={path:'/menZhenTf'}; api.post('winbff-guahaosf/shouFei/menZhenTfYjs')",
                encoding="utf-8",
            )
            (backend / "RefundController.java").write_text(
                "class RefundController {\n"
                "  public Object menZhenTfYjs() { ybService.preSettlement(); }\n"
                "}\n",
                encoding="utf-8",
            )
            conversation = {
                "messages": [{"id": "u1", "role": "user", "content": "截图显示国家医保挂号预结算失败。"}],
                "confirmed_facts": [{
                    "id": "chain", "kind": "screenshot_observed", "statement": "menZhenTfYjs 调用国家医保挂号预结算。",
                    "source_message_ids": ["u1"], "required_code_terms": ["menZhenTfYjs"], "must_not_contradict": True,
                }],
            }
            technical = {
                "selected_projects": [
                    {"name": "frontend", "path": str(frontend), "role": "frontend", "exists": True},
                    {"name": "backend", "path": str(backend), "role": "backend", "exists": True},
                ],
                "field_provenance": {"service_graph": {"branches": [{
                    "endpoint": "/winbff-guahaosf/shouFei/menZhenTfYjs", "target_project": "backend",
                    "target_path": "backend:RefundController.java", "controller_verified": True,
                }]}},
            }

            closure = build_error_chain_closure(
                demand_text="医生申请退费时提示国家医保预结算失败。",
                conversation_evidence=conversation,
                technical_decision=technical,
            )

        self.assertEqual("closed", closure["status"])
        self.assertTrue(closure["can_modify"])
        self.assertTrue(all(item["status"] == "pass" for item in closure["steps"]))

    def test_external_call_does_not_close_from_generic_medical_text_in_controller(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "frontend"
            backend = root / "backend"
            frontend.mkdir()
            backend.mkdir()
            (frontend / "refund.vue").write_text(
                "<button @click=\"menZhenTfYjs\"/> const route={path:'/menZhenTf'}; "
                "api.post('winbff-guahaosf/shouFei/menZhenTfYjs')",
                encoding="utf-8",
            )
            (backend / "RefundController.java").write_text(
                "class RefundController {\n"
                "  String note = \"医保服务说明\";\n"
                "  public Object menZhenTfYjs() { return shouFeiService.menZhenTfYjs(); }\n"
                "}\n",
                encoding="utf-8",
            )
            conversation = {
                "messages": [{"id": "u1", "role": "user", "content": "截图显示国家医保挂号预结算失败。"}],
                "confirmed_facts": [{
                    "id": "chain", "kind": "screenshot_observed", "statement": "menZhenTfYjs 调用国家医保挂号预结算。",
                    "source_message_ids": ["u1"], "required_code_terms": ["menZhenTfYjs"], "must_not_contradict": True,
                }],
            }
            technical = {
                "selected_projects": [
                    {"name": "frontend", "path": str(frontend), "role": "frontend", "exists": True},
                    {"name": "backend", "path": str(backend), "role": "backend", "exists": True},
                ],
                "field_provenance": {"service_graph": {"branches": [{
                    "endpoint": "/winbff-guahaosf/shouFei/menZhenTfYjs", "target_project": "backend",
                    "target_path": "backend:RefundController.java", "controller_verified": True,
                }]}},
            }

            closure = build_error_chain_closure(
                demand_text="医生申请退费时提示国家医保预结算失败。",
                conversation_evidence=conversation,
                technical_decision=technical,
            )

        self.assertEqual("blocked_needs_error_chain_closure", closure["status"])
        self.assertFalse(closure["can_modify"])
        steps = {item["name"]: item for item in closure["steps"]}
        self.assertEqual("pass", steps["backend_branch"]["status"])
        self.assertEqual("blocked", steps["external_insurance_call"]["status"])

    def test_external_call_follows_exact_service_method_into_private_helper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "frontend"
            backend = root / "backend"
            frontend.mkdir()
            (backend / "src").mkdir(parents=True)
            (frontend / "refund.vue").write_text(
                "<button @click=\"menZhenTfYjs\"/> const route={path:'/menZhenTf'}; "
                "api.post('winbff-guahaosf/shouFei/menZhenTfYjs')",
                encoding="utf-8",
            )
            (backend / "RefundController.java").write_text(
                "class RefundController {\n"
                "  public Object menZhenTfYjs() { return shouFeiService.menZhenTfYjs(); }\n"
                "}\n",
                encoding="utf-8",
            )
            (backend / "src" / "RefundServiceImpl.java").write_text(
                "class RefundServiceImpl {\n"
                "  public Object menZhenTfYjs() { return this.createShouFei(); }\n"
                "  private Object createShouFei() { return yiBaoServiceApi.menZhenYjs(); }\n"
                "}\n",
                encoding="utf-8",
            )
            conversation = {
                "messages": [{"id": "u1", "role": "user", "content": "截图显示国家医保挂号预结算失败。"}],
                "confirmed_facts": [{
                    "id": "chain", "kind": "screenshot_observed", "statement": "menZhenTfYjs 调用国家医保挂号预结算。",
                    "source_message_ids": ["u1"], "required_code_terms": ["menZhenTfYjs"], "must_not_contradict": True,
                }],
            }
            technical = {
                "selected_projects": [
                    {"name": "frontend", "path": str(frontend), "role": "frontend", "exists": True},
                    {"name": "backend", "path": str(backend), "role": "backend", "exists": True},
                ],
                "field_provenance": {"service_graph": {"branches": [{
                    "endpoint": "/winbff-guahaosf/shouFei/menZhenTfYjs", "target_project": "backend",
                    "target_path": "backend:RefundController.java", "controller_verified": True,
                }]}},
            }

            closure = build_error_chain_closure(
                demand_text="医生申请退费时提示国家医保预结算失败。",
                conversation_evidence=conversation,
                technical_decision=technical,
            )

        self.assertEqual("closed", closure["status"])
        self.assertTrue(closure["can_modify"])
        external = next(item for item in closure["steps"] if item["name"] == "external_insurance_call")
        self.assertIn("RefundServiceImpl.java", external["evidence"]["paths"][0])

    def test_chain_does_not_close_from_an_unrelated_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "frontend"
            backend = root / "backend"
            frontend.mkdir()
            backend.mkdir()
            (frontend / "refund.vue").write_text(
                "<button @click=\"menZhenTfYjs\"/> "
                "api.post('winbff-guahaosf/shouFei/menZhenTfYjs') "
                "api.post('winbff-guahaosf/shouFei/checkZiJinZh')",
                encoding="utf-8",
            )
            (backend / "RefundController.java").write_text("ybService.preSettlement();", encoding="utf-8")
            conversation = {
                "messages": [{"id": "u1", "role": "user", "content": "截图显示医保预结算失败。"}],
                "confirmed_facts": [{
                    "id": "chain", "kind": "user_correction",
                    "statement": "实际相关调用为 menZhenTfYjs。",
                    "source_message_ids": ["u1"],
                    "required_code_terms": ["menZhenTfYjs", "menZhenTf"],
                    "must_not_contradict": True,
                }],
            }
            technical = {
                "selected_projects": [
                    {"name": "frontend", "path": str(frontend), "role": "frontend", "exists": True},
                    {"name": "backend", "path": str(backend), "role": "backend", "exists": True},
                ],
                "field_provenance": {"service_graph": {"branches": [{
                    "endpoint": "/winbff-guahaosf/shouFei/checkZiJinZh",
                    "source_path": "frontend:refund.vue",
                    "target_project": "backend",
                    "target_path": "backend:RefundController.java",
                    "controller_verified": True,
                }]}},
            }

            closure = build_error_chain_closure(
                demand_text="退药后点击退费按钮提示医保预结算失败。",
                conversation_evidence=conversation,
                technical_decision=technical,
            )

        self.assertEqual("blocked_needs_error_chain_closure", closure["status"])
        self.assertFalse(closure["can_modify"])
        steps = {item["name"]: item for item in closure["steps"]}
        self.assertEqual("pass", steps["frontend_api"]["status"])
        self.assertEqual(
            ["/winbff-guahaosf/shouFei/menZhenTfYjs"],
            steps["frontend_api"]["evidence"]["endpoints"],
        )
        self.assertEqual("blocked", steps["backend_branch"]["status"])
