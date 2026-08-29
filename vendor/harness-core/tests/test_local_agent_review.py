from __future__ import annotations

import hashlib
import json
import os
import copy
import dataclasses
import pickle
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app import database
from app import local_agent_repository as repository_module
from app.codex_cli_worker import CodexWorkerResult, ProtocolRejectionAudit, STABLE_WORKER_ERROR_CODES, WorkerRole
from app.local_agent_contract import load_local_agent_task
from app.local_agent_confirmation import LocalAgentConfirmationService
from app.local_agent_repository import LocalAgentRunRepository, _read_process_start_identity
from app.local_agent_review import LocalAgentReviewer, ReviewValidationFailure, _ReviewSink, _review_failure_audit, _review_learning_actions, canonical_review_hash, parse_local_agent_review
from app.local_agent_runner import LocalAgentRunner, _tree_fingerprint
from app.worktree_executor import capture_local_agent_tree_snapshot
from app.repair_learning_service import RepairLearningService
from app.repair_learning import (
    LearningRuleState,
    MatchedLearningRule,
    RuleObservationOutcome,
    build_current_task_rule,
    derive_task_learning_context,
)
from app.runtime_policy import assert_local_agent_run_allowed


def _review_payload(verdict: str = "approved", *, summary: str = "No blocking findings.") -> dict[str, object]:
    findings: list[dict[str, object]] = []
    if verdict == "changes_requested":
        findings = [{"severity": "important", "path": "calculator.py", "line": 1, "message": "Incorrect boundary."}]
    payload: dict[str, object] = {
        "schema_version": "his-local-agent-review.v1",
        "verdict": verdict,
        "findings": findings,
        "summary": summary,
    }
    payload["review_hash"] = canonical_review_hash(payload)
    return payload


def _successful_verification() -> dict[str, object]:
    return {
        "returncode": 0,
        "timed_out": False,
        "cleanup": "not_needed",
        "duration_ms": 1,
        "stdout_sha256": "0" * 64,
        "stderr_sha256": "0" * 64,
    }


def _seal_current_trees(reviewer: LocalAgentReviewer, result, project: Path) -> object:
    return reviewer.seal(
        result,
        source_fingerprint=_tree_fingerprint(capture_local_agent_tree_snapshot(project)),
        worktree_fingerprint=_tree_fingerprint(capture_local_agent_tree_snapshot(Path(result.worktree_path))),
    )


def _review_learning_actions_for_test(task, run_id, focus):
    return _review_learning_actions(task, run_id, focus)


