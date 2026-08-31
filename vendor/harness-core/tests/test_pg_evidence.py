from __future__ import annotations

import unittest

from app import pg_evidence


class PgEvidenceRetirementTests(unittest.TestCase):
    """The direct PostgreSQL adapter was replaced by database.inspect MCP."""

    def test_legacy_direct_adapter_remains_fail_closed(self) -> None:
        self.assertTrue(pg_evidence.LEGACY_PG_EVIDENCE_DISABLED)
        self.assertEqual(
            "LEGACY_PG_EVIDENCE_DISABLED_USE_DATABASE_INSPECT_MCP",
            pg_evidence.LEGACY_PG_EVIDENCE_ERROR_CODE,
        )
        self.assertFalse(hasattr(pg_evidence, "PgEvidenceRequest"))
        self.assertFalse(hasattr(pg_evidence, "run_pg_evidence"))


if __name__ == "__main__":
    unittest.main()
