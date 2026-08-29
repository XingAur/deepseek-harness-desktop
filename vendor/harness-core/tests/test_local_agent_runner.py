from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.codex_cli_worker import CodexWorkerResult, ProtocolRejectionAudit
from app.flux_lite_service import FluxLiteExperienceService
from app.local_agent_confirmation import LocalAgentConfirmationService
from app.local_agent_contract import load_local_agent_task
from app.local_agent_repository import LocalAgentRunRepository, _read_process_start_identity
from app.local_agent_review import LocalAgentReviewer, canonical_review_hash
from app.repair_learning import LearningRuleState, MatchedLearningRule, build_current_task_rule, derive_task_learning_context
from app.repair_learning_service import RepairLearningService
from app.runtime_policy import assert_local_agent_run_allowed


class _FixtureWorker:
    def __init__(self, writes: dict[str, str], *, error_code: str = "") -> None:
        self.writes = writes
        self.error_code = error_code
        self.requests = []

    def start(self, request, sink):
        self.requests.append(request)
        sink.on_started(os.getpid(), _read_process_start_identity(os.getpid()))
        for relative_path, content in self.writes.items():
            (request.worktree_path / relative_path).write_text(content, encoding="utf-8")
        return CodexWorkerResult(
            exit_code=0 if not self.error_code else 1,
            error_code=self.error_code,
            primary_error_code=self.error_code,
            cleanup_error_code="",
            pid=os.getpid(),
            process_start_identity=_read_process_start_identity(os.getpid()),
            stdout_sha256="0" * 64,
            stderr_sha256="0" * 64,
            event_count=0,
            final_response=None,
            final_response_sha256="",
            final_response_validated=False,
            untrusted_final_response=True,
        )


class _RetryWorker(_FixtureWorker):
    def __init__(self) -> None:
        super().__init__({})
        self.calls = 0

    def start(self, request, sink):
        self.calls += 1
        if self.calls == 1:
            return super().start(request, sink)
        self.writes = {"calculator.py": "def add(a, b):\n    return a + b\n"}
        return super().start(request, sink)


class _EventWorker(_FixtureWorker):
    def start(self, request, sink):
        self.requests.append(request)
        identity = _read_process_start_identity(os.getpid())
        sink.on_started(os.getpid(), identity)
        sink.on_event({
            "type": "thread.started",
            "thread_id": "019c9d85-1d4c-7123-8f2a-123456789abc",
            "sequence_no": 1,
            "raw_line_sha256": "1234567890abcdef" * 4,
        })
        for relative_path, content in self.writes.items():
            (request.worktree_path / relative_path).write_text(content, encoding="utf-8")
        return CodexWorkerResult(
            0, "", "", "", os.getpid(), identity,
            "0" * 64, "0" * 64, 1, None, "", False, True,
        )


class _StagedWorker(_FixtureWorker):
    def start(self, request, sink):
        result = super().start(request, sink)
        subprocess.run(["git", "add", "calculator.py"], cwd=request.worktree_path, check=True)
        return result


class _StagedNewWorker(_FixtureWorker):
    def start(self, request, sink):
        result = super().start(request, sink)
        subprocess.run(["git", "add", "generated.py"], cwd=request.worktree_path, check=True)
        return result


class _GitlinkWorker(_FixtureWorker):
    def start(self, request, sink):
        self.requests.append(request)
        sink.on_started(os.getpid(), _read_process_start_identity(os.getpid()))
        oid = subprocess.run(["git", "rev-parse", "HEAD"], cwd=request.worktree_path, text=True, capture_output=True, check=True).stdout.strip()
        subprocess.run(["git", "update-index", "--add", "--cacheinfo", f"160000,{oid},module"], cwd=request.worktree_path, check=True)
        return CodexWorkerResult(0, "", "", "", os.getpid(), _read_process_start_identity(os.getpid()), "0" * 64, "0" * 64, 0, None, "", False, True)


class _ApprovedReviewWorker:
    def __init__(self) -> None:
        self.requests = []

    def start(self, request, sink):
        self.requests.append(request)
        sink.on_started(os.getpid(), _read_process_start_identity(os.getpid()))
        payload = {
            "schema_version": "his-local-agent-review.v1",
            "verdict": "approved",
            "findings": [],
            "summary": "No blocking findings.",
        }
        payload["review_hash"] = canonical_review_hash(payload)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        import hashlib

        digest = hashlib.sha256(encoded).hexdigest()
        return CodexWorkerResult(0, "", "", "", os.getpid(), _read_process_start_identity(os.getpid()), "0" * 64, "0" * 64, 0, payload, digest, False, True, digest)


class _ChangesRequestedReviewWorker(_ApprovedReviewWorker):
    def start(self, request, sink):
        self.requests.append(request)
        sink.on_started(os.getpid(), _read_process_start_identity(os.getpid()))
        payload = {
            "schema_version": "his-local-agent-review.v1",
            "verdict": "changes_requested",
            "findings": [{
                "severity": "important", "path": "calculator.py", "line": 1,
                "message": "Bounded fixture finding.",
            }],
            "summary": "Fixture requires a bounded change.",
        }
        payload["review_hash"] = canonical_review_hash(payload)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        import hashlib

        digest = hashlib.sha256(encoded).hexdigest()
        return CodexWorkerResult(0, "", "", "", os.getpid(), _read_process_start_identity(os.getpid()), "0" * 64, "0" * 64, 0, payload, digest, False, True, digest)


class _LearningFailureService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def matched_checks_for_attempt(self, task, *, run_id):
        self.calls.append("matched")
        return ()

    def record_verification_failure(self, **_kwargs):
        raise AssertionError("unexpected verification retrospective")

    def record_reviewer_changes_requested(self, **_kwargs):
        raise AssertionError("unexpected reviewer retrospective")

    def record_approved_review_success_observation(self, **_kwargs):
        self.calls.append("approved")
        raise ValueError("repair_learning_input_invalid")


class _LearningMatchFailureService:
    def matched_checks_for_attempt(self, _task, *, run_id):
        del run_id
        raise ValueError("repair_learning_input_invalid")

    def record_verification_failure(self, **_kwargs):
        raise AssertionError("unexpected verification retrospective")

    def record_reviewer_changes_requested(self, **_kwargs):
        raise AssertionError("unexpected reviewer retrospective")

    def record_approved_review_success_observation(self, **_kwargs):
        raise AssertionError("unexpected success observation")


