from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from app import database
from app.repair_learning import (
    LearningRuleState,
    PromotionEvidence,
    RuleObservationOutcome,
    build_current_task_rule,
    TaskLearningContext,
)
from app.repair_learning_repository import RepairLearningRepository


class RepairLearningRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "repair-learning.sqlite"

        def connection_factory() -> sqlite3.Connection:
            return database.connect_database(self.path)

        self.connection_factory = connection_factory
        database.init_db(connection_factory=self.connection_factory)
        self.repository = RepairLearningRepository(
            connection_factory=self.connection_factory,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _context(*, run_id: int = 11, task_key: str = "task-a") -> TaskLearningContext:
        return TaskLearningContext(
            run_id=run_id,
            task_key=task_key,
            repository_kind="python",
            allowed_path_prefixes=("app/example.py",),
            verification_command_fingerprints=("a" * 64,),
            high_risk_tags=(),
            failure_sources=("test_failure",),
        )

    def _retrospective(self, *, source_key: str = "source-a") -> dict[str, object]:
        return self.repository.record_retrospective(
            source_key=source_key,
            run_id=11,
            attempt_id=21,
            source_kind="run_observation",
            root_cause_kind="verification_failure",
            safe_summary={"status": "failed", "token": "must-not-survive"},
            task_context={"task_key": "task-a", "repository_kind": "python"},
        )

    def _rule(self, *, retrospective_id: int, run_id: int = 11) -> dict[str, object]:
        rule = build_current_task_rule(self._context(run_id=run_id))
        return self.repository.upsert_rule(
            rule=rule,
            origin_retrospective_id=retrospective_id,
            active_run_id=run_id,
        )

    def test_constructor_requires_an_explicit_connection_factory(self) -> None:
        with self.assertRaisesRegex(TypeError, "connection_factory must be callable"):
            RepairLearningRepository(connection_factory=None)  # type: ignore[arg-type]

    def test_plain_sqlite_connection_factory_handles_are_closed_after_every_use(self) -> None:
        handles: list[sqlite3.Connection] = []

        def plain_factory() -> sqlite3.Connection:
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            connection.execute("pragma foreign_keys = on")
            handles.append(connection)
            return connection

        repository = RepairLearningRepository(connection_factory=plain_factory)
        repository.snapshot_for_run(run_id=99)

        self.assertGreaterEqual(len(handles), 2)
        for handle in handles:
            with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
                handle.execute("select 1")

    def test_stable_rule_without_durable_observations_cannot_be_upserted_or_matched(self) -> None:
        retrospective_id = int(self._retrospective()["id"])
        stable_rule = build_current_task_rule(
            self._context(),
            state=LearningRuleState.STABLE,
            promotion_evidence=PromotionEvidence(
                task_keys=("task-a", "task-b", "task-c"),
                workspace_fingerprints=("workspace-a", "workspace-b"),
                counterexample_count=0,
            ),
        )

        with self.assertRaisesRegex(ValueError, "repair_learning_initial_state_invalid"):
            self.repository.upsert_rule(
                rule=stable_rule,
                origin_retrospective_id=retrospective_id,
                active_run_id=None,
            )

        active = self._rule(retrospective_id=retrospective_id)
        with self.connection_factory() as connection:
            connection.execute(
                "update repair_learning_rules set state = 'stable' where id = ?",
                (int(active["id"]),),
            )
        self.assertEqual([], self.repository.list_matchable_rules(run_id=11))

    def test_retrospective_and_rule_replay_are_idempotent_and_reads_are_redacted(self) -> None:
        first_retrospective = self._retrospective()
        replayed_retrospective = self._retrospective()
        first_rule = self._rule(retrospective_id=int(first_retrospective["id"]))
        replayed_rule = self._rule(retrospective_id=int(first_retrospective["id"]))

        self.assertEqual(first_retrospective["id"], replayed_retrospective["id"])
        self.assertEqual(first_rule["id"], replayed_rule["id"])
        self.assertNotIn("token", first_retrospective["safe_summary"])
        self.assertIn(
            "[REDACTED_SENSITIVE_FIELD]",
            first_retrospective["safe_summary"].values(),
        )
        with self.connection_factory() as connection:
            self.assertEqual(1, connection.execute("select count(*) from repair_retrospectives").fetchone()[0])
            self.assertEqual(1, connection.execute("select count(*) from repair_learning_rules").fetchone()[0])
            persisted = connection.execute("select safe_summary_json from repair_retrospectives").fetchone()[0]
        self.assertNotIn("must-not-survive", persisted)

    def test_observation_replay_and_same_task_do_not_inflate_rule_evidence_counts(self) -> None:
        rule = self._rule(retrospective_id=int(self._retrospective()["id"]))
        arguments = {
            "rule_id": int(rule["id"]),
            "run_id": 11,
            "attempt_id": 21,
            "task_key": "task-a",
            "workspace_fingerprint": "workspace-a",
            "outcome": RuleObservationOutcome.MATCHED,
            "evidence": {"status": "verified"},
        }
        first = self.repository.record_observation(**arguments)
        replay = self.repository.record_observation(**arguments)
        self.repository.record_observation(
            **{
                **arguments,
                "run_id": 12,
                "attempt_id": 22,
                "workspace_fingerprint": "workspace-b",
            }
        )

        self.assertEqual(first["id"], replay["id"])
        current = self.repository.snapshot_for_run(run_id=11)["rules"][0]
        self.assertEqual(1, current["verified_task_count"])
        self.assertEqual(2, current["distinct_workspace_count"])
        with self.connection_factory() as connection:
            self.assertEqual(2, connection.execute("select count(*) from repair_learning_observations").fetchone()[0])

    def test_state_version_compare_and_swap_has_exactly_one_winner(self) -> None:
        rule = self._rule(retrospective_id=int(self._retrospective()["id"]))
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def worker(state: LearningRuleState) -> None:
            barrier.wait()
            try:
                self.repository.advance_rule_state(
                    rule_id=int(rule["id"]),
                    expected_state_version=0,
                    new_state=state,
                )
                outcomes.append("success")
            except ValueError as exc:
                outcomes.append(str(exc))

        threads = [
            threading.Thread(target=worker, args=(state,))
            for state in (LearningRuleState.TRIAL, LearningRuleState.RETIRED)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(
            ["repair_learning_state_conflict", "success"],
            sorted(outcomes),
        )
        with self.assertRaisesRegex(ValueError, "repair_learning_state_conflict"):
            self.repository.advance_rule_state(
                rule_id=int(rule["id"]),
                expected_state_version=0,
                new_state=LearningRuleState.TRIAL,
            )

    def test_lifecycle_updates_keep_rule_json_state_and_immutable_rule_key_consistent(self) -> None:
        rule = self._rule(retrospective_id=int(self._retrospective()["id"]))
        identity_key = rule["rule_key"]

        trial = self.repository.advance_rule_state(
            rule_id=int(rule["id"]),
            expected_state_version=0,
            new_state=LearningRuleState.TRIAL,
        )
        self.assertEqual((identity_key, "trial", "trial"), (
            trial["rule_key"], trial["state"], trial["rule"]["state"],
        ))
        replayed_after_transition = self._rule(
            retrospective_id=int(rule["origin_retrospective_id"]),
        )
        self.assertEqual((identity_key, "trial"), (
            replayed_after_transition["rule_key"], replayed_after_transition["state"],
        ))

        retired = self.repository.advance_rule_state(
            rule_id=int(rule["id"]),
            expected_state_version=1,
            new_state=LearningRuleState.RETIRED,
        )
        self.assertEqual((identity_key, "retired", "retired"), (
            retired["rule_key"], retired["state"], retired["rule"]["state"],
        ))

        other = self._rule(
            retrospective_id=int(self._retrospective(source_key="source-b")["id"]),
            run_id=12,
        )
        suspended = self.repository.suspend_rule(
            rule_id=int(other["id"]),
            expected_state_version=0,
        )
        self.assertEqual(
            (suspended["state"], suspended["rule"]["state"]),
            ("suspended", "suspended"),
        )

    def test_counterexample_atomically_suspends_rule_and_excludes_it_from_matches(self) -> None:
        rule = self._rule(retrospective_id=int(self._retrospective()["id"]))
        before = self.repository.list_matchable_rules(run_id=11)

        observation = self.repository.record_observation(
            rule_id=int(rule["id"]),
            run_id=11,
            attempt_id=21,
            task_key="task-a",
            workspace_fingerprint="workspace-a",
            outcome=RuleObservationOutcome.NOT_MATCHED,
            evidence={"reason": "counterexample"},
        )
        replay = self.repository.record_observation(
            rule_id=int(rule["id"]),
            run_id=11,
            attempt_id=21,
            task_key="task-a",
            workspace_fingerprint="workspace-a",
            outcome=RuleObservationOutcome.NOT_MATCHED,
            evidence={"reason": "counterexample"},
        )
        after = self.repository.list_matchable_rules(run_id=11)
        snapshot = self.repository.snapshot_for_run(run_id=11)

        self.assertEqual([rule["id"]], [item["id"] for item in before])
        self.assertEqual(observation["id"], replay["id"])
        self.assertEqual([], after)
        self.assertEqual("suspended", snapshot["rules"][0]["state"])
        self.assertEqual("suspended", snapshot["rules"][0]["rule"]["state"])
        self.assertEqual(1, snapshot["rules"][0]["counterexample_count"])
        self.assertEqual(1, snapshot["rules"][0]["state_version"])
        self.assertIsNotNone(snapshot["rules"][0]["suspended_at"])

    def test_manual_suspend_uses_compare_and_swap(self) -> None:
        rule = self._rule(retrospective_id=int(self._retrospective()["id"]))
        suspended = self.repository.suspend_rule(
            rule_id=int(rule["id"]),
            expected_state_version=0,
        )
        self.assertEqual("suspended", suspended["state"])
        with self.assertRaisesRegex(ValueError, "repair_learning_state_conflict"):
            self.repository.suspend_rule(
                rule_id=int(rule["id"]),
                expected_state_version=0,
            )

    def test_stable_transition_requires_three_tasks_two_workspaces_and_no_counterexample(self) -> None:
        rule = self._rule(retrospective_id=int(self._retrospective()["id"]))
        trial = self.repository.advance_rule_state(
            rule_id=int(rule["id"]),
            expected_state_version=0,
            new_state=LearningRuleState.TRIAL,
        )
        with self.assertRaisesRegex(ValueError, "repair_learning_promotion_ineligible"):
            self.repository.advance_rule_state(
                rule_id=int(rule["id"]),
                expected_state_version=int(trial["state_version"]),
                new_state=LearningRuleState.STABLE,
            )

        for offset, (task_key, workspace) in enumerate(
            (("task-a", "workspace-a"), ("task-b", "workspace-a"), ("task-c", "workspace-b")),
            start=1,
        ):
            self.repository.record_observation(
                rule_id=int(rule["id"]),
                run_id=20 + offset,
                attempt_id=30 + offset,
                task_key=task_key,
                workspace_fingerprint=workspace,
                outcome=RuleObservationOutcome.MATCHED,
                evidence={"status": "verified"},
            )

        stable = self.repository.advance_rule_state(
            rule_id=int(rule["id"]),
            expected_state_version=int(trial["state_version"]),
            new_state=LearningRuleState.STABLE,
        )
        self.assertEqual("stable", stable["state"])
        self.assertEqual("stable", stable["rule"]["state"])
        self.assertEqual(3, stable["verified_task_count"])
        self.assertEqual(2, stable["distinct_workspace_count"])


if __name__ == "__main__":
    unittest.main()
