from __future__ import annotations

import hashlib
import json
import os
import copy
import dataclasses
import pickle
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

_EXPLICIT_TEST_DB = os.environ.get("HARNESS_DB_PATH", "")
if not _EXPLICIT_TEST_DB or not Path(_EXPLICIT_TEST_DB).is_absolute() or not _EXPLICIT_TEST_DB.startswith("/private/tmp/"):
    raise RuntimeError("Task6 tests require an explicit fresh /private/tmp HARNESS_DB_PATH")

from app import database
from app import worktree_executor as executor_module
from app.local_agent_confirmation import LocalAgentConfirmationService
from app.local_agent_contract import load_local_agent_task
from app.local_agent_repository import LocalAgentRunRepository
from app import local_agent_repository as repository_module
from app.local_agent_review import LocalAgentReviewer
from app.local_agent_runner import LocalAgentRunner
from app.runtime_policy import assert_local_agent_run_allowed
from tests.test_local_agent_review import _CodeWorker, _ReviewWorker, _review_payload, _worker_result


class _FileChangeWorker:
    def __init__(self, action) -> None:
        self.action = action

    def start(self, request, sink):
        from app.local_agent_repository import _read_process_start_identity

        sink.on_started(os.getpid(), _read_process_start_identity(os.getpid()))
        self.action(request.worktree_path)
        return _worker_result(None)


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


class LocalAgentConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="his_harness_stage_f_confirmation_", dir="/private/tmp")
        self.root = Path(self.tmp.name)
        self.previous_db_path = database.DB_PATH
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
        self.initial_head = self._git_text("rev-parse", "HEAD")
        self.initial_source = (self.project / "calculator.py").read_bytes()
        self.worktree_root = Path(tempfile.mkdtemp(prefix="his_harness_stage_f_confirmation_worktree_", dir="/private/tmp"))
        os.chmod(self.worktree_root, 0o700)
        self.run_id = self._approved_run()
        self.clock = _Clock()
        self.service = LocalAgentConfirmationService(
            repository=self.repository,
            artifact_root=self.worktree_root,
        )

    def tearDown(self) -> None:
        subprocess.run(["git", "worktree", "prune"], cwd=self.project, check=False, capture_output=True)
        import shutil

        if self.worktree_root.exists():
            shutil.rmtree(self.worktree_root)
        database.DB_PATH = self.previous_db_path
        self.tmp.cleanup()

    def _git(self, *arguments: str) -> None:
        subprocess.run(["git", *arguments], cwd=self.project, check=True, capture_output=True)

    def _git_text(self, *arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments], cwd=self.project, check=True, capture_output=True, text=True
        ).stdout.strip()

    def _approved_run(self) -> int:
        payload = {
            "schema_version": "his-local-agent-task.v1",
            "task_key": "fixture-confirmation-1",
            "project_path": str(self.project),
            "request": "Fix add so the supplied unit test passes.",
            "allowed_paths": ["calculator.py"],
            "verification_commands": [[sys.executable, "-m", "unittest", "-q", "test_calculator"]],
            "acceptance_criteria": ["The existing test passes."],
            "timeout_seconds": 30,
        }
        contract = self.root / "task.json"
        contract.write_text(json.dumps(payload), encoding="utf-8")
        task = load_local_agent_task(contract)
        reviewer = LocalAgentReviewer(
            repository=self.repository,
            worker=_ReviewWorker([_review_payload()]),
            artifact_root=self.worktree_root,
        )
        runner = LocalAgentRunner(
            repository=self.repository,
            worker=_CodeWorker(),
            reviewer=reviewer,
            worktree_root=self.worktree_root,
        )
        snapshot = runner.execute(
            task,
            assert_local_agent_run_allowed(
                allow_real_agent=True,
                authorization_id="task-six-confirmation-authorization",
            ),
        )
        self.assertEqual("awaiting_human_confirmation", snapshot["run"]["status"])
        return int(snapshot["run"]["id"])

    def test_valid_confirmation_applies_exact_patch_once_and_never_commits(self) -> None:
        confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")

        result = self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")

        self.assertEqual("locally_applied", result["run"]["status"])
        self.assertEqual("def add(a, b):\n    return a + b\n", (self.project / "calculator.py").read_text())
        self.assertEqual(self.initial_head, self._git_text("rev-parse", "HEAD"))
        with self.assertRaisesRegex(ValueError, "local_agent_confirmation_invalid"):
            self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")

    def test_repository_exposes_only_capability_gated_completion(self) -> None:
        self.assertFalse(hasattr(self.repository, "issue_apply_confirmation"))
        self.assertFalse(hasattr(self.repository, "confirm_apply_transaction"))
        before = self.repository.snapshot(self.run_id)
        with self.assertRaises(ValueError):
            self.repository.finalize_local_apply(object())
        self.assertEqual(before, self.repository.snapshot(self.run_id))

    def test_completion_capability_rejects_copy_pickle_manual_cross_repository_and_reuse(self) -> None:
        confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")
        original = self.repository.finalize_local_apply
        captured: list[object] = []

        def inspect(capability: object):
            for operation in (
                lambda: copy.copy(capability),
                lambda: copy.deepcopy(capability),
                lambda: pickle.loads(pickle.dumps(capability)),
                lambda: dataclasses.replace(capability),
                lambda: type(capability)(),
            ):
                with self.assertRaises((TypeError, pickle.PickleError)):
                    operation()
            captured.append(capability)
            return original(capability)

        with patch.object(self.repository, "finalize_local_apply", side_effect=inspect):
            result = self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")
        self.assertEqual("locally_applied", result["run"]["status"])
        capability = captured[0]
        manually_constructed = type(capability)(repository_module._LOCAL_APPLY_COMPLETION_ISSUER)
        other = LocalAgentRunRepository(database.DB_PATH)
        for candidate in (manually_constructed, capability):
            with self.subTest(candidate=candidate):
                before = self.repository.snapshot(self.run_id)
                with self.assertRaises(ValueError):
                    other.finalize_local_apply(candidate)
                self.assertEqual(before, self.repository.snapshot(self.run_id))
        with self.assertRaises(ValueError):
            self.repository.finalize_local_apply(capability)

    def test_completion_capability_revalidates_terminal_journal_before_control_commit(self) -> None:
        confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")
        original = self.repository.finalize_local_apply

        def tamper_then_finalize(capability: object):
            journal_path = next((self.project / ".git" / "his-harness" / "local-apply").glob("*/journal.json"))
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            journal["state"] = "failed_apply"
            journal_path.chmod(0o600)
            journal_path.write_text(json.dumps(journal), encoding="utf-8")
            return original(capability)

        with patch.object(self.repository, "finalize_local_apply", side_effect=tamper_then_finalize):
            with self.assertRaisesRegex(ValueError, "local_agent_confirmation_invalid"):
                self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")

        snapshot = self.repository.snapshot(self.run_id)
        self.assertEqual("awaiting_human_confirmation", snapshot["run"]["status"])
        self.assertEqual([], [item for item in snapshot["artifacts"] if item["kind"] == "local_apply_receipt"])
        self.assertNotIn("local_apply_finished", [item["event_type"] for item in snapshot["events"]])
        with database.connect_database(database.DB_PATH) as connection:
            self.assertEqual("issued", connection.execute(
                "select status from local_agent_apply_confirmations where run_id=?", (self.run_id,),
            ).fetchone()[0])

    def test_linked_journal_root_fails_before_source_or_control_state_write(self) -> None:
        outside = self.root / "outside-journal"
        outside.mkdir()
        (self.project / ".git" / "his-harness").symlink_to(outside, target_is_directory=True)
        confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")

        with self.assertRaisesRegex(ValueError, "local_agent_confirmation_invalid|local_agent_apply_failed"):
            self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")

        self.assertEqual([], list(outside.iterdir()))
        self.assertEqual(self.initial_source, (self.project / "calculator.py").read_bytes())
        snapshot = self.repository.snapshot(self.run_id)
        self.assertEqual("awaiting_human_confirmation", snapshot["run"]["status"])
        with database.connect_database(database.DB_PATH) as connection:
            self.assertEqual("issued", connection.execute(
                "select status from local_agent_apply_confirmations where run_id=?", (self.run_id,),
            ).fetchone()[0])

    def test_linked_local_apply_parent_fails_before_source_or_control_state_write(self) -> None:
        outside = self.root / "outside-local-apply"
        outside.mkdir()
        harness = self.project / ".git" / "his-harness"
        harness.mkdir()
        (harness / "local-apply").symlink_to(outside, target_is_directory=True)

        self._assert_confirmation_fails_closed(outside)

    def test_linked_application_directory_fails_before_source_or_control_state_write(self) -> None:
        outside = self.root / "outside-application"
        outside.mkdir()
        application_id = self._application_id()
        root = self.project / ".git" / "his-harness" / "local-apply"
        root.mkdir(parents=True)
        (root / application_id).symlink_to(outside, target_is_directory=True)

        self._assert_confirmation_fails_closed(outside)

    def test_linked_or_hardlinked_evidence_leaf_fails_closed(self) -> None:
        application_id = self._application_id()
        application = self.project / ".git" / "his-harness" / "local-apply" / application_id
        application.mkdir(parents=True)
        outside = self.root / "outside-evidence"
        outside.mkdir()
        external_patch = outside / "external.diff"
        external_patch.write_bytes(b"external")
        os.link(external_patch, application / "final.diff")

        self._assert_confirmation_fails_closed(outside, expected_names={"external.diff"})

    def test_linked_journal_leaf_fails_closed(self) -> None:
        application_id = self._application_id()
        application = self.project / ".git" / "his-harness" / "local-apply" / application_id
        application.mkdir(parents=True)
        outside = self.root / "outside-journal-leaf"
        outside.mkdir()
        external_journal = outside / "external.json"
        external_journal.write_bytes(b"external")
        (application / "journal.json").symlink_to(external_journal)

        self._assert_confirmation_fails_closed(outside, expected_names={"external.json"})

    def test_application_directory_replacement_is_detected_before_source_write(self) -> None:
        outside = self.root / "outside-replacement"
        outside.mkdir()
        confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")
        original = executor_module._AnchoredLocalApplyTransaction.read_bytes
        replaced = False

        def replace_namespace(transaction, leaf, **kwargs):
            nonlocal replaced
            if leaf == "final.diff" and transaction.application_fd is not None and not replaced:
                replaced = True
                application = transaction.root_path / transaction.application_id
                moved = application.with_name(application.name + "-moved")
                application.rename(moved)
                application.symlink_to(outside, target_is_directory=True)
            return original(transaction, leaf, **kwargs)

        with patch.object(executor_module._AnchoredLocalApplyTransaction, "read_bytes", replace_namespace):
            with self.assertRaisesRegex(ValueError, "local_agent_confirmation_invalid|local_agent_apply_failed|local_agent_apply_recovery_required"):
                self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")

        self.assertTrue(replaced)
        self.assertEqual([], list(outside.iterdir()))
        self._assert_source_and_confirmation_unchanged()

    def test_late_application_directory_replacement_has_durable_operation_and_same_token_recovery(self) -> None:
        outside = self.root / "outside-late-replacement"
        outside.mkdir()
        confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")
        original = executor_module.run_command
        replaced = False

        def replace_at_source_write(command, **kwargs):
            nonlocal replaced
            if command[:2] == ["git", "apply"] and "--check" not in command and not replaced:
                replaced = True
                root = self.project / ".git" / "his-harness" / "local-apply"
                application = next(item for item in root.iterdir() if item.is_dir())
                application.rename(application.with_name(application.name + "-moved"))
                application.symlink_to(outside, target_is_directory=True)
            return original(command, **kwargs)

        with patch.object(executor_module, "run_command", side_effect=replace_at_source_write):
            with self.assertRaisesRegex(ValueError, "local_agent_apply_recovery_required"):
                self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")

        self.assertTrue(replaced)
        self.assertEqual("def add(a, b):\n    return a + b\n", (self.project / "calculator.py").read_text())
        snapshot = self.repository.snapshot(self.run_id)
        self.assertEqual("awaiting_human_confirmation", snapshot["run"]["status"])
        self.assertIn(snapshot["apply_operation"]["status"], {"applying", "recovery_required"})
        self.assertEqual([], list(outside.iterdir()))

        recovered = LocalAgentConfirmationService(
            repository=self.repository,
            artifact_root=self.worktree_root,
        ).confirm_and_apply(self.run_id, confirmation.token, "local-user")

        self.assertEqual("locally_applied", recovered["run"]["status"])
        self.assertTrue(recovered["apply"]["idempotent"])
        self.assertEqual(self.initial_head, self._git_text("rev-parse", "HEAD"))
        self.assertEqual([], list(outside.iterdir()))

    def _application_id(self) -> str:
        snapshot = self.repository.snapshot(self.run_id)
        record = next(item for item in snapshot["artifacts"] if item["kind"] == "final_patch")
        patch_bytes = (self.worktree_root / record["relative_path"]).read_bytes()
        return executor_module.build_local_apply_application_id(
            project_path=self.project,
            patch_hash=hashlib.sha256(patch_bytes).hexdigest(),
        )

    def _assert_confirmation_fails_closed(self, outside: Path, *, expected_names: set[str] | None = None) -> None:
        before = {item.name: item.read_bytes() for item in outside.iterdir() if item.is_file()}
        confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")
        with self.assertRaisesRegex(ValueError, "local_agent_confirmation_invalid|local_agent_apply_failed|local_agent_apply_recovery_required"):
            self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")
        self.assertEqual(expected_names or set(), {item.name for item in outside.iterdir()})
        self.assertEqual(before, {item.name: item.read_bytes() for item in outside.iterdir() if item.is_file()})
        self._assert_source_and_confirmation_unchanged()

    def _assert_source_and_confirmation_unchanged(self) -> None:
        self.assertEqual(self.initial_source, (self.project / "calculator.py").read_bytes())
        snapshot = self.repository.snapshot(self.run_id)
        self.assertEqual("awaiting_human_confirmation", snapshot["run"]["status"])
        with database.connect_database(database.DB_PATH) as connection:
            self.assertEqual("issued", connection.execute(
                "select status from local_agent_apply_confirmations where run_id=?", (self.run_id,),
            ).fetchone()[0])

    def test_only_hash_is_persisted_and_token_is_not_exposed(self) -> None:
        confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")
        snapshot_text = json.dumps(self.repository.snapshot(self.run_id), sort_keys=True)
        with database.connect_database(database.DB_PATH) as connection:
            row = connection.execute(
                "select token_hash, binding_json, issued_at, expires_at from local_agent_apply_confirmations where run_id=?",
                (self.run_id,),
            ).fetchone()

        self.assertNotIn(confirmation.token, snapshot_text)
        self.assertNotIn(confirmation.token, row["binding_json"])
        self.assertEqual("sha256:" + hashlib.sha256(confirmation.token.encode()).hexdigest(), row["token_hash"])
        issued = datetime.fromisoformat(row["issued_at"])
        expires = datetime.fromisoformat(row["expires_at"])
        self.assertEqual(timedelta(minutes=5), expires - issued)

    def test_expired_confirmation_has_zero_source_write(self) -> None:
        with patch("app.local_agent_confirmation._now_utc", return_value=self.clock.value):
            confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")
        self.clock.value += timedelta(minutes=6)
        with patch("app.local_agent_confirmation._now_utc", return_value=self.clock.value):
            result = self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")

        self.assertEqual("confirmation_expired", result["run"]["status"])
        self.assertEqual(self.initial_source, (self.project / "calculator.py").read_bytes())
        self.assertEqual(self.initial_head, self._git_text("rev-parse", "HEAD"))

    def test_wrong_token_and_requester_do_not_consume_valid_confirmation(self) -> None:
        confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")
        for token, requester in (("x" * len(confirmation.token), "local-user"), (confirmation.token, "other-user")):
            with self.subTest(requester=requester), self.assertRaisesRegex(ValueError, "local_agent_confirmation_invalid"):
                self.service.confirm_and_apply(self.run_id, token, requester)
            self.assertEqual(self.initial_source, (self.project / "calculator.py").read_bytes())
            self.assertIsNone(self.repository.snapshot(self.run_id)["apply_operation"])

        result = self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")
        self.assertEqual("locally_applied", result["run"]["status"])

    def test_artifact_tamper_and_symlink_fail_before_source_write(self) -> None:
        confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")
        snapshot = self.repository.snapshot(self.run_id)
        record = next(item for item in snapshot["artifacts"] if item["kind"] == "final_patch")
        target = self.worktree_root / str(record["relative_path"])
        target.chmod(0o600)
        target.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "local_agent_confirmation_invalid"):
            self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")
        self.assertEqual(self.initial_source, (self.project / "calculator.py").read_bytes())

    def test_symlinked_review_seal_fails_before_source_write(self) -> None:
        confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")
        snapshot = self.repository.snapshot(self.run_id)
        record = next(item for item in snapshot["artifacts"] if item["kind"] == "review_seal")
        target = self.worktree_root / str(record["relative_path"])
        target.unlink()
        target.symlink_to(self.project / "calculator.py")

        with self.assertRaisesRegex(ValueError, "local_agent_confirmation_invalid"):
            self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")

        self.assertEqual(self.initial_source, (self.project / "calculator.py").read_bytes())

    def test_source_dirty_or_head_change_fails_before_apply(self) -> None:
        confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")
        (self.project / "calculator.py").write_text("user edit\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "local_agent_confirmation_invalid"):
            self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")

        self.assertEqual("user edit\n", (self.project / "calculator.py").read_text())
        self.assertEqual("awaiting_human_confirmation", self.repository.snapshot(self.run_id)["run"]["status"])

    def test_source_head_change_fails_before_apply_and_does_not_consume_token(self) -> None:
        confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")
        (self.project / "unrelated.txt").write_text("new head\n", encoding="utf-8")
        self._git("add", "unrelated.txt")
        self._git("commit", "-m", "head changed")

        with self.assertRaisesRegex(ValueError, "local_agent_confirmation_invalid"):
            self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")

        self.assertEqual(self.initial_source, (self.project / "calculator.py").read_bytes())
        with database.connect_database(database.DB_PATH) as connection:
            self.assertEqual("issued", connection.execute("select status from local_agent_apply_confirmations where run_id=?", (self.run_id,)).fetchone()[0])

    def test_concurrent_confirmation_has_exactly_one_winner(self) -> None:
        confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")
        barrier = threading.Barrier(2)

        def confirm() -> object:
            barrier.wait(timeout=3)
            try:
                return LocalAgentConfirmationService(
                    repository=LocalAgentRunRepository(database.DB_PATH),
                    artifact_root=self.worktree_root,
                ).confirm_and_apply(self.run_id, confirmation.token, "local-user")
            except Exception as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _index: confirm(), range(2)))

        self.assertEqual(1, sum(isinstance(item, dict) for item in outcomes))
        self.assertEqual(1, sum(isinstance(item, Exception) for item in outcomes))
        self.assertEqual("locally_applied", self.repository.snapshot(self.run_id)["run"]["status"])

    def test_interruption_after_local_apply_is_recoverable_with_same_token(self) -> None:
        confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")
        from app import local_agent_confirmation as module

        real_apply = module.apply_final_diff_to_project
        calls = 0

        def interrupted_apply(*, project_path: Path, final_diff: str, allow_file_changes: bool = False, expected_common_git_identity=None, application_id=None) -> dict:
            nonlocal calls
            calls += 1
            result = real_apply(
                project_path=project_path,
                final_diff=final_diff,
                allow_file_changes=allow_file_changes,
                expected_common_git_identity=expected_common_git_identity,
                application_id=application_id,
            )
            if calls == 1:
                raise RuntimeError("injected interruption")
            return result

        with patch.object(module, "apply_final_diff_to_project", side_effect=interrupted_apply):
            with self.assertRaisesRegex(ValueError, "local_agent_apply_recovery_required"):
                self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")
            result = self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")

        self.assertEqual("locally_applied", result["run"]["status"])
        self.assertTrue(result["apply"]["idempotent"])
        self.assertEqual(self.initial_head, self._git_text("rev-parse", "HEAD"))

    def test_process_interruption_before_source_apply_keeps_durable_operation_and_recovers(self) -> None:
        confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")
        from app import local_agent_confirmation as module

        real_apply = module.apply_final_diff_to_project
        calls = 0

        def interrupt_before_apply(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise SystemExit("injected process interruption before apply")
            return real_apply(**kwargs)

        with patch.object(module, "apply_final_diff_to_project", side_effect=interrupt_before_apply):
            with self.assertRaises(SystemExit):
                self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")

        snapshot = self.repository.snapshot(self.run_id)
        self.assertEqual("awaiting_human_confirmation", snapshot["run"]["status"])
        self.assertEqual("applying", snapshot["apply_operation"]["status"])
        self.assertEqual(self.initial_source, (self.project / "calculator.py").read_bytes())

        recovered = LocalAgentConfirmationService(
            repository=self.repository, artifact_root=self.worktree_root,
        ).confirm_and_apply(self.run_id, confirmation.token, "local-user")
        self.assertEqual("locally_applied", recovered["run"]["status"])
        self.assertFalse(recovered["apply"]["idempotent"])

    def test_process_interruption_after_source_apply_keeps_durable_operation_and_recovers(self) -> None:
        confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")
        from app import local_agent_confirmation as module

        real_apply = module.apply_final_diff_to_project
        calls = 0

        def interrupt_after_apply(**kwargs):
            nonlocal calls
            calls += 1
            result = real_apply(**kwargs)
            if calls == 1:
                raise SystemExit("injected process interruption after apply")
            return result

        with patch.object(module, "apply_final_diff_to_project", side_effect=interrupt_after_apply):
            with self.assertRaises(SystemExit):
                self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")

        snapshot = self.repository.snapshot(self.run_id)
        self.assertEqual("awaiting_human_confirmation", snapshot["run"]["status"])
        self.assertEqual("applying", snapshot["apply_operation"]["status"])
        self.assertEqual("def add(a, b):\n    return a + b\n", (self.project / "calculator.py").read_text())

        recovered = LocalAgentConfirmationService(
            repository=self.repository, artifact_root=self.worktree_root,
        ).confirm_and_apply(self.run_id, confirmation.token, "local-user")
        self.assertEqual("locally_applied", recovered["run"]["status"])
        self.assertTrue(recovered["apply"]["idempotent"])

    def test_post_intent_partial_source_drift_is_durable_recovery_required(self) -> None:
        confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")
        from app import local_agent_confirmation as module

        with patch.object(module, "apply_final_diff_to_project", side_effect=SystemExit("before apply")):
            with self.assertRaises(SystemExit):
                self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")
        partial = b"def add(a, b):\n    return a * b\n"
        (self.project / "calculator.py").write_bytes(partial)

        with self.assertRaisesRegex(ValueError, "local_agent_confirmation_invalid"):
            LocalAgentConfirmationService(
                repository=self.repository, artifact_root=self.worktree_root,
            ).confirm_and_apply(self.run_id, confirmation.token, "local-user")

        snapshot = self.repository.snapshot(self.run_id)
        self.assertEqual("awaiting_human_confirmation", snapshot["run"]["status"])
        self.assertEqual("recovery_required", snapshot["apply_operation"]["status"])
        self.assertEqual(partial, (self.project / "calculator.py").read_bytes())
        with database.connect_database(database.DB_PATH) as connection:
            self.assertEqual("issued", connection.execute(
                "select status from local_agent_apply_confirmations where run_id=?", (self.run_id,),
            ).fetchone()[0])

    def test_database_event_failure_rolls_back_control_state_and_same_token_recovers(self) -> None:
        confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")
        from app import local_agent_repository as module

        original = module._append_event_in_transaction
        failed_once = False

        def fail_local_apply(connection, run_id, attempt_id, event_type, payload_json):
            nonlocal failed_once
            if event_type == "local_apply_finished" and not failed_once:
                failed_once = True
                raise sqlite3.OperationalError("injected event failure")
            return original(connection, run_id, attempt_id, event_type, payload_json)

        with patch.object(module, "_append_event_in_transaction", side_effect=fail_local_apply):
            with self.assertRaises(ValueError):
                self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")
        self.assertEqual("awaiting_human_confirmation", self.repository.snapshot(self.run_id)["run"]["status"])
        with database.connect_database(database.DB_PATH) as connection:
            self.assertEqual("issued", connection.execute("select status from local_agent_apply_confirmations where run_id=?", (self.run_id,)).fetchone()[0])
            self.assertEqual(0, connection.execute("select count(*) from local_agent_artifacts where run_id=? and kind='local_apply_receipt'", (self.run_id,)).fetchone()[0])

        recovered = self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")
        self.assertEqual("locally_applied", recovered["run"]["status"])
        self.assertTrue(recovered["apply"]["idempotent"])

    def test_file_addition_and_deletion_patches_apply_through_same_confirmation_boundary(self) -> None:
        cases = (
            ("add", {"base.py": "BASE = True\n"}, "new.py", lambda root: (root / "new.py").write_text("VALUE = 1\n", encoding="utf-8")),
            ("delete", {"remove.py": "REMOVE = True\n"}, "remove.py", lambda root: (root / "remove.py").unlink()),
        )
        for index, (name, files, allowed, action) in enumerate(cases, start=1):
            with self.subTest(name=name):
                project = self.root / f"project-{name}"
                project.mkdir()
                subprocess.run(["git", "init", "-q"], cwd=project, check=True)
                subprocess.run(["git", "config", "user.email", "harness@example.test"], cwd=project, check=True)
                subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=project, check=True)
                for relative, content in files.items():
                    (project / relative).write_text(content, encoding="utf-8")
                (project / "test_fixture.py").write_text(
                    "import unittest\n\nclass FixtureTests(unittest.TestCase):\n"
                    "    def test_fixture(self):\n        self.assertTrue(True)\n",
                    encoding="utf-8",
                )
                subprocess.run(["git", "add", "."], cwd=project, check=True)
                subprocess.run(["git", "commit", "-m", "initial"], cwd=project, check=True, capture_output=True)
                contract = self.root / f"task-{name}.json"
                contract.write_text(json.dumps({
                    "schema_version": "his-local-agent-task.v1",
                    "task_key": f"fixture-confirmation-{name}",
                    "project_path": str(project),
                    "request": f"Apply the bounded {name} fixture.",
                    "allowed_paths": [allowed],
                    "verification_commands": [[sys.executable, "-m", "unittest", "-q", "test_fixture"]],
                    "acceptance_criteria": [f"The {name} patch is present."],
                    "timeout_seconds": 30,
                }), encoding="utf-8")
                reviewer = LocalAgentReviewer(repository=self.repository, worker=_ReviewWorker([_review_payload()]), artifact_root=self.worktree_root)
                snapshot = LocalAgentRunner(
                    repository=self.repository,
                    worker=_FileChangeWorker(action),
                    reviewer=reviewer,
                    worktree_root=self.worktree_root,
                ).execute(
                    load_local_agent_task(contract),
                    assert_local_agent_run_allowed(allow_real_agent=True, authorization_id=f"task-six-file-change-{index}"),
                )
                self.assertEqual("awaiting_human_confirmation", snapshot["run"]["status"])
                run_id = int(snapshot["run"]["id"])
                confirmation = self.service.issue_local_apply_confirmation(run_id, "local-user")
                result = self.service.confirm_and_apply(run_id, confirmation.token, "local-user")
                self.assertEqual("locally_applied", result["run"]["status"])
                if name == "add":
                    self.assertEqual("VALUE = 1\n", (project / allowed).read_text())
                else:
                    self.assertFalse((project / allowed).exists())


if __name__ == "__main__":
    unittest.main()
