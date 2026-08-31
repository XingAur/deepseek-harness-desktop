from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from app import pg_evidence as legacy_pg


HARNESS_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = HARNESS_ROOT / "tools" / "pg_evidence.py"


def load_cli_module():
    spec = importlib.util.spec_from_file_location("harness_pg_evidence_mcp_cli", CLI_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PgEvidenceCompatibilityTests(unittest.TestCase):
    """The compatibility contract is now fail-closed MCP-only retirement."""

    def test_legacy_adapter_has_stable_fail_closed_tombstone(self) -> None:
        self.assertTrue(legacy_pg.LEGACY_PG_EVIDENCE_DISABLED)
        self.assertFalse(hasattr(legacy_pg, "run_pg_evidence"))
        self.assertFalse(hasattr(legacy_pg, "PgEvidenceRequest"))
        with self.assertRaisesRegex(
            legacy_pg.LegacyPgEvidenceDisabled,
            legacy_pg.LEGACY_PG_EVIDENCE_ERROR_CODE,
        ):
            legacy_pg.require_database_inspect_mcp()

    def test_cli_accepts_only_catalog_scope_for_readonly_alias(self) -> None:
        cli = load_cli_module()
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "connection_alias": "his_test_readonly",
                        "operation": "columns",
                        "schema": "public",
                        "table": "patient",
                    }
                ),
                encoding="utf-8",
            )
            payload = cli.load_request_file(request_path)
        self.assertEqual("columns", payload["operation"])
        request = cli.build_capability_request(payload)
        self.assertEqual("database.inspect", request.capability)
        self.assertEqual("postgresql", request.provider)
        self.assertEqual("preview", request.mode)
        self.assertEqual("L1", request.mutation_level.name)
        self.assertFalse(request.authorization.explicit)
        self.assertEqual(("database:inspect",), request.authorization.scope)

    def test_cli_rejects_legacy_sql_credentials_and_non_readonly_alias(self) -> None:
        cli = load_cli_module()
        invalid_values = (
            {
                "connection_alias": "his_test_readonly",
                "operation": "columns",
                "schema": "public",
                "table": "patient",
                "sql": "select * from patient",
            },
            {
                "connection_alias": "his_test",
                "operation": "columns",
                "schema": "public",
                "table": "patient",
            },
        )
        for value in invalid_values:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                request_path = Path(directory) / "request.json"
                request_path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(SystemExit):
                    cli.load_request_file(request_path)

    def test_cli_source_contains_no_direct_provider_driver_or_secret_input(self) -> None:
        source = CLI_PATH.read_text(encoding="utf-8")
        for required in (
            "McpCapabilityRuntime",
            "build_persistent_mcp_runtime",
            '"database.inspect"',
            '"postgresql"',
        ):
            self.assertIn(required, source)
        for forbidden in (
            "run_pg_evidence",
            "PgEvidenceRequest",
            "psycopg",
            "executor_factory",
            "credentials-file",
            "profile-policy",
            '"sql"',
            "parameters",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
