from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.acceptance_contracts import execute_acceptance_contract
from app.core_closure import RequirementContract, build_requirement_contract, review_final_diff
from app.harness import RequirementWorkflowRunner, build_development_entry_status
from app.llm_client import BaseLLMClient, LLMResponse, MockLLMClient
from app.requirement_governance import GovernanceCheck, RequirementGovernanceResult
from app.single_pass_change_contract import SinglePassChangeContract
from app.technical_decision import TechnicalDecisionResult
from app.worktree_executor import WorktreeExecutionResult, extract_unified_diff
from tests.change_context_test_support import ReadyChangeContextService


PAIBAN_DEMAND = (
    "菜单/路由参数 paiBanMs：1 只过滤医生为空的排班；2 只过滤有医生的排班；"
    "空、不传或其他值保持当前默认模式。"
)
ORDERING_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "acceptance_contracts" / "dfhis-31558-ordering.json"


class CoreClosureReportStatusTests(unittest.TestCase):
    def test_ready_for_manual_review_points_to_human_review_and_business_acceptance(self) -> None:
        status = build_development_entry_status(
            {"status": "success", "evaluation_status": "ready_for_manual_review"}
        )

        self.assertEqual("本地验证已通过，可进入人工代码审查与业务验收；未自动提交或发布。", status)


def ready_calibration(*, status: str = "ready_for_development") -> dict:
    return {
        "status": status,
        "decision": {
            "can_enter_development": status == "ready_for_development",
            "needs_human_confirmation": status != "ready_for_development",
        },
        "source_priority": [{"source": "user_instruction", "reason": "用户补充规则优先"}],
        "resolved_parameters": [
            {
                "name": "paiBanMs",
                "location": "route_menu_param",
                "allowed_values": {
                    "1": "只过滤医生为空的排班",
                    "2": "只过滤有医生的排班",
                    "empty": "空、不传或其他值保持当前默认模式",
                },
            }
        ],
        "warnings": [],
    }


def ready_decision(*, paths: list[str] | None = None, verify_commands: list[str] | None = None) -> dict:
    return {
        "project_root": "/tmp/dfhis",
        "selected_projects": [
            {
                "path": "/tmp/dfhis/df-web-guahaosf",
                "name": "df-web-guahaosf",
                "role": "frontend",
                "exists": True,
                "reasons": ["测试工程证据"],
            }
        ],
        "field_provenance": {
            "target_ui_found": True,
            "evidence": [
                {
                    "project": "df-web-guahaosf",
                    "path": "src/pages/yeWuGn/guaHaoSf/index.vue",
                    "reason": "排班页面命中",
                }
            ],
        },
        "implementation_decision": {"can_patch": True, "blockers": []},
        "recommended_allowed_paths": paths if paths is not None else [
            "src/pages/yeWuGn/guaHaoSf/index.vue",
            "src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js",
        ],
        "recommended_verify_commands": verify_commands if verify_commands is not None else [
            "test -f src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js"
        ],
    }


def acceptance_matrix() -> dict:
    return {
        "items": [
            {"id": "AC-1", "statement": "paiBanMs=1 只保留医生为空的排班", "kind": "automatic"},
            {"id": "AC-2", "statement": "paiBanMs=2 只保留有医生的排班", "kind": "automatic"},
            {"id": "AC-3", "statement": "空、不传或其他值保持当前默认模式", "kind": "automatic"},
            {"id": "MANUAL-1", "statement": "挂号页面实际操作验收", "kind": "manual"},
        ]
    }


def archive_default_calibration() -> dict:
    return {
        "status": "ready_for_development",
        "decision": {"can_enter_development": True},
        "source_priority": [{"priority": 1, "source": "user_instruction", "reason": "用户补充规则优先"}],
        "resolved_parameters": [
            {
                "name": "挂号缩减版建档默认值",
                "location": "shared_component_default",
                "allowed_values": {
                    "configured": "新建或清屏后的挂号缩减版按建档同名参数读取默认值",
                    "default": "已有病人、读卡结果和用户已选择的字段不被默认值覆盖",
                },
                "evidence_tokens": [
                    "'建档_证件类型默认值'",
                    "'建档_年龄单位'",
                    "'建档_默认婚姻'",
                ],
                "default_evidence_tokens": ["defaultZhengJianLx", "defaultNianLingDw", "defaultHunYin"],
            }
        ],
        "warnings": [],
    }


def archive_default_acceptance_matrix() -> dict:
    return {
        "items": [
            {"id": "AC-1", "statement": "挂号缩减版读取建档同名默认参数", "kind": "automatic"},
            {"id": "AC-2", "statement": "已有病人、读卡结果和用户已选择字段保持不被默认值覆盖", "kind": "automatic"},
            {"id": "MANUAL-1", "statement": "挂号页面新建和清屏后默认值验收", "kind": "manual"},
        ]
    }


def manual_only_acceptance_matrix() -> dict:
    return {
        "items": [
            {"id": "MANUAL-1", "statement": "挂号页面新建和清屏后默认值验收", "kind": "manual"},
        ]
    }


