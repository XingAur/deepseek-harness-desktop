from __future__ import annotations

import unittest

from app.code_evidence_set import canonical_evidence_set_manifest


class CodeEvidenceSetContractTests(unittest.TestCase):
    def test_manifest_is_deterministic_and_requires_approved_unique_repositories(self) -> None:
        members = [
            {"repository_alias": "repo-b", "review_bundle_id": 8, "review_bundle_sha256": "b" * 64,
             "repository_snapshot_sha256": "2" * 64, "verdict": "approved"},
            {"repository_alias": "repo-a", "review_bundle_id": 3, "review_bundle_sha256": "a" * 64,
             "repository_snapshot_sha256": "1" * 64, "verdict": "approved"},
        ]

        first = canonical_evidence_set_manifest(members)
        second = canonical_evidence_set_manifest(tuple(reversed(members)))

        self.assertEqual(first, second)
        self.assertLess(first.find(b'repo-a'), first.find(b'repo-b'))

    def test_incomplete_duplicate_or_unapproved_member_fails_closed(self) -> None:
        valid = {"repository_alias": "repo-a", "review_bundle_id": 3,
                 "review_bundle_sha256": "a" * 64,
                 "repository_snapshot_sha256": "1" * 64, "verdict": "approved"}
        cases = (
            [valid, dict(valid)],
            [{**valid, "verdict": "changes_requested"}],
            [{**valid, "review_bundle_sha256": "bad"}],
        )
        for members in cases:
            with self.subTest(members=members):
                with self.assertRaisesRegex(ValueError, "code_evidence_set_invalid"):
                    canonical_evidence_set_manifest(members)


if __name__ == "__main__":
    unittest.main()
