from __future__ import annotations

import unittest

from app.change_context_applicability import (
    CandidateTarget,
    ContextApplicabilityGate,
)
from app.task_context import TaskIntentContext


def intent(goal: str = "调整患者列表展示") -> TaskIntentContext:
    return TaskIntentContext(
        background="现有页面需要小范围调整。",
        goal=goal,
        scenarios=("用户打开目标页面",),
        desired_outcome="行为符合当前明确需求。",
        constraints=("保持原兼容逻辑",),
        acceptance_criteria=("专项测试通过",),
        source_refs=("evidence://requirement/one",),
    )


def target(kind: str, path: str, *, repo: str = "frontend", relationships: tuple[str, ...] = ()) -> CandidateTarget:
    return CandidateTarget(
        repository_alias=repo,
        relative_path=path,
        target_kind=kind,
        evidence_refs=(f"evidence://code/{repo}-{kind}",),
        relationships=relationships,
    )


class ContextApplicabilityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = ContextApplicabilityGate()

    def test_only_proven_document_copy_and_style_targets_are_not_applicable(self) -> None:
        cases = (
            ("documentation", "docs/usage.md", "CTX-DATA-NA-001"),
            ("copy_only", "src/i18n/labels.json", "CTX-DATA-NA-002"),
            ("style_only", "src/pages/list.scss", "CTX-DATA-NA-003"),
        )
        for kind, path, rule_id in cases:
            with self.subTest(kind=kind):
                result = self.gate.assess(task_context=intent(), candidate_targets=(target(kind, path),))
                data = result.decision("data_graph")
                self.assertEqual("not_applicable", data.requirement)
                self.assertIn(rule_id, data.rule_ids)
                self.assertTrue(data.evidence_refs)

    def test_persistence_and_contract_targets_require_data_graph(self) -> None:
        cases = (
            ("frontend_api_field", "src/api/order.ts", "CTX-DATA-002"),
            ("frontend_save_path", "src/pages/order.vue", "CTX-DATA-006"),
            ("controller", "src/main/OrderController.java", "CTX-DATA-002"),
            ("service", "src/main/OrderService.java", "CTX-DATA-001"),
            ("repository", "src/main/OrderRepository.java", "CTX-DATA-008"),
            ("dao", "src/main/OrderDao.java", "CTX-DATA-008"),
            ("mapper", "src/main/OrderMapper.java", "CTX-DATA-008"),
            ("entity", "src/main/Order.java", "CTX-DATA-008"),
            ("dto", "src/main/OrderDto.java", "CTX-DATA-003"),
            ("sql", "db/order.sql", "CTX-DATA-004"),
            ("migration", "db/V10__order.sql", "CTX-DATA-004"),
            ("datasource", "config/datasource.yml", "CTX-DATA-005"),
            ("orm_mapping", "config/order.xml", "CTX-DATA-003"),
            ("schema_configuration", "config/schema.yml", "CTX-DATA-005"),
            ("unknown", "src/unknown.ext", "CTX-DATA-009"),
            ("frontend_state", "src/store/order.ts", "CTX-DATA-009"),
        )
        for kind, path, rule_id in cases:
            with self.subTest(kind=kind):
                data = self.gate.assess(task_context=intent(), candidate_targets=(target(kind, path),)).decision("data_graph")
                self.assertEqual("required", data.requirement)
                self.assertIn(rule_id, data.rule_ids)

    def test_detected_relationship_overrides_a_safe_file_label(self) -> None:
        item = target("copy_only", "src/i18n/labels.json", relationships=("api_field",))
        data = self.gate.assess(task_context=intent(), candidate_targets=(item,)).decision("data_graph")
        self.assertEqual("required", data.requirement)
        self.assertIn("CTX-DATA-002", data.rule_ids)

    def test_missing_targets_and_incomplete_intent_fail_conservatively(self) -> None:
        empty = self.gate.assess(task_context=intent(), candidate_targets=())
        self.assertEqual("required", empty.decision("data_graph").requirement)
        self.assertIn("CTX-DATA-009", empty.decision("data_graph").rule_ids)

        incomplete = self.gate.assess(task_context=TaskIntentContext.empty(), candidate_targets=(target("documentation", "README.md"),))
        self.assertEqual("blocked", incomplete.status)
        self.assertTrue(incomplete.blockers)

    def test_model_hint_cannot_downgrade_and_high_risk_only_adds_tags(self) -> None:
        result = self.gate.assess(
            task_context=intent("医保收费结算金额调整"),
            candidate_targets=(target("entity", "src/main/FeeEntity.java"),),
            model_hint={"data_graph": "not_applicable"},
        )
        self.assertEqual("required", result.decision("data_graph").requirement)
        self.assertIn("his_high_risk", result.risk_tags)

    def test_multi_repository_persistence_target_requires_data_for_bounded_change(self) -> None:
        result = self.gate.assess(
            task_context=intent(),
            candidate_targets=(
                target("style_only", "src/a.scss", repo="web"),
                target("repository", "src/OrderRepository.java", repo="service"),
            ),
        )
        self.assertEqual("required", result.decision("data_graph").requirement)
        self.assertIn("CTX-DATA-007", result.decision("data_graph").rule_ids)

    def test_all_four_layer_decisions_have_deterministic_order(self) -> None:
        result = self.gate.assess(task_context=intent(), candidate_targets=(target("style_only", "src/a.scss"),))
        self.assertEqual(("project_graph", "change_scope", "code_graph", "data_graph"), tuple(item.layer_type for item in result.decisions))
        self.assertTrue(all(item.requirement == "required" for item in result.decisions[:3]))

    def test_absolute_paths_and_missing_evidence_are_rejected(self) -> None:
        for item in (
            CandidateTarget("repo", "/tmp/a.py", "documentation", ("evidence://code/a",), ()),
            CandidateTarget("repo", "docs/a.md", "documentation", (), ()),
        ):
            with self.subTest(path=item.relative_path):
                with self.assertRaises(ValueError):
                    self.gate.assess(task_context=intent(), candidate_targets=(item,))


if __name__ == "__main__":
    unittest.main()
