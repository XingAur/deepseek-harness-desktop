from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.clarification_gate import PatchReadinessResult
from app.evaluator import EvaluationResult
from app.harness import RequirementWorkflowRunner
from app.llm_client import MockLLMClient
from app.technical_decision import TechnicalDecisionResult
from app.worktree_executor import WorktreeExecutionResult


class ScopeConfirmationIntegrationTests(unittest.TestCase):
    def test_legacy_mutating_run_remains_blocked_after_scope_token_is_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            (project / "src").mkdir(parents=True)
            (project / "src/view.vue").write_text("<template />", encoding="utf-8")
            decision = TechnicalDecisionResult(
                project_root=str(root),
                selected_projects=[
                    {
                        "name": "demo",
                        "path": str(project),
                        "role": "frontend",
                        "exists": True,
                    }
                ],
                implementation_decision={
                    "can_patch": True,
                    "summary": "ok",
                    "blockers": [],
                },
                recommended_allowed_paths=["src/view.vue"],
                recommended_verify_commands=["test -f src/view.vue"],
            )
            calibration = {
                "status": "ready_for_development",
                "decision": {"can_enter_development": True},
                "resolved_parameters": [],
                "proposed_subtasks": [],
            }
            readiness = PatchReadinessResult(
                status="ready",
                can_patch=True,
                summary="ok",
                allowed_paths=["src/view.vue"],
                suggested_verify_commands=["test -f src/view.vue"],
            )
            with (
                patch.object(database, "DB_PATH", root / "harness.sqlite"),
                patch("app.harness.build_technical_decision", return_value=decision),
                patch("app.harness.build_requirement_calibration", return_value=calibration),
                patch.object(RequirementWorkflowRunner, "_build_evidence_bundle", return_value=None),
                patch("app.harness.evaluate_patch_readiness", return_value=readiness),
            ):
                runner = RequirementWorkflowRunner(MockLLMClient(), allow_mock=True)
                with (
                    patch.object(
                        runner.evaluator,
                        "evaluate",
                        return_value=EvaluationResult("pass", "ok"),
                    ),
                    patch.object(
                        RequirementWorkflowRunner,
                        "_run_worktree_execution",
                        return_value=WorktreeExecutionResult(status="success", summary="ok"),
                    ) as executor,
                    patch.object(
                        RequirementWorkflowRunner,
                        "_route_worktree_local_apply",
                        side_effect=lambda result, **_: result,
                    ),
                ):
                    options = {
                        "demand_text": "只读页面显示一个字段，不涉及收费、医保或结算。",
                        "title": "范围确认集成",
                        "project_path": project,
                        "project_root": root,
                        "execution_mode": "worktree",
                        "requirement_governance": "legacy",
                        "worktree_dir": root / "worktrees",
                    }
                    first = runner.run(**options)
                    self.assertEqual("blocked", first.status)
                    self.assertEqual(
                        "awaiting_pre_change_scope_confirmation",
                        first.evaluation_status,
                    )
                    self.assertEqual(0, executor.call_count)

                    artifacts = database.get_artifacts(first.run_id)
                    confirmation = json.loads(
                        next(
                            item["content"]
                            for item in reversed(artifacts)
                            if item["kind"] == "pre_change_confirmation_json"
                        )
                    )
                    self.assertEqual("pending", confirmation["status"])
                    self.assertTrue(confirmation["confirmation_token"].startswith("CONFIRM-SCOPE:"))

                    second = runner.run(
                        **options,
                        pre_change_confirmation=confirmation["confirmation_token"],
                    )
                    self.assertEqual("failed", second.status)
                    self.assertEqual("pass", second.evaluation_status)
                    self.assertIn("需求治理未闭合", second.markdown_report)
                    self.assertEqual(0, executor.call_count)


if __name__ == "__main__":
    unittest.main()
