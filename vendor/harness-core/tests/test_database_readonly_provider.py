from __future__ import annotations

import json
import hashlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app import database
from app.manager_provider_repository import ManagerProviderRepository
from app.provider_action_authorization import (
    ProviderActionAuthorization,
    ProviderActionAuthorizer,
    canonical_json_hash,
)
from app.provider_execution import (
    ACTION_DESCRIPTORS,
    ProviderExecutionRequest,
    ProviderExecutionService,
)
from app.providers.database_readonly import (
    DatabaseReadonlyProfile,
    DatabaseReadonlyProviderAdapter,
)


class _RecordingSqliteFactory:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str, int]] = []
        self.statements: list[str] = []

    def __call__(self, profile, password: str, timeout_seconds: int):
        self.calls.append((profile, password, timeout_seconds))
        connection = sqlite3.connect(profile.database, timeout=timeout_seconds)
        return _RecordingConnection(connection, self.statements)


class _RecordingConnection:
    def __init__(self, connection: sqlite3.Connection, statements: list[str]) -> None:
        self._connection = connection
        self._statements = statements

    def execute(self, sql: str, parameters=()):
        self._statements.append(sql)
        return self._connection.execute(sql, parameters)

    def close(self) -> None:
        self._connection.close()


class DatabaseReadonlyProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.external_db_path = Path(self.temp_dir.name) / "his-fixture.sqlite"
        connection = sqlite3.connect(self.external_db_path)
        try:
            connection.execute("create table patient(id integer primary key, name text, password text)")
            connection.execute(
                "insert into patient(id, name, password) values(1, '张三', 'SENTINEL_PASSWORD')"
            )
            connection.commit()
        finally:
            connection.close()
        self.manager_db_path = Path(self.temp_dir.name) / "manager.sqlite"
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = self.manager_db_path
        self.repository = ManagerProviderRepository()
        self.profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="database",
            profile_key="his-readonly",
            display_name="HIS readonly",
            enabled=True,
            connection=self.connection(),
        )
        self.authorizer = ProviderActionAuthorizer(
            self.repository,
            clock=lambda: datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc),
        )

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def connection(self, **overrides: str) -> dict[str, str]:
        value = {
            "driver": "sqlite",
            "host": "local",
            "port": "0",
            "database": str(self.external_db_path),
            "schema": "main",
            "username": "readonly",
            "readonly_policy": "required",
        }
        value.update(overrides)
        return value

    def adapter(self, factory=None) -> DatabaseReadonlyProviderAdapter:
        return DatabaseReadonlyProviderAdapter(
            profile_loader=lambda profile_id: {
                "id": profile_id,
                "provider": "database",
                "profile_key": "his-readonly",
                "enabled": True,
                "connection": self.connection(),
            },
            driver_factories={"sqlite": factory} if factory is not None else None,
        )

    def request(self, action: str, parameters: dict[str, object]) -> tuple[object, ProviderExecutionRequest]:
        plan = self.authorizer.create_plan(
            profile_id=self.profile.id,
            action=action,
            target_alias="db-his-readonly",
            parameters=parameters,
            requested_by="manager-user",
        )
        return plan, ProviderExecutionRequest(
            plan_id=plan.id,
            actor="manager-user",
            action=action,
            parameters=parameters,
        )

    def test_database_plan_rejects_noncanonical_profile_alias_for_every_execution_action(self) -> None:
        cases = (
            ("database.connection_test", {}),
            ("database.schema.read", {}),
            ("database.query.read", {"sql": "select id from patient"}),
        )
        for action, extra_parameters in cases:
            with self.subTest(action=action, mismatch="plan"):
                with self.assertRaisesRegex(ValueError, "database_target_invalid"):
                    self.authorizer.create_plan(
                        profile_id=self.profile.id,
                        action=action,
                        target_alias="db-reviewed-but-unrelated",
                        parameters={
                            "database_alias": "db-reviewed-but-unrelated",
                            **extra_parameters,
                        },
                        requested_by="manager-user",
                    )
            with self.subTest(action=action, mismatch="request"):
                with self.assertRaisesRegex(ValueError, "database_target_invalid"):
                    self.authorizer.create_plan(
                        profile_id=self.profile.id,
                        action=action,
                        target_alias="db-his-readonly",
                        parameters={
                            "database_alias": "db-reviewed-but-unrelated",
                            **extra_parameters,
                        },
                        requested_by="manager-user",
                    )

    def test_preexisting_confirmed_plan_with_noncanonical_alias_cannot_render_or_execute(self) -> None:
        parameters = {
            "database_alias": "db-reviewed-but-unrelated",
            "sql": "select id from patient",
        }
        token = "reviewed-confirmation-token"
        authorization_hash = "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
        timestamp = "2026-08-10T08:00:00+00:00"
        with database.connect() as db:
            cursor = db.execute(
                """
                insert into manager_provider_action_plans(
                    profile_id, scope_type, scope_key, provider, profile_key,
                    action_type, target_alias, parameter_hash, requested_by,
                    confirmed_by, authorization_hash, state, created_at,
                    confirmed_at, authorization_expires_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.profile.id,
                    "local",
                    "default",
                    "database",
                    "his-readonly",
                    "database.query.read",
                    "db-reviewed-but-unrelated",
                    canonical_json_hash(parameters),
                    "manager-user",
                    "manager-user",
                    authorization_hash,
                    "confirmed",
                    timestamp,
                    timestamp,
                    "2026-08-10T08:05:00+00:00",
                ),
            )
            plan_id = int(cursor.lastrowid)
        request = ProviderExecutionRequest(
            plan_id=plan_id,
            actor="manager-user",
            action="database.query.read",
            parameters=parameters,
        )
        factory = _RecordingSqliteFactory()
        credential_calls: list[str] = []
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"database": self.adapter(factory)},
            credential_resolver=lambda *_args: (credential_calls.append("password") or "test-password"),
        )
        authorization = ProviderActionAuthorization(
            plan_id=plan_id,
            token=token,
            authorization_hash=authorization_hash,
            actor="manager-user",
            issued_at=datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc),
            expires_at=datetime(2026, 8, 10, 8, 5, tzinfo=timezone.utc),
        )

        with self.assertRaisesRegex(ValueError, "provider_target_mismatch"):
            service.render_plan(request)
        result = service.execute(authorization, request)

        self.assertEqual("blocked", result["status"])
        self.assertEqual("provider_target_mismatch", result["reason"])
        self.assertEqual([], credential_calls)
        self.assertEqual([], factory.calls)
        self.assertEqual("confirmed", self.repository.get_action_plan(plan_id)["state"])

    def test_disabled_database_profile_cannot_create_or_confirm_a_plan(self) -> None:
        self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="database",
            profile_key="his-readonly",
            display_name="HIS readonly",
            enabled=False,
            connection=self.connection(),
        )
        parameters = {"database_alias": "db-his-readonly", "sql": "select id from patient"}

        with self.assertRaisesRegex(PermissionError, "provider_profile_disabled"):
            self.authorizer.create_plan(
                profile_id=self.profile.id,
                action="database.query.read",
                target_alias="db-his-readonly",
                parameters=parameters,
                requested_by="manager-user",
            )

        self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="database",
            profile_key="his-readonly",
            display_name="HIS readonly",
            enabled=True,
            connection=self.connection(),
        )
        plan = self.authorizer.create_plan(
            profile_id=self.profile.id,
            action="database.query.read",
            target_alias="db-his-readonly",
            parameters=parameters,
            requested_by="manager-user",
        )
        self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="database",
            profile_key="his-readonly",
            display_name="HIS readonly",
            enabled=False,
            connection=self.connection(),
        )

        with self.assertRaisesRegex(PermissionError, "provider_profile_disabled"):
            self.authorizer.confirm(plan.id, actor="manager-user")
        self.assertEqual("planned", self.repository.get_action_plan(plan.id)["state"])

    def test_disabled_database_profile_blocks_confirmed_execution_before_credential_or_connection(self) -> None:
        parameters = {"database_alias": "db-his-readonly", "sql": "select id from patient"}
        plan = self.authorizer.create_plan(
            profile_id=self.profile.id,
            action="database.query.read",
            target_alias="db-his-readonly",
            parameters=parameters,
            requested_by="manager-user",
        )
        authorization = self.authorizer.confirm(plan.id, actor="manager-user")
        self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="database",
            profile_key="his-readonly",
            display_name="HIS readonly",
            enabled=False,
            connection=self.connection(),
        )
        factory = _RecordingSqliteFactory()
        credential_calls: list[str] = []
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"database": self.adapter(factory)},
            credential_resolver=lambda *_args: (credential_calls.append("password") or "test-password"),
        )

        result = service.execute(
            authorization,
            ProviderExecutionRequest(
                plan_id=plan.id,
                actor="manager-user",
                action="database.query.read",
                parameters=parameters,
            ),
        )

        self.assertEqual("blocked", result["status"])
        self.assertEqual("provider_profile_disabled", result["reason"])
        self.assertEqual([], credential_calls)
        self.assertEqual([], factory.calls)
        self.assertEqual("confirmed", self.repository.get_action_plan(plan.id)["state"])

    def test_sqlite_leaf_symlink_is_rejected_before_path_resolution(self) -> None:
        alias_path = Path(self.temp_dir.name) / "alias.sqlite"
        alias_path.symlink_to(self.external_db_path)

        with self.assertRaisesRegex(ValueError, "database_sqlite_path_invalid"):
            DatabaseReadonlyProfile.from_connection(self.connection(database=str(alias_path)))

    def test_query_requires_a_required_readonly_profile_before_connecting(self) -> None:
        factory = _RecordingSqliteFactory()
        adapter = DatabaseReadonlyProviderAdapter(
            profile_loader=lambda _profile_id: {
                "provider": "database",
                "profile_key": "his-readonly",
                "enabled": True,
                "connection": self.connection(readonly_policy="optional"),
            },
            driver_factories={"sqlite": factory},
        )
        plan, request = self.request(
            "database.query.read",
            {"database_alias": "db-his-readonly", "sql": "select id from patient"},
        )
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"database": adapter},
            credential_resolver=lambda _profile_id, _field: "test-password",
        )

        result = service.execute(self.authorizer.confirm(plan.id, actor="manager-user"), request)

        self.assertEqual("failed", result["status"])
        self.assertEqual("provider_adapter_failed", result["reason"])
        self.assertEqual([], factory.calls)

    def test_all_forbidden_sql_is_rejected_before_the_driver_factory_or_credential_is_used(self) -> None:
        factory = _RecordingSqliteFactory()
        adapter = self.adapter(factory)
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"database": adapter},
            credential_resolver=lambda *_args: self.fail("invalid SQL must not resolve a password"),
        )
        forbidden = (
            "insert into patient values(2, '李四', 'x')",
            "create table x(id integer)",
            "begin transaction",
            "pragma table_info(patient)",
            "attach database 'other.sqlite' as other",
            "select 1; select 2",
            "call business_procedure()",
        )

        for sql in forbidden:
            with self.subTest(sql=sql):
                plan, request = self.request(
                    "database.query.read",
                    {"database_alias": "db-his-readonly", "sql": sql},
                )
                result = service.execute(None, request)
                self.assertEqual("failed", result["status"])
                self.assertEqual("provider_adapter_failed", result["reason"])
        self.assertEqual([], factory.calls)
        self.assertFalse(any(statement.lower().split(maxsplit=1)[0] in {"insert", "update", "delete", "create"} for statement in factory.statements))

    def test_query_uses_a_bounded_redacted_local_response_and_audit_has_no_rows_or_sql(self) -> None:
        plan, request = self.request(
            "database.query.read",
            {
                "database_alias": "db-his-readonly",
                "sql": "select id, name, password from patient order by id",
            },
        )
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"database": self.adapter()},
            credential_resolver=lambda _profile_id, _field: "test-password",
        )

        result = service.execute(self.authorizer.confirm(plan.id, actor="manager-user"), request)
        audit = self.repository.list_action_audits(limit=1)[0]["details"]
        rendered_audit = json.dumps(audit, ensure_ascii=False)

        self.assertEqual("succeeded", result["status"])
        self.assertEqual(["id", "name", "[REDACTED_COLUMN]"], result["local_response"]["columns"])
        self.assertEqual([1, "张三", "[REDACTED]"], result["local_response"]["rows"][0])
        self.assertIn("sql_sha256", result["result_summary"])
        self.assertNotIn("select id", rendered_audit.lower())
        self.assertNotIn("张三", rendered_audit)
        self.assertNotIn("SENTINEL_PASSWORD", rendered_audit)
        self.assertNotIn("local_response", rendered_audit)

    def test_schema_read_is_summary_only_and_does_not_use_pragma(self) -> None:
        factory = _RecordingSqliteFactory()
        plan, request = self.request(
            "database.schema.read", {"database_alias": "db-his-readonly"}
        )
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"database": self.adapter(factory)},
            credential_resolver=lambda _profile_id, _field: "test-password",
        )

        result = service.execute(self.authorizer.confirm(plan.id, actor="manager-user"), request)

        self.assertEqual("succeeded", result["status"])
        self.assertEqual(1, result["result_summary"]["schema"]["object_count"])
        self.assertNotIn("local_response", result)
        self.assertFalse(any("pragma" in statement.lower() for statement in factory.statements))

    def test_connection_test_reuses_the_same_readonly_profile_contract_as_query(self) -> None:
        factory = _RecordingSqliteFactory()
        plan, request = self.request(
            "database.connection_test", {"database_alias": "db-his-readonly"}
        )
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"database": self.adapter(factory)},
            credential_resolver=lambda _profile_id, _field: "test-password",
        )

        result = service.execute(self.authorizer.confirm(plan.id, actor="manager-user"), request)

        self.assertEqual("succeeded", result["status"])
        self.assertEqual("readonly_verified", result["result_summary"]["connection"])
        self.assertEqual("sqlite", factory.calls[0][0].driver)
        self.assertEqual("required", factory.calls[0][0].readonly_policy)
        self.assertEqual(["SELECT 1"], factory.statements)

    def test_query_uses_readonly_endpoint_credential_without_harness_confirmation(self) -> None:
        factory = _RecordingSqliteFactory()
        plan, request = self.request(
            "database.query.read",
            {"database_alias": "db-his-readonly", "sql": "select id from patient"},
        )
        credential_calls: list[object] = []
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"database": self.adapter(factory)},
            credential_resolver=lambda *_args: (credential_calls.append("password") or "test-password"),
        )

        result = service.execute(None, request)

        self.assertEqual("succeeded", result["status"])
        self.assertEqual(["password"], credential_calls)
        self.assertEqual(1, len(factory.calls))
        self.assertEqual("required", factory.calls[0][0].readonly_policy)
        self.assertEqual("consumed", self.repository.get_action_plan(plan.id)["state"])

    def test_query_row_and_column_limits_bound_the_local_response(self) -> None:
        connection = sqlite3.connect(self.external_db_path)
        try:
            connection.executemany(
                "insert into patient(id, name, password) values(?, ?, ?)",
                [(identifier, f"患者{identifier}", "hidden") for identifier in range(2, 108)],
            )
            connection.commit()
        finally:
            connection.close()
        plan, request = self.request(
            "database.query.read",
            {"database_alias": "db-his-readonly", "sql": "select id from patient order by id"},
        )
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"database": self.adapter()},
            credential_resolver=lambda _profile_id, _field: "test-password",
        )

        result = service.execute(self.authorizer.confirm(plan.id, actor="manager-user"), request)

        self.assertEqual("succeeded", result["status"])
        self.assertEqual(100, result["local_response"]["row_count"])
        self.assertTrue(result["local_response"]["truncated"])
        self.assertLess(
            len(json.dumps(result["local_response"], ensure_ascii=False).encode("utf-8")),
            48 * 1024,
        )

    def test_locking_select_is_rejected_before_connection_open(self) -> None:
        factory = _RecordingSqliteFactory()
        plan, request = self.request(
            "database.query.read",
            {"database_alias": "db-his-readonly", "sql": "select id from patient for update"},
        )
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"database": self.adapter(factory)},
            credential_resolver=lambda *_args: self.fail("locking query must not resolve credential"),
        )

        result = service.execute(self.authorizer.confirm(plan.id, actor="manager-user"), request)

        self.assertEqual("failed", result["status"])
        self.assertEqual([], factory.calls)

    def test_view_sql_draft_is_manual_only_and_is_not_a_provider_execution_action(self) -> None:
        adapter = self.adapter()

        draft = adapter.draft_view_sql(
            database_alias="db-his-readonly",
            view_name="patient_summary",
            select_sql="select id, name from patient",
        )

        self.assertTrue(draft["manual_execution_required"])
        self.assertEqual("database.view_sql.draft", draft["action"])
        self.assertIn("create view", draft["sql"].lower())
        self.assertNotIn("database.view_sql.draft", ACTION_DESCRIPTORS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
