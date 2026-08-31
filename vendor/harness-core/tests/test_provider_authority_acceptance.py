from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app import database
from app.manager_provider_repository import ManagerProviderRepository
from app.provider_action_authorization import ProviderActionAuthorizer
from app.provider_execution import (
    ACTION_DESCRIPTORS,
    ProviderExecutionRequest,
    ProviderExecutionService,
)
from app.providers.gitlab import canonical_gitlab_target


ROOT = Path(__file__).resolve().parents[1]


class _CredentialReadAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, request, context):
        self.calls += 1
        for field in context.required_credential_fields:
            context.credential(field)
        return {"source": "fixture", "action": request.action}

    def verify(self, *_args, **_kwargs):  # pragma: no cover - reads have no verifier
        raise AssertionError("read adapter verification must not run")


class ProviderAuthorityAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager.sqlite"
        self.database_path = Path(self.temp_dir.name) / "readonly.sqlite"
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute("create table evidence(id integer primary key)")
        self.repository = ManagerProviderRepository()
        self.authorizer = ProviderActionAuthorizer(
            self.repository,
            clock=lambda: datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc),
        )

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_yunxiao_gitlab_and_database_reads_need_no_harness_confirmation(self) -> None:
        cases = (
            {
                "provider": "yunxiao",
                "profile_key": "company-read",
                "connection": {"project_key": "DFHIS"},
                "action": "workitem.read",
                "target": "dfhis-1",
                "parameters": {"work_item_alias": "DFHIS-1"},
                "credential_field": "pat",
            },
            {
                "provider": "gitlab",
                "profile_key": "corp",
                "connection": {"host": "corp"},
                "action": "project.read",
                "target": canonical_gitlab_target(
                    "project.read",
                    {"host_alias": "corp", "project_alias": "group/project"},
                ),
                "parameters": {"host_alias": "corp", "project_alias": "group/project"},
                "credential_field": "access_token",
            },
            {
                "provider": "database",
                "profile_key": "his-readonly",
                "connection": {
                    "driver": "sqlite",
                    "host": "local",
                    "port": "0",
                    "database": str(self.database_path),
                    "schema": "main",
                    "username": "readonly",
                    "readonly_policy": "required",
                },
                "action": "database.connection_test",
                "target": "db-his-readonly",
                "parameters": {"database_alias": "db-his-readonly"},
                "credential_field": "password",
            },
        )

        for case in cases:
            with self.subTest(provider=case["provider"]):
                profile = self.repository.upsert_profile(
                    scope_type="local",
                    scope_key="default",
                    provider=case["provider"],
                    profile_key=case["profile_key"],
                    display_name=f"{case['provider']} readonly",
                    enabled=True,
                    connection=case["connection"],
                )
                plan = self.authorizer.create_plan(
                    profile_id=profile.id,
                    action=case["action"],
                    target_alias=case["target"],
                    parameters=case["parameters"],
                    requested_by="manager-user",
                )
                request = ProviderExecutionRequest(
                    plan_id=plan.id,
                    actor="manager-user",
                    action=case["action"],
                    parameters=case["parameters"],
                )
                adapter = _CredentialReadAdapter()
                credential_calls: list[tuple[int, str]] = []
                service = ProviderExecutionService(
                    self.repository,
                    self.authorizer,
                    adapters={case["provider"]: adapter},
                    credential_resolver=lambda profile_id, field: (
                        credential_calls.append((profile_id, field)) or "FIXTURE_CREDENTIAL"
                    ),
                )

                result = service.execute(None, request)

                self.assertEqual("succeeded", result["status"])
                self.assertEqual([(profile.id, case["credential_field"])], credential_calls)
                self.assertEqual(1, adapter.calls)
                self.assertEqual("consumed", self.repository.get_action_plan(plan.id)["state"])

    def test_database_mutation_has_no_registered_execution_action(self) -> None:
        registered_database_actions = {
            action
            for action, descriptor in ACTION_DESCRIPTORS.items()
            if descriptor.provider == "database"
        }

        self.assertEqual(
            {
                "database.connection_test",
                "database.schema.read",
                "database.query.read",
            },
            registered_database_actions,
        )
        self.assertTrue(
            all(ACTION_DESCRIPTORS[action].risk == "read" for action in registered_database_actions)
        )
        matrix = json.loads(
            (ROOT / "config" / "role_capability_skill_matrix.json").read_text(
                encoding="utf-8"
            )
        )
        database_change = next(
            row
            for row in matrix["capability_routes"]
            if row["capability"] == "database.change"
        )
        self.assertFalse(database_change["external_executable"])


if __name__ == "__main__":
    unittest.main()
