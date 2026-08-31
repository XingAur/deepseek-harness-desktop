from __future__ import annotations

import unittest

from app.provider_field_schema import (
    DATABASE_CONNECTION_IDENTITY_FIELDS,
    PROVIDER_CONNECTION_FIELDS,
    provider_field_specs,
    provider_profile_from_typed_form,
    validate_provider_connection,
)


class ProviderFieldSchemaTests(unittest.TestCase):
    def test_connection_allowlists_are_exact_for_all_providers(self) -> None:
        self.assertEqual(
            {
                "yunxiao": ("organization_id", "project_id", "project_key", "workitem_scope"),
                "git": ("repository_path", "remote", "branch_policy", "allowed_paths"),
                "gitlab": ("host", "group", "project", "target_branch"),
                "github": ("owner", "repository"),
                "database": (
                    "driver",
                    "host",
                    "port",
                    "database",
                    "schema",
                    "username",
                    "readonly_policy",
                ),
                "model": (
                    "provider_kind",
                    "base_url",
                    "model",
                    "allowed_endpoint_host",
                    "timeout_seconds",
                    "max_output_tokens",
                ),
                "knowledge": (
                    "knowledge_home",
                    "obsidian_vault",
                    "index_path",
                    "allowed_sources",
                ),
            },
            PROVIDER_CONNECTION_FIELDS,
        )
        self.assertEqual(
            ("driver", "host", "port", "database", "schema"),
            DATABASE_CONNECTION_IDENTITY_FIELDS,
        )

    def test_model_form_separates_declared_credential_from_connection(self) -> None:
        result = provider_profile_from_typed_form(
            {
                "provider": ["model"],
                "profile_key": ["deepseek"],
                "display_name": ["DeepSeek"],
                "enabled": ["on"],
                "provider_kind": ["openai_compatible"],
                "base_url": ["https://api.example.test/v1"],
                "model": ["deepseek-chat"],
                "timeout_seconds": ["20"],
                "api_key": ["SENTINEL_SECRET"],
            }
        )

        self.assertEqual("model", result.provider)
        self.assertEqual("deepseek", result.profile_key)
        self.assertEqual("DeepSeek", result.display_name)
        self.assertTrue(result.enabled)
        self.assertEqual("SENTINEL_SECRET", result.credential_inputs["api_key"])
        self.assertNotIn("api_key", result.connection)
        self.assertEqual("deepseek-chat", result.connection["model"])

    def test_typed_form_rejects_unknown_and_undeclared_secret_fields(self) -> None:
        for field in ("connection_json", "unexpected", "password"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "provider_field_schema"):
                    provider_profile_from_typed_form(
                        {
                            "provider": ["model"],
                            "profile_key": ["demo"],
                            field: ["unsafe-value"],
                        }
                    )

    def test_validate_connection_rejects_unknown_fields_and_sensitive_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "provider_field_schema"):
            validate_provider_connection("gitlab", {"host": "gitlab.test", "token": "unsafe"})
        with self.assertRaisesRegex(ValueError, "provider_field_schema"):
            validate_provider_connection(
                "model",
                {"base_url": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"},
            )
        with self.assertRaisesRegex(ValueError, "provider_field_schema"):
            validate_provider_connection(
                "model",
                {"base_url": "https://service-user:SENTINEL_PASSWORD@api.example.test/v1"},
            )

    def test_provider_hosts_use_positive_hostname_or_ipv4_grammar(self) -> None:
        for provider, field, value in (
            ("database", "host", "db.example.test"),
            ("database", "host", "127.0.0.1"),
            ("gitlab", "host", "gitlab-internal"),
            ("model", "allowed_endpoint_host", "api.example.test"),
        ):
            with self.subTest(provider=provider, value=value):
                self.assertEqual(
                    value,
                    validate_provider_connection(provider, {field: value})[field],
                )

        for value in (
            "fixture_user:Fixture9Pass",
            "db.example.test:1521",
            "fixture_user@db.example.test",
            "-db.example.test",
            "db..example.test",
            "db_example.test",
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValueError, "provider_host_must_be_hostname_or_ipv4"
                ) as raised:
                    validate_provider_connection("database", {"host": value})
                self.assertNotIn(value, str(raised.exception))

    def test_field_specs_mark_only_declared_credentials_as_secret(self) -> None:
        specs = provider_field_specs("database")
        by_name = {spec.name: spec for spec in specs}

        self.assertEqual(set(PROVIDER_CONNECTION_FIELDS["database"]) | {"password"}, set(by_name))
        self.assertFalse(by_name["host"].secret)
        self.assertTrue(by_name["password"].secret)

    def test_sensitive_public_fields_are_rejected_without_echoing_the_value(self) -> None:
        cases = (
            (
                "provider",
                "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456",
            ),
            (
                "profile_key",
                "-----BEGIN PRIVATE KEY-----",
            ),
            (
                "display_name",
                "https://service-user:SENTINEL_PASSWORD@api.example.test/v1",
            ),
        )
        for field, sentinel in cases:
            data = {
                "provider": ["model"],
                "profile_key": ["demo"],
                "display_name": ["Demo"],
            }
            data[field] = [sentinel]
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "provider_field_schema") as raised:
                    provider_profile_from_typed_form(data)
                self.assertNotIn(sentinel, str(raised.exception))

    def test_credential_input_is_preserved_but_excluded_from_repr(self) -> None:
        sentinel = "SENTINEL_SECRET"
        result = provider_profile_from_typed_form(
            {
                "provider": ["model"],
                "profile_key": ["demo"],
                "api_key": [sentinel],
            }
        )

        self.assertEqual(sentinel, result.credential_inputs["api_key"])
        self.assertNotIn(sentinel, repr(result))


if __name__ == "__main__":
    unittest.main()
