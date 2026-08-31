"""Read-only database probe for Harness task packages.

When the desktop task selects a database profile, the resolved connection
string arrives through the process environment (``DSH_DATABASE_DSN``) — the
same trusted channel as every other credential; it never crosses the JSONL
protocol.  This module performs one bounded, strictly read-only connection,
records the outcome as package evidence, and never writes to the database.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from app.sensitive_text import contains_sensitive_text, redact_sensitive_text


PROBE_SCHEMA = "harness-database-probe.v1"
_MAX_SCHEMA_COUNT = 200
_DSN_SANITIZE = re.compile(r"//[^@/]+@")
_SENSITIVE_HINT = re.compile(r"(?:password|passwd|pwd)=", re.IGNORECASE)


def probe_readonly_database(*, package_dir: str | Path) -> dict[str, object] | None:
    """Run one read-only probe when a DSN is present; record it as evidence."""

    dsn = os.environ.get("DSH_DATABASE_DSN", "").strip()
    if dsn == "":
        return None
    package = Path(package_dir).expanduser().resolve()
    record: dict[str, object] = {
        "schema": PROBE_SCHEMA,
        "status": "failed",
        "driver": "psycopg",
        "mode": "readonly",
        "error": "",
        "server_version": "",
        "database": "",
        "schemas": [],
    }
    try:
        import psycopg
    except ImportError:
        record["error"] = "psycopg_unavailable:数据库驱动未安装"
        return _finalize(package, record)
    try:
        # 只读硬约束：连接级只读 + 事务只读，探测语句只有 SELECT/元数据查询。
        with psycopg.connect(dsn, connect_timeout=8, options="-c default_transaction_read_only=on") as connection:
            record["database"] = _safe_name(connection.info.dbname)
            record["server_version"] = str(connection.info.server_version)[:60]
            with connection.cursor() as cursor:
                cursor.execute("select schema_name from information_schema.schemata order by schema_name")
                schemas = [str(row[0])[:120] for row in cursor.fetchmany(_MAX_SCHEMA_COUNT)]
            record["schemas"] = schemas
            record["status"] = "connected"
            record["error"] = ""
    except Exception as error:  # noqa: BLE001 - 任何连接错误都记为有界事实
        message = _DSN_SANITIZE.sub("//***@", str(error))
        message = redact_sensitive_text(message)
        if contains_sensitive_text(message):
            message = "database_probe_failed_redacted"
        record["error"] = message[:400]
    return _finalize(package, record)


def _finalize(package: Path, record: dict[str, object]) -> dict[str, object]:
    evidence_dir = package / "engineering"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / "database_probe.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**record, "evidence_path": str(path)}


def _safe_name(value: object) -> str:
    text = str(value or "")
    return text if not _SENSITIVE_HINT.search(text) else ""
