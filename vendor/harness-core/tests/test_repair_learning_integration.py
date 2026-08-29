"""Offline end-to-end coverage for bounded repair-learning behaviour.

The fixtures are disposable local Git repositories.  Worker and reviewer
implementations are deliberately fake: these tests validate control-plane
facts and must never invoke Codex, sandbox-exec, a remote, or a real project.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.codex_cli_worker import CodexWorkerResult
from app.local_agent_confirmation import LocalAgentConfirmationService
from app.local_agent_contract import load_local_agent_task
from app.local_agent_repository import LocalAgentRunRepository, _read_process_start_identity
from app.local_agent_review import LocalAgentReviewer, canonical_review_hash
from app.local_agent_runner import LocalAgentRunner
from app.repair_learning import LearningRuleState
from app.repair_learning_service import RepairLearningService
from app.runtime_policy import assert_local_agent_run_allowed


class _FixtureWorker:
    """A local fake that writes only the contract-approved fixture path."""

    def __init__(self) -> None:
        self.requests = []

    def start(self, request, sink):
        self.requests.append(request)
        identity = _read_process_start_identity(os.getpid())
        sink.on_started(os.getpid(), identity)
        (request.worktree_path / "calculator.py").write_text(
            "def add(left, right):\n    return left + right\n",
            encoding="utf-8",
        )
        return CodexWorkerResult(
            exit_code=0,
            error_code="",
            primary_error_code="",
            cleanup_error_code="",
            pid=os.getpid(),
            process_start_identity=identity,
            stdout_sha256="0" * 64,
            stderr_sha256="0" * 64,
            event_count=0,
            final_response=None,
            final_response_sha256="",
            final_response_validated=False,
            untrusted_final_response=True,
        )


class _FixtureReviewWorker:
    def __init__(self, *verdicts: str) -> None:
        self._verdicts = list(verdicts)
        self.requests = []

    def start(self, request, sink):
        self.requests.append(request)
        identity = _read_process_start_identity(os.getpid())
        sink.on_started(os.getpid(), identity)
        verdict = self._verdicts.pop(0) if self._verdicts else "approved"
        payload: dict[str, object] = {
            "schema_version": "his-local-agent-review.v1",
            "verdict": verdict,
            "findings": [] if verdict == "approved" else [{
                "severity": "important",
                "path": "calculator.py",
                "line": 1,
                "message": "Fixture review counterexample.",
            }],
            "summary": "Fixture review result.",
        }
        payload["review_hash"] = canonical_review_hash(payload)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        return CodexWorkerResult(
            exit_code=0,
            error_code="",
            primary_error_code="",
            cleanup_error_code="",
            pid=os.getpid(),
            process_start_identity=identity,
            stdout_sha256="0" * 64,
            stderr_sha256="0" * 64,
            event_count=0,
            final_response=payload,
            final_response_sha256=digest,
            final_response_validated=False,
            untrusted_final_response=True,
            canonical_final_response_sha256=digest,
        )


class RepairLearningOfflineIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(
            prefix="his_harness_repair_learning_integration_",
            dir="/private/tmp",
        )
        self.root = Path(self._tmp.name)
        self.database_path = self.root / "control" / "harness.sqlite"
        self.database_path.parent.mkdir(mode=0o700)
        self._previous_database_path = database.DB_PATH
        database.DB_PATH = self.database_path
        database.init_db()
        self.repository = LocalAgentRunRepository(self.database_path)
        self.worktree_root = Path(tempfile.mkdtemp(
            prefix="his_harness_stage_f_learning_integration_",
            dir="/private/tmp",
        ))
        self._project_paths: list[Path] = []
        self._sequence = 0

    def tearDown(self) -> None:
        for project in self._project_paths:
            subprocess.run(
                ["git", "worktree", "prune"], cwd=project,
                check=False, capture_output=True, text=True,
            )
        shutil.rmtree(self.worktree_root, ignore_errors=True)
        database.DB_PATH = self._previous_database_path
        self._tmp.cleanup()

    def _task(self, task_key: str, *, request: str = "Fix the bounded Python calculation."):
        self._sequence += 1
        project = self.root / f"project-{self._sequence}"
        project.mkdir()
        self._project_paths.append(project)
        self._git(project, "init", "-q")
        self._git(project, "config", "user.email", "fixture@example.invalid")
        self._git(project, "config", "user.name", "Offline Fixture")
        (project / "calculator.py").write_text(
            "def add(left, right):\n    return left - right\n", encoding="utf-8",
        )
        (project / "test_calculator.py").write_text(
            "import unittest\nfrom calculator import add\n\n"
            "class CalculatorTest(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(3, add(1, 2))\n",
            encoding="utf-8",
        )
        self._git(project, "add", ".")
        self._git(project, "commit", "-q", "-m", "fixture")
        contract = self.root / f"{task_key}.json"
        contract.write_text(json.dumps({
            "schema_version": "his-local-agent-task.v1",
            "task_key": task_key,
            "project_path": str(project),
            "request": request,
            "allowed_paths": ["calculator.py"],
            "verification_commands": [[sys.executable, "-m", "unittest", "-q", "test_calculator"]],
            "acceptance_criteria": ["The bounded fixture test passes."],
            "timeout_seconds": 30,
        }), encoding="utf-8")
        return load_local_agent_task(contract), project

    @staticmethod
    def _git(project: Path, *arguments: str) -> None:
        subprocess.run(
            ["git", *arguments], cwd=project, check=True,
            capture_output=True, text=True,
        )

    def _runner(self, worker: _FixtureWorker, reviewer_worker: _FixtureReviewWorker) -> LocalAgentRunner:
        reviewer = LocalAgentReviewer(
            repository=self.repository,
            worker=reviewer_worker,
            artifact_root=self.worktree_root,
        )
        return LocalAgentRunner(
            repository=self.repository,
            worker=worker,
            reviewer=reviewer,
            worktree_root=self.worktree_root,
        )

    def _preflight(self, label: str):
        return assert_local_agent_run_allowed(
            allow_real_agent=True,
            authorization_id=f"repair-learning-integration-{label}",
        )

    @staticmethod
    def _verification(returncode: int) -> dict[str, object]:
        return {
            "returncode": returncode,
            "timed_out": False,
            "cleanup": "not_needed",
            "duration_ms": 1,
            "stdout_sha256": "0" * 64,
            "stderr_sha256": ("0" if returncode == 0 else "1") * 64,
        }

    def _failure_then_trial(
        self,
        runner: LocalAgentRunner,
        task,
        *,
        label: str,
    ) -> dict[str, object]:
        with patch(
            "app.local_agent_runner.run_local_agent_verification_argv",
            side_effect=[self._verification(1), self._verification(0)],
        ):
            failed = runner.execute(task, self._preflight(label + "-failed"))
            self.assertEqual("failed_verification", failed["run"]["status"])
            return runner.retry(int(failed["run"]["id"]))

    def _successful_run(self, runner: LocalAgentRunner, task, *, label: str) -> dict[str, object]:
        with patch(
            "app.local_agent_runner.run_local_agent_verification_argv",
            return_value=self._verification(0),
        ):
            return runner.execute(task, self._preflight(label))

    def test_verification_failure_creates_active_rule_and_retry_injects_worker_and_reviewer_checks(self) -> None:
        worker = _FixtureWorker()
        reviewer_worker = _FixtureReviewWorker("approved")
        runner = self._runner(worker, reviewer_worker)
        task, _project = self._task("repair-integration-retry")

        completed = self._failure_then_trial(runner, task, label="retry")

        learning = RepairLearningService(self.repository).snapshot_for_run(
            int(completed["run"]["id"]),
        )
        self.assertEqual("awaiting_human_confirmation", completed["run"]["status"])
        self.assertEqual(LearningRuleState.TRIAL.value, learning["rules"][0]["state"])
        self.assertNotIn("FIXED_LEARNING_CHECKS_BEGIN", worker.requests[0].prompt)
        self.assertIn("FIXED_LEARNING_CHECKS_BEGIN", worker.requests[1].prompt)
        self.assertIn("verification_replay", worker.requests[1].prompt)
        self.assertIn("FIXED_LEARNING_REVIEW_FOCUS_BEGIN", reviewer_worker.requests[0].prompt)
        self.assertIn("verification_replay", reviewer_worker.requests[0].prompt)

    def test_three_tasks_two_workspaces_promote_then_counterexample_suspends_and_stops_injection(self) -> None:
        worker = _FixtureWorker()
        reviewer_worker = _FixtureReviewWorker("approved", "approved", "approved", "changes_requested", "approved")
        runner = self._runner(worker, reviewer_worker)
        task_a, _ = self._task("repair-integration-normal-a")
        task_b, _ = self._task("repair-integration-normal-b")
        task_c, _ = self._task("repair-integration-normal-c")

        first = self._failure_then_trial(runner, task_a, label="normal-a")
        second = self._successful_run(runner, task_b, label="normal-b")
        third = self._successful_run(runner, task_c, label="normal-c")
        rule = RepairLearningService(self.repository).snapshot_for_run(int(first["run"]["id"]))["rules"][0]
        self.assertEqual("awaiting_human_confirmation", second["run"]["status"])
        self.assertEqual("awaiting_human_confirmation", third["run"]["status"])
        self.assertEqual(LearningRuleState.STABLE.value, rule["state"])
        self.assertGreaterEqual(int(rule["verified_task_count"]), 3)
        self.assertGreaterEqual(int(rule["distinct_workspace_count"]), 2)

        counterexample_task, _ = self._task("repair-integration-counterexample")
        counterexample = self._successful_run(runner, counterexample_task, label="counterexample")
        self.assertEqual("changes_requested", counterexample["run"]["status"])
        counterexample_attempt = int(counterexample["attempts"][-1]["id"])
        RepairLearningService(self.repository).record_counterexample(
            task=counterexample_task,
            run_id=int(counterexample["run"]["id"]),
            attempt_id=counterexample_attempt,
            summary="Fixture rule counterexample.",
        )
        after_counterexample = RepairLearningService(self.repository).snapshot_for_run(int(first["run"]["id"]))
        self.assertEqual(LearningRuleState.SUSPENDED.value, after_counterexample["rules"][0]["state"])

        no_injection_task, _ = self._task("repair-integration-no-injection")
        final = self._successful_run(runner, no_injection_task, label="no-injection")
        self.assertEqual("awaiting_human_confirmation", final["run"]["status"])
        self.assertNotIn("FIXED_LEARNING_CHECKS_BEGIN", worker.requests[-1].prompt)
        self.assertNotIn("FIXED_LEARNING_REVIEW_FOCUS_BEGIN", reviewer_worker.requests[-1].prompt)

    def test_high_risk_amount_settlement_rule_stays_trial_after_three_tasks_and_two_workspaces(self) -> None:
        worker = _FixtureWorker()
        reviewer_worker = _FixtureReviewWorker("approved", "approved", "approved")
        runner = self._runner(worker, reviewer_worker)
        request = "修复收费结算金额校验的离线 Python fixture。"
        task_a, _ = self._task("repair-integration-risk-a", request=request)
        task_b, _ = self._task("repair-integration-risk-b", request=request)
        task_c, _ = self._task("repair-integration-risk-c", request=request)

        first = self._failure_then_trial(runner, task_a, label="risk-a")
        self._successful_run(runner, task_b, label="risk-b")
        self._successful_run(runner, task_c, label="risk-c")

        rule = RepairLearningService(self.repository).snapshot_for_run(int(first["run"]["id"]))["rules"][0]
        self.assertEqual(LearningRuleState.TRIAL.value, rule["state"])
        self.assertGreaterEqual(int(rule["verified_task_count"]), 3)
        self.assertGreaterEqual(int(rule["distinct_workspace_count"]), 2)

    def test_human_correction_blocks_confirmation_apply_and_leaves_source_unchanged(self) -> None:
        worker = _FixtureWorker()
        reviewer_worker = _FixtureReviewWorker("approved")
        runner = self._runner(worker, reviewer_worker)
        task, project = self._task("repair-integration-human-correction")
        awaiting = self._successful_run(runner, task, label="human-correction")
        run_id = int(awaiting["run"]["id"])
        confirmation = LocalAgentConfirmationService(
            repository=self.repository,
            artifact_root=self.worktree_root,
        ).issue_local_apply_confirmation(run_id, "local-user")

        corrected = runner.record_human_correction(
            run_id,
            root_cause_kind="implementation_defect",
            summary_sha256="sha256:" + hashlib.sha256(b"fixture correction").hexdigest(),
        )

        self.assertEqual("changes_requested", corrected["run"]["status"])
        with self.assertRaisesRegex(ValueError, "local_agent_confirmation_invalid"):
            LocalAgentConfirmationService(
                repository=self.repository,
                artifact_root=self.worktree_root,
            ).confirm_and_apply(run_id, confirmation.token, "local-user")
        self.assertIn("return left - right", (project / "calculator.py").read_text(encoding="utf-8"))
        self.assertEqual(1, int(subprocess.run(
            ["git", "rev-list", "--count", "HEAD"], cwd=project,
            check=True, capture_output=True, text=True,
        ).stdout.strip()))

    def test_human_correction_never_exposes_a_durable_correction_before_confirmation_is_blocked(self) -> None:
        """The record/invalidate boundary must not permit even a fake apply."""

        worker = _FixtureWorker()
        runner = self._runner(worker, _FixtureReviewWorker("approved"))
        task, project = self._task("repair-integration-correction-race")
        awaiting = self._successful_run(runner, task, label="correction-race")
        run_id = int(awaiting["run"]["id"])
        confirmation_service = LocalAgentConfirmationService(
            repository=self.repository,
            artifact_root=self.worktree_root,
        )
        confirmation = confirmation_service.issue_local_apply_confirmation(run_id, "local-user")
        original_record = runner._learning_service.record_human_correction
        apply_attempts: list[str] = []

        def reject_if_apply(*_args, **_kwargs):
            apply_attempts.append("reached")
            raise ValueError("fixture_apply_must_not_be_reached")

        def attempt_apply_after_durable_record(**kwargs):
            record = original_record(**kwargs)
            try:
                confirmation_service.confirm_and_apply(run_id, confirmation.token, "local-user")
            except ValueError:
                # The assertion after the controlled interleaving decides
                # whether this rejection occurred before source-apply entry.
                pass
            return record

        correction_error = None
        corrected = None
        with (
            patch.object(
                runner._learning_service,
                "record_human_correction",
                side_effect=attempt_apply_after_durable_record,
            ),
            patch.object(
                confirmation_service,
                "_execute_local_apply_operation",
                side_effect=reject_if_apply,
            ),
        ):
            try:
                corrected = runner.record_human_correction(
                    run_id,
                    root_cause_kind="implementation_defect",
                    summary_sha256="sha256:" + hashlib.sha256(b"race correction").hexdigest(),
                )
            except ValueError as error:
                correction_error = str(error)

        self.assertEqual([], apply_attempts)
        self.assertIsNone(correction_error)
        self.assertIsNotNone(corrected)
        assert corrected is not None
        self.assertEqual("changes_requested", corrected["run"]["status"])
        self.assertIn("return left - right", (project / "calculator.py").read_text(encoding="utf-8"))
