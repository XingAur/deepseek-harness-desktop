from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import database, manager_provider_repository
from app.manager_credential_crypto import AesGcmCredentialCipher
from app.manager_provider_repository import (
    DEFAULT_LOCAL_SCOPE,
    CredentialStatus,
    CredentialResolutionUnavailable,
    ManagerProviderRepository,
)


class ManagerProviderRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "harness.sqlite"
        self.master_key = base64.urlsafe_b64encode(b"r" * 32).decode("ascii")
        self.environment = mock.patch.dict(
            os.environ,
            {"HARNESS_MANAGER_CREDENTIAL_MASTER_KEY": self.master_key},
            clear=False,
        )
        self.environment.start()
        self.repository = ManagerProviderRepository()

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.environment.stop()
        self.temp_dir.cleanup()

    def _profile(self):
        return self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="model",
            profile_key="demo",
            display_name="Demo",
            enabled=True,
            connection={"provider_kind": "openai_compatible", "model": "demo-model"},
        )

    def test_repository_saves_config_and_only_reports_credential_status(self) -> None:
        profile = self._profile()
        credential_status = self.repository.upsert_credential(
            profile_id=profile.id,
            field="api_key",
            plaintext="SENTINEL_SECRET",
        )

        with mock.patch.object(
            AesGcmCredentialCipher,
            "decrypt",
            side_effect=AssertionError("status must not decrypt credentials"),
        ):
            status = self.repository.profile_status(profile.id)
            profiles = self.repository.list_profiles()

        self.assertEqual(CredentialStatus.CONFIGURED, credential_status)
        self.assertEqual("configured", status["credentials"]["api_key"])
        self.assertEqual(1, len(profiles))
        self.assertNotIn("SENTINEL_SECRET", json.dumps(status, ensure_ascii=False))
        with database.connect() as connection:
            stored = connection.execute(
                "select cipher_version, ciphertext from manager_provider_credentials"
            ).fetchone()
        raw_storage = b"".join(
            path.read_bytes()
            for path in database.DB_PATH.parent.iterdir()
            if path.is_file()
        )
        self.assertEqual("aesgcm.v1", stored["cipher_version"])
        self.assertTrue(stored["ciphertext"].startswith("aesgcm.v1."))
        self.assertNotIn(b"SENTINEL_SECRET", raw_storage)

    def test_readme_describes_database_backed_provider_storage(self) -> None:
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(
            encoding="utf-8"
        )

        for phrase in (
            "Manager 数据库",
            "加密凭证",
            "数据库永久只读",
            "SQLite 本地",
            "PostgreSQL 团队部署",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, readme)
        self.assertNotIn("Provider 配置存储在 Keychain", readme)
        self.assertNotIn("Provider 配置保存在 Keychain", readme)

    def test_profile_upsert_and_default_scope_listing_are_stable(self) -> None:
        profile = self._profile()
        updated = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="model",
            profile_key="demo",
            display_name="Renamed",
            enabled=False,
            connection={"provider_kind": "openai_compatible", "model": "new-model"},
        )

        profiles = self.repository.list_profiles()

        self.assertEqual(("local", "default"), DEFAULT_LOCAL_SCOPE)
        self.assertEqual(profile.id, updated.id)
        self.assertEqual(profile.created_at, updated.created_at)
        self.assertEqual(1, len(profiles))
        self.assertEqual("Renamed", profiles[0].display_name)
        self.assertFalse(profiles[0].enabled)
        self.assertEqual("new-model", profiles[0].connection["model"])

    def test_stage_a_repository_credential_resolution_is_fail_closed(self) -> None:
        profile = self._profile()
        self.repository.upsert_credential(
            profile_id=profile.id,
            field="api_key",
            plaintext="SENTINEL_SECRET",
        )

        with mock.patch.object(
            AesGcmCredentialCipher,
            "decrypt",
            side_effect=AssertionError("Stage A must not decrypt credentials"),
        ) as decrypt:
            with self.assertRaisesRegex(
                CredentialResolutionUnavailable,
                "^credential_resolution_unavailable$",
            ):
                self.repository.resolve_credential_for_authorized_executor(
                    profile_id=profile.id,
                    field="api_key",
                )
        decrypt.assert_not_called()

    def test_profile_reads_reject_sensitive_connection_json_already_in_database(self) -> None:
        profile = self._profile()
        for sensitive_field in ("api_key", "password", "token"):
            unsafe_json = json.dumps(
                {"provider_kind": "openai_compatible", sensitive_field: "unsafe-value"}
            )
            with database.connect() as connection:
                connection.execute(
                    "update manager_provider_profiles set connection_json = ? where id = ?",
                    (unsafe_json, profile.id),
                )

            for read in (
                lambda: self.repository.profile_status(profile.id),
                self.repository.list_profiles,
            ):
                with self.subTest(sensitive_field=sensitive_field, read=read):
                    with self.assertRaisesRegex(ValueError, "sensitive"):
                        read()

    def test_profile_connection_rejects_credential_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "sensitive"):
            self.repository.upsert_profile(
                scope_type="local",
                scope_key="default",
                provider="model",
                profile_key="unsafe",
                display_name="Unsafe",
                enabled=True,
                connection={"provider_kind": "openai_compatible", "api_key": "unsafe-value"},
            )

        with database.connect() as connection:
            count = connection.execute(
                "select count(*) from manager_provider_profiles"
            ).fetchone()[0]
        self.assertEqual(0, count)

    def test_database_host_rejects_authenticated_oracle_jdbc_before_persistence(self) -> None:
        sentinel = "jdbc:oracle:thin:report_user/Secret9Password@//db.test:1521/HIS"

        with self.assertRaisesRegex(ValueError, "provider_field_schema") as raised:
            self.repository.upsert_profile(
                scope_type="local",
                scope_key="default",
                provider="database",
                profile_key="unsafe-oracle",
                display_name="Unsafe Oracle",
                enabled=True,
                connection={
                    "driver": "oracle",
                    "host": sentinel,
                    "port": "1521",
                    "database": "HIS",
                    "username": "report_user",
                    "readonly_policy": "required",
                },
            )

        self.assertNotIn(sentinel, str(raised.exception))
        with database.connect() as connection:
            count = connection.execute(
                "select count(*) from manager_provider_profiles"
            ).fetchone()[0]
        self.assertEqual(0, count)
        raw = b"".join(
            path.read_bytes()
            for path in database.DB_PATH.parent.glob("harness.sqlite*")
            if path.is_file()
        )
        self.assertNotIn(sentinel.encode(), raw)

    def test_record_action_rejects_secret_shape_and_hashes_authorization_id(self) -> None:
        profile = self._profile()
        authorization_id = "approval-fixture-123"
        self.repository.record_action(
            profile_id=profile.id,
            action_type="credential.updated",
            status="success",
            details={"field": "api_key", "changed": True},
            authorization_id=authorization_id,
            target_alias="profile-demo",
            parameter_hash="sha256:" + "a" * 64,
        )

        with self.assertRaisesRegex(ValueError, "sensitive"):
            self.repository.record_action(
                profile_id=profile.id,
                action_type="credential.updated",
                status="rejected",
                details={"authorization": "Bearer abcdefghijklmnopqrstuvwxyz012345"},
                authorization_id="must-not-persist",
            )

        with database.connect() as connection:
            rows = connection.execute(
                """
                select authorization_id_hash, authorization_hash, target_alias,
                       parameter_hash, details_json, result_summary_json
                from manager_provider_action_audits order by id
                """
            ).fetchall()
        self.assertEqual(1, len(rows))
        self.assertEqual(
            "sha256:" + hashlib.sha256(authorization_id.encode("utf-8")).hexdigest(),
            rows[0]["authorization_id_hash"],
        )
        self.assertEqual(rows[0]["authorization_id_hash"], rows[0]["authorization_hash"])
        self.assertEqual("profile-demo", rows[0]["target_alias"])
        self.assertEqual("sha256:" + "a" * 64, rows[0]["parameter_hash"])
        self.assertEqual(rows[0]["details_json"], rows[0]["result_summary_json"])
        self.assertNotIn(authorization_id, rows[0]["details_json"])

    def test_action_audit_list_returns_redacted_manager_rows(self) -> None:
        profile = self._profile()
        self.repository.record_action(
            profile_id=profile.id,
            action_type="provider.connection_test.requested",
            status="blocked",
            details={
                "attempt_id": "attempt-1",
                "provider": "model",
                "profile_key": "demo",
                "requested_by": "manager",
                "credentials_read": False,
                "external_calls": False,
            },
        )

        rows = self.repository.list_action_audits(
            action_type="provider.connection_test.requested"
        )

        self.assertEqual(1, len(rows))
        self.assertEqual("provider.connection_test.requested", rows[0]["action_type"])
        self.assertEqual("model", rows[0]["provider"])
        self.assertEqual("demo", rows[0]["profile_key"])
        self.assertEqual("attempt-1", rows[0]["details"]["attempt_id"])
        self.assertNotIn("authorization_id_hash", rows[0])

    def test_record_action_cannot_bypass_recursive_bounded_audit_redaction(self) -> None:
        profile = self._profile()
        sentinels = (
            "RandomOpaqueToken9Zx7Qp4Lm2Nv8Bc6",
            "AnotherOpaqueCredential7Ht5Rs3Wq9Yk2",
            "MixedCaseAuthorization8Mn4Vp6Lq2Xz7",
        )

        self.repository.record_action(
            profile_id=profile.id,
            action_type="provider.audit.probe",
            status="blocked",
            details={
                "accessToken": sentinels[0],
                "client-credential-value": sentinels[1],
                "AUTHORIZATION-HEADER": sentinels[2],
                "nested": [{"response-access-token": sentinels[0]}],
                "oversized": "x" * 10_000,
            },
        )

        with database.connect() as connection:
            row = connection.execute(
                "select details_json, result_summary_json "
                "from manager_provider_action_audits"
            ).fetchone()
        self.assertEqual(row["details_json"], row["result_summary_json"])
        self.assertLessEqual(len(row["details_json"].encode("utf-8")), 4096)
        for sentinel in sentinels:
            self.assertNotIn(sentinel, row["details_json"])
        for unsafe_key in (
            "accessToken",
            "client-credential-value",
            "AUTHORIZATION-HEADER",
            "response-access-token",
        ):
            self.assertNotIn(unsafe_key, row["details_json"])
        self.assertIn("REDACTED", row["details_json"])

    def test_record_action_rejects_unsafe_text_metadata_without_audit(self) -> None:
        profile = self._profile()
        sentinel = "CurrentAuditMetadataToken9Zx7Qp4Lm2Nv8Bc6"
        cases = (
            {"action_type": f"Authorization: Bearer {sentinel}"},
            {"status": f"postgresql://audit:{sentinel}@db.example/his"},
            {
                "target_alias": (
                    "-----BEGIN PRIVATE KEY-----\n"
                    f"{sentinel}\n"
                    "-----END PRIVATE KEY-----"
                )
            },
            {"action_type": " provider.audit.probe "},
            {"status": " blocked "},
            {"target_alias": "model/demo"},
        )

        for overrides in cases:
            values = {
                "action_type": "provider.audit.probe",
                "status": "blocked",
                "target_alias": "model-demo",
            }
            values.update(overrides)
            with self.subTest(field=next(iter(overrides))):
                with self.assertRaisesRegex(
                    ValueError,
                    "^provider_audit_input_invalid$",
                ) as raised:
                    self.repository.record_action(
                        profile_id=profile.id,
                        action_type=values["action_type"],
                        status=values["status"],
                        target_alias=values["target_alias"],
                        details={"result": "blocked"},
                    )
                self.assertNotIn(sentinel, str(raised.exception))

        with database.connect() as connection:
            count = int(
                connection.execute(
                    "select count(*) from manager_provider_action_audits"
                ).fetchone()[0]
            )
        self.assertEqual(0, count)

    def test_insert_action_audit_boundary_rejects_noncanonical_metadata(self) -> None:
        profile = self._profile()
        cases = (
            ("action_type", "Provider Audit Probe"),
            ("status", "BLOCKED"),
            ("target_alias", "model/demo"),
        )

        with database.connect() as connection:
            for field, unsafe_value in cases:
                values = {
                    "action_type": "provider.audit.probe",
                    "status": "blocked",
                    "target_alias": "model-demo",
                }
                values[field] = unsafe_value
                with self.subTest(field=field):
                    with self.assertRaisesRegex(
                        ValueError,
                        "^provider_audit_input_invalid$",
                    ):
                        manager_provider_repository._insert_action_audit(
                            connection,
                            action_plan_id=None,
                            profile_id=profile.id,
                            action_type=values["action_type"],
                            target_alias=values["target_alias"],
                            parameter_hash="",
                            authorization_hash="",
                            status=values["status"],
                            result_summary={"result": "blocked"},
                            created_at="2026-08-09T00:00:00+00:00",
                        )
            count = int(
                connection.execute(
                    "select count(*) from manager_provider_action_audits"
                ).fetchone()[0]
            )
        self.assertEqual(0, count)

    def test_insert_action_audit_boundary_rejects_unsafe_hashes_before_insert(self) -> None:
        profile = self._profile()
        sentinel = "Authorization: Bearer FAKE_SENTINEL_NOT_A_REAL_SECRET"
        cases = (
            ("parameter_hash", sentinel),
            ("parameter_hash", "sha256:" + "A" * 64),
            ("authorization_hash", sentinel),
            ("authorization_hash", "sha256:" + "A" * 64),
        )

        traced_statements: list[str] = []
        with database.connect() as connection:
            count_before = int(
                connection.execute(
                    "select count(*) from manager_provider_action_audits"
                ).fetchone()[0]
            )
            connection.set_trace_callback(traced_statements.append)
            for field, unsafe_value in cases:
                values = {
                    "parameter_hash": "sha256:" + "a" * 64,
                    "authorization_hash": "sha256:" + "b" * 64,
                }
                values[field] = unsafe_value
                kind = "sensitive" if unsafe_value == sentinel else "noncanonical"
                with self.subTest(field=field, kind=kind):
                    with self.assertRaisesRegex(
                        ValueError,
                        "^provider_audit_input_invalid$",
                    ) as raised:
                        manager_provider_repository._insert_action_audit(
                            connection,
                            action_plan_id=None,
                            profile_id=profile.id,
                            action_type="provider.audit.probe",
                            target_alias="model-demo",
                            parameter_hash=values["parameter_hash"],
                            authorization_hash=values["authorization_hash"],
                            status="blocked",
                            result_summary={"result": "blocked"},
                            created_at="2026-08-09T00:00:00+00:00",
                        )
                    self.assertNotIn(sentinel, str(raised.exception))
            connection.set_trace_callback(None)
            count_after = int(
                connection.execute(
                    "select count(*) from manager_provider_action_audits"
                ).fetchone()[0]
            )

        insert_statements = [
            statement
            for statement in traced_statements
            if statement.lstrip().lower().startswith("insert ")
        ]
        self.assertEqual([], insert_statements)
        self.assertEqual(count_before, count_after)

    def test_atomic_import_rechecks_existing_profile_scope_inside_transaction(self) -> None:
        self._profile()

        result = self.repository.import_profiles_once(
            source_sha256="a" * 64,
            profiles=[
                {
                    "provider": "model",
                    "profile_key": "other",
                    "display_name": "Other",
                    "enabled": True,
                    "connection": {"model": "other"},
                }
            ],
        )

        self.assertEqual("profiles_exist", result.status)
        self.assertEqual(0, result.imported_count)
        self.assertEqual(["demo"], [profile.profile_key for profile in self.repository.list_profiles()])
        with database.connect() as connection:
            import_count = connection.execute(
                "select count(*) from manager_provider_imports"
            ).fetchone()[0]
        self.assertEqual(0, import_count)

    def test_direct_upsert_rejects_unknown_connection_field_without_profile_write(self) -> None:
        with self.assertRaisesRegex(ValueError, "provider_field_schema"):
            self.repository.upsert_profile(
                scope_type="local",
                scope_key="default",
                provider="model",
                profile_key="unknown-connection",
                display_name="Unknown connection",
                enabled=True,
                connection={"model": "demo", "undeclared_field": "unsafe"},
            )

        self.assertEqual((0, 0), self._profile_and_import_count())

    def test_direct_import_rejects_unknown_connection_before_transaction(self) -> None:
        with self.assertRaisesRegex(ValueError, "provider_field_schema"):
            self.repository.import_profiles_once(
                source_sha256="b" * 64,
                profiles=[
                    {
                        "provider": "model",
                        "profile_key": "unknown-import",
                        "display_name": "Unknown import",
                        "enabled": True,
                        "connection": {"model": "demo", "undeclared_field": "unsafe"},
                    }
                ],
            )

        self.assertEqual((0, 0), self._profile_and_import_count())

    def test_profile_reads_fail_closed_for_injected_unknown_connection(self) -> None:
        profile = self._profile()
        with database.connect() as connection:
            connection.execute(
                "update manager_provider_profiles set connection_json = ? where id = ?",
                (json.dumps({"model": "demo", "undeclared_field": "unsafe"}), profile.id),
            )

        for read in (self.repository.list_profiles, lambda: self.repository.profile_status(profile.id)):
            with self.subTest(read=read):
                with self.assertRaisesRegex(ValueError, "provider_field_schema"):
                    read()

    def test_provider_credential_allowlist_blocks_cross_provider_fields(self) -> None:
        model_profile = self._profile()

        with self.assertRaisesRegex(ValueError, "undeclared_credential_field"):
            self.repository.upsert_credential(
                profile_id=model_profile.id,
                field="password",
                plaintext="SENTINEL_SECRET",
            )

        with database.connect() as connection:
            count = connection.execute(
                "select count(*) from manager_provider_credentials"
            ).fetchone()[0]
        self.assertEqual(0, count)

    def test_database_password_is_allowed_but_undeclared_database_credential_is_not(self) -> None:
        profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="database",
            profile_key="his-readonly",
            display_name="HIS readonly",
            enabled=True,
            connection={
                "driver": "postgresql",
                "host": "db.test",
                "port": "5432",
                "database": "his",
                "schema": "public",
                "username": "readonly",
                "readonly_policy": "required",
            },
        )

        self.assertEqual(
            CredentialStatus.CONFIGURED,
            self.repository.upsert_credential(
                profile_id=profile.id,
                field="password",
                plaintext="SENTINEL_SECRET",
            ),
        )
        with self.assertRaisesRegex(ValueError, "undeclared_credential_field"):
            self.repository.upsert_credential(
                profile_id=profile.id,
                field="api_key",
                plaintext="SENTINEL_OTHER_SECRET",
            )
        self.assertEqual({"password": "configured"}, self.repository.credential_statuses(profile_id=profile.id))

    def test_credential_status_rejects_injected_undeclared_field(self) -> None:
        profile = self._profile()
        with database.connect() as connection:
            connection.execute(
                """
                insert into manager_provider_credentials
                    (profile_id, credential_field, cipher_version, ciphertext, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    profile.id,
                    "password",
                    "injected.v1",
                    "not-a-real-credential",
                    database.now_iso(),
                    database.now_iso(),
                ),
            )

        for read in (
            lambda: self.repository.credential_statuses(profile_id=profile.id),
            lambda: self.repository.profile_status(profile.id),
        ):
            with self.subTest(read=read):
                with self.assertRaisesRegex(ValueError, "undeclared_credential_field"):
                    read()

    def test_secret_public_values_are_rejected_on_write_without_echo(self) -> None:
        sentinel = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"
        public_fields = ("scope_type", "scope_key", "provider", "profile_key", "display_name")
        for field in public_fields:
            values = {
                "scope_type": "local",
                "scope_key": "default",
                "provider": "model",
                "profile_key": "demo",
                "display_name": "Demo",
            }
            values[field] = sentinel
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "sensitive_public_field") as raised:
                    self.repository.upsert_profile(
                        **values,
                        enabled=True,
                        connection={"model": "demo"},
                    )
                self.assertNotIn(sentinel, str(raised.exception))

        self.assertEqual((0, 0), self._profile_and_import_count())

    def test_secret_public_values_in_database_fail_closed_without_echo(self) -> None:
        profile = self._profile()
        sentinel = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"
        with database.connect() as connection:
            connection.execute(
                "update manager_provider_profiles set display_name = ? where id = ?",
                (sentinel, profile.id),
            )

        for read in (self.repository.list_profiles, lambda: self.repository.profile_status(profile.id)):
            with self.subTest(read=read):
                with self.assertRaisesRegex(ValueError, "sensitive_public_field") as raised:
                    read()
                self.assertNotIn(sentinel, str(raised.exception))

    @staticmethod
    def _profile_and_import_count() -> tuple[int, int]:
        with database.connect() as connection:
            profile_count = connection.execute(
                "select count(*) from manager_provider_profiles"
            ).fetchone()[0]
            import_count = connection.execute(
                "select count(*) from manager_provider_imports"
            ).fetchone()[0]
        return int(profile_count), int(import_count)


if __name__ == "__main__":
    unittest.main()
