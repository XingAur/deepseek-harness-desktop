from __future__ import annotations

import unittest

from app.dynamic_planning import DynamicPlanningRequest, build_dynamic_plan
from app.task_context import TaskIntentContext, TaskIntentContextError


class TaskIntentContextTests(unittest.TestCase):
    def complete_context(self) -> TaskIntentContext:
        return TaskIntentContext(
            background="现有 Harness 已有角色和 capability，但路由关系分散。",
            goal="让每个任务先理解业务意图，再选择唯一可审计的执行路径。",
            scenarios=("本地代码分析", "隔离 fixture 验证"),
            desired_outcome="角色、能力、Skill/MCP 和验收证据能够闭环。",
            constraints=("不修改正式数据库", "外部写入默认关闭"),
            acceptance_criteria=("缺少关键意图时路由必须阻断", "完整上下文产生稳定摘要哈希"),
            source_refs=("user:instruction", "local:harness-audit"),
        )

    def test_complete_context_is_stable_and_round_trips(self) -> None:
        context = self.complete_context()

        self.assertTrue(context.is_complete)
        self.assertEqual((), context.missing_fields)
        self.assertEqual(context.content_hash, TaskIntentContext.from_dict(context.to_dict()).content_hash)
        self.assertEqual("complete", context.to_dict()["status"])

    def test_missing_background_and_scenarios_are_reported(self) -> None:
        context = TaskIntentContext(
            background="",
            goal="完成目标",
            scenarios=(),
            desired_outcome="得到结果",
        )

        self.assertFalse(context.is_complete)
        self.assertEqual(("background", "scenarios"), context.missing_fields)

    def test_sensitive_context_is_rejected(self) -> None:
        with self.assertRaisesRegex(TaskIntentContextError, "sensitive"):
            TaskIntentContext(
                background="authorization: bearer super-secret-token-value",
                goal="完成目标",
                scenarios=("本地验证",),
                desired_outcome="得到结果",
            )

    def test_dynamic_plan_carries_context_status_and_hash(self) -> None:
        request = DynamicPlanningRequest(
            requirement_id="LOCAL-CONTEXT",
            title="Harness 路由收口",
            demand_text="补齐角色到能力和 Skill 的映射。",
            task_context=self.complete_context(),
        )

        payload = build_dynamic_plan(request, enabled=True).to_dict()

        self.assertEqual("complete", payload["task_context"]["status"])
        self.assertEqual(self.complete_context().content_hash, payload["task_context"]["content_hash"])
        self.assertTrue(payload["role_routes"])
        self.assertTrue(all(item["bindings"] for item in payload["role_routes"]))


if __name__ == "__main__":
    unittest.main()
