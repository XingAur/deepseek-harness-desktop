from __future__ import annotations

import unittest

from app.code_evidence_contracts import evaluate_evidence_completeness


class CodeEvidenceCompletenessTests(unittest.TestCase):
    def _facts(self, **overrides: object) -> dict[str, object]:
        value: dict[str, object] = {
            "required_capabilities": ["git.diff", "source.read", "verification.run-local", "code.review-local"],
            "successful_capabilities": ["git.diff", "source.read", "verification.run-local", "code.review-local"],
            "required_artifact_kinds": ["diff_patch", "source_manifest", "verification_receipt", "review"],
            "artifact_kinds": ["diff_patch", "source_manifest", "verification_receipt", "review"],
            "bundle_sealed": True,
            "snapshot_consistent": True,
            "search_complete": True,
            "diff_complete": True,
            "sensitive_blocked": False,
            "limit_exceeded": False,
            "verification_bundle_sha256": "a" * 64,
            "review_bundle_sha256": "a" * 64,
            "bundle_sha256": "a" * 64,
            "verification_status": "passed",
            "review_verdict": "approved",
        }
        value.update(overrides)
        return value

    def test_complete_requires_every_capability_artifact_binding_and_approval(self) -> None:
        result = evaluate_evidence_completeness(self._facts())
        self.assertEqual("complete", result.status)
        self.assertEqual((), result.blockers)

    def test_each_incomplete_condition_has_stable_blocker_and_never_approves(self) -> None:
        cases = {
            "capability": ({"successful_capabilities": ["git.diff"]}, "code_evidence_capability_incomplete"),
            "artifact": ({"artifact_kinds": ["diff_patch"]}, "code_evidence_artifact_incomplete"),
            "unsealed": ({"bundle_sealed": False}, "code_evidence_bundle_unsealed"),
            "snapshot": ({"snapshot_consistent": False}, "code_evidence_changed"),
            "search": ({"search_complete": False}, "code_evidence_search_incomplete"),
            "diff": ({"diff_complete": False}, "code_evidence_diff_incomplete"),
            "sensitive": ({"sensitive_blocked": True}, "code_evidence_sensitive"),
            "limit": ({"limit_exceeded": True}, "code_evidence_limit_exceeded"),
            "verification": ({"verification_status": "failed"}, "code_evidence_verification_failed"),
            "review": ({"review_verdict": "changes_requested"}, "code_evidence_review_not_approved"),
            "verify_binding": ({"verification_bundle_sha256": "b" * 64}, "code_evidence_binding_invalid"),
            "review_binding": ({"review_bundle_sha256": "b" * 64}, "code_evidence_binding_invalid"),
        }
        for name, (overrides, blocker) in cases.items():
            with self.subTest(name=name):
                result = evaluate_evidence_completeness(self._facts(**overrides))
                self.assertEqual("blocked", result.status)
                self.assertIn(blocker, result.blockers)

    def test_unknown_extra_or_malformed_fact_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "code_evidence_completeness_invalid"):
            evaluate_evidence_completeness({**self._facts(), "unexpected": True})
        with self.assertRaisesRegex(ValueError, "code_evidence_completeness_invalid"):
            evaluate_evidence_completeness(self._facts(bundle_sha256="not-a-sha"))


if __name__ == "__main__":
    unittest.main()
