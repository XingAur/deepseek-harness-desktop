from __future__ import annotations

import unittest

from app.demand_progress import (
    build_demand_progress_snapshot,
    demand_progress_to_markdown,
)


class DemandProgressTests(unittest.TestCase):
    def test_pre_change_snapshot_explains_scope_and_confirmation(self) -> None:
        snapshot = build_demand_progress_snapshot(
            phase="pre_change",
            task_events=[
                {"stage": "intake", "status": "completed", "reason": "已接收"},
                {"stage": "provider_evidence", "status": "completed", "reason": "已读取"},
                {"stage": "calibration", "status": "completed", "reason": "已校准"},
                {"stage": "technical_decision", "status": "completed", "reason": "已定位"},
                {"stage": "ownership", "status": "completed", "reason": "已划分"},
                {"stage": "acceptance", "status": "completed", "reason": "已生成"},
                {"stage": "governance", "status": "blocked", "reason": "需要确认业务口径"},
                {"stage": "single_pass_contract", "status": "skipped", "reason": "未生成"},
            ],
            requirement_calibration={
                "status": "needs_human_confirmation",
                "complexity": {"level": "complex"},
                "proposed_subtasks": [
                    {"id": "FRONTEND-001", "title": "页面调整", "boundary": "只改展示"},
                ],
                "must_confirm": ["确认业务口径"],
                "warnings": [],
            },
            technical_decision={
                "can_patch": False,
                "selected_projects": [
                    {"name": "df-web-demo", "path": "/tmp/web", "role": "frontend", "exists": True},
                    {"name": "df-mic-demo", "path": "/tmp/mic", "role": "backend", "exists": True},
                ],
                "implementation_decision": {"blockers": ["证据不足"]},
            },
            change_ownership={
                "frontend": {"status": "required", "paths": ["src/views/demo.vue"]},
                "backend": {"status": "candidate", "paths": ["src/main/java/Demo.java"]},
            },
            governance={"status": "blocked_needs_requirement", "can_modify": False},
            single_pass_contract=None,
            run_status="failed",
            evaluation_status="blocked_requirement_governance",
            execution_mode="readonly",
        )

        self.assertEqual("scope_confirmation", snapshot["current_stage"])
        self.assertFalse(snapshot["confirmation"]["can_modify"])
        self.assertEqual("user", snapshot["confirmation"]["required_by"])
        self.assertIn("FRONTEND-001", [item["id"] for item in snapshot["proposed_subtasks"]])
        self.assertEqual(
            ["df-web-demo", "df-mic-demo"],
            [item["name"] for item in snapshot["affected_scope"]["projects"]],
        )

    def test_post_change_snapshot_requests_business_acceptance_not_code_review(self) -> None:
        snapshot = build_demand_progress_snapshot(
            phase="post_change",
            task_events=[
                {"stage": "intake", "status": "completed", "reason": "已接收"},
                {"stage": "provider_evidence", "status": "completed", "reason": "已读取"},
                {"stage": "calibration", "status": "completed", "reason": "已校准"},
                {"stage": "technical_decision", "status": "completed", "reason": "已定位"},
                {"stage": "ownership", "status": "completed", "reason": "已划分"},
                {"stage": "acceptance", "status": "completed", "reason": "已生成"},
                {"stage": "governance", "status": "completed", "reason": "已通过"},
                {"stage": "single_pass_contract", "status": "completed", "reason": "已冻结"},
                {"stage": "local_engineering", "status": "completed", "reason": "已修改"},
                {"stage": "verification", "status": "completed", "reason": "已验证"},
                {"stage": "knowledge_candidate", "status": "skipped", "reason": "不写入"},
                {"stage": "audit", "status": "completed", "reason": "已记录"},
            ],
            requirement_calibration={"status": "ready_for_development", "proposed_subtasks": [], "must_confirm": [], "warnings": []},
            technical_decision={
                "can_patch": True,
                "selected_projects": [{"name": "df-web-demo", "path": "/tmp/web", "role": "frontend", "exists": True}],
                "implementation_decision": {"blockers": []},
            },
            change_ownership={"frontend": {"status": "required", "paths": ["src/views/demo.vue"]}},
            governance={"status": "ready_for_local_change", "can_modify": True},
            single_pass_contract={"status": "ready", "allowed_paths": ["src/views/demo.vue"]},
            run_status="success",
            evaluation_status="ready_for_manual_review",
            execution_mode="worktree",
        )

        self.assertEqual("business_acceptance", snapshot["current_stage"])
        self.assertTrue(snapshot["confirmation"]["required"])
        self.assertEqual("business", snapshot["confirmation"]["required_by"])
        self.assertIn("页面效果", snapshot["next_action"])
        markdown = demand_progress_to_markdown(snapshot)
        self.assertIn("改动后业务确认", markdown)
        self.assertIn("只确认页面效果和业务结果", markdown)
        self.assertIn("df-web-demo", markdown)
        self.assertNotIn("确认 Java Service 是否正确", markdown)

    def test_blocked_post_snapshot_does_not_claim_business_acceptance_is_ready(self) -> None:
        snapshot = build_demand_progress_snapshot(
            phase="post_change",
            task_events=(
                {"stage": "intake", "status": "completed", "reason": "已接收"},
                {"stage": "governance", "status": "blocked", "reason": "证据不足"},
                {"stage": "local_engineering", "status": "skipped", "reason": "未开始"},
                {"stage": "verification", "status": "skipped", "reason": "未开始"},
                {"stage": "audit", "status": "completed", "reason": "已归档"},
            ),
            requirement_calibration={"must_confirm": ["请补充字段和值域"], "warnings": []},
            technical_decision={},
            change_ownership={},
            governance={"status": "blocked"},
            single_pass_contract={"status": "blocked"},
            run_status="blocked",
            evaluation_status="blocked_requirement_governance",
            execution_mode="worktree",
        )

        self.assertEqual("scope_confirmation", snapshot["current_stage"])
        self.assertFalse(snapshot["confirmation"]["required"])
        self.assertEqual("blocked", snapshot["stage_statuses"]["business_acceptance"]["status"])

    def test_readonly_scope_does_not_hide_evidence_confirmation(self) -> None:
        snapshot = build_demand_progress_snapshot(
            phase="pre_change",
            task_events=(
                {"stage": "intake", "status": "completed", "reason": "已接收"},
                {"stage": "calibration", "status": "completed", "reason": "已校准"},
                {"stage": "governance", "status": "blocked", "reason": "证据不足"},
            ),
            requirement_calibration={
                "must_confirm": ["请确认字段默认行为"],
                "warnings": [],
            },
            technical_decision={},
            change_ownership={},
            governance={"status": "blocked_needs_requirement", "can_modify": False},
            single_pass_contract={"status": "blocked"},
            run_status="blocked",
            evaluation_status="blocked_requirement_governance",
            execution_mode="readonly",
            scope_confirmation_status="not_required",
            scope_confirmation_reason="当前执行模式不修改本地业务目录。",
        )

        self.assertTrue(snapshot["confirmation"]["required"])
        self.assertEqual("evidence_or_business_scope", snapshot["confirmation"]["gate"])
        self.assertEqual("user", snapshot["confirmation"]["required_by"])

    def test_readonly_completed_analysis_does_not_ask_for_scanned_evidence_again(self) -> None:
        snapshot = build_demand_progress_snapshot(
            phase="pre_change",
            task_events=(
                {"stage": "intake", "status": "completed", "reason": "已接收"},
                {"stage": "calibration", "status": "completed", "reason": "已校准"},
                {"stage": "technical_decision", "status": "completed", "reason": "已定位"},
                {"stage": "ownership", "status": "completed", "reason": "已划分"},
                {"stage": "acceptance", "status": "completed", "reason": "已生成"},
                {"stage": "governance", "status": "blocked", "reason": "改码门禁关闭"},
            ),
            requirement_calibration={
                "status": "needs_human_confirmation",
                "must_confirm": [],
                "warnings": [],
            },
            technical_decision={
                "service_graph": {"status": "evidence_ready"},
                "selected_projects": [],
                "blockers": ["服务契约需要架构选择"],
            },
            change_ownership={},
            governance={"status": "review_only", "can_modify": False},
            single_pass_contract={"status": "blocked"},
            run_status="success",
            evaluation_status="analysis_complete_readonly",
            execution_mode="readonly",
            scope_confirmation_status="not_required",
            scope_confirmation_reason="当前执行模式不修改本地业务目录。",
            readonly_analysis_complete=True,
        )

        self.assertFalse(snapshot["confirmation"]["required"])
        self.assertIn("只读服务边界分析已完成", snapshot["next_action"])
        self.assertNotIn("请补充上面列出的业务口径或证据", snapshot["next_action"])

    def test_auto_continuation_is_explicit_and_does_not_request_repeat_prompt(self) -> None:
        snapshot = build_demand_progress_snapshot(
            phase="pre_change",
            task_events=(),
            requirement_calibration={"must_confirm": [], "warnings": []},
            technical_decision={
                "multi_service_change_contract": {
                    "continuation": {
                        "status": "auto_continue_readonly",
                        "requires_user": False,
                        "next_action": "继续核验 HTTP 路由、Controller 与 DTO 契约",
                    }
                }
            },
            change_ownership={},
            governance={},
            single_pass_contract={"status": "blocked"},
            run_status="success",
            evaluation_status="analysis_in_progress",
            execution_mode="readonly",
        )

        self.assertFalse(snapshot["confirmation"]["required"])
        self.assertIn("自动继续只读分析", snapshot["next_action"])
        self.assertIn("不需要你重复发送", snapshot["next_action"])

    def test_user_choice_continuation_overrides_generic_readonly_next_action(self) -> None:
        snapshot = build_demand_progress_snapshot(
            phase="post_change",
            task_events=(),
            requirement_calibration={"must_confirm": ["补充药品 HTTP 契约"]},
            technical_decision={
                "multi_service_change_contract": {
                    "continuation": {
                        "status": "await_user_choice",
                        "requires_user": True,
                        "next_action": "补充药品 HTTP 契约后再继续。",
                    }
                }
            },
            change_ownership={},
            governance={"blockers": ["补充药品 HTTP 契约"]},
            single_pass_contract={"status": "blocked"},
            run_status="success",
            evaluation_status="analysis_complete_readonly",
            execution_mode="readonly",
            readonly_analysis_complete=True,
        )

        self.assertEqual("补充药品 HTTP 契约后再继续。", snapshot["next_action"])

    def test_affected_scope_separates_change_projects_from_evidence_projects(self) -> None:
        snapshot = build_demand_progress_snapshot(
            phase="pre_change",
            task_events=(),
            requirement_calibration={},
            technical_decision={
                "selected_projects": [
                    {
                        "name": "df-web-yibaogl",
                        "path": "/tmp/df-web-yibaogl",
                        "role": "frontend",
                        "selection_scope": "change_required",
                    },
                    {
                        "name": "df-mic-yibaogl",
                        "path": "/tmp/df-mic-yibaogl",
                        "role": "backend",
                        "selection_scope": "candidate_change",
                    },
                    {
                        "name": "df-bff-jichufw",
                        "path": "/tmp/df-bff-jichufw",
                        "role": "backend",
                        "selection_scope": "existing_dependency",
                    },
                    {
                        "name": "df-his-api",
                        "path": "/tmp/df-his-api",
                        "role": "api",
                        "selection_scope": "contract_check",
                    },
                    {
                        "name": "df-bff-yibaogl",
                        "path": "/tmp/df-bff-yibaogl",
                        "role": "backend",
                        "selection_scope": "candidate_only",
                    },
                ]
            },
            change_ownership={},
            governance={},
            single_pass_contract=None,
            run_status="blocked",
            evaluation_status="blocked_requirement_governance",
            execution_mode="readonly",
        )

        scope = snapshot["affected_scope"]
        self.assertEqual(
            ["df-web-yibaogl", "df-mic-yibaogl"],
            [item["name"] for item in scope["projects"]],
        )
        self.assertEqual(
            ["change_required", "candidate_change"],
            [item["selection_scope"] for item in scope["projects"]],
        )
        self.assertEqual(
            ["df-bff-jichufw", "df-his-api", "df-bff-yibaogl"],
            [item["name"] for item in scope["evidence_projects"]],
        )
        markdown = demand_progress_to_markdown(snapshot)
        self.assertIn("### 证据与核验项目", markdown)
        self.assertIn("df-bff-jichufw", markdown)
        self.assertIn("仅候选，未形成实际改动证据", markdown)


if __name__ == "__main__":
    unittest.main()
