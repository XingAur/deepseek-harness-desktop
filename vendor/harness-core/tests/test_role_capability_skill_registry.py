from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.dynamic_planning import ROLE_CATALOG
from app.role_capability_skill_registry import (
    MATRIX_SCHEMA_VERSION,
    RoleCapabilitySkillRegistryError,
    RoleRoutingError,
    load_role_capability_skill_registry,
)
from app.task_context import TaskIntentContext


ROOT = Path(__file__).resolve().parents[1]


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
        self.assertEqual("provider", route.execution_kind)
        self.assertEqual("mcp_required", route.required_boundary)
        self.assertEqual("compatibility", route.migration_state)

    def test_matrix_v2_declares_truthful_boundary_fields_everywhere(self) -> None:
        payload = json.loads(
            (ROOT / "config/role_capability_skill_matrix.json").read_text(encoding="utf-8")
        )

        self.assertEqual("his-role-capability-skill-matrix.v2", MATRIX_SCHEMA_VERSION)
        self.assertEqual(MATRIX_SCHEMA_VERSION, payload["schema_version"])
        for route in payload["capability_routes"]:
            self.assertIn("required_boundary", route)
            self.assertIn("migration_state", route)
        route_by_key = {
            (route["capability"], route["provider"]): route
            for route in payload["capability_routes"]
        }
        for binding in payload["bindings"].values():
            route = route_by_key[(binding["capability"], binding["provider"])]
            self.assertEqual(route["required_boundary"], binding["required_boundary"])
            self.assertEqual(route["migration_state"], binding["migration_state"])

    def test_current_route_families_match_the_approved_architecture(self) -> None:
        internal = self.registry.resolve_capability("harness.artifacts.read", "harness")
        local = self.registry.resolve_capability("source.search", "his-engineering")
        cloud = self.registry.resolve_capability("gitlab.read", "gitlab")
        local_governance = self.registry.resolve_capability(
            "database.change-plan", "postgresql"
        )

        self.assertEqual(
            ("internal", "control_plane_internal", "native"),
            (internal.execution_kind, internal.required_boundary, internal.migration_state),
        )
        self.assertEqual(
            ("provider", "worker_allowed", "native"),
            (local.execution_kind, local.required_boundary, local.migration_state),
        )
        self.assertEqual(
            ("mcp", "mcp_required", "native"),
            (cloud.execution_kind, cloud.required_boundary, cloud.migration_state),
        )
        self.assertEqual(
            ("provider", "control_plane_internal", "native"),
            (
                local_governance.execution_kind,
                local_governance.required_boundary,
                local_governance.migration_state,
            ),
        )

    def test_role_routes_propagate_boundary_and_migration_state(self) -> None:
        routes = self.registry.route_role(
            "developer",
            ROLE_CATALOG["developer"].allowed_tools,
            task_context=self.context,
        )

        source_route = next(item for item in routes if item.capability == "source.search")
        self.assertEqual("worker_allowed", source_route.required_boundary)
        self.assertEqual("native", source_route.migration_state)

    def test_invalid_boundary_combinations_fail_closed(self) -> None:
        cases = (
            (
                "internal_requires_control_plane_native",
                "harness.artifacts.read",
                {"required_boundary": "worker_allowed"},
            ),
            (
                "local_provider_requires_worker_native",
                "source.search",
                {"migration_state": "compatibility"},
            ),
            (
                "native_mcp_route_cannot_claim_provider_execution",
                "gitlab.read",
                {"execution_kind": "provider"},
            ),
            (
                "mcp_skill_provider_is_not_native",
                "knowledge.retrieve",
                {"migration_state": "native"},
            ),
        )
        for label, capability, changes in cases:
            with self.subTest(case=label):
                payload = json.loads(
                    (ROOT / "config/role_capability_skill_matrix.json").read_text(
                        encoding="utf-8"
                    )
                )
                route = next(
                    item for item in payload["capability_routes"]
                    if item["capability"] == capability
                )
                route.update(changes)
                with tempfile.TemporaryDirectory() as temp_dir:
                    path = Path(temp_dir) / "matrix.json"
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(RoleCapabilitySkillRegistryError):
                        load_role_capability_skill_registry(path, harness_root=ROOT)

    def test_native_mcp_route_requires_matching_declared_server(self) -> None:
        payload = json.loads(
            (ROOT / "config/role_capability_skill_matrix.json").read_text(encoding="utf-8")
        )
        route = next(
            item for item in payload["capability_routes"]
            if item["capability"] == "knowledge.retrieve"
        )
        route.update(
            execution_kind="mcp",
            required_boundary="mcp_required",
            migration_state="native",
            mcp_server="wrong-server",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "matrix.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(RoleCapabilitySkillRegistryError):
                load_role_capability_skill_registry(path, harness_root=ROOT)

    def test_binding_boundary_must_match_capability_route(self) -> None:
        payload = json.loads(
            (ROOT / "config/role_capability_skill_matrix.json").read_text(encoding="utf-8")
        )
        payload["bindings"]["search_code"]["required_boundary"] = "mcp_required"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "matrix.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(RoleCapabilitySkillRegistryError):
                load_role_capability_skill_registry(path, harness_root=ROOT)

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
