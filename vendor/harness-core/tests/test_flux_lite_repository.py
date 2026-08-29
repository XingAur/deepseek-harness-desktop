from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import database
from app.flux_lite_learning import ReviewerOpinion, aggregate_opinions
from app.flux_lite_repository import FluxLiteLearningRepository


def _opinion(reviewer_id: str, *, root_cause: str = "verification_failure") -> ReviewerOpinion:
    return ReviewerOpinion(
        reviewer_id=reviewer_id,
        scope_key="python:calculator",
        root_cause=root_cause,
        focus_actions=("verification_replay", "reviewer_focus"),
        verdict="changes_requested",
        evidence_refs=("sha256:" + hashlib.sha256(reviewer_id.encode()).hexdigest(),),
    )


class FluxLiteLearningRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "flux-lite.sqlite"

        def connection_factory() -> sqlite3.Connection:
            return database.connect_database(self.path)

        self.connection_factory = connection_factory
        self.repository = FluxLiteLearningRepository(connection_factory=connection_factory)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_opinions_are_idempotent_and_replay_checked(self) -> None:
        opinion = _opinion("reviewer-a")

        first = self.repository.record_opinion(
            run_id=11,
            attempt_id=21,
            opinion=opinion,
        )
        second = self.repository.record_opinion(
            run_id=11,
            attempt_id=21,
            opinion=opinion,
        )

        self.assertEqual(first, second)
        self.assertEqual("reviewer-a", first["reviewer_id"])
        with self.connection_factory() as connection:
            self.assertEqual(
                1,
                connection.execute("select count(*) from flux_lite_reviewer_opinions").fetchone()[0],
            )

    def test_consensus_is_stored_with_immutable_candidate_metadata(self) -> None:
        opinions = (_opinion("reviewer-a"), _opinion("reviewer-b"))
        candidate = aggregate_opinions(opinions)

        stored = self.repository.record_consensus(
            run_id=11,
            attempt_id=21,
            opinions=opinions,
            candidate=candidate,
        )

        self.assertEqual(candidate.candidate_id, stored["candidate_id"])
        self.assertEqual("trial", stored["state"])
        snapshot = self.repository.snapshot_for_attempt(run_id=11, attempt_id=21)
        self.assertEqual(2, len(snapshot["opinions"]))
        self.assertEqual([candidate.candidate_id], [item["candidate_id"] for item in snapshot["candidates"]])

    def test_context_candidates_require_consensus_and_scope_match(self) -> None:
        opinions = (_opinion("reviewer-a"), _opinion("reviewer-b"))
        candidate = aggregate_opinions(opinions)
        self.repository.record_consensus(
            run_id=11,
            attempt_id=21,
            opinions=opinions,
            candidate=candidate,
        )

        self.assertEqual(
            [candidate.candidate_id],
            [item["candidate_id"] for item in self.repository.list_context_candidates(
                scope_key="python:calculator",
            )],
        )
        self.assertEqual([], self.repository.list_context_candidates(scope_key="python:other"))

    def test_same_experience_pattern_can_be_recorded_in_a_second_attempt(self) -> None:
        opinions = (_opinion("reviewer-a"), _opinion("reviewer-b"))
        candidate = aggregate_opinions(opinions)

        first = self.repository.record_consensus(
            run_id=11,
            attempt_id=21,
            opinions=opinions,
            candidate=candidate,
        )
        second = self.repository.record_consensus(
            run_id=12,
            attempt_id=22,
            opinions=opinions,
            candidate=candidate,
        )

        self.assertEqual(candidate.candidate_id, first["candidate_id"])
        self.assertEqual(candidate.candidate_id, second["candidate_id"])
        with self.connection_factory() as connection:
            self.assertEqual(
                2,
                connection.execute("select count(*) from flux_lite_experience_candidates").fetchone()[0],
            )

    def test_high_risk_candidate_is_persisted_but_never_promoted_to_context(self) -> None:
        opinions = (_opinion("reviewer-a"), _opinion("reviewer-b"))
        candidate = aggregate_opinions(opinions, high_risk=True)
        self.repository.record_consensus(
            run_id=11,
            attempt_id=21,
            opinions=opinions,
            candidate=candidate,
        )

        self.assertEqual([], self.repository.list_context_candidates(scope_key="python:calculator"))
        self.assertEqual(
            [candidate.candidate_id],
            [item["candidate_id"] for item in self.repository.list_context_candidates(
                scope_key="python:calculator",
                include_high_risk=True,
            )],
        )


if __name__ == "__main__":
    unittest.main()
