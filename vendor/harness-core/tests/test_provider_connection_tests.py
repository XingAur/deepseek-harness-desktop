from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app import database
from app.manager_provider_repository import ManagerProviderRepository
from app.provider_action_authorization import ProviderActionAuthorizer
from app.provider_connection_tests import (
    load_provider_connection_test_audit,
    run_provider_connection_test,
)
from app.provider_execution import ProviderExecutionService
from app.providers.github import GitHubHttpResponse, GitHubProviderAdapter


class FakeConnectionAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, request, context):
        self.calls += 1
        self.asserted_network = context.network_allowed
        context.credential("pat")
        return {"health": "ok"}

    def verify(self, verifier_action, original_write_action, request, target_alias, context):
        raise AssertionError("read connection action must not verify a write")


class FakeGitHubConnectionTransport:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *, method, url, headers, body, timeout_seconds):
        self.calls += 1
        if (
            method != "GET"
            or url != "https://api.github.com/rate_limit"
            or body is not None
            or headers.get("Authorization") != "Bearer GITHUB_TEST_TOKEN"
            or timeout_seconds != 10
        ):
            raise AssertionError("unexpected github connection request")
        return GitHubHttpResponse(
            status_code=200,
            headers={"x-github-request-id": "github-connection-1"},
            body=b'{"resources":{"core":{"limit":5000}}}',
        )


class ProviderConnectionTestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager.sqlite"
        self.repository = ManagerProviderRepository()
        self.profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="yunxiao",
            profile_key="company",
            display_name="Company",
            enabled=True,
            connection={"project_key": "DFHIS"},
        )
        self.profiles = [{
            "provider": "yunxiao",
            "profile_key": "company",
            "credential_ref": "identity",
            "connection": {"project_key": "DFHIS"},
        }]
        self.authorizer = ProviderActionAuthorizer(
            self.repository,
            clock=lambda: datetime(2026, 8, 9, 3, 0, tzinfo=timezone.utc),
        )

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_connection_request_creates_approval_free_governed_read_plan_without_audit(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy.jsonl"
        legacy_path.write_text("legacy\n", encoding="utf-8")

        result = run_provider_connection_test(
            self.profiles,
            provider="yunxiao",
            profile_key="company",
            requested_by="manager",
            audit_path=legacy_path,
            repository=self.repository,
            authorizer=self.authorizer,
        )

        self.assertEqual("ready_to_execute", result["status"])
        self.assertEqual("provider_technical_authority_required", result["reason"])
        self.assertEqual("yunxiao.connection_test", result["action"])
        self.assertIsInstance(result["plan_id"], int)
        self.assertFalse(result["credentials_read"])
        self.assertFalse(result["external_calls"])
        self.assertTrue(result["execution_allowed"])
        self.assertFalse(result["confirmation_required"])
        self.assertEqual("planned", self.repository.get_action_plan(result["plan_id"])["state"])
        self.assertEqual([], self.repository.list_action_audits())
        self.assertEqual("legacy\n", legacy_path.read_text(encoding="utf-8"))

    def test_read_connection_delegates_without_confirmation_and_audits_in_service(self) -> None:
        planned = run_provider_connection_test(
            self.profiles,
            provider="yunxiao",
            profile_key="company",
            requested_by="manager",
            repository=self.repository,
            authorizer=self.authorizer,
        )
        adapter = FakeConnectionAdapter()
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"yunxiao": adapter},
            credential_resolver=lambda _profile_id, _field: "FAKE_PAT",
        )

        result = run_provider_connection_test(
            self.profiles,
            provider="yunxiao",
            profile_key="company",
            requested_by="manager",
            repository=self.repository,
            authorizer=self.authorizer,
            execution_service=service,
            plan_id=planned["plan_id"],
        )

        self.assertEqual("succeeded", result["status"])
        self.assertEqual(1, adapter.calls)
        self.assertTrue(adapter.asserted_network)
        audits = load_provider_connection_test_audit(repository=self.repository)
        self.assertEqual(1, audits["record_count"])
        self.assertEqual("succeeded", audits["records"][0]["status"])

    def test_database_connection_plan_uses_the_database_adapter_target_contract(self) -> None:
        database_profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="database",
            profile_key="his-readonly",
            display_name="HIS readonly",
            enabled=True,
            connection={
                "driver": "sqlite",
                "host": "local",
                "port": "0",
                "database": str(Path(self.temp_dir.name) / "readonly.sqlite"),
                "schema": "main",
                "username": "readonly",
                "readonly_policy": "required",
            },
        )
        profiles = [{
            "provider": "database",
            "profile_key": "his-readonly",
            "credential_ref": "his_db_readonly",
            "connection": dict(database_profile.connection),
            "test_connection": dict(database_profile.connection),
        }]

        planned = run_provider_connection_test(
            profiles,
            provider="database",
            profile_key="his-readonly",
            requested_by="manager",
            repository=self.repository,
            authorizer=self.authorizer,
        )

        plan = self.repository.get_action_plan(planned["plan_id"])
        self.assertEqual("database.connection_test", planned["action"])
        self.assertEqual("db-his-readonly", plan["target_alias"])

    def test_github_connection_plan_uses_fixed_connection_target_without_repository(self) -> None:
        github_profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="github",
            profile_key="company-github",
            display_name="Company GitHub",
            enabled=True,
            connection={},
        )
        profiles = [{
            "provider": "github",
            "profile_key": "company-github",
            "credential_ref": "github_access_token",
            "connection": dict(github_profile.connection),
        }]

        planned = run_provider_connection_test(
            profiles,
            provider="github",
            profile_key="company-github",
            requested_by="manager",
            repository=self.repository,
            authorizer=self.authorizer,
        )

        plan = self.repository.get_action_plan(planned["plan_id"])
        self.assertEqual("github.connection_test", planned["action"])
        self.assertEqual("github.connection", plan["target_alias"])

    def test_github_connection_uses_the_manager_readonly_adapter_without_confirmation(self) -> None:
        github_profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="github",
            profile_key="company-github",
            display_name="Company GitHub",
            enabled=True,
            connection={},
        )
        profiles = [{
            "provider": "github",
            "profile_key": "company-github",
            "credential_ref": "github_access_token",
            "connection": dict(github_profile.connection),
        }]
        planned = run_provider_connection_test(
            profiles,
            provider="github",
            profile_key="company-github",
            requested_by="manager",
            repository=self.repository,
            authorizer=self.authorizer,
        )
        transport = FakeGitHubConnectionTransport()
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"github": GitHubProviderAdapter(transport=transport, simulated=True)},
            credential_resolver=lambda _profile_id, _field: "GITHUB_TEST_TOKEN",
        )

        result = run_provider_connection_test(
            profiles,
            provider="github",
            profile_key="company-github",
            requested_by="manager",
            repository=self.repository,
            authorizer=self.authorizer,
            execution_service=service,
            plan_id=planned["plan_id"],
        )

        self.assertEqual("succeeded", result["status"])
        self.assertEqual("github.connection", result["target_alias"])
        self.assertEqual(1, transport.calls)
        self.assertTrue(result["credentials_read"])

    def test_missing_profile_or_untrusted_execution_input_fails_closed(self) -> None:
        missing = run_provider_connection_test(
            [],
            provider="yunxiao",
            profile_key="missing",
            requested_by="manager",
            repository=self.repository,
            authorizer=self.authorizer,
        )
        self.assertEqual("blocked", missing["status"])
        self.assertEqual("provider_profile_not_found", missing["reason"])
        self.assertIsNone(missing["plan_id"])

        with self.assertRaisesRegex(ValueError, "provider_audit_input_invalid"):
            run_provider_connection_test(
                self.profiles,
                provider="undeclared",
                profile_key="company",
                requested_by="manager",
                repository=self.repository,
                authorizer=self.authorizer,
            )
        self.assertEqual([], self.repository.list_action_audits())


if __name__ == "__main__":
    unittest.main()
