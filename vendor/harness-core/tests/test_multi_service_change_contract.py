from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path

from app.multi_service_change_contract import (
    build_evidence_choices,
    build_multi_service_change_contract,
    discover_runtime_validation,
    suggest_runtime_commands,
)


def _decision(*, targets=None, controller_verified=True):
    targets = targets if targets is not None else [
        {
            "scope": "candidate_change",
            "source_project": "df-web-yibaogl",
            "source_paths": ["df-web-yibaogl:src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue"],
            "entry_paths": ["df-web-yibaogl:src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue"],
            "endpoint": "/yb-yibaogl/YiBaoSpXmWh/batchUpload",
            "target_project": "df-mic-yibaogl",
            "target_path": "df-mic-yibaogl:src/main/java/YiBaoSpXmWhController.java",
            "controller_verified": controller_verified,
        },
        {
            "scope": "candidate_change",
            "source_project": "df-web-yibaogl",
            "source_paths": ["df-web-yibaogl:src/apis/yiBaoSpXmWh.js"],
            "entry_paths": ["df-web-yibaogl:src/views/yiBaoMlDz/yiBaoSpXmWh/index.vue"],
            "endpoint": "/winbff-yibaogl/YiBaoSpXmWh/page",
            "target_project": "df-bff-yibaogl",
            "target_path": "df-bff-yibaogl:src/main/java/YiBaoSpXmWhController.java",
            "controller_verified": True,
        },
    ]
    return {
        "change_type": "multi_service_feature",
        "can_patch": False,
        "candidate_change_targets": targets,
        "change_plan": {"status": "ready_for_contract"},
        "blockers": ["需求包含多个页面、操作和数据字段。"],
    }


def _decision_with_architecture(*, status="auto_resolved"):
    decision = _decision()
    decision["change_plan"].update(
        {
            "architecture_decision": status,
            "recommended_architecture_option_id": "bff_raw_sources_yibaogl_enrichment",
            "architecture_options": [
                {
                    "id": "bff_raw_sources_yibaogl_enrichment",
                    "label": "BFF 原始目录 + 医保服务补充属性",
                }
            ],
            "architecture_evidence": [{"charge_api_proven": True, "drug_api_proven": True}],
            "architecture_requirements": [
                {
                    "id": "bff_raw_sources_yibaogl_enrichment",
                    "endpoint_contract_status": "verified",
                    "change_surfaces": ["df-bff-jichufw: 原始目录 API"],
                }
            ],
        }
    )
    return decision