def archive_default_diff() -> str:
    return """diff --git a/src/packages/components/bing-ren-xx/src/mixins/ziDianInfo.js b/src/packages/components/bing-ren-xx/src/mixins/ziDianInfo.js
index 1111111..2222222 100644
--- a/src/packages/components/bing-ren-xx/src/mixins/ziDianInfo.js
+++ b/src/packages/components/bing-ren-xx/src/mixins/ziDianInfo.js
@@ -147,10 +147,13 @@ export const ziDianInfo = {
     defaultZhengJianLx () {
+      return this.defaultArr.find(item => item.canShuId === '建档_证件类型默认值') || {canShuZhi: ''}
     },
     defaultHunYin () {
+      return this.defaultArr.find(item => item.canShuId === '建档_默认婚姻') || {canShuZhi: ''}
     },
     defaultNianLingDw () {
+      return this.defaultArr.find(item => item.canShuId === '建档_年龄单位') || {canShuZhi: '1'}
     }
"""


class CoreClosureContractTests(unittest.TestCase):
    def test_sorting_tree_requirement_blocks_without_executable_acceptance_contract(self) -> None:
        contract = build_requirement_contract(
            title="DFHIS-31558",
            demand_text="科室树和右侧排班按顺序号排序并保持一致。",
            requirement_calibration=ready_calibration(),
            technical_decision=ready_decision(),
            acceptance_matrix=acceptance_matrix(),
            apply_to_project=False,
            acceptance_contract_result=None,
        )

        self.assertEqual("blocked", contract.status)
        self.assertIn("可执行排序验收契约", "\n".join(contract.blockers))

    def test_paiban_contract_keeps_user_rule_and_default_behavior(self) -> None:
        contract = build_requirement_contract(
            title="DFHIS-31465",
            demand_text=PAIBAN_DEMAND,
            requirement_calibration=ready_calibration(),
            technical_decision=ready_decision(),
            acceptance_matrix=acceptance_matrix(),
            apply_to_project=False,
        )

        payload = contract.to_dict()
        self.assertEqual("ready", contract.status)
        self.assertIn("paiBanMs", json.dumps(payload, ensure_ascii=False))
        self.assertEqual("空、不传或其他值保持当前默认模式", contract.default_behavior)
        self.assertFalse(payload["apply_to_project"])

    def test_contract_blocks_without_verify_command(self) -> None:
        contract = build_requirement_contract(
            title="DFHIS-31465",
            demand_text=PAIBAN_DEMAND,
            requirement_calibration=ready_calibration(),
            technical_decision=ready_decision(verify_commands=[]),
            acceptance_matrix=acceptance_matrix(),
            apply_to_project=False,
        )

        self.assertEqual("blocked", contract.status)
        self.assertIn("专项验证命令", "\n".join(contract.blockers))

    def test_contract_blocks_without_allowed_path(self) -> None:
        contract = build_requirement_contract(
            title="DFHIS-31465",
            demand_text=PAIBAN_DEMAND,
            requirement_calibration=ready_calibration(),
            technical_decision=ready_decision(paths=[]),
            acceptance_matrix=acceptance_matrix(),
            apply_to_project=False,
        )

        self.assertEqual("blocked", contract.status)
        self.assertIn("允许修改路径", "\n".join(contract.blockers))

    def test_contract_blocks_unready_calibration(self) -> None:
        contract = build_requirement_contract(
            title="DFHIS-31465",
            demand_text=PAIBAN_DEMAND,
            requirement_calibration=ready_calibration(status="needs_human_confirmation"),
            technical_decision=ready_decision(),
            acceptance_matrix=acceptance_matrix(),
            apply_to_project=False,
        )

        self.assertEqual("blocked", contract.status)
        self.assertIn("需求校准", "\n".join(contract.blockers))

    def test_contract_blocks_resolved_default_precedence_without_four_source_code_evidence(self) -> None:
        calibration = ready_calibration()
        calibration["default_value_precedence"] = {
            "required": True,
            "status": "resolved",
            "steps": [
                {"source": "common_form_setting"},
                {"source": "parameter_setting"},
                {"source": "page_hardcoded_default"},
                {"source": "no_default"},
            ],
        }
        decision = ready_decision()
        decision["field_provenance"]["default_value_precedence"] = {
            "required": True,
            "status": "blocked",
            "sources": [],
            "blockers": ["通用表单读取路径未定位。"],
        }

        contract = build_requirement_contract(
            title="DFHIS-32106",
            demand_text="默认字段支持四级来源覆盖。",
            requirement_calibration=calibration,
            technical_decision=decision,
            acceptance_matrix=acceptance_matrix(),
            apply_to_project=False,
        )

        self.assertEqual("blocked", contract.status)
        self.assertIn("默认值来源优先级", "\n".join(contract.blockers))

    def test_contract_blocks_unverified_cross_layer_contract(self) -> None:
        decision = ready_decision()
        decision["contract_verification"] = {
            "required": True,
            "status": "blocked",
            "layers": {
                "client_request": {"status": "verified"},
                "server_contract": {"status": "missing"},
            },
        }
        contract = build_requirement_contract(
            title="DFHIS-31551",
            demand_text="挂号查询排序入参调整。",
            requirement_calibration=ready_calibration(),
            technical_decision=decision,
            acceptance_matrix=acceptance_matrix(),
            apply_to_project=False,
        )

        self.assertEqual("blocked", contract.status)
        self.assertIn("前后端契约", "\n".join(contract.blockers))

    def test_contract_blocks_high_risk_demand(self) -> None:
        contract = build_requirement_contract(
            title="医保收费调整",
            demand_text="医保收费金额计算调整",
            requirement_calibration=ready_calibration(),
            technical_decision=ready_decision(),
            acceptance_matrix=acceptance_matrix(),
            apply_to_project=False,
        )

        self.assertEqual("blocked", contract.status)
        self.assertIn("高风险", "\n".join(contract.blockers))

    def test_contract_accepts_explicit_default_sync_rules(self) -> None:
        contract = build_requirement_contract(
            title="DFHIS-31557",
            demand_text="挂号缩减版与建档默认值保持一致。",
            requirement_calibration=archive_default_calibration(),
            technical_decision=ready_decision(paths=["src/packages/components/bing-ren-xx/src/mixins/ziDianInfo.js"]),
            acceptance_matrix=archive_default_acceptance_matrix(),
            apply_to_project=False,
        )

        self.assertEqual("ready", contract.status)
        self.assertEqual("已有病人、读卡结果和用户已选择的字段不被默认值覆盖", contract.default_behavior)

    def test_contract_uses_explicit_rules_when_matrix_has_only_manual_acceptance(self) -> None:
        contract = build_requirement_contract(
            title="DFHIS-31557",
            demand_text="挂号缩减版与建档默认值保持一致。",
            requirement_calibration=archive_default_calibration(),
            technical_decision=ready_decision(paths=["src/packages/components/bing-ren-xx/src/mixins/ziDianInfo.js"]),
            acceptance_matrix=manual_only_acceptance_matrix(),
            apply_to_project=False,
        )

        self.assertEqual("ready", contract.status)
        self.assertEqual(
            ["新建或清屏后的挂号缩减版按建档同名参数读取默认值"],
            list(contract.automatic_acceptance),
        )


