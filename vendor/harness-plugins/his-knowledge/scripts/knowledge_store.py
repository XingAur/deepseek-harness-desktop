"""Independent, local SQLite persistence for staged HIS knowledge data."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Optional


DEFAULT_KNOWLEDGE_HOME = Path(
    os.environ.get(
        "HIS_KNOWLEDGE_HOME",
        "/Users/lym/.local/share/his-knowledge",
    )
).expanduser().resolve()

KINDS = {
    "business_rule",
    "code_path",
    "data_dictionary",
    "integration_topology",
    "requirement_history",
    "service_contract",
    "support_boundary",
    "workflow",
    "troubleshooting",
    "personal_memory",
}
AUTHORITIES = {
    "official_policy",
    "verified_runtime",
    "verified_code",
    "reviewed_team_knowledge",
    "personal_preference",
}
ITEM_STATUSES = {"active", "superseded"}
REVIEW_STATUSES = {"approved", "rejected"}

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS knowledge_items (
        id INTEGER PRIMARY KEY,
        stable_key TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        kind TEXT NOT NULL,
        authority TEXT NOT NULL,
        status TEXT NOT NULL,
        hospital_scope TEXT NOT NULL DEFAULT '',
        region_scope TEXT NOT NULL DEFAULT '',
        module_scope TEXT NOT NULL DEFAULT '',
        repo_scope TEXT NOT NULL DEFAULT '',
        branch_scope TEXT NOT NULL DEFAULT '',
        version_label TEXT NOT NULL DEFAULT '',
        valid_from TEXT NOT NULL DEFAULT '',
        valid_until TEXT NOT NULL DEFAULT '',
        source_refs_json TEXT NOT NULL,
        tags_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS knowledge_versions (
        id INTEGER PRIMARY KEY,
        item_id INTEGER NOT NULL,
        version_no INTEGER NOT NULL,
        snapshot_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(item_id, version_no),
        FOREIGN KEY(item_id) REFERENCES knowledge_items(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS knowledge_candidates (
        id INTEGER PRIMARY KEY,
        proposed_key TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        provenance_json TEXT NOT NULL,
        review_status TEXT NOT NULL,
        review_reason TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        reviewed_at TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS knowledge_relations (
        id INTEGER PRIMARY KEY,
        source_key TEXT NOT NULL,
        relation TEXT NOT NULL,
        target_key TEXT NOT NULL,
        UNIQUE(source_key, relation, target_key)
    )
    """,
)


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("value must be JSON serializable") from error


