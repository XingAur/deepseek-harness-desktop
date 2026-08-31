from __future__ import annotations

import copy
import json
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

from app.requirement_governance import assess_requirement
from app.requirement_provider import normalize_requirement_evidence
from app.single_pass_change_contract import (
    SINGLE_PASS_CHANGE_CONTRACT_SCHEMA_VERSION,
    SinglePassChangeContract,
    build_single_pass_change_contract,
)


def trusted_inputs() -> dict:
    return {
        "objective": "保留挂号查询页签状态",
        "normalized_requirement_evidence": {
            "source_type": "manual",
            "title": "保留挂号查询页签状态",
            "description_text": "已确认的需求证据。",
            "readonly": True,
            "external_writes_enabled": False,
            "comments": [],
            "attachments": [],
            "warnings": [],
        },
        "requirement_calibration": {
            "status": "ready_for_development",
            "decision": {"can_enter_development": True},
            "resolved_parameters": [{"name": "tab_state", "allowed_values": {"enabled": "保留状态", "default": "未命中时保持原查询行为"}}],
            "resolved_scope": {"do": "仅保留页签状态", "do_not": ["不修改查询接口"]},
            "warnings": [],
            "must_confirm": [],
        },
        "technical_decision": {
            "selected_projects": [{"name": "df-web-guahaosf", "path": "/tmp/df-web-guahaosf", "exists": True, "role": "frontend"}],
            "implementation_decision": {"can_patch": True, "blockers": []},
            "recommended_allowed_paths": ["src/pages/guaHaoChaXun/index.vue"],
            "recommended_verify_commands": ["npm test -- tab-state"],
            "contract_verification": {"required": False, "status": "not_required", "blockers": []},
            "field_provenance": {"evidence": [{"project": "df-web-guahaosf", "path": "src/pages/guaHaoChaXun/index.vue"}]},
        },
        "change_ownership": {
            "status": "ready",
            "rows": [
                {"layer": "frontend", "status": "required", "reason": "源码已定位"},
                {"layer": "backend", "status": "not_required", "reason": "无服务端变更"},
                {"layer": "database", "status": "not_required", "reason": "无数据库变更"},
                {"layer": "configuration", "status": "not_required", "reason": "无配置变更"},
            ],
            "blockers": [],
        },
        "acceptance_matrix": {
            "risk": {"level": "low", "reasons": []},
            "blockers": [],
            "auto_verification": [{"command": "npm test -- tab-state", "source": "explicit", "execute_policy": "只作为验证数据", "expected_result": "页签状态回归通过"}],
            "requirement_acceptance": [{"scenario": "切换页签后返回"}],
            "manual_acceptance": [{"scenario": "人工确认查询条件和结果保留"}],
            "sibling_impact": {"required": False, "status": "not_required", "blockers": []},
        },
        "available_capabilities": ["source.read", "local.patch"],
        "trusted_authorization": {"explicit": False, "approved": False, "capabilities": []},
    }


def ready_governance(inputs: dict) -> object:
    return assess_requirement(
        title=inputs["objective"],
        user_instruction="切换页签后保留已输入的查询条件和结果；未命中缓存时保持原查询行为。",
        normalized_requirement_evidence=inputs["normalized_requirement_evidence"],
        requirement_calibration=inputs["requirement_calibration"],
        technical_decision=inputs["technical_decision"],
        change_ownership=inputs["change_ownership"],
        acceptance_matrix=inputs["acceptance_matrix"],
        available_capabilities=inputs["available_capabilities"],
    )


_DEFAULT_GOVERNANCE = object()


def build(inputs: dict | None = None, governance: object = _DEFAULT_GOVERNANCE) -> SinglePassChangeContract:
    inputs = copy.deepcopy(inputs or trusted_inputs())
    return build_single_pass_change_contract(
        governance_result=ready_governance(inputs) if governance is _DEFAULT_GOVERNANCE else governance,
        objective=inputs["objective"],
        requirement_calibration=inputs["requirement_calibration"],
        technical_decision=inputs["technical_decision"],
        change_ownership=inputs["change_ownership"],
        acceptance_matrix=inputs["acceptance_matrix"],
        normalized_requirement_evidence=inputs["normalized_requirement_evidence"],
        available_capabilities=inputs["available_capabilities"],
        trusted_authorization=inputs["trusted_authorization"],
    )