class MultiServiceChangeContractTests(unittest.TestCase):
    def test_unproven_architecture_contract_keeps_static_api_evidence_and_gap(self) -> None:
        decision = _decision_with_architecture()
        decision["change_plan"]["architecture_requirements"][0].update(
            {
                "endpoint_contract_status": "not_proven",
                "contract_gap": ["missing_drug_http_route"],
                "existing_api_contracts": [
                    {
                        "http_method": "POST",
                        "route": "/shouFeiXm/getAndShouFeiXmJgPage",
                        "request_types": ["DTO_GY_ShouFeiXmYiBaoCx"],
                        "response_types": ["DTO_PageData"],
                        "upstream_api_calls": [{"api": "ShouFeiXmApi", "method": "getAndShouFeiXmJgPage"}],
                    }
                ],
                "contract_proposal": {
                    "status": "review_required",
                    "decision": "new_bff_unified_directory_contract_required",
                    "write_ready": False,
                    "route": {"candidate_http_method": "POST", "candidate_path": None},
                    "required_evidence_before_worktree": ["YaoPinZdApi 的 HTTP 契约"],
                },
            }
        )
        contract = build_multi_service_change_contract(
            technical_decision=decision,
            governance_ready=True,
            selected_projects=[],
            runtime_validation={"status": "ready", "commands_by_project": {}},
            acceptance={"automatic": ["静态契约证据可追溯"]},
        )

        self.assertEqual("blocked", contract.status)
        self.assertIn("missing_drug_http_route", "\n".join(contract.blockers))
        self.assertIn("ShouFeiXmApi.getAndShouFeiXmJgPage", contract.to_markdown())
        self.assertEqual("review_required", contract.contract_proposals[0]["status"])

    def test_runtime_command_suggestions_are_candidates_not_verified_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "package.json").write_text(
                json.dumps({"scripts": {"lint": "eslint .", "test": "vitest"}}),
                encoding="utf-8",
            )
            self.assertEqual(["npm run lint", "npm run test"], suggest_runtime_commands(str(root)))

    def test_runtime_discovery_uses_repository_package_manager_and_affected_projects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            web = root / "web"
            service = root / "service"
            web.mkdir()
            service.mkdir()
            (web / "package.json").write_text(
                json.dumps({"scripts": {"lint": "eslint .", "test": "vitest"}}),
                encoding="utf-8",
            )
            (web / "yarn.lock").write_text("", encoding="utf-8")
            (service / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
            decision = _decision()
            decision["candidate_change_targets"] = [
                {
                    **decision["candidate_change_targets"][0],
                    "source_project": "web",
                    "target_project": "service",
                }
            ]
            validation = discover_runtime_validation(
                technical_decision=decision,
                selected_projects=[
                    {"name": "web", "path": str(web), "role": "frontend", "exists": True},
                    {"name": "service", "path": str(service), "role": "backend", "exists": True},
                    {"name": "evidence-only", "path": str(root / "missing"), "role": "api", "exists": False},
                ],
            )

            self.assertEqual("ready", validation["status"])
            self.assertEqual(["yarn lint", "yarn test"], validation["commands_by_project"]["web"])
            self.assertEqual(["./gradlew compileJava"], validation["commands_by_project"]["service"])
            self.assertEqual("harness_auto_discovery", validation["source"])
            self.assertNotIn("evidence-only", validation["commands_by_project"])

    def test_blockers_become_explicit_user_choices_with_readonly_fallback(self) -> None:
        gaps, options = build_evidence_choices(
            [
                "运行时验证未就绪；没有逐仓库可执行验证命令，拒绝自动改码。",
                "候选目标 #1 缺少或未验证：target_path, controller_verified。",
            ]
        )

        self.assertEqual({"runtime_validation", "service_evidence"}, {item["id"] for item in gaps})
        self.assertIn("provide_runtime_validation", {item["id"] for item in options})
        self.assertIn("provide_service_evidence", {item["id"] for item in options})
        self.assertIn("readonly_only", {item["id"] for item in options})

    def test_contract_groups_targets_by_repository_and_keeps_relative_paths(self) -> None:
        contract = build_multi_service_change_contract(
            technical_decision=_decision(),
            governance_ready=True,
            selected_projects=[
                {"name": "df-web-yibaogl", "path": "/workspace/df-web-yibaogl", "role": "frontend", "exists": True},
                {"name": "df-bff-yibaogl", "path": "/workspace/df-bff-yibaogl", "role": "backend", "exists": True},
                {"name": "df-mic-yibaogl", "path": "/workspace/df-mic-yibaogl", "role": "backend", "exists": True},
            ],
            runtime_validation={
                "status": "ready",
                "commands_by_project": {
                    "df-web-yibaogl": ["npm run lint -- --no-fix"],
                    "df-bff-yibaogl": ["./gradlew :compileJava"],
                    "df-mic-yibaogl": ["./gradlew :compileJava"],
                },
            },
            acceptance={"automatic": ["目标页面请求参数保持兼容"], "manual": ["批量上传成功" ]},
        )

        self.assertEqual("ready", contract.status)
        self.assertEqual(3, len(contract.repositories))
        self.assertEqual(
            ["src/main/java/YiBaoSpXmWhController.java"],
            contract.repositories["df-mic-yibaogl"]["allowed_paths"],
        )
        self.assertEqual("/workspace/df-mic-yibaogl", contract.repositories["df-mic-yibaogl"]["project_path"])
        self.assertNotIn("df-mic-yibaogl:", " ".join(contract.repositories["df-mic-yibaogl"]["allowed_paths"]))
        self.assertEqual(2, len(contract.targets))
        self.assertEqual("ready", contract.rollback["status"])

    def test_contract_blocks_unverified_controller_or_incomplete_target(self) -> None:
        broken = _decision(controller_verified=False)
        broken["candidate_change_targets"][0]["target_path"] = ""
        contract = build_multi_service_change_contract(
            technical_decision=broken,
            governance_ready=True,
            selected_projects=[
                {"name": "df-web-yibaogl", "path": "/workspace/df-web-yibaogl", "role": "frontend", "exists": True},
                {"name": "df-mic-yibaogl", "path": "/workspace/df-mic-yibaogl", "role": "backend", "exists": True},
            ],
            runtime_validation={"status": "ready", "commands_by_project": {"df-mic-yibaogl": ["./gradlew :compileJava"]}},
            acceptance={"automatic": ["request contract"]},
        )

        self.assertEqual("blocked", contract.status)
        self.assertTrue(any("target_path" in item or "controller_verified" in item for item in contract.blockers))
        self.assertEqual({}, contract.repositories)
        self.assertEqual([], contract.targets)
        self.assertEqual("not_available", contract.rollback["status"])
        self.assertEqual("await_user_choice", contract.continuation["status"])
        self.assertTrue(any(item["id"] == "provide_service_evidence" for item in contract.evidence_options))
        self.assertTrue(any(item["id"] == "readonly_only" for item in contract.evidence_options))

    def test_contract_blocks_without_runtime_validation_and_never_authorizes_patch(self) -> None:
        contract = build_multi_service_change_contract(
            technical_decision=_decision(),
            governance_ready=True,
            selected_projects=[
                {"name": "df-web-yibaogl", "path": "/workspace/df-web-yibaogl", "role": "frontend", "exists": True},
                {"name": "df-bff-yibaogl", "path": "/workspace/df-bff-yibaogl", "role": "backend", "exists": True},
                {"name": "df-mic-yibaogl", "path": "/workspace/df-mic-yibaogl", "role": "backend", "exists": True},
            ],
            runtime_validation={"status": "not_configured"},
            acceptance={"automatic": ["request contract"]},
        )

        self.assertEqual("blocked", contract.status)
        self.assertTrue(any("运行时验证" in item for item in contract.blockers))
        self.assertEqual({}, contract.repositories)
        self.assertEqual("not_available", contract.rollback["status"])
        self.assertEqual("await_user_choice", contract.continuation["status"])

    def test_contract_accepts_the_serialized_technical_decision_shape(self) -> None:
        decision = {
            "implementation_decision": _decision(),
            "field_provenance": {
                "service_graph": {
                    "status": "evidence_ready",
                    "unresolved_endpoints": [],
                }
            },
        }
        contract = build_multi_service_change_contract(
            technical_decision=decision,
            governance_ready=True,
            selected_projects=[
                {"name": "df-web-yibaogl", "path": "/workspace/df-web-yibaogl", "role": "frontend", "exists": True},
                {"name": "df-bff-yibaogl", "path": "/workspace/df-bff-yibaogl", "role": "backend", "exists": True},
                {"name": "df-mic-yibaogl", "path": "/workspace/df-mic-yibaogl", "role": "backend", "exists": True},
            ],
            runtime_validation={
                "status": "ready",
                "commands_by_project": {
                    "df-web-yibaogl": ["npm run lint -- --no-fix"],
                    "df-bff-yibaogl": ["./gradlew :compileJava"],
                    "df-mic-yibaogl": ["./gradlew :compileJava"],
                },
            },
            acceptance={"automatic": ["request contract"]},
        )

        self.assertEqual("ready", contract.status)
        self.assertEqual("ready_for_execution", contract.continuation["status"])

    def test_contract_persists_auto_resolved_architecture_without_user_prompt(self) -> None:
        contract = build_multi_service_change_contract(
            technical_decision=_decision_with_architecture(),
            governance_ready=True,
            selected_projects=[
                {"name": "df-web-yibaogl", "path": "/workspace/df-web-yibaogl", "role": "frontend", "exists": True},
                {"name": "df-bff-yibaogl", "path": "/workspace/df-bff-yibaogl", "role": "backend", "exists": True},
                {"name": "df-mic-yibaogl", "path": "/workspace/df-mic-yibaogl", "role": "backend", "exists": True},
            ],
            runtime_validation={
                "status": "ready",
                "commands_by_project": {
                    "df-web-yibaogl": ["npm run lint -- --no-fix"],
                    "df-bff-yibaogl": ["./gradlew :compileJava"],
                    "df-mic-yibaogl": ["./gradlew :compileJava"],
                },
            },
            acceptance={"automatic": ["request contract"]},
        )

        self.assertEqual("ready", contract.status)
        self.assertEqual("auto_resolved", contract.architecture_decision["status"])
        self.assertEqual(
            "verified",
            contract.architecture_decision["requirements"][0]["endpoint_contract_status"],
        )
        self.assertNotIn("provide_architecture_evidence", {item["id"] for item in contract.evidence_options})

    def test_contract_requests_architecture_choice_only_when_evidence_is_incomplete(self) -> None:
        contract = build_multi_service_change_contract(
            technical_decision=_decision_with_architecture(status="needs_user_choice"),
            governance_ready=True,
            selected_projects=[
                {"name": "df-web-yibaogl", "path": "/workspace/df-web-yibaogl", "role": "frontend", "exists": True},
                {"name": "df-bff-yibaogl", "path": "/workspace/df-bff-yibaogl", "role": "backend", "exists": True},
                {"name": "df-mic-yibaogl", "path": "/workspace/df-mic-yibaogl", "role": "backend", "exists": True},
            ],
            runtime_validation={
                "status": "ready",
                "commands_by_project": {
                    "df-web-yibaogl": ["npm run lint -- --no-fix"],
                    "df-bff-yibaogl": ["./gradlew :compileJava"],
                    "df-mic-yibaogl": ["./gradlew :compileJava"],
                },
            },
            acceptance={"automatic": ["request contract"]},
        )

        self.assertEqual("blocked", contract.status)
        self.assertTrue(any("架构方案" in item for item in contract.blockers))
        self.assertIn("provide_architecture_evidence", {item["id"] for item in contract.evidence_options})

    def test_contract_auto_continues_readonly_for_auto_resolved_contract_gap(self) -> None:
        decision = _decision_with_architecture()
        decision["change_plan"]["architecture_requirements"][0].update(
            {
                "endpoint_contract_status": "not_proven",
                "contract_gap": ["missing_drug_http_route"],
                "contract_proposal": {
                    "status": "review_required",
                    "write_ready": False,
                    "remaining_evidence_before_worktree": ["药品公共 API 的 HTTP 路由"],
                },
            }
        )
        contract = build_multi_service_change_contract(
            technical_decision=decision,
            governance_ready=True,
            selected_projects=[
                {"name": "df-web-yibaogl", "path": "/workspace/df-web-yibaogl", "role": "frontend", "exists": True},
                {"name": "df-bff-yibaogl", "path": "/workspace/df-bff-yibaogl", "role": "backend", "exists": True},
                {"name": "df-mic-yibaogl", "path": "/workspace/df-mic-yibaogl", "role": "backend", "exists": True},
            ],
            runtime_validation={
                "status": "ready",
                "commands_by_project": {
                    "df-web-yibaogl": ["npm run lint"],
                    "df-bff-yibaogl": ["./gradlew compileJava"],
                    "df-mic-yibaogl": ["./gradlew compileJava"],
                },
            },
            acceptance={"automatic": ["静态契约证据可追溯"]},
        )

        self.assertEqual("blocked", contract.status)
        self.assertEqual("auto_continue_readonly", contract.continuation["status"])
        self.assertFalse(contract.continuation["requires_user"])
        self.assertEqual("closed", contract.continuation["write_gate"])


if __name__ == "__main__":
    unittest.main()
