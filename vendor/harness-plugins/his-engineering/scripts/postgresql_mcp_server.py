"""Catalog-only PostgreSQL MCP server with a forced readonly transaction."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Optional
from urllib.parse import parse_qsl, unquote, urlsplit


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from mcp_readonly_common import (  # noqa: E402
    JsonRpcReadonlyServer,
    content_version,
    fallback_metadata,
    result_envelope,
    safe_metadata,
    sanitize_json,
    tool_result,
    utc_timestamp,
)


SERVER_NAME = "postgresql"
SERVER_VERSION = "1.0.0"
TOOL_NAME = "readonly_inspect"
DEFAULT_CREDENTIALS_FILE = Path("/Users/lym/WorkCode/ai/apiKey/credentials.json")
ROW_LIMIT = 500
_ALIAS = re.compile(r"^[a-z][a-z0-9_-]{0,63}_readonly$")
_IDENTIFIER = re.compile(r"^(?:|[a-z_][a-z0-9_]{0,62})$")
_OPERATIONS = ("schemas", "tables", "columns", "constraints", "indexes", "foreign_keys")
_SENSITIVE_NAME = re.compile(r"(?:password|passwd|secret|token|credential|mobile|phone|idcard|email)", re.IGNORECASE)

INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["connection_alias", "operation", "schema", "table"],
    "properties": {
        "connection_alias": {"type": "string", "pattern": _ALIAS.pattern, "maxLength": 64},
        "operation": {"type": "string", "enum": list(_OPERATIONS)},
        "schema": {"type": "string", "pattern": _IDENTIFIER.pattern, "maxLength": 63},
        "table": {"type": "string", "pattern": _IDENTIFIER.pattern, "maxLength": 63},
    },
}
TOOLS = (
    {
        "name": TOOL_NAME,
        "description": "Inspect bounded PostgreSQL schemas, tables, columns, constraints, indexes, and relationships.",
        "inputSchema": INPUT_SCHEMA,
        "annotations": {
            "title": "PostgreSQL readonly catalog inspection",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    },
)

_CATALOG_QUERIES = {
    "schemas": (
        "SELECT schema_name FROM information_schema.schemata "
        "WHERE LEFT(schema_name, 3) <> 'pg_' AND schema_name <> 'information_schema' "
        "ORDER BY schema_name",
        lambda values: (),
    ),
    "tables": (
        "SELECT table_schema, table_name, table_type FROM information_schema.tables "
        "WHERE (%s = '' OR table_schema = %s) AND LEFT(table_schema, 3) <> 'pg_' "
        "AND table_schema <> 'information_schema' ORDER BY table_schema, table_name",
        lambda values: (values["schema"], values["schema"]),
    ),
    "columns": (
        "SELECT table_schema, table_name, ordinal_position, column_name, data_type, "
        "is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
        lambda values: (values["schema"], values["table"]),
    ),
    "constraints": (
        "SELECT tc.constraint_name, tc.constraint_type, kcu.column_name, kcu.ordinal_position "
        "FROM information_schema.table_constraints tc LEFT JOIN information_schema.key_column_usage kcu "
        "ON tc.constraint_catalog = kcu.constraint_catalog AND tc.constraint_schema = kcu.constraint_schema "
        "AND tc.constraint_name = kcu.constraint_name WHERE tc.table_schema = %s AND tc.table_name = %s "
        "ORDER BY tc.constraint_name, kcu.ordinal_position",
        lambda values: (values["schema"], values["table"]),
    ),
    "indexes": (
        "SELECT schemaname, tablename, indexname, indexdef FROM pg_catalog.pg_indexes "
        "WHERE schemaname = %s AND tablename = %s ORDER BY indexname",
        lambda values: (values["schema"], values["table"]),
    ),
    "foreign_keys": (
        "SELECT tc.constraint_name, kcu.table_schema, kcu.table_name, kcu.column_name, "
        "ccu.table_schema AS foreign_table_schema, ccu.table_name AS foreign_table_name, "
        "ccu.column_name AS foreign_column_name FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu ON tc.constraint_catalog = kcu.constraint_catalog "
        "AND tc.constraint_schema = kcu.constraint_schema AND tc.constraint_name = kcu.constraint_name "
        "JOIN information_schema.constraint_column_usage ccu ON tc.constraint_catalog = ccu.constraint_catalog "
        "AND tc.constraint_schema = ccu.constraint_schema AND tc.constraint_name = ccu.constraint_name "
        "WHERE tc.constraint_type = 'FOREIGN KEY' AND kcu.table_schema = %s AND kcu.table_name = %s "
        "ORDER BY tc.constraint_name, kcu.ordinal_position",
        lambda values: (values["schema"], values["table"]),
    ),
}


class _DatabaseExecutionStageError(RuntimeError):
    """Carry only a stable execution stage while retaining the driver cause."""

    def __init__(self, stage: str, cause: Exception) -> None:
        super().__init__(stage)
        self.stage = stage
        self.cause = cause


def _run_database_stage(stage: str, action: Callable[[], object]) -> object:
    try:
        return action()
    except Exception as exc:
        raise _DatabaseExecutionStageError(stage, exc) from exc


def _credentials_file() -> Path:
    configured = os.environ.get("HARNESS_CREDENTIALS_FILE", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_CREDENTIALS_FILE


def load_credentials(alias: str) -> Mapping[str, object]:
    if _ALIAS.fullmatch(alias) is None:
        raise ValueError("invalid readonly database alias")
    payload = json.loads(_credentials_file().read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("credentials unavailable")
    prefix = f"pg_{alias}"
    dsn = os.environ.get(prefix.upper() + "_DSN") or payload.get(prefix + "_dsn")
    user = os.environ.get(prefix.upper() + "_USER") or payload.get(prefix + "_user")
    password = os.environ.get(prefix.upper() + "_PASSWORD") or payload.get(prefix + "_password")
    if not all(isinstance(value, str) and value for value in (dsn, user, password)):
        raise ValueError("credentials unavailable")
    return {"dsn": dsn, "username": user, "password": password}


def _arguments(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(INPUT_SCHEMA["required"]):
        raise ValueError("invalid arguments")
    checked = {key: value.get(key) for key in INPUT_SCHEMA["required"]}
    if (
        not isinstance(checked["connection_alias"], str)
        or _ALIAS.fullmatch(checked["connection_alias"]) is None
        or checked["operation"] not in _OPERATIONS
        or not isinstance(checked["schema"], str)
        or _IDENTIFIER.fullmatch(checked["schema"]) is None
        or not isinstance(checked["table"], str)
        or _IDENTIFIER.fullmatch(checked["table"]) is None
    ):
        raise ValueError("invalid arguments")
    operation = str(checked["operation"])
    if operation == "schemas" and (checked["schema"] or checked["table"]):
        raise ValueError("invalid arguments")
    if operation == "tables" and checked["table"]:
        raise ValueError("invalid arguments")
    if operation in {"columns", "constraints", "indexes", "foreign_keys"} and not (
        checked["schema"] and checked["table"]
    ):
        raise ValueError("invalid arguments")
    return {key: str(item) for key, item in checked.items()}


def _normalize_postgres_dsn(value: object) -> str:
    dsn = str(value or "").strip()
    jdbc_prefix = "jdbc:postgresql://"
    if dsn.casefold().startswith(jdbc_prefix):
        return "postgresql://" + dsn[len(jdbc_prefix) :]
    return dsn


def _build_postgres_connect_kwargs(value: object) -> dict[str, object]:
    dsn = _normalize_postgres_dsn(value)
    parts = urlsplit(dsn)
    if parts.scheme not in {"postgresql", "postgres"} or not parts.hostname:
        return {"dsn": dsn}
    kwargs: dict[str, object] = {
        "host": parts.hostname,
        "dbname": unquote(parts.path.lstrip("/")),
    }
    if parts.port is not None:
        kwargs["port"] = parts.port
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        if key in {"sslmode", "target_session_attrs", "gssencmode", "channel_binding"}:
            kwargs[key] = item
    return kwargs


def _connect(profile: Mapping[str, object], timeout_seconds: int):
    connect_kwargs = _build_postgres_connect_kwargs(profile.get("dsn", ""))
    dsn = connect_kwargs.pop("dsn", None)
    options = {
        "user": str(profile.get("username", "")),
        "password": str(profile.get("password", "")),
        "connect_timeout": timeout_seconds,
    }
    try:
        import psycopg  # type: ignore

        if dsn is not None:
            return psycopg.connect(str(dsn), **options)
        return psycopg.connect(**connect_kwargs, **options)
    except ImportError:
        import psycopg2  # type: ignore

        if dsn is not None:
            return psycopg2.connect(str(dsn), **options)
        return psycopg2.connect(**connect_kwargs, **options)


def execute_catalog_query(
    *,
    profile: Mapping[str, object],
    operation: str,
    schema: str,
    table: str,
    readonly: bool,
    timeout_seconds: int,
    row_limit: int,
) -> Mapping[str, object]:
    if readonly is not True or operation not in _CATALOG_QUERIES:
        raise PermissionError("database mutation is forbidden")
    statement, parameters_factory = _CATALOG_QUERIES[operation]
    parameters = parameters_factory({"schema": schema, "table": table})
    connection = _run_database_stage(
        "connection_setup", lambda: _connect(profile, timeout_seconds)
    )
    primary_error: Optional[Exception] = None
    try:
        _run_database_stage("readonly_transaction", lambda: setattr(connection, "autocommit", False))
        cursor = _run_database_stage("readonly_transaction", connection.cursor)
        _run_database_stage(
            "readonly_transaction",
            lambda: cursor.execute("SET TRANSACTION READ ONLY"),
        )
        _run_database_stage(
            "timeout_configuration",
            lambda: cursor.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (f"{timeout_seconds * 1000}ms",),
            ),
        )
        if parameters:
            _run_database_stage("catalog_query", lambda: cursor.execute(statement, parameters))
        else:
            _run_database_stage("catalog_query", lambda: cursor.execute(statement))
        description = _run_database_stage("catalog_metadata", lambda: cursor.description)
        columns = [str(item[0]) for item in (description or ())]
        fetched = list(
            _run_database_stage("catalog_fetch", lambda: cursor.fetchmany(row_limit + 1))
        )
        rows = [list(row) for row in fetched[:row_limit]]
        _run_database_stage("transaction_rollback", connection.rollback)
        return {"columns": columns, "rows": rows, "truncated": len(fetched) > row_limit}
    except Exception as exc:
        primary_error = exc
        raise
    finally:
        try:
            connection.close()
        except Exception as exc:
            if primary_error is None:
                raise _DatabaseExecutionStageError("connection_close", exc) from exc


def _database_error(error: Exception) -> tuple[str, bool]:
    """Map driver failures to stable non-sensitive operational categories."""

    if isinstance(error, _DatabaseExecutionStageError):
        nested_code, nested_retryable = _database_error(error.cause)
        if nested_code in {
            "DATABASE_AUTHENTICATION_FAILED",
            "DATABASE_CONNECTION_FAILED",
            "DATABASE_CONNECTION_TIMEOUT",
            "DATABASE_CONNECTION_REFUSED",
            "DATABASE_NAME_RESOLUTION_FAILED",
            "DATABASE_NETWORK_UNREACHABLE",
            "DATABASE_DRIVER_UNAVAILABLE",
            "DATABASE_QUERY_TIMEOUT",
            "DATABASE_CATALOG_PERMISSION_DENIED",
            "DATABASE_DATABASE_UNAVAILABLE",
            "DATABASE_CONNECTION_NOT_READY",
            "DATABASE_CONNECTION_LIMIT_REACHED",
            "DATABASE_CONNECTION_OPTION_UNSUPPORTED",
            "DATABASE_CONNECTION_OPTION_INVALID",
            "DATABASE_CONNECTION_DSN_SYNTAX_INVALID",
            "DATABASE_CONNECTION_PORT_INVALID",
        } or nested_code.startswith("DATABASE_CLIENT_"):
            return nested_code, nested_retryable
        if nested_code.startswith("DATABASE_SQLSTATE_"):
            return nested_code, nested_retryable
        return {
            "connection_setup": "DATABASE_CONNECTION_CONFIGURATION_FAILED",
            "readonly_transaction": "DATABASE_READONLY_TRANSACTION_FAILED",
            "timeout_configuration": "DATABASE_TIMEOUT_CONFIGURATION_FAILED",
            "catalog_query": "DATABASE_CATALOG_QUERY_FAILED",
            "catalog_metadata": "DATABASE_CATALOG_METADATA_FAILED",
            "catalog_fetch": "DATABASE_CATALOG_FETCH_FAILED",
            "transaction_rollback": "DATABASE_TRANSACTION_ROLLBACK_FAILED",
            "connection_close": "DATABASE_CONNECTION_CLOSE_FAILED",
        }.get(error.stage, "DATABASE_READ_FAILED"), False
    if isinstance(error, (ImportError, ModuleNotFoundError)):
        return "DATABASE_DRIVER_UNAVAILABLE", False
    sqlstate = getattr(error, "sqlstate", None) or getattr(error, "pgcode", None)
    if isinstance(sqlstate, str):
        if sqlstate.startswith("28"):
            return "DATABASE_AUTHENTICATION_FAILED", False
        if sqlstate == "57014":
            return "DATABASE_QUERY_TIMEOUT", True
        if sqlstate.startswith("08"):
            return "DATABASE_CONNECTION_FAILED", True
        if sqlstate == "42501":
            return "DATABASE_CATALOG_PERMISSION_DENIED", False
        if sqlstate == "42601":
            return "DATABASE_CATALOG_SYNTAX_FAILED", False
        if sqlstate == "42P01":
            return "DATABASE_CATALOG_OBJECT_UNAVAILABLE", False
        if sqlstate == "42703":
            return "DATABASE_CATALOG_COLUMN_UNAVAILABLE", False
        if sqlstate == "3D000":
            return "DATABASE_DATABASE_UNAVAILABLE", False
        if sqlstate == "57P03":
            return "DATABASE_CONNECTION_NOT_READY", True
        if sqlstate == "53300":
            return "DATABASE_CONNECTION_LIMIT_REACHED", True
        if sqlstate == "0A000":
            return "DATABASE_CONNECTION_OPTION_UNSUPPORTED", False
        if sqlstate.startswith("42"):
            normalized = sqlstate if re.fullmatch(r"[0-9A-Z]{5}", sqlstate) else "UNKNOWN"
            return f"DATABASE_CATALOG_SQLSTATE_{normalized}", False
        if re.fullmatch(r"[0-9A-Z]{5}", sqlstate):
            return f"DATABASE_SQLSTATE_{sqlstate}", False
    error_name = type(error).__name__
    error_module = type(error).__module__
    if error_module.startswith(("psycopg", "psycopg2")):
        if error_name in {"OperationalError", "InterfaceError"}:
            normalized_message = str(error).casefold()
            if "timeout expired" in normalized_message or "timed out" in normalized_message:
                return "DATABASE_CONNECTION_TIMEOUT", True
            if "connection refused" in normalized_message:
                return "DATABASE_CONNECTION_REFUSED", True
            if any(
                marker in normalized_message
                for marker in (
                    "could not translate host name",
                    "name or service not known",
                    "nodename nor servname",
                    "failure in name resolution",
                )
            ):
                return "DATABASE_NAME_RESOLUTION_FAILED", True
            if "network is unreachable" in normalized_message or "no route to host" in normalized_message:
                return "DATABASE_NETWORK_UNREACHABLE", True
            return "DATABASE_CONNECTION_FAILED", True
        if error_name in {"ProgrammingError", "NotSupportedError"}:
            normalized_message = str(error).casefold()
            if "invalid uri query parameter" in normalized_message or (
                "invalid" in normalized_message
                and any(
                    marker in normalized_message
                    for marker in (
                        "sslmode",
                        "target_session_attrs",
                        "gssencmode",
                        "channel_binding",
                    )
                )
            ):
                return "DATABASE_CONNECTION_OPTION_INVALID", False
            if "missing \"=\" after" in normalized_message or "unterminated quoted string" in normalized_message:
                return "DATABASE_CONNECTION_DSN_SYNTAX_INVALID", False
            if "port" in normalized_message and "invalid" in normalized_message:
                return "DATABASE_CONNECTION_PORT_INVALID", False
            if "placeholder" in normalized_message or "parameters were passed" in normalized_message:
                return "DATABASE_CLIENT_PARAMETER_BINDING_FAILED", False
            if "didn't produce" in normalized_message and (
                "record" in normalized_message or "result" in normalized_message
            ):
                return "DATABASE_CLIENT_RESULT_STATE_FAILED", False
            return "DATABASE_CATALOG_QUERY_FAILED", False
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return "DATABASE_CONNECTION_FAILED", True
    return "DATABASE_READ_FAILED", True


class PostgresqlMcpServer(JsonRpcReadonlyServer):
    server_name = SERVER_NAME
    server_version = SERVER_VERSION
    tools = TOOLS

    def __init__(
        self,
        *,
        credential_loader: Optional[Callable[[str], Mapping[str, object]]] = None,
        query_executor: Optional[Callable[..., Mapping[str, object]]] = None,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.credential_loader = credential_loader or load_credentials
        self.query_executor = query_executor or execute_catalog_query
        self.now = now or (lambda: datetime.now(timezone.utc))

    def call_tool(self, name: str, arguments: object, metadata: object = None) -> dict[str, object]:
        request_id, trace_id = fallback_metadata(metadata)
        try:
            request_id, trace_id = safe_metadata(metadata)
            checked = _arguments(arguments)
        except (TypeError, ValueError):
            return tool_result(self._failure(request_id, trace_id, "invalid", "INVALID_TOOL_ARGUMENTS", False))
        if name != TOOL_NAME:
            return tool_result(self._failure(request_id, trace_id, "invalid", "UNKNOWN_TOOL", False))
        try:
            profile = self.credential_loader(checked["connection_alias"])
            if not isinstance(profile, Mapping):
                raise ValueError("credentials unavailable")
        except Exception:
            return tool_result(self._failure(request_id, trace_id, "unavailable", "DATABASE_READONLY_PROFILE_UNAVAILABLE", False))
        try:
            raw = self.query_executor(
                profile=profile,
                operation=checked["operation"],
                schema=checked["schema"],
                table=checked["table"],
                readonly=True,
                timeout_seconds=10,
                row_limit=ROW_LIMIT,
            )
            secrets = tuple(str(profile.get(key, "")) for key in ("dsn", "username", "password"))
            safe = sanitize_json(raw, secrets=secrets)
            if not isinstance(safe, Mapping):
                raise ValueError("invalid database result")
            columns = safe.get("columns")
            rows = safe.get("rows")
            if not isinstance(columns, list) or not isinstance(rows, list):
                raise ValueError("invalid database result")
            redacted_rows: list[list[object]] = []
            for row in rows[:ROW_LIMIT]:
                if not isinstance(row, list):
                    raise ValueError("invalid database result")
                redacted_rows.append(
                    ["[REDACTED]" if isinstance(cell, str) and _SENSITIVE_NAME.search(cell) else cell for cell in row]
                )
            data = {
                "connection_alias": checked["connection_alias"],
                "operation": checked["operation"],
                "columns": columns,
                "rows": redacted_rows,
            }
            version = content_version(data)
            object_id = f"{checked['connection_alias']}:{checked['operation']}"
            if checked["schema"]:
                object_id += f":{checked['schema']}"
            if checked["table"]:
                object_id += f":{checked['table']}"
            envelope = result_envelope(
                request_id=request_id,
                trace_id=trace_id,
                capability="database.inspect",
                provider="postgresql",
                server=SERVER_NAME,
                tool=TOOL_NAME,
                server_version=SERVER_VERSION,
                status="success",
                data=data,
                object_id=object_id,
                version=version,
                observed_at=utc_timestamp(self.now()),
                truncated=bool(safe.get("truncated")),
                next_cursor="",
            )
            return tool_result(envelope)
        except Exception as exc:
            code, retryable = _database_error(exc)
            return tool_result(self._failure(request_id, trace_id, "failed", code, retryable))

    @staticmethod
    def _failure(request_id: str, trace_id: str, status: str, code: str, retryable: bool) -> dict[str, object]:
        return result_envelope(
            request_id=request_id,
            trace_id=trace_id,
            capability="database.inspect",
            provider="postgresql",
            server=SERVER_NAME,
            tool=TOOL_NAME,
            server_version=SERVER_VERSION,
            status=status,
            data={},
            error_code=code,
            retryable=retryable,
            recovery="Check the readonly database alias, driver, and bounded catalog target.",
        )


def main() -> int:
    PostgresqlMcpServer().serve(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
