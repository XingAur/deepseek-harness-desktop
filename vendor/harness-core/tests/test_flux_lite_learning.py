from __future__ import annotations

import hashlib
import unittest

from app.flux_lite_learning import (
    ReviewerOpinion,
    aggregate_opinions,
    select_learning_checks,
)


def _opinion(
    reviewer_id: str,
    *,
    scope_key: str = "python:calculator",
    root_cause: str = "verification_failure",
    focus_actions: tuple[str, ...] = ("verification_replay", "reviewer_focus"),
    verdict: str = "changes_requested",
) -> ReviewerOpinion:
    return ReviewerOpinion(
        reviewer_id=reviewer_id,
        scope_key=scope_key,
        root_cause=root_cause,
        focus_actions=focus_actions,
        verdict=verdict,
        evidence_refs=("sha256:" + hashlib.sha256(reviewer_id.encode()).hexdigest(),),
    )


class FluxLiteLearningTests(unittest.TestCase):
    def test_unanimous_opinions_keep_full_context_weight(self) -> None:
        candidate = aggregate_opinions([_opinion("r1"), _opinion("r2"), _opinion("r3")])

        self.assertEqual("python:calculator", candidate.scope_key)
        self.assertEqual(3, candidate.reviewer_count)
        self.assertEqual(1.0, candidate.agreement_ratio)
        self.assertEqual(0.0, candidate.conflict_score)
        self.assertEqual(1.0, candidate.context_weight)
        self.assertTrue(candidate.promotion_allowed)
        self.assertEqual("trial", candidate.state)

    def test_disagreement_reduces_context_weight_but_keeps_dominant_action(self) -> None:
        candidate = aggregate_opinions(
            [_opinion("r1"), _opinion("r2"), _opinion("r3", root_cause="review_gap")],
        )

        self.assertAlmostEqual(2 / 3, candidate.agreement_ratio)
        self.assertAlmostEqual(1 / 3, candidate.conflict_score)
        self.assertLess(candidate.context_weight, 1.0)
        self.assertGreaterEqual(candidate.context_weight, 0.2)
        self.assertFalse(candidate.promotion_allowed)
        self.assertEqual("candidate", candidate.state)
        self.assertEqual("verification_failure", candidate.root_cause)

    def test_high_risk_candidate_is_always_bounded_and_not_promotable(self) -> None:
        candidate = aggregate_opinions(
            [_opinion("r1", scope_key="his:settlement"), _opinion("r2", scope_key="his:settlement")],
            high_risk=True,
        )

        self.assertTrue(candidate.high_risk)
        self.assertLessEqual(candidate.context_weight, 0.25)
        self.assertFalse(candidate.promotion_allowed)

    def test_high_risk_weight_bound_cannot_be_raised_above_safety_cap(self) -> None:
        with self.assertRaisesRegex(ValueError, "flux_lite_weight_bounds_invalid"):
            aggregate_opinions(
                [_opinion("r1"), _opinion("r2")],
                high_risk=True,
                minimum_weight=0.3,
            )

    def test_insufficient_independent_reviewers_stays_as_candidate(self) -> None:
        candidate = aggregate_opinions([_opinion("r1")])

        self.assertEqual(1, candidate.reviewer_count)
        self.assertFalse(candidate.promotion_allowed)
        self.assertEqual("candidate", candidate.state)

    def test_only_consensus_actions_are_selected_for_prompt_injection(self) -> None:
        candidate = aggregate_opinions([_opinion("r1"), _opinion("r2")])

        self.assertEqual(
            ("verification_replay", "reviewer_focus"),
            select_learning_checks(candidate, ("verification_replay", "reviewer_focus", "path_coverage")),
        )

    def test_conflicting_scope_is_rejected_instead_of_merged(self) -> None:
        with self.assertRaisesRegex(ValueError, "flux_lite_scope_conflict"):
            aggregate_opinions([_opinion("r1"), _opinion("r2", scope_key="node:api")])


if __name__ == "__main__":
    unittest.main()
