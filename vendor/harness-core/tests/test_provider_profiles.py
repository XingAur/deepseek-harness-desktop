from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app import database
from app.manager_provider_repository import ManagerProviderRepository
from app.provider_profiles import (
    build_provider_connection_test_plan,
    build_provider_profile_status,
    default_provider_profiles,
    import_legacy_provider_profiles,
    load_provider_profiles,
    provider_profile_from_form,
    save_provider_profiles,
)


class ProviderProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "harness.sqlite"

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_profiles_are_reported_without_secret_values(self) -> None:
        status = build_provider_profile_status(
            [
                {
                    "provider": "yunxiao",
                    "profile_key": "company-yunxiao",
                    "credential_ref": "aliyun_devops_pat",
                    "connection": {
                        "organization_id_ref": "aliyun_devops_organization_id",
                        "project_key": "DFHIS",
                    },
                },
                {
                    "provider": "gitlab",
                    "profile_key": "company-gitlab",
                    "credential_ref": "gitlab_token",
                    "connection": {"host": "gitlab.company.test", "group": "his"},
                },
            ]
        )

        self.assertEqual("his-provider-profiles.v1", status["schema_version"])
        self.assertFalse(status["changed"])
        self.assertEqual(2, status["profile_count"])
        rendered = json.dumps(status, ensure_ascii=False)
        self.assertIn("aliyun_devops_pat", rendered)
        self.assertIn("gitlab_token", rendered)
        self.assertNotIn("Bearer", rendered)

    def test_database_test_connection_must_match_runtime_connection_shape(self) -> None:
        status = build_provider_profile_status(
            [
                {
                    "provider": "database",
                    "profile_key": "his-main-db",
                    "credential_ref": "his_db_readonly",
                    "connection": {
                        "driver": "postgresql",
                        "host": "db.test",
                        "port": "5432",
                        "database": "his",
                        "schema": "public",
                    },
                    "test_connection": {
                        "driver": "postgresql",
                        "host": "db.test",
                        "port": "5432",
                        "database": "his",
                        "schema": "public",
                    },
                }
            ]
        )

        profile = status["profiles"][0]
        self.assertTrue(profile["test_connection_matches_runtime"])
        self.assertEqual([], profile["issues"])
        self.assertEqual("not_run", profile["test_connection_status"])

    def test_database_test_connection_drift_is_blocked(self) -> None:
        status = build_provider_profile_status(
            [
                {
                    "provider": "database",
                    "profile_key": "his-main-db",
                    "credential_ref": "his_db_readonly",
                    "connection": {
                        "driver": "postgresql",
                        "host": "db.test",
                        "port": "5432",
                        "database": "his",
                        "schema": "public",
                    },
                    "test_connection": {
                        "driver": "postgresql",
                        "host": "db.other",
                        "port": "5432",
                        "database": "his",
                        "schema": "public",
                    },
                }
            ]
        )

        profile = status["profiles"][0]
        self.assertFalse(profile["test_connection_matches_runtime"])
        self.assertIn("database_test_connection_drift", profile["issues"])

    def test_profile_rejects_secret_shaped_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "sensitive value"):
            build_provider_profile_status(
                [
                    {
                        "provider": "yunxiao",
                        "profile_key": "company-yunxiao",
                        "credential_ref": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456",
                        "connection": {},
                    }
                ]
            )

    def test_default_profiles_cover_remote_and_local_providers_without_values(self) -> None:
        status = build_provider_profile_status(default_provider_profiles())

        self.assertEqual(
            {"yunxiao", "gitlab", "github", "git", "database", "knowledge", "model"},
            {profile["provider"] for profile in status["profiles"]},
        )
        rendered = json.dumps(status, ensure_ascii=False)
        self.assertIn("aliyun_devops_pat", rendered)
        self.assertIn("aliyun_devops_organization_id", rendered)
        self.assertNotIn("Bearer", rendered)
        self.assertNotIn("password", rendered.lower())

    def test_connection_test_plan_is_inert_but_needs_no_harness_confirmation(self) -> None:
        plan = build_provider_connection_test_plan(
            [
                {
                    "provider": "yunxiao",
                    "profile_key": "company-yunxiao",
                    "credential_ref": "aliyun_devops_pat",
                    "connection": {
                        "organization_id_ref": "aliyun_devops_organization_id",
                        "project_key": "DFHIS",
                    },
                },
                {
                    "provider": "database",
                    "profile_key": "his-main-db",
                    "credential_ref": "his_db_readonly",
                    "connection": {
                        "driver": "postgresql",
                        "host": "db.test",
                        "port": "5432",
                        "database": "his",
                        "schema": "public",
                    },
                    "test_connection": {
                        "driver": "postgresql",
                        "host": "db.test",
                        "port": "5432",
                        "database": "his",
                        "schema": "public",
                    },
                },
            ]
        )

        self.assertEqual("his-provider-connection-test-plan.v1", plan["schema_version"])
        self.assertFalse(plan["changed"])
        self.assertFalse(plan["credentials_read"])
        self.assertFalse(plan["external_calls"])
        self.assertFalse(plan["execution_allowed"])
        self.assertEqual(["planned", "planned"], [item["status"] for item in plan["tests"]])
        self.assertFalse(plan["confirmation_required"])
        self.assertTrue(all(not item["confirmation_required"] for item in plan["tests"]))
        self.assertTrue(all(item["execution_allowed"] for item in plan["tests"]))

    def test_connection_test_plan_blocks_database_drift(self) -> None:
        plan = build_provider_connection_test_plan(
            [
                {
                    "provider": "database",
                    "profile_key": "his-main-db",
                    "credential_ref": "his_db_readonly",
                    "connection": {
                        "driver": "postgresql",
                        "host": "db.test",
                        "port": "5432",
                        "database": "his",
                        "schema": "public",
                    },
                    "test_connection": {
                        "driver": "postgresql",
                        "host": "db.other",
                        "port": "5432",
                        "database": "his",
                        "schema": "public",
                    },
                }
            ]
        )

        self.assertEqual("blocked", plan["tests"][0]["status"])
        self.assertIn("database_test_connection_drift", plan["tests"][0]["blockers"])

    def test_missing_profile_store_loads_safe_defaults_without_creating_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "profiles.json"

            profiles = load_provider_profiles(store_path)

            self.assertFalse(store_path.exists())
            self.assertEqual(
                {"yunxiao", "gitlab", "github", "git", "database", "knowledge", "model"},
                {profile["provider"] for profile in profiles},
            )

    def test_save_and_load_provider_profiles_imports_allowed_fields_without_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "profiles.json"
            profiles = [
                {
                    "provider": "yunxiao",
                    "profile_key": "company-yunxiao",
                    "credential_ref": "aliyun_devops_pat",
                    "connection": {
                        "organization_id_ref": "aliyun_devops_organization_id",
                        "project_key": "DFHIS",
                    },
                }
            ]

            result = save_provider_profiles(profiles, store_path)
            loaded = load_provider_profiles(store_path)

            self.assertTrue(result["changed"])
            self.assertTrue(store_path.is_file())
            self.assertEqual(1, len(loaded))
            self.assertEqual("yunxiao", loaded[0]["provider"])
            self.assertEqual("DFHIS", loaded[0]["connection"]["project_key"])
            self.assertNotIn("organization_id_ref", loaded[0]["connection"])
            rendered = store_path.read_text(encoding="utf-8")
            self.assertIn("aliyun_devops_pat", rendered)
            self.assertNotIn("Bearer", rendered)
            self.assertNotIn("password", rendered.lower())

    def test_save_provider_profiles_rejects_secret_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "profiles.json"

            with self.assertRaisesRegex(ValueError, "sensitive field"):
                save_provider_profiles(
                    [
                        {
                            "provider": "database",
                            "profile_key": "his-main-db",
                            "credential_ref": "his_db_readonly",
                            "connection": {
                                "driver": "postgresql",
                                "password": "short-but-still-secret",
                            },
                        }
                    ],
                    store_path,
                )

            self.assertFalse(store_path.exists())

    def test_provider_profile_from_form_builds_database_profile_with_matching_test_identity(self) -> None:
        profile = provider_profile_from_form(
            {
                "provider": ["database"],
                "profile_key": ["his-main-db"],
                "credential_ref": ["his_db_readonly"],
                "connection_json": [
                    json.dumps(
                        {
                            "driver": "postgresql",
                            "host": "db.test",
                            "port": "5432",
                            "database": "his",
                            "schema": "public",
                        }
                    )
                ],
                "test_connection_json": [
                    json.dumps(
                        {
                            "driver": "postgresql",
                            "host": "db.test",
                            "port": "5432",
                            "database": "his",
                            "schema": "public",
                        }
                    )
                ],
            }
        )

        status = build_provider_profile_status([profile])

        self.assertEqual("database", profile["provider"])
        self.assertTrue(status["profiles"][0]["test_connection_matches_runtime"])

    def test_legacy_import_is_one_way_redacted_and_preserves_source(self) -> None:
        store_path = Path(self.temp_dir.name) / "legacy.json"
        payload = {
            "schema_version": "his-provider-profile-store.v1",
            "profiles": [
                {
                    "provider": "model",
                    "profile_key": "demo",
                    "credential_ref": "SENTINEL_CREDENTIAL_REFERENCE",
                    "connection": {
                        "provider_kind": "openai_compatible",
                        "base_url": "https://api.example.test/v1",
                        "model": "demo-model",
                        "legacy_unused": "discard-me",
                    },
                }
            ],
        }
        source = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        store_path.write_bytes(source)
        repository = ManagerProviderRepository()

        first = import_legacy_provider_profiles(store_path, repository)
        second = import_legacy_provider_profiles(store_path, repository)
        status = repository.profile_status(repository.list_profiles()[0].id)

        self.assertEqual("imported", first["status"])
        self.assertEqual(1, first["imported_count"])
        self.assertFalse(first["credentials_imported"])
        self.assertEqual("already_imported", second["status"])
        self.assertEqual(1, len(repository.list_profiles()))
        self.assertEqual(source, store_path.read_bytes())
        self.assertNotIn("legacy_unused", status["connection"])
        self.assertNotIn("SENTINEL_CREDENTIAL_REFERENCE", json.dumps(status, ensure_ascii=False))
        with database.connect() as connection:
            credential_count = connection.execute(
                "select count(*) from manager_provider_credentials"
            ).fetchone()[0]
            import_count = connection.execute(
                "select count(*) from manager_provider_imports"
            ).fetchone()[0]
            stored_connections = connection.execute(
                "select connection_json from manager_provider_profiles"
            ).fetchall()
        self.assertEqual(0, credential_count)
        self.assertEqual(1, import_count)
        self.assertNotIn(
            "SENTINEL_CREDENTIAL_REFERENCE",
            json.dumps([row["connection_json"] for row in stored_connections]),
        )

    def test_load_prefers_manager_database_and_does_not_parse_legacy_json_again(self) -> None:
        repository = ManagerProviderRepository()
        repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="git",
            profile_key="local",
            display_name="Local",
            enabled=True,
            connection={"repository_path": "/tmp/repository", "remote": "origin"},
        )
        invalid_legacy = Path(self.temp_dir.name) / "invalid.json"
        invalid_legacy.write_text("not-json", encoding="utf-8")

        profiles = load_provider_profiles(invalid_legacy)

        self.assertEqual(["local"], [profile["profile_key"] for profile in profiles])

    def test_legacy_import_rejects_sensitive_record_before_any_profile_write(self) -> None:
        store_path = Path(self.temp_dir.name) / "unsafe.json"
        store_path.write_text(
            json.dumps(
                {
                    "schema_version": "his-provider-profile-store.v1",
                    "profiles": [
                        {
                            "provider": "model",
                            "profile_key": "unsafe",
                            "credential_ref": "model-key",
                            "connection": {"model": "demo", "api_key": "SENTINEL_SECRET"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        repository = ManagerProviderRepository()

        with self.assertRaisesRegex(ValueError, "sensitive field"):
            import_legacy_provider_profiles(store_path, repository)

        self.assertEqual([], repository.list_profiles())

    def test_legacy_import_requires_credential_reference_but_does_not_store_it(self) -> None:
        store_path = Path(self.temp_dir.name) / "missing-credential-ref.json"
        store_path.write_text(
            json.dumps(
                {
                    "schema_version": "his-provider-profile-store.v1",
                    "profiles": [
                        {
                            "provider": "model",
                            "profile_key": "missing-ref",
                            "connection": {"model": "demo"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        repository = ManagerProviderRepository()

        with self.assertRaisesRegex(ValueError, "credential_ref"):
            import_legacy_provider_profiles(store_path, repository)

        self.assertEqual([], repository.list_profiles())

    def test_legacy_import_rolls_back_all_profiles_when_second_profile_fails(self) -> None:
        store_path = self._write_legacy_store(
            "profile-failure.json",
            [
                self._legacy_model_profile("first"),
                self._legacy_model_profile("fail-second"),
            ],
        )
        source = store_path.read_bytes()
        repository = ManagerProviderRepository()
        with database.connect() as connection:
            connection.execute(
                """
                create trigger fail_second_legacy_profile
                before insert on manager_provider_profiles
                when new.profile_key = 'fail-second'
                begin
                    select raise(abort, 'injected second profile failure');
                end
                """
            )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected second profile failure"):
            import_legacy_provider_profiles(store_path, repository)

        self.assertEqual((0, 0, 0), self._manager_import_counts())
        self.assertEqual(source, store_path.read_bytes())

    def test_legacy_import_rolls_back_profiles_when_import_marker_fails(self) -> None:
        store_path = self._write_legacy_store(
            "marker-failure.json",
            [self._legacy_model_profile("first")],
        )
        source = store_path.read_bytes()
        repository = ManagerProviderRepository()
        with database.connect() as connection:
            connection.execute(
                """
                create trigger fail_legacy_import_marker
                before insert on manager_provider_imports
                begin
                    select raise(abort, 'injected import marker failure');
                end
                """
            )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected import marker failure"):
            import_legacy_provider_profiles(store_path, repository)

        self.assertEqual((0, 0, 0), self._manager_import_counts())
        self.assertEqual(source, store_path.read_bytes())

    def test_concurrent_legacy_import_is_serialized_without_duplicates(self) -> None:
        store_path = self._write_legacy_store(
            "concurrent.json",
            [self._legacy_model_profile("one")],
        )
        repository = ManagerProviderRepository()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _index: import_legacy_provider_profiles(store_path, repository),
                    range(2),
                )
            )

        self.assertEqual(["already_imported", "imported"], sorted(item["status"] for item in results))
        self.assertEqual((1, 1, 1), self._manager_import_counts())

    def _write_legacy_store(
        self,
        name: str,
        profiles: list[dict[str, object]],
    ) -> Path:
        store_path = Path(self.temp_dir.name) / name
        store_path.write_text(
            json.dumps(
                {
                    "schema_version": "his-provider-profile-store.v1",
                    "profiles": profiles,
                }
            ),
            encoding="utf-8",
        )
        return store_path

    @staticmethod
    def _legacy_model_profile(profile_key: str) -> dict[str, object]:
        return {
            "provider": "model",
            "profile_key": profile_key,
            "credential_ref": "model-key",
            "connection": {"provider_kind": "openai_compatible", "model": "demo"},
        }

    @staticmethod
    def _manager_import_counts() -> tuple[int, int, int]:
        with database.connect() as connection:
            scope_count = connection.execute(
                "select count(*) from manager_provider_scopes"
            ).fetchone()[0]
            profile_count = connection.execute(
                "select count(*) from manager_provider_profiles"
            ).fetchone()[0]
            import_count = connection.execute(
                "select count(*) from manager_provider_imports"
            ).fetchone()[0]
        return int(scope_count), int(profile_count), int(import_count)


if __name__ == "__main__":
    unittest.main()
