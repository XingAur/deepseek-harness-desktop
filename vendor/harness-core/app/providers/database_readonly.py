"""Narrow, permanently read-only database inspection adapter.

This module intentionally supports only a local SQLite read-only URI in this
stage.  It is not a generic DB-API bridge: there is no SQL script runner,
parameter forwarding, transaction method, or view executor.  Other database
dialects must be added as separately reviewed, explicit factories.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.database_read_policy import ReadonlySqlValidation, validate_readonly_sql
from app.provider_execution import ProviderExecutionContext, ProviderExecutionRequest
from app.sensitive_text import contains_sensitive_text, is_sensitive_mapping_key, redact_sensitive_text


DATABASE_READONLY_MAX_TIMEOUT_SECONDS = 10
DATABASE_READONLY_ROW_LIMIT = 100
DATABASE_READONLY_COLUMN_LIMIT = 64
DATABASE_READONLY_RESPONSE_BYTES = 48 * 1024
DATABASE_READONLY_CELL_BYTES = 4 * 1024
DATABASE_SCHEMA_OBJECT_LIMIT = 64
_DATABASE_ALIAS = re.compile(r"db-[a-z0-9][a-z0-9._-]{0,123}\Z")
_VIEW_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")
DATABASE_PROFILE_BOUND_ACTIONS = frozenset(
    {
        "database.connection_test",
        "database.schema.read",
        "database.query.read",
    }
)
_SQLITE_READONLY_ACTIONS = tuple(
    name
    for name in (
        "SQLITE_INSERT",
        "SQLITE_UPDATE",
        "SQLITE_DELETE",
        "SQLITE_CREATE_INDEX",
        "SQLITE_CREATE_TABLE",
        "SQLITE_CREATE_TEMP_INDEX",
        "SQLITE_CREATE_TEMP_TABLE",
        "SQLITE_CREATE_TEMP_TRIGGER",
        "SQLITE_CREATE_TEMP_VIEW",
        "SQLITE_CREATE_TRIGGER",
        "SQLITE_CREATE_VIEW",
        "SQLITE_DROP_INDEX",
        "SQLITE_DROP_TABLE",
        "SQLITE_DROP_TEMP_INDEX",
        "SQLITE_DROP_TEMP_TABLE",
        "SQLITE_DROP_TEMP_TRIGGER",
        "SQLITE_DROP_TEMP_VIEW",
        "SQLITE_DROP_TRIGGER",
        "SQLITE_DROP_VIEW",
        "SQLITE_ALTER_TABLE",
        "SQLITE_REINDEX",
        "SQLITE_ANALYZE",
        "SQLITE_PRAGMA",
        "SQLITE_ATTACH",
        "SQLITE_DETACH",
        "SQLITE_TRANSACTION",
        "SQLITE_SAVEPOINT",
    )
    if hasattr(sqlite3, name)
)
_SENSITIVE_RESULT_COLUMN = re.compile(
    r"(?:password|passwd|secret|token|credential|phone|mobile|idcard|identity|email|address)",
    re.IGNORECASE,
)


class _DbApiCursor(Protocol):
    description: object

    def fetchmany(self, size: int = ...) -> list[object]: ...


class _DbApiConnection(Protocol):
    def execute(self, sql: str, parameters: object = ...) -> _DbApiCursor: ...

    def close(self) -> None: ...


DriverFactory = Callable[["DatabaseReadonlyProfile", str, int], _DbApiConnection]
ProfileLoader = Callable[[int], Mapping[str, object]]


@dataclass(frozen=True)
class DatabaseReadonlyProfile:
    """Validated connection identity; no credential value is part of this type."""

    driver: str
    host: str
    port: int
    database: str
    schema: str
    username: str
    readonly_policy: str

    @classmethod
    def from_connection(cls, connection: Mapping[str, object]) -> "DatabaseReadonlyProfile":
        if not isinstance(connection, Mapping):
            raise ValueError("database_profile_connection_invalid")
        expected = {
            "driver",
            "host",
            "port",
            "database",
            "schema",
            "username",
            "readonly_policy",
        }
        if set(connection) != expected:
            raise ValueError("database_profile_fields_invalid")
        values = {field: _required_text(connection.get(field), field) for field in expected}
        if values["driver"] != "sqlite":
            raise ValueError("database_driver_not_supported")
        if values["host"] != "local" or values["port"] != "0":
            raise ValueError("database_sqlite_endpoint_invalid")
        if values["schema"] != "main":
            raise ValueError("database_sqlite_schema_invalid")
        if values["readonly_policy"] != "required":
            raise ValueError("database_readonly_policy_required")
        database_path = _sqlite_database_path(values["database"])
        return cls(
            driver="sqlite",
            host="local",
            port=0,
            database=str(database_path),
            schema="main",
            username=values["username"],
            readonly_policy="required",
        )


def canonical_database_target(profile_key: object) -> str:
    """Return the sole reviewed target name allowed for one database profile."""

    if not isinstance(profile_key, str) or profile_key != profile_key.strip() or not profile_key:
        raise ValueError("database_target_invalid")
    target_alias = f"db-{profile_key}"
    if _DATABASE_ALIAS.fullmatch(target_alias) is None or contains_sensitive_text(target_alias):
        raise ValueError("database_target_invalid")
    return target_alias


class DatabaseReadonlyProviderAdapter:
    """Authorization-bound schema/query reader with no database write surface."""

    def __init__(
        self,
        *,
        profile_loader: ProfileLoader | None = None,
        driver_factories: Mapping[str, DriverFactory] | None = None,
    ) -> None:
        supplied = {"sqlite": _sqlite_readonly_connect} if driver_factories is None else dict(driver_factories)
        if set(supplied) != {"sqlite"} or not callable(supplied.get("sqlite")):
            raise ValueError("database_driver_factory_invalid")
        self._profile_loader = profile_loader or _load_manager_profile
        self._driver_factories = supplied

    def normalize_target_alias(self, value: object) -> str:
        if (
            not isinstance(value, str)
            or value != value.strip()
            or _DATABASE_ALIAS.fullmatch(value) is None
            or contains_sensitive_text(value)
        ):
            raise ValueError("database_target_alias_invalid")
        return value

    def normalize_request_target(self, parameters: Mapping[str, object]) -> str:
        if not isinstance(parameters, Mapping):
            raise ValueError("database_parameters_invalid")
        return self.normalize_target_alias(parameters.get("database_alias"))

    def render_plan(self, request: ProviderExecutionRequest) -> dict[str, object]:
        action, _profile, values = self._validated_request(request.action, request.parameters, profile_id=None)
        change: dict[str, object] = {"field": "read", "after": "no_database_change"}
        if action == "database.query.read":
            change["sql_sha256"] = _sql_hash(str(values["sql"]))
            change["statement_kind"] = str(values["statement_kind"])
        return {
            "provider": "database",
            "action": action,
            "target_alias": str(values["database_alias"]),
            "change": change,
        }

    def validate_profile_binding(self, *, profile_id: int, target_alias: object) -> str:
        """Bind a reviewed alias to the current profile before authorization use."""

        record = self._profile_loader(profile_id)
        if not isinstance(record, Mapping) or record.get("provider") != "database":
            raise ValueError("database_profile_invalid")
        if record.get("enabled") is not True:
            raise ValueError("database_profile_disabled")
        canonical_target = canonical_database_target(record.get("profile_key"))
        if self.normalize_target_alias(target_alias) != canonical_target:
            raise ValueError("database_target_invalid")
        return canonical_target

    def execute(
        self, request: ProviderExecutionRequest, context: ProviderExecutionContext
    ) -> Mapping[str, object]:
        action, profile, values = self._validated_request(
            request.action, request.parameters, profile_id=context.profile_id
        )
        # SQL and profile policy are fully validated above.  Password material is
        # therefore not requested for rejected statements or malformed profiles.
        password = context.credential("password")
        connection = self._driver_factories[profile.driver](
            profile, password, int(values["timeout_seconds"])
        )
        if not hasattr(connection, "execute") or not callable(connection.execute):
            raise RuntimeError("database_driver_connection_invalid")
        started = time.monotonic()
        try:
            _configure_readonly_connection(connection, timeout_seconds=int(values["timeout_seconds"]))
            if action == "database.connection_test":
                return _connection_summary(connection, started)
            if action == "database.schema.read":
                return _schema_summary(connection, started)
            return _query_result(
                connection,
                sql=str(values["sql"]),
                validation=values["validation"],
                started=started,
            )
        finally:
            connection.close()

    def verify(
        self,
        _verifier_action: str,
        _original_write_action: str,
        _request: ProviderExecutionRequest,
        _target_alias: str,
        _context: ProviderExecutionContext,
    ) -> str:
        return "unknown"

    def draft_view_sql(
        self,
        *,
        database_alias: object,
        view_name: object,
        select_sql: object,
    ) -> dict[str, object]:
        """Return a manual-only view draft; it is intentionally not executable."""

        alias = self.normalize_target_alias(database_alias)
        if not isinstance(view_name, str) or _VIEW_NAME.fullmatch(view_name) is None:
            raise ValueError("database_view_name_invalid")
        validation = validate_readonly_sql(select_sql if isinstance(select_sql, str) else "")
        sql = f"CREATE VIEW {view_name} AS {select_sql.strip()}"
        return {
            "provider": "database",
            "action": "database.view_sql.draft",
            "target_alias": alias,
            "manual_execution_required": True,
            "statement_kind": validation.statement_kind,
            "sql": sql,
            "execution_allowed": False,
        }

    def _validated_request(
        self,
        action: object,
        parameters: Mapping[str, object],
        *,
        profile_id: int | None,
    ) -> tuple[str, DatabaseReadonlyProfile, dict[str, object]]:
        if not isinstance(action, str) or action not in DATABASE_PROFILE_BOUND_ACTIONS:
            raise ValueError("database_action_not_allowed")
        if not isinstance(parameters, Mapping):
            raise ValueError("database_parameters_invalid")
        allowed = {"database_alias", "timeout_seconds"}
        if action == "database.query.read":
            allowed.add("sql")
        if set(parameters) - allowed or "database_alias" not in parameters:
            raise ValueError("database_parameters_invalid")
        if action == "database.query.read" and "sql" not in parameters:
            raise ValueError("database_parameters_invalid")
        alias = self.normalize_target_alias(parameters["database_alias"])
        timeout = _timeout_seconds(parameters.get("timeout_seconds", DATABASE_READONLY_MAX_TIMEOUT_SECONDS))
        profile, canonical_target = _profile_from_record(
            self._profile_loader(profile_id) if profile_id is not None else {
                "provider": "database",
                "connection": _render_plan_connection_placeholder(),
            },
            allow_render_placeholder=profile_id is None,
        )
        if profile_id is not None and alias != canonical_target:
            raise ValueError("database_target_invalid")
        values: dict[str, object] = {
            "database_alias": alias,
            "timeout_seconds": timeout,
        }
        if action == "database.query.read":
            sql = parameters["sql"]
            validation = validate_readonly_sql(sql if isinstance(sql, str) else "")
            values.update(sql=sql.strip(), statement_kind=validation.statement_kind, validation=validation)
        return action, profile, values


def _render_plan_connection_placeholder() -> dict[str, str]:
    """Keep render_plan pure; runtime profile verification happens before execute."""

    return {
        "driver": "sqlite",
        "host": "local",
        "port": "0",
        "database": "/tmp/harness-render-plan.sqlite",
        "schema": "main",
        "username": "readonly",
        "readonly_policy": "required",
    }


def _profile_from_record(
    record: Mapping[str, object], *, allow_render_placeholder: bool = False
) -> tuple[DatabaseReadonlyProfile, str]:
    if not isinstance(record, Mapping) or record.get("provider") not in {None, "database"}:
        raise ValueError("database_profile_invalid")
    connection = record.get("connection")
    if not isinstance(connection, Mapping):
        raise ValueError("database_profile_invalid")
    if allow_render_placeholder:
        # The synthetic value is never connected to; it exists only so a query
        # plan can be rendered without reading a profile or credential.
        return (
            DatabaseReadonlyProfile(
                driver="sqlite", host="local", port=0,
                database="/tmp/harness-render-plan.sqlite", schema="main",
                username="readonly", readonly_policy="required",
            ),
            "db-render-plan",
        )
    if record.get("enabled") is not True:
        raise ValueError("database_profile_disabled")
    return (
        DatabaseReadonlyProfile.from_connection(connection),
        canonical_database_target(record.get("profile_key")),
    )


def _load_manager_profile(profile_id: int) -> Mapping[str, object]:
    from app.manager_provider_repository import ManagerProviderRepository

    if not isinstance(profile_id, int) or isinstance(profile_id, bool) or profile_id < 1:
        raise ValueError("database_profile_invalid")
    record = ManagerProviderRepository().profile_status(profile_id)
    return {
        "provider": "database",
        "profile_key": record.get("profile_key"),
        "enabled": record.get("enabled"),
        "connection": record.get("connection"),
    }


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or value != value.strip() or not value:
        raise ValueError(f"database_profile_{name}_invalid")
    if len(value.encode("utf-8")) > 4096 or contains_sensitive_text(value):
        raise ValueError(f"database_profile_{name}_invalid")
    return value


def _sqlite_database_path(value: str) -> Path:
    if value.startswith("file:") or value == ":memory:" or "\x00" in value:
        raise ValueError("database_sqlite_path_invalid")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("database_sqlite_path_invalid")
    # Resolve would replace a leaf symlink with its target and make a later
    # is_symlink() check meaningless.  Reject the submitted identity first.
    if path.is_symlink():
        raise ValueError("database_sqlite_path_invalid")
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        raise ValueError("database_sqlite_path_invalid") from None
    if not resolved.is_file():
        raise ValueError("database_sqlite_path_invalid")
    return resolved


def _timeout_seconds(value: object) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= DATABASE_READONLY_MAX_TIMEOUT_SECONDS
    ):
        raise ValueError("database_timeout_invalid")
    return value


def _sqlite_readonly_connect(
    profile: DatabaseReadonlyProfile, _password: str, timeout_seconds: int
) -> sqlite3.Connection:
    path = _sqlite_database_path(profile.database)
    # mode=ro is the primary no-write guarantee.  isolation_level=None avoids
    # an implicit transaction even if a future caller accidentally requests one.
    connection = sqlite3.connect(
        path.as_uri() + "?mode=ro",
        uri=True,
        timeout=float(timeout_seconds),
        isolation_level=None,
        check_same_thread=False,
    )
    return connection


def _configure_readonly_connection(connection: _DbApiConnection, *, timeout_seconds: int) -> None:
    if isinstance(connection, sqlite3.Connection):
        denied = {getattr(sqlite3, name) for name in _SQLITE_READONLY_ACTIONS}

        def authorizer(action_code: int, _arg1: object, _arg2: object, _database: object, _source: object) -> int:
            return sqlite3.SQLITE_DENY if action_code in denied else sqlite3.SQLITE_OK

        connection.set_authorizer(authorizer)
        deadline = time.monotonic() + timeout_seconds
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1_000)
        if hasattr(connection, "setlimit") and hasattr(sqlite3, "SQLITE_LIMIT_LENGTH"):
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, DATABASE_READONLY_CELL_BYTES)


def _schema_summary(connection: _DbApiConnection, started: float) -> dict[str, object]:
    cursor = connection.execute(
        "SELECT type, name FROM sqlite_master "
        "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY type, name LIMIT ?",
        (DATABASE_SCHEMA_OBJECT_LIMIT + 1,),
    )
    rows = cursor.fetchmany(DATABASE_SCHEMA_OBJECT_LIMIT + 1)
    truncated = len(rows) > DATABASE_SCHEMA_OBJECT_LIMIT
    objects = []
    for row in rows[:DATABASE_SCHEMA_OBJECT_LIMIT]:
        values = tuple(row)  # sqlite Row and DB-API tuples both support this.
        if len(values) != 2:
            raise RuntimeError("database_schema_result_invalid")
        kind, name = values
        if kind not in {"table", "view"} or not isinstance(name, str):
            raise RuntimeError("database_schema_result_invalid")
        objects.append({"kind": kind, "name": _safe_identifier(name)})
    return {
        "schema": {
            "object_count": len(objects),
            "objects": objects,
            "truncated": truncated,
        },
        "elapsed_ms": _elapsed_ms(started),
    }


def _connection_summary(connection: _DbApiConnection, started: float) -> dict[str, object]:
    cursor = connection.execute("SELECT 1")
    row = cursor.fetchmany(1)
    if len(row) != 1 or tuple(row[0]) != (1,):
        raise RuntimeError("database_connection_test_invalid")
    return {
        "connection": "readonly_verified",
        "read_only": True,
        "elapsed_ms": _elapsed_ms(started),
    }


def _query_result(
    connection: _DbApiConnection,
    *,
    sql: str,
    validation: object,
    started: float,
) -> dict[str, object]:
    if not isinstance(validation, ReadonlySqlValidation):
        raise RuntimeError("database_sql_validation_missing")
    cursor = connection.execute(sql)
    description = getattr(cursor, "description", None)
    if not isinstance(description, (list, tuple)) or not description:
        raise RuntimeError("database_query_result_invalid")
    if len(description) > DATABASE_READONLY_COLUMN_LIMIT:
        raise RuntimeError("database_query_column_limit_exceeded")
    columns, sensitive_columns = _columns(description)
    rows = cursor.fetchmany(DATABASE_READONLY_ROW_LIMIT + 1)
    truncated = len(rows) > DATABASE_READONLY_ROW_LIMIT
    rendered_rows: list[list[object]] = []
    for row in rows[:DATABASE_READONLY_ROW_LIMIT]:
        values = tuple(row)
        if len(values) != len(columns):
            raise RuntimeError("database_query_result_invalid")
        rendered_rows.append(
            [
                "[REDACTED]" if sensitive else _safe_cell(value)
                for value, sensitive in zip(values, sensitive_columns)
            ]
        )
        if _json_bytes({"columns": columns, "rows": rendered_rows}) > DATABASE_READONLY_RESPONSE_BYTES:
            rendered_rows.pop()
            truncated = True
            break
    local_response = {
        "columns": columns,
        "rows": rendered_rows,
        "row_count": len(rendered_rows),
        "truncated": truncated,
    }
    if _json_bytes(local_response) > DATABASE_READONLY_RESPONSE_BYTES:
        raise RuntimeError("database_query_response_limit_exceeded")
    return {
        "sql_sha256": _sql_hash(sql),
        "statement_kind": validation.statement_kind,
        "row_count": len(rendered_rows),
        "column_count": len(columns),
        "truncated": truncated,
        "elapsed_ms": _elapsed_ms(started),
        "result_schema": {"columns": columns},
        "__local_response__": local_response,
    }


def _columns(description: list[object] | tuple[object, ...]) -> tuple[list[str], list[bool]]:
    columns: list[str] = []
    sensitive: list[bool] = []
    for item in description:
        name = item[0] if isinstance(item, (list, tuple)) and item else None
        if not isinstance(name, str) or not name or len(name.encode("utf-8")) > 128:
            raise RuntimeError("database_query_column_invalid")
        is_sensitive = is_sensitive_mapping_key(name) or bool(_SENSITIVE_RESULT_COLUMN.search(name))
        columns.append("[REDACTED_COLUMN]" if is_sensitive else _safe_identifier(name))
        sensitive.append(is_sensitive)
    return columns, sensitive


def _safe_identifier(value: str) -> str:
    if len(value.encode("utf-8")) > 128:
        return "[REDACTED_IDENTIFIER]"
    return redact_sensitive_text(value)


def _safe_cell(value: object) -> object:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "[REDACTED_NON_FINITE_NUMBER]"
    if isinstance(value, str):
        encoded = value.encode("utf-8", "replace")
        if len(encoded) > DATABASE_READONLY_CELL_BYTES:
            return "[REDACTED_TEXT_TRUNCATED_sha256:" + hashlib.sha256(encoded).hexdigest() + "]"
        return redact_sensitive_text(value)
    return "[REDACTED_UNSUPPORTED_VALUE]"


def _json_bytes(value: Mapping[str, object]) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def _sql_hash(sql: str) -> str:
    return "sha256:" + hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _elapsed_ms(started: float) -> int:
    return min(60_000, max(0, int((time.monotonic() - started) * 1_000)))
