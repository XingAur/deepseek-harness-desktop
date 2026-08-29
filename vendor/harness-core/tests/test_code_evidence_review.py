from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import database
from app.code_evidence_artifacts import EvidenceArtifactRecord, EvidenceArtifactStore
from app.code_evidence_git import GitDiffEvidenceService
from app.code_evidence_repository import CodeEvidenceRepository
from app.code_evidence_review import CodeEvidenceReviewService
from app.code_evidence_verification import CodeEvidenceVerificationService
from app.local_agent_review import canonical_review_hash
from app.repository_scope import RepositoryScope


class _ReviewerWorker:
    def __init__(self, response: dict[str, object], *, mutate: bool = False) -> None:
        self.response = response
        self.mutate = mutate
        self.calls = 0

    def start(self, request, sink):
        self.calls += 1
        sink.on_started(123, "darwin-proc-bsdinfo-v1:123:456")
        sink.on_event({"type": "thread.started", "sequence_no": 1, "raw_line_sha256": "1" * 64})
        sink.on_event({"type": "turn.started", "sequence_no": 2, "raw_line_sha256": "2" * 64})
        sink.on_event({"type": "item.completed", "item_type": "agent_message", "sequence_no": 3, "raw_line_sha256": "3" * 64})
        sink.on_event({"type": "turn.completed", "sequence_no": 4, "raw_line_sha256": "4" * 64})
        if self.mutate:
            (request.worktree_path / "reviewer-side-effect.txt").write_text("bad", encoding="utf-8")
        raw = json.dumps(self.response, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(raw).hexdigest()
        return SimpleNamespace(
            pid=123,
            process_start_identity="darwin-proc-bsdinfo-v1:123:456",
            exit_code=0,
            error_code="",
            primary_error_code="",
            cleanup_error_code="",
            final_response=self.response,
            final_response_sha256=digest,
            canonical_final_response_sha256=digest,
            final_response_validated=False,
            untrusted_final_response=True,
            protocol_rejection=None,
        )


class CodeEvidenceReviewServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="his_harness_code_review_")
        root = Path(self.temp_dir.name)
        self.repo_path = root / "repo"
        self.repo_path.mkdir()
        self._git("init")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "Harness Test")
        (self.repo_path / "calculator.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        (self.repo_path / "test_calculator.py").write_text(
            "import unittest\nfrom calculator import add\n\n"
            "class CalculatorTests(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(5, add(2, 3))\n",
            encoding="utf-8",
        )
        self._git("add", ".")
        self._git("commit", "-m", "baseline")
        (self.repo_path / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = root / "manager.sqlite"
        self.repository = CodeEvidenceRepository()
        self.store = EvidenceArtifactStore(root / "evidence")
        self.scope = RepositoryScope("repo-a", self.repo_path)
        scopes = {"repo-a": self.scope}
        self.diff = GitDiffEvidenceService(self.repository, self.store, scopes).capture(
            repository_alias="repo-a", bundle_key="diff-a", conversation_key="conversation-a", task_key="task-a"
        )
        self.verification = CodeEvidenceVerificationService(
            self.repository, self.store, scopes, command_runner=self._verification_runner
        ).verify(
            diff_bundle_id=int(self.diff["bundle_id"]), bundle_key="verify-a",
            conversation_key="conversation-a", task_key="task-a",
            commands=((sys.executable, "-m", "unittest", "-q", "test_calculator"),), timeout_seconds=30,
        )

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def _git(self, *arguments: str) -> str:
        return subprocess.run(
            ["/usr/bin/git", "-C", str(self.repo_path), *arguments], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout

    @staticmethod
    def _verification_runner(command, *, cwd, timeout, source_path=None):
        return {
            "returncode": 0, "timed_out": False, "cleanup": "not_needed", "duration_ms": 3,
            "stdout_sha256": "a" * 64, "stderr_sha256": "b" * 64,
        }

    @staticmethod
    def _response(verdict: str = "approved", findings: list[dict[str, object]] | None = None) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": "his-local-agent-review.v1",
            "verdict": verdict,
            "findings": [] if findings is None else findings,
            "summary": "The frozen patch and verification evidence are consistent.",
        }
        value["review_hash"] = canonical_review_hash(value)
        return value

    def _service(self, worker) -> CodeEvidenceReviewService:
        return CodeEvidenceReviewService(
            self.repository, self.store, {"repo-a": self.scope}, worker=worker
        )

    def _review(self, worker) -> dict[str, object]:
        return self._service(worker).review(
            diff_bundle_id=int(self.diff["bundle_id"]),
            verification_bundle_id=int(self.verification["verification_bundle_id"]),
            bundle_key="review-a", conversation_key="conversation-a", task_key="task-a",
        )

    def test_approved_review_reopens_exact_inputs_and_persists_bound_review_bundle(self) -> None:
        worker = _ReviewerWorker(self._response())
        before = self._git("status", "--porcelain=v2", "--untracked-files=all")
        result = self._review(worker)

        self.assertEqual("approved", result["review_verdict"])
        self.assertEqual(self.diff["seal_sha256"], result["evidence_bundle_sha256"])
        self.assertEqual(1, worker.calls)
        bundle = self.repository.get_bundle(int(result["review_bundle_id"]))
        self.assertEqual("reviewed", bundle["status"])
        self.assertEqual("approved", bundle["review"]["verdict"])
        self.assertEqual({"review", "review_seal", "bundle_seal"}, {item["kind"] for item in bundle["artifacts"]})
        self.assertEqual(before, self._git("status", "--porcelain=v2", "--untracked-files=all"))

    def test_failed_or_incomplete_verification_never_starts_reviewer(self) -> None:
        worker = _ReviewerWorker(self._response())
        verification_bundle = self.repository.get_bundle(int(self.verification["verification_bundle_id"]))
        receipt = next(item for item in verification_bundle["artifacts"] if item["kind"] == "verification_receipt")
        record = self._record(receipt)
        raw = json.loads(self.store.reopen(record))
        raw["verification_status"] = "failed"
        (self.store.root / record.relative_path).write_bytes(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode())
        with self.assertRaises(ValueError):
            self._review(worker)
        self.assertEqual(0, worker.calls)

    def test_out_of_scope_findings_secret_output_and_reviewer_side_effect_fail_closed(self) -> None:
        finding = {"severity": "important", "path": "outside.py", "line": 1, "message": "Wrong behavior."}
        with self.assertRaisesRegex(ValueError, "code_evidence_review_invalid"):
            self._review(_ReviewerWorker(self._response("changes_requested", [finding])))

        secret = self._response()
        secret["summary"] = "Bearer " + "A9" * 24
        secret["review_hash"] = canonical_review_hash(secret)
        with self.assertRaises(ValueError):
            self._review(_ReviewerWorker(secret))

        with self.assertRaisesRegex(ValueError, "code_evidence_review_side_effect"):
            self._review(_ReviewerWorker(self._response(), mutate=True))

    def test_real_reviewer_is_disabled_by_default_and_enabled_calls_are_audited_external(self) -> None:
        with mock.patch("app.code_evidence_review.CodexCliWorker") as worker_type:
            disabled = CodeEvidenceReviewService(
                self.repository, self.store, {"repo-a": self.scope}
            )
            worker_type.assert_not_called()
            with self.assertRaisesRegex(ValueError, "code_evidence_reviewer_disabled"):
                disabled.review(
                    diff_bundle_id=int(self.diff["bundle_id"]),
                    verification_bundle_id=int(self.verification["verification_bundle_id"]),
                    bundle_key="review-disabled",
                    conversation_key="conversation-a",
                    task_key="task-a",
                )

            worker = _ReviewerWorker(self._response())
            worker_type.return_value = worker
            enabled = CodeEvidenceReviewService(
                self.repository,
                self.store,
                {"repo-a": self.scope},
                allow_external_model=True,
            )
            result = enabled.review(
                diff_bundle_id=int(self.diff["bundle_id"]),
                verification_bundle_id=int(self.verification["verification_bundle_id"]),
                bundle_key="review-enabled",
                conversation_key="conversation-a",
                task_key="task-a",
            )

        self.assertTrue(result["external_calls"])
        self.assertEqual(1, worker.calls)
        bundle = self.repository.get_bundle(int(result["review_bundle_id"]))
        seal = next(item for item in bundle["artifacts"] if item["kind"] == "review_seal")
        audit = json.loads(self.store.reopen(self._record(seal)))
        self.assertTrue(audit["external_calls"])

    @staticmethod
    def _record(item: dict[str, object]) -> EvidenceArtifactRecord:
        return EvidenceArtifactRecord(
            bundle_id=int(item["bundle_id"]), kind=str(item["kind"]), relative_path=str(item["relative_path"]),
            sha256=str(item["sha256"]), size_bytes=int(item["size_bytes"]), device=int(item["device"]),
            inode=int(item["inode"]), mode=int(item["mode"]), link_count=int(item["link_count"]),
        )


if __name__ == "__main__":
    unittest.main()
