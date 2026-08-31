from __future__ import annotations

import unittest

from app.provider_authority_policy import provider_authority_policy


class ProviderAuthorityPolicyTests(unittest.TestCase):
    def test_remote_reads_use_personal_token_without_harness_approval(self) -> None:
        for provider, action in (
            ("yunxiao", "workitem.read"),
            ("gitlab", "gitlab.repository.file.read"),
            ("github", "github.repository.file.read"),
        ):
            with self.subTest(provider=provider):
                policy = provider_authority_policy(
                    provider=provider,
                    action=action,
                    risk="read",
                )

                self.assertEqual("personal_token", policy.technical_authority_source)
                self.assertFalse(policy.harness_authorization_required)
                self.assertFalse(policy.exact_scope_authorization_required)
                self.assertFalse(policy.destructive_scope_authorization_required)

    def test_database_read_uses_readonly_endpoint_without_harness_approval(self) -> None:
        policy = provider_authority_policy(
            provider="database",
            action="database.query.read",
            risk="read",
        )

        self.assertEqual(
            "readonly_endpoint_or_credential",
            policy.technical_authority_source,
        )
        self.assertFalse(policy.harness_authorization_required)
        self.assertFalse(policy.exact_scope_authorization_required)
        self.assertFalse(policy.destructive_scope_authorization_required)

    def test_local_read_uses_local_permissions_without_harness_approval(self) -> None:
        policy = provider_authority_policy(
            provider="git",
            action="repo.diff.read",
            risk="read",
        )

        self.assertEqual("local_permissions", policy.technical_authority_source)
        self.assertFalse(policy.harness_authorization_required)

    def test_database_change_requires_explicit_exact_scope(self) -> None:
        policy = provider_authority_policy(
            provider="database",
            action="database.row.update",
            risk="remote_write",
        )

        self.assertEqual("explicit_user_authorization", policy.technical_authority_source)
        self.assertTrue(policy.harness_authorization_required)
        self.assertTrue(policy.exact_scope_authorization_required)
        self.assertFalse(policy.destructive_scope_authorization_required)

    def test_database_delete_requires_explicit_destructive_scope(self) -> None:
        for action in (
            "database.row.delete",
            "database.table.truncate",
            "database.table.drop",
        ):
            with self.subTest(action=action):
                policy = provider_authority_policy(
                    provider="database",
                    action=action,
                    risk="remote_write",
                )

                self.assertTrue(policy.harness_authorization_required)
                self.assertTrue(policy.exact_scope_authorization_required)
                self.assertTrue(policy.destructive_scope_authorization_required)

    def test_database_mutation_mislabeled_as_read_fails_closed(self) -> None:
        for action in (
            "database.row.update",
            "database.row.delete",
            "database.table.truncate",
            "database.table.drop",
        ):
            with self.subTest(action=action):
                with self.assertRaisesRegex(
                    ValueError,
                    "provider_authority_policy",
                ):
                    provider_authority_policy(
                        provider="database",
                        action=action,
                        risk="read",
                    )

    def test_non_database_write_still_requires_explicit_authorization(self) -> None:
        policy = provider_authority_policy(
            provider="gitlab",
            action="merge_request.comment.write",
            risk="remote_write",
        )

        self.assertEqual("explicit_user_authorization", policy.technical_authority_source)
        self.assertTrue(policy.harness_authorization_required)
        self.assertTrue(policy.exact_scope_authorization_required)
        self.assertFalse(policy.destructive_scope_authorization_required)

    def test_unknown_or_mismatched_policy_input_fails_closed(self) -> None:
        invalid_cases = (
            {"provider": "unknown", "action": "workitem.read", "risk": "read"},
            {"provider": "database", "action": "workitem.read", "risk": "read"},
            {"provider": "database", "action": "database.query.read", "risk": "unknown"},
        )

        for case in invalid_cases:
            with self.subTest(case=case):
                with self.assertRaisesRegex(ValueError, "provider_authority_policy"):
                    provider_authority_policy(**case)


if __name__ == "__main__":
    unittest.main()
