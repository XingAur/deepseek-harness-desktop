from __future__ import annotations

import importlib.util
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.plugin_test_layout import PLUGIN_SOURCE_ROOT, REPOSITORY_ROOT


os.environ.setdefault("HARNESS_ENABLE_STAGED_PLUGIN_TESTS", "1")
os.environ.setdefault(
    "HARNESS_STAGED_PLUGIN_ROOT",
    str(PLUGIN_SOURCE_ROOT),
)
import app.pg_evidence as adapter


ROOT = REPOSITORY_ROOT
HARNESS_ROOT = ROOT / "Harness"
PLUGIN_ROOT = PLUGIN_SOURCE_ROOT / "his-engineering"
PROVIDER_PATH = PLUGIN_ROOT / "scripts" / "pg_evidence.py"


def load_provider():
    module_name = "compatibility_his_engineering_pg_evidence"
    spec = importlib.util.spec_from_file_location(module_name, PROVIDER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def policy(module):
    return module.PgEvidencePolicy(
        schema_version="1.0-pg-evidence-profiles",
        default_mode="off",
        profiles={
            "his_test": module.PgProfilePolicy(
                name="his_test",
                environment="test",
                enabled=True,
                max_rows=2,
                connect_timeout_seconds=5,
                query_timeout_seconds=10,
                total_timeout_seconds=45,
                max_metadata_queries=3,
                sensitive_column_patterns=("patient", "phone"),
            )
        },
    )


def profile(module):
    return module.PgProfile(
        name="his_test",
        dsn_configured=True,
        user_configured=True,
        password_configured=True,
        credential_prefix="pg_his_test_readonly",
    )


def write_adapter_plugin(root: Path, *, contract_version: str = "pg-evidence.v2") -> None:
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(PLUGIN_ROOT / "scripts" / "database_read.py", scripts / "database_read.py")
    shutil.copy2(PROVIDER_PATH, scripts / "pg_evidence.py")
    (root / "capabilities.json").write_text(
        json.dumps(
            {
                "schema_version": "his-capabilities.v1",
                "plugin": "his-engineering",
                "plugin_version": "0.1.0",
                "capabilities": [
                    {
                        "name": "database.inspect",
                        "provider": "postgresql",
                        "contract_version": contract_version,
                        "mutation_level": "L1",
                        "credential_class": "database_readonly",
                        "entrypoint": "scripts/database_read.py",
                        "enabled": True,
                        "scopes": [
                            "database:metadata:read",
                            "database:rows:read",
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def write_runtime_config(path: Path, roots: list[Path]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "his-capability-runtime-config.v1",
                "routing_mode": "legacy",
                "plugin_roots": [str(root) for root in roots],
                "external_writes_default": False,
                "default_timeout_seconds": 60,
            }
        ),
        encoding="utf-8",
    )


class PgEvidenceCompatibilityTests(unittest.TestCase):
    def test_provider_accepts_the_jdbc_postgresql_url_used_by_his_credentials(self) -> None:
        provider = load_provider()
        self.assertEqual(
            "postgresql://db.example.invalid:5432/df_his",
            provider.normalize_postgres_dsn(
                "jdbc:postgresql://db.example.invalid:5432/df_his"
            ),
        )
        self.assertEqual(
            "postgresql://db.example.invalid/df_his",
            provider.normalize_postgres_dsn("postgresql://db.example.invalid/df_his"),
        )

    @classmethod
    def setUpClass(cls) -> None:
        cls.provider = load_provider()

    def test_harness_module_is_a_thin_reexport_of_plugin_provider(self) -> None:
        self.assertEqual(PROVIDER_PATH.resolve(), Path(adapter.__provider_source__).resolve())
        self.assertEqual(
            PROVIDER_PATH.resolve(),
            Path(inspect.getsourcefile(adapter.PgEvidenceRequest) or "").resolve(),
        )
        adapter_source = Path(adapter.__file__).read_text(encoding="utf-8")
        for implementation_marker in (
            "class PgProfile:",
            "class PgEvidencePlan:",
            "def validate_readonly_sql(",
            "def run_pg_evidence(",
        ):
            self.assertNotIn(implementation_marker, adapter_source)
        self.assertNotIn("/Users/lym/plugins", adapter_source)

    def test_adapter_loads_only_a_strict_provider_explicitly_listed_by_runtime_config(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pg-adapter-config-") as directory:
            root = Path(directory)
            configured = root / "configured" / "his-engineering"
            hostile_unlisted = root / "hostile" / "his-engineering"
            write_adapter_plugin(configured)
            write_adapter_plugin(hostile_unlisted)
            (hostile_unlisted / "scripts" / "pg_evidence.py").write_text(
                "raise RuntimeError('hostile unlisted provider loaded')\n",
                encoding="utf-8",
            )
            config = root / "capabilities.json"
            write_runtime_config(config, [configured])

            loaded, provider_path = adapter._load_provider(
                include_staging=False,
                config_path=config,
            )

        self.assertEqual(
            (configured / "scripts" / "pg_evidence.py").resolve(),
            provider_path,
        )
        self.assertTrue(hasattr(loaded, "validate_readonly_sql"))

    def test_adapter_fails_closed_when_configured_descriptor_contract_is_not_exact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pg-adapter-invalid-") as directory:
            root = Path(directory)
            invalid = root / "invalid" / "his-engineering"
            write_adapter_plugin(invalid, contract_version="pg-evidence.v999")
            config = root / "capabilities.json"
            write_runtime_config(config, [invalid])

            with self.assertRaises(ImportError):
                adapter._load_provider(
                    include_staging=False,
                    config_path=config,
                )

    def test_sql_guard_plan_candidate_template_mask_and_parameter_hash_are_equal(self) -> None:
        provider = self.provider
        guarded_sql = (
            "SELECT patient_phone FROM his_test.his_config "
            "WHERE code = %(code)s FOR UPDATE"
        )
        adapter_guard = adapter.validate_readonly_sql(guarded_sql, {"code": "secret-value"})
        provider_guard = provider.validate_readonly_sql(guarded_sql, {"code": "secret-value"})
        self.assertEqual(provider_guard.status, adapter_guard.status)
        self.assertEqual(provider_guard.blockers, adapter_guard.blockers)

        sql = (
            "SELECT patient_phone, value FROM his_test.his_config "
            "WHERE code = %(code)s"
        )
        with tempfile.TemporaryDirectory(prefix="pg-compatibility-") as directory:
            root = Path(directory)
            adapter_plan = adapter.build_pg_evidence_plan(
                adapter.PgEvidenceRequest(
                    subject="配置证据",
                    keywords=("配置",),
                    sql=sql,
                    parameters={"code": "secret-value"},
                ),
                policy(adapter),
                [profile(adapter)],
                root,
            )
            provider_plan = provider.build_pg_evidence_plan(
                provider.PgEvidenceRequest(
                    subject="配置证据",
                    keywords=("配置",),
                    sql=sql,
                    parameters={"code": "secret-value"},
                ),
                policy(provider),
                [profile(provider)],
                root,
            )

        self.assertEqual(provider_plan.status, adapter_plan.status)
        self.assertEqual(
            [item.to_dict() for item in provider_plan.candidates],
            [item.to_dict() for item in adapter_plan.candidates],
        )
        self.assertEqual(provider_plan.query_template_id, adapter_plan.query_template_id)
        self.assertEqual(
            provider.mask_sensitive_rows(
                [{"patient_phone": "13800138000", "value": "safe"}],
                ("patient", "phone"),
            ),
            adapter.mask_sensitive_rows(
                [{"patient_phone": "13800138000", "value": "safe"}],
                ("patient", "phone"),
            ),
        )
        self.assertEqual(
            provider.build_parameter_audit({"code": "secret-value", "unused": 3}),
            adapter.build_parameter_audit({"code": "secret-value", "unused": 3}),
        )

    def test_cli_source_routes_only_through_capability_service(self) -> None:
        source = (HARNESS_ROOT / "tools" / "pg_evidence.py").read_text(encoding="utf-8")
        self.assertIn("build_database_capability_service", source)
        self.assertNotIn("CapabilityRuntime", source)
        self.assertNotIn("CapabilityRegistry", source)
        self.assertNotIn("resolve_plugin_root", source)
        self.assertNotIn("load_credentials_file", source)
        self.assertNotIn("run_pg_evidence(", source)
        self.assertNotIn("build_psycopg_executor_factory", source)
        self.assertNotIn("/Users/lym/plugins", source)

    def test_cli_plan_rebuilds_redacted_artifacts_from_runtime_result(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pg-cli-runtime-") as directory:
            root = Path(directory)
            request_file = root / "request.json"
            policy_file = root / "policy.json"
            credentials_file = root / "credentials.json"
            output_dir = root / "outputs"
            request_file.write_text(
                json.dumps(
                    {
                        "subject": "运行时配置证据",
                        "keywords": ["配置"],
                        "sql": (
                            "SELECT patient_phone, value FROM his_test.his_config "
                            "WHERE code = %(code)s"
                        ),
                        "parameters": {"code": "secret-parameter"},
                    }
                ),
                encoding="utf-8",
            )
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0-pg-evidence-profiles",
                        "default_mode": "off",
                        "profiles": {
                            "his_test": {
                                "environment": "test",
                                "enabled": True,
                                "max_rows": 2,
                                "connect_timeout_seconds": 5,
                                "query_timeout_seconds": 10,
                                "total_timeout_seconds": 45,
                                "max_metadata_queries": 3,
                                "sensitive_column_patterns": ["patient", "phone"],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            credentials_file.write_text(
                json.dumps(
                    {
                        "pg_his_test_readonly_dsn": "postgresql://secret-host/secret-db",
                        "pg_his_test_readonly_user": "secret-user",
                        "pg_his_test_readonly_password": "secret-password",
                        "unrelated_write_password": "must-not-be-allowlisted",
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(HARNESS_ROOT / "tools" / "pg_evidence.py"),
                    "--request-file",
                    str(request_file),
                    "--profile-policy",
                    str(policy_file),
                    "--credentials-file",
                    str(credentials_file),
                    "--mode",
                    "plan",
                    "--project-root",
                    str(root),
                    "--output-dir",
                    str(output_dir),
                ],
                cwd=HARNESS_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=10,
                check=False,
            )

            self.assertEqual(0, completed.returncode, completed.stdout)
            audit = json.loads(
                (output_dir / "pg_evidence_audit.json").read_text(encoding="utf-8")
            )
            self.assertTrue(audit["capability_runtime"])
            self.assertFalse(audit["provider"]["database_connection_attempted"])
            self.assertEqual(
                [
                    "pg_his_test_readonly_dsn",
                    "pg_his_test_readonly_password",
                    "pg_his_test_readonly_user",
                ],
                audit["runtime"]["environment_keys"],
            )
            serialized = completed.stdout + "\n" + "\n".join(
                path.read_text(encoding="utf-8") for path in output_dir.iterdir()
            )
            for secret in (
                "secret-parameter",
                "secret-host",
                "secret-db",
                "secret-user",
                "secret-password",
                "must-not-be-allowlisted",
            ):
                self.assertNotIn(secret, serialized)

    def test_cli_execute_also_uses_runtime_and_sql_guard_before_connection(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pg-cli-execute-guard-") as directory:
            root = Path(directory)
            request_file = root / "request.json"
            policy_file = root / "policy.json"
            credentials_file = root / "credentials.json"
            output_dir = root / "outputs"
            request_file.write_text(
                json.dumps(
                    {
                        "subject": "禁止写 SQL",
                        "keywords": ["配置"],
                        "sql": "UPDATE his_test.his_config SET value = %(value)s",
                        "parameters": {"value": "secret-parameter"},
                    }
                ),
                encoding="utf-8",
            )
            policy_file.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0-pg-evidence-profiles",
                        "default_mode": "off",
                        "profiles": {
                            "his_test": {
                                "environment": "test",
                                "enabled": True,
                                "max_rows": 2,
                                "connect_timeout_seconds": 5,
                                "query_timeout_seconds": 10,
                                "total_timeout_seconds": 45,
                                "max_metadata_queries": 3,
                                "sensitive_column_patterns": ["patient", "phone"],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            credentials_file.write_text(
                json.dumps(
                    {
                        "pg_his_test_readonly_dsn": "postgresql://secret-host/secret-db",
                        "pg_his_test_readonly_user": "secret-user",
                        "pg_his_test_readonly_password": "secret-password",
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(HARNESS_ROOT / "tools" / "pg_evidence.py"),
                    "--request-file",
                    str(request_file),
                    "--profile-policy",
                    str(policy_file),
                    "--credentials-file",
                    str(credentials_file),
                    "--mode",
                    "execute",
                    "--project-root",
                    str(root),
                    "--output-dir",
                    str(output_dir),
                ],
                cwd=HARNESS_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=10,
                check=False,
            )

            self.assertEqual(1, completed.returncode, completed.stdout)
            audit = json.loads(
                (output_dir / "pg_evidence_audit.json").read_text(encoding="utf-8")
            )
            self.assertTrue(audit["capability_runtime"])
            self.assertFalse(audit["provider"]["database_connection_attempted"])
            result = json.loads(
                (output_dir / "pg_evidence_result.json").read_text(encoding="utf-8")
            )
            self.assertEqual("blocked", result["status"])
            plan = json.loads(
                (output_dir / "pg_evidence_plan.json").read_text(encoding="utf-8")
            )
            self.assertNotIn(
                "没有同时满足策略与只读凭证完整性要求的 PG Profile。",
                plan["blockers"],
            )
            serialized = completed.stdout + "\n" + "\n".join(
                path.read_text(encoding="utf-8") for path in output_dir.iterdir()
            )
            for secret in (
                "secret-parameter",
                "secret-host",
                "secret-db",
                "secret-user",
                "secret-password",
            ):
                self.assertNotIn(secret, serialized)


if __name__ == "__main__":
    unittest.main()
