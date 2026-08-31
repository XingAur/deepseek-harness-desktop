from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.flux_lite_learning import ReviewerOpinion
from app.local_agent_contract import load_local_agent_task
from app.local_agent_repository import LocalAgentRunRepository
from app.repair_learning import LearningRuleState, RootCauseKind
from app.repair_learning_service import RepairLearningService
from app.runtime_policy import assert_local_agent_run_allowed


class RepairLearningServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "harness.sqlite"
        self.sequence = 0

        def connection_factory() -> sqlite3.Connection:
            return database.connect_database(self.database_path)

        database.init_db(connection_factory=connection_factory)
        self.local_repository = LocalAgentRunRepository(
            self.database_path,
            connection_factory=connection_factory,
        )
        self.service = RepairLearningService(self.local_repository)
        self.task = self._task("repair-service-task-a", request="Fix a Python validation path.")

    def _task(self, task_key: str, *, request: str):
        self.sequence += 1
        project = self.root / f"project-{self.sequence}"
        project.mkdir()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        (project / "calculator.py").write_text("def add(left, right):\n    return left + right\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(project), "add", "calculator.py"], check=True)
        subprocess.run(
            [
                "git", "-C", str(project), "-c", "user.email=fixture@example.invalid",
                "-c", "user.name=Fixture", "commit", "--quiet", "-m", "fixture",
            ],
            check=True,
        )
        contract = self.root / f"{task_key}-{self.sequence}.json"
        contract.write_text(
            json.dumps(
                {
                    "schema_version": "his-local-agent-task.v1",
                    "task_key": task_key,
                    "project_path": str(project),
                    "request": request,
                    "allowed_paths": ["calculator.py"],
                    "verification_commands": [[sys.executable, "-m", "unittest", "-q"]],
                    "acceptance_criteria": ["Run the bounded verification."],
                    "timeout_seconds": 120,
                }
            ),
            encoding="utf-8",
        )
        return load_local_agent_task(contract)

    def _workspace_binding(self, *, workspace_id: int, run_id: int) -> dict[str, object]:
        root = f"/private/tmp/his_harness_stage_f_learning_{workspace_id}"
        worktree = root + f"/run_{run_id}"
        return {
            "worktree_path": worktree,
            "source_metadata": {},
            "source_worktrees": [],
            "worktree_identity": [17, workspace_id, 16_384],
            "worktree_git_identity": [17, workspace_id + 10_000, 32_768],
            "marker_path": root + "/.harness_worktree_markers/" + hashlib.sha256(worktree.encode("utf-8")).hexdigest() + ".json",
            "task_artifact": f".harness_local_agent_control/run_{run_id}/task.json",
            "task_sha256": "a" * 64,
        }

    def _run_at(
        self,
        task,
        status: str,
        *,
        workspace_id: int | None = None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        preflight = assert_local_agent_run_allowed(
            allow_real_agent=True,
            authorization_id=f"repair-learning-service-{self.sequence:03d}",
        )
        run = self.local_repository.consume_preflight(task, preflight)
        self.local_repository.bind_workspace(
            run["id"],
            self._workspace_binding(
                workspace_id=run["id"] if workspace_id is None else workspace_id,
                run_id=run["id"],
            ),
        )
        attempt = self.local_repository.start_attempt(run["id"])
        with patch(
            "app.local_agent_repository._read_process_start_identity",
            return_value="darwin-proc-bsdinfo-v1:1:2",
        ):
            self.local_repository.bind_worker_identity(
                attempt["id"], 12345, "darwin-proc-bsdinfo-v1:1:2",
            )
        self.local_repository.complete_attempt(attempt["id"], "completed")
        if status == "verifying":
            return run, attempt
        if status == "failed_verification":
            self.local_repository.transition(run["id"], "verifying", "failed_verification", {})
        else:
            self.local_repository.transition(run["id"], "verifying", "reviewing", {})
            if status == "changes_requested":
                self.local_repository.transition(run["id"], "reviewing", "changes_requested", {})
            elif status == "awaiting_human_confirmation":
                self.local_repository.transition(run["id"], "reviewing", "awaiting_human_confirmation", {})
            else:
                raise AssertionError(f"unsupported fixture status: {status}")
        return run, attempt

    def _retry_to_awaiting(self, run: dict[str, object]) -> dict[str, object]:
        attempt = self.local_repository.start_attempt(run["id"])
        with patch(
            "app.local_agent_repository._read_process_start_identity",
            return_value="darwin-proc-bsdinfo-v1:1:2",
        ):
            self.local_repository.bind_worker_identity(
                attempt["id"], 12345, "darwin-proc-bsdinfo-v1:1:2",
            )
        self.local_repository.complete_attempt(attempt["id"], "completed")
        self.local_repository.transition(run["id"], "verifying", "reviewing", {})
        self.local_repository.transition(run["id"], "reviewing", "awaiting_human_confirmation", {})
        return attempt

    def test_three_structured_sources_are_replay_safe_and_have_distinct_stable_keys(self) -> None:
        verification_run, verification_attempt = self._run_at(self.task, "failed_verification")
        review_task = self._task("repair-service-task-review", request="Fix a Python validation path.")
        review_run, review_attempt = self._run_at(review_task, "changes_requested")
        human_task = self._task("repair-service-task-human", request="Fix a Python validation path.")
        human_run, human_attempt = self._run_at(human_task, "changes_requested")

        verification = self.service.record_verification_failure(
            task=self.task, run_id=verification_run["id"], attempt_id=verification_attempt["id"], summary="assertion failed",
        )
        replayed = self.service.record_verification_failure(
            task=self.task, run_id=verification_run["id"], attempt_id=verification_attempt["id"], summary="assertion failed",
        )
        review = self.service.record_reviewer_changes_requested(
            task=review_task, run_id=review_run["id"], attempt_id=review_attempt["id"], summary="missing null check",
        )
        human = self.service.record_human_correction(
            task=human_task,
            run_id=human_run["id"],
            attempt_id=human_attempt["id"],
            root_cause_kind=RootCauseKind.CONTRACT_MISMATCH,
            summary="scope correction",
        )

        self.assertEqual(verification.retrospective["id"], replayed.retrospective["id"])
        self.assertEqual(verification.rule.key, replayed.rule.key)
        self.assertEqual(3, len({
            verification.retrospective["source_key"],
            review.retrospective["source_key"],
            human.retrospective["source_key"],
        }))
        self.assertEqual("run_observation", verification.retrospective["source_kind"])
        self.assertEqual("review_observation", review.retrospective["source_kind"])
        self.assertEqual("offline_import", human.retrospective["source_kind"])
        self.assertEqual(1, len(self.service.snapshot_for_run(verification_run["id"])["rules"]))

    def test_human_correction_is_a_cross_run_no_repeat_guard(self) -> None:
        corrected_task = self._task(
            "repair-service-task-human-guard-source",
            request="Fix a Python validation path.",
        )
        corrected_run, corrected_attempt = self._run_at(corrected_task, "changes_requested")
        self.service.record_human_correction(
            task=corrected_task,
            run_id=corrected_run["id"],
            attempt_id=corrected_attempt["id"],
            root_cause_kind=RootCauseKind.CONTRACT_MISMATCH,
            summary="the previous plan used the wrong contract boundary",
        )

        next_task = self._task(
            "repair-service-task-human-guard-next",
            request="Fix a Python validation path.",
        )
        next_run, _ = self._run_at(next_task, "verifying")
        matched = self.service.matched_checks_for_attempt(
            next_task, run_id=next_run["id"],
        )

        self.assertTrue(any(
            item.rule.root_cause is RootCauseKind.CONTRACT_MISMATCH
            and "replan_before_execute" in item.rule.actions
            for item in matched
        ))

    def test_safe_summary_and_artifact_never_keep_raw_unified_diff_without_diff_git_marker(self) -> None:
        run, attempt = self._run_at(self.task, "failed_verification")
        raw_diff = (
            "Bearer deliberately-secret-token\n"
            "prompt: do not persist this\n"
            "verification failed\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old_value\n+new_value"
        )
        record = self.service.record_verification_failure(
            task=self.task,
            run_id=run["id"],
            attempt_id=attempt["id"],
            summary=raw_diff,
        )
        encoded = json.dumps(record.retrospective["safe_summary"], ensure_ascii=False)
        artifact = record.artifact.content.decode("utf-8")

        for raw_line in (
            "deliberately-secret-token", "prompt: do not persist this",
            "--- a/x.py", "+++ b/x.py", "@@ -1 +1 @@", "-old_value", "+new_value",
        ):
            self.assertNotIn(raw_line, encoded)
            self.assertNotIn(raw_line, artifact)
        with self.local_repository.open_learning_connection() as connection:
            persisted = connection.execute(
                "select safe_summary_json from repair_retrospectives"
            ).fetchone()
        self.assertIsNotNone(persisted)
        self.assertNotIn("-old_value", persisted["safe_summary_json"])
        self.assertEqual("redacted", record.retrospective["safe_summary"]["summary_status"])
        self.assertEqual("his-repair-retrospective.v1", record.artifact.payload["schema_version"])
        self.assertEqual(hashlib.sha256(record.artifact.content).hexdigest(), record.artifact.sha256)

    def test_safe_summary_never_persists_whitespace_prefixed_patch_markers(self) -> None:
        cases = (
            (
                "unified",
                "  --- a/secret.py\n\t+++ b/secret.py\n \t@@ -1 +1 @@\n-raw_old_value\n+raw_new_value",
            ),
            ("hunk", "\t@@ -12,2 +12,2 @@\n-raw_old_value\n+raw_new_value"),
            ("binary", "  GIT binary patch\nliteral 3\nraw_binary_payload"),
        )
        for index, (_kind, raw_patch) in enumerate(cases, start=1):
            with self.subTest(kind=_kind):
                task = self._task(
                    f"repair-service-space-diff-{index}",
                    request="Fix a Python validation path.",
                )
                run, attempt = self._run_at(task, "failed_verification")
                record = self.service.record_verification_failure(
                    task=task,
                    run_id=run["id"],
                    attempt_id=attempt["id"],
                    summary=raw_patch,
                )
                artifact = record.artifact.content.decode("utf-8")
                encoded = json.dumps(record.retrospective["safe_summary"], ensure_ascii=False)
                self.assertEqual("redacted", record.retrospective["safe_summary"]["summary_status"])
                self.assertNotIn(raw_patch, encoded)
                self.assertNotIn(raw_patch, artifact)
                with self.local_repository.open_learning_connection() as connection:
                    persisted = connection.execute(
                        "select safe_summary_json from repair_retrospectives where run_id=?",
                        (run["id"],),
                    ).fetchone()
                self.assertNotIn(raw_patch, persisted["safe_summary_json"])

    def test_safe_summary_never_persists_unicode_whitespace_prefixed_unified_diff(self) -> None:
        prefixes = ("\u00a0", "\u2003", "\f", "\v")
        for index, prefix in enumerate(prefixes, start=1):
            with self.subTest(prefix=ascii(prefix)):
                task = self._task(
                    f"repair-service-unicode-diff-{index}",
                    request="Fix a Python validation path.",
                )
                run, attempt = self._run_at(task, "failed_verification")
                raw_patch = f"{prefix}--- a/secret.py\n{prefix}+++ b/secret.py\n-raw_old\n+raw_new"
                record = self.service.record_verification_failure(
                    task=task,
                    run_id=run["id"],
                    attempt_id=attempt["id"],
                    summary=raw_patch,
                )
                artifact = record.artifact.content.decode("utf-8")
                encoded = json.dumps(record.retrospective["safe_summary"], ensure_ascii=False)
                self.assertEqual("redacted", record.retrospective["safe_summary"]["summary_status"])
                self.assertNotIn(raw_patch, encoded)
                self.assertNotIn(raw_patch, artifact)
                with self.local_repository.open_learning_connection() as connection:
                    persisted = connection.execute(
                        "select safe_summary_json from repair_retrospectives where run_id=?",
                        (run["id"],),
                    ).fetchone()
                self.assertNotIn(raw_patch, persisted["safe_summary_json"])

    def test_safe_summary_never_persists_common_standalone_tokens(self) -> None:
        tokens = (
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
            "gho_" + "A" * 36,
            "ghu_" + "A" * 36,
            "ghs_" + "A" * 36,
            "ghr_" + "A" * 36,
            "github_pat_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890abcdefghijklmnopqrstuvwxyz",
            "glpat-" + "A" * 20,
            "xoxb-test-placeholder",
            "xapp-" + "A" * 20,
            "ASIAABCDEFGHIJKLMNOP",
        )
        for index, token in enumerate(tokens, start=1):
            with self.subTest(token=token[:8]):
                task = self._task(f"repair-service-tok-{index}", request="Fix a Python validation path.")
                run, attempt = self._run_at(task, "failed_verification")
                record = self.service.record_verification_failure(
                    task=task,
                    run_id=run["id"],
                    attempt_id=attempt["id"],
                    summary=f"verification failed {token}",
                )
                artifact = record.artifact.content.decode("utf-8")
                encoded = json.dumps(record.retrospective["safe_summary"], ensure_ascii=False)
                self.assertNotIn(token, artifact)
                self.assertNotIn(token, encoded)
                with self.local_repository.open_learning_connection() as connection:
                    persisted = connection.execute(
                        "select safe_summary_json from repair_retrospectives where run_id=?", (run["id"],),
                    ).fetchone()
                self.assertNotIn(token, persisted["safe_summary_json"])

    def test_safe_summary_never_persists_unicode_prefixed_standalone_tokens(self) -> None:
        tokens = (
            "ghp_" + "A" * 36,
            "gho_" + "A" * 36,
            "ghu_" + "A" * 36,
            "ghs_" + "A" * 36,
            "ghr_" + "A" * 36,
            "github_pat_" + "A" * 20,
            "glpat-" + "A" * 20,
            "xoxb-" + "A" * 20,
            "xapp-" + "A" * 20,
            "AKIAABCDEFGHIJKLMNOP",
            "ASIAABCDEFGHIJKLMNOP",
            "sk-" + "A" * 8,
        )
        for index, token in enumerate(tokens, start=1):
            with self.subTest(token=token[:8]):
                task = self._task(f"repair-service-unicode-tok-{index}", request="Fix a Python validation path.")
                run, attempt = self._run_at(task, "failed_verification")
                raw_summary = "中文" + token
                record = self.service.record_verification_failure(
                    task=task,
                    run_id=run["id"],
                    attempt_id=attempt["id"],
                    summary=raw_summary,
                )
                artifact = record.artifact.content.decode("utf-8")

                self.assertEqual("redacted", record.retrospective["safe_summary"]["summary_status"])
                self.assertNotIn(token, artifact)
                with self.local_repository.open_learning_connection() as connection:
                    persisted = connection.execute(
                        "select safe_summary_json from repair_retrospectives where run_id=?", (run["id"],),
                    ).fetchone()
                self.assertNotIn(token, persisted["safe_summary_json"])

        task = self._task("repair-service-ordinary-chinese", request="Fix a Python validation path.")
        run, attempt = self._run_at(task, "failed_verification")
        record = self.service.record_verification_failure(
            task=task,
            run_id=run["id"],
            attempt_id=attempt["id"],
            summary="中文普通验证摘要",
        )
        self.assertEqual(
            {"summary_status": "safe", "summary": "中文普通验证摘要"},
            record.retrospective["safe_summary"],
        )

    def test_unbound_or_corrupt_workspace_cannot_be_used_as_learning_evidence(self) -> None:
        preflight = assert_local_agent_run_allowed(
            allow_real_agent=True,
            authorization_id="repair-learning-unbound-workspace-001",
        )
        unbound = self.local_repository.consume_preflight(self.task, preflight)
        self.local_repository.transition(unbound["id"], "created", "workspace_ready", {})
        attempt = self.local_repository.start_attempt(unbound["id"])
        with patch(
            "app.local_agent_repository._read_process_start_identity",
            return_value="darwin-proc-bsdinfo-v1:1:2",
        ):
            self.local_repository.bind_worker_identity(
                attempt["id"], 12345, "darwin-proc-bsdinfo-v1:1:2",
            )
        self.local_repository.complete_attempt(attempt["id"], "completed")
        self.local_repository.transition(unbound["id"], "verifying", "failed_verification", {})

        with self.assertRaises(ValueError):
            self.service.record_verification_failure(
                task=self.task, run_id=unbound["id"], attempt_id=attempt["id"], summary="no workspace binding",
            )
        self.assertEqual([], self.service.snapshot_for_run(unbound["id"])["retrospectives"])

    def test_same_durable_workspace_across_three_runs_cannot_promote_to_stable(self) -> None:
        source_run, source_attempt = self._run_at(self.task, "failed_verification", workspace_id=77)
        self.service.record_verification_failure(
            task=self.task, run_id=source_run["id"], attempt_id=source_attempt["id"], summary="test failed",
        )
        source_current = self._retry_to_awaiting(source_run)
        task_b = self._task("repair-service-samews-b", request="Fix a Python validation path.")
        run_b, attempt_b = self._run_at(task_b, "awaiting_human_confirmation", workspace_id=77)
        task_c = self._task("repair-service-samews-c", request="Fix a Python validation path.")
        run_c, attempt_c = self._run_at(task_c, "awaiting_human_confirmation", workspace_id=77)

        for task, run, attempt in (
            (self.task, source_run, source_current),
            (task_b, run_b, attempt_b),
            (task_c, run_c, attempt_c),
        ):
            state = self.service.record_successful_observation(
                task=task, run_id=run["id"], attempt_id=attempt["id"],
            )

        self.assertEqual((LearningRuleState.TRIAL,), tuple(item.state for item in state))
        record = self.service.snapshot_for_run(run_c["id"])["rules"][0]
        self.assertEqual(1, record["distinct_workspace_count"])

    def test_retry_started_before_atomic_learning_write_rejects_stale_attempt_without_record(self) -> None:
        run, attempt = self._run_at(self.task, "failed_verification")
        original = self.local_repository.record_learning_retrospective_with_rule

        def start_retry_then_write(*args, **kwargs):
            self.local_repository.start_attempt(run["id"])
            return original(*args, **kwargs)

        with patch.object(
            self.local_repository,
            "record_learning_retrospective_with_rule",
            side_effect=start_retry_then_write,
        ):
            with self.assertRaises(ValueError):
                self.service.record_verification_failure(
                    task=self.task, run_id=run["id"], attempt_id=attempt["id"], summary="race must fail closed",
                )

        self.assertEqual([], self.service.snapshot_for_run(run["id"])["retrospectives"])

    def test_learning_writes_require_current_durable_run_attempt_and_contract(self) -> None:
        run, first_attempt = self._run_at(self.task, "failed_verification")
        other_task = self._task("repair-service-task-other", request="Fix a Python validation path.")
        other_run, other_attempt = self._run_at(other_task, "failed_verification")
        current_attempt = self._retry_to_awaiting(run)
        stale_task = dataclasses.replace(self.task, contract_hash="0" * 64)

        for task, run_id, attempt_id in (
            (self.task, other_run["id"], other_attempt["id"]),
            (other_task, run["id"], first_attempt["id"]),
            (self.task, run["id"], other_attempt["id"]),
            (self.task, run["id"], first_attempt["id"]),
            (stale_task, run["id"], current_attempt["id"]),
        ):
            with self.subTest(task_key=task.task_key, run_id=run_id, attempt_id=attempt_id):
                with self.assertRaises(ValueError):
                    self.service.record_human_correction(
                        task=task,
                        run_id=run_id,
                        attempt_id=attempt_id,
                        root_cause_kind=RootCauseKind.CONTRACT_MISMATCH,
                        summary="identity must be durable",
                    )

        self.assertEqual([], self.service.snapshot_for_run(run["id"])["retrospectives"])
        self.assertEqual([], self.service.snapshot_for_run(other_run["id"])["retrospectives"])

    def test_current_task_rule_matches_a_retry_and_success_advances_to_trial(self) -> None:
        run, first_attempt = self._run_at(self.task, "failed_verification")
        record = self.service.record_verification_failure(
            task=self.task, run_id=run["id"], attempt_id=first_attempt["id"], summary="test failed",
        )
        current_attempt = self._retry_to_awaiting(run)

        matched = self.service.matched_checks_for_attempt(self.task, run_id=run["id"])
        advanced = self.service.record_successful_observation(
            task=self.task,
            run_id=run["id"],
            attempt_id=current_attempt["id"],
        )

        self.assertEqual((record.rule.key,), tuple(item.key for item in matched))
        self.assertEqual((LearningRuleState.TRIAL,), tuple(item.state for item in advanced))
        durable = self.local_repository.read_learning_binding(
            self.task, run_id=run["id"], attempt_id=current_attempt["id"],
        )
        self.assertEqual(
            durable["workspace_fingerprint"],
            self.service.snapshot_for_run(run["id"])["observations"][0]["workspace_fingerprint"],
        )

    def test_flux_lite_consensus_is_added_without_changing_legacy_learning_rules(self) -> None:
        run, attempt = self._run_at(self.task, "changes_requested")
        opinions = tuple(
            ReviewerOpinion(
                reviewer_id=reviewer_id,
                scope_key="python:repair-service-task-a",
                root_cause="verification_failure",
                focus_actions=("verification_replay",),
                verdict="changes_requested",
                evidence_refs=("sha256:" + hashlib.sha256(reviewer_id.encode()).hexdigest(),),
            )
            for reviewer_id in ("reviewer-a", "reviewer-b")
        )

        self.service.record_reviewer_opinions(
            task=self.task,
            run_id=run["id"],
            attempt_id=attempt["id"],
            opinions=opinions,
        )

        matched = self.service.matched_checks_for_attempt(self.task, run_id=run["id"])
        self.assertEqual(("verification_replay",), matched[-1].rule.actions)
        self.assertEqual([], self.service.snapshot_for_run(run["id"])["retrospectives"])

    def test_successful_observation_rejects_a_forged_workspace_fingerprint(self) -> None:
        run, first_attempt = self._run_at(self.task, "failed_verification")
        self.service.record_verification_failure(
            task=self.task, run_id=run["id"], attempt_id=first_attempt["id"], summary="test failed",
        )
        current_attempt = self._retry_to_awaiting(run)

        with self.assertRaisesRegex(ValueError, "repair_learning_input_invalid"):
            self.service.record_successful_observation(
                task=self.task,
                run_id=run["id"],
                attempt_id=current_attempt["id"],
                workspace_fingerprint="forged_workspace",
            )

        self.assertEqual([], self.service.snapshot_for_run(run["id"])["observations"])

    def test_three_tasks_and_two_workspaces_promote_normal_rule_but_not_high_risk_rule(self) -> None:
        normal_run, normal_attempt = self._run_at(self.task, "failed_verification")
        self.service.record_verification_failure(
            task=self.task, run_id=normal_run["id"], attempt_id=normal_attempt["id"], summary="test failed",
        )
        normal_current = self._retry_to_awaiting(normal_run)
        high_risk_task = self._task("repair-service-task-risk", request="修复医保结算金额检查")
        risk_run, risk_attempt = self._run_at(high_risk_task, "failed_verification")
        self.service.record_verification_failure(
            task=high_risk_task, run_id=risk_run["id"], attempt_id=risk_attempt["id"], summary="test failed",
        )
        risk_current = self._retry_to_awaiting(risk_run)
        normal_task_b = self._task("repair-service-task-b", request="Fix a Python validation path.")
        normal_run_b, normal_attempt_b = self._run_at(normal_task_b, "awaiting_human_confirmation")
        normal_task_c = self._task("repair-service-task-c", request="Fix a Python validation path.")
        normal_run_c, normal_attempt_c = self._run_at(normal_task_c, "awaiting_human_confirmation")
        risk_task_b = self._task("repair-service-task-risk-b", request="修复医保结算金额检查")
        risk_run_b, risk_attempt_b = self._run_at(risk_task_b, "awaiting_human_confirmation")
        risk_task_c = self._task("repair-service-task-risk-c", request="修复医保结算金额检查")
        risk_run_c, risk_attempt_c = self._run_at(risk_task_c, "awaiting_human_confirmation")

        normal_updates = ()
        for task, run, attempt in (
            (self.task, normal_run, normal_current),
            (normal_task_b, normal_run_b, normal_attempt_b),
            (normal_task_c, normal_run_c, normal_attempt_c),
        ):
            normal_updates = self.service.record_successful_observation(
                task=task, run_id=run["id"], attempt_id=attempt["id"],
            )
        high_risk_updates = ()
        for task, run, attempt in (
            (high_risk_task, risk_run, risk_current),
            (risk_task_b, risk_run_b, risk_attempt_b),
            (risk_task_c, risk_run_c, risk_attempt_c),
        ):
            high_risk_updates = self.service.record_successful_observation(
                task=task, run_id=run["id"], attempt_id=attempt["id"],
            )

        self.assertEqual((LearningRuleState.STABLE,), tuple(item.state for item in normal_updates))
        self.assertEqual((LearningRuleState.TRIAL,), tuple(item.state for item in high_risk_updates))
        self.assertGreaterEqual(self.service.snapshot_for_run(normal_run_c["id"])["rules"][0]["distinct_workspace_count"], 2)

    def test_counterexample_immediately_suspends_only_matched_rule(self) -> None:
        run, attempt = self._run_at(self.task, "changes_requested")
        self.service.record_reviewer_changes_requested(
            task=self.task, run_id=run["id"], attempt_id=attempt["id"], summary="review finding",
        )
        self.service.record_counterexample(
            task=self.task,
            run_id=run["id"],
            attempt_id=attempt["id"],
            summary="rule did not apply",
        )

        self.assertEqual((), self.service.matched_checks_for_attempt(self.task, run_id=run["id"]))
        self.assertEqual("suspended", self.service.snapshot_for_run(run["id"])["rules"][0]["state"])

    def test_replay_recovers_after_atomic_rule_write_failure_without_partial_retrospective(self) -> None:
        run, attempt = self._run_at(self.task, "failed_verification")
        with database.connect_database(self.database_path) as connection:
            connection.execute(
                """create trigger task5_replay_rule_insert_failure
                   before insert on repair_learning_rules
                   begin select raise(abort, 'fixture failure'); end"""
            )
        try:
            with self.assertRaisesRegex(ValueError, "repair_learning_storage_invalid"):
                self.service.record_verification_failure(
                    task=self.task, run_id=run["id"], attempt_id=attempt["id"], summary="replayable failure",
                )
        finally:
            with database.connect_database(self.database_path) as connection:
                connection.execute("drop trigger task5_replay_rule_insert_failure")
        self.assertEqual(0, len(self.service.snapshot_for_run(run["id"])["retrospectives"]))

        recovered = self.service.record_verification_failure(
            task=self.task, run_id=run["id"], attempt_id=attempt["id"], summary="replayable failure",
        )

        snapshot = self.service.snapshot_for_run(run["id"])
        self.assertEqual(1, len(snapshot["retrospectives"]))
        self.assertEqual(1, len(snapshot["rules"]))
        self.assertEqual(snapshot["retrospectives"][0]["id"], recovered.retrospective["id"])

    def test_invalid_human_root_cause_fails_closed_without_creating_records(self) -> None:
        run, attempt = self._run_at(self.task, "awaiting_human_confirmation")
        with self.assertRaisesRegex(ValueError, "repair_learning_input_invalid"):
            self.service.record_human_correction(
                task=self.task,
                run_id=run["id"],
                attempt_id=attempt["id"],
                root_cause_kind="model_guess",
                summary="not allowed",
            )
        self.assertEqual([], self.service.snapshot_for_run(run["id"])["retrospectives"])

    def test_public_human_correction_rejects_awaiting_without_mutating_issued_confirmation(self) -> None:
        run, attempt = self._run_at(self.task, "awaiting_human_confirmation")
        with database.connect_database(self.database_path) as connection:
            connection.execute(
                """insert into local_agent_apply_confirmations(
                       run_id, attempt_id, token_hash, requested_by, binding_json,
                       issued_at, expires_at, status
                   ) values(?, ?, ?, 'fixture-user', '{}', ?, ?, 'issued')""",
                (
                    run["id"], attempt["id"], "sha256:" + "e" * 64,
                    database.now_iso(), database.now_iso(),
                ),
            )
        before = self.local_repository.snapshot(run["id"])

        with self.assertRaisesRegex(ValueError, "repair_learning_input_invalid"):
            self.service.record_human_correction(
                task=self.task,
                run_id=run["id"],
                attempt_id=attempt["id"],
                root_cause_kind=RootCauseKind.IMPLEMENTATION_DEFECT,
                summary="direct service correction must not bypass confirmation",
            )

        after = self.local_repository.snapshot(run["id"])
        self.assertEqual(before["run"], after["run"])
        self.assertEqual(before["events"], after["events"])
        self.assertEqual([], self.service.snapshot_for_run(run["id"])["retrospectives"])
        with database.connect_database(self.database_path) as connection:
            self.assertEqual(
                "issued",
                connection.execute(
                    "select status from local_agent_apply_confirmations where run_id=?",
                    (run["id"],),
                ).fetchone()[0],
            )

    def test_rule_write_failure_rolls_back_retrospective_and_replay_is_idempotent(self) -> None:
        run, attempt = self._run_at(self.task, "failed_verification")
        with database.connect_database(self.database_path) as connection:
            connection.execute(
                """create trigger task5_fail_learning_rule_insert
                   before insert on repair_learning_rules
                   begin
                     select raise(abort, 'task5 forced rule write failure');
                   end"""
            )
        try:
            with self.assertRaises(Exception):
                self.service.record_verification_failure(
                    task=self.task,
                    run_id=run["id"],
                    attempt_id=attempt["id"],
                    summary="the rule insert must share the retrospective transaction",
                )
        finally:
            with database.connect_database(self.database_path) as connection:
                connection.execute("drop trigger task5_fail_learning_rule_insert")

        failed_snapshot = self.service.snapshot_for_run(run["id"])
        self.assertEqual([], failed_snapshot["retrospectives"])
        self.assertEqual([], failed_snapshot["rules"])

        first = self.service.record_verification_failure(
            task=self.task,
            run_id=run["id"],
            attempt_id=attempt["id"],
            summary="the rule insert must share the retrospective transaction",
        )
        replayed = self.service.record_verification_failure(
            task=self.task,
            run_id=run["id"],
            attempt_id=attempt["id"],
            summary="the rule insert must share the retrospective transaction",
        )
        recovered_snapshot = self.service.snapshot_for_run(run["id"])
        self.assertEqual(first.retrospective["id"], replayed.retrospective["id"])
        self.assertEqual(first.rule.key, replayed.rule.key)
        self.assertEqual(1, len(recovered_snapshot["retrospectives"]))
        self.assertEqual(1, len(recovered_snapshot["rules"]))

    def test_human_correction_implementation_defect_on_failed_verification_keeps_run_status(self) -> None:
        run, attempt = self._run_at(self.task, "failed_verification")

        record = self.service.record_human_correction(
            task=self.task,
            run_id=run["id"],
            attempt_id=attempt["id"],
            root_cause_kind=RootCauseKind.IMPLEMENTATION_DEFECT,
            summary="human found implementation gap",
        )

        self.assertEqual("implementation_defect", record.retrospective["root_cause_kind"])
        self.assertEqual(
            "failed_verification",
            self.local_repository.snapshot(run["id"])["run"]["status"],
        )


if __name__ == "__main__":
    unittest.main()
