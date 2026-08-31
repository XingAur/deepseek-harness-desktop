from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


PLUGIN_ROOT = Path("/Users/lym/plugins/his-engineering")


def _load(relative: str, module_name: str):
    path = PLUGIN_ROOT / relative
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class McpConnectorServerContractTests(unittest.TestCase):
    def test_gitlab_server_is_get_only_and_never_exposes_credentials(self) -> None:
        module = _load("scripts/gitlab_mcp_server.py", "phase1d_gitlab_mcp")
        self.assertIsNotNone(module, "GitLab MCP server is required")
        if module is None:
            return
        calls: list[dict[str, object]] = []

        def transport(**kwargs):
            calls.append(dict(kwargs))
            return module.GitLabHttpResponse(
                status_code=200,
                headers={},
                body=b'{"id":1,"path_with_namespace":"group/project"}',
            )

        server = module.GitLabMcpServer(
            credential_loader=lambda **_: {
                "base_url": "https://gitlab.example.test",
                "access_token": "gitlab-secret-sentinel",
            },
            transport=transport,
            now=lambda: datetime(2026, 8, 30, tzinfo=timezone.utc),
        )
        listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tool = listed["result"]["tools"][0]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        schema_text = json.dumps(tool["inputSchema"], sort_keys=True).lower()
        for forbidden in ("token", "password", "credential", "dsn", "connection_string"):
            self.assertNotIn(forbidden, schema_text)

        result = server.call_tool(
            "repository_read",
            {
                "project": "group/project",
                "operation": "project",
                "ref": "",
                "path": "",
                "object_id": "",
            },
            {"request_id": "gitlab-request-1", "trace_id": "gitlab-trace-1"},
        )
        self.assertEqual("success", result["structuredContent"]["status"])
        self.assertEqual(1, len(calls))
        self.assertEqual("GET", calls[0]["method"])
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn("gitlab-secret-sentinel", rendered)

    def test_postgresql_server_executes_only_allowlisted_catalog_reads(self) -> None:
        module = _load("scripts/postgresql_mcp_server.py", "phase1d_postgresql_mcp")
        self.assertIsNotNone(module, "PostgreSQL MCP server is required")
        if module is None:
            return
        calls: list[dict[str, object]] = []

        def query_executor(**kwargs):
            calls.append(dict(kwargs))
            return {
                "columns": ["schema_name", "table_name"],
                "rows": [["public", "orders"]],
                "truncated": False,
            }

        server = module.PostgresqlMcpServer(
            credential_loader=lambda alias: {
                "host": "127.0.0.1",
                "port": 5432,
                "database": "his",
                "username": "readonly_user",
                "password": "database-secret-sentinel",
                "sslmode": "require",
            },
            query_executor=query_executor,
            now=lambda: datetime(2026, 8, 30, tzinfo=timezone.utc),
        )
        listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tool = listed["result"]["tools"][0]
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        schema_text = json.dumps(tool["inputSchema"], sort_keys=True).lower()
        for forbidden in ("sql", "token", "password", "credential", "dsn", "connection_string"):
            self.assertNotIn(forbidden, schema_text)

        result = server.call_tool(
            "readonly_inspect",
            {
                "connection_alias": "his_readonly",
                "operation": "foreign_keys",
                "schema": "public",
                "table": "orders",
            },
            {"request_id": "db-request-1", "trace_id": "db-trace-1"},
        )
        self.assertEqual("success", result["structuredContent"]["status"])
        self.assertEqual(1, len(calls))
        self.assertEqual("foreign_keys", calls[0]["operation"])
        self.assertTrue(calls[0]["readonly"])
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn("database-secret-sentinel", rendered)

    def test_postgresql_server_classifies_failures_without_echoing_driver_errors(self) -> None:
        module = _load("scripts/postgresql_mcp_server.py", "phase1d_postgresql_mcp_errors")
        self.assertIsNotNone(module, "PostgreSQL MCP server is required")
        if module is None:
            return

        class OperationalError(Exception):
            sqlstate = "08006"

        OperationalError.__module__ = "psycopg"

        def failed_query(**_kwargs):
            raise OperationalError("secret-host secret-user secret-password")

        server = module.PostgresqlMcpServer(
            credential_loader=lambda _alias: {
                "dsn": "secret-host",
                "username": "secret-user",
                "password": "secret-password",
            },
            query_executor=failed_query,
        )
        result = server.call_tool(
            "readonly_inspect",
            {
                "connection_alias": "his_readonly",
                "operation": "schemas",
                "schema": "",
                "table": "",
            },
            {"request_id": "db-request-2", "trace_id": "db-trace-2"},
        )

        content = result["structuredContent"]
        self.assertEqual("DATABASE_CONNECTION_FAILED", content["error"]["code"])
        self.assertTrue(content["error"]["retryable"])
        rendered = json.dumps(result, sort_keys=True)
        for secret in ("secret-host", "secret-user", "secret-password"):
            self.assertNotIn(secret, rendered)

        for sqlstate, expected in (
            ("42501", "DATABASE_CATALOG_PERMISSION_DENIED"),
            ("42601", "DATABASE_CATALOG_SYNTAX_FAILED"),
            ("42P01", "DATABASE_CATALOG_OBJECT_UNAVAILABLE"),
            ("42703", "DATABASE_CATALOG_COLUMN_UNAVAILABLE"),
            ("42ZZZ", "DATABASE_CATALOG_SQLSTATE_42ZZZ"),
            ("3D000", "DATABASE_DATABASE_UNAVAILABLE"),
            ("57P03", "DATABASE_CONNECTION_NOT_READY"),
            ("53300", "DATABASE_CONNECTION_LIMIT_REACHED"),
            ("0A000", "DATABASE_CONNECTION_OPTION_UNSUPPORTED"),
            ("XX000", "DATABASE_SQLSTATE_XX000"),
        ):
            error = OperationalError("must-not-be-rendered")
            error.sqlstate = sqlstate
            with self.subTest(sqlstate=sqlstate):
                expected_retryable = sqlstate in {"57P03", "53300"}
                self.assertEqual(
                    (expected, expected_retryable), module._database_error(error)
                )

        class ProgrammingError(Exception):
            sqlstate = None

        ProgrammingError.__module__ = "psycopg"
        for message, expected in (
            (
                "the query has 0 placeholders but 1 parameters were passed",
                "DATABASE_CLIENT_PARAMETER_BINDING_FAILED",
            ),
            (
                "the last operation didn't produce records",
                "DATABASE_CLIENT_RESULT_STATE_FAILED",
            ),
            (
                'invalid URI query parameter: "currentSchema"',
                "DATABASE_CONNECTION_OPTION_INVALID",
            ),
            (
                'invalid sslmode value: "false"',
                "DATABASE_CONNECTION_OPTION_INVALID",
            ),
            (
                'missing "=" after "jdbc" in connection info string',
                "DATABASE_CONNECTION_DSN_SYNTAX_INVALID",
            ),
            (
                'invalid port number: "abc"',
                "DATABASE_CONNECTION_PORT_INVALID",
            ),
        ):
            with self.subTest(client_message=expected):
                self.assertEqual((expected, False), module._database_error(ProgrammingError(message)))

        for message, expected in (
            ("connection timeout expired", "DATABASE_CONNECTION_TIMEOUT"),
            ("connection refused", "DATABASE_CONNECTION_REFUSED"),
            ("could not translate host name", "DATABASE_NAME_RESOLUTION_FAILED"),
            ("network is unreachable", "DATABASE_NETWORK_UNREACHABLE"),
        ):
            with self.subTest(connection_message=expected):
                error = OperationalError(message)
                error.sqlstate = None
                self.assertEqual(
                    (expected, True),
                    module._database_error(error),
                )
                self.assertEqual(
                    (expected, True),
                    module._database_error(
                        module._DatabaseExecutionStageError("connection_setup", error)
                    ),
                )

    def test_postgresql_schema_query_does_not_bind_an_empty_parameter_tuple(self) -> None:
        module = _load("scripts/postgresql_mcp_server.py", "phase1d_postgresql_mcp_binding")
        self.assertIsNotNone(module, "PostgreSQL MCP server is required")
        if module is None:
            return
        calls: list[tuple[object, ...]] = []

        class Cursor:
            description = (("schema_name",),)

            def execute(self, *args):
                calls.append(args)

            def fetchmany(self, _limit):
                return [("public",)]

        class Connection:
            autocommit = True

            def cursor(self):
                return Cursor()

            def rollback(self):
                return None

            def close(self):
                return None

        original_connect = module._connect
        module._connect = lambda _profile, _timeout: Connection()
        self.addCleanup(setattr, module, "_connect", original_connect)

        result = module.execute_catalog_query(
            profile={},
            operation="schemas",
            schema="",
            table="",
            readonly=True,
            timeout_seconds=10,
            row_limit=500,
        )

        self.assertEqual({"columns": ["schema_name"], "rows": [["public"]], "truncated": False}, result)
        self.assertEqual(("SET TRANSACTION READ ONLY",), calls[0])
        self.assertEqual(1, len(calls[-1]))

    def test_postgresql_catalog_queries_do_not_mix_driver_placeholders_with_like_percent_literals(self) -> None:
        module = _load("scripts/postgresql_mcp_server.py", "phase1d_postgresql_mcp_percent")
        self.assertIsNotNone(module, "PostgreSQL MCP server is required")
        if module is None:
            return

        for operation, (statement, _parameters_factory) in module._CATALOG_QUERIES.items():
            with self.subTest(operation=operation):
                non_placeholders = statement.replace("%s", "").replace("%%", "")
                self.assertNotIn("%", non_placeholders)

    def test_postgresql_connector_normalizes_the_his_jdbc_readonly_dsn(self) -> None:
        module = _load("scripts/postgresql_mcp_server.py", "phase1d_postgresql_mcp_jdbc")
        self.assertIsNotNone(module, "PostgreSQL MCP server is required")
        if module is None:
            return
        self.assertTrue(
            hasattr(module, "_normalize_postgres_dsn"),
            "PostgreSQL MCP must normalize the HIS JDBC readonly DSN internally",
        )
        if not hasattr(module, "_normalize_postgres_dsn"):
            return

        self.assertEqual(
            "postgresql://db.example.invalid:5432/df_his",
            module._normalize_postgres_dsn(
                "jdbc:postgresql://db.example.invalid:5432/df_his"
            ),
        )
        self.assertEqual(
            "postgresql://db.example.invalid/df_his",
            module._normalize_postgres_dsn("postgresql://db.example.invalid/df_his"),
        )
        self.assertTrue(
            hasattr(module, "_build_postgres_connect_kwargs"),
            "PostgreSQL MCP must isolate JDBC-only properties from libpq arguments",
        )
        if not hasattr(module, "_build_postgres_connect_kwargs"):
            return
        self.assertEqual(
            {
                "host": "db.example.invalid",
                "port": 5432,
                "dbname": "df_his",
                "sslmode": "require",
            },
            module._build_postgres_connect_kwargs(
                "jdbc:postgresql://db.example.invalid:5432/df_his"
                "?currentSchema=his&stringtype=unspecified&sslmode=require"
            ),
        )

    def test_postgresql_server_reports_the_exact_catalog_execution_stage(self) -> None:
        module = _load("scripts/postgresql_mcp_server.py", "phase1d_postgresql_mcp_stages")
        self.assertIsNotNone(module, "PostgreSQL MCP server is required")
        if module is None:
            return

        class ProgrammingError(Exception):
            sqlstate = None

        ProgrammingError.__module__ = "psycopg"

        class Cursor:
            def __init__(self, failed_stage: str) -> None:
                self.failed_stage = failed_stage
                self.execute_count = 0

            @property
            def description(self):
                if self.failed_stage == "catalog_metadata":
                    raise ProgrammingError("secret-host secret-user secret-password")
                return (("schema_name",),)

            def execute(self, *_args):
                stage = ("readonly_transaction", "timeout_configuration", "catalog_query")[
                    self.execute_count
                ]
                self.execute_count += 1
                if stage == self.failed_stage:
                    raise ProgrammingError("secret-host secret-user secret-password")

            def fetchmany(self, _limit):
                if self.failed_stage == "catalog_fetch":
                    raise ProgrammingError("secret-host secret-user secret-password")
                return [("public",)]

        class Connection:
            autocommit = True

            def __init__(self, failed_stage: str) -> None:
                self.failed_stage = failed_stage

            def cursor(self):
                return Cursor(self.failed_stage)

            def rollback(self):
                if self.failed_stage == "transaction_rollback":
                    raise ProgrammingError("secret-host secret-user secret-password")
                return None

            def close(self):
                if self.failed_stage == "connection_close":
                    raise ProgrammingError("secret-host secret-user secret-password")
                return None

        expected_codes = {
            "connection_setup": "DATABASE_CONNECTION_CONFIGURATION_FAILED",
            "readonly_transaction": "DATABASE_READONLY_TRANSACTION_FAILED",
            "timeout_configuration": "DATABASE_TIMEOUT_CONFIGURATION_FAILED",
            "catalog_query": "DATABASE_CATALOG_QUERY_FAILED",
            "catalog_metadata": "DATABASE_CATALOG_METADATA_FAILED",
            "catalog_fetch": "DATABASE_CATALOG_FETCH_FAILED",
            "transaction_rollback": "DATABASE_TRANSACTION_ROLLBACK_FAILED",
            "connection_close": "DATABASE_CONNECTION_CLOSE_FAILED",
        }
        original_connect = module._connect
        self.addCleanup(setattr, module, "_connect", original_connect)
        for failed_stage, expected_code in expected_codes.items():
            with self.subTest(failed_stage=failed_stage):
                def connect(_profile, _timeout, stage=failed_stage):
                    if stage == "connection_setup":
                        raise ProgrammingError("secret-host secret-user secret-password")
                    return Connection(stage)

                module._connect = connect
                server = module.PostgresqlMcpServer(
                    credential_loader=lambda _alias: {
                        "dsn": "secret-host",
                        "username": "secret-user",
                        "password": "secret-password",
                    }
                )
                result = server.call_tool(
                    "readonly_inspect",
                    {
                        "connection_alias": "his_readonly",
                        "operation": "schemas",
                        "schema": "",
                        "table": "",
                    },
                    {"request_id": "db-request-stage", "trace_id": "db-trace-stage"},
                )

                content = result["structuredContent"]
                self.assertEqual(expected_code, content["error"]["code"])
                rendered = json.dumps(result, sort_keys=True)
                for secret in ("secret-host", "secret-user", "secret-password"):
                    self.assertNotIn(secret, rendered)


if __name__ == "__main__":
    unittest.main()
