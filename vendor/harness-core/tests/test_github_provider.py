from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import app.providers.github as github_module
from app import database
from app.manager_provider_repository import ManagerProviderRepository
from app.provider_action_authorization import ProviderActionAuthorizer
from app.provider_execution import (
    ProviderExecutionContext,
    ProviderExecutionRequest,
    ProviderExecutionService,
)
from app.provider_execution import ACTION_DESCRIPTORS
from app.providers.github import GitHubHttpResponse, GitHubProviderAdapter
from app.providers.registry import build_manager_adapter_registry
from app.provider_capability_status import build_provider_capability_status
from app.provider_field_schema import provider_field_specs


BASE = "https://api.github.com"
HEADERS = {
    "Accept": "application/vnd.github+json",
    "Authorization": "Bearer github-read-test-token",
    "User-Agent": "his-harness-readonly/1",
    "X-GitHub-Api-Version": "2022-11-28",
}


class StrictTransport:
    def __init__(self, expected: list[tuple[str, str, dict[str, str], object]]) -> None:
        self.expected = list(expected)
        self.calls: list[dict[str, object]] = []

    def __call__(self, *, method, url, headers, body, timeout_seconds):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if not self.expected:
            raise AssertionError("unexpected transport call")
        expected_method, path, expected_headers, payload = self.expected.pop(0)
        if method != expected_method or url != BASE + path or headers != expected_headers or body is not None:
            raise AssertionError("github HTTP contract mismatch")
        return GitHubHttpResponse(
            status_code=200,
            headers={"x-github-request-id": "github-request-1"},
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )


class RouteTransport:
    def __init__(self, expected: list[tuple[str, str, object, object]]) -> None:
        self.expected = list(expected)
        self.calls: list[dict[str, object]] = []

    def __call__(self, *, method, url, headers, body, timeout_seconds):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if not self.expected:
            raise AssertionError("unexpected transport call")
        expected_method, path, expected_body, payload = self.expected.pop(0)
        actual_body = json.loads(body.decode("utf-8")) if body is not None else None
        if method != expected_method or url != BASE + path or actual_body != expected_body:
            raise AssertionError("github HTTP route mismatch")
        expected_headers = dict(HEADERS)
        if expected_body is not None:
            expected_headers["Content-Type"] = "application/json"
        if headers != expected_headers:
            raise AssertionError("github HTTP headers mismatch")
        return GitHubHttpResponse(
            status_code=201 if expected_method == "POST" else 200,
            headers={"x-github-request-id": "github-request-1"},
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )


