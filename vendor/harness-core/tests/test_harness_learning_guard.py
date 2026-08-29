from __future__ import annotations

import unittest
from pathlib import Path

from app.harness_learning_guard import (
    build_learning_guard_payload,
    build_replan_decision,
    required_checks_for_root_cause,
)
from app.local_agent_contract import LocalAgentTask
from app.repair_learning import (
    LearningRule,
    MatchedLearningRule,
    RootCauseKind,
    build_current_task_rule,
    derive_task_learning_context,
)


class HarnessLearningGuardTests(unittest.TestCase):
    def task(self) -> LocalAgentTask:
        return LocalAgentTask(
            task_key="fixture-replan",
            project_path=Path("/safe/project"),
            request="修复医保退费链路。",
            allowed_paths=("app/refund.py",),
            verification_commands=(("python", "-m", "unittest"),),
            acceptance_criteria=("专项测试通过",),
            timeout_seconds=60,
            contract_hash="a" * 64,
            repository_root_identity=(1, 1),
            git_entry_identity=(1, 2),
            git_dir_identity=(1, 3),
            initial_head="b" * 40,
            allowed_path_parent_identities=((1, 4),),
            verification_executable_identities=((1, 5),),
        )

    def test_root_cause_always_has_reinspection_checks(self) -> None:
        for root_cause in RootCauseKind:
            checks = required_checks_for_root_cause(root_cause)
            with self.subTest(root_cause=root_cause):
                self.assertIn("reinspect_requirement_and_call_chain", checks)
                self.assertIn("replan_before_model_execution", checks)

    def test_learning_guard_is_structured_and_forbids_old_plan_replay(self) -> None:
        task = self.task()
        rule = build_current_task_rule(
            derive_task_learning_context(task, run_id=1),
            root_cause=RootCauseKind.IMPLEMENTATION_DEFECT,
            actions=("replan_before_execute",),
        )
        payload = build_learning_guard_payload(
            run_id=2,
            attempt_id=1,
            checks=(MatchedLearningRule(rule),),
        )

        self.assertEqual("his-harness-learning-guard.v1", payload["schema_version"])
        self.assertTrue(payload["must_replan"])
        self.assertTrue(payload["forbid_replaying_previous_decision"])
        self.assertEqual("implementation_defect", payload["guards"][0]["root_cause"])
        self.assertIn("replan_before_model_execution", payload["guards"][0]["required_checks"])

    def test_replan_increments_version_and_supersedes_previous_decision(self) -> None:
        task = self.task()
        first = build_replan_decision(
            task,
            run_id=2,
            attempt_id=1,
            previous_plan_version=0,
            failure_code="initial_execution",
            learning_guard=build_learning_guard_payload(run_id=2, attempt_id=1, checks=()),
        )
        second = build_replan_decision(
            task,
            run_id=2,
            attempt_id=2,
            previous_plan_version=first["plan_version"],
            failure_code="verification_failed",
            learning_guard=build_learning_guard_payload(run_id=2, attempt_id=2, checks=()),
        )

        self.assertEqual(1, first["plan_version"])
        self.assertEqual(2, second["plan_version"])
        self.assertEqual(1, second["supersedes_plan_version"])
        self.assertNotEqual(first["decision_sha256"], second["decision_sha256"])
        self.assertTrue(second["execute_only"])
        self.assertTrue(second["must_reinspect"])


if __name__ == "__main__":
    unittest.main()
