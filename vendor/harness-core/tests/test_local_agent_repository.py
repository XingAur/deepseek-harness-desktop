from __future__ import annotations

import copy
import ctypes
import dataclasses
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app import database
from app.local_agent_contract import load_local_agent_task
from app.local_agent_repository import (
    LocalAgentRunRepository,
    _ProcBsdInfo,
    _read_process_start_identity,
)
from app.repair_learning import RuleObservationOutcome, derive_task_learning_context
from app.repair_learning_service import RepairLearningService
from app.runtime_policy import (
    LocalAgentActivationPreflight,
    LocalAgentRunNotAllowedError,
    assert_local_agent_run_allowed,
)


class LocalAgentRunRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "harness.sqlite"
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = self.database_path
        self.addCleanup(self._restore_database_path)
        database.init_db()
        self.repository = LocalAgentRunRepository(self.database_path)
        self.project = self._create_project_fixture()
        self.task = self._load_task()
        self.preflight = assert_local_agent_run_allowed(
            allow_real_agent=True,
            authorization_id="local-agent-repository-fixture-001",
        )
        self.second_contract_sequence = 0

    def _restore_database_path(self) -> None:
        database.DB_PATH = self.previous_db_path

    def _create_project_fixture(self) -> Path:
        project = self.root / "project"
        project.mkdir()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        (project / "calculator.py").write_text("def add(left, right):\n    return left + right\n")
        subprocess.run(["git", "-C", str(project), "add", "calculator.py"], check=True)
        subprocess.run(
            [
                "git", "-C", str(project), "-c", "user.email=fixture@example.invalid",
                "-c", "user.name=Fixture", "commit", "--quiet", "-m", "fixture",
            ],
            check=True,
        )
        return project

    def _load_task(self):
        contract = self.root / "task.json"
        contract.write_text(
            json.dumps(
                {
                    "schema_version": "his-local-agent-task.v1",
                    "task_key": "repository-fixture-1",
                    "project_path": str(self.project),
                    "request": "Record a bounded local agent run.",
                    "allowed_paths": ["calculator.py"],
                    "verification_commands": [[sys.executable, "-m", "unittest", "-q"]],
                    "acceptance_criteria": ["Repository state is append only."],
                    "timeout_seconds": 120,
                }
            ),
            encoding="utf-8",
        )
        return load_local_agent_task(contract)

    def _create_run_and_attempt(self) -> tuple[dict, dict]:
        run = self.repository.consume_preflight(self.task, self.preflight)
        self.repository.bind_workspace(run["id"], self._workspace_binding(run["id"]))
        return run, self.repository.start_attempt(run["id"])

    def _workspace_binding(self, workspace_id: int) -> dict[str, object]:
        root = f"/private/tmp/his_harness_stage_f_repository_{workspace_id}"
        worktree = root + f"/run_{workspace_id}"
        return {
            "worktree_path": worktree,
            "source_metadata": {},
            "source_worktrees": [],
            "worktree_identity": [17, workspace_id, 16_384],
            "worktree_git_identity": [17, workspace_id + 10_000, 32_768],
            "marker_path": root + "/.harness_worktree_markers/" + hashlib.sha256(worktree.encode("utf-8")).hexdigest() + ".json",
            "task_artifact": f".harness_local_agent_control/run_{workspace_id}/task.json",
            "task_sha256": "a" * 64,
        }

    def _bind_worker(self, attempt: dict) -> dict:
        with patch("app.local_agent_repository._read_process_start_identity", return_value="darwin-proc-bsdinfo-v1:1:2"):
            return self.repository.bind_worker_identity(attempt["id"], 12345, "darwin-proc-bsdinfo-v1:1:2")

    @staticmethod
    def _learning_task_context(task, run_id: int) -> dict[str, object]:
        context = derive_task_learning_context(task, run_id=run_id)
        return {
            "run_id": context.run_id,
            "task_key": context.task_key,
            "repository_kind": context.repository_kind,
            "allowed_path_prefixes": list(context.allowed_path_prefixes),
            "verification_command_fingerprints": list(context.verification_command_fingerprints),
            "high_risk_tags": list(context.high_risk_tags),
            "failure_sources": list(context.failure_sources),
        }

    def test_preflight_is_consumed_once_across_retry_and_restart(self) -> None:
        run = self.repository.consume_preflight(self.task, self.preflight)
        self.assertEqual("created", run["status"])
        reopened = LocalAgentRunRepository(self.database_path)

        with self.assertRaisesRegex(ValueError, "local_agent_authorization_already_consumed"):
            reopened.consume_preflight(self.task, self.preflight)

    def test_project_lease_conflict_does_not_consume_new_authorization(self) -> None:
        active = self.repository.consume_preflight(self.task, self.preflight)
        contract = self.root / "same-project-task.json"
        payload = json.loads((self.root / "task.json").read_text(encoding="utf-8"))
        payload["task_key"] = "repository-same-project-2"
        contract.write_text(json.dumps(payload), encoding="utf-8")
        task = load_local_agent_task(contract)
        candidate = assert_local_agent_run_allowed(
            allow_real_agent=True,
            authorization_id="local-agent-project-lease-candidate-002",
        )

        with self.assertRaisesRegex(ValueError, "local_agent_project_run_active"):
            self.repository.consume_preflight(task, candidate)

        self.repository.transition(active["id"], "created", "workspace_ready", {})
        attempt = self.repository.start_attempt(active["id"])
        self._bind_worker(attempt)
        self.repository.complete_attempt(attempt["id"], "failed_scope", "worker_scope_invalid")
        replacement = self.repository.consume_preflight(task, candidate)
        self.assertEqual("created", replacement["status"])

    def test_contract_valid_long_task_key_round_trips_through_repository(self) -> None:
        contract = self.root / "long-task-key.json"
        payload = json.loads((self.root / "task.json").read_text(encoding="utf-8"))
        payload["task_key"] = "stage-f-real-calculator-20260812"
        contract.write_text(json.dumps(payload), encoding="utf-8")
        task = load_local_agent_task(contract)
        preflight = assert_local_agent_run_allowed(
            allow_real_agent=True,
            authorization_id="local-agent-long-task-key-fixture-001",
        )

        run = self.repository.consume_preflight(task, preflight)

        self.assertEqual(payload["task_key"], run["task_key"])
        self.assertEqual(payload["task_key"], self.repository.snapshot(run["id"])["run"]["task_key"])

    def test_forged_or_cloned_preflight_is_rejected_before_a_run_is_inserted(self) -> None:
        with self.assertRaises(LocalAgentRunNotAllowedError):
            LocalAgentActivationPreflight()
        with self.assertRaises(TypeError):
            dataclasses.replace(self.preflight)

        forged = object.__new__(LocalAgentActivationPreflight)
        clones = (copy.copy(self.preflight), copy.deepcopy(self.preflight), forged)
        for candidate in clones:
            with self.subTest(candidate=type(candidate).__name__), self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
                self.repository.consume_preflight(self.task, candidate)
            with database.connect_database(self.database_path) as connection:
                self.assertEqual(0, connection.execute("select count(*) from local_agent_runs").fetchone()[0])

    def test_concurrent_preflight_consumption_has_exactly_one_winner(self) -> None:
        outcomes: list[object] = []
        gate = threading.Barrier(2)

        def consume() -> None:
            try:
                gate.wait(timeout=2)
                outcomes.append(LocalAgentRunRepository(self.database_path).consume_preflight(self.task, self.preflight))
            except Exception as error:  # assertion below checks the stable outcome
                outcomes.append(error)

        first = threading.Thread(target=consume)
        second = threading.Thread(target=consume)
        first.start()
        second.start()
        first.join(timeout=5)
        second.join(timeout=5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(1, sum(isinstance(outcome, dict) for outcome in outcomes))
        failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
        self.assertEqual(1, len(failures))
        self.assertRegex(str(failures[0]), "local_agent_authorization_already_consumed")

    def test_events_and_artifacts_reject_update_delete_and_replace(self) -> None:
        run, attempt = self._create_run_and_attempt()
        event = self.repository.append_event(run["id"], attempt["id"], "worker_started", {"pid": 123})
        artifact = self.repository.add_artifact(
            run["id"], attempt["id"], "diff", "changes.patch", "a" * 64, 12
        )
        with database.connect_database(self.database_path) as connection:
            for table, row in (("local_agent_run_events", event), ("local_agent_artifacts", artifact)):
                statements = (
                    (f"update {table} set created_at='changed' where id=?", (row["id"],)),
                    (f"delete from {table} where id=?", (row["id"],)),
                )
                for sql, parameters in statements:
                    with self.subTest(table=table, sql=sql), self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(sql, parameters)
                with self.subTest(table=table, sql="upsert"), self.assertRaises(sqlite3.IntegrityError):
                    if table.endswith("events"):
                        connection.execute(
                            "insert into local_agent_run_events(id,run_id,attempt_id,sequence_no,event_type,payload_json,created_at) values(?,?,?,?,?,?,?) on conflict(id) do update set event_type='changed'",
                            (row["id"], run["id"], attempt["id"], event["sequence_no"], "changed", "{}", event["created_at"]),
                        )
                    else:
                        connection.execute(
                            "insert into local_agent_artifacts(id,run_id,attempt_id,kind,relative_path,sha256,size_bytes,created_at) values(?,?,?,?,?,?,?,?) on conflict(id) do update set kind='changed'",
                            (row["id"], run["id"], attempt["id"], "changed", "changes.patch", "b" * 64, 12, artifact["created_at"]),
                        )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "insert or replace into local_agent_run_events(id,run_id,attempt_id,sequence_no,event_type,payload_json,created_at) values(?,?,?,?,?,?,?)",
                    (event["id"], run["id"], attempt["id"], event["sequence_no"], "changed", "{}", event["created_at"]),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "insert or replace into local_agent_artifacts(id,run_id,attempt_id,kind,relative_path,sha256,size_bytes,created_at) values(?,?,?,?,?,?,?,?)",
                    (artifact["id"], run["id"], attempt["id"], "changed", "changes.patch", "b" * 64, 12, artifact["created_at"]),
                )

    def test_invalid_transition_and_cross_attempt_event_fail_closed(self) -> None:
        run, attempt = self._create_run_and_attempt()
        other_run, other_attempt = self._create_run_and_attempt_for_new_contract()
        with self.assertRaisesRegex(ValueError, "local_agent_state_transition_invalid"):
            self.repository.transition(run["id"], "created", "locally_applied", {})
        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.append_event(run["id"], other_attempt["id"], "worker_started", {})
        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.add_artifact(
                run["id"], other_attempt["id"], "diff", "cross-run.patch", "c" * 64, 1
            )
        self.assertNotEqual(run["id"], other_run["id"])
        self.assertNotEqual(attempt["id"], other_attempt["id"])

    def _create_run_and_attempt_for_new_contract(
        self,
        *,
        allowed_paths: list[str] | None = None,
    ) -> tuple[dict, dict]:
        self.second_contract_sequence += 1
        second_contract = self.root / "second-task.json"
        payload = json.loads((self.root / "task.json").read_text(encoding="utf-8"))
        payload["task_key"] = f"repository-fixture-{self.second_contract_sequence + 1}"
        if allowed_paths is not None:
            payload["allowed_paths"] = allowed_paths
        # I3 permits concurrent runs only for different project identities.
        # Keep this cross-run integrity helper genuinely cross-project.
        second_project = self.root / f"project-{self.second_contract_sequence}"
        subprocess.run(["git", "clone", "--quiet", str(self.project), str(second_project)], check=True)
        payload["project_path"] = str(second_project)
        second_contract.write_text(json.dumps(payload), encoding="utf-8")
        task = load_local_agent_task(second_contract)
        preflight = assert_local_agent_run_allowed(
            allow_real_agent=True,
            authorization_id=f"local-agent-repository-fixture-{self.second_contract_sequence + 1:03d}",
        )
        run = self.repository.consume_preflight(task, preflight)
        self.repository.bind_workspace(run["id"], self._workspace_binding(run["id"]))
        return run, self.repository.start_attempt(run["id"])

    def test_snapshot_rejects_historical_json_corruption_without_echoing_it(self) -> None:
        run, _ = self._create_run_and_attempt()
        with database.connect_database(self.database_path) as connection:
            connection.execute("drop trigger trg_local_agent_run_events_append_only_update")
            connection.execute(
                "update local_agent_runs set summary_json=? where id=?",
                ('{"secret":"Bearer deliberately-corrupted-token"}', run["id"]),
            )
        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid") as raised:
            self.repository.snapshot(run["id"])
        self.assertNotIn("deliberately-corrupted-token", str(raised.exception))

    def test_orphan_recovery_interrupts_only_missing_worker_in_one_transaction(self) -> None:
        run = self.repository.consume_preflight(self.task, self.preflight)
        self.repository.transition(run["id"], "created", "workspace_ready", {})
        attempt = self.repository.start_attempt(run["id"])
        with patch("app.local_agent_repository._read_process_start_identity", return_value="darwin-proc-bsdinfo-v1:1:2"):
            self.repository.bind_worker_identity(attempt["id"], 12345, "darwin-proc-bsdinfo-v1:1:2")

        self.assertEqual([run["id"]], self.repository.mark_orphaned_attempts_interrupted())
        snapshot = self.repository.snapshot(run["id"])
        self.assertEqual("interrupted", snapshot["run"]["status"])
        self.assertEqual("interrupted", snapshot["attempts"][0]["status"])
        self.assertEqual("attempt_interrupted", snapshot["events"][-1]["event_type"])
        self.assertEqual(attempt["id"], snapshot["events"][-1]["attempt_id"])

    def test_orphan_recovery_keeps_attempt_when_process_identity_is_uncertain(self) -> None:
        run = self.repository.consume_preflight(self.task, self.preflight)
        self.repository.transition(run["id"], "created", "workspace_ready", {})
        attempt = self.repository.start_attempt(run["id"])
        with patch("app.local_agent_repository._read_process_start_identity", return_value="darwin-proc-bsdinfo-v1:1:2"):
            self.repository.bind_worker_identity(attempt["id"], 12345, "darwin-proc-bsdinfo-v1:1:2")

        with patch("app.local_agent_repository.os.kill", side_effect=PermissionError):
            with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
                self.repository.mark_orphaned_attempts_interrupted()

        snapshot = self.repository.snapshot(run["id"])
        self.assertEqual("worker_running", snapshot["run"]["status"])
        self.assertEqual("worker_running", snapshot["attempts"][0]["status"])
        self.assertEqual([], snapshot["events"])

    def test_start_attempt_reserves_worker_atomically_and_rejects_second_active_attempt(self) -> None:
        run = self.repository.consume_preflight(self.task, self.preflight)
        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.start_attempt(run["id"])
        self.repository.transition(run["id"], "created", "workspace_ready", {})
        attempt = self.repository.start_attempt(run["id"])
        self.assertEqual("starting", attempt["status"])
        self.assertEqual("worker_running", self.repository.snapshot(run["id"])["run"]["status"])
        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.start_attempt(run["id"])

    def test_starting_attempt_recovery_after_reopen_is_terminal_and_allows_contiguous_retry(self) -> None:
        run = self.repository.consume_preflight(self.task, self.preflight)
        self.repository.transition(run["id"], "created", "workspace_ready", {})
        first = self.repository.start_attempt(run["id"])
        reopened = LocalAgentRunRepository(self.database_path)

        self.assertEqual([run["id"]], reopened.mark_orphaned_attempts_interrupted())
        recovered = reopened.snapshot(run["id"])
        self.assertEqual("interrupted", recovered["run"]["status"])
        self.assertEqual("interrupted", recovered["attempts"][0]["status"])
        self.assertEqual("worker_start_failed", recovered["attempts"][0]["error_code"])
        self.assertEqual("attempt_interrupted", recovered["events"][-1]["event_type"])

        second = reopened.start_attempt(run["id"])
        retried = reopened.snapshot(run["id"])
        self.assertEqual((1, 2), tuple(item["attempt_no"] for item in retried["attempts"]))
        self.assertEqual("starting", second["status"])
        self.assertEqual(1, sum(item["status"] in {"starting", "worker_running"} for item in retried["attempts"]))
        self.assertNotEqual(first["id"], second["id"])

    def test_abandon_starting_attempt_is_compare_and_set_and_cross_run_safe(self) -> None:
        run, attempt = self._create_run_and_attempt()
        other_run, other_attempt = self._create_run_and_attempt_for_new_contract()
        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.abandon_starting_attempt(other_run["id"], attempt["id"])
        abandoned = self.repository.abandon_starting_attempt(run["id"], attempt["id"])
        self.assertEqual("interrupted", abandoned["status"])
        self.assertEqual("worker_start_failed", abandoned["error_code"])
        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.abandon_starting_attempt(run["id"], attempt["id"])
        self.assertEqual("worker_running", self.repository.snapshot(other_run["id"])["run"]["status"])
        self.assertEqual("starting", other_attempt["status"])

    def test_start_attempt_concurrency_has_exactly_one_winner(self) -> None:
        run = self.repository.consume_preflight(self.task, self.preflight)
        self.repository.transition(run["id"], "created", "workspace_ready", {})
        gate = threading.Barrier(2)
        outcomes: list[object] = []

        def start() -> None:
            try:
                gate.wait(timeout=2)
                outcomes.append(LocalAgentRunRepository(self.database_path).start_attempt(run["id"]))
            except Exception as error:
                outcomes.append(error)

        workers = [threading.Thread(target=start), threading.Thread(target=start)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
        self.assertEqual(1, sum(isinstance(outcome, dict) for outcome in outcomes))
        self.assertEqual(1, sum(isinstance(outcome, Exception) for outcome in outcomes))
        self.assertEqual(1, len(self.repository.snapshot(run["id"])["attempts"]))

    def test_complete_attempt_drives_full_happy_path(self) -> None:
        run, attempt = self._create_run_and_attempt()
        with patch("app.local_agent_repository._read_process_start_identity", return_value="darwin-proc-bsdinfo-v1:1:2"):
            bound = self.repository.bind_worker_identity(attempt["id"], 12345, "darwin-proc-bsdinfo-v1:1:2")
        self.assertEqual("worker_running", bound["status"])
        completed = self.repository.complete_attempt(attempt["id"], "completed")
        self.assertEqual("completed", completed["status"])
        self.assertEqual("verifying", self.repository.snapshot(run["id"])["run"]["status"])
        self.repository.transition(run["id"], "verifying", "reviewing", {})
        self.repository.transition(run["id"], "reviewing", "awaiting_human_confirmation", {})
        with self.assertRaisesRegex(ValueError, "local_agent_state_transition_invalid"):
            self.repository.transition(run["id"], "awaiting_human_confirmation", "locally_applied", {})
        self.assertEqual("awaiting_human_confirmation", self.repository.snapshot(run["id"])["run"]["status"])

    def test_worker_terminal_outcomes_and_review_terminals_follow_the_state_graph(self) -> None:
        for outcome, error_code in (
            ("failed_scope", "scope_denied"),
            ("failed_worker", "worker_failed"),
            ("cancelled", "operator_cancelled"),
            ("interrupted", "worker_interrupted"),
        ):
            with self.subTest(outcome=outcome):
                run, attempt = self._create_run_and_attempt_for_new_contract()
                self._bind_worker(attempt)
                self.repository.complete_attempt(attempt["id"], outcome, error_code)
                snapshot = self.repository.snapshot(run["id"])
                self.assertEqual(outcome, snapshot["run"]["status"])
                self.assertEqual(outcome, snapshot["attempts"][0]["status"])

        for outcome in ("failed_review", "changes_requested"):
            with self.subTest(outcome=outcome):
                run, attempt = self._create_run_and_attempt_for_new_contract()
                self._bind_worker(attempt)
                self.repository.complete_attempt(attempt["id"], "completed")
                self.repository.transition(run["id"], "verifying", "reviewing", {})
                self.repository.transition(run["id"], "reviewing", outcome, {})
                self.assertEqual(outcome, self.repository.snapshot(run["id"])["run"]["status"])

        run, attempt = self._create_run_and_attempt_for_new_contract()
        self._bind_worker(attempt)
        self.repository.complete_attempt(attempt["id"], "completed")
        self.repository.transition(run["id"], "verifying", "reviewing", {})
        self.repository.transition(run["id"], "reviewing", "awaiting_human_confirmation", {})
        self.repository.transition(run["id"], "awaiting_human_confirmation", "confirmation_expired", {})
        self.assertEqual("confirmation_expired", self.repository.snapshot(run["id"])["run"]["status"])

    def test_retryable_terminal_states_allocate_contiguous_new_starting_attempt(self) -> None:
        retry_paths = ("interrupted", "failed_worker", "failed_verification", "changes_requested")
        for target in retry_paths:
            with self.subTest(target=target):
                run, attempt = self._create_run_and_attempt_for_new_contract()
                self._bind_worker(attempt)
                if target == "failed_verification":
                    self.repository.complete_attempt(attempt["id"], "completed")
                    self.repository.transition(run["id"], "verifying", "failed_verification", {})
                elif target == "changes_requested":
                    self.repository.complete_attempt(attempt["id"], "completed")
                    self.repository.transition(run["id"], "verifying", "reviewing", {})
                    self.repository.transition(run["id"], "reviewing", "changes_requested", {})
                else:
                    self.repository.complete_attempt(attempt["id"], target, f"worker_{target}")
                retry = self.repository.start_attempt(run["id"])
                snapshot = self.repository.snapshot(run["id"])
                self.assertEqual("worker_running", snapshot["run"]["status"])
                self.assertEqual((1, 2), tuple(item["attempt_no"] for item in snapshot["attempts"]))
                self.assertEqual("starting", retry["status"])

    def test_all_retryable_attempt_outcomes_exhaust_on_third_attempt(self) -> None:
        for target in ("interrupted", "failed_verification", "changes_requested"):
            with self.subTest(target=target):
                run, attempt = self._create_run_and_attempt_for_new_contract()
                for attempt_no in range(1, 4):
                    self._bind_worker(attempt)
                    if target == "interrupted":
                        self.repository.complete_attempt(attempt["id"], "interrupted", "worker_interrupted")
                    else:
                        self.repository.complete_attempt(attempt["id"], "completed")
                        self.repository.transition(run["id"], "verifying", "reviewing" if target == "changes_requested" else "failed_verification", {})
                        if target == "changes_requested":
                            self.repository.transition(run["id"], "reviewing", "changes_requested", {})
                    snapshot = self.repository.snapshot(run["id"])
                    if attempt_no < 3:
                        self.assertEqual(target, snapshot["run"]["status"])
                        attempt = self.repository.start_attempt(run["id"])
                    else:
                        self.assertEqual("attempts_exhausted", snapshot["run"]["status"])
                        self.assertEqual("attempt_budget_exhausted", snapshot["events"][-1]["event_type"])
                        with database.connect_database(self.database_path) as connection:
                            self.assertIsNone(connection.execute(
                                "select 1 from local_agent_project_leases where run_id=?", (run["id"],),
                            ).fetchone())

    def test_append_only_guards_hold_when_recursive_triggers_are_disabled(self) -> None:
        run, attempt = self._create_run_and_attempt()
        event = self.repository.append_event(run["id"], attempt["id"], "worker_started", {})
        artifact = self.repository.add_artifact(run["id"], attempt["id"], "diff", "changes.patch", "a" * 64, 1)
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute("pragma recursive_triggers = off")
            for table, row, replacement in (
                ("local_agent_run_events", event, "insert or replace into local_agent_run_events(id,run_id,attempt_id,sequence_no,event_type,payload_json,created_at) values(?,?,?,?,?,?,?)"),
                ("local_agent_artifacts", artifact, "insert or replace into local_agent_artifacts(id,run_id,attempt_id,kind,relative_path,sha256,size_bytes,created_at) values(?,?,?,?,?,?,?,?)"),
            ):
                for mutation in ("update", "delete", "upsert", "replace", "replace_natural"):
                    with self.subTest(table=table, mutation=mutation), self.assertRaises(sqlite3.IntegrityError):
                        if table.endswith("events") and mutation == "update":
                            connection.execute(f"update {table} set event_type='changed' where id=?", (row["id"],))
                        elif table.endswith("events") and mutation == "delete":
                            connection.execute(f"delete from {table} where id=?", (row["id"],))
                        elif table.endswith("events") and mutation == "upsert":
                            connection.execute(
                                "insert into local_agent_run_events(id,run_id,attempt_id,sequence_no,event_type,payload_json,created_at) values(?,?,?,?,?,?,?) on conflict(id) do update set event_type='changed'",
                                (row["id"], run["id"], attempt["id"], event["sequence_no"], "changed", "{}", event["created_at"]),
                            )
                        elif table.endswith("events") and mutation == "replace_natural":
                            connection.execute(
                                "insert or replace into local_agent_run_events(run_id,attempt_id,sequence_no,event_type,payload_json,created_at) values(?,?,?,?,?,?)",
                                (run["id"], attempt["id"], event["sequence_no"], "changed", "{}", event["created_at"]),
                            )
                        elif table.endswith("events"):
                            connection.execute(replacement, (row["id"], run["id"], attempt["id"], event["sequence_no"], "changed", "{}", event["created_at"]))
                        elif mutation == "update":
                            connection.execute(f"update {table} set kind='changed' where id=?", (row["id"],))
                        elif mutation == "delete":
                            connection.execute(f"delete from {table} where id=?", (row["id"],))
                        elif mutation == "upsert":
                            connection.execute(
                                "insert into local_agent_artifacts(id,run_id,attempt_id,kind,relative_path,sha256,size_bytes,created_at) values(?,?,?,?,?,?,?,?) on conflict(id) do update set kind='changed'",
                                (row["id"], run["id"], attempt["id"], "changed", "changes.patch", "b" * 64, 2, artifact["created_at"]),
                            )
                        else:
                            if mutation == "replace_natural":
                                connection.execute(
                                    "insert or replace into local_agent_artifacts(run_id,attempt_id,kind,relative_path,sha256,size_bytes,created_at) values(?,?,?,?,?,?,?)",
                                    (run["id"], attempt["id"], "diff", "changes.patch", "b" * 64, 2, artifact["created_at"]),
                                )
                            else:
                                connection.execute(replacement, (row["id"], run["id"], attempt["id"], "changed", "changes.patch", "b" * 64, 2, artifact["created_at"]))

    def test_snapshot_rejects_invalid_binding_status_and_attempt_sequence(self) -> None:
        run, attempt = self._create_run_and_attempt()
        with database.connect_database(self.database_path) as connection:
            connection.execute("pragma ignore_check_constraints = on")
            connection.execute("update local_agent_runs set project_identity_json=?, status=? where id=?", ("{}", "polluted", run["id"]))
            connection.execute("update local_agent_attempts set attempt_no=2, status='polluted' where id=?", (attempt["id"],))
        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.snapshot(run["id"])

    def test_snapshot_rejects_a_terminal_attempt_that_was_never_bound_to_a_worker(self) -> None:
        run, attempt = self._create_run_and_attempt()
        with database.connect_database(self.database_path) as connection:
            connection.execute(
                "update local_agent_runs set status='failed_worker' where id=?",
                (run["id"],),
            )
            connection.execute(
                "update local_agent_attempts set status='failed_worker', error_code='worker_failed', finished_at=? where id=?",
                (database.now_iso(), attempt["id"]),
            )
        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.snapshot(run["id"])

    def test_snapshot_rejects_reversed_timestamps_and_impossible_state_families(self) -> None:
        run = self.repository.consume_preflight(self.task, self.preflight)
        with database.connect_database(self.database_path) as connection:
            connection.execute(
                "update local_agent_runs set status='locally_applied', updated_at='2000-01-01T00:00:00+00:00' where id=?",
                (run["id"],),
            )
        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.snapshot(run["id"])

    def test_snapshot_rejects_every_run_state_family_when_attempt_history_is_incoherent(self) -> None:
        for status in (
            "created", "workspace_ready", "verifying", "reviewing",
            "awaiting_human_confirmation", "locally_applied", "interrupted",
            "failed_scope", "failed_worker", "cancelled", "failed_verification",
            "changes_requested", "failed_review", "confirmation_expired",
        ):
            with self.subTest(status=status):
                run, _ = self._create_run_and_attempt_for_new_contract()
                with database.connect_database(self.database_path) as connection:
                    connection.execute("update local_agent_runs set status=? where id=?", (status, run["id"]))
                with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
                    self.repository.snapshot(run["id"])

        run = self.repository.consume_preflight(self.task, self.preflight)
        with database.connect_database(self.database_path) as connection:
            connection.execute("update local_agent_runs set status='worker_running' where id=?", (run["id"],))
        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.snapshot(run["id"])

        run, attempt = self._create_run_and_attempt_for_new_contract()
        self._bind_worker(attempt)
        self.repository.complete_attempt(attempt["id"], "completed")
        with database.connect_database(self.database_path) as connection:
            connection.execute("update local_agent_runs set status='failed_scope' where id=?", (run["id"],))
            connection.execute("update local_agent_attempts set finished_at='2000-01-01T00:00:00+00:00' where id=?", (attempt["id"],))
        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.snapshot(run["id"])

    def test_exact_digest_fields_accept_deterministic_hex_values(self) -> None:
        deterministic_authorization = assert_local_agent_run_allowed(
            allow_real_agent=True,
            authorization_id="authorization-245",
        )
        run = self.repository.consume_preflight(self.task, deterministic_authorization)
        self.assertEqual("created", run["status"])
        second_digest = "d1e5982d6d5af49dc24c43c14ca8e2fac2e18357178809fee3772cce9ff117b6"
        with database.connect_database(self.database_path) as connection:
            connection.execute("update local_agent_runs set contract_hash=? where id=?", (second_digest, run["id"]))
        self.assertEqual(second_digest, self.repository.snapshot(run["id"])["run"]["contract_hash"])

    def test_harness_decision_event_accepts_pii_shaped_sha256_digest(self) -> None:
        run, attempt = self._create_run_and_attempt()
        payload = {
            "plan_version": 1,
            "supersedes_plan_version": None,
            "decision_kind": "initial_plan",
            "failure_code": "initial_execution",
            "decision_digest": "sha256:7cff524767863421371581b5319a473a1e2613e46b0af04b7eaf63068ffad89d",
            "must_reinspect": True,
            "execute_only": True,
        }

        event = self.repository.append_event(
            run["id"], attempt["id"], "harness_decision_issued", payload,
        )

        self.assertEqual(payload, event["payload"])
        self.assertEqual(payload, self.repository.snapshot(run["id"])["events"][-1]["payload"])
        for mutation in (
            {"decision_digest": "7cff524767863421371581b5319a473a1e2613e46b0af04b7eaf63068ffad89d"},
            {"decision_kind": "replan"},
            {"supersedes_plan_version": 1},
            {"must_reinspect": False},
            {"extra": "value"},
        ):
            with self.subTest(mutation=mutation), self.assertRaisesRegex(
                ValueError, "local_agent_storage_invalid",
            ):
                self.repository.append_event(
                    run["id"], attempt["id"], "harness_decision_issued",
                    {**payload, **mutation},
                )

    def test_protocol_rejection_event_rejects_polluted_payload_without_echo(self) -> None:
        run, attempt = self._create_run_and_attempt()
        self._bind_worker(attempt)
        payload = {
            "candidate_event_type": "unknown_event_type",
            "candidate_item_type": "unknown_item_type",
            "top_level_keys": 5,
            "raw_line_sha256": "1631f96cbf2dfe490b988c3a4ae6d996d39d27c194a370f8cc48160191ab2d29",
            "sequence_no": 3,
            "fsm_state": "turn_active",
            "elapsed_bucket": "60_179s",
            "error_container_kind": "object",
            "error_known_keys": 5,
            "error_field_count": 3,
        }
        event = self.repository.append_event(run["id"], attempt["id"], "worker_protocol_rejected", payload)
        self.assertEqual(payload, event["payload"])
        for key, candidate in (
            ("candidate_event_type", "future.event"),
            ("candidate_event_type", "token_abcdefghijklmnopqrstuvwx"),
            ("candidate_event_type", "unknown_item_type"),
            ("candidate_item_type", "019c9d85-1d4c-7123-8f2a-123456789abc"),
            ("candidate_item_type", "unknown_event_type"),
            ("error_container_kind", "future_secret"),
            ("error_known_keys", 16),
            ("error_field_count", 17),
        ):
            with self.subTest(key=key, candidate=candidate), self.assertRaisesRegex(ValueError, "worker_protocol_audit_invalid"):
                self.repository.append_event(
                    run["id"], attempt["id"], "worker_protocol_rejected",
                    {**payload, key: candidate},
                )
        with self.assertRaisesRegex(ValueError, "worker_protocol_audit_invalid"):
            self.repository.append_event(
                run["id"], attempt["id"], "worker_protocol_rejected",
                {**payload, "error_known_keys": 15, "error_field_count": 0},
            )
        with database.connect_database(self.database_path) as connection:
            connection.execute("drop trigger trg_local_agent_run_events_append_only_update")
            connection.execute(
                "update local_agent_run_events set payload_json=? where id=?",
                (json.dumps({**payload, "raw": "/tmp/secret-token"}), event["id"]),
            )
        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid") as raised:
            self.repository.snapshot(run["id"])
        self.assertNotIn("secret-token", str(raised.exception))

        for key, candidate in (
            ("candidate_event_type", "token_abcdefghijklmnopqrstuvwx"),
            ("candidate_item_type", "unknown_event_type"),
        ):
            with database.connect_database(self.database_path) as connection:
                connection.execute(
                    "update local_agent_run_events set payload_json=? where id=?",
                    (json.dumps({**payload, key: candidate}), event["id"]),
                )
            with self.subTest(key=key, candidate=candidate), self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
                self.repository.snapshot(run["id"])

    def test_review_failure_rejects_impossible_validation_code(self) -> None:
        run, attempt = self._create_run_and_attempt()
        self._bind_worker(attempt)
        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.fail_review(
                run["id"], attempt["id"],
                audit={"validation_code": "finding_invalid"},
            )

    def test_review_fields_shape_rejects_impossible_or_extra_audit_data(self) -> None:
        run, attempt = self._create_run_and_attempt()
        self._bind_worker(attempt)
        self.repository.complete_attempt(attempt["id"], "completed")
        self.repository.transition(run["id"], "verifying", "reviewing", {})
        self.repository.fail_review(
            run["id"], attempt["id"],
            audit={
                "validation_code": "fields_invalid",
                "value_kind": "object",
                "known_fields_mask": 31,
                "field_count": 6,
            },
        )
        event = next(
            item for item in self.repository.snapshot(run["id"])["events"]
            if item["event_type"] == "review_failed"
        )
        self.assertEqual(31, event["payload"]["known_fields_mask"])
        for mutation in (
            {"known_fields_mask": 31, "field_count": 4},
            {"value_kind": "other", "known_fields_mask": 1, "field_count": 1},
            {"unknown_key": "Bearer hidden-value"},
        ):
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
                self.repository.fail_review(
                    run["id"], attempt["id"],
                    audit={
                        "validation_code": "fields_invalid",
                        "value_kind": "object",
                        "known_fields_mask": 31,
                        "field_count": 6,
                        **mutation,
                    },
                )

    def test_darwin_process_identity_layout_and_live_probe(self) -> None:
        if sys.platform != "darwin":
            with self.assertRaises(RuntimeError):
                _read_process_start_identity(os.getpid())
            return
        self.assertEqual(136, ctypes.sizeof(_ProcBsdInfo))
        self.assertEqual(120, _ProcBsdInfo.start_seconds.offset)
        self.assertEqual(128, _ProcBsdInfo.start_microseconds.offset)
        first = _read_process_start_identity(os.getpid())
        second = _read_process_start_identity(os.getpid())
        self.assertRegex(first, r"^darwin-proc-bsdinfo-v1:[1-9][0-9]*:[0-9]{1,6}$")
        self.assertEqual(first, second)

    def test_snapshot_reads_one_consistent_transactional_view(self) -> None:
        run = self.repository.consume_preflight(self.task, self.preflight)
        self.repository.transition(run["id"], "created", "workspace_ready", {})
        original_connect = database.connect_database
        writer = LocalAgentRunRepository(self.database_path)

        class InterleavingConnection:
            def __init__(self, connection):
                self.connection = connection
                self.interleaved = False

            def __enter__(self):
                self.connection.__enter__()
                return self

            def __exit__(self, *arguments):
                return self.connection.__exit__(*arguments)

            def execute(self, sql, parameters=()):
                result = self.connection.execute(sql, parameters)
                if not self.interleaved and "select * from local_agent_runs" in sql.lower():
                    self.interleaved = True
                    database.connect_database = original_connect
                    try:
                        writer.start_attempt(run["id"])
                    finally:
                        database.connect_database = lambda path: self
                return result

        proxy = InterleavingConnection(original_connect(self.database_path))
        with patch("app.local_agent_repository.database.connect_database", return_value=proxy):
            snapshot = self.repository.snapshot(run["id"])
        self.assertEqual("workspace_ready", snapshot["run"]["status"])
        self.assertEqual([], snapshot["attempts"])

    def test_open_learning_connection_uses_the_repository_connection_factory(self) -> None:
        opened: list[sqlite3.Connection] = []

        def factory() -> sqlite3.Connection:
            connection = database.connect_database(self.database_path)
            opened.append(connection)
            return connection

        repository = LocalAgentRunRepository(self.database_path, connection_factory=factory)
        with repository.open_learning_connection() as connection:
            self.assertIsNotNone(connection.execute("select 1").fetchone())
        self.assertEqual(1, len(opened))

    def test_read_learning_binding_requires_the_durable_current_attempt_and_contract(self) -> None:
        run, first_attempt = self._create_run_and_attempt()
        binding = self.repository.read_learning_binding(
            self.task, run_id=run["id"], attempt_id=first_attempt["id"],
        )

        self.assertEqual(run["id"], binding["run_id"])
        self.assertEqual(first_attempt["id"], binding["attempt_id"])
        self.assertEqual(self.task.task_key, binding["task_key"])
        self.assertEqual(self.task.contract_hash, binding["contract_hash"])
        self.assertRegex(binding["workspace_fingerprint"], r"^ws[0-9a-f]{21}$")
        self.assertNotIn("authorization_hash", binding)

        self._bind_worker(first_attempt)
        self.repository.complete_attempt(first_attempt["id"], "completed")
        self.repository.transition(run["id"], "verifying", "failed_verification", {})
        second_attempt = self.repository.start_attempt(run["id"])
        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.read_learning_binding(
                self.task, run_id=run["id"], attempt_id=first_attempt["id"],
            )
        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.read_learning_binding(
                dataclasses.replace(self.task, contract_hash="0" * 64),
                run_id=run["id"], attempt_id=second_attempt["id"],
            )

    def test_read_learning_binding_rejects_missing_or_structurally_invalid_workspace_binding(self) -> None:
        unbound = self.repository.consume_preflight(self.task, self.preflight)
        self.repository.transition(unbound["id"], "created", "workspace_ready", {})
        unbound_attempt = self.repository.start_attempt(unbound["id"])
        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.read_learning_binding(
                self.task, run_id=unbound["id"], attempt_id=unbound_attempt["id"],
            )

        self.second_contract_sequence += 1
        contract = self.root / "invalid-binding-task.json"
        payload = json.loads((self.root / "task.json").read_text(encoding="utf-8"))
        payload["task_key"] = "repository-invalid-binding-2"
        project = self.root / "project-invalid-binding"
        subprocess.run(["git", "clone", "--quiet", str(self.project), str(project)], check=True)
        payload["project_path"] = str(project)
        contract.write_text(json.dumps(payload), encoding="utf-8")
        task = load_local_agent_task(contract)
        invalid = self.repository.consume_preflight(
            task,
            assert_local_agent_run_allowed(
                allow_real_agent=True,
                authorization_id="local-agent-repository-invalid-binding-002",
            ),
        )
        with database.connect_database(self.database_path) as connection:
            connection.execute(
                "insert into local_agent_workspace_bindings(run_id, binding_json, created_at) values(?, ?, ?)",
                (invalid["id"], "{}", database.now_iso()),
            )
        self.repository.transition(invalid["id"], "created", "workspace_ready", {})
        invalid_attempt = self.repository.start_attempt(invalid["id"])
        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.read_learning_binding(
                task, run_id=invalid["id"], attempt_id=invalid_attempt["id"],
            )

    def test_learning_observation_rejects_an_active_rule_from_another_durable_run(self) -> None:
        service = RepairLearningService(self.repository)
        source_run, source_attempt = self._create_run_and_attempt()
        self._bind_worker(source_attempt)
        self.repository.complete_attempt(source_attempt["id"], "completed")
        self.repository.transition(source_run["id"], "verifying", "failed_verification", {})
        service.record_verification_failure(
            task=self.task,
            run_id=source_run["id"],
            attempt_id=source_attempt["id"],
            summary="verification failed",
        )
        source_rule = service.snapshot_for_run(source_run["id"])["rules"][0]

        target_run, target_attempt = self._create_run_and_attempt_for_new_contract()
        target_task = load_local_agent_task(self.root / "second-task.json")
        self._bind_worker(target_attempt)
        self.repository.complete_attempt(target_attempt["id"], "completed")
        self.repository.transition(target_run["id"], "verifying", "reviewing", {})
        self.repository.transition(target_run["id"], "reviewing", "awaiting_human_confirmation", {})
        binding = self.repository.read_learning_binding(
            target_task, run_id=target_run["id"], attempt_id=target_attempt["id"],
        )

        with self.assertRaisesRegex(ValueError, "repair_learning_input_invalid"):
            self.repository.record_learning_observation(
                target_task,
                rule_id=source_rule["id"],
                run_id=target_run["id"],
                attempt_id=target_attempt["id"],
                task_key=target_task.task_key,
                workspace_fingerprint=binding["workspace_fingerprint"],
                outcome=RuleObservationOutcome.MATCHED,
                evidence={"event": "verification_and_review_passed"},
                allowed_run_statuses=frozenset({"awaiting_human_confirmation"}),
            )

        self.assertEqual([], service.snapshot_for_run(target_run["id"])["observations"])
        self.assertEqual(0, service.snapshot_for_run(source_run["id"])["rules"][0]["verified_task_count"])

    def test_learning_observation_rejects_a_trial_rule_outside_durable_contract_scope(self) -> None:
        service = RepairLearningService(self.repository)
        source_run, source_attempt = self._create_run_and_attempt()
        self._bind_worker(source_attempt)
        self.repository.complete_attempt(source_attempt["id"], "completed")
        self.repository.transition(source_run["id"], "verifying", "failed_verification", {})
        service.record_verification_failure(
            task=self.task,
            run_id=source_run["id"],
            attempt_id=source_attempt["id"],
            summary="verification failed",
        )
        source_rule = service.snapshot_for_run(source_run["id"])["rules"][0]
        service._learning_repository.advance_rule_state(
            rule_id=source_rule["id"],
            expected_state_version=source_rule["state_version"],
            new_state="trial",
        )

        target_run, target_attempt = self._create_run_and_attempt_for_new_contract(
            allowed_paths=["unrelated.py"],
        )
        target_task = load_local_agent_task(self.root / "second-task.json")
        self._bind_worker(target_attempt)
        self.repository.complete_attempt(target_attempt["id"], "completed")
        self.repository.transition(target_run["id"], "verifying", "reviewing", {})
        self.repository.transition(target_run["id"], "reviewing", "awaiting_human_confirmation", {})
        binding = self.repository.read_learning_binding(
            target_task, run_id=target_run["id"], attempt_id=target_attempt["id"],
        )

        with self.assertRaisesRegex(ValueError, "repair_learning_input_invalid"):
            self.repository.record_learning_observation(
                target_task,
                rule_id=source_rule["id"],
                run_id=target_run["id"],
                attempt_id=target_attempt["id"],
                task_key=target_task.task_key,
                workspace_fingerprint=binding["workspace_fingerprint"],
                outcome=RuleObservationOutcome.MATCHED,
                evidence={"event": "verification_and_review_passed"},
                allowed_run_statuses=frozenset({"awaiting_human_confirmation"}),
            )

        self.assertEqual([], service.snapshot_for_run(target_run["id"])["observations"])
        self.assertEqual(0, service.snapshot_for_run(source_run["id"])["rules"][0]["verified_task_count"])

    def test_learning_observation_rejects_raw_patch_counterexample_evidence(self) -> None:
        service = RepairLearningService(self.repository)
        run, first_attempt = self._create_run_and_attempt()
        self._bind_worker(first_attempt)
        self.repository.complete_attempt(first_attempt["id"], "completed")
        self.repository.transition(run["id"], "verifying", "failed_verification", {})
        service.record_verification_failure(
            task=self.task,
            run_id=run["id"],
            attempt_id=first_attempt["id"],
            summary="verification failed",
        )
        rule = service.snapshot_for_run(run["id"])["rules"][0]
        current_attempt = self.repository.start_attempt(run["id"])
        self._bind_worker(current_attempt)
        self.repository.complete_attempt(current_attempt["id"], "completed")
        self.repository.transition(run["id"], "verifying", "reviewing", {})
        self.repository.transition(run["id"], "reviewing", "awaiting_human_confirmation", {})
        binding = self.repository.read_learning_binding(
            self.task, run_id=run["id"], attempt_id=current_attempt["id"],
        )
        raw_patch = "\t--- a/secret.py\n  +++ b/secret.py\n@@ -1 +1 @@\n-raw_old_value\n+raw_new_value"

        with self.assertRaisesRegex(ValueError, "repair_learning_input_invalid"):
            self.repository.record_learning_observation(
                self.task,
                rule_id=rule["id"],
                run_id=run["id"],
                attempt_id=current_attempt["id"],
                task_key=self.task.task_key,
                workspace_fingerprint=binding["workspace_fingerprint"],
                outcome=RuleObservationOutcome.NOT_MATCHED,
                evidence={
                    "event": "counterexample",
                    "summary_status": "safe",
                    "summary": raw_patch,
                },
                allowed_run_statuses=frozenset({"awaiting_human_confirmation", "changes_requested"}),
            )

        self.assertEqual([], service.snapshot_for_run(run["id"])["observations"])
        with self.repository.open_learning_connection() as connection:
            self.assertIsNone(connection.execute(
                "select 1 from repair_learning_observations where rule_id=?", (rule["id"],),
            ).fetchone())

    def test_learning_observation_rejects_extended_standalone_secret_evidence(self) -> None:
        tokens = (
            "gho_" + "A" * 36,
            "ghu_" + "A" * 36,
            "ghs_" + "A" * 36,
            "ghr_" + "A" * 36,
            "glpat-" + "A" * 20,
            "xapp-" + "A" * 20,
        )
        service = RepairLearningService(self.repository)

        for token in tokens:
            with self.subTest(token=token[:4]):
                run, first_attempt = self._create_run_and_attempt_for_new_contract()
                task = load_local_agent_task(self.root / "second-task.json")
                self._bind_worker(first_attempt)
                self.repository.complete_attempt(first_attempt["id"], "completed")
                self.repository.transition(run["id"], "verifying", "failed_verification", {})
                service.record_verification_failure(
                    task=task,
                    run_id=run["id"],
                    attempt_id=first_attempt["id"],
                    summary="verification failed",
                )
                rule = service.snapshot_for_run(run["id"])["rules"][0]
                current_attempt = self.repository.start_attempt(run["id"])
                self._bind_worker(current_attempt)
                self.repository.complete_attempt(current_attempt["id"], "completed")
                self.repository.transition(run["id"], "verifying", "reviewing", {})
                self.repository.transition(run["id"], "reviewing", "awaiting_human_confirmation", {})
                binding = self.repository.read_learning_binding(
                    task, run_id=run["id"], attempt_id=current_attempt["id"],
                )

                with self.assertRaisesRegex(ValueError, "repair_learning_input_invalid"):
                    self.repository.record_learning_observation(
                        task,
                        rule_id=rule["id"],
                        run_id=run["id"],
                        attempt_id=current_attempt["id"],
                        task_key=task.task_key,
                        workspace_fingerprint=binding["workspace_fingerprint"],
                        outcome=RuleObservationOutcome.NOT_MATCHED,
                        evidence={
                            "event": "counterexample",
                            "summary_status": "safe",
                            "summary": token,
                        },
                        allowed_run_statuses=frozenset({"awaiting_human_confirmation", "changes_requested"}),
                    )

                with self.repository.open_learning_connection() as connection:
                    self.assertIsNone(connection.execute(
                        "select 1 from repair_learning_observations where run_id=?", (run["id"],),
                    ).fetchone())

    def test_learning_observation_rejects_unicode_prefixed_standalone_secret_evidence(self) -> None:
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
        service = RepairLearningService(self.repository)

        for token in tokens:
            with self.subTest(token=token[:8]):
                run, first_attempt = self._create_run_and_attempt_for_new_contract()
                task = load_local_agent_task(self.root / "second-task.json")
                self._bind_worker(first_attempt)
                self.repository.complete_attempt(first_attempt["id"], "completed")
                self.repository.transition(run["id"], "verifying", "failed_verification", {})
                service.record_verification_failure(
                    task=task,
                    run_id=run["id"],
                    attempt_id=first_attempt["id"],
                    summary="verification failed",
                )
                rule = service.snapshot_for_run(run["id"])["rules"][0]
                current_attempt = self.repository.start_attempt(run["id"])
                self._bind_worker(current_attempt)
                self.repository.complete_attempt(current_attempt["id"], "completed")
                self.repository.transition(run["id"], "verifying", "reviewing", {})
                self.repository.transition(run["id"], "reviewing", "awaiting_human_confirmation", {})
                binding = self.repository.read_learning_binding(
                    task, run_id=run["id"], attempt_id=current_attempt["id"],
                )

                with self.assertRaisesRegex(ValueError, "repair_learning_input_invalid"):
                    self.repository.record_learning_observation(
                        task,
                        rule_id=rule["id"],
                        run_id=run["id"],
                        attempt_id=current_attempt["id"],
                        task_key=task.task_key,
                        workspace_fingerprint=binding["workspace_fingerprint"],
                        outcome=RuleObservationOutcome.NOT_MATCHED,
                        evidence={
                            "event": "counterexample",
                            "summary_status": "safe",
                            "summary": "中文" + token,
                        },
                        allowed_run_statuses=frozenset({"awaiting_human_confirmation", "changes_requested"}),
                    )

                with self.repository.open_learning_connection() as connection:
                    self.assertIsNone(connection.execute(
                        "select 1 from repair_learning_observations where run_id=?", (run["id"],),
                    ).fetchone())

    def test_learning_observation_rejects_unicode_whitespace_prefixed_patch_evidence(self) -> None:
        service = RepairLearningService(self.repository)
        run, first_attempt = self._create_run_and_attempt()
        self._bind_worker(first_attempt)
        self.repository.complete_attempt(first_attempt["id"], "completed")
        self.repository.transition(run["id"], "verifying", "failed_verification", {})
        service.record_verification_failure(
            task=self.task,
            run_id=run["id"],
            attempt_id=first_attempt["id"],
            summary="verification failed",
        )
        rule = service.snapshot_for_run(run["id"])["rules"][0]
        current_attempt = self.repository.start_attempt(run["id"])
        self._bind_worker(current_attempt)
        self.repository.complete_attempt(current_attempt["id"], "completed")
        self.repository.transition(run["id"], "verifying", "reviewing", {})
        self.repository.transition(run["id"], "reviewing", "awaiting_human_confirmation", {})
        binding = self.repository.read_learning_binding(
            self.task, run_id=run["id"], attempt_id=current_attempt["id"],
        )

        for prefix in ("\u00a0", "\u2003", "\f", "\v"):
            with self.subTest(prefix=ascii(prefix)):
                raw_patch = f"{prefix}--- a/secret.py\n{prefix}+++ b/secret.py\n-raw_old\n+raw_new"
                with self.assertRaisesRegex(ValueError, "repair_learning_input_invalid"):
                    self.repository.record_learning_observation(
                        self.task,
                        rule_id=rule["id"],
                        run_id=run["id"],
                        attempt_id=current_attempt["id"],
                        task_key=self.task.task_key,
                        workspace_fingerprint=binding["workspace_fingerprint"],
                        outcome=RuleObservationOutcome.NOT_MATCHED,
                        evidence={
                            "event": "counterexample",
                            "summary_status": "safe",
                            "summary": raw_patch,
                        },
                        allowed_run_statuses=frozenset({"awaiting_human_confirmation", "changes_requested"}),
                    )

        self.assertEqual([], service.snapshot_for_run(run["id"])["observations"])
        with self.repository.open_learning_connection() as connection:
            self.assertIsNone(connection.execute(
                "select 1 from repair_learning_observations where rule_id=?", (rule["id"],),
            ).fetchone())

    def test_learning_retrospective_rejects_raw_patch_or_secret_safe_summary(self) -> None:
        unsafe_summaries = (
            {
                "summary_status": "safe",
                "summary": "\u00a0--- a/secret.py\n\u00a0+++ b/secret.py\n-raw_old\n+raw_new",
            },
            {
                "summary_status": "safe",
                "summary": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
            },
        )

        for index, safe_summary in enumerate(unsafe_summaries):
            with self.subTest(safe_summary=safe_summary["summary"][:8]):
                if index == 0:
                    task = self.task
                    run, attempt = self._create_run_and_attempt()
                else:
                    run, attempt = self._create_run_and_attempt_for_new_contract()
                    task = load_local_agent_task(self.root / "second-task.json")
                self._bind_worker(attempt)
                self.repository.complete_attempt(attempt["id"], "completed")
                self.repository.transition(run["id"], "verifying", "failed_verification", {})
                source_key = f"retro-r{run['id']}-a{attempt['id']}-s1-c1"
                with self.assertRaisesRegex(ValueError, "repair_learning_input_invalid"):
                    self.repository.record_learning_retrospective(
                        task,
                        source_key=source_key,
                        run_id=run["id"],
                        attempt_id=attempt["id"],
                        source_kind="run_observation",
                        root_cause_kind="verification_failure",
                        safe_summary=safe_summary,
                        task_context=self._learning_task_context(task, run["id"]),
                        allowed_run_statuses=frozenset({"verifying", "failed_verification"}),
                    )
                with self.repository.open_learning_connection() as connection:
                    self.assertIsNone(connection.execute(
                        "select 1 from repair_retrospectives where source_key=?", (source_key,),
                    ).fetchone())

    def test_learning_retrospective_rejects_raw_patch_or_secret_task_context(self) -> None:
        raw_context_values = (
            "\u00a0--- a/secret.py\n\u00a0+++ b/secret.py\n-raw_old\n+raw_new",
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
        )

        for index, raw in enumerate(raw_context_values):
            with self.subTest(task_context=raw[:8]):
                if index == 0:
                    task = self.task
                    run, attempt = self._create_run_and_attempt()
                else:
                    run, attempt = self._create_run_and_attempt_for_new_contract()
                    task = load_local_agent_task(self.root / "second-task.json")
                self._bind_worker(attempt)
                self.repository.complete_attempt(attempt["id"], "completed")
                self.repository.transition(run["id"], "verifying", "failed_verification", {})
                source_key = f"retro-r{run['id']}-a{attempt['id']}-s1-c1"
                candidate = self._learning_task_context(task, run["id"])
                candidate = dict(candidate)
                candidate["failure_sources"] = [raw]
                with self.assertRaisesRegex(ValueError, "repair_learning_input_invalid"):
                    self.repository.record_learning_retrospective(
                        task,
                        source_key=source_key,
                        run_id=run["id"],
                        attempt_id=attempt["id"],
                        source_kind="run_observation",
                        root_cause_kind="verification_failure",
                        safe_summary={"summary_status": "empty"},
                        task_context=candidate,
                        allowed_run_statuses=frozenset({"verifying", "failed_verification"}),
                    )
                with self.repository.open_learning_connection() as connection:
                    self.assertIsNone(connection.execute(
                        "select 1 from repair_retrospectives where source_key=?", (source_key,),
                    ).fetchone())

    def test_human_correction_invalidates_only_current_awaiting_confirmation(self) -> None:
        run, attempt = self._create_run_and_attempt()
        self._bind_worker(attempt)
        self.repository.complete_attempt(attempt["id"], "completed")
        self.repository.transition(run["id"], "verifying", "reviewing", {})
        self.repository.transition(run["id"], "reviewing", "awaiting_human_confirmation", {})
        with database.connect_database(self.database_path) as connection:
            connection.execute(
                """insert into local_agent_apply_confirmations(
                       run_id, attempt_id, token_hash, requested_by, binding_json,
                       issued_at, expires_at, status
                   ) values(?, ?, ?, 'fixture-user', '{}', ?, ?, 'issued')""",
                (run["id"], attempt["id"], "sha256:" + "a" * 64,
                 database.now_iso(), database.now_iso()),
            )

        invalidated = self.repository.invalidate_confirmation_for_correction(
            run["id"], attempt["id"], correction_kind="contract_mismatch",
        )
        snapshot = self.repository.snapshot(run["id"])
        with database.connect_database(self.database_path) as connection:
            confirmation = connection.execute(
                "select status, consumed_at from local_agent_apply_confirmations where run_id=?",
                (run["id"],),
            ).fetchone()

        self.assertEqual("changes_requested", invalidated["status"])
        self.assertEqual("changes_requested", snapshot["run"]["status"])
        self.assertEqual("expired", confirmation["status"])
        self.assertIsNotNone(confirmation["consumed_at"])
        self.assertEqual("confirmation_invalidated_for_correction", snapshot["events"][-1]["event_type"])

    def test_awaiting_human_correction_persists_evidence_and_revokes_confirmation_atomically(self) -> None:
        run, attempt = self._create_run_and_attempt()
        self._bind_worker(attempt)
        self.repository.complete_attempt(attempt["id"], "completed")
        self.repository.transition(run["id"], "verifying", "reviewing", {})
        self.repository.transition(run["id"], "reviewing", "awaiting_human_confirmation", {})
        source_key = f"retro-r{run['id']}-a{attempt['id']}-s3-c5"

        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.record_awaiting_human_correction(
                self.task,
                source_key=source_key,
                run_id=run["id"],
                attempt_id=attempt["id"],
                root_cause_kind="implementation_defect",
                safe_summary={"summary_status": "safe", "summary": "human found defect"},
            )
        with self.repository.open_learning_connection() as connection:
            self.assertEqual(0, connection.execute(
                "select count(*) from repair_retrospectives where run_id=?", (run["id"],),
            ).fetchone()[0])
            self.assertEqual(0, connection.execute(
                "select count(*) from repair_learning_rules where active_run_id=?", (run["id"],),
            ).fetchone()[0])

        with database.connect_database(self.database_path) as connection:
            connection.execute(
                """insert into local_agent_apply_confirmations(
                       run_id, attempt_id, token_hash, requested_by, binding_json,
                       issued_at, expires_at, status
                   ) values(?, ?, ?, 'fixture-user', '{}', ?, ?, 'issued')""",
                (run["id"], attempt["id"], "sha256:" + "f" * 64,
                 database.now_iso(), database.now_iso()),
            )

        stored = self.repository.record_awaiting_human_correction(
            self.task,
            source_key=source_key,
            run_id=run["id"],
            attempt_id=attempt["id"],
            root_cause_kind="implementation_defect",
            safe_summary={"summary_status": "safe", "summary": "human found defect"},
        )
        snapshot = self.repository.snapshot(run["id"])
        self.assertEqual(source_key, stored["retrospective"]["source_key"])
        self.assertEqual("implementation_defect", stored["retrospective"]["root_cause_kind"])
        self.assertEqual("active_current_task", stored["rule"]["state"])
        self.assertEqual("changes_requested", snapshot["run"]["status"])
        self.assertEqual("confirmation_invalidated_for_correction", snapshot["events"][-1]["event_type"])
        with self.repository.open_learning_connection() as connection:
            self.assertEqual(
                "expired",
                connection.execute(
                    "select status from local_agent_apply_confirmations where run_id=?", (run["id"],),
                ).fetchone()[0],
            )
            self.assertEqual(1, connection.execute(
                "select count(*) from repair_retrospectives where run_id=?", (run["id"],),
            ).fetchone()[0])
            self.assertEqual(1, connection.execute(
                "select count(*) from repair_learning_rules where active_run_id=?", (run["id"],),
            ).fetchone()[0])

    def test_human_correction_implementation_defect_is_allowed(self) -> None:
        run, attempt = self._create_run_and_attempt()
        self._bind_worker(attempt)
        self.repository.complete_attempt(attempt["id"], "completed")
        self.repository.transition(run["id"], "verifying", "reviewing", {})
        self.repository.transition(run["id"], "reviewing", "awaiting_human_confirmation", {})
        with database.connect_database(self.database_path) as connection:
            connection.execute(
                """insert into local_agent_apply_confirmations(
                       run_id, attempt_id, token_hash, requested_by, binding_json,
                       issued_at, expires_at, status
                   ) values(?, ?, ?, 'fixture-user', '{}', ?, ?, 'issued')""",
                (run["id"], attempt["id"], "sha256:" + "b" * 64,
                 database.now_iso(), database.now_iso()),
            )

        changed = self.repository.invalidate_confirmation_for_correction(
            run["id"], attempt["id"], correction_kind="implementation_defect",
        )
        self.assertEqual("changes_requested", changed["status"])
        with database.connect_database(self.database_path) as connection:
            self.assertEqual(
                "expired",
                connection.execute(
                    "select status from local_agent_apply_confirmations where run_id=?", (run["id"],),
                ).fetchone()[0],
            )

    def test_human_correction_requires_an_issued_confirmation_without_partial_state_change(self) -> None:
        run, attempt = self._create_run_and_attempt()
        self._bind_worker(attempt)
        self.repository.complete_attempt(attempt["id"], "completed")
        self.repository.transition(run["id"], "verifying", "reviewing", {})
        self.repository.transition(run["id"], "reviewing", "awaiting_human_confirmation", {})
        before = self.repository.snapshot(run["id"])

        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.invalidate_confirmation_for_correction(
                run["id"], attempt["id"], correction_kind="contract_mismatch",
            )

        after = self.repository.snapshot(run["id"])
        self.assertEqual("awaiting_human_confirmation", after["run"]["status"])
        self.assertEqual(before["events"], after["events"])
        with database.connect_database(self.database_path) as connection:
            self.assertIsNone(connection.execute(
                "select 1 from local_agent_apply_confirmations where run_id=?", (run["id"],),
            ).fetchone())

    def test_human_correction_rejects_nonissued_confirmation_without_partial_state_change(self) -> None:
        run, attempt = self._create_run_and_attempt()
        self._bind_worker(attempt)
        self.repository.complete_attempt(attempt["id"], "completed")
        self.repository.transition(run["id"], "verifying", "reviewing", {})
        self.repository.transition(run["id"], "reviewing", "awaiting_human_confirmation", {})
        with database.connect_database(self.database_path) as connection:
            connection.execute(
                """insert into local_agent_apply_confirmations(
                       run_id, attempt_id, token_hash, requested_by, binding_json,
                       issued_at, expires_at, status, consumed_at
                   ) values(?, ?, ?, 'fixture-user', '{}', ?, ?, 'expired', ?)""",
                (run["id"], attempt["id"], "sha256:" + "c" * 64,
                 database.now_iso(), database.now_iso(), database.now_iso()),
            )
        before = self.repository.snapshot(run["id"])
        with database.connect_database(self.database_path) as connection:
            confirmation_before = dict(connection.execute(
                "select status, consumed_at from local_agent_apply_confirmations where run_id=?", (run["id"],),
            ).fetchone())

        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.invalidate_confirmation_for_correction(
                run["id"], attempt["id"], correction_kind="contract_mismatch",
            )

        after = self.repository.snapshot(run["id"])
        self.assertEqual(before["run"], after["run"])
        self.assertEqual(before["events"], after["events"])
        with database.connect_database(self.database_path) as connection:
            confirmation_after = dict(connection.execute(
                "select status, consumed_at from local_agent_apply_confirmations where run_id=?", (run["id"],),
            ).fetchone())
        self.assertEqual(confirmation_before, confirmation_after)

    def test_human_correction_on_third_attempt_stays_changes_requested(self) -> None:
        run, first_attempt = self._create_run_and_attempt()
        self._bind_worker(first_attempt)
        self.repository.complete_attempt(first_attempt["id"], "completed")
        self.repository.transition(run["id"], "verifying", "reviewing", {})
        self.repository.transition(run["id"], "reviewing", "changes_requested", {})

        second_attempt = self.repository.start_attempt(run["id"])
        self._bind_worker(second_attempt)
        self.repository.complete_attempt(second_attempt["id"], "completed")
        self.repository.transition(run["id"], "verifying", "reviewing", {})
        self.repository.transition(run["id"], "reviewing", "changes_requested", {})

        third_attempt = self.repository.start_attempt(run["id"])
        self._bind_worker(third_attempt)
        self.repository.complete_attempt(third_attempt["id"], "completed")
        self.repository.transition(run["id"], "verifying", "reviewing", {})
        self.repository.transition(run["id"], "reviewing", "awaiting_human_confirmation", {})
        with database.connect_database(self.database_path) as connection:
            connection.execute(
                """insert into local_agent_apply_confirmations(
                       run_id, attempt_id, token_hash, requested_by, binding_json,
                       issued_at, expires_at, status
                   ) values(?, ?, ?, 'fixture-user', '{}', ?, ?, 'issued')""",
                (run["id"], third_attempt["id"], "sha256:" + "d" * 64,
                 database.now_iso(), database.now_iso()),
            )

        changed = self.repository.invalidate_confirmation_for_correction(
            run["id"], third_attempt["id"], correction_kind="review_gap",
        )
        snapshot = self.repository.snapshot(run["id"])

        self.assertEqual("changes_requested", changed["status"])
        self.assertEqual("changes_requested", snapshot["run"]["status"])
        self.assertNotIn("attempt_budget_exhausted", tuple(event["event_type"] for event in snapshot["events"]))

    def test_human_correction_cannot_invalidate_an_apply_operation(self) -> None:
        run, attempt = self._create_run_and_attempt()
        self._bind_worker(attempt)
        self.repository.complete_attempt(attempt["id"], "completed")
        self.repository.transition(run["id"], "verifying", "reviewing", {})
        self.repository.transition(run["id"], "reviewing", "awaiting_human_confirmation", {})
        with database.connect_database(self.database_path) as connection:
            connection.execute(
                """insert into local_agent_apply_confirmations(
                       run_id, attempt_id, token_hash, requested_by, binding_json,
                       issued_at, expires_at, status
                   ) values(?, ?, ?, 'fixture-user', '{}', ?, ?, 'issued')""",
                (run["id"], attempt["id"], "sha256:" + "b" * 64,
                 database.now_iso(), database.now_iso()),
            )
            connection.execute(
                """insert into local_agent_apply_operations(
                       run_id, attempt_id, operation_id, token_hash, facts_json,
                       status, created_at, updated_at
                   ) values(?, ?, 'fixture-operation', ?, '{}', 'applying', ?, ?)""",
                (run["id"], attempt["id"], "sha256:" + "b" * 64,
                 database.now_iso(), database.now_iso()),
            )

        with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
            self.repository.invalidate_confirmation_for_correction(
                run["id"], attempt["id"], correction_kind="contract_mismatch",
            )
        self.assertEqual("awaiting_human_confirmation", self.repository.snapshot(run["id"])["run"]["status"])

    def test_human_correction_invalidation_fails_closed_for_wrong_state_or_attempt(self) -> None:
        run, attempt = self._create_run_and_attempt()
        with self.assertRaisesRegex(ValueError, "local_agent_state_transition_invalid"):
            self.repository.invalidate_confirmation_for_correction(
                run["id"], attempt["id"], correction_kind="contract_mismatch",
            )
        self._bind_worker(attempt)
        self.repository.complete_attempt(attempt["id"], "completed")
        self.repository.transition(run["id"], "verifying", "reviewing", {})
        self.repository.transition(run["id"], "reviewing", "awaiting_human_confirmation", {})
        with self.assertRaisesRegex(ValueError, "local_agent_state_transition_invalid"):
            self.repository.invalidate_confirmation_for_correction(
                run["id"], attempt["id"] + 1, correction_kind="contract_mismatch",
            )

    def test_orphan_recovery_fails_closed_on_identity_reader_unknown_result(self) -> None:
        run, attempt = self._create_run_and_attempt()
        with patch("app.local_agent_repository._read_process_start_identity", return_value="darwin-proc-bsdinfo-v1:1:2"):
            self.repository.bind_worker_identity(attempt["id"], 12345, "darwin-proc-bsdinfo-v1:1:2")
        with patch("app.local_agent_repository.os.kill"), patch("app.local_agent_repository._read_process_start_identity", side_effect=RuntimeError):
            with self.assertRaisesRegex(ValueError, "local_agent_storage_invalid"):
                self.repository.mark_orphaned_attempts_interrupted()
        self.assertEqual("worker_running", self.repository.snapshot(run["id"])["run"]["status"])


if __name__ == "__main__":
    unittest.main()