def ready_contract():
    return build_requirement_contract(
        title="DFHIS-31465",
        demand_text=PAIBAN_DEMAND,
        requirement_calibration=ready_calibration(),
        technical_decision=ready_decision(),
        acceptance_matrix=acceptance_matrix(),
        apply_to_project=False,
    )


def ready_ordering_review_contract() -> tuple[RequirementContract, object]:
    acceptance_result = execute_acceptance_contract(ORDERING_CONTRACT_PATH)
    contract = RequirementContract(
        schema_version="1.0-requirement-contract",
        status="ready",
        title="DFHIS-31558",
        demand_digest="科室树和右侧排班按顺序号排序并保持一致。",
        allowed_paths=(
            "src/pages/yeWuGn/guaHaoSf/index.vue",
            "src/pages/yeWuGn/guaHaoSf/js/paiBanSort.js",
        ),
        verify_commands=(acceptance_result.verify_command,),
        acceptance_contract=acceptance_result.to_dict(),
    )
    return contract, acceptance_result


def ordering_diff(*, includes_parent_sort_evidence: bool) -> str:
    parent_sort_evidence = "" if not includes_parent_sort_evidence else """
+function getPaiBanSortKey (node) {
+  return node.paiBanSortIndex
+}
"""
    return """diff --git a/src/pages/yeWuGn/guaHaoSf/js/paiBanSort.js b/src/pages/yeWuGn/guaHaoSf/js/paiBanSort.js
index 1111111..2222222 100644
--- a/src/pages/yeWuGn/guaHaoSf/js/paiBanSort.js
+++ b/src/pages/yeWuGn/guaHaoSf/js/paiBanSort.js
@@ -1,3 +1,8 @@
+function sortKeShiNodes (nodes) {
++  return nodes.sort((a, b) => a.paiBanSortIndex - b.paiBanSortIndex)
+}
""" + parent_sort_evidence


def paiban_diff(*, include_default_guard: bool) -> str:
    default_guard = "" if not include_default_guard else """
+  if (!['1', '2'].includes(String(paiBanMs || ''))) {
+    return paiBanList
+  }
"""
    return """diff --git a/src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js b/src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js
index 1111111..2222222 100644
--- a/src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js
+++ b/src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js
@@ -1,3 +1,14 @@
+export function filterByPaiBanMs (paiBanList, paiBanMs) {
""" + default_guard + """+  if (String(paiBanMs) === '1') {
+    return paiBanList.filter(item => !item.doctorId)
+  }
+  if (String(paiBanMs) === '2') {
+    return paiBanList.filter(item => item.doctorId)
+  }
+  return paiBanList
+}
"""


