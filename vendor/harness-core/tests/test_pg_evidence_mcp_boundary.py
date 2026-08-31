from __future__ import annotations

import unittest
from pathlib import Path

from app import pg_evidence


class PgEvidenceMcpBoundaryTests(unittest.TestCase):
    def test_legacy_direct_provider_is_importable_but_permanently_disabled(self) -> None:
        self.assertTrue(pg_evidence.LEGACY_PG_EVIDENCE_DISABLED)
        self.assertEqual(
            "LEGACY_PG_EVIDENCE_DISABLED_USE_DATABASE_INSPECT_MCP",
            pg_evidence.LEGACY_PG_EVIDENCE_ERROR_CODE,
        )
        with self.assertRaisesRegex(
            pg_evidence.LegacyPgEvidenceDisabled,
            pg_evidence.LEGACY_PG_EVIDENCE_ERROR_CODE,
        ):
            pg_evidence.require_database_inspect_mcp()

    def test_compatibility_module_contains_no_provider_loader_or_direct_driver(self) -> None:
        self.assertIsNotNone(pg_evidence.__file__)
        text = Path(str(pg_evidence.__file__)).read_text(encoding="utf-8")
        for forbidden in (
            "_load_provider",
            "database_read.py",
            "scripts/pg_evidence.py",
            "psycopg",
            "PostgresExecutor",
            "executor_factory",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("database.inspect MCP", text)

    def test_cli_accepts_catalog_scope_only_and_routes_through_mcp_runtime(self) -> None:
        text = (
            Path(__file__).resolve().parents[1] / "tools" / "pg_evidence.py"
        ).read_text(encoding="utf-8")
        for required in (
            "database.inspect",
            "postgresql",
            "McpCapabilityRuntime",
            "connection_alias",
            "operation",
            "schema",
            "table",
        ):
            self.assertIn(required, text)
        for forbidden in (
            "PgEvidenceRequest",
            "build_database_capability_service",
            '"sql"',
            "parameters",
            "credentials-file",
            "executor_factory",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
