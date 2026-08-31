"""Isolated, explicitly initialized SQLite storage for HIS Engineering delivery records."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


SCHEMA_VERSION = 1
LEGACY_MAX_SCHEMA_VERSION = 62
DEFAULT_HOME = Path("/Users/lym/.local/share/his-engineering")
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
_JSON_OBJECT_FIELDS = frozenset({
    "policy_snapshot", "repository_snapshot", "release_acceptance", "rc_acceptance", "parity_result",
})
_JSON_LIST_FIELDS = frozenset({"commit_records", "remote_results"})
_JSON_FIELDS = _JSON_OBJECT_FIELDS | _JSON_LIST_FIELDS
_TRANSACTION_FIELDS = (
    "transaction_key", "task_id", "source_run_id", "entity_kind", "entity_id", "project_path", "state",
    "plan_hash", "policy_snapshot", "repository_snapshot", "release_acceptance", "rc_acceptance",
    "commit_records", "remote_results", "parity_result", "output_dir", "journal_path", "last_error",
    "created_at", "updated_at",
)
_UPDATE_FIELDS = frozenset({
    "state", "plan_hash", "policy_snapshot", "repository_snapshot", "release_acceptance", "rc_acceptance",
    "commit_records", "remote_results", "parity_result", "output_dir", "journal_path", "last_error",
})
_UPDATE_ORDER = tuple(sorted(_UPDATE_FIELDS))
_PLUGIN_TABLES = frozenset({"delivery_schema_version", "delivery_transactions", "delivery_events", "sqlite_sequence"})
_LEGACY_TABLES = frozenset({"harness_delivery_transactions", "harness_delivery_events", "sqlite_sequence"})

# (name, declared_type, notnull, default, primary_key) from SQLite PRAGMA table_info.
_PLUGIN_CONTRACTS = {
    "delivery_schema_version": (
        ("singleton", "INTEGER", 0, None, 1), ("version", "INTEGER", 1, None, 0),
    ),
    "delivery_transactions": (
        ("id", "INTEGER", 0, None, 1), ("transaction_key", "TEXT", 1, None, 0),
        ("task_id", "INTEGER", 0, None, 0), ("source_run_id", "INTEGER", 0, None, 0),
        ("entity_kind", "TEXT", 1, "''", 0), ("entity_id", "TEXT", 1, "''", 0),
        ("project_path", "TEXT", 1, None, 0), ("state", "TEXT", 1, None, 0),
        ("plan_hash", "TEXT", 1, None, 0), ("policy_snapshot", "TEXT", 1, "'{}'", 0),
        ("repository_snapshot", "TEXT", 1, "'{}'", 0), ("release_acceptance", "TEXT", 1, "'{}'", 0),
        ("rc_acceptance", "TEXT", 1, "'{}'", 0), ("commit_records", "TEXT", 1, "'[]'", 0),
        ("remote_results", "TEXT", 1, "'[]'", 0), ("parity_result", "TEXT", 1, "'{}'", 0),
        ("output_dir", "TEXT", 1, "''", 0), ("journal_path", "TEXT", 1, "''", 0),
        ("last_error", "TEXT", 1, "''", 0), ("created_at", "TEXT", 1, None, 0),
        ("updated_at", "TEXT", 1, None, 0),
    ),
    "delivery_events": (
        ("id", "INTEGER", 0, None, 1), ("transaction_id", "INTEGER", 1, None, 0),
        ("sequence", "INTEGER", 1, None, 0), ("event_type", "TEXT", 1, None, 0),
        ("status", "TEXT", 1, None, 0), ("input_hash", "TEXT", 1, "''", 0),
        ("details", "TEXT", 1, "'{}'", 0), ("created_at", "TEXT", 1, None, 0),
    ),
}
_LEGACY_CONTRACTS = {
    "harness_delivery_transactions": _PLUGIN_CONTRACTS["delivery_transactions"],
    "harness_delivery_events": _PLUGIN_CONTRACTS["delivery_events"],
}
_LEGACY_TABLE_INFO_SQL = {
    "harness_delivery_transactions": "pragma table_info(harness_delivery_transactions)",
    "harness_delivery_events": "pragma table_info(harness_delivery_events)",
}
_PLUGIN_TABLE_INFO_SQL = {
    "delivery_schema_version": "pragma table_info(delivery_schema_version)",
    "delivery_transactions": "pragma table_info(delivery_transactions)",
    "delivery_events": "pragma table_info(delivery_events)",
}
_PLUGIN_INDEXES = {
    "delivery_transactions": {
        "idx_delivery_transactions_entity": (0, "c", ("entity_kind", "entity_id", "id")),
        "idx_delivery_transactions_task": (0, "c", ("task_id", "id")),
        "sqlite_autoindex_delivery_transactions_1": (1, "u", ("transaction_key",)),
    },
    "delivery_events": {
        "idx_delivery_events_transaction": (0, "c", ("transaction_id", "sequence")),
        "sqlite_autoindex_delivery_events_1": (1, "u", ("transaction_id", "sequence")),
    },
}
_EVENT_FOREIGN_KEY = (0, 0, "delivery_transactions", "transaction_id", "id", "NO ACTION", "NO ACTION", "NONE")
_INDEX_LIST_SQL = {
    "delivery_transactions": "pragma index_list(delivery_transactions)",
    "delivery_events": "pragma index_list(delivery_events)",
}
_INDEX_INFO_SQL = {
    "idx_delivery_transactions_entity": "pragma index_info(idx_delivery_transactions_entity)",
    "idx_delivery_transactions_task": "pragma index_info(idx_delivery_transactions_task)",
    "sqlite_autoindex_delivery_transactions_1": "pragma index_info(sqlite_autoindex_delivery_transactions_1)",
    "idx_delivery_events_transaction": "pragma index_info(idx_delivery_events_transaction)",
    "sqlite_autoindex_delivery_events_1": "pragma index_info(sqlite_autoindex_delivery_events_1)",
}
_DDL = (
    """create table delivery_schema_version (
        singleton integer primary key check(singleton = 1), version integer not null
    )""",
    """create table delivery_transactions (
        id integer primary key autoincrement, transaction_key text not null unique, task_id integer,
        source_run_id integer, entity_kind text not null default '', entity_id text not null default '',
        project_path text not null, state text not null, plan_hash text not null,
        policy_snapshot text not null default '{}', repository_snapshot text not null default '{}',
        release_acceptance text not null default '{}', rc_acceptance text not null default '{}',
        commit_records text not null default '[]', remote_results text not null default '[]',
        parity_result text not null default '{}', output_dir text not null default '',
        journal_path text not null default '', last_error text not null default '',
        created_at text not null, updated_at text not null
    )""",
    "create index idx_delivery_transactions_task on delivery_transactions(task_id, id)",
    "create index idx_delivery_transactions_entity on delivery_transactions(entity_kind, entity_id, id)",
    """create table delivery_events (
        id integer primary key autoincrement, transaction_id integer not null, sequence integer not null,
        event_type text not null, status text not null, input_hash text not null default '',
        details text not null default '{}', created_at text not null, unique(transaction_id, sequence),
        foreign key(transaction_id) references delivery_transactions(id)
    )""",
    "create index idx_delivery_events_transaction on delivery_events(transaction_id, sequence)",
)


class DeliveryStore(Protocol):
    def init(self) -> None: ...
    def get_by_key(self, transaction_key: str) -> dict[str, Any] | None: ...
    def add_transaction(self, payload: Mapping[str, Any]) -> int: ...
    def get_transaction(self, transaction_id: int) -> dict[str, Any] | None: ...
    def update_transaction(self, transaction_id: int, **fields: Any) -> None: ...
    def add_event(self, payload: Mapping[str, Any]) -> int: ...
    def get_events(self, transaction_id: int) -> list[dict[str, Any]]: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dump(value: object, field: str) -> str:
    if field in _JSON_OBJECT_FIELDS and not isinstance(value, dict):
        raise ValueError(f"{field} JSON 必须是 object")
    if field in _JSON_LIST_FIELDS and not isinstance(value, list):
        raise ValueError(f"{field} JSON 必须是 list")
    if field == "details" and not isinstance(value, dict):
        raise ValueError("details JSON 必须是 object")
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} JSON 无法序列化") from exc


def _json_load(value: object, field: str) -> Any:
    try:
        decoded = json.loads(str(value))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field} JSON 无效") from exc
    _json_dump(decoded, field)
    return copy.deepcopy(decoded)


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in conn.execute("select name from sqlite_master where type = 'table'")}


def _table_info(conn: sqlite3.Connection, sql: str) -> tuple[tuple[Any, ...], ...]:
    return tuple(tuple(row)[1:] for row in conn.execute(sql))


def _index_contract(conn: sqlite3.Connection, table: str) -> dict[str, tuple[int, str, tuple[str, ...]]]:
    if table not in _INDEX_LIST_SQL:
        raise ValueError("delivery store schema is incompatible")
    rows = conn.execute(_INDEX_LIST_SQL[table]).fetchall()
    result: dict[str, tuple[int, str, tuple[str, ...]]] = {}
    for row in rows:
        name = str(row[1])
        if name not in _INDEX_INFO_SQL:
            raise ValueError("delivery store schema is incompatible")
        columns = tuple(str(item[2]) for item in conn.execute(_INDEX_INFO_SQL[name]))
        result[name] = (int(row[2]), str(row[3]), columns)
    return result


def _hash_fd(fd: int) -> str:
    digest = hashlib.sha256()
    while True:
        block = os.read(fd, 1024 * 1024)
        if not block:
            return digest.hexdigest()
        digest.update(block)


def _write_all(fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written <= 0:
            raise OSError("short write while copying legacy database")
        offset += written


def _metadata(path: Path) -> tuple[int, int, int, int, int, int, str]:
    item = os.lstat(path)
    if stat.S_ISLNK(item.st_mode) or not stat.S_ISREG(item.st_mode):
        raise ValueError("database path must be a regular non-symlink file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (item.st_dev, item.st_ino):
            raise ValueError("database path changed during metadata read")
        digest = _hash_fd(descriptor)
    finally:
        os.close(descriptor)
    return (item.st_dev, item.st_ino, item.st_mode, item.st_size, item.st_mtime_ns, item.st_ctime_ns, digest)


def _source_unchanged(source: Path, before: tuple[int, int, int, int, int, int, str], sidecars: set[str]) -> bool:
    return _metadata(source) == before and sidecars == {
        suffix for suffix in _SIDECAR_SUFFIXES if source.with_name(source.name + suffix).exists()
    }


def _secure_identity(path: Path, *, directory: bool) -> tuple[int, int, int, int]:
    item = os.lstat(path)
    required_type = stat.S_ISDIR if directory else stat.S_ISREG
    if stat.S_ISLNK(item.st_mode) or not required_type(item.st_mode):
        raise ValueError("delivery database path is unsafe")
    if os.name != "nt":
        if item.st_uid != os.geteuid() or item.st_mode & 0o077:
            raise ValueError("delivery database path ownership or mode is unsafe")
        if directory and stat.S_IMODE(item.st_mode) != 0o700:
            raise ValueError("delivery database parent is unsafe")
    return (item.st_dev, item.st_ino, item.st_mode, item.st_uid)


def _open_rw_nofollow(path: Path) -> tuple[int, tuple[int, int, int, int]]:
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    opened = os.fstat(descriptor)
    identity = (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_uid)
    if identity != _secure_identity(path, directory=False):
        os.close(descriptor)
        raise ValueError("delivery database changed during secure open")
    return descriptor, identity


def _owned_regular_identity(path: Path) -> tuple[int, int, int, int]:
    """Identity for a private-directory sidecar; the target DB has stricter modes."""
    item = os.lstat(path)
    if stat.S_ISLNK(item.st_mode) or not stat.S_ISREG(item.st_mode):
        raise ValueError("delivery database sidecar is unsafe")
    if os.name != "nt" and item.st_uid != os.geteuid():
        raise ValueError("delivery database sidecar ownership is unsafe")
    return (item.st_dev, item.st_ino, item.st_mode, item.st_uid)


def _reject_noncanonical_symlink_components(path: Path) -> None:
    """Reject caller-controlled links before canonicalizing; macOS system aliases are canonicalized."""
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        try:
            item = os.lstat(current)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(item.st_mode):
            # macOS exposes /var and /tmp as system aliases; normalize them before safety checks.
            if str(current) in {"/var", "/tmp"}:
                continue
            raise ValueError("database path contains a symlink")


class SQLiteDeliveryStore:
    """Plugin-owned store that neither discovers nor opens Harness paths by default."""

    def __init__(self, database_path: str | os.PathLike[str] | None = None) -> None:
        raw = Path(database_path) if database_path is not None else Path(
            os.environ.get("HIS_ENGINEERING_HOME", str(DEFAULT_HOME))
        ) / "delivery.sqlite"
        if not raw.is_absolute():
            raise ValueError("delivery database path must be absolute")
        _reject_noncanonical_symlink_components(raw)
        self.path = raw.resolve(strict=False)

    def _assert_no_symlink_ancestry(self, path: Path) -> None:
        current = Path(path.anchor)
        for component in path.parts[1:]:
            current = current / component
            try:
                item = os.lstat(current)
            except FileNotFoundError:
                return
            if stat.S_ISLNK(item.st_mode):
                raise ValueError("database path contains a symlink")
            if current != path and not stat.S_ISDIR(item.st_mode):
                raise ValueError("database path ancestry is not a directory")

    def _validate_trusted_ancestry(self, path: Path, *, create_missing: bool) -> list[Path]:
        """Validate every existing POSIX ancestor and safely create any missing tail."""
        if os.name == "nt":
            return []
        current = Path(path.anchor)
        created: list[Path] = []
        must_own_next = False
        for component in path.parts[1:]:
            current = current / component
            try:
                item = os.lstat(current)
            except FileNotFoundError:
                if not create_missing:
                    return created
                os.mkdir(current, 0o700)
                item = os.lstat(current)
                if stat.S_ISLNK(item.st_mode) or not stat.S_ISDIR(item.st_mode) or item.st_uid != os.geteuid():
                    raise ValueError("delivery database created ancestry is unsafe")
                os.chmod(current, 0o700)
                item = os.lstat(current)
                if stat.S_ISLNK(item.st_mode) or not stat.S_ISDIR(item.st_mode) or item.st_uid != os.geteuid():
                    raise ValueError("delivery database created ancestry is unsafe")
                created.append(current)
            if stat.S_ISLNK(item.st_mode) or not stat.S_ISDIR(item.st_mode):
                raise ValueError("delivery database ancestry is unsafe")
            if must_own_next and item.st_uid != os.geteuid():
                raise ValueError("delivery database sticky ancestry owner is unsafe")
            must_own_next = False
            if item.st_mode & 0o022:
                if not item.st_mode & stat.S_ISVTX:
                    raise ValueError("delivery database writable ancestry is unsafe")
                must_own_next = True
        return created

    def _prepare_parent(self) -> tuple[bool, list[Path]]:
        self._assert_no_symlink_ancestry(self.path.parent)
        missing = self._validate_trusted_ancestry(self.path.parent, create_missing=True)
        parent_info = os.lstat(self.path.parent)
        if not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode):
            raise ValueError("delivery database parent is unsafe")
        _secure_identity(self.path.parent, directory=True)
        return bool(missing), missing

    def _validate_parent_readonly(self) -> None:
        self._assert_no_symlink_ancestry(self.path.parent)
        self._validate_trusted_ancestry(self.path.parent, create_missing=False)
        try:
            item = os.lstat(self.path.parent)
        except FileNotFoundError as exc:
            raise RuntimeError("delivery store is not initialized; call init() first") from exc
        if stat.S_ISLNK(item.st_mode) or not stat.S_ISDIR(item.st_mode):
            raise ValueError("delivery database parent is unsafe")
        _secure_identity(self.path.parent, directory=True)

    def _target_kind(self) -> str:
        self._assert_no_symlink_ancestry(self.path)
        try:
            item = os.lstat(self.path)
        except FileNotFoundError:
            return "missing"
        if stat.S_ISLNK(item.st_mode):
            raise ValueError("delivery database target is a symlink")
        if not stat.S_ISREG(item.st_mode):
            raise ValueError("delivery database target is not a regular file")
        _secure_identity(self.path, directory=False)
        return "regular"

    @staticmethod
    def _immutable_uri(path: Path) -> str:
        return path.as_uri() + "?mode=ro&immutable=1"

    def _validate_plugin_connection(self, conn: sqlite3.Connection) -> None:
        if _table_names(conn) != _PLUGIN_TABLES:
            raise ValueError("delivery store schema is incompatible")
        for name, expected in _PLUGIN_CONTRACTS.items():
            if _table_info(conn, _PLUGIN_TABLE_INFO_SQL[name]) != expected:
                raise ValueError("delivery store schema is incompatible")
        if any(_index_contract(conn, table) != expected for table, expected in _PLUGIN_INDEXES.items()):
            raise ValueError("delivery store schema is incompatible")
        foreign_keys = tuple(tuple(row) for row in conn.execute("pragma foreign_key_list(delivery_events)"))
        if foreign_keys != (_EVENT_FOREIGN_KEY,):
            raise ValueError("delivery store schema is incompatible")
        if conn.execute("select 1 from sqlite_master where type in ('trigger', 'view') limit 1").fetchone() is not None:
            raise ValueError("delivery store schema is incompatible")
        row = conn.execute("select version from delivery_schema_version where singleton = 1").fetchone()
        if row is None or int(row[0]) != SCHEMA_VERSION:
            raise ValueError("unsupported delivery store schema version")

    def _validate_existing_plugin(self) -> None:
        self._validate_parent_readonly()
        if self._target_kind() != "regular":
            raise RuntimeError("delivery store is not initialized; call init() first")
        descriptor, identity = _open_rw_nofollow(self.path)
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(self._rw_uri(self.path), uri=True)
            opened = os.fstat(descriptor)
            if identity != _secure_identity(self.path, directory=False) or identity != (
                opened.st_dev, opened.st_ino, opened.st_mode, opened.st_uid,
            ):
                raise ValueError("delivery database changed during validation")
            self._validate_plugin_connection(conn)
        finally:
            try:
                if conn is not None:
                    conn.close()
            finally:
                try:
                    if identity != _secure_identity(self.path, directory=False):
                        raise ValueError("delivery database changed during validation")
                finally:
                    os.close(descriptor)

    @staticmethod
    def _rw_uri(path: Path) -> str:
        # SQLite does not provide a portable, effective URI nofollow flag.  The
        # caller keeps an O_NOFOLLOW descriptor open and verifies this pathname
        # before and after use; the remaining same-UID swap window is documented
        # by the plugin policy rather than hidden behind a non-working URI flag.
        return path.as_uri() + "?mode=rw"

    def _connect(self) -> tuple[sqlite3.Connection, int, tuple[int, int, int, int]]:
        self._validate_existing_plugin()
        descriptor, identity = _open_rw_nofollow(self.path)
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(self._rw_uri(self.path), uri=True, timeout=10, isolation_level=None)
            if identity != _secure_identity(self.path, directory=False) or identity != (
                os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino,
                os.fstat(descriptor).st_mode, os.fstat(descriptor).st_uid,
            ):
                raise ValueError("delivery database changed during connection")
            conn.row_factory = sqlite3.Row
            conn.execute("pragma foreign_keys = on")
            conn.execute("pragma busy_timeout = 10000")
            return conn, descriptor, identity
        except BaseException:
            if conn is not None:
                conn.close()
            os.close(descriptor)
            raise

    @contextmanager
    def _connection(self):
        conn, descriptor, identity = self._connect()
        try:
            yield conn
        finally:
            verification_error: BaseException | None = None
            try:
                before_close = _secure_identity(self.path, directory=False)
                opened = os.fstat(descriptor)
                if identity != before_close or identity != (
                    opened.st_dev, opened.st_ino, opened.st_mode, opened.st_uid,
                ):
                    verification_error = ValueError("delivery database changed during connection")
            except BaseException as exc:
                verification_error = exc
            try:
                conn.close()
            finally:
                try:
                    after_close = _secure_identity(self.path, directory=False)
                    if identity != after_close:
                        verification_error = verification_error or ValueError(
                            "delivery database changed during connection"
                        )
                except BaseException as exc:
                    verification_error = verification_error or exc
                finally:
                    os.close(descriptor)
            if verification_error is not None:
                raise verification_error

    def _cleanup_new_database(self, created_identity: tuple[int, int, int, int]) -> list[str]:
        """Remove only files that are still the files created by this invocation."""
        failures: list[str] = []
        for suffix in _SIDECAR_SUFFIXES:
            candidate = self.path.with_name(self.path.name + suffix)
            try:
                sidecar_identity = _owned_regular_identity(candidate)
            except FileNotFoundError:
                continue
            except ValueError:
                failures.append(candidate.name)
                continue
            try:
                if sidecar_identity == _owned_regular_identity(candidate):
                    candidate.unlink()
                else:
                    failures.append(candidate.name)
            except (FileNotFoundError, ValueError, OSError):
                failures.append(candidate.name)
        try:
            if _secure_identity(self.path, directory=False) == created_identity:
                self.path.unlink()
            else:
                failures.append(self.path.name)
        except FileNotFoundError:
            pass
        except (ValueError, OSError):
            failures.append(self.path.name)
        return failures

    def init(self) -> None:
        self._assert_no_symlink_ancestry(self.path)
        existing = self._target_kind()
        self._prepare_parent()
        if existing == "regular":
            # No DDL, chmod, or normal read/write connection may touch an existing database.
            self._validate_existing_plugin()
            return
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except FileExistsError:
            self._validate_existing_plugin()
            return
        else:
            opened = os.fstat(descriptor)
            created_identity = (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_uid)
            os.close(descriptor)
        try:
            descriptor, held_identity = _open_rw_nofollow(self.path)
        except BaseException as original:
            failures = self._cleanup_new_database(created_identity)
            if failures:
                raise RuntimeError("delivery database initialization cleanup was incomplete") from original
            raise
        if created_identity != held_identity:
            os.close(descriptor)
            failures = self._cleanup_new_database(created_identity)
            if failures:
                raise RuntimeError("delivery database initialization cleanup was incomplete")
            raise ValueError("delivery database changed during initialization")
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(self._rw_uri(self.path), uri=True, timeout=10, isolation_level=None)
            opened = os.fstat(descriptor)
            if created_identity != _secure_identity(self.path, directory=False) or created_identity != (
                opened.st_dev, opened.st_ino, opened.st_mode, opened.st_uid,
            ):
                raise ValueError("delivery database changed during initialization")
            conn.execute("pragma foreign_keys = on")
            conn.execute("begin immediate")
            for statement in _DDL:
                conn.execute(statement)
            conn.execute("insert into delivery_schema_version(singleton, version) values(1, ?)", (SCHEMA_VERSION,))
            self._validate_plugin_connection(conn)
            conn.execute("commit")
        except BaseException as original:
            rollback_error: BaseException | None = None
            try:
                if conn is not None and conn.in_transaction:
                    conn.execute("rollback")
            except BaseException as exc:
                rollback_error = exc
            close_error: BaseException | None = None
            try:
                if conn is not None:
                    conn.close()
            except BaseException as exc:
                close_error = exc
            finally:
                os.close(descriptor)
            failures = self._cleanup_new_database(created_identity)
            if rollback_error is not None or close_error is not None or failures:
                detail = "rollback failed" if rollback_error is not None else "connection close failed" if close_error else "cleanup incomplete"
                if failures:
                    detail += ": " + ", ".join(sorted(failures))
                raise RuntimeError(f"delivery database initialization failed and {detail}") from original
            raise
        else:
            assert conn is not None
            try:
                conn.close()
            finally:
                os.close(descriptor)
        if os.name != "nt":
            os.chmod(self.path, 0o600)

    def _normalize_transaction(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("delivery transaction payload must be a mapping")
        key = payload.get("transaction_key")
        if not isinstance(key, str) or not key:
            raise ValueError("transaction_key is required")
        timestamp = _now_iso()
        record: dict[str, Any] = {
            "transaction_key": key, "task_id": payload.get("task_id"), "source_run_id": payload.get("source_run_id"),
            "entity_kind": payload.get("entity_kind", ""), "entity_id": payload.get("entity_id", ""),
            "project_path": payload.get("project_path", ""), "state": payload.get("state", "planned"),
            "plan_hash": payload.get("plan_hash", ""), "output_dir": payload.get("output_dir", ""),
            "journal_path": payload.get("journal_path", ""), "last_error": payload.get("last_error", ""),
            "created_at": payload.get("created_at") or timestamp, "updated_at": payload.get("updated_at") or timestamp,
        }
        for field in _JSON_OBJECT_FIELDS:
            record[field] = _json_dump(payload.get(field, {}), field)
        for field in _JSON_LIST_FIELDS:
            record[field] = _json_dump(payload.get(field, []), field)
        for field in ("entity_kind", "entity_id", "project_path", "state", "plan_hash", "output_dir", "journal_path", "last_error", "created_at", "updated_at"):
            if not isinstance(record[field], str):
                raise ValueError(f"{field} must be a string")
        for field in ("task_id", "source_run_id"):
            if record[field] is not None and (not isinstance(record[field], int) or isinstance(record[field], bool)):
                raise ValueError(f"{field} must be an integer or null")
        return record

    @staticmethod
    def _decode_transaction(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for field in _JSON_FIELDS:
            item[field] = _json_load(item[field], field)
        return copy.deepcopy(item)

    @staticmethod
    def _decode_event(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["details"] = _json_load(item["details"], "details")
        return copy.deepcopy(item)

    def add_transaction(self, payload: Mapping[str, Any]) -> int:
        record = self._normalize_transaction(payload)
        with self._connection() as conn:
            try:
                cursor = conn.execute(
                    """insert into delivery_transactions(
                        transaction_key, task_id, source_run_id, entity_kind, entity_id, project_path, state, plan_hash,
                        policy_snapshot, repository_snapshot, release_acceptance, rc_acceptance, commit_records,
                        remote_results, parity_result, output_dir, journal_path, last_error, created_at, updated_at
                    ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    tuple(record[field] for field in _TRANSACTION_FIELDS),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("transaction_key already exists") from exc
            return int(cursor.lastrowid)

    def get_transaction(self, transaction_id: int) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("select * from delivery_transactions where id = ?", (transaction_id,)).fetchone()
        return self._decode_transaction(row) if row else None

    def get_by_key(self, transaction_key: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("select * from delivery_transactions where transaction_key = ?", (transaction_key,)).fetchone()
        return self._decode_transaction(row) if row else None

    def update_transaction(self, transaction_id: int, **fields: Any) -> None:
        unknown = sorted(set(fields) - _UPDATE_FIELDS)
        if unknown:
            raise ValueError("不支持更新 delivery transaction 字段：" + ", ".join(unknown))
        normalized = {field: _json_dump(value, field) if field in _JSON_FIELDS else value for field, value in fields.items()}
        if any(field not in _JSON_FIELDS and not isinstance(value, str) for field, value in fields.items()):
            raise ValueError("delivery transaction text fields must be strings")
        with self._connection() as conn:
            conn.execute("begin immediate")
            try:
                current = conn.execute("select * from delivery_transactions where id = ?", (transaction_id,)).fetchone()
                if current is None:
                    raise ValueError("delivery transaction 不存在")
                if fields:
                    values = tuple(normalized.get(field, current[field]) for field in _UPDATE_ORDER)
                    conn.execute(
                        """update delivery_transactions set
                            commit_records=?, journal_path=?, last_error=?, output_dir=?, parity_result=?, plan_hash=?,
                            policy_snapshot=?, rc_acceptance=?, release_acceptance=?, remote_results=?,
                            repository_snapshot=?, state=?, updated_at=? where id=?""",
                        (*values, _now_iso(), transaction_id),
                    )
                conn.execute("commit")
            except Exception:
                if conn.in_transaction:
                    conn.execute("rollback")
                raise

    def add_event(self, payload: Mapping[str, Any]) -> int:
        if not isinstance(payload, Mapping):
            raise ValueError("delivery event payload must be a mapping")
        transaction_id = payload.get("transaction_id")
        if not isinstance(transaction_id, int) or isinstance(transaction_id, bool) or transaction_id <= 0:
            raise ValueError("delivery event 缺少 transaction_id")
        text = tuple(payload.get(field, "") for field in ("event_type", "status", "input_hash"))
        if not all(isinstance(value, str) for value in text):
            raise ValueError("delivery event text fields must be strings")
        details = _json_dump(payload.get("details", {}), "details")
        created_at = payload.get("created_at") or _now_iso()
        if not isinstance(created_at, str):
            raise ValueError("created_at must be a string")
        with self._connection() as conn:
            conn.execute("begin immediate")
            try:
                if conn.execute("select id from delivery_transactions where id = ?", (transaction_id,)).fetchone() is None:
                    raise ValueError("delivery event transaction_id 不存在")
                sequence = int(conn.execute(
                    "select coalesce(max(sequence), 0) + 1 from delivery_events where transaction_id = ?", (transaction_id,)
                ).fetchone()[0])
                cursor = conn.execute(
                    """insert into delivery_events(transaction_id, sequence, event_type, status, input_hash, details, created_at)
                    values(?, ?, ?, ?, ?, ?, ?)""",
                    (transaction_id, sequence, *text, details, created_at),
                )
                conn.execute("commit")
                return int(cursor.lastrowid)
            except Exception:
                if conn.in_transaction:
                    conn.execute("rollback")
                raise

    def get_events(self, transaction_id: int) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute("select * from delivery_events where transaction_id = ? order by sequence", (transaction_id,)).fetchall()
        return [self._decode_event(row) for row in rows]

    def _legacy_snapshot(self, source: Path) -> tuple[Path, tuple[int, int, int, int, int, int, str], set[str], tempfile.TemporaryDirectory[str]]:
        self._assert_no_symlink_ancestry(source)
        if source.is_symlink() or not source.is_file():
            raise ValueError("legacy database source must be a regular non-symlink file")
        sidecars = {suffix for suffix in _SIDECAR_SUFFIXES if source.with_name(source.name + suffix).exists()}
        if sidecars:
            raise ValueError("legacy database sidecar is present")
        before = _metadata(source)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(source, flags)
        temporary = tempfile.TemporaryDirectory(prefix="his-engineering-legacy-")
        directory = Path(temporary.name)
        if os.name != "nt":
            os.chmod(directory, 0o700)
        snapshot = directory / "legacy.sqlite"
        snapshot_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            snapshot_flags |= os.O_NOFOLLOW
        output = os.open(snapshot, snapshot_flags, 0o600)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != before[:2]:
                raise ValueError("legacy database changed during snapshot")
            while True:
                block = os.read(descriptor, 1024 * 1024)
                if not block:
                    break
                _write_all(output, block)
        finally:
            os.close(descriptor)
            os.close(output)
        if os.name != "nt":
            os.chmod(snapshot, 0o600)
        after = _metadata(source)
        after_sidecars = {suffix for suffix in _SIDECAR_SUFFIXES if source.with_name(source.name + suffix).exists()}
        if before != after or sidecars != after_sidecars:
            temporary.cleanup()
            raise ValueError("legacy database changed during snapshot")
        return snapshot, before, sidecars, temporary

    @staticmethod
    def _validate_legacy_rows(transactions: Sequence[dict[str, Any]], events: Sequence[dict[str, Any]]) -> None:
        ids: set[int] = set()
        keys: set[str] = set()
        for item in transactions:
            if not isinstance(item["id"], int) or isinstance(item["id"], bool) or item["id"] <= 0 or item["id"] in ids:
                raise ValueError("legacy transaction id is invalid")
            if not isinstance(item["transaction_key"], str) or not item["transaction_key"] or item["transaction_key"] in keys:
                raise ValueError("legacy transaction key is invalid")
            for field in ("task_id", "source_run_id"):
                if item[field] is not None and (not isinstance(item[field], int) or isinstance(item[field], bool) or item[field] <= 0):
                    raise ValueError("legacy transaction reference is invalid")
            for field in ("entity_kind", "entity_id", "project_path", "state", "plan_hash", "output_dir", "journal_path", "last_error", "created_at", "updated_at"):
                if not isinstance(item[field], str):
                    raise ValueError("legacy transaction text is invalid")
            ids.add(item["id"])
            keys.add(item["transaction_key"])
        event_ids: set[int] = set()
        sequences: dict[int, list[int]] = {}
        for item in events:
            if not isinstance(item["id"], int) or isinstance(item["id"], bool) or item["id"] <= 0 or item["id"] in event_ids:
                raise ValueError("legacy event id is invalid")
            if not isinstance(item["transaction_id"], int) or isinstance(item["transaction_id"], bool) or item["transaction_id"] <= 0 or item["transaction_id"] not in ids:
                raise ValueError("legacy event foreign key is invalid")
            if not isinstance(item["sequence"], int) or isinstance(item["sequence"], bool) or item["sequence"] <= 0:
                raise ValueError("legacy event sequence is invalid")
            if any(not isinstance(item[field], str) for field in ("event_type", "status", "input_hash", "created_at")):
                raise ValueError("legacy event text is invalid")
            event_ids.add(item["id"])
            sequences.setdefault(item["transaction_id"], []).append(item["sequence"])
        if any(values != sorted(values) or len(values) != len(set(values)) for values in sequences.values()):
            raise ValueError("legacy event sequence is not strictly increasing")

    def import_legacy_db(self, legacy_path: str | os.PathLike[str]) -> dict[str, int]:
        source = Path(legacy_path)
        if not source.is_absolute():
            raise ValueError("legacy database path must be absolute")
        _reject_noncanonical_symlink_components(source)
        source = source.resolve(strict=False)
        self._assert_no_symlink_ancestry(source)
        if self._target_kind() == "regular" and os.path.samefile(source, self.path):
            raise ValueError("legacy source and destination are the same file")
        snapshot, source_before, source_sidecars, temporary = self._legacy_snapshot(source)
        try:
            legacy = sqlite3.connect(self._immutable_uri(snapshot), uri=True)
            legacy.row_factory = sqlite3.Row
            try:
                user_version = int(legacy.execute("pragma user_version").fetchone()[0])
                if user_version < 0 or user_version > LEGACY_MAX_SCHEMA_VERSION:
                    raise ValueError("legacy schema version is unsupported")
                if not _LEGACY_TABLES.issubset(_table_names(legacy)):
                    raise ValueError("legacy database is incompatible")
                for name, expected in _LEGACY_CONTRACTS.items():
                    if _table_info(legacy, _LEGACY_TABLE_INFO_SQL[name]) != expected:
                        raise ValueError("legacy database is incompatible")
                transactions = [self._decode_transaction(row) for row in legacy.execute(
                    "select * from harness_delivery_transactions order by id"
                ).fetchall()]
                events = [self._decode_event(row) for row in legacy.execute(
                    "select * from harness_delivery_events order by transaction_id, sequence"
                ).fetchall()]
            except sqlite3.Error as exc:
                raise ValueError("legacy database is incompatible") from exc
            finally:
                legacy.close()
            self._validate_legacy_rows(transactions, events)
            if not _source_unchanged(source, source_before, source_sidecars):
                raise ValueError("legacy database changed during import")
            imported_transactions = 0
            imported_events = 0
            with self._connection() as conn:
                conn.execute("begin immediate")
                try:
                    for record in transactions:
                        existing = conn.execute("select * from delivery_transactions where transaction_key = ?", (record["transaction_key"],)).fetchone()
                        if existing is not None:
                            if self._decode_transaction(existing) != record:
                                raise ValueError("legacy transaction conflicts with destination")
                            continue
                        if conn.execute("select id from delivery_transactions where id = ?", (record["id"],)).fetchone() is not None:
                            raise ValueError("legacy transaction id conflicts with destination")
                        values = (record["id"], *(record[field] if field not in _JSON_FIELDS else _json_dump(record[field], field) for field in _TRANSACTION_FIELDS))
                        conn.execute(
                            """insert into delivery_transactions(
                                id, transaction_key, task_id, source_run_id, entity_kind, entity_id, project_path, state,
                                plan_hash, policy_snapshot, repository_snapshot, release_acceptance, rc_acceptance,
                                commit_records, remote_results, parity_result, output_dir, journal_path, last_error,
                                created_at, updated_at
                            ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values,
                        )
                        imported_transactions += 1
                    for event in events:
                        existing = conn.execute("select * from delivery_events where id = ?", (event["id"],)).fetchone()
                        if existing is not None:
                            if self._decode_event(existing) != event:
                                raise ValueError("legacy event conflicts with destination")
                            continue
                        if conn.execute("select id from delivery_events where transaction_id = ? and sequence = ?", (event["transaction_id"], event["sequence"])).fetchone() is not None:
                            raise ValueError("legacy event sequence conflicts with destination")
                        conn.execute(
                            """insert into delivery_events(id, transaction_id, sequence, event_type, status, input_hash, details, created_at)
                            values(?, ?, ?, ?, ?, ?, ?, ?)""",
                            (event["id"], event["transaction_id"], event["sequence"], event["event_type"], event["status"], event["input_hash"], _json_dump(event["details"], "details"), event["created_at"]),
                        )
                        imported_events += 1
                    if not _source_unchanged(source, source_before, source_sidecars):
                        raise ValueError("legacy database changed during import")
                    conn.execute("commit")
                except Exception:
                    if conn.in_transaction:
                        conn.execute("rollback")
                    raise
            return {"transactions": imported_transactions, "events": imported_events}
        finally:
            temporary.cleanup()