class CoreClosureDiffReviewTests(unittest.TestCase):
    def test_diff_review_rejects_deleted_sorting_evidence(self) -> None:
        contract, acceptance_result = ready_ordering_review_contract()
        deletion_diff = """diff --git a/src/pages/yeWuGn/guaHaoSf/js/paiBanSort.js b/src/pages/yeWuGn/guaHaoSf/js/paiBanSort.js
index 1111111..2222222 100644
--- a/src/pages/yeWuGn/guaHaoSf/js/paiBanSort.js
+++ b/src/pages/yeWuGn/guaHaoSf/js/paiBanSort.js
@@ -1,6 +1,3 @@
-const paiBanSortIndex = 1
-function getPaiBanSortKey () {
-  return paiBanSortIndex
-}
+const unrelatedChange = true
"""

        review = review_final_diff(
            contract=contract,
            final_diff=deletion_diff,
            verification_passed=True,
            acceptance_contract_result=acceptance_result,
        )

        self.assertEqual("blocked", review.status)
        self.assertIn("paiBanSortIndex", "\n".join(review.findings))

    def test_diff_review_blocks_sorting_contract_without_parent_sort_evidence(self) -> None:
        contract, acceptance_result = ready_ordering_review_contract()

        review = review_final_diff(
            contract=contract,
            final_diff=ordering_diff(includes_parent_sort_evidence=False),
            verification_passed=True,
            acceptance_contract_result=acceptance_result,
        )

        self.assertEqual("blocked", review.status)
        self.assertIn("getPaiBanSortKey", "\n".join(review.findings))

    def test_diff_review_accepts_sorting_contract_evidence_after_fixture_passes(self) -> None:
        contract, acceptance_result = ready_ordering_review_contract()

        review = review_final_diff(
            contract=contract,
            final_diff=ordering_diff(includes_parent_sort_evidence=True),
            verification_passed=True,
            acceptance_contract_result=acceptance_result,
        )

        self.assertEqual("pass", review.status)

    def test_review_rejects_path_outside_contract(self) -> None:
        review = review_final_diff(
            contract=ready_contract(),
            final_diff=paiban_diff(include_default_guard=True).replace(
                "src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js", "src/other.vue"
            ),
            verification_passed=True,
        )

        self.assertEqual("blocked", review.status)
        self.assertIn("白名单", "\n".join(review.findings))

    def test_review_requires_default_mode_guard(self) -> None:
        review = review_final_diff(
            contract=ready_contract(),
            final_diff=paiban_diff(include_default_guard=False),
            verification_passed=True,
        )

        self.assertEqual("blocked", review.status)
        self.assertIn("默认", "\n".join(review.findings))

    def test_review_accepts_paiban_rules_and_default_guard(self) -> None:
        review = review_final_diff(
            contract=ready_contract(),
            final_diff=paiban_diff(include_default_guard=True),
            verification_passed=True,
        )

        self.assertEqual("pass", review.status)

    def test_review_accepts_explicit_default_sync_evidence(self) -> None:
        contract = build_requirement_contract(
            title="DFHIS-31557",
            demand_text="挂号缩减版与建档默认值保持一致。",
            requirement_calibration=archive_default_calibration(),
            technical_decision=ready_decision(paths=["src/packages/components/bing-ren-xx/src/mixins/ziDianInfo.js"]),
            acceptance_matrix=archive_default_acceptance_matrix(),
            apply_to_project=False,
        )

        review = review_final_diff(contract=contract, final_diff=archive_default_diff(), verification_passed=True)

        self.assertEqual("pass", review.status)

    def test_review_accepts_route_component_cache_name_change(self) -> None:
        calibration = {
            "status": "ready_for_development",
            "decision": {"can_enter_development": True},
            "source_priority": [{"source": "yunxiao_evidence", "reason": "需求原始来源"}],
            "resolved_parameters": [
                {
                    "name": "top_tab_state",
                    "location": "route_component_cache",
                    "allowed_values": {
                        "tab_switch": "切换业务页签后保留当前查询条件、结果和分页状态。",
                        "default": "首次进入或页面已关闭后，保持当前组件的初始化行为。",
                    },
                }
            ],
            "warnings": [],
        }
        path = "src/pages/chaXunTj/guaHaoChaX/index.vue"
        contract = build_requirement_contract(
            title="挂号病人查询切换标签页不要刷新",
            demand_text="切换顶部业务页签后不刷新。",
            requirement_calibration=calibration,
            technical_decision=ready_decision(paths=[path]),
            acceptance_matrix={"items": [{"id": "AC-1", "statement": "切换业务页签后保留查询条件", "kind": "automatic"}]},
            apply_to_project=False,
        )
        diff = """diff --git a/src/pages/chaXunTj/guaHaoChaX/index.vue b/src/pages/chaXunTj/guaHaoChaX/index.vue
--- a/src/pages/chaXunTj/guaHaoChaX/index.vue
+++ b/src/pages/chaXunTj/guaHaoChaX/index.vue
@@ -365,1 +365,1 @@
-  name: 'GuaHaoChaXun',
+  name: 'GuaHaoChaX',
"""

        review = review_final_diff(contract=contract, final_diff=diff, verification_passed=True)

        self.assertEqual("pass", review.status)


class WorktreePatchExtractionTests(unittest.TestCase):
    def test_extract_unified_diff_restores_terminal_newline(self) -> None:
        patch = paiban_diff(include_default_guard=True).rstrip("\n")

        extracted = extract_unified_diff(patch)

        self.assertTrue(extracted.endswith("\n"))


class FixturePatchLLMClient(BaseLLMClient):
    mode = "fixture"
    model_name = "fixture-patch"
    is_mock = False

    def complete(self, *, system_prompt: str, user_prompt: str, step_key: str, expert_name: str) -> LLMResponse:
        patch_text = """diff --git a/src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js b/src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js
index 1111111..2222222 100644
--- a/src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js
+++ b/src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js
@@ -1 +1,12 @@
-export const original = true
+export function filterByPaiBanMs (paiBanList, paiBanMs) {
+  if (!['1', '2'].includes(String(paiBanMs || ''))) {
+    return paiBanList
+  }
+  if (String(paiBanMs) === '1') {
+    return paiBanList.filter(item => !item.doctorId)
+  }
+  if (String(paiBanMs) === '2') {
+    return paiBanList.filter(item => item.doctorId)
+  }
+  return paiBanList
+}
"""
        return LLMResponse(patch_text, 10, 10, self.mode, self.model_name)


