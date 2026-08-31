from __future__ import annotations

import unittest

from app.requirement_calibration import build_requirement_calibration, find_high_risk_terms, requirement_calibration_to_markdown


ARCHIVE_DEFAULT_RULES = """
```harness-rules
[
  {
    "name": "挂号缩减版建档默认值",
    "location": "shared_component_default",
    "allowed_values": {
      "configured": "新建或清屏后的挂号缩减版按建档同名参数读取默认值",
      "default": "已有病人、读卡结果和用户已选择的字段不被默认值覆盖"
    },
    "evidence_tokens": [
      "'建档_证件类型默认值'",
      "'建档_年龄单位'",
      "'建档_默认婚姻'"
    ],
    "default_evidence_tokens": ["defaultZhengJianLx", "defaultNianLingDw", "defaultHunYin"]
  }
]
```
"""

DEFAULT_VALUE_PRECEDENCE_RULE = (
    "不收费需要支持默认值：如果通用表单设置了默认值，优先使用通用表单设置；"
    "没有通用表单设置时，如果参数设置了默认值，使用参数默认值；"
    "前两者都没有且界面写死了默认值时，使用界面写死的默认值；"
    "以上都没有时代表没有默认值。"
)


class RequirementCalibrationTests(unittest.TestCase):

    def test_display_only_registration_room_requirement_ignores_delivery_boundaries(self) -> None:
        demand = (
            "诊室来源必须是当前排班的 zhenShiMc。"
            "目标组件是 paiBanCard.vue。"
            "排班没有维护诊室时保持空白。"
            "不改变挂号、预约数量、号源数量和收费金额逻辑。"
            "本轮禁止云效写入、Git 远程写入、数据库写入、部署和发布。"
            "原仓库有 rebase_merge；进入改码时使用 Harness 隔离 worktree，并在回写前检查原工作区。"
        )
        self.assertEqual(
            [],
            find_high_risk_terms(
                title="【宁远县人民医院】挂号收费列表中，每个挂号医生后面加上诊室",
                demand_text=demand,
            ),
        )
        card = build_requirement_calibration(
            title="【宁远县人民医院】挂号收费列表中，每个挂号医生后面加上诊室",
            demand_text=(
                demand
                + "\n【Harness v0.15 需求理解确认卡】\n"
                + "通用高风险提示：医保、结算、收费、报表、对账、回写。"
            ),
        )
        self.assertEqual(
            ["zhenShiMc"],
            [item["name"] for item in card["resolved_parameters"]],
        )
        self.assertEqual("simple", card["complexity"]["level"])
        self.assertEqual("ready_for_development", card["status"])

    def test_normalized_provider_metadata_and_readonly_code_evidence_do_not_become_parameters(self) -> None:
        demand = (
            "诊室来源必须是当前排班的 zhenShiMc。"
            "排班没有维护诊室时保持空白。\n"
            "只读代码证据：页面调用 getPaiBanListByJiaGeTxV2，DTO_MZ_GuaHaoPb 已声明 zhenShiId。\n"
            "当前本地仓库边界：原工作区位于 WorkCode 并使用 HarnessHistory 归档。"
        )
        card = build_requirement_calibration(
            title="【宁远县人民医院】挂号收费列表中，每个挂号医生后面加上诊室",
            demand_text=demand,
            requirement_evidence={
                "source_type": "yunxiao",
                "source_url": "/Users/lym/WorkCode/ai/HarnessHistory/YUNXIAO/DFHIS-32109/evidence.json",
                "status": "ready_for_analysis",
                "title": "挂号收费列表中，每个挂号医生后面加上诊室",
                "description_text": "挂号界面增加每个排班对应的诊室信息显示",
                "comments": [],
                "evidence_quality": {"analysis_ready": True},
            },
        )

        self.assertEqual(["zhenShiMc"], [item["name"] for item in card["resolved_parameters"]])
        self.assertEqual("ready_for_development", card["status"])
        self.assertEqual("yunxiao_evidence", card["source_priority"][0]["source"])

    def test_negated_charging_scope_does_not_count_as_business_risk(self) -> None:
        self.assertEqual(
            [],
            find_high_risk_terms(
                title="挂号病人查询切换标签页不要刷新",
                demand_text="不修改收费接口、发票页签或后端服务。",
            ),
        )
        self.assertEqual(
            ["收费", "金额"],
            find_high_risk_terms(title="挂号病人查询", demand_text="调整收费接口的金额计算。"),
        )

    def test_top_tab_state_requirement_is_structured_as_low_risk_rule(self) -> None:
        card = build_requirement_calibration(
            title="【宁远人民医院】挂号收费--挂号病人查询切换标签页不要刷新",
            demand_text="现在搜入了搜索条件后切换标签页会把内容刷掉。切换顶部业务页签后不刷新。",
        )

        parameters = {item["name"]: item for item in card["resolved_parameters"]}
        self.assertEqual("route_component_cache", parameters["top_tab_state"]["location"])
        self.assertIn("tab_switch", parameters["top_tab_state"]["allowed_values"])
        self.assertEqual("ready_for_development", card["status"])
        self.assertEqual("simple", card["complexity"]["level"])

    def test_sort_request_parameters_are_structured_with_default_behavior(self) -> None:
        result = build_requirement_calibration(
            title="DFHIS-31551 挂号病人查询排序",
            demand_text="接口 getGuaHaoPageList 入参新增 sortField=排序字段，sortOrder=排序方式(desc/asc)。未设置排序时保持当前默认排序。",
        )

        parameters = {item["name"]: item for item in result["resolved_parameters"]}
        self.assertEqual("request_param", parameters["sortField"]["location"])
        self.assertIn("configured_column", parameters["sortField"]["allowed_values"])
        self.assertEqual("排序方式 desc", parameters["sortOrder"]["allowed_values"]["desc"])
        self.assertTrue(result["decision"]["can_enter_development"])

    def test_comment_contract_parameters_are_included_in_calibration(self) -> None:
        result = build_requirement_calibration(
            title="DFHIS-31551 挂号病人查询排序",
            demand_text="挂号病人查询需要对全部分页数据排序。",
            yunxiao_evidence={
                "status": "success",
                "clean_text": "当前排序只对本页生效。",
                "comments": [
                    {
                        "content": "接口 getGuaHaoPageList 入参新增 sortField=排序字段，sortOrder=排序方式(desc/asc)。未设置排序时保持当前默认排序。"
                    }
                ],
            },
        )

        parameters = {item["name"]: item for item in result["resolved_parameters"]}
        self.assertEqual("request_param", parameters["sortField"]["location"])
        self.assertIn("empty", parameters["sortOrder"]["allowed_values"])

    def test_explicit_rule_overrides_conflicting_comment_contract_parameters(self) -> None:
        result = build_requirement_calibration(
            title="DFHIS-31551 挂号病人查询排序",
            demand_text=(
                "前端只传 sortField，格式为 排序字段A|排序方式,排序字段B|排序方式。\n"
                "```harness-rules\n"
                "[{\"name\": \"sortField\", \"location\": \"request_param\", "
                "\"allowed_values\": {\"encoded\": \"字段A|方式,字段B|方式\", "
                "\"empty\": \"未设置排序时保持当前默认排序\"}}]\n"
                "```"
            ),
            yunxiao_evidence={
                "status": "success",
                "comments": [
                    {
                        "content": "接口 getGuaHaoPageList 入参新增 sortField=排序字段，sortOrder=排序方式(desc/asc)。"
                    }
                ],
            },
        )

        parameters = result["resolved_parameters"]
        self.assertEqual(["sortField"], [item["name"] for item in parameters])
        self.assertEqual("explicit_harness_rule", parameters[0]["source"])
        self.assertEqual("user_instruction", result["source_priority"][0]["source"])

    def test_explicit_harness_rules_make_low_risk_default_sync_ready(self) -> None:
        card = build_requirement_calibration(
            title="DFHIS-31557",
            demand_text="挂号缩减版与建档默认值保持一致。\n" + ARCHIVE_DEFAULT_RULES,
        )

        self.assertEqual("ready_for_development", card["status"])
        self.assertEqual("simple", card["complexity"]["level"])
        self.assertEqual(1, len(card["resolved_parameters"]))
        self.assertEqual("挂号缩减版建档默认值", card["resolved_parameters"][0]["name"])
        self.assertEqual(
            ["'建档_证件类型默认值'", "'建档_年龄单位'", "'建档_默认婚姻'"],
            card["resolved_parameters"][0]["evidence_tokens"],
        )

    def test_default_value_sources_are_calibrated_as_an_ordered_precedence_contract(self) -> None:
        card = build_requirement_calibration(
            title="DFHIS-32106 挂号收费不收费默认值",
            demand_text=DEFAULT_VALUE_PRECEDENCE_RULE,
        )

        precedence = card["default_value_precedence"]
        self.assertTrue(precedence["required"])
        self.assertEqual("resolved", precedence["status"])
        self.assertEqual(
            [
                "common_form_setting",
                "parameter_setting",
                "page_hardcoded_default",
                "no_default",
            ],
            [step["source"] for step in precedence["steps"]],
        )
        self.assertIn("默认值来源优先级", requirement_calibration_to_markdown(card))

    def test_default_value_request_without_a_complete_precedence_contract_cannot_enter_development(self) -> None:
        card = build_requirement_calibration(
            title="DFHIS-32106 挂号收费不收费默认值",
            demand_text="不收费字段支持通用表单设置默认值。",
        )

        self.assertTrue(card["default_value_precedence"]["required"])
        self.assertEqual("unresolved", card["default_value_precedence"]["status"])
        self.assertFalse(card["decision"]["can_enter_development"])
        self.assertTrue(
            any(item["type"] == "default_value_precedence_unresolved" for item in card["warnings"])
        )

    def test_resolved_default_precedence_enters_automatic_source_tracing_not_human_confirmation(self) -> None:
        card = build_requirement_calibration(
            title="DFHIS-32106 挂号收费不收费默认值",
            demand_text=DEFAULT_VALUE_PRECEDENCE_RULE,
        )

        self.assertEqual("needs_technical_evidence", card["status"])
        self.assertTrue(card["decision"]["can_enter_technical_analysis"])
        self.assertFalse(card["decision"]["needs_human_confirmation"])
        self.assertEqual([], card["must_confirm"])
        self.assertEqual(
            ["common_form_setting", "parameter_setting", "page_hardcoded_default", "no_default"],
            card["technical_investigation"]["source_order"],
        )
        markdown = requirement_calibration_to_markdown(card)
        self.assertIn("自动源码追踪", markdown)
        self.assertIn("无需用户确认；Harness 将先继续自动源码追踪。", markdown)

    def test_invalid_explicit_harness_rules_do_not_bypass_confirmation(self) -> None:
        card = build_requirement_calibration(
            title="DFHIS-31557",
            demand_text="挂号缩减版与建档默认值保持一致。\n```harness-rules\nnot-json\n```",
        )

        self.assertEqual("needs_human_confirmation", card["status"])

    def test_natural_language_code_fields_are_visible_for_confirmation(self) -> None:
        card = build_requirement_calibration(
            title="DFHIS-30372",
            demand_text=(
                "医院目录来自 bff-jichufw 的 gy_shoufeixm，"
                "门诊自费对应 ziFeiScGl 的 menzhenbz=1、zifeibz=1。"
            ),
        )

        parameters = {item["name"]: item for item in card["resolved_parameters"]}
        self.assertIn("gy_shoufeixm", parameters)
        self.assertIn("ziFeiScGl", parameters)
        self.assertEqual("1", parameters["menzhenbz"]["allowed_values"]["1"])
        self.assertNotIn(
            "需求提到字段或参数，但未识别到明确名称和值域。",
            card["must_confirm"],
        )
        self.assertNotIn("GitHub", parameters)
        self.assertFalse(any("menzhenbz" in warning["message"] for warning in card["warnings"]))
        self.assertIn("组合业务规则", requirement_calibration_to_markdown(card))

    def test_outpatient_and_inpatient_flags_are_calibrated_as_composite_rules(self) -> None:
        card = build_requirement_calibration(
            title="DFHIS-30372",
            demand_text=(
                "门诊自费、门诊部上传的前提都必须是 menzhenbz 为 1，"
                "再分别判断 zifeibz 和 bushangchuanbz；住院自费、住院部上传一样，"
                "住院先满足 zhuyuanbz 为 1，再判断 zifeibz 和 bushangchuanbz。"
            ),
        )

        rules = {item["name"]: item for item in card["composite_rules"]}
        self.assertEqual(
            ["menzhenbz", "zifeibz"],
            [item["field"] for item in rules["门诊自费"]["conditions"]],
        )
        self.assertEqual(
            ["menzhenbz", "bushangchuanbz"],
            [item["field"] for item in rules["门诊部上传"]["conditions"]],
        )
        self.assertEqual(
            ["zhuyuanbz", "zifeibz"],
            [item["field"] for item in rules["住院自费"]["conditions"]],
        )
        self.assertEqual(
            ["zhuyuanbz", "bushangchuanbz"],
            [item["field"] for item in rules["住院部上传"]["conditions"]],
        )
        self.assertTrue(all(condition["value"] == "1" for rule in rules.values() for condition in rule["conditions"]))
        self.assertFalse(any("menzhenbz" in warning["message"] for warning in card["warnings"]))
        self.assertIn("组合业务规则", requirement_calibration_to_markdown(card))

    def test_inpatient_same_rule_phrase_infers_the_inpatient_gate(self) -> None:
        card = build_requirement_calibration(
            title="DFHIS-30372",
            demand_text="门诊自费、门诊部上传均先 menzhenbz=1，再看 zifeibz 和 bushangchuanbz；住院一样。",
        )

        rules = {item["name"]: item for item in card["composite_rules"]}
        self.assertEqual("zhuyuanbz", rules["住院自费"]["conditions"][0]["field"])
        self.assertEqual("1", rules["住院部上传"]["conditions"][0]["value"])


if __name__ == "__main__":
    unittest.main()
