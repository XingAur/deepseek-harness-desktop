from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
from collections import deque
from collections.abc import Iterable, Mapping
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import database
from app.mcp_audit import prepare_mcp_audit_event, prepare_mcp_evidence


MCP_STORE_SCHEMA_VERSION = "his-mcp-store.v1"
_BUSY_TIMEOUT_MS = 5_000
_GENESIS_EVENT_HASH = "0" * 64
_TABLES = frozenset(
    {"mcp_store_meta", "mcp_evidence_records", "mcp_audit_events"}
)
_TRIGGERS = frozenset(
    {
        "mcp_evidence_no_update",
        "mcp_evidence_no_delete",
        "mcp_audit_no_update",
        "mcp_audit_no_delete",
    }
)
_COLUMNS = {
    "mcp_store_meta": (
        "schema_version",
        "created_at",
    ),
    "mcp_evidence_records": (
        "evidence_ref",
        "request_id",
        "capability",
        "provider",
        "payload_json",
        "payload_sha256",
        "created_at",
    ),
    "mcp_audit_events": (
        "id",
        "event_json",
        "previous_event_hash",
        "event_hash",
        "request_id",
        "trace_id",
        "task_id",
        "run_id",
        "created_at",
    ),
}


class McpPersistenceError(RuntimeError):
    """The independent MCP evidence/audit store failed closed."""


