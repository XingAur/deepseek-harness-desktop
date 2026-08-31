from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.harness_doctor import run_harness_doctor


class HarnessDoctorTests(unittest.TestCase):
    def test_doctor_reports_redacted_database_credential_presence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report = run_harness_doctor(
                database_profile="his_152",
                environment={
                    "pg_his_test_readonly_dsn": "postgresql://secret",
                    "pg_his_test_readonly_user": "df_bi",
                    "pg_his_test_readonly_password": "secret-password",
                },
                database_path=Path(temp_dir) / "harness.sqlite",
            )
        self.assertEqual("degraded", report["status"])
        database = report["checks"]["database_credentials"]
        self.assertEqual([], database["missing_keys"])
        self.assertFalse(database["connection_attempted"])
        self.assertNotIn("secret-password", json.dumps(report, ensure_ascii=False))
        self.assertNotIn("postgresql://secret", json.dumps(report, ensure_ascii=False))

    def test_doctor_checks_local_git_without_remote_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Path(temp_dir) / "repo"
            repository.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
            (repository / "README.md").write_text("local\n", encoding="utf-8")
            report = run_harness_doctor(
                repository_paths=[repository],
                require_git=True,
            )
        self.assertEqual("degraded", report["status"])
        self.assertEqual("ready", report["checks"]["repositories"][0]["status"])
        self.assertEqual([], report["checks"]["repositories"][0]["remotes"])
        self.assertFalse(report["external_calls"])
        self.assertFalse(report["external_writes"])

    def test_doctor_uses_credential_file_keys_without_exposing_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            credentials = Path(temp_dir) / "credentials.json"
            credentials.write_text(
                json.dumps(
                    {
                        "pg_his_test_readonly_dsn": "postgresql://secret-host/df_his",
                        "pg_his_test_readonly_user": "df_bi",
                        "pg_his_test_readonly_password": "secret-password",
                    }
                ),
                encoding="utf-8",
            )
            report = run_harness_doctor(
                database_profile="his_152",
                credentials_file=credentials,
            )
        database = report["checks"]["database_credentials"]
        self.assertEqual([], database["missing_keys"])
        self.assertTrue(database["credential_file_present"])
        rendered = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("secret-password", rendered)
        self.assertNotIn("postgresql://secret-host", rendered)

    def test_missing_plugin_is_reported_as_recoverable_diagnosis(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report = run_harness_doctor(plugin_roots=[Path(temp_dir) / "missing"])
        self.assertEqual("blocked", report["status"])
        self.assertIn("plugin", report["errors"][0])
        self.assertTrue(report["recovery_actions"])

    def test_local_152_policy_delegates_schema_authorization_to_postgresql(self) -> None:
        report = run_harness_doctor(database_profile="his_152", environment={})

        policy = report["checks"]["database_policy"]
        self.assertEqual("ready", policy["status"])
        self.assertEqual("postgresql_account", policy["schema_authorization"])

    def test_incomplete_optional_database_profile_keeps_doctor_degraded(self) -> None:
        report = run_harness_doctor(database_profile="his_152", environment={})

        self.assertEqual("degraded", report["status"])
        self.assertIn("database_credentials_incomplete", report["warnings"])
        self.assertTrue(report["credential_key_names_inspected"])
        self.assertFalse(report["credential_values_exposed"])


if __name__ == "__main__":
    unittest.main()