class _ForeignActiveLearningMatchService:
    def matched_checks_for_attempt(self, task, *, run_id):
        foreign = build_current_task_rule(
            derive_task_learning_context(task, run_id=run_id + 1),
            actions=("verification_replay",),
        )
        return (MatchedLearningRule(foreign),)

    def record_verification_failure(self, **_kwargs):
        raise AssertionError("unexpected verification retrospective")

    def record_reviewer_changes_requested(self, **_kwargs):
        raise AssertionError("unexpected reviewer retrospective")

    def record_approved_review_success_observation(self, **_kwargs):
        raise AssertionError("unexpected success observation")


class _MutationAfterStagingLearningService:
    def __init__(self, repository: LocalAgentRunRepository) -> None:
        self._repository = repository
        self._delegate = RepairLearningService(repository)

    def matched_checks_for_attempt(self, task, *, run_id):
        return self._delegate.matched_checks_for_attempt(task, run_id=run_id)

    def record_verification_failure(self, **kwargs):
        return self._delegate.record_verification_failure(**kwargs)

    def record_reviewer_changes_requested(self, **kwargs):
        return self._delegate.record_reviewer_changes_requested(**kwargs)

    def record_approved_review_success_observation(self, **kwargs):
        staged = self._delegate.record_approved_review_success_observation(**kwargs)
        worktree = Path(str(self._repository.snapshot(int(kwargs["run_id"]))["run"]["worktree_path"]))
        (worktree / "late-learning-race.txt").write_text("late\n", encoding="utf-8")
        return staged


