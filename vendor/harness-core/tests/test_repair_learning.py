from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from app.local_agent_contract import LocalAgentTask
from app.repair_learning import (
    LearningRule,
    LearningRuleState,
    PromotionEvidence,
    RetrospectiveSourceKind,
    RootCauseKind,
    build_current_task_rule,
    canonical_rule_bytes,
    derive_task_learning_context,
    match_rules,
    rule_key,
    validate_rule_payload,
)


class RepairLearningTests(unittest.TestCase):
    def task(
        self,
        *,
        task_key: str = "repair-fixture",
        allowed_paths: tuple[str, ...] = ("app/repair.py", "tests/test_repair.py"),
        commands: tuple[tuple[str, ...], ...] = (("python", "-m", "unittest", "tests.test_repair"),),
        request: str = "修复普通业务校验。",
    ) -> LocalAgentTask:
        return LocalAgentTask(
            task_key=task_key,
            project_path=Path("/safe/fixture"),
            request=request,
            allowed_paths=allowed_paths,
            verification_commands=commands,
            acceptance_criteria=("专项验证通过",),
            timeout_seconds=60,
            contract_hash="a" * 64,
            repository_root_identity=(1, 1),
            git_entry_identity=(1, 2),
            git_dir_identity=(1, 3),
            initial_head="b" * 40,
            allowed_path_parent_identities=((1, 4),),
            verification_executable_identities=((1, 5),),
        )

    def test_semantic_equivalent_payload_has_stable_rule_key(self) -> None:
        context = derive_task_learning_context(self.task(), run_id=41)
        rule = build_current_task_rule(
            context,
            root_cause=RootCauseKind.VERIFICATION_FAILURE,
            actions=("verification_replay", "reviewer_focus"),
        )
        payload = rule.to_payload()
        equivalent = {
            "actions": list(reversed(payload["actions"])),
            "match": dict(payload["match"]),
            "promotion_evidence": payload["promotion_evidence"],
            "root_cause": payload["root_cause"],
            "schema_version": payload["schema_version"],
            "source_kind": payload["source_kind"],
            "state": payload["state"],
        }

        self.assertEqual(rule.key, rule_key(equivalent))
        self.assertEqual(canonical_rule_bytes(payload), canonical_rule_bytes(equivalent))

    def test_unknown_action_is_rejected(self) -> None:
        payload = self._valid_payload()
        payload["actions"] = ["run_shell"]

        with self.assertRaises(ValueError):
            validate_rule_payload(payload)

    def test_unknown_repository_cannot_match_another_task(self) -> None:
        first = derive_task_learning_context(
            self.task(task_key="one", allowed_paths=("notes/item.txt",), commands=(("echo", "check"),)),
            run_id=1,
        )
        second = derive_task_learning_context(
            self.task(task_key="two", allowed_paths=("notes/item.txt",), commands=(("echo", "check"),)),
            run_id=2,
        )
        rule = build_current_task_rule(first)

        self.assertFalse(match_rules(second, (rule,)))

    def test_high_risk_rule_actions_are_check_only(self) -> None:
        context = derive_task_learning_context(
            self.task(request="修复医保结算金额校验。"), run_id=3
        )
        rule = build_current_task_rule(context, actions=("reviewer_focus", "path_coverage"))

        self.assertEqual(("path_coverage", "reviewer_focus"), rule.actions)
        payload = rule.to_payload()
        payload["actions"] = ["apply_patch"]
        with self.assertRaises(ValueError):
            validate_rule_payload(payload)

    def test_implementation_defect_is_a_canonical_explicit_root_cause(self) -> None:
        context = derive_task_learning_context(self.task(), run_id=37)
        rule = build_current_task_rule(
            context,
            root_cause=RootCauseKind.IMPLEMENTATION_DEFECT,
            source_kind=RetrospectiveSourceKind.OFFLINE_IMPORT,
            actions=("verification_replay", "reviewer_focus"),
        )

        self.assertEqual("implementation_defect", rule.root_cause.value)
        self.assertEqual(rule.key, rule_key(rule.to_payload()))

    def test_active_current_task_only_matches_the_same_run(self) -> None:
        context = derive_task_learning_context(self.task(), run_id=11)
        rule = build_current_task_rule(context)

        self.assertTrue(match_rules(context, (rule,)))
        self.assertFalse(match_rules(replace(context, run_id=12), (rule,)))

    def test_trial_and_stable_need_all_match_conditions_to_be_identical(self) -> None:
        context = derive_task_learning_context(self.task(), run_id=18)
        for state in (LearningRuleState.TRIAL, LearningRuleState.STABLE):
            rule = build_current_task_rule(
                context,
                state=state,
                promotion_evidence=self._stable_evidence() if state is LearningRuleState.STABLE else None,
            )
            self.assertTrue(match_rules(replace(context, run_id=19), (rule,)))
            self.assertFalse(
                match_rules(
                    replace(context, allowed_path_prefixes=("app/other.py",)), (rule,)
                )
            )
            self.assertFalse(
                match_rules(
                    replace(context, verification_command_fingerprints=("f" * 64,)),
                    (rule,),
                )
            )
            self.assertFalse(
                match_rules(replace(context, high_risk_tags=("billing",)), (rule,))
            )

    def test_lifecycle_states_are_exact_and_only_applicable_states_match(self) -> None:
        context = derive_task_learning_context(self.task(), run_id=22)
        self.assertEqual(
            {"draft", "active_current_task", "trial", "stable", "suspended", "retired"},
            {state.value for state in LearningRuleState},
        )
        for state in LearningRuleState:
            rule = build_current_task_rule(
                context,
                state=state,
                promotion_evidence=self._stable_evidence() if state is LearningRuleState.STABLE else None,
            )
            with self.subTest(state=state):
                self.assertEqual(
                    state in {
                        LearningRuleState.ACTIVE_CURRENT_TASK,
                        LearningRuleState.TRIAL,
                        LearningRuleState.STABLE,
                    },
                    bool(match_rules(context, (rule,))),
                )

    def test_stable_requires_distinct_task_workspace_evidence_and_no_counterexamples(self) -> None:
        context = derive_task_learning_context(self.task(), run_id=23)
        for evidence in (
            PromotionEvidence(("task-a", "task-b"), ("work-a", "work-b"), 0),
            PromotionEvidence(("task-a", "task-b", "task-c"), ("work-a",), 0),
            PromotionEvidence(("task-a", "task-b", "task-c"), ("work-a", "work-b"), 1),
        ):
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                build_current_task_rule(
                    context,
                    state=LearningRuleState.STABLE,
                    promotion_evidence=evidence,
                )

        stable = build_current_task_rule(
            context,
            state=LearningRuleState.STABLE,
            promotion_evidence=self._stable_evidence(),
        )
        self.assertEqual(LearningRuleState.STABLE, stable.state)
        payload = stable.to_payload()
        payload["promotion_evidence"]["counterexample_count"] = 1
        with self.assertRaises(ValueError):
            validate_rule_payload(payload)

    def test_active_current_task_requires_exact_contract_scope(self) -> None:
        context = derive_task_learning_context(self.task(), run_id=24)
        outside_scope_rule = build_current_task_rule(
            replace(context, allowed_path_prefixes=("app/outside.py",)),
            actions=("path_coverage",),
        )

        self.assertFalse(match_rules(context, (outside_scope_rule,)))

    def test_repository_classification_is_order_independent_and_exact(self) -> None:
        first = derive_task_learning_context(
            self.task(allowed_paths=("app/a.py", "README.md")), run_id=25
        )
        second = derive_task_learning_context(
            self.task(allowed_paths=("README.md", "app/a.py")), run_id=26
        )
        false_gradle = derive_task_learning_context(
            self.task(allowed_paths=("README.md",), commands=(("notgradle", "check"),)),
            run_id=27,
        )

        self.assertEqual("python", first.repository_kind)
        self.assertEqual(first.repository_kind, second.repository_kind)
        self.assertEqual("unknown", false_gradle.repository_kind)

    def test_versioned_python_executables_are_exact_python_markers(self) -> None:
        for executable in ("python3.11", "/usr/bin/python3.11"):
            context = derive_task_learning_context(
                self.task(allowed_paths=("README.md",), commands=((executable, "-m", "unittest"),)),
                run_id=28,
            )
            with self.subTest(executable=executable):
                self.assertEqual("python", context.repository_kind)

    def test_directly_constructed_invalid_candidates_fail_closed(self) -> None:
        context = derive_task_learning_context(self.task(), run_id=29)
        valid = build_current_task_rule(context)
        invalid_rules = (
            LearningRule(
                key=valid.key,
                state=LearningRuleState.STABLE,
                source_kind=valid.source_kind,
                root_cause=valid.root_cause,
                actions=valid.actions,
                context=context,
                promotion_evidence=None,
            ),
            LearningRule(
                key=valid.key,
                state=LearningRuleState.ACTIVE_CURRENT_TASK,
                source_kind=valid.source_kind,
                root_cause=valid.root_cause,
                actions=("apply_patch",),
                context=context,
            ),
            LearningRule(
                key="f" * 64,
                state=valid.state,
                source_kind=valid.source_kind,
                root_cause=valid.root_cause,
                actions=valid.actions,
                context=context,
            ),
        )

        self.assertFalse(match_rules(context, invalid_rules))

    def test_subclass_payload_spoof_cannot_return_unsafe_rule(self) -> None:
        context = derive_task_learning_context(self.task(), run_id=30)
        benign = build_current_task_rule(context)

        class PayloadSpoof(LearningRule):
            def to_payload(self) -> dict[str, object]:
                return benign.to_payload()

        spoof = PayloadSpoof(
            key=benign.key,
            state=LearningRuleState.STABLE,
            source_kind=benign.source_kind,
            root_cause=benign.root_cause,
            actions=("apply_patch",),
            context=context,
            promotion_evidence=None,
        )

        self.assertFalse(match_rules(context, (spoof,)))

    def test_hostile_action_subclass_is_omitted_without_semantic_calls(self) -> None:
        context = derive_task_learning_context(self.task(), run_id=31)
        calls: list[str] = []

        class HostileAction(str):
            def __hash__(self) -> int:
                calls.append("hash")
                return hash("verification_replay")

            def __eq__(self, other: object) -> bool:
                calls.append("eq")
                return other == "verification_replay"

        unsafe = LearningRule(
            key="f" * 64,
            state=LearningRuleState.ACTIVE_CURRENT_TASK,
            source_kind=RetrospectiveSourceKind.RUN_OBSERVATION,
            root_cause=RootCauseKind.VERIFICATION_FAILURE,
            actions=(HostileAction("apply_patch"),),
            context=context,
        )

        self.assertFalse(match_rules(context, (unsafe,)))
        self.assertEqual([], calls)

    def test_unsafe_or_free_text_payload_values_are_rejected(self) -> None:
        for field, value in (
            ("summary", "free text\nnot allowed"),
            ("api_token", "not allowed"),
        ):
            payload = self._valid_payload()
            payload[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_rule_payload(payload)

        for value in ("x" * 257, "../outside", "app;rm"):
            payload = self._valid_payload()
            payload["match"]["allowed_path_prefixes"] = [value]
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_rule_payload(payload)

    def _valid_payload(self) -> dict[str, object]:
        context = derive_task_learning_context(self.task(), run_id=9)
        return build_current_task_rule(
            context,
            state=LearningRuleState.DRAFT,
        ).to_payload()

    @staticmethod
    def _stable_evidence() -> PromotionEvidence:
        return PromotionEvidence(
            task_keys=("task-a", "task-b", "task-c"),
            workspace_fingerprints=("work-a", "work-b"),
            counterexample_count=0,
        )


if __name__ == "__main__":
    unittest.main()
