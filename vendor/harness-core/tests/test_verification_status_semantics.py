from __future__ import annotations

import unittest

from app.precommit_verifier import build_verification_matrix, verification_gate_can_modify


class VerificationStatusSemanticsTests(unittest.TestCase):
    def test_baseline_failure_never_opens_commit_gate(self) -> None:
        matrix = build_verification_matrix(
            status="success",
            summary="baseline",
            targets=[{"name": "repo", "status": "success", "verification_status": "baseline_failed"}],
        )
        self.assertEqual("baseline_failed", matrix["verification_status"])
        self.assertFalse(matrix["can_commit"])
        self.assertFalse(verification_gate_can_modify("baseline_failed"))

    def test_only_real_pass_opens_gate(self) -> None:
        self.assertTrue(verification_gate_can_modify("passed"))
        for status in ("not_run", "tool_missing", "failed", "side_effect_failed"):
            self.assertFalse(verification_gate_can_modify(status))


if __name__ == "__main__":
    unittest.main()
