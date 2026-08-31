from __future__ import annotations

import inspect
import sqlite3
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from app import database
from app.mcp_audit import AUDIT_FIELDS, McpAuditError
from app.mcp_persistence import McpPersistenceError, SqliteMcpStore


def evidence_payload(title: str = "Requirement") -> dict[str, object]:
    return {
        "schema_version": "his-mcp-result-envelope.v1",
        "request_id": "request-001",
        "capability": "workitem.read",
        "provider": "yunxiao",
        "status": "success",
        "data": {"item": {"id": "DFHIS-100", "title": title}},
        "evidence_ref": "source-evidence:DFHIS-100:v1",
    }


def audit_event(
    *,
    request_id: str = "request-001",
    trace_id: str = "request-001",
    evidence_ref: str = "",
) -> dict[str, object]:
    event: dict[str, object] = {
        "request_id": request_id,
        "capability": "workitem.read",
        "provider": "yunxiao",
        "mutation_level": "L1",
        "status": "success",
        "trace_id": trace_id,
        "server": "yunxiao",
        "tool": "workitem_get",
        "duration_ms": 12,
        "evidence_ref": evidence_ref,
        "error_code": "",
        "retryable": False,
        "timestamp": "2026-08-30T00:00:00Z",
        "task_id": "task-1",
        "run_id": "run-1",
        "project_id": "project-1",
        "repository_id": "repository-1",
        "context_pack_id": "pack-1",
    }
    assert set(event) == AUDIT_FIELDS
    return event


class SqliteMcpStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "state" / "mcp.sqlite"
        self.path.parent.mkdir()

    def test_persists_evidence_and_audit_across_process_lifetime(self) -> None:
        first = SqliteMcpStore(self.path)
        payload = evidence_payload()
        reference = first.store(
            request_id="request-001",
            capability="workitem.read",
            provider="yunxiao",
            payload=payload,
        )
        first.record(audit_event(evidence_ref=reference))

        second = SqliteMcpStore(self.path)

        self.assertEqual(payload, second.load_evidence(reference))
        self.assertEqual((audit_event(evidence_ref=reference),), second.list_audit_events())
        self.assertEqual(
            {"status": "passed", "evidence_records": 1, "audit_events": 1},
            second.verify_integrity(),
        )

    def test_identical_evidence_replay_is_idempotent_but_conflict_is_rejected(self) -> None:
        store = SqliteMcpStore(self.path)
        arguments = {
            "request_id": "request-001",
            "capability": "workitem.read",
            "provider": "yunxiao",
            "payload": evidence_payload(),
        }

        first = store.store(**arguments)
        second = store.store(**arguments)

        self.assertEqual(first, second)
        self.assertEqual(1, store.verify_integrity()["evidence_records"])
        with self.assertRaises(McpPersistenceError):
            store.store(**{**arguments, "payload": evidence_payload("Changed")})

    def test_audit_is_strict_append_only_and_concurrently_chain_safe(self) -> None:
        store = SqliteMcpStore(self.path)

        def append(index: int) -> None:
            store.record(
                audit_event(
                    request_id=f"request-{index:03d}",
                    trace_id=f"trace-{index:03d}",
                )
            )

        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(append, range(18)))

        events = store.list_audit_events()
        self.assertEqual(18, len(events))
        self.assertEqual(18, len({event["request_id"] for event in events}))
        self.assertEqual("passed", store.verify_integrity()["status"])

    def test_rejects_unknown_audit_fields_and_sensitive_metadata(self) -> None:
        store = SqliteMcpStore(self.path)
        with self.assertRaises(McpAuditError):
            store.record({"unexpected": "field"})
        unsafe = audit_event()
        unsafe["task_id"] = "Authorization: Bearer sentinel"
        with self.assertRaises(McpAuditError):
            store.record(unsafe)

    def test_database_triggers_reject_update_and_delete(self) -> None:
        store = SqliteMcpStore(self.path)
        reference = store.store(
            request_id="request-001",
            capability="workitem.read",
            provider="yunxiao",
            payload=evidence_payload(),
        )
        store.record(audit_event(evidence_ref=reference))

        with closing(sqlite3.connect(self.path)) as connection, connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "update mcp_evidence_records set provider = ? where evidence_ref = ?",
                    ("gitlab", reference),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("delete from mcp_audit_events")

    def test_missing_append_only_guard_blocks_later_writes_and_integrity(self) -> None:
        store = SqliteMcpStore(self.path)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("drop trigger mcp_audit_no_delete")

        with self.assertRaises(McpPersistenceError):
            store.record(audit_event())
        self.assertEqual("failed", store.verify_integrity()["status"])

    def test_audit_reads_are_bounded_and_keep_chronological_order(self) -> None:
        store = SqliteMcpStore(self.path)
        for index in range(5):
            store.record(
                audit_event(
                    request_id=f"request-{index}",
                    trace_id=f"trace-{index}",
                )
            )

        events = store.list_audit_events(limit=2)

        self.assertEqual(["request-3", "request-4"], [event["request_id"] for event in events])
        with self.assertRaises(McpPersistenceError):
            store.list_audit_events(limit=0)

    def test_integrity_and_audit_reads_stream_rows_without_fetchall(self) -> None:
        self.assertNotIn(
            ".fetchall()",
            inspect.getsource(SqliteMcpStore.list_audit_events),
        )
        self.assertNotIn(
            ".fetchall()",
            inspect.getsource(SqliteMcpStore.verify_integrity),
        )

    def test_evidence_hash_and_audit_chain_tampering_are_detected(self) -> None:
        store = SqliteMcpStore(self.path)
        reference = store.store(
            request_id="request-001",
            capability="workitem.read",
            provider="yunxiao",
            payload=evidence_payload(),
        )
        store.record(audit_event(evidence_ref=reference))

        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("drop trigger mcp_evidence_no_update")
            connection.execute(
                "update mcp_evidence_records set payload_sha256 = ? where evidence_ref = ?",
                ("0" * 64, reference),
            )
        with self.assertRaises(McpPersistenceError):
            store.load_evidence(reference)
        self.assertEqual("failed", store.verify_integrity()["status"])

        audit_path = self.path.parent / "audit-tamper.sqlite"
        audit_store = SqliteMcpStore(audit_path)
        audit_store.record(audit_event())
        with closing(sqlite3.connect(audit_path)) as connection, connection:
            connection.execute("drop trigger mcp_audit_no_update")
            connection.execute(
                "update mcp_audit_events set event_hash = ? where id = 1",
                ("f" * 64,),
            )
        with self.assertRaises(McpPersistenceError):
            audit_store.list_audit_events()
        self.assertEqual("failed", audit_store.verify_integrity()["status"])

    def test_unknown_schema_is_rejected_without_reinitialization(self) -> None:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "create table mcp_store_meta(schema_version text primary key, created_at text not null)"
            )
            connection.execute(
                "insert into mcp_store_meta values (?, ?)",
                ("future-schema.v99", "2026-08-30T00:00:00Z"),
            )
        before = self.path.read_bytes()

        with self.assertRaises(McpPersistenceError):
            SqliteMcpStore(self.path)

        self.assertEqual(before, self.path.read_bytes())

    def test_file_is_owner_only_and_main_harness_database_is_forbidden(self) -> None:
        SqliteMcpStore(self.path)

        mode = stat.S_IMODE(self.path.stat().st_mode)
        self.assertEqual(0o600, mode)
        with self.assertRaises(McpPersistenceError):
            SqliteMcpStore(database.DB_PATH)

    def test_requires_an_explicit_absolute_non_symlink_database_path(self) -> None:
        with self.assertRaises(McpPersistenceError):
            SqliteMcpStore(Path("mcp.sqlite"))
        target = self.path.parent / "target.sqlite"
        SqliteMcpStore(target)
        link = self.path.parent / "linked.sqlite"
        link.symlink_to(target)
        with self.assertRaises(McpPersistenceError):
            SqliteMcpStore(link)

    def test_read_snapshots_do_not_alias_caller_state(self) -> None:
        store = SqliteMcpStore(self.path)
        payload = evidence_payload()
        reference = store.store(
            request_id="request-001",
            capability="workitem.read",
            provider="yunxiao",
            payload=payload,
        )
        loaded = store.load_evidence(reference)
        loaded["data"]["item"]["title"] = "caller mutation"

        self.assertEqual(
            "Requirement",
            store.load_evidence(reference)["data"]["item"]["title"],
        )


if __name__ == "__main__":
    unittest.main()
