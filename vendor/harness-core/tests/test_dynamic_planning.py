from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from app.dynamic_planning import (
    DynamicPlanningRequest,
    PlanningSignals,
    TaskEdge,
    build_dynamic_plan,
    render_dynamic_plan_outputs,
    validate_task_graph,
    write_dynamic_plan_outputs,
)


class DynamicPlanningComplexityTests(unittest.TestCase):
    def test_simple_requirement_uses_minimum_team(self) -> None:
        plan = build_dynamic_plan(simple_request(), enabled=True)

        self.assertEqual("ready", plan.status)
        self.assertEqual("simple", plan.assessment.level)
        self.assertEqual(
            ["product_analyst", "frontend_developer", "test_executor"],
            [role.role_id for role in plan.team.roles],
        )
        self.assertFalse(plan.code_write_enabled)
        self.assertFalse(plan.external_actions_enabled)

    def test_medium_requirement_adds_architecture_and_independent_review(self) -> None:
        request = DynamicPlanningRequest(
            requirement_id="DFHIS-MEDIUM",
            title="查询条件前后端联动调整",
            demand_text="前端新增查询条件，后端接口接收并保持历史默认行为。",
            signals=PlanningSignals(
                affected_layers=("frontend", "backend"),
                estimated_file_count=6,
                dependency_mode="serial",
                evidence_status="partial",
                verification_mode="integration",
                allowed_paths={
                    "frontend": ("src/views/query.vue",),
                    "backend": ("src/main/java/QueryService.java",),
                },
            ),
        )

        plan = build_dynamic_plan(request, enabled=True)

        self.assertEqual("medium", plan.assessment.level)
        role_ids = [role.role_id for role in plan.team.roles]
        self.assertIn("architect", role_ids)
        self.assertIn("code_reviewer", role_ids)
        self.assertIn("frontend_developer", role_ids)
        self.assertIn("backend_developer", role_ids)
        review_node = next(node for node in plan.graph.nodes if node.role_id == "code_reviewer")
        self.assertNotIn(review_node.role_id, {"frontend_developer", "backend_developer"})
        developer = next(role for role in plan.team.roles if role.role_id == "frontend_developer")
        reviewer = next(role for role in plan.team.roles if role.role_id == "code_reviewer")
        self.assertIn("worktree_edit", developer.allowed_tools)
        self.assertIn("git_push", developer.forbidden_tools)
        self.assertNotIn("worktree_edit", reviewer.allowed_tools)
        self.assertGreater(developer.input_budget_tokens, 0)
        self.assertGreater(developer.timeout_seconds, 0)

    def test_large_requirement_uses_layer_specific_team_and_acceptance(self) -> None:
        plan = build_dynamic_plan(large_request(), enabled=True)

        self.assertEqual("large", plan.assessment.level)
        role_ids = {role.role_id for role in plan.team.roles}
        self.assertTrue(
            {
                "frontend_developer",
                "backend_developer",
                "database_specialist",
                "test_designer",
                "test_executor",
                "acceptance_agent",
            }.issubset(role_ids)
        )

    def test_high_risk_rule_cannot_be_downgraded_by_low_score(self) -> None:
        request = DynamicPlanningRequest(
            requirement_id="DFHIS-HIGH",
            title="医保部分退费在院状态校验",
            demand_text="医保部分退费前增加在院状态判断，收费结算原逻辑保持不变。",
            signals=PlanningSignals(
                affected_layers=("frontend",),
                estimated_file_count=1,
                evidence_status="complete",
                verification_mode="targeted",
                allowed_paths={"frontend": ("src/views/refund.vue",)},
            ),
        )

        plan = build_dynamic_plan(request, enabled=True)

        self.assertEqual("high_risk", plan.assessment.level)
        self.assertEqual("needs_human_confirmation", plan.status)
        self.assertIn("医保", plan.assessment.forced_upgrade_rules)
        role_ids = {role.role_id for role in plan.team.roles}
        self.assertTrue(
            {"high_risk_reviewer", "conflict_arbiter", "human_gate"}.issubset(role_ids)
        )


