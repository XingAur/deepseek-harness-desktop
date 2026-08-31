from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from app import database
from app.manager_provider_repository import ManagerProviderRepository
from app.provider_action_authorization import ProviderActionAuthorizer
from app.provider_execution import ProviderExecutionService
from app.provider_readonly_smoke import (
    LOCAL_READONLY_SMOKE_CONFIRMATION,
    build_provider_readonly_smoke_plan,
    load_provider_readonly_smoke_audit,
    run_provider_readonly_smoke,
)


class FakeGitAdapter:
    def __init__(self) -> None:
        self.calls = 0
        self.requests = []

    def execute(self, request, context):
        self.calls += 1
        self.requests.append(request)
        if context.network_allowed:
            raise AssertionError("readonly git smoke must not receive network permission")
        self.credentials = context.required_credential_fields
        return {"repository_state": "clean"}

    def verify(self, verifier_action, original_write_action, request, target_alias, context):
        raise AssertionError("readonly action must not verify a write")


class ProviderReadonlySmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager.sqlite"
        self.repository = ManagerProviderRepository()
        self.profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="git",
            profile_key="local",
            display_name="Local",
            enabled=True,
            connection={"repository_path": "/opaque/repository"},
        )
        self.profiles = [{
            "provider": "git",
            "profile_key": "local",
            "credential_ref": "identity",
            "connection": {"repository_path": "/opaque/repository"},
        }]
        self.authorizer = ProviderActionAuthorizer(
            self.repository,
            clock=lambda: datetime(2026, 8, 9, 3, 0, tzinfo=timezone.utc),
        )

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_readonly_smoke_plan_is_descriptive_and_awaits_confirmation(self) -> None:
        plan = build_provider_readonly_smoke_plan(self.profiles)

        item = plan["items"][0]
        self.assertEqual("awaiting_confirmation", item["status"])
        self.assertEqual("git.readonly_smoke", item["action"])
        self.assertEqual("provider_execution_service", item["adapter"])
        self.assertFalse(plan["credentials_read"])
        self.assertFalse(plan["external_calls"])

    def test_run_creates_manager_plan_without_subprocess_or_separate_audit(self) -> None:
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)) as run:
            result = run_provider_readonly_smoke(
                self.profiles,
                provider="git",
                profile_key="local",
                requested_by="manager",
                confirmation_text=LOCAL_READONLY_SMOKE_CONFIRMATION,
                repository=self.repository,
                authorizer=self.authorizer,
            )

        self.assertEqual("awaiting_confirmation", result["status"])
        self.assertEqual("git.readonly_smoke", result["action"])
        self.assertEqual("planned", self.repository.get_action_plan(result["plan_id"])["state"])
        self.assertFalse(run.called)
        self.assertEqual([], self.repository.list_action_audits())

    def test_confirmed_readonly_smoke_delegates_only_to_service(self) -> None:
        planned = run_provider_readonly_smoke(
            self.profiles,
            provider="git",
            profile_key="local",
            requested_by="manager",
            confirmation_text=LOCAL_READONLY_SMOKE_CONFIRMATION,
            repository=self.repository,
            authorizer=self.authorizer,
        )
        plan = self.repository.get_action_plan(planned["plan_id"])
        # The Manager smoke target identifies a configured Git profile, not a
        # repository scope.  Its request deliberately contains only timeout.
        self.assertEqual("git.local", plan["target_alias"])
        authorization = self.authorizer.confirm(
            planned["plan_id"], actor="manager", ttl_seconds=60
        )
        adapter = FakeGitAdapter()
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"git": adapter},
        )

        result = run_provider_readonly_smoke(
            self.profiles,
            provider="git",
            profile_key="local",
            requested_by="manager",
            confirmation_text=LOCAL_READONLY_SMOKE_CONFIRMATION,
            repository=self.repository,
            authorizer=self.authorizer,
            execution_service=service,
            plan_id=planned["plan_id"],
            authorization=authorization,
        )

        self.assertEqual("succeeded", result["status"])
        self.assertEqual(1, adapter.calls)
        self.assertEqual((), adapter.credentials)
        self.assertEqual(
            {"timeout_seconds": 5}, adapter.requests[0].parameters
        )
        audit = load_provider_readonly_smoke_audit(repository=self.repository)
        self.assertEqual(1, audit["record_count"])
        self.assertEqual("succeeded", audit["records"][0]["status"])

    def test_non_git_smoke_is_blocked_without_plan_or_audit(self) -> None:
        result = run_provider_readonly_smoke(
            [{"provider": "yunxiao", "profile_key": "company", "credential_ref": "pat", "connection": {}}],
            provider="yunxiao",
            profile_key="company",
            requested_by="manager",
            confirmation_text=LOCAL_READONLY_SMOKE_CONFIRMATION,
            repository=self.repository,
            authorizer=self.authorizer,
        )

        self.assertEqual("blocked", result["status"])
        self.assertEqual("provider_readonly_smoke_adapter_not_registered", result["reason"])
        self.assertIsNone(result["plan_id"])
        self.assertEqual([], self.repository.list_action_audits())


if __name__ == "__main__":
    unittest.main()
