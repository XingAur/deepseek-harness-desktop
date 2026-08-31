"""Fail-closed tombstone for the retired direct PostgreSQL evidence adapter.

Runtime database evidence is available only through the frozen
``database.inspect MCP`` catalog connector and ``DataGraphCollector``. This
module remains importable so stale integrations fail with a stable boundary
instead of attempting a legacy provider, driver, SQL client, or credential
fallback.
"""
from __future__ import annotations


LEGACY_PG_EVIDENCE_DISABLED = True
LEGACY_PG_EVIDENCE_ERROR_CODE = (
    "LEGACY_PG_EVIDENCE_DISABLED_USE_DATABASE_INSPECT_MCP"
)


class LegacyPgEvidenceDisabled(RuntimeError):
    """Raised when a retired direct-database integration is invoked."""


def require_database_inspect_mcp() -> None:
    """Reject the legacy route without resolving credentials or opening I/O."""

    raise LegacyPgEvidenceDisabled(LEGACY_PG_EVIDENCE_ERROR_CODE)


__all__ = (
    "LEGACY_PG_EVIDENCE_DISABLED",
    "LEGACY_PG_EVIDENCE_ERROR_CODE",
    "LegacyPgEvidenceDisabled",
    "require_database_inspect_mcp",
)
