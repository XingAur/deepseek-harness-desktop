from __future__ import annotations

import unittest

from app.acceptance_matrix import build_acceptance_matrix, build_requirement_acceptance


class AcceptanceMatrixTests(unittest.TestCase):
    def test_registration_room_display_is_low_risk_despite_module_and_no_change_terms(self) -> None:
        matrix = build_acceptance_matrix(
            title="【宁远县人民医院】挂号收费列表中，每个挂号医生后面加上诊室",
            demand_text=(
                "每张排班卡片显示当前排班的 zhenShiMc；未维护时保持空白。"
                "不改变挂号、预约数量、号源数量和收费金额逻辑。"
                "后端、BFF 和数据库均不应修改。"
            ),
        )

        self.assertEqual("low", matrix["risk"]["level"])
        self.assertEqual({"frontend"}, set(matrix["categories"]))

    def test_unrelated_repository_hits_do_not_expand_display_requirement_categories(self) -> None:
        matrix = build_acceptance_matrix(
            title="挂号排班卡片显示诊室",
            demand_text="排班卡片显示 zhenShiMc，后端和数据库均不应修改。",
            evidence_bundle={
                "risk": {"level": "low", "reasons": ["纯展示"]},
                "impact": {
                    "categories": {
                        "frontend": ["src/pages/guaHao/paiBanCard.vue"],
                        "backend": ["src/other/UnrelatedService.java"],
                        "docs": ["docs/unrelated.md"],
                        "test": ["tests/unrelated.test.js"],
                    }
                },
            },
        )

        self.assertEqual({"frontend"}, set(matrix["categories"]))

    def test_readonly_code_evidence_and_acceptance_heading_do_not_expand_change_categories(self) -> None:
        matrix = build_acceptance_matrix(
            title="【宁远县人民医院】挂号收费列表中，每个挂号医生后面加上诊室",
            demand_text=(
                "挂号处理页右侧排班卡片显示当前排班的 zhenShiMc。\n"
                "验收规则：未维护诊室时保持空白。\n"
                "只读代码证据：接口直接归属 df-mic-jj-menzhen，后端 DTO 已声明 zhenShiMc；"
                "后端、BFF 和数据库均不应修改。\n"
                "当前本地仓库边界：进入改码时使用隔离 worktree 并执行回归验证。"
            ),
        )

        self.assertEqual({"frontend"}, set(matrix["categories"]))

    def test_charging_module_prefix_and_negated_scope_stay_low_risk(self) -> None:
        matrix = build_acceptance_matrix(
            title="【宁远人民医院】挂号收费--挂号病人查询切换标签页不要刷新",
            demand_text="切换顶部业务页签再返回，查询条件不能清空；不修改查询接口、发票页签或后端服务。",
        )

        self.assertEqual("low", matrix["risk"]["level"])

    def test_pagination_state_is_frontend_state_not_backend_flow(self) -> None:
        matrix = build_acceptance_matrix(
            title="挂号病人查询切换标签页不要刷新",
            demand_text="切换顶部业务页签再返回，查询结果和分页状态不能被清空。",
        )

        self.assertEqual("low", matrix["risk"]["level"])
        self.assertEqual({"frontend"}, set(matrix["categories"]))

    def test_generic_sync_does_not_activate_schedule_acceptance(self) -> None:
        items = build_requirement_acceptance(
            demand_text="医保审批维护页面需要同步医保等级和对照编码。",
            categories={"frontend"},
            risk_level="medium",
            yunxiao_evidence=None,
        )

        self.assertFalse(
            any(item["id"].startswith("REQ-SCHEDULE-") for item in items)
        )

    def test_schedule_room_display_does_not_activate_one_week_sync_acceptance(self) -> None:
        items = build_requirement_acceptance(
            demand_text="挂号排班卡片显示今日排班维护的诊室名称。",
            categories={"frontend"},
            risk_level="low",
            yunxiao_evidence=None,
        )

        self.assertFalse(
            any(item["id"].startswith("REQ-SCHEDULE-") for item in items)
        )

    def test_explicit_schedule_terms_keep_schedule_acceptance(self) -> None:
        items = build_requirement_acceptance(
            demand_text="一周排班确认后是否同步今日排班需要明确规则。",
            categories={"frontend"},
            risk_level="medium",
            yunxiao_evidence=None,
        )

        self.assertTrue(
            any(item["id"].startswith("REQ-SCHEDULE-") for item in items)
        )

    def test_default_value_precedence_has_one_acceptance_scenario_per_source_level(self) -> None:
        matrix = build_acceptance_matrix(
            title="DFHIS-32106 挂号收费不收费默认值",
            demand_text="不收费字段支持通用表单、参数和页面默认值。",
            default_value_precedence={
                "required": True,
                "status": "resolved",
                "steps": [
                    {"source": "common_form_setting"},
                    {"source": "parameter_setting"},
                    {"source": "page_hardcoded_default"},
                    {"source": "no_default"},
                ],
            },
        )

        items = {item["id"]: item for item in matrix["requirement_acceptance"]}
        for suffix in ("COMMON-FORM", "PARAMETER", "HARDCODED", "NONE"):
            self.assertIn(f"REQ-DEFAULT-PRECEDENCE-{suffix}", items)


if __name__ == "__main__":
    unittest.main()
