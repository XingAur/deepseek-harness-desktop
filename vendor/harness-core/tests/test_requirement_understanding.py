from __future__ import annotations

import unittest

from app.requirement_understanding import build_requirement_understanding


def ready_inputs() -> dict:
    return {
        "title": "排班卡片显示诊室",
        "user_instruction": "门诊护士在排班卡片确认诊室，避免到诊室后才发现排错；只调整排班卡片展示。",
        "requirement_evidence": {
            "title": "排班卡片显示诊室",
            "description_text": "当前护士只能在进入详情后看到诊室，排班确认效率低。需要在排班卡片展示当前排班的诊室名称。",
        },
        "requirement_calibration": {
            "status": "ready_for_development",
            "decision": {"can_enter_development": True},
            "resolved_scope": {"in_scope": ["排班卡片"], "out_of_scope": ["不改排班数据"]},
        },
        "technical_decision": {
            "selected_projects": [
                {"name": "df-web-test", "path": "/tmp/df-web-test", "role": "frontend", "exists": True}
            ],
            "recommended_allowed_paths": ["src/components/paiBanCard.vue"],
            "recommended_verify_commands": ["npm test -- paiBanCard"],
            "field_provenance": {
                "target_ui_found": True,
                "target_ui_paths": ["src/components/paiBanCard.vue"],
                "evidence": [
                    {"project": "df-web-test", "path": "src/components/paiBanCard.vue", "reason": "卡片入口和排班字段引用"}
                ],
            },
            "implementation_decision": {"can_patch": True, "blockers": []},
        },
        "change_ownership": {
            "status": "ready",
            "rows": [
                {"layer": "frontend", "status": "change_required"},
                {"layer": "backend", "status": "not_required"},
                {"layer": "database", "status": "not_required"},
                {"layer": "configuration", "status": "not_required"},
            ],
            "blockers": [],
        },
        "acceptance_matrix": {
            "requirement_acceptance": [{"scenario": "护士打开排班卡片时显示当前排班诊室"}],
            "manual_acceptance": [{"path": "门诊排班页面 -> 排班卡片"}],
            "auto_verification": [{"command": "npm test -- paiBanCard"}],
        },
    }


class RequirementUnderstandingTests(unittest.TestCase):
    def test_calibrated_requirement_without_project_call_chain_is_blocked(self) -> None:
        inputs = ready_inputs()
        inputs["technical_decision"] = {
            "selected_projects": [],
            "recommended_allowed_paths": [],
            "recommended_verify_commands": [],
            "field_provenance": {},
            "implementation_decision": {"can_patch": True, "blockers": []},
        }

        result = build_requirement_understanding(**inputs)

        self.assertFalse(result.can_modify)
        self.assertEqual("blocked_needs_project_discovery", result.status)
        self.assertIn("project_selection", {check.name for check in result.checks if check.status == "blocked"})
        self.assertIn("entry_and_call_chain", {check.name for check in result.checks if check.status == "blocked"})
        self.assertTrue(any("项目入口" in action for action in result.next_readonly_actions))

    def test_missing_business_context_does_not_get_invented(self) -> None:
        inputs = ready_inputs()
        inputs["requirement_evidence"] = {"title": "排班卡片显示诊室", "description_text": ""}
        inputs["user_instruction"] = ""
        inputs["acceptance_matrix"]["requirement_acceptance"] = []
        inputs["acceptance_matrix"]["manual_acceptance"] = []

        result = build_requirement_understanding(**inputs)

        self.assertFalse(result.can_modify)
        self.assertEqual("blocked_needs_requirement_context", result.status)
        blocked = {check.name for check in result.checks if check.status == "blocked"}
        self.assertIn("business_background", blocked)
        self.assertIn("usage_scenario", blocked)
        self.assertIn("target_and_boundary", blocked)

    def test_action_only_requirement_is_not_mistaken_for_business_background(self) -> None:
        inputs = ready_inputs()
        inputs["requirement_evidence"]["description_text"] = "在页面增加诊室字段。"

        result = build_requirement_understanding(**inputs)

        self.assertFalse(result.can_modify)
        self.assertIn(
            "business_background",
            {check.name for check in result.checks if check.status == "blocked"},
        )

    def test_complete_business_and_project_evidence_is_ready_for_change(self) -> None:
        result = build_requirement_understanding(**ready_inputs())

        self.assertEqual("ready_for_change", result.status)
        self.assertTrue(result.can_modify)
        self.assertFalse(result.blockers)
        self.assertIn("业务背景", result.to_markdown())
        self.assertIn("项目入口与调用链", result.to_markdown())

    def test_runtime_ownership_tuple_rows_are_accepted(self) -> None:
        inputs = ready_inputs()
        inputs["change_ownership"]["rows"] = tuple(inputs["change_ownership"]["rows"])

        result = build_requirement_understanding(**inputs)

        self.assertEqual("ready_for_change", result.status)
        self.assertTrue(result.can_modify)

    def test_required_error_chain_closure_blocks_change_even_when_other_evidence_is_ready(self) -> None:
        inputs = ready_inputs()
        inputs["error_chain_closure"] = {
            "required": True,
            "status": "blocked_needs_error_chain_closure",
            "can_modify": False,
        }

        result = build_requirement_understanding(**inputs)

        self.assertFalse(result.can_modify)
        self.assertIn("error_chain_closure", {check.name for check in result.checks if check.status == "blocked"})


if __name__ == "__main__":
    unittest.main()