class SqliteMcpStore:
    """Independent append-only MCP evidence store and hash-chained audit ledger."""

    def __init__(self, path: Path) -> None:
        self.path = self._validated_path(path)
        created = self._create_owner_only_file_if_missing()
        try:
            self._initialize_or_validate()
        except Exception:
            if created:
                # Leave the new empty/partial file in place for diagnosis. Never
                # replace or repair a path that may have become externally owned.
                pass
            raise
        try:
            os.chmod(self.path, 0o600)
        except OSError as exc:
            raise McpPersistenceError("MCP store permissions cannot be secured") from exc

    @staticmethod
    def _validated_path(path: Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            raise McpPersistenceError("MCP store path must be absolute")
        if candidate.is_symlink():
            raise McpPersistenceError("MCP store path cannot be a symlink")
        parent = candidate.parent
        if not parent.is_dir() or parent.is_symlink():
            raise McpPersistenceError("MCP store parent must be an existing directory")
        try:
            resolved = candidate.resolve(strict=False)
            main_database = database.DB_PATH.expanduser().resolve(strict=False)
        except OSError as exc:
            raise McpPersistenceError("MCP store path is unavailable") from exc
        if resolved == main_database:
            raise McpPersistenceError("MCP store must be separate from the Harness database")
        if resolved.exists() and not resolved.is_file():
            raise McpPersistenceError("MCP store path must be a regular file")
        return resolved

    def _create_owner_only_file_if_missing(self) -> bool:
        if self.path.exists():
            return False
        try:
            descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_RDWR,
                0o600,
            )
        except FileExistsError:
            if self.path.is_symlink() or not self.path.is_file():
                raise McpPersistenceError("MCP store path changed during initialization") from None
            return False
        except OSError as exc:
            raise McpPersistenceError("MCP store cannot be created") from exc
        else:
            os.close(descriptor)
            return True

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=_BUSY_TIMEOUT_MS / 1000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys = on")
        connection.execute("pragma recursive_triggers = on")
        connection.execute(f"pragma busy_timeout = {_BUSY_TIMEOUT_MS}")
        connection.execute("pragma synchronous = full")
        return connection

    def _initialize_or_validate(self) -> None:
        try:
            with closing(self._connect()) as connection:
                tables = self._object_names(connection, "table")
                if not tables:
                    self._create_schema(connection)
                else:
                    self._validate_schema(connection, tables=tables)
                mode = str(connection.execute("pragma journal_mode = wal").fetchone()[0]).lower()
                if mode != "wal":
                    raise McpPersistenceError("MCP store could not enable WAL mode")
        except McpPersistenceError:
            raise
        except sqlite3.Error as exc:
            raise McpPersistenceError("MCP store schema is unavailable") from exc

    @staticmethod
    def _object_names(connection: sqlite3.Connection, object_type: str) -> frozenset[str]:
        rows = connection.execute(
            "select name from sqlite_master where type = ? and name not like 'sqlite_%'",
            (object_type,),
        ).fetchall()
        return frozenset(str(row[0]) for row in rows)

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        now = self._now()
        with connection:
            connection.executescript(
                """
                create table mcp_store_meta (
                    schema_version text primary key,
                    created_at text not null
                );

                create table mcp_evidence_records (
                    evidence_ref text primary key,
                    request_id text not null unique,
                    capability text not null,
                    provider text not null,
                    payload_json text not null,
                    payload_sha256 text not null,
                    created_at text not null
                );

                create table mcp_audit_events (
                    id integer primary key,
                    event_json text not null,
                    previous_event_hash text not null,
                    event_hash text not null unique,
                    request_id text not null,
                    trace_id text not null,
                    task_id text not null,
                    run_id text not null,
                    created_at text not null
                );

                create index mcp_evidence_request_idx
                    on mcp_evidence_records(request_id);
                create index mcp_audit_request_idx
                    on mcp_audit_events(request_id, id);
                create index mcp_audit_trace_idx
                    on mcp_audit_events(trace_id, id);
                create index mcp_audit_task_run_idx
                    on mcp_audit_events(task_id, run_id, id);

                create trigger mcp_evidence_no_update
                before update on mcp_evidence_records
                begin
                    select raise(abort, 'MCP evidence is append-only');
                end;

                create trigger mcp_evidence_no_delete
                before delete on mcp_evidence_records
                begin
                    select raise(abort, 'MCP evidence is append-only');
                end;

                create trigger mcp_audit_no_update
                before update on mcp_audit_events
                begin
                    select raise(abort, 'MCP audit is append-only');
                end;

                create trigger mcp_audit_no_delete
                before delete on mcp_audit_events
                begin
                    select raise(abort, 'MCP audit is append-only');
                end;
                """
            )
            connection.execute(
                "insert into mcp_store_meta(schema_version, created_at) values (?, ?)",
                (MCP_STORE_SCHEMA_VERSION, now),
            )

    def _validate_schema(
        self,
        connection: sqlite3.Connection,
        *,
        tables: frozenset[str],
    ) -> None:
        if "mcp_store_meta" not in tables:
            raise McpPersistenceError("MCP store schema metadata is missing")
        try:
            rows = connection.execute(
                "select schema_version, created_at from mcp_store_meta"
            ).fetchall()
        except sqlite3.Error as exc:
            raise McpPersistenceError("MCP store schema metadata is invalid") from exc
        if (
            len(rows) != 1
            or rows[0]["schema_version"] != MCP_STORE_SCHEMA_VERSION
            or not isinstance(rows[0]["created_at"], str)
            or not rows[0]["created_at"]
        ):
            raise McpPersistenceError("MCP store schema version is unsupported")
        if tables != _TABLES:
            raise McpPersistenceError("MCP store tables do not match the frozen schema")
        for table, expected in _COLUMNS.items():
            columns = tuple(
                str(row[1])
                for row in connection.execute(f"pragma table_info({table})").fetchall()
            )
            if columns != expected:
                raise McpPersistenceError("MCP store columns do not match the frozen schema")
        if self._object_names(connection, "trigger") != _TRIGGERS:
            raise McpPersistenceError("MCP store append-only guards are missing")

    def store(
        self,
        *,
        request_id: str,
        capability: str,
        provider: str,
        payload: Mapping[str, Any],
    ) -> str:
        snapshot, encoded, reference = prepare_mcp_evidence(
            request_id=request_id,
            capability=capability,
            provider=provider,
            payload=payload,
        )
        del snapshot
        payload_json = encoded.decode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute("begin immediate")
                self._validate_schema(
                    connection,
                    tables=self._object_names(connection, "table"),
                )
                existing = connection.execute(
                    """
                    select evidence_ref, capability, provider, payload_json, payload_sha256
                    from mcp_evidence_records where request_id = ?
                    """,
                    (request_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["evidence_ref"] == reference
                        and existing["capability"] == capability
                        and existing["provider"] == provider
                        and existing["payload_json"] == payload_json
                        and existing["payload_sha256"] == digest
                    ):
                        return reference
                    raise McpPersistenceError("MCP request evidence conflicts with an existing record")
                connection.execute(
                    """
                    insert into mcp_evidence_records(
                        evidence_ref, request_id, capability, provider,
                        payload_json, payload_sha256, created_at
                    ) values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reference,
                        request_id,
                        capability,
                        provider,
                        payload_json,
                        digest,
                        self._now(),
                    ),
                )
        except McpPersistenceError:
            raise
        except sqlite3.Error as exc:
            raise McpPersistenceError("MCP evidence could not be persisted") from exc
        return reference

    def record(self, event: Mapping[str, Any]) -> None:
        snapshot = prepare_mcp_audit_event(event)
        encoded = self._canonical_json_bytes(snapshot)
        event_json = encoded.decode("utf-8")
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute("begin immediate")
                self._validate_schema(
                    connection,
                    tables=self._object_names(connection, "table"),
                )
                previous_row = connection.execute(
                    "select event_hash from mcp_audit_events order by id desc limit 1"
                ).fetchone()
                previous_hash = (
                    _GENESIS_EVENT_HASH
                    if previous_row is None
                    else str(previous_row["event_hash"])
                )
                event_hash = self._event_hash(previous_hash, encoded)
                connection.execute(
                    """
                    insert into mcp_audit_events(
                        event_json, previous_event_hash, event_hash,
                        request_id, trace_id, task_id, run_id, created_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_json,
                        previous_hash,
                        event_hash,
                        snapshot["request_id"],
                        snapshot["trace_id"],
                        snapshot["task_id"],
                        snapshot["run_id"],
                        self._now(),
                    ),
                )
        except sqlite3.Error as exc:
            raise McpPersistenceError("MCP audit event could not be persisted") from exc

    def load_evidence(self, evidence_ref: str) -> dict[str, Any]:
        if not isinstance(evidence_ref, str) or not evidence_ref:
            raise McpPersistenceError("MCP evidence reference is invalid")
        try:
            with closing(self._connect()) as connection:
                self._validate_schema(
                    connection,
                    tables=self._object_names(connection, "table"),
                )
                row = connection.execute(
                    """
                    select evidence_ref, request_id, capability, provider,
                           payload_json, payload_sha256
                    from mcp_evidence_records where evidence_ref = ?
                    """,
                    (evidence_ref,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise McpPersistenceError("MCP evidence could not be read") from exc
        if row is None:
            raise McpPersistenceError("MCP evidence was not found")
        return self._validated_evidence_row(row)

    def list_audit_events(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1_000:
            raise McpPersistenceError("MCP audit read limit is invalid")
        try:
            with closing(self._connect()) as connection:
                self._validate_schema(
                    connection,
                    tables=self._object_names(connection, "table"),
                )
                rows = connection.execute(
                    """
                    select id, event_json, previous_event_hash, event_hash,
                           request_id, trace_id, task_id, run_id
                    from mcp_audit_events order by id
                    """
                )
                events, _ = self._validated_audit_rows(
                    rows,
                    retain_limit=limit,
                )
        except sqlite3.Error as exc:
            raise McpPersistenceError("MCP audit events could not be read") from exc
        return events

    def verify_integrity(self) -> dict[str, int | str]:
        evidence_count = 0
        audit_count = 0
        try:
            with closing(self._connect()) as connection:
                self._validate_schema(
                    connection,
                    tables=self._object_names(connection, "table"),
                )
                integrity = tuple(
                    str(row[0]) for row in connection.execute("pragma integrity_check")
                )
                if integrity != ("ok",):
                    raise McpPersistenceError("SQLite integrity check failed")
                evidence_rows = connection.execute(
                    """
                    select evidence_ref, request_id, capability, provider,
                           payload_json, payload_sha256
                    from mcp_evidence_records order by evidence_ref
                    """
                )
                for row in evidence_rows:
                    self._validated_evidence_row(row)
                    evidence_count += 1
                audit_rows = connection.execute(
                    """
                    select id, event_json, previous_event_hash, event_hash,
                           request_id, trace_id, task_id, run_id
                    from mcp_audit_events order by id
                    """
                )
                _, audit_count = self._validated_audit_rows(
                    audit_rows,
                    retain_limit=0,
                )
        except (McpPersistenceError, sqlite3.Error, TypeError, ValueError):
            return {
                "status": "failed",
                "evidence_records": evidence_count,
                "audit_events": audit_count,
            }
        return {
            "status": "passed",
            "evidence_records": evidence_count,
            "audit_events": audit_count,
        }

    @staticmethod
    def _validated_evidence_row(row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(str(row["payload_json"]))
        except (json.JSONDecodeError, UnicodeError, TypeError):
            raise McpPersistenceError("MCP evidence payload is invalid") from None
        snapshot, encoded, reference = prepare_mcp_evidence(
            request_id=str(row["request_id"]),
            capability=str(row["capability"]),
            provider=str(row["provider"]),
            payload=payload,
        )
        if (
            encoded.decode("utf-8") != row["payload_json"]
            or hashlib.sha256(encoded).hexdigest() != row["payload_sha256"]
            or reference != row["evidence_ref"]
        ):
            raise McpPersistenceError("MCP evidence integrity verification failed")
        return copy.deepcopy(snapshot)

    @classmethod
    def _validated_audit_rows(
        cls,
        rows: Iterable[sqlite3.Row],
        *,
        retain_limit: int,
    ) -> tuple[tuple[dict[str, Any], ...], int]:
        previous_hash = _GENESIS_EVENT_HASH
        events: deque[dict[str, Any]] = deque(maxlen=retain_limit)
        previous_id = 0
        row_count = 0
        for row in rows:
            try:
                event = json.loads(str(row["event_json"]))
            except (json.JSONDecodeError, UnicodeError, TypeError):
                raise McpPersistenceError("MCP audit event is invalid") from None
            snapshot = prepare_mcp_audit_event(event)
            encoded = cls._canonical_json_bytes(snapshot)
            row_id = row["id"]
            if (
                isinstance(row_id, bool)
                or not isinstance(row_id, int)
                or row_id <= previous_id
                or row["event_json"] != encoded.decode("utf-8")
                or row["previous_event_hash"] != previous_hash
                or row["event_hash"] != cls._event_hash(previous_hash, encoded)
                or row["request_id"] != snapshot["request_id"]
                or row["trace_id"] != snapshot["trace_id"]
                or row["task_id"] != snapshot["task_id"]
                or row["run_id"] != snapshot["run_id"]
            ):
                raise McpPersistenceError("MCP audit chain integrity verification failed")
            previous_id = row_id
            previous_hash = str(row["event_hash"])
            events.append(copy.deepcopy(snapshot))
            row_count += 1
        return tuple(events), row_count

    @staticmethod
    def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
        try:
            return json.dumps(
                dict(value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise McpPersistenceError("MCP persistence payload is invalid") from exc

    @staticmethod
    def _event_hash(previous_hash: str, encoded: bytes) -> str:
        return hashlib.sha256(previous_hash.encode("ascii") + b"\n" + encoded).hexdigest()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
