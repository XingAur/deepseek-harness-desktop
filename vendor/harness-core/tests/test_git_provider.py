from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from app import database
from app.manager_provider_repository import ManagerProviderRepository
from app.provider_action_authorization import ProviderActionAuthorizer
from app.provider_execution import ProviderExecutionContext, ProviderExecutionRequest, ProviderExecutionService
from app.providers.git import GitProviderAdapter, validate_git_action_parameters
from app.repository_scope import RepositoryScope


class GitProviderAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_path = Path(self.temp_dir.name) / "repo"
        self.repo_path.mkdir()
        self._git("init")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "Harness Test")
        (self.repo_path / "README.md").write_text("initial\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "initial")
        self._git("checkout", "-b", "feature-work")
        self.adapter = GitProviderAdapter({
            "local-repo": RepositoryScope("local-repo", self.repo_path, allowed_paths=(".",), remotes=(("origin", "https://gitlab.example.test/group/project.git"),)),
        })

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(self.repo_path), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return completed.stdout

    def _context(self) -> ProviderExecutionContext:
        return ProviderExecutionContext(
            profile_id=1,
            required_credential_fields=(),
            network_allowed=False,
            credential_resolver=lambda _profile_id, _field: "",
        )

    def _authorized_context(self) -> ProviderExecutionContext:
        context = self._context()
        context.mark_authorization_consumed()
        return context

    def _request(self, action: str, **parameters: object) -> ProviderExecutionRequest:
        return ProviderExecutionRequest(
            plan_id=1,
            actor="manager",
            action=action,
            parameters={"repository_alias": "local-repo", **parameters},
        )

    def test_read_actions_use_bounded_argument_list_output_and_never_a_shell_string(self) -> None:
        status = self.adapter.execute(self._request("repo.status.read"), self._context())
        log = self.adapter.execute(self._request("repo.log.read", limit=1), self._context())
        diff = self.adapter.execute(self._request("repo.diff.read"), self._context())

        self.assertEqual("git", status["source"])
        self.assertEqual("repo.status.read", status["action"])
        self.assertEqual(0, status["changed_file_count"])
        self.assertEqual(1, log["commit_count"])
        self.assertEqual("repo.diff.read", diff["action"])

    def test_read_results_do_not_return_raw_git_output(self) -> None:
        result = self.adapter.execute(self._request("repo.status.read"), self._context())
        self.assertEqual({"source", "action", "changed_file_count"}, set(result))

    def test_history_and_remote_operations_are_explicit_plan_only(self) -> None:
        head = self.adapter.capture_branch_base("local-repo")
        request = self._request(
            "git.operation.plan",
            operation="merge.local",
            branch_name="feature-work",
            expected_head_sha=head,
            source_ref="refs/heads/feature-work",
            strategy="ff-only",
        )
        result = self.adapter.execute(request, self._context())
        self.assertEqual("plan_only", result["execution_status"])
        self.assertEqual("merge.local", result["operation"])
        self.assertEqual(head, self.adapter.capture_branch_base("local-repo"))
        self.assertIn("确认", str(result["required_confirmation"]))

        with self.assertRaises(ValueError):
            self.adapter.execute(
                self._request(
                    "git.operation.plan",
                    operation="remote.push",
                    branch_name="feature-work",
                    expected_head_sha=head,
                    remote_alias="origin",
                    source_ref="refs/heads/feature-work",
                    target_ref="refs/heads/main",
                    expected_remote_sha=head,
                    force=True,
                ),
                self._context(),
            )

    def test_operation_plan_reports_preflight_state_and_blocks_dirty_drift(self) -> None:
        head = self.adapter.capture_branch_base("local-repo")
        request = self._request(
            "git.operation.plan",
            operation="merge.local",
            branch_name="feature-work",
            expected_head_sha=head,
            source_ref="refs/heads/feature-work",
            strategy="ff-only",
        )

        rendered = self.adapter.render_plan(request)
        self.assertEqual("ready", rendered["preflight"]["status"])
        self.assertTrue(rendered["preflight"]["worktree_clean"])
        self.assertEqual(head, rendered["preflight"]["current_head_sha"])
        self.assertTrue(rendered["preflight"]["target_commit_exists"])

        (self.repo_path / "README.md").write_text("dirty\n", encoding="utf-8")
        result = self.adapter.execute(request, self._context())
        self.assertEqual("blocked", result["preflight"]["status"])
        self.assertIn("git_worktree_not_clean", result["preflight"]["blockers"])
        self.assertEqual(head, self.adapter.capture_branch_base("local-repo"))

    def test_operation_plan_requires_remote_readback_and_blocks_head_drift(self) -> None:
        head = self.adapter.capture_branch_base("local-repo")
        remote_request = self._request(
            "git.operation.plan",
            operation="remote.push",
            branch_name="feature-work",
            expected_head_sha=head,
            remote_alias="origin",
            source_ref="refs/heads/feature-work",
            target_ref="refs/heads/main",
            expected_remote_sha=head,
            force=False,
        )

        remote_plan = self.adapter.render_plan(remote_request)
        self.assertEqual("needs_remote_evidence", remote_plan["preflight"]["status"])
        self.assertEqual("requires_remote_readback", remote_plan["preflight"]["remote_state"])
        self.assertEqual([], remote_plan["preflight"]["blockers"])

        drift_request = self._request(
            "git.operation.plan",
            operation="merge.local",
            branch_name="feature-work",
            expected_head_sha="a" * 40,
            source_ref="refs/heads/feature-work",
            strategy="ff-only",
        )
        drift_plan = self.adapter.render_plan(drift_request)
        self.assertEqual("blocked", drift_plan["preflight"]["status"])
        self.assertIn("git_expected_head_drift", drift_plan["preflight"]["blockers"])

    def test_local_history_operation_requires_one_use_authorization(self) -> None:
        head = self.adapter.capture_branch_base("local-repo")
        request = self._request(
            "reset.local",
            branch_name="feature-work",
            expected_head_sha=head,
            target_sha=head,
            mode="soft",
        )
        with self.assertRaisesRegex(PermissionError, "git_operation_authorization_required"):
            self.adapter.execute(request, self._context())
        self.assertEqual(head, self.adapter.capture_branch_base("local-repo"))

    def test_local_reset_executes_with_fixed_scope_and_readback(self) -> None:
        first = self._git("rev-parse", "HEAD").strip()
        (self.repo_path / "README.md").write_text("second\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "second")
        second = self._git("rev-parse", "HEAD").strip()
        request = self._request(
            "reset.local",
            branch_name="feature-work",
            expected_head_sha=second,
            target_sha=first,
            mode="hard",
        )

        result = self.adapter.execute(request, self._authorized_context())

        self.assertEqual("applied", result["execution_status"])
        self.assertEqual(first, result["after_head_sha"])
        self.assertEqual(first, self._git("rev-parse", "HEAD").strip())
        self.assertEqual("initial\n", (self.repo_path / "README.md").read_text(encoding="utf-8"))
        self.assertEqual(0, self.adapter.execute(self._request("repo.status.read"), self._context())["changed_file_count"])

    def test_local_reset_service_requires_confirmation_and_reports_verified_effect(self) -> None:
        previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager-local-reset.sqlite"
        try:
            repository = ManagerProviderRepository()
            profile = repository.upsert_profile(
                scope_type="local", scope_key="default", provider="git", profile_key="local",
                display_name="Local", enabled=True,
                connection={"repository_path": str(self.repo_path)},
            )
            authorizer = ProviderActionAuthorizer(
                repository, clock=lambda: datetime(2026, 8, 9, tzinfo=timezone.utc)
            )
            service = ProviderExecutionService(repository, authorizer, adapters={"git": self.adapter})
            first = self._git("rev-parse", "HEAD").strip()
            (self.repo_path / "README.md").write_text("second\n", encoding="utf-8")
            self._git("add", "README.md")
            self._git("commit", "-m", "second")
            second = self._git("rev-parse", "HEAD").strip()
            parameters = {
                "repository_alias": "local-repo",
                "branch_name": "feature-work",
                "expected_head_sha": second,
                "target_sha": first,
                "mode": "hard",
            }
            plan = authorizer.create_plan(
                profile_id=profile.id, action="reset.local", target_alias="local-repo",
                parameters=parameters, requested_by="manager",
            )
            request = ProviderExecutionRequest(plan.id, "manager", "reset.local", parameters)

            blocked = service.execute(None, request)
            self.assertEqual("authorization_required", blocked["reason"])
            authorization = authorizer.confirm(plan.id, actor="manager", ttl_seconds=60)
            result = service.execute(authorization, request)

            self.assertEqual("succeeded", result["status"])
            self.assertEqual("verified_applied", result["write_effect_status"])
            self.assertTrue(result["write_performed"])
            self.assertEqual(first, self._git("rev-parse", "HEAD").strip())
            reused = service.execute(authorization, request)
            self.assertEqual("authorization_reused", reused["reason"])
        finally:
            database.DB_PATH = previous_db_path

    def test_local_cherry_pick_applies_only_the_authorized_commit(self) -> None:
        base = self._git("rev-parse", "HEAD").strip()
        self._git("checkout", "-b", "side-work")
        (self.repo_path / "side.txt").write_text("side\n", encoding="utf-8")
        self._git("add", "side.txt")
        self._git("commit", "-m", "side change")
        side_commit = self._git("rev-parse", "HEAD").strip()
        self._git("checkout", "feature-work")
        request = self._request(
            "cherry-pick.local",
            branch_name="feature-work",
            expected_head_sha=base,
            commit_sha=side_commit,
        )

        result = self.adapter.execute(request, self._authorized_context())

        self.assertEqual("applied", result["execution_status"])
        self.assertEqual(base, result["before_head_sha"])
        self.assertNotEqual(base, result["after_head_sha"])
        self.assertEqual("side change", self._git("log", "-1", "--format=%s").strip())
        self.assertEqual("side\n", (self.repo_path / "side.txt").read_text(encoding="utf-8"))
        self.assertEqual(0, self.adapter.execute(self._request("repo.status.read"), self._context())["changed_file_count"])

    def test_local_merge_ff_only_applies_the_authorized_source_ref(self) -> None:
        base = self._git("rev-parse", "HEAD").strip()
        self._git("checkout", "-b", "merge-source")
        (self.repo_path / "merge.txt").write_text("merged\n", encoding="utf-8")
        self._git("add", "merge.txt")
        self._git("commit", "-m", "merge change")
        source_commit = self._git("rev-parse", "HEAD").strip()
        self._git("checkout", "feature-work")
        request = self._request(
            "merge.local",
            branch_name="feature-work",
            expected_head_sha=base,
            source_ref="refs/heads/merge-source",
            strategy="ff-only",
        )

        result = self.adapter.execute(request, self._authorized_context())

        self.assertEqual("applied", result["execution_status"])
        self.assertEqual(source_commit, result["after_head_sha"])
        self.assertEqual(source_commit, self._git("rev-parse", "HEAD").strip())
        self.assertEqual("merged\n", (self.repo_path / "merge.txt").read_text(encoding="utf-8"))

    def test_local_cherry_pick_conflict_stops_without_automatic_resolution(self) -> None:
        self._git("checkout", "-b", "conflict-source")
        (self.repo_path / "README.md").write_text("source\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "source conflict")
        source_commit = self._git("rev-parse", "HEAD").strip()
        self._git("checkout", "feature-work")
        (self.repo_path / "README.md").write_text("target\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "target conflict")
        target_head = self._git("rev-parse", "HEAD").strip()
        request = self._request(
            "cherry-pick.local",
            branch_name="feature-work",
            expected_head_sha=target_head,
            commit_sha=source_commit,
        )

        with self.assertRaisesRegex(RuntimeError, "git_operation_conflict"):
            self.adapter.execute(request, self._authorized_context())
        self.assertEqual(target_head, self._git("rev-parse", "HEAD").strip())
        status = self._git("status", "--porcelain")
        self.assertTrue(status)

    def test_operation_plan_validator_rejects_force_push_and_unbounded_refs(self) -> None:
        head = self.adapter.capture_branch_base("local-repo")
        valid = {
            "repository_alias": "local-repo",
            "operation": "remote.push",
            "branch_name": "feature-work",
            "expected_head_sha": head,
            "remote_alias": "origin",
            "source_ref": "refs/heads/feature-work",
            "target_ref": "refs/heads/main",
            "expected_remote_sha": head,
            "force": False,
        }
        validate_git_action_parameters("git.operation.plan", "local-repo", valid)
        with self.assertRaises(ValueError):
            validate_git_action_parameters(
                "git.operation.plan", "local-repo", {**valid, "force": True}
            )
        with self.assertRaises(ValueError):
            validate_git_action_parameters(
                "git.operation.plan", "local-repo", {**valid, "target_ref": "refs/heads/a..b"}
            )

        local = {
            "repository_alias": "local-repo",
            "branch_name": "feature-work",
            "expected_head_sha": head,
            "target_sha": head,
            "mode": "hard",
            "allow_dirty": True,
        }
        with self.assertRaisesRegex(ValueError, "git_dirty_override_forbidden"):
            validate_git_action_parameters("reset.local", "local-repo", local)

    def test_injection_and_unscoped_paths_are_rejected_before_git_execution(self) -> None:
        for request in (
            self._request("branch.create", branch_name="next; touch /tmp/pwned"),
            self._request("repo.diff.read", file_list=["../outside.txt"]),
            self._request("repo.log.read", limit="1; rm -rf /"),
        ):
            with self.subTest(action=request.action):
                with self.assertRaises(ValueError):
                    self.adapter.execute(request, self._context())

    def test_branch_and_commit_need_one_confirmed_plan_and_return_verified_sha(self) -> None:
        previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager.sqlite"
        try:
            repository = ManagerProviderRepository()
            profile = repository.upsert_profile(
                scope_type="local", scope_key="default", provider="git", profile_key="local",
                display_name="Local", enabled=True, connection={"repository_path": str(self.repo_path)},
            )
            authorizer = ProviderActionAuthorizer(
                repository, clock=lambda: datetime(2026, 8, 9, tzinfo=timezone.utc)
            )
            service = ProviderExecutionService(repository, authorizer, adapters={"git": self.adapter})
            parameters = {"repository_alias": "local-repo", "branch_name": "feature-safe", "expected_base_sha": self.adapter.capture_branch_base("local-repo")}
            plan = authorizer.create_plan(
                profile_id=profile.id, action="branch.create", target_alias="local-repo",
                parameters=parameters, requested_by="manager",
            )
            request = ProviderExecutionRequest(plan.id, "manager", "branch.create", parameters)
            rendered = service.render_plan(request)
            self.assertEqual("local-repo", rendered["target_alias"])
            self.assertEqual(plan.parameter_hash, rendered["parameter_hash"])

            blocked = service.execute(None, request)
            self.assertEqual("authorization_required", blocked["reason"])
            self.assertNotIn("feature-safe", self._git("branch", "--format=%(refname:short)").splitlines())

            authorization = authorizer.confirm(plan.id, actor="manager", ttl_seconds=60)
            result = service.execute(authorization, request)
            self.assertEqual("succeeded", result["status"])
            self.assertEqual("verified_applied", result["write_effect_status"])
            self.assertIn("feature-safe", self._git("branch", "--format=%(refname:short)").splitlines())
            reused = service.execute(authorization, request)
            self.assertEqual("authorization_reused", reused["reason"])
            self.assertIn("feature-safe", self._git("branch", "--format=%(refname:short)").splitlines())

            (self.repo_path / "README.md").write_text("changed\n", encoding="utf-8")
            commit_parameters = {"repository_alias": "local-repo", "branch_name": "feature-work", "file_list": ["README.md"], "message": "safe change", **self.adapter.capture_commit_evidence("local-repo", branch_name="feature-work", file_list=["README.md"])}
            commit_plan = authorizer.create_plan(
                profile_id=profile.id, action="commit.create", target_alias="local-repo",
                parameters=commit_parameters, requested_by="manager",
            )
            commit_request = ProviderExecutionRequest(commit_plan.id, "manager", "commit.create", commit_parameters)
            commit_authorization = authorizer.confirm(commit_plan.id, actor="manager", ttl_seconds=60)
            committed = service.execute(commit_authorization, commit_request)
            self.assertEqual("succeeded", committed["status"])
            self.assertEqual(40, len(committed["result_summary"]["commit_sha"]))
            self.assertEqual("safe change", self._git("log", "-1", "--format=%s").strip())
        finally:
            database.DB_PATH = previous_db_path

    def test_fetch_is_denied_without_network_capability_before_git_runs(self) -> None:
        with self.assertRaisesRegex(PermissionError, "git_network_not_allowed"):
            self.adapter.execute(self._request("remote.fetch", remote_alias="origin", ref_name="refs/heads/main"), self._context())

    def test_remote_push_uses_one_consumed_authorization_and_a_fixed_simulated_refspec(self) -> None:
        head = self.adapter.capture_branch_base("local-repo")
        calls: list[dict[str, object]] = []
        adapter = GitProviderAdapter(
            self.adapter._scopes,
            push_transport=lambda **kwargs: calls.append(kwargs),
            simulated=True,
        )
        request = self._request(
            "remote.push",
            branch_name="feature-work",
            expected_head_sha=head,
            remote_alias="origin",
            source_ref="refs/heads/feature-work",
            target_ref="refs/heads/feature-published",
            expected_remote_sha=head,
            force=False,
        )
        context = ProviderExecutionContext(
            profile_id=1,
            required_credential_fields=(),
            network_allowed=True,
            credential_resolver=lambda _profile_id, _field: "",
        )

        with self.assertRaisesRegex(PermissionError, "git_operation_authorization_required"):
            adapter.execute(request, context)
        context.mark_authorization_consumed()
        result = adapter.execute(request, context)

        self.assertEqual("remote.push", result["action"])
        self.assertEqual("simulated", result["execution_provenance"])
        self.assertEqual(
            [{
                "url": "https://gitlab.example.test/group/project.git",
                "refspec": "refs/heads/feature-work:refs/heads/feature-published",
                "timeout_seconds": 5,
                "follow_redirects": False,
            }],
            calls,
        )

    def test_remote_push_allows_a_plan_to_create_an_absent_task_branch(self) -> None:
        head = self.adapter.capture_branch_base("local-repo")
        calls: list[dict[str, object]] = []
        adapter = GitProviderAdapter(
            self.adapter._scopes,
            push_transport=lambda **kwargs: calls.append(kwargs),
            simulated=True,
        )
        request = self._request(
            "remote.push",
            branch_name="feature-work",
            expected_head_sha=head,
            remote_alias="origin",
            source_ref="refs/heads/feature-work",
            target_ref="refs/heads/feature-new-task",
            expected_remote_sha=None,
            force=False,
        )
        context = ProviderExecutionContext(
            profile_id=1,
            required_credential_fields=(),
            network_allowed=True,
            credential_resolver=lambda _profile_id, _field: "",
        )
        context.mark_authorization_consumed()

        result = adapter.execute(request, context)

        self.assertEqual("remote.push", result["action"])
        self.assertEqual(
            "refs/heads/feature-work:refs/heads/feature-new-task",
            calls[0]["refspec"],
        )

    def test_confirmed_branch_plan_is_blocked_when_expected_base_moves_before_execution(self) -> None:
        previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager-mismatch.sqlite"
        try:
            repository = ManagerProviderRepository()
            profile = repository.upsert_profile(
                scope_type="local", scope_key="default", provider="git", profile_key="local",
                display_name="Local", enabled=True, connection={"repository_path": str(self.repo_path)},
            )
            authorizer = ProviderActionAuthorizer(repository, clock=lambda: datetime(2026, 8, 9, tzinfo=timezone.utc))
            service = ProviderExecutionService(repository, authorizer, adapters={"git": self.adapter})
            parameters = {"repository_alias": "local-repo", "branch_name": "feature-stale", "expected_base_sha": self.adapter.capture_branch_base("local-repo")}
            plan = authorizer.create_plan(profile_id=profile.id, action="branch.create", target_alias="local-repo", parameters=parameters, requested_by="manager")
            request = ProviderExecutionRequest(plan.id, "manager", "branch.create", parameters)
            authorization = authorizer.confirm(plan.id, actor="manager", ttl_seconds=60)
            (self.repo_path / "advance.txt").write_text("advance\n", encoding="utf-8")
            self._git("add", "advance.txt")
            self._git("commit", "-m", "advance")

            result = service.execute(authorization, request)

            self.assertEqual("failed", result["status"])
            self.assertEqual("unknown", result["write_effect_status"])
            self.assertNotIn("feature-stale", self._git("branch", "--format=%(refname:short)").splitlines())
        finally:
            database.DB_PATH = previous_db_path

    def test_fetch_runs_only_after_confirmation_and_uses_explicit_simulated_transport(self) -> None:
        previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager-fetch.sqlite"
        calls: list[dict[str, object]] = []
        try:
            repository = ManagerProviderRepository()
            profile = repository.upsert_profile(
                scope_type="local", scope_key="default", provider="git", profile_key="local",
                display_name="Local", enabled=True, connection={"repository_path": str(self.repo_path)},
            )
            adapter = GitProviderAdapter(self.adapter._scopes, fetch_transport=lambda **kwargs: calls.append(kwargs), simulated=True)
            authorizer = ProviderActionAuthorizer(repository, clock=lambda: datetime(2026, 8, 9, tzinfo=timezone.utc))
            service = ProviderExecutionService(repository, authorizer, adapters={"git": adapter})
            parameters = {"repository_alias": "local-repo", "remote_alias": "origin", "ref_name": "refs/heads/main"}
            plan = authorizer.create_plan(profile_id=profile.id, action="remote.fetch", target_alias="local-repo", parameters=parameters, requested_by="manager")
            request = ProviderExecutionRequest(plan.id, "manager", "remote.fetch", parameters)

            blocked = service.execute(None, request)
            self.assertEqual("authorization_required", blocked["reason"])
            self.assertEqual([], calls)
            authorization = authorizer.confirm(plan.id, actor="manager", ttl_seconds=60)
            result = service.execute(authorization, request)

            self.assertEqual("succeeded", result["status"])
            self.assertFalse(result["external_calls"])
            self.assertEqual(1, result["simulated_dispatch_count"])
            self.assertEqual("simulated", result["execution_provenance"])
            self.assertEqual([{"url": "https://gitlab.example.test/group/project.git", "refspec": "refs/heads/main:refs/remotes/origin/main", "timeout_seconds": 5, "follow_redirects": False}], calls)
        finally:
            database.DB_PATH = previous_db_path

    def test_live_fetch_audit_callback_incident_is_never_recorded_as_zero_external_calls(self) -> None:
        previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager-fetch-audit-incident.sqlite"
        try:
            repository = ManagerProviderRepository()
            profile = repository.upsert_profile(scope_type="local", scope_key="default", provider="git", profile_key="local", display_name="Local", enabled=True, connection={"repository_path": str(self.repo_path)})
            authorizer = ProviderActionAuthorizer(repository, clock=lambda: datetime(2026, 8, 9, tzinfo=timezone.utc))
            service = ProviderExecutionService(repository, authorizer, adapters={"git": self.adapter})
            parameters = {"repository_alias": "local-repo", "remote_alias": "origin", "ref_name": "refs/heads/main"}
            plan = authorizer.create_plan(profile_id=profile.id, action="remote.fetch", target_alias="local-repo", parameters=parameters, requested_by="manager")
            authorization = authorizer.confirm(plan.id, actor="manager", ttl_seconds=60)
            request = ProviderExecutionRequest(plan.id, "manager", "remote.fetch", parameters)

            @contextlib.contextmanager
            def fake_snapshot(_scope):
                yield self.repo_path

            def fake_git(_snapshot, arguments, _timeout, **kwargs):
                if arguments[0] == "show-ref":
                    return subprocess.CompletedProcess([], 0, ("a" * 40 + "\n").encode(), b"")
                callback = kwargs.get("on_started")
                if callback is not None:
                    callback()
                return subprocess.CompletedProcess([], 0, b"", b"")

            with mock.patch.object(self.adapter, "_preflight"), mock.patch.object(self.adapter, "_execution_snapshot", fake_snapshot), mock.patch.object(self.adapter, "_read_ref", return_value=None), mock.patch.object(self.adapter, "_git_snapshot", side_effect=fake_git), mock.patch.object(ProviderExecutionContext, "record_network_dispatch", side_effect=RuntimeError("audit unavailable")):
                result = service.execute(authorization, request)

            self.assertEqual("failed", result["status"])
            self.assertIsNone(result["external_calls"])
            self.assertEqual(1, result["network_dispatch_incident_count"])
            self.assertEqual("unknown", result["execution_provenance"])
            self.assertIsNone(result["write_performed"])
            self.assertEqual("unknown", result["write_effect_status"])
        finally:
            database.DB_PATH = previous_db_path

    def test_live_fetch_source_publish_failures_are_recorded_as_unknown_local_effects(self) -> None:
        previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager-fetch-publish-failure.sqlite"
        try:
            repository = ManagerProviderRepository()
            profile = repository.upsert_profile(scope_type="local", scope_key="default", provider="git", profile_key="local", display_name="Local", enabled=True, connection={"repository_path": str(self.repo_path)})
            authorizer = ProviderActionAuthorizer(repository, clock=lambda: datetime(2026, 8, 9, tzinfo=timezone.utc))
            service = ProviderExecutionService(repository, authorizer, adapters={"git": self.adapter})

            @contextlib.contextmanager
            def fake_snapshot(_scope):
                yield self.repo_path

            def fake_git(_snapshot, arguments, _timeout, **kwargs):
                if arguments[0] == "show-ref":
                    return next(show_refs)
                callback = kwargs.get("on_started")
                if callback is not None:
                    callback()
                return subprocess.CompletedProcess([], 0, b"", b"")

            for failed_phase in ("copy", "publish"):
                with self.subTest(failed_phase=failed_phase):
                    parameters = {"repository_alias": "local-repo", "remote_alias": "origin", "ref_name": "refs/heads/main"}
                    plan = authorizer.create_plan(profile_id=profile.id, action="remote.fetch", target_alias="local-repo", parameters=parameters, requested_by="manager")
                    authorization = authorizer.confirm(plan.id, actor="manager", ttl_seconds=60)
                    request = ProviderExecutionRequest(plan.id, "manager", "remote.fetch", parameters)
                    show_refs = iter((
                        subprocess.CompletedProcess([], 0, ("a" * 40 + "\n").encode(), b""),
                        subprocess.CompletedProcess([], 0, ("b" * 40 + "\n").encode(), b""),
                    ))
                    copy_failure = RuntimeError("copy failed") if failed_phase == "copy" else None
                    publish_failure = RuntimeError("publish failed") if failed_phase == "publish" else None
                    with mock.patch.object(self.adapter, "_preflight"), mock.patch.object(self.adapter, "_execution_snapshot", fake_snapshot), mock.patch.object(self.adapter, "_read_ref", return_value=None), mock.patch.object(self.adapter, "_git_snapshot", side_effect=fake_git), mock.patch.object(self.adapter, "_copy_new_objects", side_effect=copy_failure) as copied, mock.patch.object(self.adapter, "_publish_ref", side_effect=publish_failure) as published:
                        result = service.execute(authorization, request)

                    self.assertEqual("failed", result["status"])
                    self.assertTrue(result["external_calls"])
                    self.assertIsNone(result["write_performed"])
                    self.assertEqual("unknown", result["write_effect_status"])
                    self.assertTrue(copied.called)
                    self.assertEqual(failed_phase == "publish", published.called)
        finally:
            database.DB_PATH = previous_db_path

    def test_confirmed_commit_plan_rejects_final_content_drift_before_ref_mutation(self) -> None:
        previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager-content-drift.sqlite"
        try:
            repository = ManagerProviderRepository()
            profile = repository.upsert_profile(scope_type="local", scope_key="default", provider="git", profile_key="local", display_name="Local", enabled=True, connection={"repository_path": str(self.repo_path)})
            authorizer = ProviderActionAuthorizer(repository, clock=lambda: datetime(2026, 8, 9, tzinfo=timezone.utc))
            service = ProviderExecutionService(repository, authorizer, adapters={"git": self.adapter})
            (self.repo_path / "README.md").write_text("reviewed content\n", encoding="utf-8")
            parameters = {"repository_alias": "local-repo", "branch_name": "feature-work", "file_list": ["README.md"], "message": "reviewed change", **self.adapter.capture_commit_evidence("local-repo", branch_name="feature-work", file_list=["README.md"])}
            plan = authorizer.create_plan(profile_id=profile.id, action="commit.create", target_alias="local-repo", parameters=parameters, requested_by="manager")
            request = ProviderExecutionRequest(plan.id, "manager", "commit.create", parameters)
            authorization = authorizer.confirm(plan.id, actor="manager", ttl_seconds=60)
            head_before = self._git("rev-parse", "HEAD").strip()
            (self.repo_path / "README.md").write_text("changed after review\n", encoding="utf-8")

            result = service.execute(authorization, request)

            self.assertEqual("failed", result["status"])
            self.assertEqual("unknown", result["write_effect_status"])
            self.assertEqual(head_before, self._git("rev-parse", "HEAD").strip())
        finally:
            database.DB_PATH = previous_db_path

    def test_commit_stops_before_ref_or_index_publish_when_standard_index_lock_is_held(self) -> None:
        (self.repo_path / "README.md").write_text("reviewed content\n", encoding="utf-8")
        parameters = {
            "repository_alias": "local-repo",
            "branch_name": "feature-work",
            "file_list": ["README.md"],
            "message": "reviewed change",
            **self.adapter.capture_commit_evidence("local-repo", branch_name="feature-work", file_list=["README.md"]),
        }
        head_before = self._git("rev-parse", "HEAD").strip()
        index_before = (self.repo_path / ".git" / "index").read_bytes()
        lock = self.repo_path / ".git" / "index.lock"
        lock.write_bytes(b"another git writer owns this lock")
        try:
            with self.assertRaisesRegex(ValueError, "git_publish_lock_unavailable"):
                self.adapter.execute(self._request("commit.create", **parameters), self._context())
        finally:
            lock.unlink(missing_ok=True)

        self.assertEqual(head_before, self._git("rev-parse", "HEAD").strip())
        self.assertEqual(index_before, (self.repo_path / ".git" / "index").read_bytes())
        self.assertFalse((self.repo_path / ".git" / "harness-transaction.json").exists())

    def test_commit_rejects_staged_content_even_when_it_has_the_same_authorized_path(self) -> None:
        (self.repo_path / "README.md").write_text("reviewed content\n", encoding="utf-8")
        parameters = {
            "repository_alias": "local-repo",
            "branch_name": "feature-work",
            "file_list": ["README.md"],
            "message": "reviewed change",
            **self.adapter.capture_commit_evidence("local-repo", branch_name="feature-work", file_list=["README.md"]),
        }
        self._git("add", "README.md")
        head_before = self._git("rev-parse", "HEAD").strip()
        index_before = (self.repo_path / ".git" / "index").read_bytes()

        with self.assertRaisesRegex(ValueError, "git_staged_data_not_allowed"):
            self.adapter.execute(self._request("commit.create", **parameters), self._context())

        self.assertEqual(head_before, self._git("rev-parse", "HEAD").strip())
        self.assertEqual(index_before, (self.repo_path / ".git" / "index").read_bytes())

    def test_ref_publish_io_failure_leaves_a_durable_recovery_journal_without_rollback(self) -> None:
        (self.repo_path / "README.md").write_text("reviewed content\n", encoding="utf-8")
        parameters = {
            "repository_alias": "local-repo",
            "branch_name": "feature-work",
            "file_list": ["README.md"],
            "message": "reviewed change",
            **self.adapter.capture_commit_evidence("local-repo", branch_name="feature-work", file_list=["README.md"]),
        }
        head_before = self._git("rev-parse", "HEAD").strip()
        index_before = (self.repo_path / ".git" / "index").read_bytes()
        original_replace = os.replace

        def fail_ref_publish(source, destination, *args, **kwargs):
            if source == "feature-work.lock" and destination == "feature-work":
                raise OSError("injected ref publication failure")
            return original_replace(source, destination, *args, **kwargs)

        with mock.patch("app.providers.git.os.replace", side_effect=fail_ref_publish):
            with self.assertRaisesRegex(RuntimeError, "git_publish_unknown"):
                self.adapter.execute(self._request("commit.create", **parameters), self._context())

        journal = self.repo_path / ".git" / "harness-transaction.json"
        backup = self.repo_path / ".git" / "harness-transaction-old-index"
        self.assertTrue(journal.exists())
        self.assertTrue(backup.exists())
        self.assertEqual(head_before, self._git("rev-parse", "HEAD").strip())
        self.assertEqual(index_before, (self.repo_path / ".git" / "index").read_bytes())
        self.assertEqual(index_before, backup.read_bytes())
        with self.assertRaisesRegex(ValueError, "git_transaction_recovery_required"):
            self.adapter.execute(self._request("repo.status.read"), self._context())
        journal.unlink()
        backup.unlink()

    def test_index_install_failure_after_ref_publication_preserves_recovery_material_and_user_index(self) -> None:
        (self.repo_path / "README.md").write_text("reviewed content\n", encoding="utf-8")
        parameters = {
            "repository_alias": "local-repo",
            "branch_name": "feature-work",
            "file_list": ["README.md"],
            "message": "reviewed change",
            **self.adapter.capture_commit_evidence("local-repo", branch_name="feature-work", file_list=["README.md"]),
        }
        index_before = (self.repo_path / ".git" / "index").read_bytes()
        parent = self._git("rev-parse", "HEAD").strip()
        original_replace = os.replace

        def fail_index_install(source, destination, *args, **kwargs):
            if source == "index.lock" and destination == "index":
                raise OSError("injected index installation failure")
            return original_replace(source, destination, *args, **kwargs)

        with mock.patch("app.providers.git.os.replace", side_effect=fail_index_install):
            with self.assertRaisesRegex(RuntimeError, "git_publish_unknown"):
                self.adapter.execute(self._request("commit.create", **parameters), self._context())

        journal = self.repo_path / ".git" / "harness-transaction.json"
        backup = self.repo_path / ".git" / "harness-transaction-old-index"
        self.assertTrue(journal.exists())
        self.assertTrue(backup.exists())
        self.assertTrue((self.repo_path / ".git" / "index.lock").exists())
        self.assertEqual(index_before, (self.repo_path / ".git" / "index").read_bytes())
        self.assertEqual(index_before, backup.read_bytes())
        self.assertNotEqual(parent, self._git("rev-parse", "HEAD").strip())
        with self.assertRaisesRegex(ValueError, "git_transaction_recovery_required"):
            self.adapter.execute(self._request("repo.status.read"), self._context())


if __name__ == "__main__":
    unittest.main()