class KnowledgeStore:
    """A deliberately small local store; construction itself has no I/O side effects."""

    def __init__(self, home: Optional[Path] = None, now: Optional[Callable[[], str]] = None) -> None:
        self.home = (Path(home) if home is not None else DEFAULT_KNOWLEDGE_HOME).expanduser().resolve()
        self._now = now or self._utc_now

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @property
    def database_path(self) -> Path:
        """Return the independent knowledge SQLite path without creating it."""
        return self.home / "knowledge.sqlite"

    def connect(self) -> sqlite3.Connection:
        """Open the independent database and enable foreign-key enforcement."""
        self.home.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        """Create the fixed Task 2 schema; repeated calls are idempotent."""
        connection = self.connect()
        try:
            with connection:
                for statement in SCHEMA:
                    connection.execute(statement)
        finally:
            connection.close()

    def upsert_item(
        self,
        *,
        stable_key: str,
        title: str,
        body: str,
        kind: str,
        authority: str,
        status: str = "active",
        hospital_scope: str = "",
        region_scope: str = "",
        module_scope: str = "",
        repo_scope: str = "",
        branch_scope: str = "",
        version_label: str = "",
        valid_from: str = "",
        valid_until: str = "",
        source_refs: Optional[object] = None,
        tags: Optional[object] = None,
    ) -> dict[str, object]:
        """Create or update an item, recording an immutable version only when its content changes."""
        values = self._normalized_item(
            stable_key=stable_key,
            title=title,
            body=body,
            kind=kind,
            authority=authority,
            status=status,
            hospital_scope=hospital_scope,
            region_scope=region_scope,
            module_scope=module_scope,
            repo_scope=repo_scope,
            branch_scope=branch_scope,
            version_label=version_label,
            valid_from=valid_from,
            valid_until=valid_until,
            source_refs=source_refs,
            tags=tags,
        )
        self.initialize()
        connection = self.connect()
        try:
            with connection:
                return self._upsert_normalized_item(connection, values)
        finally:
            connection.close()

    def import_items_atomically(
        self,
        items: list[Mapping[str, object]],
    ) -> list[dict[str, object]]:
        """Normalize every item before SQLite initialization, then commit the batch together."""
        if not isinstance(items, list) or not items:
            raise ValueError("items must be a non-empty list")
        normalized = [self._normalized_item(**dict(item)) for item in items]
        self.initialize()
        connection = self.connect()
        try:
            with connection:
                imported = []
                for values in normalized:
                    imported.append(self._upsert_normalized_item(connection, values))
                return imported
        finally:
            connection.close()

    def get_item(self, stable_key: str) -> Optional[dict[str, object]]:
        """Retrieve a current formal item by stable key."""
        self.initialize()
        connection = self.connect()
        try:
            row = connection.execute("SELECT * FROM knowledge_items WHERE stable_key = ?", (stable_key,)).fetchone()
            return None if row is None else self._item_from_row(connection, row)
        finally:
            connection.close()

    def list_versions(self, stable_key: str) -> list[dict[str, object]]:
        """List immutable snapshots in version order for one stable key."""
        self.initialize()
        connection = self.connect()
        try:
            rows = connection.execute(
                """
                SELECT knowledge_versions.* FROM knowledge_versions
                JOIN knowledge_items ON knowledge_items.id = knowledge_versions.item_id
                WHERE knowledge_items.stable_key = ? ORDER BY knowledge_versions.version_no
                """,
                (stable_key,),
            )
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def list_answerable_items(self, include_superseded: bool = False) -> list[dict[str, object]]:
        """List current items, excluding superseded history unless explicitly requested."""
        self.initialize()
        connection = self.connect()
        try:
            sql = "SELECT * FROM knowledge_items"
            values: tuple[object, ...] = ()
            if not include_superseded:
                sql += " WHERE status != ?"
                values = ("superseded",)
            rows = connection.execute(sql + " ORDER BY stable_key", values)
            return [self._item_from_row(connection, row) for row in rows]
        finally:
            connection.close()

    def read_retrieval_snapshot(
        self,
    ) -> Optional[tuple[tuple[dict[str, object], ...], tuple[tuple[str, str, str], ...]]]:
        """Read formal items and relations without creating or modifying the database.

        ``None`` means the independent database is absent or cannot safely be
        read.  Retrieval callers must treat both cases as no formal evidence.
        """
        if not self.database_path.is_file():
            return None
        try:
            connection = sqlite3.connect(f"{self.database_path.as_uri()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(
                    "SELECT * FROM knowledge_items WHERE status != ? ORDER BY stable_key", ("superseded",)
                )
                items = []
                for row in rows:
                    item = dict(row)
                    item["source_refs"] = json.loads(item["source_refs_json"])
                    item["tags"] = json.loads(item["tags_json"])
                    items.append(item)
                relations = tuple(
                    (str(row["source_key"]), str(row["relation"]), str(row["target_key"]))
                    for row in connection.execute(
                        "SELECT source_key, relation, target_key FROM knowledge_relations "
                        "ORDER BY source_key, relation, target_key"
                    )
                )
                return tuple(items), relations
            finally:
                connection.close()
        except (OSError, sqlite3.Error, json.JSONDecodeError):
            return None

    def create_candidate(
        self, *, proposed_key: str, payload: Mapping[str, object], provenance: Mapping[str, object]
    ) -> dict[str, object]:
        """Persist a Task 2 candidate in the only allowed initial state, ``pending``."""
        if not isinstance(proposed_key, str) or not proposed_key:
            raise ValueError("proposed_key must be a non-empty string")
        if not isinstance(payload, Mapping) or not isinstance(provenance, Mapping):
            raise ValueError("candidate payload and provenance must be mappings")
        payload_json = _canonical_json(dict(payload))
        provenance_json = _canonical_json(dict(provenance))
        self.initialize()
        connection = self.connect()
        try:
            timestamp = self._now()
            with connection:
                cursor = connection.execute(
                    """
                    INSERT INTO knowledge_candidates
                    (proposed_key, payload_json, provenance_json, review_status, review_reason, created_at, reviewed_at)
                    VALUES (?, ?, ?, 'pending', '', ?, '')
                    """,
                    (proposed_key, payload_json, provenance_json, timestamp),
                )
                row = connection.execute("SELECT * FROM knowledge_candidates WHERE id = ?", (cursor.lastrowid,)).fetchone()
                return self._candidate_from_row(row)
        finally:
            connection.close()

    def get_candidate(self, candidate_id: int) -> Optional[dict[str, object]]:
        """Retrieve a candidate and decode its canonical JSON fields."""
        self.initialize()
        connection = self.connect()
        try:
            row = connection.execute("SELECT * FROM knowledge_candidates WHERE id = ?", (candidate_id,)).fetchone()
            return None if row is None else self._candidate_from_row(row)
        finally:
            connection.close()

    def find_candidate_by_payload(self, payload: Mapping[str, object]) -> Optional[dict[str, object]]:
        """Find one candidate by its canonical payload without exposing SQL to callers."""
        if not isinstance(payload, Mapping):
            raise ValueError("candidate payload must be a mapping")
        payload_json = _canonical_json(dict(payload))
        self.initialize()
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT * FROM knowledge_candidates WHERE payload_json = ? ORDER BY id LIMIT 1", (payload_json,)
            ).fetchone()
            return None if row is None else self._candidate_from_row(row)
        finally:
            connection.close()

    def review_candidate(self, candidate_id: int, *, status: str, reason: str) -> dict[str, object]:
        """Record an explicit approved or rejected review result."""
        if status not in REVIEW_STATUSES or not isinstance(reason, str):
            raise ValueError("candidate review must be approved or rejected with a string reason")
        self.initialize()
        connection = self.connect()
        try:
            with connection:
                cursor = connection.execute(
                    """
                    UPDATE knowledge_candidates
                    SET review_status = ?, review_reason = ?, reviewed_at = ?
                    WHERE id = ? AND review_status = 'pending'
                    """,
                    (status, reason, self._now(), candidate_id),
                )
                if cursor.rowcount != 1:
                    raise ValueError("only pending candidates may be reviewed")
                row = connection.execute("SELECT * FROM knowledge_candidates WHERE id = ?", (candidate_id,)).fetchone()
                return self._candidate_from_row(row)
        finally:
            connection.close()

    def promote_candidate_with_audit(self, candidate_id: int, *, reviewer: str, reason: str) -> dict[str, object]:
        """Atomically promote one approved candidate and persist its redacted promotion audit."""
        if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(reason, str) or not reason.strip():
            raise ValueError("promotion audit requires reviewer and reason")
        self.initialize()
        connection = self.connect()
        try:
            with connection:
                row = connection.execute("SELECT * FROM knowledge_candidates WHERE id = ?", (candidate_id,)).fetchone()
                if row is None or row["review_status"] != "approved":
                    raise ValueError("only approved candidates can promote")
                candidate = self._candidate_from_row(row)
                payload = candidate["payload"]
                if not isinstance(payload, dict):
                    raise ValueError("candidate payload must be an item mapping")
                item = self._upsert_normalized_item(connection, self._normalized_item(**payload))
                audit = self._promotion_audit(row["review_reason"], reviewer, reason)
                if audit != row["review_reason"]:
                    connection.execute(
                        "UPDATE knowledge_candidates SET review_reason = ? WHERE id = ?", (audit, candidate_id)
                    )
                return item
        finally:
            connection.close()

    def promote_candidate(self, candidate_id: int) -> dict[str, object]:
        """Promote only an approved candidate using the formal-item primitive."""
        candidate = self.get_candidate(candidate_id)
        if candidate is None or candidate["review_status"] != "approved":
            raise ValueError("only approved candidates can be promoted")
        payload = candidate["payload"]
        if not isinstance(payload, dict):
            raise ValueError("candidate payload must be an item mapping")
        return self.upsert_item(**payload)

    def add_relation(self, source_key: str, relation: str, target_key: str) -> bool:
        """Persist a relation once; duplicate triples return ``False``."""
        if not all(isinstance(value, str) and value for value in (source_key, relation, target_key)):
            raise ValueError("relation values must be non-empty strings")
        self.initialize()
        connection = self.connect()
        try:
            with connection:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO knowledge_relations (source_key, relation, target_key) VALUES (?, ?, ?)",
                    (source_key, relation, target_key),
                )
                return cursor.rowcount == 1
        finally:
            connection.close()

    def _upsert_normalized_item(self, connection: sqlite3.Connection, values: dict[str, object]) -> dict[str, object]:
        existing = connection.execute(
            "SELECT * FROM knowledge_items WHERE stable_key = ?", (values["stable_key"],)
        ).fetchone()
        if existing is not None and existing["content_hash"] == values["content_hash"]:
            return self._item_from_row(connection, existing)
        timestamp = self._now()
        if existing is None:
            values["created_at"] = timestamp
            values["updated_at"] = timestamp
            columns = list(values)
            connection.execute(
                "INSERT INTO knowledge_items ({}) VALUES ({})".format(
                    ", ".join(columns), ", ".join("?" for _ in columns)
                ),
                [values[column] for column in columns],
            )
            item_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            version_no = 1
        else:
            values["created_at"] = existing["created_at"]
            values["updated_at"] = timestamp
            columns = [column for column in values if column not in {"stable_key", "created_at"}]
            connection.execute(
                "UPDATE knowledge_items SET {} WHERE id = ?".format(
                    ", ".join(f"{column} = ?" for column in columns)
                ),
                [values[column] for column in columns] + [existing["id"]],
            )
            item_id = existing["id"]
            version_no = connection.execute(
                "SELECT COALESCE(MAX(version_no), 0) + 1 FROM knowledge_versions WHERE item_id = ?", (item_id,)
            ).fetchone()[0]
        snapshot = dict(values)
        snapshot["version_no"] = version_no
        connection.execute(
            "INSERT INTO knowledge_versions (item_id, version_no, snapshot_json, created_at) VALUES (?, ?, ?, ?)",
            (item_id, version_no, _canonical_json(snapshot), timestamp),
        )
        row = connection.execute("SELECT * FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
        return self._item_from_row(connection, row)

    @staticmethod
    def _promotion_audit(review_reason: str, reviewer: str, reason: str) -> str:
        try:
            reviewed = json.loads(review_reason)
        except json.JSONDecodeError:
            reviewed = {"reason": review_reason}
        if isinstance(reviewed, dict) and "review" in reviewed and "promotion" in reviewed:
            reviewed = reviewed["review"]
        return _canonical_json({
            "promotion": {"reviewer": reviewer.strip(), "reason": reason.strip()},
            "review": reviewed,
        })

    def _normalized_item(self, **values: object) -> dict[str, object]:
        for name, default in {
            "hospital_scope": "", "region_scope": "", "module_scope": "", "repo_scope": "",
            "branch_scope": "", "version_label": "", "valid_from": "", "valid_until": "",
            "source_refs": None, "tags": None,
        }.items():
            values.setdefault(name, default)
        if values["kind"] not in KINDS or values["authority"] not in AUTHORITIES or values["status"] not in ITEM_STATUSES:
            raise ValueError("item kind, authority, and status must use Task 2 enums")
        required_strings = (
            "stable_key", "title", "body", "hospital_scope", "region_scope", "module_scope", "repo_scope",
            "branch_scope", "version_label", "valid_from", "valid_until",
        )
        if not all(isinstance(values[name], str) and values[name] for name in ("stable_key", "title", "body")):
            raise ValueError("stable_key, title, and body must be non-empty strings")
        if not all(isinstance(values[name], str) for name in required_strings):
            raise ValueError("item text fields must be strings")
        values["source_refs"] = [] if values["source_refs"] is None else values["source_refs"]
        values["tags"] = [] if values["tags"] is None else values["tags"]
        if not isinstance(values["source_refs"], list) or not isinstance(values["tags"], list):
            raise ValueError("source_refs and tags must be JSON lists")
        normalized = {name: values[name] for name in required_strings}
        normalized.update({"kind": values["kind"], "authority": values["authority"], "status": values["status"]})
        normalized["source_refs_json"] = _canonical_json(values["source_refs"])
        normalized["tags_json"] = _canonical_json(values["tags"])
        normalized["content_hash"] = hashlib.sha256(_canonical_json(normalized).encode("utf-8")).hexdigest()
        return normalized

    def _item_from_row(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
        item = dict(row)
        item["source_refs"] = json.loads(item["source_refs_json"])
        item["tags"] = json.loads(item["tags_json"])
        item["version_no"] = connection.execute(
            "SELECT MAX(version_no) FROM knowledge_versions WHERE item_id = ?", (item["id"],)
        ).fetchone()[0]
        return item

    @staticmethod
    def _candidate_from_row(row: sqlite3.Row) -> dict[str, object]:
        candidate = dict(row)
        candidate["payload"] = json.loads(candidate["payload_json"])
        candidate["provenance"] = json.loads(candidate["provenance_json"])
        return candidate