class DynamicPlanningGraphTests(unittest.TestCase):
    def test_disjoint_development_paths_are_parallel(self) -> None:
        plan = build_dynamic_plan(large_request(), enabled=True)
        development_nodes = [node for node in plan.graph.nodes if node.node_kind == "implementation"]

        self.assertGreaterEqual(len(development_nodes), 3)
        self.assertEqual(1, len({node.parallel_group for node in development_nodes}))

    def test_overlapping_development_paths_are_serialized(self) -> None:
        request = replace(
            large_request(),
            signals=replace(
                large_request().signals,
                allowed_paths={
                    "frontend": ("shared/src",),
                    "backend": ("shared/src",),
                    "database": ("db/migrations",),
                },
            ),
        )

        plan = build_dynamic_plan(request, enabled=True)
        implementation_ids = {node.node_id for node in plan.graph.nodes if node.node_kind == "implementation"}
        serialized_edges = [
            edge
            for edge in plan.graph.edges
            if edge.reason == "allowed_paths_overlap"
            and edge.source in implementation_ids
            and edge.target in implementation_ids
        ]

        self.assertEqual(1, len(serialized_edges))
        self.assertEqual([], validate_task_graph(plan.graph, plan.team))

    def test_missing_implementation_paths_needs_evidence(self) -> None:
        request = replace(simple_request(), signals=replace(simple_request().signals, allowed_paths={}))

        plan = build_dynamic_plan(request, enabled=True)

        self.assertEqual("needs_evidence", plan.status)
        self.assertIn("allowed_paths", "\n".join(plan.blockers))

    def test_cycle_is_rejected(self) -> None:
        plan = build_dynamic_plan(simple_request(), enabled=True)
        graph = replace(
            plan.graph,
            edges=plan.graph.edges
            + (
                TaskEdge(
                    source="verify",
                    target="requirement_analysis",
                    dependency_type="requires",
                    artifact_schema="VerificationResult",
                    reason="test_cycle",
                ),
            ),
        )

        errors = validate_task_graph(graph, plan.team)

        self.assertIn("task_graph_cycle", errors)

    def test_unsafe_allowed_path_is_blocked(self) -> None:
        request = replace(
            simple_request(),
            signals=replace(
                simple_request().signals,
                allowed_paths={"frontend": ("../outside.vue",)},
            ),
        )

        plan = build_dynamic_plan(request, enabled=True)

        self.assertEqual("blocked", plan.status)
        self.assertTrue(any(item.startswith("unsafe_allowed_path") for item in plan.blockers))

    def test_handoff_contracts_use_stable_hashes_and_real_producers(self) -> None:
        plan = build_dynamic_plan(simple_request(), enabled=True)

        self.assertEqual(len(plan.graph.nodes), len(plan.handoffs))
        self.assertTrue(all(contract.content_hash.startswith("sha256:") for contract in plan.handoffs))
        producer_by_node = {node.node_id: node.role_id for node in plan.graph.nodes}
        self.assertTrue(all(producer_by_node[item.node_id] == item.producer for item in plan.handoffs))


class DynamicPlanningOutputTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        plan = build_dynamic_plan(simple_request())

        self.assertEqual("disabled", plan.status)
        self.assertEqual((), plan.graph.nodes)

    def test_outputs_are_readonly_and_do_not_claim_execution(self) -> None:
        plan = build_dynamic_plan(simple_request(), enabled=True)
        rendered = render_dynamic_plan_outputs(plan)

        self.assertIn("dynamic-plan", rendered)
        self.assertIn("只读规划", rendered)
        self.assertNotIn("业务验收通过", rendered)
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_dynamic_plan_outputs(Path(temp_dir), plan)
            self.assertEqual(3, len(paths))
            self.assertTrue(all(path.exists() for path in paths))

    def test_cli_requires_explicit_enable_and_writes_plan(self) -> None:
        payload = simple_request().to_dict()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            request_path = root / "request.json"
            output_dir = root / "output"
            request_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            disabled = subprocess.run(
                [
                    sys.executable,
                    "tools/dynamic_plan.py",
                    "--request-file",
                    str(request_path),
                    "--output-dir",
                    str(output_dir),
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )
            enabled = subprocess.run(
                [
                    sys.executable,
                    "tools/dynamic_plan.py",
                    "--request-file",
                    str(request_path),
                    "--output-dir",
                    str(output_dir),
                    "--enable",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, disabled.returncode, disabled.stderr)
            self.assertIn("disabled", disabled.stdout)
            self.assertEqual(0, enabled.returncode, enabled.stderr)
            self.assertIn("ready", enabled.stdout)
            self.assertTrue((output_dir / "dynamic_plan.json").exists())


def simple_request() -> DynamicPlanningRequest:
    return DynamicPlanningRequest(
        requirement_id="DFHIS-SIMPLE",
        title="挂号页面证件类型默认值调整",
        demand_text="前端单页面默认值与档案管理保持一致。",
        evidence_refs=("user:instruction", "code:archive-defaults"),
        signals=PlanningSignals(
            affected_layers=("frontend",),
            repository_count=1,
            estimated_file_count=2,
            dependency_mode="none",
            evidence_status="complete",
            verification_mode="targeted",
            rollback_mode="single_patch",
            allowed_paths={"frontend": ("src/views/register.vue",)},
        ),
    )


def large_request() -> DynamicPlanningRequest:
    return DynamicPlanningRequest(
        requirement_id="DFHIS-LARGE",
        title="跨模块患者查询能力调整",
        demand_text="前端、后端和数据库查询契约联动，包含并行子目标和多仓库验收。",
        signals=PlanningSignals(
            affected_layers=("frontend", "backend", "database"),
            repository_count=2,
            estimated_file_count=12,
            dependency_mode="parallel",
            evidence_status="partial",
            verification_mode="login_ui",
            rollback_mode="multi_repo",
            allowed_paths={
                "frontend": ("web/src/views",),
                "backend": ("service/src/main/java",),
                "database": ("database/readonly-sql",),
            },
        ),
    )


if __name__ == "__main__":
    unittest.main()