class LocalAgentRunnerTests(unittest.TestCase):
    def test_human_correction_awaiting_transaction_rolls_back_when_audit_fails(self) -> None:
        worker = _FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})
        success = {
            "returncode": 0, "timed_out": False, "cleanup": "not_needed",
            "duration_ms": 1, "stdout_sha256": "0" * 64, "stderr_sha256": "0" * 64,
        }
        runner = self._runner(worker)
        with patch("app.local_agent_runner.run_local_agent_verification_argv", return_value=success):
            first = runner.execute(
                self._task(), self._preflight("runner-human-correction-invalidation-replay"),
            )
        run_id = int(first["run"]["id"])
        LocalAgentConfirmationService(
            repository=self.repository, artifact_root=self.worktree_root,
        ).issue_local_apply_confirmation(run_id, "local-user")
        summary_sha256 = "sha256:" + hashlib.sha256(b"human correction after issuance").hexdigest()

        append_event = __import__("app.local_agent_repository", fromlist=["_append_event_in_transaction"])._append_event_in_transaction

        def fail_correction_audit(connection, event_run_id, event_attempt_id, event_type, payload_json):
            if event_type == "confirmation_invalidated_for_correction":
                raise ValueError("local_agent_storage_invalid")
            return append_event(connection, event_run_id, event_attempt_id, event_type, payload_json)

        with patch(
            "app.local_agent_repository._append_event_in_transaction",
            side_effect=fail_correction_audit,
        ):
            with self.assertRaisesRegex(ValueError, "repair_learning_storage_invalid"):
                runner.record_human_correction(
                    run_id,
                    root_cause_kind="implementation_defect",
                    summary_sha256=summary_sha256,
                )

        retained = self.repository.snapshot(run_id)
        self.assertEqual("awaiting_human_confirmation", retained["run"]["status"])
        with self.repository.open_learning_connection() as connection:
            self.assertEqual(
                "issued",
                connection.execute(
                    "select status from local_agent_apply_confirmations where run_id=?", (run_id,),
                ).fetchone()[0],
            )
        self.assertFalse(any(
            item["event_type"] == "confirmation_invalidated_for_correction"
            for item in retained["events"]
        ))
        self.assertEqual(0, len([item for item in retained["artifacts"] if item["kind"] == "repair_retrospective"]))
        self.assertEqual(
            0,
            len(RepairLearningService(self.repository).snapshot_for_run(run_id)["retrospectives"]),
        )

        replayed = runner.record_human_correction(
            run_id,
            root_cause_kind="implementation_defect",
            summary_sha256=summary_sha256,
        )
        self.assertEqual("changes_requested", replayed["run"]["status"])
        self.assertEqual(
            1,
            len([item for item in replayed["artifacts"] if item["kind"] == "repair_retrospective"]),
        )
        self.assertEqual(
            1,
            len(RepairLearningService(self.repository).snapshot_for_run(run_id)["retrospectives"]),
        )

    def test_human_correction_expires_confirmation_before_artifact_hook_can_apply(self) -> None:
        worker = _FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})
        success = {
            "returncode": 0, "timed_out": False, "cleanup": "not_needed",
            "duration_ms": 1, "stdout_sha256": "0" * 64, "stderr_sha256": "0" * 64,
        }
        runner = self._runner(worker)
        with patch("app.local_agent_runner.run_local_agent_verification_argv", return_value=success):
            first = runner.execute(
                self._task(), self._preflight("runner-human-correction-confirmation-race"),
            )
        run_id = int(first["run"]["id"])
        confirmation = LocalAgentConfirmationService(
            repository=self.repository, artifact_root=self.worktree_root,
        )
        issued = confirmation.issue_local_apply_confirmation(run_id, "local-user")
        interleaved: dict[str, object] = {}
        write_artifact = runner._write_learning_retrospective_artifact

        def attempt_apply_before_artifact(*args, **kwargs):
            try:
                interleaved["result"] = confirmation.confirm_and_apply(
                    run_id, issued.token, "local-user",
                )
            except ValueError as error:
                interleaved["error"] = str(error)
            return write_artifact(*args, **kwargs)

        with patch.object(
            runner,
            "_write_learning_retrospective_artifact",
            side_effect=attempt_apply_before_artifact,
        ):
            corrected = runner.record_human_correction(
                run_id,
                root_cause_kind="implementation_defect",
                summary_sha256="sha256:" + hashlib.sha256(b"race correction").hexdigest(),
            )

        self.assertEqual("changes_requested", corrected["run"]["status"])
        self.assertEqual("local_agent_confirmation_invalid", interleaved.get("error"))
        self.assertNotIn("result", interleaved)
        self.assertEqual("def add(a, b):\n    return a - b\n", (self.project / "calculator.py").read_text(encoding="utf-8"))
        retained = self.repository.snapshot(run_id)
        self.assertEqual("changes_requested", retained["run"]["status"])
        with self.repository.open_learning_connection() as connection:
            self.assertEqual(
                "expired",
                connection.execute(
                    "select status from local_agent_apply_confirmations where run_id=?", (run_id,),
                ).fetchone()[0],
            )
        self.assertEqual(1, len(RepairLearningService(self.repository).snapshot_for_run(run_id)["retrospectives"]))

    def test_human_correction_artifact_failure_keeps_atomic_apply_blocker(self) -> None:
        worker = _FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})
        success = {
            "returncode": 0, "timed_out": False, "cleanup": "not_needed",
            "duration_ms": 1, "stdout_sha256": "0" * 64, "stderr_sha256": "0" * 64,
        }
        runner = self._runner(worker)
        with patch("app.local_agent_runner.run_local_agent_verification_argv", return_value=success):
            first = runner.execute(
                self._task(), self._preflight("runner-human-correction-artifact-failure"),
            )
        run_id = int(first["run"]["id"])
        LocalAgentConfirmationService(
            repository=self.repository, artifact_root=self.worktree_root,
        ).issue_local_apply_confirmation(run_id, "local-user")

        with patch.object(
            runner,
            "_write_learning_retrospective_artifact",
            side_effect=ValueError("local_agent_artifact_invalid"),
        ):
            with self.assertRaisesRegex(ValueError, "local_agent_artifact_invalid"):
                runner.record_human_correction(
                    run_id,
                    root_cause_kind="implementation_defect",
                    summary_sha256="sha256:" + hashlib.sha256(b"artifact failure").hexdigest(),
                )

        retained = self.repository.snapshot(run_id)
        self.assertEqual("changes_requested", retained["run"]["status"])
        with self.repository.open_learning_connection() as connection:
            self.assertEqual(
                "expired",
                connection.execute(
                    "select status from local_agent_apply_confirmations where run_id=?", (run_id,),
                ).fetchone()[0],
            )
        self.assertEqual(1, len(RepairLearningService(self.repository).snapshot_for_run(run_id)["retrospectives"]))

    def test_record_human_correction_on_failed_verification_preserves_status_and_writes_hash_only_artifact(self) -> None:
        worker = _FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})
        failed = {
            "returncode": 1, "timed_out": False, "cleanup": "not_needed",
            "duration_ms": 1, "stdout_sha256": "0" * 64, "stderr_sha256": "1" * 64,
        }
        runner = self._runner(worker)
        with patch("app.local_agent_runner.run_local_agent_verification_argv", return_value=failed):
            result = runner.execute(self._task(), self._preflight("runner-human-correction-failed-verification"))
        raw_summary = "human found implementation defect before retry"

        snapshot = runner.record_human_correction(
            int(result["run"]["id"]),
            root_cause_kind="implementation_defect",
            summary_sha256="sha256:" + hashlib.sha256(raw_summary.encode("utf-8")).hexdigest(),
        )

        self.assertEqual("failed_verification", snapshot["run"]["status"])
        repair = RepairLearningService(self.repository).snapshot_for_run(int(result["run"]["id"]))
        self.assertEqual("implementation_defect", repair["retrospectives"][-1]["root_cause_kind"])
        artifacts = [item for item in snapshot["artifacts"] if item["kind"] == "repair_retrospective"]
        self.assertEqual(2, len(artifacts))
        self.assertEqual(2, len({item["relative_path"] for item in artifacts}))
        self.assertTrue(any("-s1-c1" in item["relative_path"] for item in artifacts))
        self.assertTrue(any("-s3-c5" in item["relative_path"] for item in artifacts))
        verification_artifact = next(item for item in artifacts if "-s1-c1" in item["relative_path"])
        payload = (self.worktree_root / artifacts[-1]["relative_path"]).read_text(encoding="utf-8")
        self.assertNotIn(raw_summary, payload)
        self.assertRegex(payload, r'"summary":"sha256:[0-9a-f]{64}"')

        replayed = runner.record_human_correction(
            int(result["run"]["id"]),
            root_cause_kind="implementation_defect",
            summary_sha256="sha256:" + hashlib.sha256(raw_summary.encode("utf-8")).hexdigest(),
        )
        replayed_artifacts = [item for item in replayed["artifacts"] if item["kind"] == "repair_retrospective"]
        self.assertEqual(2, len(replayed_artifacts))
        replayed_verification = next(item for item in replayed_artifacts if "-s1-c1" in item["relative_path"])
        self.assertEqual(verification_artifact["relative_path"], replayed_verification["relative_path"])
        self.assertEqual(verification_artifact["sha256"], replayed_verification["sha256"])
    def test_protocol_rejection_audit_is_persisted_before_worker_failure(self) -> None:
        audit = ProtocolRejectionAudit("unknown_event_type", "unknown_item_type", 5, "a" * 64, 3, "turn_active", "60_179s", "missing", 0, 0)
        worker = _FixtureWorker({}, error_code="worker_protocol_invalid")
        original = worker.start
        def start(request, sink):
            return dataclasses.replace(original(request, sink), protocol_rejection=audit)
        worker.start = start
        snapshot = self._runner(worker).execute(self._task(), self._preflight("protocol-rejection-audit"))
        event = next(item for item in snapshot["events"] if item["event_type"] == "worker_protocol_rejected")
        self.assertEqual(audit.as_mapping(), event["payload"])
        self.assertEqual("failed_worker", snapshot["run"]["status"])

    def test_protocol_failed_audit_is_persisted_before_worker_failure(self) -> None:
        audit = ProtocolRejectionAudit("turn.failed", "missing", 35, "1631f96cbf2dfe490b988c3a4ae6d996d39d27c194a370f8cc48160191ab2d29", 3, "turn_active", "10_59s", "object", 1, 1)
        worker = _FixtureWorker({}, error_code="worker_protocol_failed")
        original = worker.start

        def start(request, sink):
            return dataclasses.replace(original(request, sink), protocol_rejection=audit)

        worker.start = start
        snapshot = self._runner(worker).execute(self._task(), self._preflight("protocol-failed-audit"))
        event = next(item for item in snapshot["events"] if item["event_type"] == "worker_protocol_rejected")
        self.assertEqual(audit.as_mapping(), event["payload"])
        self.assertEqual("failed_worker", snapshot["run"]["status"])
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="his_harness_stage_f_runner_", dir="/private/tmp")
        self.root = Path(self.tmp.name)
        self._original_database_path = database.DB_PATH
        self._default_database_path = (database.DATA_DIR / "harness.sqlite").resolve()
        self.database_path = self.root / "control" / "harness.sqlite"
        self.database_path.parent.mkdir()
        database.DB_PATH = self.database_path
        self.database_open_paths: list[Path] = []
        raw_connect = database.connect_database

        def guarded_connect(path: Path):
            resolved = path.resolve()
            self.assertNotEqual(self._default_database_path, resolved)
            self.database_open_paths.append(resolved)
            return raw_connect(path)

        self._connect_patch = patch("app.database.connect_database", side_effect=guarded_connect)
        self._connect_patch.start()
        self.project = self.root / "project"
        self.project.mkdir()
        self._git("init")
        self._git("config", "user.email", "harness@example.test")
        self._git("config", "user.name", "Harness Test")
        (self.project / "calculator.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        (self.project / "test_calculator.py").write_text(
            "import unittest\nfrom calculator import add\n\nclass CalculatorTests(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(3, add(1, 2))\n",
            encoding="utf-8",
        )
        self._git("add", ".")
        self._git("commit", "-m", "initial")
        database.init_db()
        self.repository = LocalAgentRunRepository(self.database_path)
        self.worktree_root = Path(tempfile.mkdtemp(prefix="his_harness_stage_f_worktree_", dir="/private/tmp"))

    def tearDown(self) -> None:
        subprocess.run(["git", "worktree", "prune"], cwd=self.project, check=False, capture_output=True, text=True)
        self.tmp.cleanup()
        if self.worktree_root.exists():
            import shutil

            shutil.rmtree(self.worktree_root)
        self._connect_patch.stop()
        database.DB_PATH = self._original_database_path
        self.assertNotIn(self._default_database_path, self.database_open_paths)

    def _git(self, *arguments: str) -> None:
        subprocess.run(["git", *arguments], cwd=self.project, check=True, capture_output=True, text=True)

    def _task(self, *, test_name: str = "test_calculator", allowed_paths: list[str] | None = None, task_key: str = "fixture-fix-1"):
        payload = {
            "schema_version": "his-local-agent-task.v1",
            "task_key": task_key,
            "project_path": str(self.project),
            "request": "Fix add so the supplied unit test passes.",
            "allowed_paths": allowed_paths or ["calculator.py"],
            "verification_commands": [[sys.executable, "-m", "unittest", "-q", test_name]],
            "acceptance_criteria": ["The existing test passes."],
            "timeout_seconds": 30,
        }
        path = self.root / f"{test_name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return load_local_agent_task(path)

    def _preflight(self, label: str):
        return assert_local_agent_run_allowed(allow_real_agent=True, authorization_id=label)

    def _runner(self, worker, *, learning_service=None, review_worker=None, backend_id=None):
        from app.local_agent_runner import LocalAgentRunner

        reviewer = LocalAgentReviewer(
            repository=self.repository,
            worker=review_worker or _ApprovedReviewWorker(),
            artifact_root=self.worktree_root,
        )
        return LocalAgentRunner(
            repository=self.repository,
            worker=worker,
            reviewer=reviewer,
            worktree_root=self.worktree_root,
            learning_service=learning_service,
            backend_id=backend_id,
        )

    def test_learning_success_observation_failure_never_reaches_confirmation(self) -> None:
        worker = _FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})
        learning = _LearningFailureService()
        success = {
            "returncode": 0,
            "timed_out": False,
            "cleanup": "not_needed",
            "duration_ms": 1,
            "stdout_sha256": "0" * 64,
            "stderr_sha256": "0" * 64,
        }

        with patch("app.local_agent_runner.run_local_agent_verification_argv", return_value=success):
            snapshot = self._runner(worker, learning_service=learning).execute(
                self._task(), self._preflight("runner-learning-success-failure")
            )

        self.assertEqual(["matched", "approved"], learning.calls)
        self.assertEqual("failed_review", snapshot["run"]["status"])
        self.assertFalse(any(item["event_type"] == "review_finished" for item in snapshot["events"]))
        self.assertFalse(any(item["kind"] in {"final_review", "review_seal"} for item in snapshot["artifacts"]))

    def test_learning_match_failure_is_audited_and_never_starts_worker(self) -> None:
        worker = _FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})

        snapshot = self._runner(worker, learning_service=_LearningMatchFailureService()).execute(
            self._task(), self._preflight("runner-learning-match-failure"),
        )

        self.assertEqual("interrupted", snapshot["run"]["status"])
        self.assertEqual([], worker.requests)
        self.assertTrue(any(
            item["event_type"] == "repair_learning_failed"
            and item["payload"] == {"stage": "attempt_start"}
            for item in snapshot["events"]
        ))
        self.assertFalse(any(item["event_type"] == "review_finished" for item in snapshot["events"]))

    def test_foreign_active_learning_match_fails_before_any_learning_audit_or_worker_start(self) -> None:
        worker = _FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})

        snapshot = self._runner(
            worker,
            learning_service=_ForeignActiveLearningMatchService(),
        ).execute(
            self._task(), self._preflight("runner-learning-foreign-active-match"),
        )

        self.assertEqual("interrupted", snapshot["run"]["status"])
        self.assertEqual([], worker.requests)
        self.assertFalse(any(item["kind"] == "repair_learning_checks" for item in snapshot["artifacts"]))
        self.assertTrue(any(
            item["event_type"] == "repair_learning_failed"
            and item["payload"] == {"stage": "attempt_start"}
            for item in snapshot["events"]
        ))
        self.assertFalse(any(item["event_type"] == "repair_learning_checks_matched" for item in snapshot["events"]))

    def test_last_pre_final_integrity_failure_leaves_no_learning_observation_or_confirmation(self) -> None:
        worker = _FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})
        failure = {
            "returncode": 1, "timed_out": False, "cleanup": "not_needed",
            "duration_ms": 1, "stdout_sha256": "0" * 64,
            "stderr_sha256": "1" * 64,
        }
        success = {**failure, "returncode": 0, "stderr_sha256": "0" * 64}
        first_runner = self._runner(worker)

        with patch(
            "app.local_agent_runner.run_local_agent_verification_argv",
            side_effect=[failure, success],
        ):
            first = first_runner.execute(
                self._task(), self._preflight("runner-learning-pre-final-first"),
            )
            snapshot = self._runner(
                worker,
                learning_service=_MutationAfterStagingLearningService(self.repository),
            ).retry(int(first["run"]["id"]))

        self.assertEqual("failed_review", snapshot["run"]["status"])
        learning = RepairLearningService(self.repository).snapshot_for_run(int(first["run"]["id"]))
        self.assertEqual([], learning["observations"])
        self.assertEqual(LearningRuleState.ACTIVE_CURRENT_TASK.value, learning["rules"][0]["state"])
        self.assertFalse(any(item["event_type"] == "review_finished" for item in snapshot["events"]))
        self.assertFalse(any(item["kind"] in {"final_review", "review_seal"} for item in snapshot["artifacts"]))
        self.assertNotEqual("awaiting_human_confirmation", snapshot["run"]["status"])

    def test_finalization_transaction_rechecks_sealed_worktree_fingerprint(self) -> None:
        self._assert_finalization_transaction_rechecks_sealed_fingerprint("worktree")

    def test_finalization_transaction_rechecks_sealed_source_fingerprint(self) -> None:
        self._assert_finalization_transaction_rechecks_sealed_fingerprint("source")

    def _assert_finalization_transaction_rechecks_sealed_fingerprint(self, mutation_kind: str) -> None:
        """A mutation after Runner's last check cannot cross finalization."""

        worker = _FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})
        failure = {
            "returncode": 1, "timed_out": False, "cleanup": "not_needed",
            "duration_ms": 1, "stdout_sha256": "0" * 64,
            "stderr_sha256": "1" * 64,
        }
        success = {**failure, "returncode": 0, "stderr_sha256": "0" * 64}

        task_key = f"runner-finalization-transaction-{mutation_kind}"
        first_runner = self._runner(worker)
        original_finalize = self.repository.finalize_review

        def mutate_before_transaction(capability, **kwargs):
            if mutation_kind == "worktree":
                worktree = Path(str(self.repository.snapshot(int(first["run"]["id"]))["run"]["worktree_path"]))
                (worktree / "transaction-window.txt").write_text("late\n", encoding="utf-8")
            else:
                (self.project / "source-transaction-window.txt").write_text("late\n", encoding="utf-8")
            return original_finalize(capability, **kwargs)

        with patch(
            "app.local_agent_runner.run_local_agent_verification_argv",
            side_effect=[failure, success],
        ):
            first = first_runner.execute(
                self._task(task_key=task_key),
                self._preflight(f"runner-finalization-first-{mutation_kind}"),
            )
            with patch.object(
                self.repository,
                "finalize_review",
                side_effect=mutate_before_transaction,
            ):
                snapshot = self._runner(worker).retry(int(first["run"]["id"]))

        run_id = int(first["run"]["id"])
        self.assertEqual("failed_review", snapshot["run"]["status"])
        learning = RepairLearningService(self.repository).snapshot_for_run(run_id)
        self.assertEqual([], learning["observations"])
        self.assertEqual(LearningRuleState.ACTIVE_CURRENT_TASK.value, learning["rules"][0]["state"])
        self.assertFalse(any(item["event_type"] == "review_finished" for item in snapshot["events"]))
        self.assertFalse(any(item["kind"] in {"final_review", "review_seal"} for item in snapshot["artifacts"]))
        with self.repository._connect() as connection:
            self.assertIsNone(connection.execute(
                "select 1 from local_agent_apply_confirmations where run_id=?", (run_id,),
            ).fetchone())

    def test_verification_failure_persists_one_retro_and_next_retry_injects_checks(self) -> None:
        # Both attempts need a durable, allowed worker patch.  The first
        # attempt must reach the verifier before retry can be exercised.
        worker = _FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})
        failure = {
            "returncode": 1, "timed_out": False, "cleanup": "not_needed",
            "duration_ms": 1, "stdout_sha256": "0" * 64,
            "stderr_sha256": "1" * 64,
        }
        success = {**failure, "returncode": 0, "stderr_sha256": "0" * 64}
        review_worker = _ApprovedReviewWorker()
        runner = self._runner(worker, review_worker=review_worker)
        with patch("app.local_agent_runner.run_local_agent_verification_argv", side_effect=[failure, success]):
            first = runner.execute(
                self._task(), self._preflight("runner-learning-verification-failure"),
            )
            second = runner.retry(first["run"]["id"])

        learning = RepairLearningService(self.repository).snapshot_for_run(first["run"]["id"])
        self.assertEqual("failed_verification", first["run"]["status"])
        self.assertEqual("awaiting_human_confirmation", second["run"]["status"])
        self.assertEqual(1, len(learning["retrospectives"]))
        self.assertEqual(1, len(learning["observations"]))
        self.assertEqual(2, len(worker.requests))
        self.assertNotIn("FIXED_LEARNING_CHECKS_BEGIN", worker.requests[0].prompt)
        self.assertIn("FIXED_LEARNING_CHECKS_BEGIN", worker.requests[1].prompt)
        self.assertIn("verification_replay", worker.requests[1].prompt)
        self.assertEqual(1, len(review_worker.requests))
        self.assertIn("FIXED_LEARNING_REVIEW_FOCUS_BEGIN", review_worker.requests[0].prompt)
        self.assertIn("verification_replay", review_worker.requests[0].prompt)
        artifacts = [item for item in second["artifacts"] if item["kind"] == "repair_learning_checks"]
        self.assertEqual(2, len(artifacts))

    def test_retry_issues_a_new_authoritative_harness_decision(self) -> None:
        worker = _FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})
        failure = {
            "returncode": 1, "timed_out": False, "cleanup": "not_needed",
            "duration_ms": 1, "stdout_sha256": "0" * 64, "stderr_sha256": "1" * 64,
        }
        success = {**failure, "returncode": 0, "stderr_sha256": "0" * 64}
        runner = self._runner(worker)
        with patch("app.local_agent_runner.run_local_agent_verification_argv", side_effect=[failure, success]):
            first = runner.execute(
                self._task(task_key="runner-authoritative-decision"),
                self._preflight("runner-authoritative-decision"),
            )
            second = runner.retry(first["run"]["id"])

        decisions = [item for item in second["artifacts"] if item["kind"] == "harness_decision"]
        self.assertEqual(2, len(decisions))
        first_payload = json.loads((self.worktree_root / decisions[0]["relative_path"]).read_text(encoding="utf-8"))
        second_payload = json.loads((self.worktree_root / decisions[1]["relative_path"]).read_text(encoding="utf-8"))
        self.assertEqual(1, first_payload["plan_version"])
        self.assertEqual(2, second_payload["plan_version"])
        self.assertEqual(1, second_payload["supersedes_plan_version"])
        self.assertEqual("replan", second_payload["decision_kind"])
        self.assertTrue(second_payload["execute_only"])
        self.assertTrue(second_payload["forbid_model_replanning"])
        self.assertNotEqual(first_payload["decision_sha256"], second_payload["decision_sha256"])
        self.assertIn("HARNESS_DECISION_BEGIN", worker.requests[1].prompt)
        self.assertIn("forbid_model_replanning", worker.requests[1].prompt)
        self.assertIn("replan_before_model_execution", worker.requests[1].prompt)

    def test_human_correction_is_loaded_before_next_attempt_and_forces_replan(self) -> None:
        worker = _FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})
        failure = {
            "returncode": 1, "timed_out": False, "cleanup": "not_needed",
            "duration_ms": 1, "stdout_sha256": "0" * 64, "stderr_sha256": "1" * 64,
        }
        success = {**failure, "returncode": 0, "stderr_sha256": "0" * 64}
        runner = self._runner(worker)
        summary = "the previous decision used the wrong implementation boundary"
        with patch("app.local_agent_runner.run_local_agent_verification_argv", side_effect=[failure, success]):
            first = runner.execute(
                self._task(task_key="runner-human-guard-next-attempt"),
                self._preflight("runner-human-guard-next-attempt"),
            )
            runner.record_human_correction(
                int(first["run"]["id"]),
                root_cause_kind="contract_mismatch",
                summary_sha256="sha256:" + hashlib.sha256(summary.encode("utf-8")).hexdigest(),
            )
            second = runner.retry(int(first["run"]["id"]))

        self.assertEqual("awaiting_human_confirmation", second["run"]["status"])
        decision_artifacts = [item for item in second["artifacts"] if item["kind"] == "harness_decision"]
        payload = json.loads((self.worktree_root / decision_artifacts[-1]["relative_path"]).read_text(encoding="utf-8"))
        self.assertEqual("contract_mismatch", payload["learning_guard"]["guards"][0]["root_cause"])
        self.assertTrue(payload["learning_guard"]["must_replan"])
        self.assertTrue(payload["forbid_model_replanning"])
        self.assertIn("reconcile_contract_with_project_evidence", worker.requests[1].prompt)
        self.assertIn("replan_before_model_execution", worker.requests[1].prompt)

    def test_reviewer_changes_requested_persists_one_retro_and_no_confirmation(self) -> None:
        worker = _FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})
        success = {
            "returncode": 0, "timed_out": False, "cleanup": "not_needed",
            "duration_ms": 1, "stdout_sha256": "0" * 64,
            "stderr_sha256": "0" * 64,
        }
        with patch("app.local_agent_runner.run_local_agent_verification_argv", return_value=success):
            snapshot = self._runner(worker, review_worker=_ChangesRequestedReviewWorker()).execute(
                self._task(), self._preflight("runner-learning-review-changes"),
            )

        learning = RepairLearningService(self.repository).snapshot_for_run(snapshot["run"]["id"])
        self.assertEqual("changes_requested", snapshot["run"]["status"])
        self.assertEqual(1, len(learning["retrospectives"]))
        self.assertEqual("review_observation", learning["retrospectives"][0]["source_kind"])
        self.assertFalse(any(item["kind"] == "repair_learning_checks" and item["attempt_id"] != snapshot["attempts"][-1]["id"] for item in snapshot["artifacts"]))
        flux = FluxLiteExperienceService(self.repository).snapshot_for_attempt(
            run_id=int(snapshot["run"]["id"]),
            attempt_id=int(snapshot["attempts"][-1]["id"]),
        )
        self.assertEqual(1, len(flux["opinions"]))
        self.assertEqual("candidate", flux["candidates"][0]["state"])

    def test_execute_records_safe_allowed_change_and_verifies(self) -> None:
        worker = _FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})
        task = self._task()

        snapshot = self._runner(worker).execute(task, self._preflight("runner-test-authorization-1"))

        self.assertEqual("awaiting_human_confirmation", snapshot["run"]["status"])
        self.assertEqual(["calculator.py"], snapshot["change"]["changed_paths"])
        self.assertEqual(0, snapshot["verification"][0]["returncode"])
        self.assertEqual(1, len(worker.requests))
        request = worker.requests[0]
        active_workspace = Path(str(snapshot["run"]["worktree_path"]))
        self.assertEqual(active_workspace, request.worktree_path)
        self.assertIn(
            f"Active isolated workspace: {json.dumps(str(active_workspace))}", request.prompt,
        )
        self.assertIn(
            f"Source repository identity: {json.dumps(str(task.project_path))}", request.prompt,
        )
        self.assertNotIn("Work only inside the validated project path", request.prompt)

    def test_execute_persists_reduced_real_worker_event_without_opaque_identifiers(self) -> None:
        worker = _EventWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})

        snapshot = self._runner(worker).execute(
            self._task(), self._preflight("runner-test-real-event-authorization")
        )

        self.assertEqual("awaiting_human_confirmation", snapshot["run"]["status"])
        event = next(item for item in snapshot["events"] if item["event_type"] == "worker_event")
        self.assertEqual({
            "type": "thread.started",
            "sequence_no": 1,
            "raw_line_digest": "sha256:" + "1234567890abcdef" * 4,
        }, event["payload"])

    def test_recoverable_error_event_has_a_safe_persistent_shape(self) -> None:
        from app.local_agent_runner import _persistent_worker_event

        self.assertEqual({
            "type": "error",
            "sequence_no": 3,
            "raw_line_digest": "sha256:" + "abcdef0123456789" * 4,
        }, _persistent_worker_event({
            "type": "error",
            "sequence_no": 3,
            "raw_line_sha256": "abcdef0123456789" * 4,
        }))

    def test_completed_error_item_has_a_safe_persistent_shape(self) -> None:
        from app.local_agent_runner import _persistent_worker_event

        self.assertEqual({
            "type": "item.completed",
            "sequence_no": 4,
            "item_type": "error",
            "raw_line_digest": "sha256:" + "0123456789abcdef" * 4,
        }, _persistent_worker_event({
            "type": "item.completed",
            "sequence_no": 4,
            "item_type": "error",
            "raw_line_sha256": "0123456789abcdef" * 4,
        }))

    def test_execute_rejects_path_outside_contract_before_verification(self) -> None:
        worker = _FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n", "outside.py": "forbidden\n"})

        snapshot = self._runner(worker).execute(self._task(), self._preflight("runner-test-authorization-2"))

        self.assertEqual("failed_scope", snapshot["run"]["status"])
        self.assertEqual([], snapshot["verification"])

    def test_execute_rejects_real_unittest_side_effect(self) -> None:
        (self.project / "test_side_effect.py").write_text(
            "import unittest\nfrom pathlib import Path\n\nclass SideEffectTests(unittest.TestCase):\n    def test_write(self):\n        Path('verification-side-effect.txt').write_text('x', encoding='utf-8')\n",
            encoding="utf-8",
        )
        self._git("add", "test_side_effect.py")
        self._git("commit", "-m", "side effect test")
        worker = _FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})

        snapshot = self._runner(worker).execute(self._task(test_name="test_side_effect"), self._preflight("runner-test-authorization-3"))

        self.assertEqual("failed_verification", snapshot["run"]["status"])
        self.assertNotEqual(0, snapshot["verification"][0]["returncode"])
        self.assertFalse(snapshot["verification"][0]["side_effect"])
        quarantined = snapshot["run"]["worktree_path"]
        # C3: retry never reuses the tainted path; it replays the durable
        # worker patch into a new owned generation before the worker starts.
        replayed = self._runner(worker).retry(snapshot["run"]["id"])
        self.assertEqual("failed_verification", replayed["run"]["status"])
        self.assertNotEqual(quarantined, replayed["run"]["worktree_path"])
        self.assertTrue(Path(quarantined).exists())
        self.assertIn("_attempt_2", replayed["run"]["worktree_path"])

    def test_retry_reuses_the_same_worktree_without_reconsuming_preflight(self) -> None:
        worker = _RetryWorker()
        worker.error_code = "worker_process_failed"
        runner = self._runner(worker)

        first = runner.execute(self._task(), self._preflight("runner-test-authorization-4"))
        worker.error_code = ""
        # Reconstructing a new runner proves retry is DB/artifact based rather
        # than dependent on process-local bindings.
        second = self._runner(worker).retry(first["run"]["id"])

        self.assertEqual("failed_worker", first["run"]["status"])
        self.assertEqual("awaiting_human_confirmation", second["run"]["status"])
        self.assertEqual([1, 2], [attempt["attempt_no"] for attempt in second["attempts"]])
        self.assertEqual(2, worker.calls)

    def test_selected_backend_is_audited_and_cannot_change_on_retry(self) -> None:
        worker = _FixtureWorker({}, error_code="worker_process_failed")
        first = self._runner(worker, backend_id="host-bridge").execute(
            self._task(), self._preflight("runner-backend-binding")
        )

        self.assertEqual(
            "host-bridge",
            next(
                event["payload"]["backend_id"]
                for event in first["events"]
                if event["event_type"] == "agent_backend_selected"
            ),
        )
        with self.assertRaisesRegex(ValueError, "local_agent_backend_mismatch"):
            self._runner(
                _FixtureWorker({}, error_code="worker_process_failed"),
                backend_id="codex-cli",
            ).retry(first["run"]["id"])

    def test_auto_repair_is_budgeted_and_stops_at_human_confirmation(self) -> None:
        worker = _RetryWorker()
        worker.error_code = "worker_process_failed"
        runner = self._runner(worker, review_worker=_ApprovedReviewWorker())

        success = {
            "returncode": 0, "timed_out": False, "cleanup": "not_needed",
            "duration_ms": 1, "stdout_sha256": "0" * 64, "stderr_sha256": "0" * 64,
        }
        with patch("app.local_agent_runner.run_local_agent_verification_argv", return_value=success):
            first = runner.execute(self._task(), self._preflight("runner-auto-repair-budget"))
            worker.error_code = ""
            repaired = runner.auto_repair(first["run"]["id"], max_rounds=2)

        self.assertEqual("failed_worker", first["run"]["status"])
        self.assertEqual("awaiting_human_confirmation", repaired["run"]["status"])
        self.assertEqual(2, len(repaired["attempts"]))
        self.assertEqual(2, worker.calls)
        self.assertTrue(any(item["event_type"] == "auto_repair_round" for item in repaired["events"]))
        decisions = [item for item in repaired["artifacts"] if item["kind"] == "harness_decision"]
        self.assertEqual(2, len(decisions))
        first_decision = json.loads((self.worktree_root / decisions[0]["relative_path"]).read_text(encoding="utf-8"))
        second_decision = json.loads((self.worktree_root / decisions[1]["relative_path"]).read_text(encoding="utf-8"))
        self.assertEqual(1, first_decision["plan_version"])
        self.assertEqual(2, second_decision["plan_version"])
        self.assertEqual(1, second_decision["supersedes_plan_version"])
        self.assertEqual("replan", second_decision["decision_kind"])
        self.assertTrue(second_decision["forbid_model_replanning"])

    def test_third_failed_attempt_exhausts_run_and_releases_project_lease(self) -> None:
        worker = _FixtureWorker({}, error_code="worker_process_failed")
        runner = self._runner(worker)

        first = runner.execute(self._task(), self._preflight("runner-test-authorization-exhaust-1"))
        second = runner.retry(first["run"]["id"])
        exhausted = runner.retry(second["run"]["id"])

        self.assertEqual("attempts_exhausted", exhausted["run"]["status"])
        self.assertEqual([1, 2, 3], [attempt["attempt_no"] for attempt in exhausted["attempts"]])
        with self.assertRaisesRegex(ValueError, "local_agent_retry_exhausted"):
            runner.retry(exhausted["run"]["id"])

        replacement = runner.execute(
            self._task(task_key="fixture-fix-2"), self._preflight("runner-test-authorization-exhaust-2")
        )
        self.assertEqual("failed_worker", replacement["run"]["status"])
        self.assertNotEqual(exhausted["run"]["id"], replacement["run"]["id"])

    def test_retry_repairs_legacy_three_attempt_failure_and_releases_lease(self) -> None:
        worker = _FixtureWorker({}, error_code="worker_process_failed")
        runner = self._runner(worker)
        first = runner.execute(self._task(), self._preflight("runner-test-authorization-legacy-1"))
        second = runner.retry(first["run"]["id"])
        identity = _read_process_start_identity(os.getpid())
        now = database.now_iso()
        with database.connect_database(self.database_path) as connection:
            connection.execute(
                "insert into local_agent_attempts(run_id,attempt_no,status,worker_pid,worker_start_identity,error_code,started_at,finished_at) values(?,?,?,?,?,?,?,?)",
                (second["run"]["id"], 3, "failed_worker", os.getpid(), identity, "worker_process_failed", now, now),
            )
        reopened = self._runner(worker)
        self.assertEqual("failed_worker", self.repository.snapshot(second["run"]["id"])["run"]["status"])

        with self.assertRaisesRegex(ValueError, "local_agent_retry_exhausted"):
            reopened.retry(second["run"]["id"])

        repaired = self.repository.snapshot(second["run"]["id"])
        self.assertEqual("attempts_exhausted", repaired["run"]["status"])
        with database.connect_database(self.database_path) as connection:
            self.assertIsNone(connection.execute(
                "select 1 from local_agent_project_leases where run_id=?", (second["run"]["id"],),
            ).fetchone())
        replacement = reopened.execute(
            self._task(task_key="fixture-legacy-replacement"),
            self._preflight("runner-test-authorization-legacy-2"),
        )
        self.assertEqual("failed_worker", replacement["run"]["status"])

    def test_execute_accepts_staged_cumulative_change(self) -> None:
        worker = _StagedWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})
        snapshot = self._runner(worker).execute(self._task(), self._preflight("runner-test-authorization-staged"))
        self.assertEqual("awaiting_human_confirmation", snapshot["run"]["status"])
        self.assertEqual(["calculator.py"], snapshot["change"]["changed_paths"])

    def test_prepare_marker_failure_quarantines_registered_path_and_retries_generation(self) -> None:
        worker = _FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})
        with patch("app.worktree_lifecycle.create_worktree_marker", side_effect=OSError("injected")):
            first = self._runner(worker).execute(self._task(), self._preflight("runner-test-authorization-marker"))
        self.assertEqual("failed_workspace", first["run"]["status"])
        quarantined = list(self.worktree_root.glob("run_*"))
        self.assertEqual(1, len(quarantined))
        self.assertTrue(quarantined[0].is_dir())
        second = self._runner(worker).retry(first["run"]["id"])
        self.assertEqual("awaiting_human_confirmation", second["run"]["status"])
        self.assertTrue(second["run"]["worktree_path"].endswith("_attempt_2"))

    def test_verification_exception_is_retryable_failed_verification(self) -> None:
        worker = _FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})
        with patch("app.local_agent_runner.run_local_agent_verification_argv", side_effect=OSError("injected")):
            snapshot = self._runner(worker).execute(self._task(), self._preflight("runner-test-authorization-verify-ex"))
        self.assertEqual("failed_verification", snapshot["run"]["status"])
        self.assertEqual(
            1,
            len(RepairLearningService(self.repository).snapshot_for_run(snapshot["run"]["id"])["retrospectives"]),
        )

    def test_untracked_only_patch_replays_into_new_quarantine_generation(self) -> None:
        (self.project / "test_side_effect.py").write_text(
            "import unittest\nfrom pathlib import Path\n\nclass SideEffectTests(unittest.TestCase):\n"
            "    def test_write(self):\n        Path('verification-side-effect.txt').write_text('x', encoding='utf-8')\n",
            encoding="utf-8",
        )
        self._git("add", "test_side_effect.py")
        self._git("commit", "-m", "side effect test")
        worker = _FixtureWorker({"generated.txt": "untracked worker patch\n"})
        first = self._runner(worker).execute(
            self._task(test_name="test_side_effect", allowed_paths=["generated.txt"]),
            self._preflight("runner-test-authorization-untracked-replay"),
        )
        self.assertEqual("failed_verification", first["run"]["status"])
        self.assertEqual(["generated.txt"], first["change"]["changed_paths"])
        old_path = first["run"]["worktree_path"]
        first_hash = first["change"]["patch_sha256"]
        second = self._runner(worker).retry(first["run"]["id"])
        self.assertEqual("failed_verification", second["run"]["status"])
        self.assertNotEqual(old_path, second["run"]["worktree_path"])
        self.assertTrue(Path(old_path).is_dir())
        self.assertEqual(["generated.txt"], second["change"]["changed_paths"])
        self.assertEqual(first_hash, second["change"]["patch_sha256"])

    def test_verification_chmod_root_is_sandbox_rejected_and_retryable(self) -> None:
        (self.project / "test_chmod.py").write_text(
            "import unittest\nfrom pathlib import Path\n\nclass ChmodTests(unittest.TestCase):\n"
            "    def test_chmod(self):\n        Path('.').chmod(0o700)\n",
            encoding="utf-8",
        )
        self._git("add", "test_chmod.py")
        self._git("commit", "-m", "chmod test")
        worker = _FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n"})
        first = self._runner(worker).execute(self._task(test_name="test_chmod"), self._preflight("runner-test-authorization-chmod"))
        self.assertEqual("failed_verification", first["run"]["status"])
        self.assertNotEqual(0, first["verification"][0]["returncode"])
        self.assertFalse(first["verification"][0]["side_effect"])
        self.assertEqual("failed_verification", self._runner(worker).retry(first["run"]["id"])["run"]["status"])

    def test_restart_recovers_workspace_ready_without_attempt(self) -> None:
        from app.local_agent_runner import _binding
        from app.worktree_lifecycle import prepare_local_agent_worktree

        runner = self._runner(_FixtureWorker({"calculator.py": "def add(a, b):\n    return a + b\n"}))
        task = self._task()
        run = self.repository.consume_preflight(task, self._preflight("runner-test-authorization-ready"))
        artifact = runner._write_artifact(run["id"], None, "task_contract", __import__("app.local_agent_contract", fromlist=["serialize_local_agent_task"]).serialize_local_agent_task(task), "task.json")
        prepared = prepare_local_agent_worktree(project_path=task.project_path, worktree_root=self.worktree_root, run_id=run["id"])
        binding = _binding(task, prepared, artifact["relative_path"])
        self.repository.bind_workspace(run["id"], __import__("app.local_agent_runner", fromlist=["_binding_mapping"])._binding_mapping(binding, prepared))
        restored = self._runner(runner._worker).retry(run["id"])
        self.assertEqual("awaiting_human_confirmation", restored["run"]["status"])
        self.assertEqual([1], [attempt["attempt_no"] for attempt in restored["attempts"]])

    def test_staged_new_patch_replays_after_verification_failure(self) -> None:
        worker = _StagedNewWorker({"generated.py": "value = 1\n"})
        failure = {"returncode": 1, "timed_out": False, "cleanup": "not_needed", "duration_ms": 1, "stdout_sha256": "0" * 64, "stderr_sha256": "1" * 64}
        success = {"returncode": 0, "timed_out": False, "cleanup": "not_needed", "duration_ms": 1, "stdout_sha256": "0" * 64, "stderr_sha256": "0" * 64}
        with patch("app.local_agent_runner.run_local_agent_verification_argv", side_effect=[failure, success]):
            first = self._runner(worker).execute(self._task(allowed_paths=["generated.py"]), self._preflight("runner-test-authorization-staged-new"))
            second = self._runner(worker).retry(first["run"]["id"])
        self.assertEqual("failed_verification", first["run"]["status"])
        self.assertEqual("awaiting_human_confirmation", second["run"]["status"])
        self.assertEqual(["generated.py"], second["change"]["changed_paths"])
        self.assertEqual(first["change"]["patch_sha256"], second["change"]["patch_sha256"])

    def test_index_only_gitlink_is_failed_scope(self) -> None:
        snapshot = self._runner(_GitlinkWorker({})).execute(self._task(allowed_paths=["module"]), self._preflight("runner-test-authorization-gitlink"))
        self.assertEqual("failed_scope", snapshot["run"]["status"])


if __name__ == "__main__":
    unittest.main()