class MissingDefaultGuardPatchLLMClient(FixturePatchLLMClient):
    def complete(self, *, system_prompt: str, user_prompt: str, step_key: str, expert_name: str) -> LLMResponse:
        response = super().complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            step_key=step_key,
            expert_name=expert_name,
        )
        patch_text = response.content.replace(
            "+  if (!['1', '2'].includes(String(paiBanMs || ''))) {\n"
            "+    return paiBanList\n"
            "+  }\n",
            "",
        )
        return LLMResponse(patch_text, response.prompt_tokens, response.completion_tokens, self.mode, self.model_name)


def core_technical_decision(project_path: Path, *, verify_commands: list[str] | None = None) -> TechnicalDecisionResult:
    decision = ready_decision(verify_commands=verify_commands)
    decision["selected_projects"][0]["path"] = str(project_path)
    return TechnicalDecisionResult(
        project_root=str(project_path.parent),
        selected_projects=decision["selected_projects"],
        field_provenance=decision["field_provenance"],
        implementation_decision=decision["implementation_decision"],
        recommended_allowed_paths=decision["recommended_allowed_paths"],
        recommended_verify_commands=decision["recommended_verify_commands"],
        artifacts={},
    )


def ready_governance_for_enforce() -> RequirementGovernanceResult:
    return RequirementGovernanceResult(
        schema_version="requirement-governance.v1",
        status="ready_for_local_change",
        can_modify=True,
        can_complete_in_single_pass=True,
        risk_level="low",
        checks=tuple(
            GovernanceCheck(name, "pass", "已闭合。")
            for name in (
                "source_integrity", "reasonableness", "compliance", "completeness",
                "changeability", "impact", "verification", "single_pass_readiness",
            )
        ),
        blockers=(),
        missing_information=(),
        unsupported_reasons=(),
        required_capabilities=(),
        evidence_refs=({"source": "structured_input"},),
    )


def ready_single_pass_for_ordering(project_path: Path) -> SinglePassChangeContract:
    context = ReadyChangeContextService().result
    projection = context.projections["implementation"]
    return SinglePassChangeContract(
        schema_version="single-pass-change-contract.v1",
        status="ready",
        objective="科室树和右侧排班排序一致",
        in_scope=("排班排序",),
        out_of_scope=("不修改后端",),
        repositories=({"name": "df-web-guahaosf", "path": str(project_path), "role": "frontend"},),
        allowed_paths=("src/pages/yeWuGn/guaHaoSf/js/paiBanSort.js",),
        business_rules=({"name": "ordering", "allowed_values": {"default": "保持原逻辑"}},),
        preserved_behaviors=("无顺序号保持原相对顺序",),
        adjacent_paths=(),
        database_impacts=(),
        configuration_impacts=(),
        verify_commands=("test -f src/pages/yeWuGn/guaHaoSf/js/paiBanSort.js",),
        automatic_acceptance=("排序 fixture 通过",),
        manual_acceptance=("排班列表与树一致",),
        rollback_strategy="restore_pre_change_local_files",
        blockers=(),
        change_context_pack_id=context.pack.pack_id,
        change_context_projection_hash=projection.projection_hash,
        change_context_layer_hashes=tuple(
            {"layer_type": layer.layer_type, "content_hash": layer.content_hash}
            for layer in context.pack.layers
        ),
    )


class CoreClosureRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project_path = self.root / "df-web-guahaosf"
        target = self.project_path / "src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js"
        target.parent.mkdir(parents=True)
        target.write_text("export const original = true\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=self.project_path, check=True, capture_output=True, text=True)
        subprocess.run(["git", "add", "."], cwd=self.project_path, check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "-c", "user.name=Harness Test", "-c", "user.email=harness@example.test", "commit", "-m", "fixture"],
            cwd=self.project_path,
            check=True,
            capture_output=True,
            text=True,
        )
        self.db_path = self.root / "harness.sqlite"
        self.env_patch = patch.dict(os.environ, {"HARNESS_DB_PATH": str(self.db_path)})
        self.env_patch.start()
        self.change_context = ReadyChangeContextService()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def _runner(self, llm_client, **kwargs) -> RequirementWorkflowRunner:
        return RequirementWorkflowRunner(
            llm_client,
            change_context_service=self.change_context,
            **kwargs,
        )

    def _run_with_scope_confirmation(self, runner: RequirementWorkflowRunner, **options):
        """Replay the real preview -> exact token -> mutating execution flow."""
        result = runner.run(**options)
        previous_token = ""
        for _ in range(3):
            pending = [
                item
                for item in reversed(database.get_artifacts(result.run_id))
                if item["kind"] == "pre_change_confirmation_json"
            ]
            if not pending:
                break
            confirmation = json.loads(
                pending[0]["content"]
            )
            token = confirmation.get("confirmation_token") or ""
            if not token or token == previous_token:
                break
            if result.status != "blocked" and result.evaluation_status != "awaiting_pre_change_scope_confirmation":
                break
            previous_token = token
            result = runner.run(
                **options,
                pre_change_confirmation=token,
            )
        return result

    def test_blocked_core_closure_never_enters_worktree(self) -> None:
        with (
            patch("app.harness.build_technical_decision", return_value=core_technical_decision(self.project_path, verify_commands=[])),
            patch("app.harness.build_acceptance_matrix", return_value=acceptance_matrix()),
        ):
            result = self._runner(FixturePatchLLMClient()).run(
                title="DFHIS-31465",
                demand_text=PAIBAN_DEMAND,
                project_path=self.project_path,
                execution_mode="core-closure-trial",
            )

        artifacts = {item["kind"] for item in database.get_artifacts(result.run_id)}
        self.assertEqual("blocked", result.status)
        self.assertIn("core_closure_json", artifacts)
        self.assertNotIn("worktree_manifest_json", artifacts)
        self.assertEqual([], database.get_step_runs(result.run_id))

    def test_sorting_tree_demand_without_fixture_never_enters_worktree(self) -> None:
        with (
            patch("app.harness.build_technical_decision", return_value=core_technical_decision(self.project_path)),
            patch("app.harness.build_acceptance_matrix", return_value=acceptance_matrix()),
        ):
            result = self._runner(FixturePatchLLMClient()).run(
                title="DFHIS-31558",
                demand_text="科室树和右侧排班按顺序号排序并保持一致。",
                project_path=self.project_path,
                execution_mode="core-closure-trial",
            )

        artifacts = {item["kind"] for item in database.get_artifacts(result.run_id)}
        self.assertEqual("blocked", result.status)
        self.assertIn("core_closure_json", artifacts)
        self.assertNotIn("worktree_manifest_json", artifacts)

    def test_sorting_tree_requirement_from_yunxiao_evidence_requires_fixture(self) -> None:
        with (
            patch("app.harness.build_technical_decision", return_value=core_technical_decision(self.project_path)),
            patch("app.harness.build_acceptance_matrix", return_value=acceptance_matrix()),
            patch(
                "app.harness.collect_yunxiao_evidence",
                return_value={"status": "success", "clean_text": "科室树和右侧排班按顺序号排序并保持一致。"},
            ),
        ):
            result = self._runner(FixturePatchLLMClient()).run(
                title="DFHIS-31558",
                demand_text="按云效需求处理。",
                project_path=self.project_path,
                execution_mode="core-closure-trial",
                yunxiao_read=True,
                yunxiao_url="https://example.test/DFHIS-31558",
            )

        closure = next(
            json.loads(item["content"])
            for item in database.get_artifacts(result.run_id)
            if item["kind"] == "core_closure_json"
        )
        self.assertEqual("blocked", result.status)
        self.assertIn("需求校准未达到 ready_for_development", "\n".join(closure["contract"]["blockers"]))
        artifacts = {item["kind"] for item in database.get_artifacts(result.run_id)}
        self.assertNotIn("worktree_manifest_json", artifacts)
        self.assertEqual([], database.get_step_runs(result.run_id))

    def test_sorting_fixture_adds_targeted_command_before_worktree(self) -> None:
        worktree_result = WorktreeExecutionResult(status="failed", summary="fixture worktree stop")
        with (
            patch("app.harness.build_technical_decision", return_value=core_technical_decision(self.project_path)),
            patch("app.harness.build_acceptance_matrix", return_value=acceptance_matrix()),
            patch("app.harness.build_requirement_calibration", return_value=ready_calibration()),
            patch.object(RequirementWorkflowRunner, "_run_worktree_execution", return_value=worktree_result) as executor,
        ):
            result = self._run_with_scope_confirmation(self._runner(FixturePatchLLMClient()),
                title="DFHIS-31558",
                demand_text="科室树和右侧排班按顺序号排序并保持一致。",
                project_path=self.project_path,
                execution_mode="core-closure-trial",
                acceptance_contract_file=ORDERING_CONTRACT_PATH,
                apply_approved_diff=False,
            )

        verify_commands = executor.call_args.kwargs["verify_commands"]
        artifacts = {item["kind"] for item in database.get_artifacts(result.run_id)}
        self.assertEqual("blocked", result.status)
        self.assertIn("node src/pages/yeWuGn/guaHaoSf/js/paiBanSort.test.js", verify_commands)
        self.assertIn("acceptance_contract_result_json", artifacts)

    def test_enforce_sorting_fixture_keeps_command_and_independent_diff_gate(self) -> None:
        technical = core_technical_decision(self.project_path)
        object.__setattr__(technical, "recommended_allowed_paths", ["src/pages/yeWuGn/guaHaoSf/js/paiBanSort.js"])
        worktree_result = WorktreeExecutionResult(
            status="success",
            summary="fixture worktree finished",
            final_diff=ordering_diff(includes_parent_sort_evidence=False),
        )
        with (
            patch("app.harness.build_technical_decision", return_value=technical),
            patch("app.harness.build_acceptance_matrix", return_value=acceptance_matrix()),
            patch("app.harness.build_requirement_calibration", return_value=ready_calibration()),
            patch(
                "app.harness.build_requirement_governance_outputs",
                return_value=(ready_governance_for_enforce(), ready_single_pass_for_ordering(self.project_path), ""),
            ),
            patch.object(RequirementWorkflowRunner, "_run_worktree_execution", return_value=worktree_result) as executor,
        ):
            result = self._run_with_scope_confirmation(self._runner(FixturePatchLLMClient()),
                title="DFHIS-31558",
                demand_text="科室树和右侧排班按顺序号排序并保持一致。",
                project_path=self.project_path,
                execution_mode="core-closure-trial",
                acceptance_contract_file=ORDERING_CONTRACT_PATH,
                requirement_governance="enforce",
                apply_approved_diff=False,
            )

        contract = next(
            json.loads(item["content"])
            for item in database.get_artifacts(result.run_id)
            if item["kind"] == "core_requirement_contract_json"
        )
        self.assertEqual("blocked", result.status)
        self.assertIn("node src/pages/yeWuGn/guaHaoSf/js/paiBanSort.test.js", executor.call_args.kwargs["verify_commands"])
        self.assertIn("node src/pages/yeWuGn/guaHaoSf/js/paiBanSort.test.js", contract["verify_commands"])
        self.assertTrue(contract["acceptance_contract"])
        self.assertIn("getPaiBanSortKey", "\n".join(contract["blockers"]) + "\n" + json.dumps(
            next(
                json.loads(item["content"])
                for item in database.get_artifacts(result.run_id)
                if item["kind"] == "core_diff_review_json"
            ),
            ensure_ascii=False,
        ))

    def test_review_only_core_closure_exports_review_and_keeps_project_clean(self) -> None:
        with (
            patch("app.harness.build_technical_decision", return_value=core_technical_decision(self.project_path)),
            patch("app.harness.build_acceptance_matrix", return_value=acceptance_matrix()),
        ):
            result = self._run_with_scope_confirmation(self._runner(FixturePatchLLMClient()),
                title="DFHIS-31465",
                demand_text=PAIBAN_DEMAND,
                project_path=self.project_path,
                execution_mode="core-closure-trial",
                worktree_dir=self.root / "worktrees",
                apply_approved_diff=False,
            )

        payload = next(
            json.loads(item["content"])
            for item in database.get_artifacts(result.run_id)
            if item["kind"] == "core_closure_json"
        )
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.project_path, check=True, capture_output=True, text=True)
        self.assertEqual("ready_for_manual_review", result.status, json.dumps(payload, ensure_ascii=False, indent=2))
        self.assertEqual("pass", payload["diff_review"]["status"])
        self.assertFalse(payload["apply_to_project"])
        self.assertEqual("", status.stdout)
        self.assertEqual([], database.get_step_runs(result.run_id))

    def test_core_closure_applies_to_local_project_by_default_after_diff_review(self) -> None:
        with (
            patch("app.harness.build_technical_decision", return_value=core_technical_decision(self.project_path)),
            patch("app.harness.build_acceptance_matrix", return_value=acceptance_matrix()),
        ):
            result = self._run_with_scope_confirmation(self._runner(FixturePatchLLMClient()),
                title="DFHIS-31465",
                demand_text=PAIBAN_DEMAND,
                project_path=self.project_path,
                execution_mode="core-closure-trial",
                worktree_dir=self.root / "worktrees",
            )

        payload = next(
            json.loads(item["content"])
            for item in database.get_artifacts(result.run_id)
            if item["kind"] == "core_closure_json"
        )
        target = self.project_path / "src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js"
        self.assertEqual("ready_for_manual_review", result.status)
        self.assertTrue(payload["apply_to_project"])
        self.assertEqual("success", payload["worktree"]["apply_to_project"]["status"])
        self.assertIn("filterByPaiBanMs", target.read_text(encoding="utf-8"))

    def test_auto_local_routes_low_risk_demand_to_applied_core_closure(self) -> None:
        with (
            patch("app.harness.build_technical_decision", return_value=core_technical_decision(self.project_path)),
            patch("app.harness.build_acceptance_matrix", return_value=acceptance_matrix()),
        ):
            result = self._run_with_scope_confirmation(self._runner(FixturePatchLLMClient()),
                title="DFHIS-31465",
                demand_text=PAIBAN_DEMAND,
                project_path=self.project_path,
                execution_mode="auto-local",
                worktree_dir=self.root / "worktrees",
            )

        route = next(
            json.loads(item["content"])
            for item in database.get_artifacts(result.run_id)
            if item["kind"] == "execution_route_json"
        )
        target = self.project_path / "src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js"
        self.assertEqual("ready_for_manual_review", result.status)
        self.assertEqual("auto-local", route["requested_execution_mode"])
        self.assertEqual("core-closure-trial", route["resolved_execution_mode"])
        self.assertIn("filterByPaiBanMs", target.read_text(encoding="utf-8"))

    def test_auto_local_fallback_records_completed_project_scan(self) -> None:
        with (
            patch("app.harness.build_technical_decision", return_value=core_technical_decision(self.project_path)),
            patch("app.harness.build_acceptance_matrix", return_value=acceptance_matrix()),
        ):
            result = self._runner(FixturePatchLLMClient()).run(
                title="DFHIS-31465",
                demand_text=PAIBAN_DEMAND,
                project_path=self.project_path,
                execution_mode="auto-local",
                worktree_dir=self.root / "worktrees",
            )

        profile = next(
            json.loads(item["content"])
            for item in database.get_artifacts(result.run_id)
            if item["kind"] == "auto_local_performance_json"
        )
        self.assertEqual("completed", profile["stages"]["project_context_scan"]["status"])
        self.assertEqual("ready_for_manual_review", result.status)
        self.assertTrue(profile["fast_local"]["blockers"])
        self.assertGreaterEqual(profile["total_duration_ms"], profile["stages"]["core_closure"]["duration_ms"])
        self.assertNotIn("_started_perf_counter", profile)

    def test_auto_local_skips_broad_scan_for_explicit_local_frontend_scope(self) -> None:
        allowed_path = "src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js"
        with (
            patch("app.harness.build_technical_decision", return_value=core_technical_decision(self.project_path)),
            patch("app.harness.build_acceptance_matrix", return_value=acceptance_matrix()),
            patch.object(RequirementWorkflowRunner, "_build_evidence_bundle", side_effect=AssertionError("不应执行全仓扫描")) as scanner,
        ):
            result = self._run_with_scope_confirmation(self._runner(FixturePatchLLMClient()),
                title="DFHIS-31465",
                demand_text=PAIBAN_DEMAND,
                project_path=self.project_path,
                execution_mode="auto-local",
                allowed_paths=[allowed_path],
                worktree_dir=self.root / "worktrees",
            )

        decision_artifacts = [
            item for item in database.get_artifacts(result.run_id) if item["kind"] == "fast_local_decision_json"
        ]
        decision = json.loads(decision_artifacts[0]["content"])
        scanner.assert_not_called()
        self.assertEqual("ready_for_manual_review", result.status)
        self.assertEqual(1, len(decision_artifacts))
        self.assertTrue(decision["eligible"])
        self.assertTrue(decision["skip_project_context_scan"])

        profile = next(
            json.loads(item["content"])
            for item in database.get_artifacts(result.run_id)
            if item["kind"] == "auto_local_performance_json"
        )
        self.assertEqual("skipped", profile["stages"]["project_context_scan"]["status"])
        self.assertEqual("fast_local", profile["fast_local"]["route"])
        self.assertIn("requirement_calibration", profile["stages"])
        self.assertIn("core_closure", profile["stages"])

    def test_auto_local_blocks_high_risk_demand_before_worktree(self) -> None:
        with (
            patch("app.harness.build_technical_decision", return_value=core_technical_decision(self.project_path)),
            patch("app.harness.build_acceptance_matrix", return_value=acceptance_matrix()),
        ):
            result = self._runner(FixturePatchLLMClient()).run(
                title="DFHIS-高风险医保退费",
                demand_text="医保患者部分退费前需要校验在院状态，并按参数控制是否允许继续。",
                project_path=self.project_path,
                execution_mode="auto-local",
                worktree_dir=self.root / "worktrees",
            )

        artifacts = {item["kind"] for item in database.get_artifacts(result.run_id)}
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.project_path, check=True, capture_output=True, text=True)
        self.assertEqual("blocked", result.status)
        self.assertIn("execution_route_json", artifacts)
        self.assertIn("core_closure_json", artifacts)
        self.assertNotIn("worktree_manifest_json", artifacts)
        self.assertEqual("", status.stdout)

    def test_requested_apply_waits_for_independent_diff_review(self) -> None:
        with (
            patch("app.harness.build_technical_decision", return_value=core_technical_decision(self.project_path)),
            patch("app.harness.build_acceptance_matrix", return_value=acceptance_matrix()),
        ):
            result = self._run_with_scope_confirmation(self._runner(MissingDefaultGuardPatchLLMClient()),
                title="DFHIS-31465",
                demand_text=PAIBAN_DEMAND,
                project_path=self.project_path,
                execution_mode="core-closure-trial",
                worktree_dir=self.root / "worktrees",
                apply_approved_diff=True,
            )

        payload = next(
            json.loads(item["content"])
            for item in database.get_artifacts(result.run_id)
            if item["kind"] == "core_closure_json"
        )
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.project_path, check=True, capture_output=True, text=True)
        self.assertEqual("blocked", result.status)
        self.assertFalse(payload["apply_to_project"])
        self.assertEqual("blocked_independent_review", payload["worktree"]["apply_to_project"]["status"])
        self.assertEqual("", status.stdout)

    def test_explicit_apply_runs_only_after_independent_diff_review_passes(self) -> None:
        with (
            patch("app.harness.build_technical_decision", return_value=core_technical_decision(self.project_path)),
            patch("app.harness.build_acceptance_matrix", return_value=acceptance_matrix()),
        ):
            result = self._run_with_scope_confirmation(self._runner(FixturePatchLLMClient()),
                title="DFHIS-31465",
                demand_text=PAIBAN_DEMAND,
                project_path=self.project_path,
                execution_mode="core-closure-trial",
                worktree_dir=self.root / "worktrees",
                apply_approved_diff=True,
            )

        payload = next(
            json.loads(item["content"])
            for item in database.get_artifacts(result.run_id)
            if item["kind"] == "core_closure_json"
        )
        target = self.project_path / "src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js"
        self.assertEqual("ready_for_manual_review", result.status)
        self.assertTrue(payload["apply_to_project"])
        self.assertEqual("success", payload["worktree"]["apply_to_project"]["status"])
        self.assertIn("filterByPaiBanMs", target.read_text(encoding="utf-8"))

    def test_legacy_readonly_mode_keeps_nine_step_workflow(self) -> None:
        result = self._runner(MockLLMClient(), allow_mock=True).run(
            title="兼容性检查",
            demand_text="挂号页面显示一个只读提示字段，不涉及收费、医保或结算。",
            project_path=self.project_path,
            execution_mode="readonly",
        )

        artifacts = {item["kind"] for item in database.get_artifacts(result.run_id)}
        self.assertEqual("success", result.status)
        self.assertEqual(9, len(database.get_step_runs(result.run_id)))
        self.assertNotIn("core_closure_json", artifacts)




if __name__ == "__main__":
    unittest.main()
