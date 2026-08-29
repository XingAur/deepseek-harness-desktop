from __future__ import annotations

import unittest

from app.dynamic_planning import ROLE_CATALOG
from app.role_capability_skill_registry import (
    RoleRoutingError,
    load_role_capability_skill_registry,
)
from app.task_context import TaskIntentContext


class RoleCapabilitySkillRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_role_capability_skill_registry()
        cls.context = TaskIntentContext(
            background="Harness 需要把业务意图、角色和执行能力收口。",
            goal="为每个角色生成唯一可审计路由。",
            scenarios=("本地代码任务", "隔离 fixture 验证"),
            desired_outcome="不再出现角色有工具但没有 Skill/MCP 归属的情况。",
        )

    def test_every_dynamic_role_has_a_route_for_each_allowed_tool(self) -> None:
        self.registry.validate_role_catalog(ROLE_CATALOG)
        for role_id, role in ROLE_CATALOG.items():
            with self.subTest(role=role_id):
                routes = self.registry.route_role(
                    role_id,
                    role.allowed_tools,
                    task_context=self.context,
                )
                self.assertEqual(role.allowed_tools, tuple(item.tool for item in routes))
                self.assertTrue(all(item.skill for item in routes))

    def test_code_evidence_is_a_real_canonical_skill(self) -> None:
        route = self.registry.resolve_capability("source.search", "his-engineering")

        self.assertEqual("his-code-evidence", route.skill)
        self.assertEqual("codex_skill", self.registry.skills[route.skill].kind)

    def test_knowledge_route_declares_the_mcp_server(self) -> None:
        route = self.registry.resolve_capability("knowledge.retrieve", "his-knowledge")

        self.assertEqual("mcp_skill", self.registry.skills[route.skill].kind)
        self.assertEqual("his-knowledge", route.mcp_server)

    def test_flux_lite_and_auto_repair_are_internal_and_not_provider_executable(self) -> None:
        for capability in ("harness.flux-lite.learn", "harness.flux-lite.replay", "harness.auto-repair.run"):
            with self.subTest(capability=capability):
                route = self.registry.resolve_internal_capability(capability)
                self.assertEqual("internal", route.execution_kind)
                self.assertFalse(route.external_executable)

    def test_visual_evidence_extraction_is_internal_and_reserved_for_requirement_analysis(self) -> None:
        route = self.registry.resolve_capability("visual.extract", "harness")

        self.assertEqual("harness-visual-evidence", route.skill)
        self.assertEqual("internal", route.execution_kind)
        self.assertFalse(route.external_executable)
        product_routes = self.registry.route_role(
            "product_analyst",
            ROLE_CATALOG["product_analyst"].allowed_tools,
            task_context=self.context,
        )
        self.assertIn("visual.extract", [item.capability for item in product_routes])

    def test_incomplete_context_fails_closed_before_route_resolution(self) -> None:
        with self.assertRaisesRegex(RoleRoutingError, "task_context_incomplete"):
            self.registry.route_role(
                "developer",
                ROLE_CATALOG["developer"].allowed_tools,
                task_context=TaskIntentContext(),
            )


if __name__ == "__main__":
    unittest.main()