class GitHubProviderAdapterTests(unittest.TestCase):
    def context(self) -> ProviderExecutionContext:
        return ProviderExecutionContext(
            profile_id=51,
            required_credential_fields=("access_token",),
            network_allowed=True,
            credential_resolver=lambda profile_id, field: (
                "github-read-test-token" if (profile_id, field) == (51, "access_token") else ""
            ),
        )

    def request(self, action: str, **parameters: object) -> ProviderExecutionRequest:
        return ProviderExecutionRequest(
            plan_id=9,
            actor="manager-user",
            action=action,
            parameters={"owner": "octocat", "repository": "hello-world", **parameters},
        )

    def test_repository_read_uses_fixed_github_api_and_redacted_evidence(self) -> None:
        secret = "github-read-test-token"
        transport = StrictTransport(
            [
                (
                    "GET",
                    "/repos/octocat/hello-world",
                    HEADERS,
                    {"full_name": "octocat/hello-world", "token_echo": secret},
                )
            ]
        )

        result = GitHubProviderAdapter(transport=transport, simulated=True).execute(
            self.request("github.repository.read"), self.context()
        )

        self.assertEqual([], transport.expected)
        self.assertEqual("github", result["source"])
        self.assertEqual("repository", result["resource"])
        self.assertEqual("github-request-1", result["request_id"])
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(secret, rendered)
        self.assertLessEqual(len(rendered.encode("utf-8")), 16_384)

    def test_issue_read_requires_positive_number_before_credential_resolution(self) -> None:
        context = self.context()
        with self.assertRaisesRegex(ValueError, "github_issue_number_invalid"):
            GitHubProviderAdapter(transport=StrictTransport([]), simulated=True).execute(
                self.request("github.issue.read", issue_number=0), context
            )
        self.assertFalse(context.credential_resolver_called)

    def test_connection_test_uses_fixed_rate_limit_endpoint_without_repository_identity(self) -> None:
        transport = StrictTransport(
            [("GET", "/rate_limit", HEADERS, {"resources": {"core": {"limit": 5000}}})]
        )
        request = ProviderExecutionRequest(
            plan_id=9,
            actor="manager-user",
            action="github.connection_test",
            parameters={"timeout_seconds": 10},
        )

        result = GitHubProviderAdapter(transport=transport, simulated=True).execute(
            request, self.context()
        )

        self.assertEqual([], transport.expected)
        self.assertEqual("connection", result["resource"])
        self.assertEqual("github-request-1", result["request_id"])

    def test_manager_registry_exposes_only_readonly_github_actions(self) -> None:
        registry = build_manager_adapter_registry(provider="github")

        self.assertIsInstance(registry["github"], GitHubProviderAdapter)
        self.assertEqual("github", ACTION_DESCRIPTORS["github.connection_test"].provider)
        self.assertEqual("read", ACTION_DESCRIPTORS["github.repository.read"].risk)
        self.assertNotIn("github.issue.write", ACTION_DESCRIPTORS)

    def test_live_https_transport_explicitly_disables_environment_proxy_resolution(self) -> None:
        """A GitHub token must be sent only to the fixed HTTPS endpoint."""
        observed: dict[str, object] = {}

        class Opened:
            status = 200
            headers: dict[str, str] = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, _limit):
                return b"{}"

        class Opener:
            def open(self, request, *, timeout):
                observed["url"] = request.full_url
                observed["token"] = request.get_header("Authorization")
                observed["timeout"] = timeout
                return Opened()

        with mock.patch.dict(
            os.environ,
            {
                "HTTPS_PROXY": "http://proxy.invalid:8080",
                "ALL_PROXY": "socks5://proxy.invalid:1080",
            },
            clear=False,
        ), mock.patch(
            "app.providers.github.urllib.request.build_opener", return_value=Opener()
        ) as build_opener:
            response = github_module._https_transport(
                method="GET",
                url="https://api.github.com/repos/octocat/hello-world",
                headers={"Authorization": "Bearer do-not-proxy-this-token"},
                body=None,
                timeout_seconds=5,
            )

        handlers = build_opener.call_args.args
        proxy_handler = next(
            handler
            for handler in handlers
            if isinstance(handler, github_module.urllib.request.ProxyHandler)
        )
        self.assertEqual({}, proxy_handler.proxies)
        self.assertEqual("https://api.github.com/repos/octocat/hello-world", observed["url"])
        self.assertEqual("Bearer do-not-proxy-this-token", observed["token"])
        self.assertEqual(200, response.status_code)

    def test_gitlab_parity_code_evidence_actions_are_registered_as_github_reads(self) -> None:
        actions = {
            "github.repository.file.read",
            "github.commit.read",
            "github.commit.diff.read",
            "github.compare.read",
            "github.pull_request.commits.read",
            "github.pull_request.diffs.read",
            "github.actions.run.jobs.read",
        }

        for action in actions:
            with self.subTest(action=action):
                descriptor = ACTION_DESCRIPTORS[action]
                self.assertEqual("github", descriptor.provider)
                self.assertEqual("read", descriptor.risk)
                self.assertEqual(("access_token",), descriptor.required_credential_fields)
                self.assertIsNone(descriptor.read_back_verifier)

    def test_gitlab_parity_code_evidence_uses_fixed_get_routes_and_ephemeral_payloads(self) -> None:
        cases = (
            (
                "github.repository.file.read",
                {"file_path": "src/main.py", "ref": "feature/safe"},
                "/repos/octocat/hello-world/contents/src/main.py?ref=feature%2Fsafe",
                {"path": "src/main.py", "content": "cHJpbnQoJ29rJykK"},
                "repository_file",
            ),
            (
                "github.commit.read",
                {"sha": "a" * 40},
                "/repos/octocat/hello-world/commits/" + "a" * 40,
                {"sha": "a" * 40, "commit": {"message": "safe change"}},
                "commit",
            ),
            (
                "github.commit.diff.read",
                {"sha": "b" * 40, "page": 1, "per_page": 100},
                "/repos/octocat/hello-world/commits/" + "b" * 40 + "?page=1&per_page=100",
                {"sha": "b" * 40, "files": [{"filename": "app.py", "patch": "@@"}]},
                "commit_diff",
            ),
            (
                "github.compare.read",
                {"from_ref": "main", "to_ref": "feature/safe", "page": 1, "per_page": 100},
                "/repos/octocat/hello-world/compare/main...feature%2Fsafe?page=1&per_page=100",
                {"commits": [{"sha": "c" * 40}], "files": [{"filename": "app.py", "patch": "@@"}]},
                "compare",
            ),
            (
                "github.pull_request.commits.read",
                {"pull_request_number": 7, "page": 1, "per_page": 100},
                "/repos/octocat/hello-world/pulls/7/commits?page=1&per_page=100",
                [{"sha": "d" * 40}],
                "pull_request_commits",
            ),
            (
                "github.pull_request.diffs.read",
                {"pull_request_number": 7, "page": 1, "per_page": 100},
                "/repos/octocat/hello-world/pulls/7/files?page=1&per_page=100",
                [{"filename": "a.py", "patch": "@@"}],
                "pull_request_diffs",
            ),
            (
                "github.actions.run.jobs.read",
                {"workflow_run_id": 11, "page": 1, "per_page": 100},
                "/repos/octocat/hello-world/actions/runs/11/jobs?page=1&per_page=100",
                {"total_count": 1, "jobs": [{"id": 12, "name": "test", "conclusion": "success"}]},
                "actions_run_jobs",
            ),
        )

        for action, parameters, path, payload, kind in cases:
            with self.subTest(action=action):
                transport = RouteTransport([("GET", path, None, payload)])
                result = GitHubProviderAdapter(transport=transport, simulated=True).execute(
                    self.request(action, **parameters), self.context()
                )

                self.assertEqual(kind, result["kind"])
                self.assertRegex(result["payload_sha256"], r"^[0-9a-f]{64}$")
                self.assertEqual(payload, result["__local_response__"]["payload"])
                self.assertFalse(result["__local_response__"]["truncated"])
                self.assertEqual([], transport.expected)

    def test_invalid_github_code_evidence_fails_before_credential_resolution(self) -> None:
        requests = (
            self.request("github.repository.file.read", file_path="../secret", ref="main"),
            self.request("github.repository.file.read", file_path="config/.env", ref="main"),
            self.request("github.commit.read", sha="not-a-sha"),
            self.request("github.commit.diff.read", sha="a" * 40, page=0, per_page=100),
            self.request("github.compare.read", from_ref="main", to_ref="main", page=1, per_page=100),
            self.request("github.pull_request.commits.read", pull_request_number=True, page=1, per_page=100),
            self.request("github.pull_request.diffs.read", pull_request_number=7, page=1, per_page=101),
            self.request("github.actions.run.jobs.read", workflow_run_id=0, page=1, per_page=100),
        )

        for request in requests:
            with self.subTest(action=request.action):
                context = self.context()
                with self.assertRaises(ValueError):
                    GitHubProviderAdapter(transport=RouteTransport([]), simulated=True).execute(
                        request, context
                    )
                self.assertFalse(context.credential_resolver_called)

    def test_github_pr_writes_are_remote_write_actions_with_exact_readback(self) -> None:
        self.assertEqual("remote_write", ACTION_DESCRIPTORS["github.pull_request.comment.write"].risk)
        self.assertEqual("github.pull_request.read", ACTION_DESCRIPTORS["github.pull_request.comment.write"].read_back_verifier)
        self.assertEqual("remote_write", ACTION_DESCRIPTORS["github.pull_request.create"].risk)
        self.assertEqual("github.pull_request.read", ACTION_DESCRIPTORS["github.pull_request.create"].read_back_verifier)

        transport = RouteTransport([
            (
                "POST",
                "/repos/octocat/hello-world/issues/7/comments",
                {"body": "reviewed"},
                {"id": 8},
            ),
            (
                "GET",
                "/repos/octocat/hello-world/issues/comments/8",
                None,
                {"id": 8, "body": "reviewed"},
            ),
        ])
        adapter = GitHubProviderAdapter(transport=transport, simulated=True)
        request = self.request(
            "github.pull_request.comment.write",
            pull_request_number=7,
            body="reviewed",
        )
        context = self.context()
        target = adapter.normalize_request_target(request.parameters)

        adapter.execute(request, context)
        self.assertEqual(
            "verified_applied",
            adapter.verify("github.pull_request.read", request.action, request, target, context),
        )
        self.assertEqual([], transport.expected)

    def test_github_pull_request_create_reads_back_the_created_resource(self) -> None:
        transport = RouteTransport([
            (
                "POST",
                "/repos/octocat/hello-world/pulls",
                {"head": "feature/safe", "base": "main", "title": "Safe change"},
                {"number": 12},
            ),
            (
                "GET",
                "/repos/octocat/hello-world/pulls/12",
                None,
                {"number": 12, "head": {"ref": "feature/safe"}, "base": {"ref": "main"}, "title": "Safe change"},
            ),
        ])
        adapter = GitHubProviderAdapter(transport=transport, simulated=True)
        request = self.request(
            "github.pull_request.create",
            head="feature/safe",
            base="main",
            title="Safe change",
        )
        context = self.context()
        target = adapter.normalize_request_target(request.parameters)

        adapter.execute(request, context)
        self.assertEqual(
            "verified_applied",
            adapter.verify("github.pull_request.read", request.action, request, target, context),
        )
        self.assertEqual([], transport.expected)

    def test_confirmed_github_diff_read_returns_ephemeral_payload_without_persisting_diff(self) -> None:
        secret_diff = "@@ -1 +1 @@\n-password=private\n+password=safer"
        transport = RouteTransport([
            (
                "GET",
                "/repos/octocat/hello-world/commits/" + "a" * 40 + "?page=1&per_page=100",
                None,
                {"sha": "a" * 40, "files": [{"filename": "app.py", "patch": secret_diff}]},
            )
        ])
        adapter = GitHubProviderAdapter(transport=transport, simulated=True)
        with tempfile.TemporaryDirectory() as directory:
            previous = database.DB_PATH
            database.DB_PATH = Path(directory) / "manager.sqlite"
            try:
                repository = ManagerProviderRepository()
                profile = repository.upsert_profile(
                    scope_type="local",
                    scope_key="default",
                    provider="github",
                    profile_key="github-main",
                    display_name="GitHub",
                    enabled=True,
                    connection={"owner": "octocat", "repository": "hello-world"},
                )
                authorizer = ProviderActionAuthorizer(
                    repository,
                    clock=lambda: datetime(2026, 8, 20, tzinfo=timezone.utc),
                )
                service = ProviderExecutionService(
                    repository,
                    authorizer,
                    adapters={"github": adapter},
                    credential_resolver=lambda _id, _field: "github-read-test-token",
                )
                parameters = {
                    "owner": "octocat",
                    "repository": "hello-world",
                    "sha": "a" * 40,
                    "page": 1,
                    "per_page": 100,
                }
                target = adapter.normalize_request_target(parameters)
                plan = authorizer.create_plan(
                    profile_id=profile.id,
                    action="github.commit.diff.read",
                    target_alias=target,
                    parameters=parameters,
                    requested_by="manager",
                )
                authorization = authorizer.confirm(plan.id, actor="manager", ttl_seconds=60)
                result = service.execute(
                    authorization,
                    ProviderExecutionRequest(plan.id, "manager", plan.action, parameters),
                )

                self.assertEqual("succeeded", result["status"], result)
                self.assertEqual(secret_diff, result["local_response"]["payload"]["files"][0]["patch"])
                self.assertRegex(result["result_summary"]["payload_sha256"], r"^[0-9a-f]{64}$")
                durable_audit = json.dumps(repository.list_action_audits(), ensure_ascii=False)
                self.assertNotIn(secret_diff, durable_audit)
                self.assertNotIn("local_response", durable_audit)
            finally:
                database.DB_PATH = previous

    def test_plan_creation_rejects_noncanonical_github_target_before_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = database.DB_PATH
            database.DB_PATH = Path(directory) / "manager.sqlite"
            try:
                repository = ManagerProviderRepository()
                profile = repository.upsert_profile(
                    scope_type="local",
                    scope_key="default",
                    provider="github",
                    profile_key="github-main",
                    display_name="GitHub",
                    enabled=True,
                    connection={"owner": "octocat", "repository": "hello-world"},
                )
                authorizer = ProviderActionAuthorizer(
                    repository,
                    clock=lambda: datetime(2026, 8, 20, tzinfo=timezone.utc),
                )
                parameters = {
                    "owner": "octocat",
                    "repository": "hello-world",
                    "pull_request_number": 7,
                    "body": "reviewed",
                }

                with self.assertRaisesRegex(ValueError, "github_target_invalid"):
                    authorizer.create_plan(
                        profile_id=profile.id,
                        action="github.pull_request.comment.write",
                        target_alias="github.octocat.hello-world.pr7",
                        parameters=parameters,
                        requested_by="manager",
                    )
                self.assertEqual([], repository.list_action_audits())
            finally:
                database.DB_PATH = previous

    def test_github_comment_write_requires_one_use_confirmation_and_exact_readback(self) -> None:
        transport = RouteTransport([
            (
                "POST",
                "/repos/octocat/hello-world/issues/7/comments",
                {"body": "reviewed"},
                {"id": 8},
            ),
            (
                "GET",
                "/repos/octocat/hello-world/issues/comments/8",
                None,
                {"id": 8, "body": "reviewed"},
            ),
        ])
        adapter = GitHubProviderAdapter(transport=transport, simulated=True)
        with tempfile.TemporaryDirectory() as directory:
            previous = database.DB_PATH
            database.DB_PATH = Path(directory) / "manager.sqlite"
            try:
                repository = ManagerProviderRepository()
                profile = repository.upsert_profile(
                    scope_type="local",
                    scope_key="default",
                    provider="github",
                    profile_key="github-main",
                    display_name="GitHub",
                    enabled=True,
                    connection={"owner": "octocat", "repository": "hello-world"},
                )
                authorizer = ProviderActionAuthorizer(
                    repository,
                    clock=lambda: datetime(2026, 8, 20, tzinfo=timezone.utc),
                )
                service = ProviderExecutionService(
                    repository,
                    authorizer,
                    adapters={"github": adapter},
                    credential_resolver=lambda _id, _field: "github-read-test-token",
                )
                parameters = {
                    "owner": "octocat",
                    "repository": "hello-world",
                    "pull_request_number": 7,
                    "body": "reviewed",
                }
                target = adapter.normalize_request_target(parameters)
                plan = authorizer.create_plan(
                    profile_id=profile.id,
                    action="github.pull_request.comment.write",
                    target_alias=target,
                    parameters=parameters,
                    requested_by="manager",
                )
                request = ProviderExecutionRequest(plan.id, "manager", plan.action, parameters)

                blocked = service.execute(None, request)
                authorization = authorizer.confirm(plan.id, actor="manager", ttl_seconds=60)
                result = service.execute(authorization, request)
                reused = service.execute(authorization, request)

                self.assertEqual("authorization_required", blocked["reason"])
                self.assertEqual("succeeded", result["status"], result)
                self.assertEqual("verified", result["verification_status"])
                self.assertEqual("unknown", result["write_effect_status"])
                self.assertEqual(2, result["simulated_dispatch_count"])
                self.assertEqual("authorization_reused", reused["reason"])
                self.assertEqual([], transport.expected)
            finally:
                database.DB_PATH = previous

    def test_capability_status_reports_registered_github_read_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "capabilities.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "his-capabilities.v1",
                        "plugin": "his-engineering",
                        "capabilities": [{"name": "github.read", "enabled": True}],
                    }
                ),
                encoding="utf-8",
            )
            result = build_provider_capability_status(
                [
                    {
                        "provider": "github",
                        "profile_key": "github-main",
                        "credential_ref": "github_access_token",
                        "connection": {},
                    }
                ],
                manifest_paths={"his-engineering": str(manifest)},
            )

        item = result["items"][0]
        self.assertEqual("available", item["capability_state"])
        self.assertEqual("github.read", item["capabilities"][0]["name"])
        self.assertEqual(
            {
                "github.connection_test",
                "github.repository.read",
                "github.issue.read",
                "github.pull_request.read",
                "github.repository.file.read",
                "github.commit.read",
                "github.commit.diff.read",
                "github.compare.read",
                "github.pull_request.commits.read",
                "github.pull_request.diffs.read",
                "github.actions.run.jobs.read",
                "github.pull_request.comment.write",
                "github.pull_request.create",
            },
            {action["action"] for action in item["actions"]},
        )
        github_write = next(
            capability
            for capability in item["capabilities"]
            if capability["name"] == "github.write"
        )
        self.assertEqual("missing", github_write["contract_status"])
        self.assertEqual("blocked", github_write["execution_status"])

    def test_github_profile_form_accepts_only_public_repository_identity_and_token(self) -> None:
        fields = provider_field_specs("github")

        self.assertEqual(
            [(field.name, field.secret) for field in fields],
            [("owner", False), ("repository", False), ("access_token", True)],
        )


if __name__ == "__main__":
    unittest.main()