class _CodeWorker:
    def __init__(self) -> None:
        self.requests = []

    def start(self, request, sink):
        self.requests.append(request)
        sink.on_started(os.getpid(), _read_process_start_identity(os.getpid()))
        (request.worktree_path / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        return _worker_result(None)


class _ReviewWorker:
    def __init__(self, payloads: list[dict[str, object]], *, mutation: str = "", source_mutation: Path | None = None, tamper_relative: str = "") -> None:
        self.payloads = list(payloads)
        self.mutation = mutation
        self.source_mutation = source_mutation
        self.tamper_relative = tamper_relative
        self.requests = []

    def start(self, request, sink):
        self.requests.append(request)
        sink.on_started(os.getpid(), _read_process_start_identity(os.getpid()))
        if self.mutation:
            (request.worktree_path / self.mutation).write_text("reviewer mutation\n", encoding="utf-8")
        if self.source_mutation is not None:
            self.source_mutation.write_text("reviewer source mutation\n", encoding="utf-8")
        if self.tamper_relative:
            target = request.worktree_path.parent / self.tamper_relative
            target.chmod(0o600)
            target.write_bytes(b"tampered")
        payload = self.payloads.pop(0)
        return _worker_result(payload)


class _DigestMismatchReviewWorker(_ReviewWorker):
    def start(self, request, sink):
        result = super().start(request, sink)
        return replace(result, canonical_final_response_sha256="0" * 64)


class _ProtocolFailedReviewWorker(_ReviewWorker):
    def start(self, request, sink):
        sink.on_started(os.getpid(), _read_process_start_identity(os.getpid()))
        audit = ProtocolRejectionAudit(
            "turn.failed", "missing", 35,
            "1631f96cbf2dfe490b988c3a4ae6d996d39d27c194a370f8cc48160191ab2d29",
            3, "turn_active", "10_59s", "object", 1, 1,
        )
        return replace(
            _worker_result(None), exit_code=1,
            error_code="worker_protocol_failed",
            primary_error_code="worker_protocol_failed",
            protocol_rejection=audit,
        )


def _worker_result(payload: dict[str, object] | None) -> CodexWorkerResult:
    encoded = b"" if payload is None else json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return CodexWorkerResult(
        exit_code=0,
        error_code="",
        primary_error_code="",
        cleanup_error_code="",
        pid=os.getpid(),
        process_start_identity=_read_process_start_identity(os.getpid()),
        stdout_sha256="0" * 64,
        stderr_sha256="0" * 64,
        event_count=0,
        final_response=payload,
        final_response_sha256=hashlib.sha256(encoded).hexdigest() if encoded else "",
        final_response_validated=False,
        untrusted_final_response=payload is not None,
        canonical_final_response_sha256=hashlib.sha256(encoded).hexdigest() if encoded else "",
    )


class LocalAgentReviewSchemaTests(unittest.TestCase):
    def test_fields_invalid_exposes_only_bounded_top_level_shape(self) -> None:
        payload = {**_review_payload(), "future_key": "Bearer hidden-value"}

        with self.assertRaises(ReviewValidationFailure) as raised:
            parse_local_agent_review(json.dumps(payload).encode())

        self.assertEqual({
            "validation_code": "fields_invalid",
            "value_kind": "object",
            "known_fields_mask": 31,
            "field_count": 6,
        }, raised.exception.audit)
        self.assertNotIn("future_key", repr(raised.exception.audit))
        self.assertNotIn("hidden-value", repr(raised.exception.audit))

    def test_all_stable_worker_errors_classify_and_repository_round_trip(self) -> None:
        sink = _ReviewSink(type("Repository", (), {"append_event": lambda *args: None})(), 1, 2)  # type: ignore[arg-type]
        for code in sorted(STABLE_WORKER_ERROR_CODES):
            with self.subTest(code=code):
                result = replace(_worker_result(None), exit_code=1, error_code=code, primary_error_code=code)
                audit = _review_failure_audit(result, sink, 1.0)
                self.assertEqual(code, audit["error_code"])
                encoded = repository_module._encode_review_failure({"reason": "review_failed", **audit})
                decoded = repository_module._decode_safe_mapping(encoded)
                repository_module._validate_review_failure(decoded)
                self.assertEqual(code, decoded["error_code"])

    def test_review_failure_audit_maps_fake_worker_results_without_raw_data(self) -> None:
        sink = _ReviewSink(type("Repository", (), {"append_event": lambda *args: None})(), 1, 2)  # type: ignore[arg-type]
        sink.on_event({"type": "item.completed", "item_type": "error", "sequence_no": 1, "raw_line_sha256": "a" * 64})
        cases = (
            ("worker_process_failed", "worker_process_failed", 1),
            ("worker_timeout", "worker_timeout", -15),
        )
        for supplied, expected, returncode in cases:
            with self.subTest(supplied=supplied):
                result = replace(
                    _worker_result(None),
                    exit_code=returncode,
                    error_code=supplied,
                    primary_error_code=supplied,
                    stderr_sha256="b" * 64,
                )
                audit = _review_failure_audit(result, sink, 114.2)
                self.assertEqual(expected, audit["error_code"])
                self.assertEqual(returncode, audit["process_returncode"])
                self.assertEqual("60_179s", audit["elapsed_bucket"])
                self.assertEqual({"type": "item.completed", "item_type": "error"}, audit["terminal_shape"])
                rendered = json.dumps(audit, sort_keys=True)
                self.assertNotIn("Bearer", rendered)
                self.assertNotIn("x" * 16, rendered)
                self.assertNotIn("thread_id", rendered)
        unknown = replace(
            _worker_result(None), exit_code=None,
            error_code="not-allowlisted Bearer " + "x" * 48,
            primary_error_code="not-allowlisted Bearer " + "x" * 48,
        )
        with self.assertRaisesRegex(ValueError, "local_agent_review_failed"):
            _review_failure_audit(unknown, sink, 114.2)

    def test_review_failure_audit_rejects_invalid_digest_returncode_and_shape(self) -> None:
        sink = _ReviewSink(type("Repository", (), {"append_event": lambda *args: None})(), 1, 2)  # type: ignore[arg-type]
        result = replace(_worker_result(None), stderr_sha256="not-a-digest")
        with self.assertRaisesRegex(ValueError, "local_agent_review_failed"):
            _review_failure_audit(result, sink, 1.0)
        inconsistent = (
            replace(_worker_result(None), error_code="worker_process_failed", primary_error_code="worker_timeout", exit_code=1),
            replace(_worker_result(None), error_code="", primary_error_code="", cleanup_error_code="worker_cleanup_reap_failed", exit_code=0),
            replace(_worker_result(None), error_code="worker_cleanup_failed", primary_error_code="", cleanup_error_code="unknown_cleanup", exit_code=1),
            replace(_worker_result(None), error_code="unknown_supplied", primary_error_code="", cleanup_error_code="", exit_code=1),
            replace(_worker_result(None), error_code="worker_process_failed", primary_error_code="", cleanup_error_code="", exit_code=1),
        )
        for result in inconsistent:
            with self.subTest(result=result):
                with self.assertRaisesRegex(ValueError, "local_agent_review_failed"):
                    _review_failure_audit(result, sink, 1.0)

        cleanup = replace(
            _worker_result(None), error_code="worker_cleanup_failed", primary_error_code="",
            cleanup_error_code="worker_cleanup_reap_failed", exit_code=0,
        )
        self.assertEqual("worker_cleanup_failed", _review_failure_audit(cleanup, sink, 1.0)["error_code"])
        generic_cleanup = replace(
            _worker_result(None), error_code="worker_cleanup_failed", primary_error_code="",
            cleanup_error_code="worker_cleanup_failed", exit_code=0,
        )
        self.assertEqual("worker_cleanup_failed", _review_failure_audit(generic_cleanup, sink, 1.0)["error_code"])
        primary_and_cleanup = replace(
            _worker_result(None), error_code="worker_timeout", primary_error_code="worker_timeout",
            cleanup_error_code="worker_cleanup_reap_failed", exit_code=None,
        )
        self.assertEqual("worker_timeout", _review_failure_audit(primary_and_cleanup, sink, 1.0)["error_code"])
        self.assertEqual("over_360s", _review_failure_audit(_worker_result(None), sink, 361.0)["elapsed_bucket"])
        result = replace(_worker_result(None), exit_code=9999)
        with self.assertRaisesRegex(ValueError, "local_agent_review_failed"):
            _review_failure_audit(result, sink, 1.0)

    def test_review_sink_persists_reduced_event_without_opaque_identifiers(self) -> None:
        captured: list[tuple[object, ...]] = []

        class Repository:
            def append_event(self, *arguments):
                captured.append(arguments)

        sink = _ReviewSink(Repository(), 1, 2)  # type: ignore[arg-type]
        sink.on_event({
            "type": "thread.started",
            "thread_id": "019c9d85-1d4c-7123-8f2a-123456789abc",
            "sequence_no": 1,
            "raw_line_sha256": "1234567890abcdef" * 4,
        })

        self.assertEqual({
            "type": "thread.started",
            "sequence_no": 1,
            "raw_line_digest": "sha256:" + "1234567890abcdef" * 4,
        }, captured[0][3])

    def test_exact_approved_schema_is_canonical(self) -> None:
        payload = _review_payload()
        result = parse_local_agent_review(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        self.assertEqual("approved", result.verdict)
        self.assertEqual(payload["review_hash"], result.review_hash)
        self.assertEqual((), result.findings)

    def test_schema_rejects_duplicate_nonfinite_secret_and_invalid_finding_shapes(self) -> None:
        cases: list[bytes] = [
            b'{"schema_version":"his-local-agent-review.v1","verdict":"approved","findings":[],"summary":"ok","summary":"duplicate","review_hash":"' + b"0" * 64 + b'"}',
            b'{"schema_version":"his-local-agent-review.v1","verdict":"approved","findings":[],"summary":1e999,"review_hash":"' + b"0" * 64 + b'"}',
        ]
        mutations = [
            {"summary": "Bearer " + "a" * 48},
            {"findings": [{"severity": "warning", "path": "calculator.py", "line": 1, "message": "bad"}]},
            {"findings": [{"severity": "important", "path": "../outside.py", "line": 1, "message": "bad"}]},
            {"findings": [{"severity": "important", "path": "calculator.py", "line": 0, "message": "bad"}]},
            {"findings": [{"severity": "important", "path": "calculator.py", "line": 1, "message": "x" * 4001}]},
            {"findings": [{"severity": "minor", "path": f"file-{index}.py", "line": 1, "message": "bad"} for index in range(33)]},
            {"extra": True},
        ]
        for mutation in mutations:
            payload = _review_payload()
            payload.update(mutation)
            if "review_hash" in payload:
                payload["review_hash"] = canonical_review_hash({key: value for key, value in payload.items() if key != "review_hash"})
            cases.append(json.dumps(payload, allow_nan=True).encode())
        for raw in cases:
            with self.subTest(raw=raw[:80]):
                with self.assertRaisesRegex(ValueError, "local_agent_review_invalid"):
                    parse_local_agent_review(raw)

    def test_schema_rejects_noncanonical_posix_paths(self) -> None:
        for path in ("calculator.py//child.py", "calculator.py/./child.py", "calculator.py/", "/calculator.py", "calculator.py\\child.py", "calculator.py\x00child.py"):
            payload = _review_payload("changes_requested")
            payload["findings"] = [{"severity": "important", "path": path, "line": 1, "message": "Invalid spelling."}]
            payload["review_hash"] = canonical_review_hash(payload)
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "local_agent_review_invalid"):
                    parse_local_agent_review(json.dumps(payload).encode())


class LocalAgentReviewIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="his_harness_stage_f_review_", dir="/private/tmp")
        self.root = Path(self.tmp.name)
        database.DB_PATH = self.root / "harness.sqlite"
        database.init_db()
        self.repository = LocalAgentRunRepository(database.DB_PATH)
        self.project = self.root / "project"
        self.project.mkdir()
        self._git("init")
        self._git("config", "user.email", "harness@example.test")
        self._git("config", "user.name", "Harness Test")
        (self.project / "calculator.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        (self.project / "test_calculator.py").write_text(
            "import unittest\nfrom calculator import add\n\nclass CalculatorTests(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(3, add(1, 2))\n",
            encoding="utf-8",
        )
        self._git("add", ".")
        self._git("commit", "-m", "initial")
        self.worktree_root = Path(tempfile.mkdtemp(prefix="his_harness_stage_f_review_worktree_", dir="/private/tmp"))

    def tearDown(self) -> None:
        subprocess.run(["git", "worktree", "prune"], cwd=self.project, check=False, capture_output=True)
        import shutil

        if self.worktree_root.exists():
            shutil.rmtree(self.worktree_root)
        self.tmp.cleanup()

    def _git(self, *arguments: str) -> None:
        subprocess.run(["git", *arguments], cwd=self.project, check=True, capture_output=True)

    def _task(self, *, allowed_paths: list[str] | None = None, task_key: str = "fixture-review-1"):
        payload = {
            "schema_version": "his-local-agent-task.v1",
            "task_key": task_key,
            "project_path": str(self.project),
            "request": "Fix add so the supplied unit test passes.",
            "allowed_paths": ["calculator.py"] if allowed_paths is None else allowed_paths,
            "verification_commands": [[sys.executable, "-m", "unittest", "-q", "test_calculator"]],
            "acceptance_criteria": ["The existing test passes."],
            "timeout_seconds": 30,
        }
        path = self.root / "task.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return load_local_agent_task(path)

    def _run(self, review_worker: _ReviewWorker):
        reviewer = LocalAgentReviewer(repository=self.repository, worker=review_worker, artifact_root=self.worktree_root)
        runner = LocalAgentRunner(repository=self.repository, worker=_CodeWorker(), reviewer=reviewer, worktree_root=self.worktree_root)
        return runner.execute(
            self._task(),
            assert_local_agent_run_allowed(allow_real_agent=True, authorization_id="task-five-review-authorization"),
        )

    def _reviewing_snapshot(
        self,
        authorization_label: str = "task-five-reviewing-authorization",
        task_key: str = "fixture-review-1",
    ):
        reviewer = LocalAgentReviewer(repository=self.repository, worker=_ReviewWorker([_review_payload()]), artifact_root=self.worktree_root)
        runner = LocalAgentRunner(repository=self.repository, worker=_CodeWorker(), reviewer=reviewer, worktree_root=self.worktree_root)
        verification = [{
            "index": 0, "returncode": 0, "timed_out": False,
            "cleanup": "not_needed", "duration_ms": 1,
            "stdout_sha256": "0" * 64, "stderr_sha256": "0" * 64,
            "side_effect": False,
        }]
        with patch.object(LocalAgentRunner, "_verify", return_value=verification), patch.object(
            LocalAgentRunner,
            "_review",
            lambda current, run_id, attempt_id, binding, change, verification, learning_checks=(): current._result(run_id, change, verification),
        ):
            snapshot = runner.execute(
                self._task(task_key=task_key),
                assert_local_agent_run_allowed(allow_real_agent=True, authorization_id=authorization_label),
            )
        self.assertEqual("reviewing", snapshot["run"]["status"])
        return snapshot

    def _prepared_finalization(
        self,
        *,
        issued_at: datetime | None = None,
        verdict: str = "approved",
        authorization_label: str = "task-five-reviewing-authorization",
        task_key: str = "fixture-review-1",
    ):
        snapshot = self._reviewing_snapshot(authorization_label, task_key)
        run_id = int(snapshot["run"]["id"])
        reviewer = LocalAgentReviewer(repository=self.repository, worker=_ReviewWorker([_review_payload(verdict)]), artifact_root=self.worktree_root)
        result = _seal_current_trees(reviewer, reviewer.review(run_id), self.project)
        arguments = {
            "run_id": run_id, "attempt_id": result.attempt_id,
            "expected_updated_at": result.run_revision,
            "expected_event_count": result.event_count,
            "verdict": result.verdict, "finding_count": len(result.findings),
            "pending_artifacts": result.pending_artifacts,
        }
        if issued_at is not None:
            arguments["now"] = issued_at
        capability = self.repository._prepare_review_finalization(**arguments)
        review_record = next(item for item in result.pending_artifacts if item["kind"] == "final_review")
        return run_id, result, capability, self.worktree_root / str(review_record["relative_path"])

    def test_sealed_approved_capability_is_required_for_pre_finalization_learning_observation(self) -> None:
        run_id, result, capability, _path = self._prepared_finalization()
        task = self._task(task_key="fixture-review-1")
        service = RepairLearningService(self.repository)
        service.record_reviewer_changes_requested(
            task=task,
            run_id=run_id,
            attempt_id=result.attempt_id,
            summary="bounded fixture finding",
        )

        for invalid in (object(), "not-a-capability"):
            with self.subTest(invalid=type(invalid).__name__), self.assertRaisesRegex(
                ValueError, "repair_learning_input_invalid"
            ):
                service.record_approved_review_success_observation(
                    task=task,
                    run_id=run_id,
                    attempt_id=result.attempt_id,
                    review_finalization_capability=invalid,
                )

        staged = service.record_approved_review_success_observation(
            task=task,
            run_id=run_id,
            attempt_id=result.attempt_id,
            review_finalization_capability=capability,
        )
        before_finalize = self.repository.snapshot(run_id)
        self.assertIsNotNone(staged)
        self.assertEqual("reviewing", before_finalize["run"]["status"])
        self.assertFalse(any(item["event_type"] == "review_finished" for item in before_finalize["events"]))
        self.assertEqual([], service.snapshot_for_run(run_id)["observations"])

        with self.assertRaisesRegex(ValueError, "repair_learning_input_invalid"):
            service.record_approved_review_success_observation(
                task=task,
                run_id=run_id,
                attempt_id=result.attempt_id,
                review_finalization_capability=capability,
            )
        competing_capability = self.repository._prepare_review_finalization(
            run_id=run_id,
            attempt_id=result.attempt_id,
            expected_updated_at=result.run_revision,
            expected_event_count=result.event_count,
            verdict=result.verdict,
            finding_count=len(result.findings),
            pending_artifacts=result.pending_artifacts,
        )
        with self.assertRaisesRegex(ValueError, "repair_learning_input_invalid"):
            self.repository.finalize_review(
                competing_capability,
                learning_observation=staged,
            )
        self.assertEqual("reviewing", self.repository.snapshot(run_id)["run"]["status"])
        self.repository.finalize_review(capability, learning_observation=staged)
        self.assertEqual("awaiting_human_confirmation", self.repository.snapshot(run_id)["run"]["status"])
        self.assertEqual(
            LearningRuleState.TRIAL.value,
            service.snapshot_for_run(run_id)["rules"][0]["state"],
        )
        self.assertEqual(1, len(service.snapshot_for_run(run_id)["observations"]))

    def test_changes_requested_finalization_capability_cannot_record_success(self) -> None:
        run_id, result, capability, _path = self._prepared_finalization(
            verdict="changes_requested",
            authorization_label="task-five-reviewing-stage-changes",
            task_key="fixture-review-stage-changes",
        )
        task = self._task(task_key="fixture-review-stage-changes")
        service = RepairLearningService(self.repository)
        service.record_reviewer_changes_requested(
            task=task,
            run_id=run_id,
            attempt_id=result.attempt_id,
            summary="bounded fixture finding",
        )
        with self.assertRaisesRegex(ValueError, "repair_learning_input_invalid"):
            service.record_approved_review_success_observation(
                task=task,
                run_id=run_id,
                attempt_id=result.attempt_id,
                review_finalization_capability=capability,
            )
        self.assertEqual("reviewing", self.repository.snapshot(run_id)["run"]["status"])
        self.assertEqual([], service.snapshot_for_run(run_id)["observations"])

    def test_expired_finalization_capability_cannot_record_success(self) -> None:
        run_id, result, capability, _path = self._prepared_finalization(
            issued_at=datetime.now(timezone.utc) - timedelta(seconds=301),
            authorization_label="task-five-reviewing-stage-expired",
            task_key="fixture-review-stage-expired",
        )
        task = self._task(task_key="fixture-review-stage-expired")
        service = RepairLearningService(self.repository)
        service.record_reviewer_changes_requested(
            task=task,
            run_id=run_id,
            attempt_id=result.attempt_id,
            summary="bounded fixture finding",
        )
        with self.assertRaisesRegex(ValueError, "repair_learning_input_invalid"):
            service.record_approved_review_success_observation(
                task=task,
                run_id=run_id,
                attempt_id=result.attempt_id,
                review_finalization_capability=capability,
            )
        self.assertEqual("reviewing", self.repository.snapshot(run_id)["run"]["status"])
        self.assertEqual([], service.snapshot_for_run(run_id)["observations"])

    def test_staged_success_observation_rolls_back_when_finalization_fails(self) -> None:
        run_id, result, capability, _path = self._prepared_finalization(
            authorization_label="task-five-reviewing-stage-atomic",
            task_key="fixture-review-stage-atomic",
        )
        task = self._task(task_key="fixture-review-stage-atomic")
        service = RepairLearningService(self.repository)
        service.record_reviewer_changes_requested(
            task=task,
            run_id=run_id,
            attempt_id=result.attempt_id,
            summary="bounded fixture finding",
        )
        staged = service.record_approved_review_success_observation(
            task=task,
            run_id=run_id,
            attempt_id=result.attempt_id,
            review_finalization_capability=capability,
        )
        self.assertIsNotNone(staged)
        original_append = repository_module._append_event_in_transaction

        def reject_finished_event(connection, current_run_id, current_attempt_id, event_type, payload):
            if event_type == "review_finished":
                raise database.sqlite3.OperationalError("injected finalization failure")
            return original_append(connection, current_run_id, current_attempt_id, event_type, payload)

        with patch.object(repository_module, "_append_event_in_transaction", side_effect=reject_finished_event):
            with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
                self.repository.finalize_review(capability, learning_observation=staged)

        after_failure = self.repository.snapshot(run_id)
        self.assertEqual("reviewing", after_failure["run"]["status"])
        self.assertFalse(any(item["event_type"] == "review_finished" for item in after_failure["events"]))
        self.assertFalse(any(item["kind"] in {"final_review", "review_seal"} for item in after_failure["artifacts"]))
        self.assertEqual([], service.snapshot_for_run(run_id)["observations"])
        self.repository.fail_review(run_id, result.attempt_id, "stale_review")
        self.assertEqual("failed_review", self.repository.snapshot(run_id)["run"]["status"])

    def _assert_finalize_rejects_without_db_writes(self, run_id: int, capability: object) -> None:
        before = self.repository.snapshot(run_id)
        with self.assertRaises(ValueError):
            self.repository.finalize_review(capability)
        after = self.repository.snapshot(run_id)
        self.assertEqual(before, after)
        self.assertEqual("reviewing", after["run"]["status"])
        self.assertFalse(any(item["event_type"] == "review_finished" for item in after["events"]))
        self.assertFalse(any(item["kind"] in {"final_review", "review_seal"} for item in after["artifacts"]))

    def test_approved_review_uses_read_only_role_and_persists_rehashed_manifest(self) -> None:
        worker = _ReviewWorker([_review_payload()])
        snapshot = self._run(worker)
        self.assertEqual("awaiting_human_confirmation", snapshot["run"]["status"])
        self.assertEqual("approved", snapshot["review"]["verdict"])
        self.assertEqual(WorkerRole.REVIEWER, worker.requests[0].role)
        self.assertIsNotNone(worker.requests[0].output_schema_path)
        self.assertIn(
            'Return one JSON object with exactly these five top-level fields and no others: '
            '"schema_version", "verdict", "findings", "summary", "review_hash".',
            worker.requests[0].prompt,
        )
        final_kinds = {"final_diff", "final_patch", "final_verification", "final_review", "final_manifest"}
        records = [item for item in snapshot["artifacts"] if item["kind"] in final_kinds]
        self.assertEqual(final_kinds, {item["kind"] for item in records})
        for item in records:
            path = self.worktree_root / str(item["relative_path"])
            self.assertFalse(path.is_symlink())
            content = path.read_bytes()
            self.assertEqual(item["sha256"], hashlib.sha256(content).hexdigest())
            self.assertEqual(item["size_bytes"], len(content))
        manifest_record = next(item for item in records if item["kind"] == "final_manifest")
        manifest = json.loads((self.worktree_root / str(manifest_record["relative_path"])).read_bytes())
        self.assertEqual("his-local-agent-artifact-manifest.v1", manifest["schema_version"])
        self.assertEqual(snapshot["change"]["patch_sha256"], manifest["inputs"]["worker_patch"]["sha256"])
        for key in ("initial_head", "current_head", "changed_paths", "changed_paths_sha256", "source_identity", "worktree_identity"):
            self.assertIn(key, manifest)

    def test_reviewer_uses_fixed_360_second_timeout_while_worker_keeps_contract_timeout(self) -> None:
        code_worker = _CodeWorker()
        review_worker = _ReviewWorker([_review_payload()])
        runner = LocalAgentRunner(
            repository=self.repository,
            worker=code_worker,
            reviewer=LocalAgentReviewer(repository=self.repository, worker=review_worker, artifact_root=self.worktree_root),
            worktree_root=self.worktree_root,
        )

        snapshot = runner.execute(
            self._task(),
            assert_local_agent_run_allowed(allow_real_agent=True, authorization_id="fixed-review-timeout-authorization"),
        )

        self.assertEqual(30, code_worker.requests[0].timeout_seconds)
        self.assertEqual(360, review_worker.requests[0].timeout_seconds)
        manifest_record = next(item for item in snapshot["artifacts"] if item["kind"] == "final_manifest")
        manifest = json.loads((self.worktree_root / str(manifest_record["relative_path"])).read_bytes())
        self.assertEqual({"reviewer": 360, "verification": 30, "worker": 30}, manifest["timeout_seconds"])

    def test_public_reviewer_reloads_authoritative_run_by_id(self) -> None:
        snapshot = self._reviewing_snapshot()
        patch_record = next(item for item in snapshot["artifacts"] if item["kind"] == "worker_patch")
        forged = dict(snapshot)
        forged["run"] = {**snapshot["run"], "initial_head": "f" * 40}
        forged["artifacts"] = [
            ({**item, "sha256": "f" * 64, "size_bytes": 1} if item["id"] == patch_record["id"] else item)
            for item in snapshot["artifacts"]
        ]
        reviewer = LocalAgentReviewer(repository=self.repository, worker=_ReviewWorker([_review_payload()]), artifact_root=self.worktree_root)
        result = reviewer.review(int(snapshot["run"]["id"]))
        self.assertEqual("approved", result.verdict)
        self.assertNotEqual("f" * 64, next(item for item in self.repository.snapshot(int(snapshot["run"]["id"]))["artifacts"] if item["kind"] == "worker_patch")["sha256"])

    def test_reviewer_renders_canonical_learning_focus_without_changing_review_schema(self) -> None:
        with patch(
            "app.local_agent_runner.run_local_agent_verification_argv",
            return_value=_successful_verification(),
        ):
            snapshot = self._reviewing_snapshot()
        run_id = int(snapshot["run"]["id"])
        task = self._task()
        rule = build_current_task_rule(
            derive_task_learning_context(task, run_id=run_id),
            actions=("reviewer_focus", "verification_replay"),
        )
        worker = _ReviewWorker([_review_payload()])
        reviewer = LocalAgentReviewer(
            repository=self.repository, worker=worker, artifact_root=self.worktree_root,
        )

        result = reviewer.review(run_id, learning_focus=(MatchedLearningRule(rule),))

        self.assertEqual(_review_payload()["review_hash"], result.review_hash)
        self.assertEqual(
            {"schema_version", "verdict", "findings", "summary", "review_hash"},
            set(result.as_mapping()),
        )
        prompt = worker.requests[0].prompt
        self.assertIn("FIXED_LEARNING_REVIEW_FOCUS_BEGIN", prompt)
        self.assertIn("reviewer_focus", prompt)
        self.assertIn("verification_replay", prompt)
        self.assertNotIn("repair-retrospective", prompt)

    def test_reviewer_rejects_unmatched_or_noncanonical_learning_focus(self) -> None:
        with patch(
            "app.local_agent_runner.run_local_agent_verification_argv",
            return_value=_successful_verification(),
        ):
            snapshot = self._reviewing_snapshot()
        run_id = int(snapshot["run"]["id"])
        task = self._task()
        context = derive_task_learning_context(task, run_id=run_id)
        valid = build_current_task_rule(context)
        suspended = build_current_task_rule(context, state=LearningRuleState.SUSPENDED)
        reviewer = LocalAgentReviewer(
            repository=self.repository,
            worker=_ReviewWorker([_review_payload(), _review_payload(), _review_payload()]),
            artifact_root=self.worktree_root,
        )
        for focus in (
            {},
            {"actions": ["reviewer_focus"]},
            (MatchedLearningRule(valid, outcome=RuleObservationOutcome.NOT_MATCHED),),
            (MatchedLearningRule(suspended),),
        ):
            with self.subTest(focus=focus):
                with self.assertRaisesRegex(ValueError, "local_agent_review_invalid"):
                    _review_learning_actions_for_test(task, run_id, focus)

    def test_reviewer_rejects_active_learning_focus_from_another_run(self) -> None:
        with patch(
            "app.local_agent_runner.run_local_agent_verification_argv",
            return_value=_successful_verification(),
        ):
            snapshot = self._reviewing_snapshot()
        run_id = int(snapshot["run"]["id"])
        task = self._task()
        foreign = build_current_task_rule(
            derive_task_learning_context(task, run_id=run_id + 1),
            actions=("reviewer_focus",),
        )

        with self.assertRaisesRegex(ValueError, "local_agent_review_invalid"):
            _review_learning_actions_for_test(task, run_id, (MatchedLearningRule(foreign),))

    def test_changes_requested_is_retryable_and_never_confirms_first_review(self) -> None:
        worker = _ReviewWorker([_review_payload("changes_requested"), _review_payload()])
        first = self._run(worker)
        self.assertEqual("changes_requested", first["run"]["status"])
        runner = LocalAgentRunner(
            repository=self.repository,
            worker=_CodeWorker(),
            reviewer=LocalAgentReviewer(repository=self.repository, worker=worker, artifact_root=self.worktree_root),
            worktree_root=self.worktree_root,
        )
        second = runner.retry(int(first["run"]["id"]))
        self.assertEqual("awaiting_human_confirmation", second["run"]["status"])
        self.assertEqual([1, 2], [item["attempt_no"] for item in second["attempts"]])

    def test_secret_review_fails_closed(self) -> None:
        payload = _review_payload(summary="Bearer " + "a" * 48)
        snapshot = self._run(_ReviewWorker([payload]))
        self.assertEqual("failed_review", snapshot["run"]["status"])
        event = next(item for item in snapshot["events"] if item["event_type"] == "review_failed")
        self.assertEqual("summary_invalid", event["payload"]["validation_code"])

    def _assert_review_validation_code(self, payload, expected) -> None:
        snapshot = self._run(_ReviewWorker([payload]))
        event = next(item for item in snapshot["events"] if item["event_type"] == "review_failed")
        self.assertEqual(expected, event["payload"]["validation_code"])
        self.assertNotIn("hidden-value", repr(event["payload"]))

    def test_extra_review_fields_persist_only_fixed_stage_code(self) -> None:
        self._assert_review_validation_code({**_review_payload(), "extra": "hidden-value"}, "fields_invalid")

    def test_invalid_review_verdict_persists_only_fixed_stage_code(self) -> None:
        self._assert_review_validation_code({**_review_payload(), "verdict": "future"}, "verdict_invalid")

    def test_invalid_review_hash_persists_only_fixed_stage_code(self) -> None:
        self._assert_review_validation_code({**_review_payload(), "review_hash": "0" * 64}, "review_hash_invalid")

    def test_worker_response_digest_mismatch_fails_closed(self) -> None:
        snapshot = self._run(_DigestMismatchReviewWorker([_review_payload()]))
        self.assertEqual("failed_review", snapshot["run"]["status"])

    def test_worker_failure_persists_only_safe_classification(self) -> None:
        class FailedWorker(_ReviewWorker):
            def start(self, request, sink):
                sink.on_started(os.getpid(), _read_process_start_identity(os.getpid()))
                sink.on_event({"type": "item.completed", "item_type": "error", "sequence_no": 1, "raw_line_sha256": "c" * 64})
                return replace(
                    _worker_result(None),
                    exit_code=1,
                    error_code="worker_process_failed",
                    primary_error_code="worker_process_failed",
                    stderr_sha256="d" * 64,
                    event_count=1,
                )

        verification = [{
            "index": 0, "returncode": 0, "timed_out": False, "cleanup": "not_needed",
            "duration_ms": 1, "stdout_sha256": "0" * 64, "stderr_sha256": "0" * 64,
            "side_effect": False,
        }]
        with patch.object(LocalAgentRunner, "_verify", return_value=verification):
            snapshot = self._run(FailedWorker([]))
        self.assertEqual("failed_review", snapshot["run"]["status"], snapshot)
        event = next(item for item in snapshot["events"] if item["event_type"] == "review_failed")
        self.assertEqual("worker_process_failed", event["payload"]["error_code"])
        self.assertEqual(1, event["payload"]["process_returncode"])
        self.assertEqual("d" * 64, event["payload"]["stderr_sha256"])
        self.assertEqual({"type": "item.completed", "item_type": "error"}, event["payload"]["terminal_shape"])
        self.assertNotIn("detail", event["payload"])
        self.assertNotIn("path", event["payload"])

    def test_review_failure_accepts_real_mixed_sha256_without_secret_redaction(self) -> None:
        snapshot = self._reviewing_snapshot()
        run_id = int(snapshot["run"]["id"])
        attempt_id = int(snapshot["attempts"][-1]["id"])
        digest = "1631f96cbf2dfe490b988c3a4ae6d996d39d27c194a370f8cc48160191ab2d29"
        audit = {
            "error_code": "worker_process_failed",
            "process_returncode": 1,
            "stdout_sha256": digest,
            "stderr_sha256": digest,
            "terminal_shape": {"type": "error"},
            "elapsed_bucket": "60_179s",
        }

        result = self.repository.fail_review(run_id, attempt_id, audit=audit)

        self.assertEqual("failed_review", result["status"])
        event = self.repository.snapshot(run_id)["events"][-1]
        self.assertEqual(digest, event["payload"]["stdout_sha256"])

    def test_reviewer_protocol_failed_persists_bounded_turn_failure_audit(self) -> None:
        verification = [{
            "index": 0, "returncode": 0, "timed_out": False, "cleanup": "not_needed",
            "duration_ms": 1, "stdout_sha256": "0" * 64, "stderr_sha256": "0" * 64,
            "side_effect": False,
        }]
        with patch.object(LocalAgentRunner, "_verify", return_value=verification):
            snapshot = self._run(_ProtocolFailedReviewWorker([]))

        self.assertEqual("failed_review", snapshot["run"]["status"])
        event = next(
            (item for item in snapshot["events"] if item["event_type"] == "worker_protocol_rejected"),
            None,
        )
        self.assertIsNotNone(event, snapshot)
        self.assertEqual("turn.failed", event["payload"]["candidate_event_type"])
        self.assertNotIn("message", repr(event["payload"]))

    def test_review_failure_snapshot_rejects_polluted_historical_payloads(self) -> None:
        verification = [{
            "index": 0, "returncode": 0, "timed_out": False, "cleanup": "not_needed",
            "duration_ms": 1, "stdout_sha256": "0" * 64, "stderr_sha256": "0" * 64,
            "side_effect": False,
        }]
        with patch.object(LocalAgentRunner, "_verify", return_value=verification):
            snapshot = self._reviewing_snapshot()
        run_id = int(snapshot["run"]["id"])
        attempt_id = int(snapshot["attempts"][-1]["id"])
        self.repository.fail_review(run_id, attempt_id, "stale_review")
        self.assertEqual("stale_review", self.repository.snapshot(run_id)["events"][-1]["payload"]["reason"])
        for index, payload in enumerate((
            {"reason": "/private/tmp/leak"},
            {"reason": "x" * 512},
            {"reason": "review_failed", "detail": "Bearer " + "x" * 48},
        )):
            with self.subTest(payload=payload):
                task = self._task(task_key=f"fixture-review-pollution-{index}")
                reviewer = LocalAgentReviewer(repository=self.repository, worker=_ReviewWorker([_review_payload()]), artifact_root=self.worktree_root)
                runner = LocalAgentRunner(repository=self.repository, worker=_CodeWorker(), reviewer=reviewer, worktree_root=self.worktree_root)
                with patch.object(LocalAgentRunner, "_verify", return_value=verification), patch.object(LocalAgentRunner, "_review", lambda current, run_id, attempt_id, binding, change, items: current._result(run_id, change, items)):
                    polluted = runner.execute(task, assert_local_agent_run_allowed(allow_real_agent=True, authorization_id=f"task-five-pollution-{index}"))
                polluted_run_id = int(polluted["run"]["id"])
                polluted_attempt_id = int(polluted["attempts"][-1]["id"])
                with self.repository._connect() as connection:
                    sequence = connection.execute("select max(sequence_no)+1 from local_agent_run_events where run_id=?", (polluted_run_id,)).fetchone()[0]
                    connection.execute(
                        "insert into local_agent_run_events(run_id,attempt_id,sequence_no,event_type,payload_json,created_at) values(?,?,?,?,?,?)",
                        (polluted_run_id, polluted_attempt_id, sequence, "review_failed", json.dumps(payload, separators=(",", ":")), "2026-08-12T00:00:00+08:00"),
                    )
                with self.assertRaises(ValueError) as raised:
                    self.repository.snapshot(polluted_run_id)
                self.assertEqual("local_agent_storage_invalid", str(raised.exception))
                with self.repository._connect() as connection:
                    connection.execute("delete from local_agent_project_leases where run_id=?", (polluted_run_id,))

    def test_malformed_review_hash_fails_closed(self) -> None:
        payload = _review_payload()
        payload["review_hash"] = "0" * 64
        snapshot = self._run(_ReviewWorker([payload]))
        self.assertEqual("failed_review", snapshot["run"]["status"])

    def test_finding_outside_allowed_paths_fails_closed(self) -> None:
        payload = _review_payload("changes_requested")
        payload["findings"] = [{"severity": "important", "path": "test_calculator.py", "line": 1, "message": "Out of scope."}]
        payload["review_hash"] = canonical_review_hash(payload)
        snapshot = self._run(_ReviewWorker([payload]))
        self.assertEqual("failed_review", snapshot["run"]["status"])

    def test_finding_in_contract_but_not_in_durable_changed_paths_fails_closed(self) -> None:
        payload = _review_payload("changes_requested")
        payload["findings"] = [{"severity": "important", "path": "test_calculator.py", "line": 1, "message": "Not part of the patch."}]
        payload["review_hash"] = canonical_review_hash(payload)
        reviewer = LocalAgentReviewer(repository=self.repository, worker=_ReviewWorker([payload]), artifact_root=self.worktree_root)
        runner = LocalAgentRunner(repository=self.repository, worker=_CodeWorker(), reviewer=reviewer, worktree_root=self.worktree_root)
        snapshot = runner.execute(
            self._task(allowed_paths=["calculator.py", "test_calculator.py"]),
            assert_local_agent_run_allowed(allow_real_agent=True, authorization_id="task-five-durable-path-authorization"),
        )
        self.assertEqual("failed_review", snapshot["run"]["status"])

    def test_reviewer_worktree_mutation_fails_closed(self) -> None:
        snapshot = self._run(_ReviewWorker([_review_payload()], mutation="reviewer-write.txt"))
        self.assertEqual("failed_review", snapshot["run"]["status"])

    def test_reviewer_source_mutation_fails_closed(self) -> None:
        snapshot = self._run(_ReviewWorker([_review_payload()], source_mutation=self.project / "calculator.py"))
        self.assertEqual("failed_review", snapshot["run"]["status"])

    def test_artifact_tamper_during_review_fails_closed(self) -> None:
        worker = _ReviewWorker([_review_payload()])
        original_start = worker.start

        def tampering_start(request, sink):
            run_root = request.worktree_path.parent / ".harness_local_agent_control"
            target = next(run_root.glob("run_*/attempt_*/final.patch"))
            worker.tamper_relative = str(target.relative_to(request.worktree_path.parent))
            return original_start(request, sink)

        worker.start = tampering_start  # type: ignore[method-assign]
        snapshot = self._run(worker)
        self.assertEqual("failed_review", snapshot["run"]["status"])

    def test_persisted_worker_patch_tamper_during_review_fails_closed(self) -> None:
        worker = _ReviewWorker([_review_payload()])
        original_start = worker.start

        def tampering_start(request, sink):
            run_root = request.worktree_path.parent / ".harness_local_agent_control"
            target = next(run_root.glob("run_*/attempt_*.patch"))
            worker.tamper_relative = str(target.relative_to(request.worktree_path.parent))
            return original_start(request, sink)

        worker.start = tampering_start  # type: ignore[method-assign]
        snapshot = self._run(worker)
        self.assertEqual("failed_review", snapshot["run"]["status"])

    def test_reviewer_common_git_mutation_fails_closed(self) -> None:
        snapshot = self._run(_ReviewWorker([_review_payload()], source_mutation=self.project / ".git" / "reviewer-marker"))
        self.assertEqual("failed_review", snapshot["run"]["status"])

    def test_verification_artifact_write_failure_never_strands_reviewing(self) -> None:
        reviewer = LocalAgentReviewer(repository=self.repository, worker=_ReviewWorker([_review_payload()]), artifact_root=self.worktree_root)
        runner = LocalAgentRunner(repository=self.repository, worker=_CodeWorker(), reviewer=reviewer, worktree_root=self.worktree_root)
        original = runner._write_artifact

        def fail_verification(run_id, attempt_id, kind, content, leaf):
            if kind == "verification_manifest":
                raise OSError("injected")
            return original(run_id, attempt_id, kind, content, leaf)

        with patch.object(runner, "_write_artifact", side_effect=fail_verification):
            snapshot = runner.execute(
                self._task(),
                assert_local_agent_run_allowed(allow_real_agent=True, authorization_id="task-five-verification-artifact-failure"),
            )
        self.assertEqual("failed_verification", snapshot["run"]["status"])
        self.assertFalse(any(item["kind"] == "verification_manifest" for item in snapshot["artifacts"]))

    def test_late_final_patch_replacement_cannot_reach_confirmation(self) -> None:
        original = self.repository.finalize_review

        def replace_after_finalize(capability, **kwargs):
            result = original(capability, **kwargs)
            run_id = int(result["run"]["id"])
            record = next(item for item in self.repository.snapshot(run_id)["artifacts"] if item["kind"] == "final_patch")
            path = self.worktree_root / str(record["relative_path"])
            path.chmod(0o600)
            path.write_bytes(b"late replacement")
            return result

        with patch.object(LocalAgentRunner, "_verify", return_value=[{
            "index": 0, "returncode": 0, "timed_out": False,
            "cleanup": "not_needed", "duration_ms": 1,
            "stdout_sha256": "0" * 64, "stderr_sha256": "0" * 64,
            "side_effect": False,
        }]), patch.object(self.repository, "finalize_review", side_effect=replace_after_finalize):
            snapshot = self._run(_ReviewWorker([_review_payload()]))
        self.assertEqual("awaiting_human_confirmation", snapshot["run"]["status"])
        confirmation = LocalAgentConfirmationService(
            repository=self.repository, artifact_root=self.worktree_root,
        )
        with self.assertRaisesRegex(ValueError, "local_agent_confirmation_invalid"):
            confirmation.issue_local_apply_confirmation(int(snapshot["run"]["id"]), "local-user")

    def test_late_worktree_mutation_cannot_reach_confirmation(self) -> None:
        original = self.repository.finalize_review

        def mutate_after_finalize(capability, **kwargs):
            result = original(capability, **kwargs)
            run_id = int(result["run"]["id"])
            worktree = Path(str(self.repository.snapshot(run_id)["run"]["worktree_path"]))
            (worktree / "review-race.txt").write_text("late\n", encoding="utf-8")
            return result

        with patch.object(LocalAgentRunner, "_verify", return_value=[{
            "index": 0, "returncode": 0, "timed_out": False,
            "cleanup": "not_needed", "duration_ms": 1,
            "stdout_sha256": "0" * 64, "stderr_sha256": "0" * 64,
            "side_effect": False,
        }]), patch.object(self.repository, "finalize_review", side_effect=mutate_after_finalize):
            snapshot = self._run(_ReviewWorker([_review_payload()]))
        self.assertEqual("awaiting_human_confirmation", snapshot["run"]["status"])
        confirmation = LocalAgentConfirmationService(
            repository=self.repository, artifact_root=self.worktree_root,
        )
        with self.assertRaisesRegex(ValueError, "local_agent_confirmation_invalid"):
            confirmation.issue_local_apply_confirmation(int(snapshot["run"]["id"]), "local-user")

    def test_review_transition_and_event_failure_are_atomic(self) -> None:
        original = repository_module._append_event_in_transaction

        def fail_finished(connection, run_id, attempt_id, event_type, payload):
            if event_type == "review_finished":
                raise OSError("injected")
            return original(connection, run_id, attempt_id, event_type, payload)

        with patch.object(repository_module, "_append_event_in_transaction", side_effect=fail_finished):
            snapshot = self._run(_ReviewWorker([_review_payload()]))
        self.assertEqual("failed_review", snapshot["run"]["status"])
        self.assertFalse(any(item["event_type"] == "review_finished" for item in snapshot["events"]))
        self.assertFalse(any(item["kind"] in {"final_review", "review_seal"} for item in snapshot["artifacts"]))

    def test_concurrent_review_finalization_has_exactly_one_winner(self) -> None:
        snapshot = self._reviewing_snapshot()
        run_id = int(snapshot["run"]["id"])
        reviewer = LocalAgentReviewer(repository=self.repository, worker=_ReviewWorker([_review_payload()]), artifact_root=self.worktree_root)
        result = reviewer.review(run_id)
        result = _seal_current_trees(reviewer, result, self.project)
        capability = self.repository._prepare_review_finalization(
            run_id=run_id,
            attempt_id=result.attempt_id,
            expected_updated_at=result.run_revision,
            expected_event_count=result.event_count,
            verdict=result.verdict,
            finding_count=len(result.findings),
            pending_artifacts=result.pending_artifacts,
        )

        def finalize() -> bool:
            try:
                self.repository.finalize_review(capability)
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            winners = list(pool.map(lambda _index: finalize(), range(2)))
        self.assertEqual(1, winners.count(True))
        final = self.repository.snapshot(run_id)
        self.assertEqual(1, sum(item["event_type"] == "review_finished" for item in final["events"]))
        self.assertEqual({"final_review", "review_seal"}, {item["kind"] for item in final["artifacts"] if item["kind"] in {"final_review", "review_seal"}})

    def test_public_finalize_rejects_forged_artifact_facts_before_any_write(self) -> None:
        snapshot = self._reviewing_snapshot()
        run_id = int(snapshot["run"]["id"])
        attempt_id = int(snapshot["attempts"][-1]["id"])
        forged = (
            {"run_id": run_id, "attempt_id": attempt_id, "kind": "final_review", "relative_path": f".harness_local_agent_control/run_{run_id}/attempt_{attempt_id}/forged-review.json", "sha256": "1" * 64, "size_bytes": 1},
            {"run_id": run_id, "attempt_id": attempt_id, "kind": "review_seal", "relative_path": f".harness_local_agent_control/run_{run_id}/attempt_{attempt_id}/forged-seal.json", "sha256": "2" * 64, "size_bytes": 1},
        )
        with self.assertRaises((TypeError, ValueError)):
            self.repository.finalize_review(
                run_id=run_id,
                attempt_id=attempt_id,
                expected_updated_at=str(snapshot["run"]["updated_at"]),
                expected_event_count=len(snapshot["events"]),
                verdict="approved",
                finding_count=0,
                authoritative_artifacts=(),
                pending_artifacts=forged,
                integrity_check=lambda: None,
            )
        final = self.repository.snapshot(run_id)
        self.assertEqual("reviewing", final["run"]["status"])
        self.assertFalse(any(item["event_type"] == "review_finished" for item in final["events"]))
        self.assertFalse(any(item["kind"] in {"final_review", "review_seal"} for item in final["artifacts"]))

    def test_finalization_capability_rejects_copy_pickle_manual_and_cross_repository(self) -> None:
        snapshot = self._reviewing_snapshot()
        run_id = int(snapshot["run"]["id"])
        reviewer = LocalAgentReviewer(repository=self.repository, worker=_ReviewWorker([_review_payload()]), artifact_root=self.worktree_root)
        result = _seal_current_trees(reviewer, reviewer.review(run_id), self.project)
        capability = self.repository._prepare_review_finalization(
            run_id=run_id, attempt_id=result.attempt_id, expected_updated_at=result.run_revision,
            expected_event_count=result.event_count, verdict=result.verdict,
            finding_count=len(result.findings), pending_artifacts=result.pending_artifacts,
        )
        for operation in (
            lambda: copy.copy(capability), lambda: copy.deepcopy(capability),
            lambda: pickle.loads(pickle.dumps(capability)),
            lambda: dataclasses.replace(capability), lambda: type(capability)(),
        ):
            with self.assertRaises((TypeError, ValueError)):
                operation()
        manually_constructed = type(capability)(repository_module._FINALIZATION_ISSUER)
        other_path = self.root / "other.sqlite"
        database.DB_PATH = other_path
        database.init_db()
        other = LocalAgentRunRepository(other_path)
        for candidate in [manually_constructed, capability]:
            with self.subTest(candidate=type(candidate).__name__):
                with self.assertRaises(ValueError):
                    other.finalize_review(candidate)
        self.assertEqual("reviewing", self.repository.snapshot(run_id)["run"]["status"])

    def test_prepare_finalization_rejects_wrong_pending_owner_path_and_shape(self) -> None:
        snapshot = self._reviewing_snapshot()
        run_id = int(snapshot["run"]["id"])
        reviewer = LocalAgentReviewer(repository=self.repository, worker=_ReviewWorker([_review_payload()]), artifact_root=self.worktree_root)
        result = _seal_current_trees(reviewer, reviewer.review(run_id), self.project)
        review, seal = result.pending_artifacts
        cases = (
            ({**review, "relative_path": str(review["relative_path"]) + ".forged"}, seal),
            ({**review, "run_id": run_id + 1}, seal),
            (review, {**seal, "attempt_id": result.attempt_id + 1}),
            ({**review, "sha256": "f" * 64}, seal),
            (review, {**seal, "size_bytes": int(seal["size_bytes"]) + 1}),
            (review, {**seal, "kind": "final_review"}),
            (review,),
            (review, seal, seal),
        )
        for pending in cases:
            with self.subTest(pending=pending):
                with self.assertRaises(ValueError):
                    self.repository._prepare_review_finalization(
                        run_id=run_id, attempt_id=result.attempt_id,
                        expected_updated_at=result.run_revision,
                        expected_event_count=result.event_count,
                        verdict=result.verdict, finding_count=len(result.findings),
                        pending_artifacts=pending,
                    )
        final = self.repository.snapshot(run_id)
        self.assertEqual("reviewing", final["run"]["status"])
        self.assertFalse(any(item["event_type"] == "review_finished" for item in final["events"]))

    def test_successful_finalization_capability_cannot_be_reused(self) -> None:
        snapshot = self._reviewing_snapshot()
        run_id = int(snapshot["run"]["id"])
        reviewer = LocalAgentReviewer(repository=self.repository, worker=_ReviewWorker([_review_payload()]), artifact_root=self.worktree_root)
        result = _seal_current_trees(reviewer, reviewer.review(run_id), self.project)
        capability = self.repository._prepare_review_finalization(
            run_id=run_id, attempt_id=result.attempt_id,
            expected_updated_at=result.run_revision, expected_event_count=result.event_count,
            verdict=result.verdict, finding_count=len(result.findings),
            pending_artifacts=result.pending_artifacts,
        )
        self.repository.finalize_review(capability)
        before = self.repository.snapshot(run_id)
        with self.assertRaises(ValueError):
            self.repository.finalize_review(capability)
        self.assertEqual(before, self.repository.snapshot(run_id))

    def test_finalize_rejects_review_byte_change_after_capability_issue(self) -> None:
        run_id, _result, capability, path = self._prepared_finalization()
        content = bytearray(path.read_bytes())
        content[-2] ^= 1
        path.chmod(0o600)
        path.write_bytes(bytes(content))
        path.chmod(0o400)
        self._assert_finalize_rejects_without_db_writes(run_id, capability)

    def test_finalize_rejects_review_inode_replacement_after_capability_issue(self) -> None:
        run_id, _result, capability, path = self._prepared_finalization()
        replacement = path.with_name("replacement-review.json")
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(0o400)
        os.replace(replacement, path)
        self._assert_finalize_rejects_without_db_writes(run_id, capability)

    def test_finalize_rejects_review_symlink_after_capability_issue(self) -> None:
        run_id, _result, capability, path = self._prepared_finalization()
        outside = self.root / "review-copy.json"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
        self._assert_finalize_rejects_without_db_writes(run_id, capability)

    def test_finalize_rejects_review_truncate_after_capability_issue(self) -> None:
        run_id, _result, capability, path = self._prepared_finalization()
        path.chmod(0o600)
        path.write_bytes(path.read_bytes()[:-1])
        path.chmod(0o400)
        self._assert_finalize_rejects_without_db_writes(run_id, capability)

    def test_finalize_rejects_review_extend_after_capability_issue(self) -> None:
        run_id, _result, capability, path = self._prepared_finalization()
        path.chmod(0o600)
        path.write_bytes(path.read_bytes() + b" ")
        path.chmod(0o400)
        self._assert_finalize_rejects_without_db_writes(run_id, capability)

    def test_finalize_rejects_authoritative_patch_change_after_capability_issue(self) -> None:
        run_id, _result, capability, _path = self._prepared_finalization()
        record = next(item for item in self.repository.snapshot(run_id)["artifacts"] if item["kind"] == "final_patch")
        path = self.worktree_root / str(record["relative_path"])
        content = bytearray(path.read_bytes())
        content[0] ^= 1
        path.chmod(0o600)
        path.write_bytes(bytes(content))
        path.chmod(0o400)
        self._assert_finalize_rejects_without_db_writes(run_id, capability)

    def test_expired_capability_rejects_without_db_state_change(self) -> None:
        issued_at = datetime.now(timezone.utc) - timedelta(minutes=6)
        run_id, _result, capability, _path = self._prepared_finalization(issued_at=issued_at)
        self._assert_finalize_rejects_without_db_writes(run_id, capability)

    def test_prepare_finalization_rejects_extra_authoritative_kind_before_verdict_writes(self) -> None:
        snapshot = self._reviewing_snapshot()
        run_id = int(snapshot["run"]["id"])
        reviewer = LocalAgentReviewer(repository=self.repository, worker=_ReviewWorker([_review_payload()]), artifact_root=self.worktree_root)
        result = _seal_current_trees(reviewer, reviewer.review(run_id), self.project)
        self.repository.add_artifact(
            run_id, result.attempt_id, "final_patch",
            f".harness_local_agent_control/run_{run_id}/attempt_{result.attempt_id}/extra-final.patch",
            "a" * 64, 0,
        )
        with self.assertRaises(ValueError):
            self.repository._prepare_review_finalization(
                run_id=run_id, attempt_id=result.attempt_id,
                expected_updated_at=result.run_revision, expected_event_count=result.event_count,
                verdict=result.verdict, finding_count=len(result.findings),
                pending_artifacts=result.pending_artifacts,
            )
        final = self.repository.snapshot(run_id)
        self.assertEqual("reviewing", final["run"]["status"])
        self.assertFalse(any(item["event_type"] == "review_finished" for item in final["events"]))
        self.assertFalse(any(item["kind"] in {"final_review", "review_seal"} for item in final["artifacts"]))

    def test_finalization_capability_is_one_time_and_stale_state_fails_before_writes(self) -> None:
        snapshot = self._reviewing_snapshot()
        run_id = int(snapshot["run"]["id"])
        reviewer = LocalAgentReviewer(repository=self.repository, worker=_ReviewWorker([_review_payload()]), artifact_root=self.worktree_root)
        result = _seal_current_trees(reviewer, reviewer.review(run_id), self.project)
        capability = self.repository._prepare_review_finalization(
            run_id=run_id, attempt_id=result.attempt_id, expected_updated_at=result.run_revision,
            expected_event_count=result.event_count, verdict=result.verdict,
            finding_count=len(result.findings), pending_artifacts=result.pending_artifacts,
        )
        self.repository.fail_review(run_id, result.attempt_id, "stale_review")
        before = self.repository.snapshot(run_id)
        with self.assertRaises(ValueError):
            self.repository.finalize_review(capability)
        with self.assertRaises(ValueError):
            self.repository.finalize_review(capability)
        after = self.repository.snapshot(run_id)
        self.assertEqual(before, after)

    def test_preexisting_symlink_artifact_target_fails_closed(self) -> None:
        worker = _ReviewWorker([_review_payload()])
        original_start = worker.start

        def precreate_before_artifacts(request, sink):
            return original_start(request, sink)

        worker.start = precreate_before_artifacts  # type: ignore[method-assign]
        # The deterministic first attempt path lets the test plant the exact output target.
        run_dir = self.worktree_root / ".harness_local_agent_control" / "run_1" / "attempt_1"
        run_dir.mkdir(parents=True)
        (run_dir / "final.diff").symlink_to(self.root / "outside")
        snapshot = self._run(worker)
        self.assertEqual("failed_review", snapshot["run"]["status"])


if __name__ == "__main__":
    unittest.main()
