from __future__ import annotations

import unittest

from app.scope_confirmation import (
    build_scope_confirmation_binding,
    confirmation_token,
    scope_confirmation_to_markdown,
    validate_scope_confirmation,
)


class ScopeConfirmationTests(unittest.TestCase):
    @staticmethod
    def _inputs() -> dict:
        return {
            "execution_mode": "core-closure-trial",
            "technical_decision": {
                "selected_projects": [
                    {"name": "df-mic-demo", "path": "/tmp/mic", "role": "backend", "selection_scope": "candidate_change"},
                    {"name": "df-web-demo", "path": "/tmp/web", "role": "frontend", "selection_scope": "change_required"},
                    {"name": "df-his-api", "path": "/tmp/api", "role": "api", "selection_scope": "contract_check"},
                ],
                "recommended_allowed_paths": ["src/api/Demo.java", "src/views/demo.vue"],
                "multi_service_change_contract": {
                    "status": "ready",
                    "repositories": [
                        {"name": "df-web-demo", "path": "/tmp/web", "role": "frontend"},
                        {"name": "df-mic-demo", "path": "/tmp/mic", "role": "backend"},
                    ],
                },
            },
            "change_ownership": {
                "frontend": {"status": "required", "paths": ["src/views/demo.vue"]},
                "backend": {"status": "required", "paths": ["src/api/Demo.java"]},
            },
            "governance": {"status": "ready_for_local_change", "can_modify": True},
            "single_pass_contract": {
                "status": "ready",
                "change_context_pack_id": "ccp:sha256:" + "a" * 64,
                "change_context_projection_hash": "sha256:" + "b" * 64,
                "allowed_paths": ["src/views/demo.vue", "src/api/Demo.java"],
                "verify_commands": ["npm test", "./gradlew test"],
                "repositories": [
                    {"name": "df-web-demo", "path": "/tmp/web", "role": "frontend"},
                    {"name": "df-mic-demo", "path": "/tmp/mic", "role": "backend"},
                ],
            },
            "allowed_paths": ["src/api/Demo.java", "src/views/demo.vue"],
            "verify_commands": ["./gradlew test", "npm test"],
        }

    def test_binding_is_order_independent_and_has_exact_token(self) -> None:
        first = build_scope_confirmation_binding(**self._inputs())
        inputs = self._inputs()
        inputs["allowed_paths"] = list(reversed(inputs["allowed_paths"]))
        inputs["verify_commands"] = list(reversed(inputs["verify_commands"]))
        inputs["technical_decision"]["selected_projects"] = list(
            reversed(inputs["technical_decision"]["selected_projects"])
        )
        second = build_scope_confirmation_binding(**inputs)

        self.assertEqual(first["scope_hash"], second["scope_hash"])
        self.assertEqual(first["confirmation_token"], confirmation_token(first["scope_hash"]))
        self.assertTrue(validate_scope_confirmation(first["confirmation_token"], first["scope_hash"]))
        self.assertFalse(validate_scope_confirmation(first["confirmation_token"] + "x", first["scope_hash"]))

    def test_binding_contains_only_safe_scope_facts(self) -> None:
        binding = build_scope_confirmation_binding(**self._inputs())

        self.assertEqual("pending", binding["status"])
        self.assertNotIn("model output", str(binding))
        self.assertNotIn("password", str(binding).lower())
        self.assertEqual(
            ["df-his-api", "df-mic-demo", "df-web-demo"],
            [item["name"] for item in binding["scope"]["projects"]],
        )
        self.assertEqual(
            ["src/api/Demo.java", "src/views/demo.vue"],
            binding["scope"]["allowed_paths"],
        )

    def test_markdown_tells_user_what_is_confirmed(self) -> None:
        binding = build_scope_confirmation_binding(**self._inputs())
        markdown = scope_confirmation_to_markdown(
            binding,
            status="pending",
            reason="等待用户确认改动范围",
        )

        self.assertIn("改动前范围确认", markdown)
        self.assertIn("CONFIRM-SCOPE:", markdown)
        self.assertIn("df-web-demo", markdown)
        self.assertIn("证据与核验项目（不代表要改）", markdown)
        self.assertIn("df-his-api", markdown)
        self.assertIn("src/views/demo.vue", markdown)
        self.assertIn("不确认不会进入改码", markdown)

    def test_scope_changes_invalidate_the_previous_token(self) -> None:
        binding = build_scope_confirmation_binding(**self._inputs())
        changed = self._inputs()
        changed["allowed_paths"] = ["src/other.vue"]
        changed_binding = build_scope_confirmation_binding(**changed)

        self.assertNotEqual(binding["scope_hash"], changed_binding["scope_hash"])
        self.assertFalse(
            validate_scope_confirmation(
                binding["confirmation_token"],
                changed_binding["scope_hash"],
            )
        )

    def test_pack_or_projection_change_invalidates_confirmation(self) -> None:
        binding = build_scope_confirmation_binding(**self._inputs())
        for field, replacement in (
            ("change_context_pack_id", "ccp:sha256:" + "c" * 64),
            ("change_context_projection_hash", "sha256:" + "d" * 64),
        ):
            with self.subTest(field=field):
                changed = self._inputs()
                changed["single_pass_contract"][field] = replacement
                changed_binding = build_scope_confirmation_binding(**changed)
                self.assertNotEqual(binding["scope_hash"], changed_binding["scope_hash"])
                self.assertFalse(
                    validate_scope_confirmation(
                        binding["confirmation_token"],
                        changed_binding["scope_hash"],
                    )
                )


if __name__ == "__main__":
    unittest.main()