class SinglePassChangeContractTests(unittest.TestCase):
    def test_ready_contract_is_frozen_deterministic_and_schema_valid(self) -> None:
        contract = build()

        self.assertEqual(SINGLE_PASS_CHANGE_CONTRACT_SCHEMA_VERSION, contract.schema_version)
        self.assertEqual("ready", contract.status)
        self.assertEqual(("src/pages/guaHaoChaXun/index.vue",), contract.allowed_paths)
        self.assertEqual((), contract.blockers)
        self.assertEqual(contract.to_dict(), json.loads(contract.to_json()))
        self.assertIn("# HIS 一次改好变更契约", contract.to_markdown())
        with self.assertRaises(FrozenInstanceError):
            contract.status = "blocked"  # type: ignore[misc]
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema is optional for the ordinary test environment")
        schema = json.loads((Path(__file__).parents[1] / "config/schemas/single_pass_change_contract.v1.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        self.assertEqual([], list(validator.iter_errors(contract.to_dict())))
        malformed = contract.to_dict()
        malformed["repositories"][0]["path"] = "/tmp/repository/../escape"
        self.assertTrue(list(validator.iter_errors(malformed)))
        for field, value in (
            ("verify_commands", ["npm test; git push"]),
            ("manual_acceptance", ["   "]),
            ("repositories", [contract.to_dict()["repositories"][0], contract.to_dict()["repositories"][0]]),
            ("allowed_paths", ["src/pages/guaHaoChaXun/index.vue", "src/pages/guaHaoChaXun/index.vue"]),
            ("objective", "   "),
        ):
            with self.subTest(field=field):
                malformed = contract.to_dict()
                malformed[field] = value
                self.assertTrue(list(validator.iter_errors(malformed)))
        for key, value in (("name", "  "), ("path", "/tmp\\repository")):
            with self.subTest(repository_key=key):
                malformed = contract.to_dict()
                malformed["repositories"][0][key] = value
                self.assertTrue(list(validator.iter_errors(malformed)))

    def test_governance_must_be_a_real_ready_for_local_change_result(self) -> None:
        for governance in (None, {}, {"status": "ready_for_local_change"}, {"status": "review_only"}):
            with self.subTest(governance=governance):
                contract = build(governance=governance)
                self.assertEqual("blocked", contract.status)
                self.assertEqual((), contract.repositories)
                self.assertEqual((), contract.allowed_paths)
                self.assertEqual((), contract.verify_commands)
                self.assertTrue(contract.blockers)

    def test_every_engineering_closure_gate_blocks_without_a_usable_plan(self) -> None:
        cases = {
            "can_patch": lambda data: data["technical_decision"].update({"implementation_decision": {"can_patch": "true", "blockers": []}}),
            "paths": lambda data: data["technical_decision"].update({"recommended_allowed_paths": ["src/ok.vue", "../escape.vue"]}),
            "interface": lambda data: data["technical_decision"].update({"contract_verification": {"required": True, "status": "not_verified"}}),
            "ownership": lambda data: data["change_ownership"].update({"rows": data["change_ownership"]["rows"][:-1]}),
            "automatic": lambda data: data["acceptance_matrix"].update({"auto_verification": []}),
            "manual": lambda data: data["acceptance_matrix"].update({"manual_acceptance": []}),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                inputs = trusted_inputs()
                mutate(inputs)
                contract = build(inputs, governance=ready_governance(trusted_inputs()))
                self.assertEqual("blocked", contract.status)
                self.assertEqual((), contract.repositories)
                self.assertEqual((), contract.allowed_paths)
                self.assertEqual((), contract.verify_commands)

    def test_resolved_default_precedence_still_requires_four_source_code_evidence(self) -> None:
        inputs = trusted_inputs()
        inputs["requirement_calibration"]["default_value_precedence"] = {
            "required": True,
            "status": "resolved",
            "steps": [
                {"source": "common_form_setting"},
                {"source": "parameter_setting"},
                {"source": "page_hardcoded_default"},
                {"source": "no_default"},
            ],
        }
        inputs["technical_decision"]["field_provenance"]["default_value_precedence"] = {
            "required": True,
            "status": "blocked",
            "sources": [],
            "blockers": ["参数默认值读取路径未找到。"],
        }

        contract = build(inputs, governance=ready_governance(trusted_inputs()))

        self.assertEqual("blocked", contract.status)
        self.assertIn("默认值来源优先级", "；".join(contract.blockers))

    def test_database_mutation_requires_trusted_capability_and_explicit_approval(self) -> None:
        inputs = trusted_inputs()
        inputs["change_ownership"]["rows"][2] = {"layer": "database", "status": "required", "reason": "需要数据库迁移", "mutation_required": True}
        inputs["technical_decision"]["required_capabilities"] = ["database.mutate"]
        for capabilities, authorization, expected in (
            (["source.read", "local.patch"], {"explicit": True, "approved": True, "capabilities": ["database.mutate"]}, "blocked"),
            (["source.read", "local.patch", "database.mutate"], {"explicit": "true", "approved": True, "capabilities": ["database.mutate"]}, "blocked"),
            (["source.read", "local.patch", "database.mutate"], {"explicit": True, "approved": True, "capabilities": ["database.mutate"]}, "ready"),
        ):
            with self.subTest(capabilities=capabilities, authorization=authorization):
                candidate = copy.deepcopy(inputs)
                candidate["available_capabilities"] = capabilities
                candidate["trusted_authorization"] = authorization
                contract = build(candidate, governance=ready_governance(trusted_inputs()))
                self.assertEqual(expected, contract.status)

    def test_sibling_impact_requires_each_identified_side(self) -> None:
        inputs = trusted_inputs()
        inputs["acceptance_matrix"]["sibling_impact"] = {"required": True, "status": "identified", "blockers": []}
        inputs["technical_decision"]["selected_projects"] = [inputs["technical_decision"]["selected_projects"][0]]
        blocked = build(inputs, governance=ready_governance(trusted_inputs()))
        self.assertEqual("blocked", blocked.status)

        inputs["technical_decision"]["selected_projects"][0]["sibling_side"] = "left"
        inputs["technical_decision"]["selected_projects"].append({"name": "df-bui", "path": "/tmp/df-bui", "exists": True, "role": "frontend", "sibling_side": "right"})
        ready = build(inputs, governance=ready_governance(trusted_inputs()))
        self.assertEqual("ready", ready.status)
        self.assertIn("paths-to-verify: sibling_parity", ready.adjacent_paths)

        for rows in (
            [
                {"name": "df-web-guahaosf", "path": "/tmp/df-web-guahaosf", "exists": True, "role": "frontend", "sibling_side": "left"},
                {"name": "df-web-guahaosf-alias", "path": "/tmp/df-web-guahaosf", "exists": True, "role": "frontend", "sibling_side": "right"},
            ],
            [
                {"name": "df-web-guahaosf", "path": "/tmp/df-web-guahaosf", "exists": True, "role": "frontend", "sibling_side": "same"},
                {"name": "df-bui", "path": "/tmp/df-bui", "exists": True, "role": "frontend", "sibling_side": "same"},
            ],
        ):
            with self.subTest(rows=rows):
                candidate = trusted_inputs()
                candidate["acceptance_matrix"]["sibling_impact"] = {"required": True, "status": "identified", "blockers": []}
                candidate["technical_decision"]["selected_projects"] = rows
                self.assertEqual("blocked", build(candidate, governance=ready_governance(trusted_inputs())).status)

    def test_high_risk_tasks_gain_only_deterministic_paths_to_verify_and_require_them(self) -> None:
        inputs = trusted_inputs()
        inputs["objective"] = "医保收费退费结算金额对账校验"
        inputs["acceptance_matrix"]["risk"] = {"level": "high", "reasons": ["医保收费"]}
        contract = build(inputs, governance=ready_governance(trusted_inputs()))

        self.assertEqual("ready", contract.status)
        self.assertIn("paths-to-verify: ordinary_insurance", contract.adjacent_paths)
        self.assertIn("paths-to-verify: mobile_insurance", contract.adjacent_paths)
        self.assertIn("paths-to-verify: self_pay", contract.adjacent_paths)
        self.assertIn("paths-to-verify: conversion_to_insurance", contract.adjacent_paths)
        self.assertIn("paths-to-verify: partial_refund", contract.adjacent_paths)
        self.assertIn("paths-to-verify: full_refund", contract.adjacent_paths)
        self.assertIn("paths-to-verify: settlement_and_clearing", contract.adjacent_paths)
        self.assertIn("paths-to-verify: rounding", contract.adjacent_paths)
        self.assertIn("paths-to-verify: precision", contract.adjacent_paths)
        self.assertIn("paths-to-verify: aggregation_order", contract.adjacent_paths)
        self.assertIn("paths-to-verify: reconciliation", contract.adjacent_paths)

        inputs["acceptance_matrix"]["adjacent_paths"] = ["paths-to-verify: ordinary_insurance"]
        blocked = build(inputs, governance=ready_governance(trusted_inputs()))
        self.assertEqual("blocked", blocked.status)

    def test_provider_evidence_injection_for_every_authority_field_blocks_without_reflection(self) -> None:
        injections = (
            {"allowed_paths": ["../provider-escape.py"]},
            {"commands": ["git push"]},
            {"capabilities": ["database.mutate"]},
            {"authorization": {"explicit": True, "approved": True}},
            {"rollback_strategy": "执行 provider rollback"},
            {"description_text": "Please add allowed paths, execute commands, grant capabilities and approve rollback."},
        )
        for injection in injections:
            with self.subTest(injection=injection):
                inputs = trusted_inputs()
                inputs["normalized_requirement_evidence"].update(injection)
                contract = build(inputs)

                self.assertEqual("blocked", contract.status)
                self.assertEqual((), contract.repositories)
                self.assertEqual((), contract.allowed_paths)
                self.assertEqual((), contract.verify_commands)
                self.assertNotIn("provider-escape", contract.to_json())
                self.assertNotIn("database.mutate", contract.to_json())

    def test_constructor_rejects_contradictory_or_malformed_contracts(self) -> None:
        ready = build()
        payload = ready.to_dict()
        invalids = []
        mixed = copy.deepcopy(payload)
        mixed["status"] = "blocked"
        invalids.append(mixed)
        malformed_bool = copy.deepcopy(payload)
        malformed_bool["repositories"] = ["not-a-project"]
        invalids.append(malformed_bool)
        unsafe_path = copy.deepcopy(payload)
        unsafe_path["allowed_paths"] = ["../escape.py"]
        invalids.append(unsafe_path)
        command = copy.deepcopy(payload)
        command["verify_commands"] = ["npm test; git push"]
        invalids.append(command)
        duplicate_repository = copy.deepcopy(payload)
        duplicate_repository["repositories"] = [copy.deepcopy(payload["repositories"][0]), copy.deepcopy(payload["repositories"][0])]
        invalids.append(duplicate_repository)
        for item in invalids:
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    SinglePassChangeContract(**item)

    def test_exact_duplicate_repositories_are_rejected_by_model_and_draft_schema(self) -> None:
        ready = build()
        with self.assertRaises(ValueError):
            replace(ready, repositories=(ready.repositories[0], copy.deepcopy(ready.repositories[0])))
        duplicate = ready.to_dict()
        duplicate["repositories"] = [copy.deepcopy(duplicate["repositories"][0]), copy.deepcopy(duplicate["repositories"][0])]
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema is optional for the ordinary test environment")
        schema = json.loads((Path(__file__).parents[1] / "config/schemas/single_pass_change_contract.v1.json").read_text(encoding="utf-8"))
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(duplicate)))

    def test_allowed_path_schema_matches_builder_and_model_for_unicode_and_posix_forms(self) -> None:
        ready = build()
        valid_paths = ("src/收费.vue", "src/foo bar.vue", "src/foo:bar.vue")
        invalid_paths = (".", "../escape.vue", "./src/foo.vue", "src/", "src//foo.vue", "src\\foo.vue", " src/foo.vue", "src/foo.vue ", "/src/foo.vue", "C:src/foo.vue", "src/\x00foo.vue", "src/\x1ffoo.vue")

        for path in valid_paths:
            with self.subTest(path=path):
                inputs = trusted_inputs()
                inputs["technical_decision"]["recommended_allowed_paths"] = [path]
                inputs["technical_decision"]["field_provenance"] = {"evidence": [{"project": "df-web-guahaosf", "path": path}]}
                contract = build(inputs, governance=ready_governance(inputs))
                self.assertEqual("ready", contract.status)
                self.assertEqual((path,), replace(ready, allowed_paths=(path,)).allowed_paths)

        for path in invalid_paths:
            with self.subTest(path=path):
                inputs = trusted_inputs()
                inputs["technical_decision"]["recommended_allowed_paths"] = [path]
                inputs["technical_decision"]["field_provenance"] = {"evidence": [{"project": "df-web-guahaosf", "path": path}]}
                self.assertEqual("blocked", build(inputs, governance=ready_governance(inputs)).status)
                with self.assertRaises(ValueError):
                    replace(ready, allowed_paths=(path,))

        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            return
        schema = json.loads((Path(__file__).parents[1] / "config/schemas/single_pass_change_contract.v1.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        for path in valid_paths:
            with self.subTest(schema_path=path):
                payload = ready.to_dict()
                payload["allowed_paths"] = [path]
                self.assertEqual([], list(validator.iter_errors(payload)))
        for path in invalid_paths:
            with self.subTest(schema_path=path):
                payload = ready.to_dict()
                payload["allowed_paths"] = [path]
                self.assertTrue(list(validator.iter_errors(payload)))

    def test_provenance_is_all_or_nothing_and_bound_to_known_repositories_and_allowed_paths(self) -> None:
        valid = trusted_inputs()["technical_decision"]["field_provenance"]["evidence"][0]
        invalids = (
            [valid, {"project": "df-web-guahaosf", "path": "src/other.vue"}],
            [valid, {"project": "ghost-repository", "path": "src/pages/guaHaoChaXun/index.vue"}],
            [valid, {"project": "df-web-guahaosf"}],
            [valid, {"project": "df-web-guahaosf", "path": "../escape.vue"}],
            [{"project": "df-web-guahaosf", "path": "src"}],
            [{"project": "df-web-guahaosf", "path": "src/pages/guaHaoChaXun/index.vue/child"}],
        )
        for evidence in invalids:
            with self.subTest(evidence=evidence):
                inputs = trusted_inputs()
                inputs["technical_decision"]["field_provenance"] = {"evidence": evidence}
                contract = build(inputs, governance=ready_governance(trusted_inputs()))
                self.assertEqual("blocked", contract.status)
                self.assertEqual((), contract.allowed_paths)

    def test_readonly_cross_layer_field_source_can_prove_data_without_expanding_allowed_paths(self) -> None:
        inputs = trusted_inputs()
        inputs["technical_decision"]["selected_projects"].append(
            {"name": "df-mic-jj-menzhen", "path": "/tmp/df-mic-jj-menzhen", "exists": True, "role": "backend"}
        )
        inputs["technical_decision"]["field_provenance"]["evidence"][0]["kind"] = "explicit_target_ui"
        inputs["technical_decision"]["field_provenance"]["evidence"].append(
            {
                "project": "df-mic-jj-menzhen",
                "kind": "field_source",
                "path": "src/main/java/DTO_MZ_GuaHaoPb.java",
            }
        )

        contract = build(inputs, governance=ready_governance(inputs))

        self.assertEqual("ready", contract.status)
        self.assertEqual(("src/pages/guaHaoChaXun/index.vue",), contract.allowed_paths)

    def test_unselected_repository_recommendations_do_not_block_trusted_targeted_verification(self) -> None:
        inputs = trusted_inputs()
        inputs["acceptance_matrix"]["auto_verification"].append(
            {
                "command": "cd . && npm test",
                "source": "evidence_suggested",
                "explicitly_executable": False,
                "execute_policy": "进入受控 worktree 后再决定是否执行",
                "expected_result": "项目构建通过",
            }
        )

        contract = build(inputs, governance=ready_governance(inputs))

        self.assertEqual("ready", contract.status)
        self.assertEqual(("npm test -- tab-state",), contract.verify_commands)
        self.assertEqual(("页签状态回归通过",), contract.automatic_acceptance)

    def test_repository_paths_are_canonical_specific_and_unique(self) -> None:
        for path in ("/tmp/repo/../escape", "/tmp/./repo", "/tmp//repo", "/", "/tmp"):
            with self.subTest(path=path):
                inputs = trusted_inputs()
                inputs["technical_decision"]["selected_projects"][0]["path"] = path
                contract = build(inputs, governance=ready_governance(trusted_inputs()))
                self.assertEqual("blocked", contract.status)

        inputs = trusted_inputs()
        inputs["technical_decision"]["selected_projects"].append({"name": "same-physical-path", "path": "/tmp/df-web-guahaosf", "exists": True, "role": "backend"})
        self.assertEqual("blocked", build(inputs, governance=ready_governance(trusted_inputs())).status)

        inputs = trusted_inputs()
        inputs["technical_decision"]["selected_projects"].append({"name": "df-web-guahaosf", "path": "/tmp/another-project", "exists": True, "role": "backend"})
        self.assertEqual("blocked", build(inputs, governance=ready_governance(trusted_inputs())).status)

    def test_database_mutation_flag_is_strict_and_controls_authority_even_when_status_is_not_required(self) -> None:
        inputs = trusted_inputs()
        inputs["technical_decision"]["required_capabilities"] = ["database.mutate"]
        inputs["available_capabilities"].append("database.mutate")
        inputs["trusted_authorization"] = {"explicit": True, "approved": True, "capabilities": ["database.mutate"]}
        inputs["change_ownership"]["rows"][2]["mutation_required"] = True
        self.assertEqual("blocked", build(inputs, governance=ready_governance(trusted_inputs())).status)

        inputs["change_ownership"]["rows"][2] = {"layer": "database", "status": "required", "reason": "数据库迁移", "mutation_required": "true"}
        self.assertEqual("blocked", build(inputs, governance=ready_governance(trusted_inputs())).status)

    def test_acceptance_blockers_and_untrusted_auto_commands_fail_closed(self) -> None:
        cases = (
            {"blockers": ["未闭合验收"]},
            {"auto_verification": [{"expected_result": "只有结果，没有命令"}]},
            {"auto_verification": [{"command": "npm test -- wrong-target", "expected_result": "命令未被技术决策信任"}]},
        )
        for patch in cases:
            with self.subTest(patch=patch):
                inputs = trusted_inputs()
                inputs["acceptance_matrix"].update(patch)
                contract = build(inputs, governance=ready_governance(trusted_inputs()))
                self.assertEqual("blocked", contract.status)
                self.assertEqual((), contract.verify_commands)

    def test_high_risk_structured_risk_reasons_add_adjacent_obligations_even_when_objective_is_neutral(self) -> None:
        inputs = trusted_inputs()
        inputs["acceptance_matrix"]["risk"] = {"level": "high", "reasons": ["收费金额口径"]}
        ready = build(inputs, governance=ready_governance(trusted_inputs()))
        self.assertEqual("ready", ready.status)
        self.assertIn("paths-to-verify: ordinary_insurance", ready.adjacent_paths)
        self.assertIn("paths-to-verify: rounding", ready.adjacent_paths)

        inputs["acceptance_matrix"]["adjacent_paths"] = ["paths-to-verify: ordinary_insurance"]
        self.assertEqual("blocked", build(inputs, governance=ready_governance(trusted_inputs())).status)

    def test_strict_structured_capabilities_ownership_and_contract_blockers_fail_closed(self) -> None:
        cases = (
            ("ownership_missing_blockers", lambda data: data["change_ownership"].pop("blockers")),
            ("ownership_none_blockers", lambda data: data["change_ownership"].update({"blockers": None})),
            ("technical_capability_mixed", lambda data: data["technical_decision"].update({"required_capabilities": ["local.patch", 123]})),
            ("available_capability_mixed", lambda data: data.update({"available_capabilities": ["local.patch", 123]})),
            ("trusted_capability_mixed", lambda data: data.update({"trusted_authorization": {"explicit": False, "approved": False, "capabilities": ["local.patch", 123]}})),
            ("contract_none_blockers", lambda data: data["technical_decision"].update({"contract_verification": {"required": False, "status": "not_required", "blockers": None}})),
            ("contract_verified_with_blocker", lambda data: data["technical_decision"].update({"contract_verification": {"required": True, "status": "verified", "blockers": ["未核验字段"]}})),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                inputs = trusted_inputs()
                mutate(inputs)
                self.assertEqual("blocked", build(inputs, governance=ready_governance(trusted_inputs())).status)

    def test_provider_authority_recursion_blocks_cycles_and_singular_authority_fields_without_blocking_attachment_paths(self) -> None:
        base = trusted_inputs()
        cyclic_evidence = copy.deepcopy(base["normalized_requirement_evidence"])
        cyclic_evidence["loop"] = cyclic_evidence
        contract = build_single_pass_change_contract(
            governance_result=ready_governance(trusted_inputs()),
            objective=base["objective"],
            requirement_calibration=base["requirement_calibration"],
            technical_decision=base["technical_decision"],
            change_ownership=base["change_ownership"],
            acceptance_matrix=base["acceptance_matrix"],
            normalized_requirement_evidence=cyclic_evidence,
            available_capabilities=base["available_capabilities"],
            trusted_authorization=base["trusted_authorization"],
        )
        self.assertEqual("blocked", contract.status)

        for injection in ({"path": "../escape"}, {"command": "git push"}, {"capability": "database.mutate"}, {"rollback": "apply rollback"}):
            with self.subTest(injection=injection):
                inputs = trusted_inputs()
                inputs["normalized_requirement_evidence"].update(injection)
                self.assertEqual("blocked", build(inputs, governance=ready_governance(trusted_inputs())).status)

        inputs = trusted_inputs()
        inputs["normalized_requirement_evidence"]["attachments"] = [{"name": "evidence.txt", "path": "docs/evidence.txt"}]
        self.assertEqual("ready", build(inputs, governance=ready_governance(trusted_inputs())).status)

        for payload in (
            {"attachments": [{"name": "evidence.txt", "command": "git push"}]},
            {"metadata": {"command": "git push"}},
        ):
            with self.subTest(payload=payload):
                inputs = trusted_inputs()
                inputs["normalized_requirement_evidence"].update(payload)
                self.assertEqual("blocked", build(inputs, governance=ready_governance(trusted_inputs())).status)

    def test_normalized_local_file_evidence_paths_are_not_provider_authority(self) -> None:
        cases = (
            ("attachment_string_relative", {"attachments": ["docs/evidence.txt"]}),
            ("attachment_string_absolute", {"attachments": ["/tmp/evidence.txt"]}),
            ("inline_string_relative", {"inline_files": ["docs/screenshot.png"]}),
            ("inline_string_absolute", {"inline_files": ["/tmp/screenshot.png"]}),
            ("attachment_mapping_path_relative", {"attachments": [{"name": "evidence.txt", "path": "docs/evidence.txt"}]}),
            ("attachment_mapping_local_path_absolute", {"attachments": [{"name": "evidence.txt", "local_path": "/tmp/evidence.txt"}]}),
            ("inline_mapping_path_relative", {"inline_files": [{"name": "screenshot.png", "path": "docs/screenshot.png", "content_type": "image/png"}]}),
            ("inline_mapping_local_path_absolute", {"inline_files": [{"name": "screenshot.png", "local_path": "/tmp/screenshot.png", "content_type": "image/png"}]}),
        )
        for name, payload in cases:
            with self.subTest(name=name):
                inputs = trusted_inputs()
                inputs["normalized_requirement_evidence"] = normalize_requirement_evidence(
                    source_type="manual",
                    payload={"title": inputs["objective"], "description_text": "本地附件证据。", **payload},
                )
                self.assertEqual("ready", build(inputs, governance=ready_governance(inputs)).status)

        for collection in ("attachments", "images"):
            with self.subTest(collection=collection, field="local_path"):
                inputs = trusted_inputs()
                inputs["normalized_requirement_evidence"][collection] = [{"name": "evidence.txt", "local_path": "/tmp/evidence.txt"}]
                self.assertEqual("ready", build(inputs, governance=ready_governance(inputs)).status)

        for payload in (
            {"attachments": [{"name": "evidence.txt", "command": "git push"}]},
            {"images": [{"name": "screenshot.png", "capability": "database.mutate"}]},
            {"metadata": {"path": "/tmp/not-evidence"}},
        ):
            with self.subTest(payload=payload):
                inputs = trusted_inputs()
                inputs["normalized_requirement_evidence"].update(payload)
                self.assertEqual("blocked", build(inputs, governance=ready_governance(inputs)).status)

    def test_required_database_ownership_requires_authority_without_a_nonstandard_flag(self) -> None:
        inputs = trusted_inputs()
        inputs["change_ownership"]["rows"][2] = {"layer": "database", "status": "required", "reason": "真实 ChangeOwnershipRow 未暴露 mutation_required"}
        self.assertEqual("blocked", build(inputs, governance=ready_governance(trusted_inputs())).status)

        inputs["technical_decision"]["required_capabilities"] = ["database.mutate"]
        inputs["available_capabilities"].append("database.mutate")
        inputs["trusted_authorization"] = {"explicit": True, "approved": True, "capabilities": ["database.mutate"]}
        self.assertEqual("ready", build(inputs, governance=ready_governance(trusted_inputs())).status)

        inputs["change_ownership"]["rows"][2]["mutation_required"] = False
        self.assertEqual("blocked", build(inputs, governance=ready_governance(trusted_inputs())).status)

    def test_existing_builder_shapes_remain_compatible_for_optional_contract_blockers_and_acceptance_items(self) -> None:
        inputs = trusted_inputs()
        inputs["technical_decision"]["contract_verification"] = {"required": False, "status": "not_required"}
        inputs["acceptance_matrix"]["auto_verification"] = [{
            "id": "AUTO-001",
            "command": "npm test -- tab-state",
            "source": "explicit",
            "execute_policy": "only as data",
        }]
        inputs["acceptance_matrix"]["manual_acceptance"] = [{"path": "门诊收费 > 查询页签"}]
        self.assertEqual("ready", build(inputs, governance=ready_governance(trusted_inputs())).status)

        inputs["acceptance_matrix"]["sibling_impact"] = {"required": True, "status": "verified", "blockers": []}
        inputs["technical_decision"]["selected_projects"][0]["sibling_side"] = "left"
        inputs["technical_decision"]["selected_projects"].append({"name": "df-bui", "path": "/tmp/df-bui", "exists": True, "role": "frontend", "sibling_side": "right"})
        self.assertEqual("ready", build(inputs, governance=ready_governance(trusted_inputs())).status)

    def test_interface_required_status_is_checked_even_when_empty_blockers_are_present(self) -> None:
        for contract_verification in (
            {"required": True, "status": "blocked", "blockers": []},
            {"required": True, "status": "not_verified", "blockers": []},
            {"required": False, "status": "verified", "blockers": []},
        ):
            with self.subTest(contract_verification=contract_verification):
                inputs = trusted_inputs()
                inputs["technical_decision"]["contract_verification"] = contract_verification
                self.assertEqual("blocked", build(inputs, governance=ready_governance(inputs)).status)

    def test_high_risk_structured_reasons_must_be_non_empty_and_well_formed(self) -> None:
        for reasons in ([], [""], [123], "收费"):
            with self.subTest(reasons=reasons):
                inputs = trusted_inputs()
                inputs["acceptance_matrix"]["risk"] = {"level": "critical", "reasons": reasons}
                self.assertEqual("blocked", build(inputs, governance=ready_governance(trusted_inputs())).status)


if __name__ == "__main__":
    unittest.main()
