from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from app import database
from app.manager_provider_repository import ManagerProviderRepository
from app.provider_action_authorization import ProviderActionAuthorizer
from app.provider_execution import ACTION_DESCRIPTORS, ProviderExecutionContext, ProviderExecutionRequest, ProviderExecutionService
from app.providers.gitlab import GitLabHttpResponse, GitLabProviderAdapter
from app.providers import gitlab as gitlab_module


class StrictTransport:
    def __init__(self, responses: list[tuple[str, str, object, object]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def __call__(self, *, method, url, headers, body, timeout_seconds):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        expected_method, expected_url, expected_body, payload = self.responses.pop(0)
        if (method, url) != (expected_method, expected_url):
            raise AssertionError("GitLab HTTP route mismatch")
        actual_body = json.loads(body.decode("utf-8")) if body is not None else None
        if actual_body != expected_body:
            raise AssertionError("GitLab HTTP body mismatch")
        return GitLabHttpResponse(200, {"x-request-id": "req-1"}, json.dumps(payload).encode("utf-8"))


class GitLabProviderAdapterTests(unittest.TestCase):
    def _context(self) -> ProviderExecutionContext:
        return ProviderExecutionContext(
            profile_id=2,
            required_credential_fields=("access_token",),
            network_allowed=True,
            credential_resolver=lambda profile_id, field: "fake-gitlab-token" if (profile_id, field) == (2, "access_token") else "",
        )

    def _request(self, action: str, **parameters: object) -> ProviderExecutionRequest:
        return ProviderExecutionRequest(
            1, "manager", action,
            {"host_alias": "corp", "project_alias": "group/project", **parameters},
        )

    def test_project_read_uses_only_allowlisted_https_alias_and_redacts_response(self) -> None:
        secret = "Authorization: Bearer do-not-return-this-1234567890"
        transport = StrictTransport([("GET", "https://gitlab.example.test/api/v4/projects/group%2Fproject", None, {"name": "project", "description": secret})])
        adapter = GitLabProviderAdapter({"corp": "https://gitlab.example.test"}, transport=transport, simulated=True)

        result = adapter.execute(self._request("project.read"), self._context())

        self.assertEqual("gitlab", result["source"])
        self.assertEqual([], transport.responses)
        self.assertNotIn(secret, json.dumps(result))

    def test_merge_request_read_exposes_only_bounded_pipeline_identity(self) -> None:
        url = "https://gitlab.example.test/api/v4/projects/group%2Fproject/merge_requests/7"
        transport = StrictTransport([(
            "GET",
            url,
            None,
            {
                "iid": 7,
                "state": "opened",
                "title": "private title",
                "head_pipeline": {"id": 19, "web_url": "https://private.example/19"},
            },
        )])
        adapter = GitLabProviderAdapter(
            {"corp": "https://gitlab.example.test"},
            transport=transport,
            simulated=True,
        )

        result = adapter.execute(
            self._request("merge_request.read", merge_request_iid=7),
            self._context(),
        )

        self.assertEqual(19, result["head_pipeline_id"])
        self.assertNotIn("title", result)
        self.assertNotIn("web_url", result)

    def test_code_evidence_read_actions_are_registered_as_bounded_gitlab_reads(self) -> None:
        actions = {
            "gitlab.repository.file.read",
            "gitlab.commit.read",
            "gitlab.commit.diff.read",
            "gitlab.compare.read",
            "gitlab.merge_request.commits.read",
            "gitlab.merge_request.diffs.read",
            "gitlab.pipeline.jobs.read",
        }

        for action in actions:
            with self.subTest(action=action):
                descriptor = ACTION_DESCRIPTORS[action]
                self.assertEqual("gitlab", descriptor.provider)
                self.assertEqual("read", descriptor.risk)
                self.assertTrue(descriptor.network_allowed)
                self.assertEqual(("access_token",), descriptor.required_credential_fields)
                self.assertIsNone(descriptor.read_back_verifier)

    def test_code_evidence_reads_use_only_reviewed_get_routes_and_keep_payload_ephemeral(self) -> None:
        base = "https://gitlab.example.test/api/v4/projects/group%2Fproject"
        cases = (
            (
                "gitlab.repository.file.read",
                {"file_path": "src/main.py", "ref": "feature/safe"},
                base + "/repository/files/src%2Fmain.py?ref=feature%2Fsafe",
                {"file_path": "src/main.py", "ref": "feature/safe", "content": "cHJpbnQoJ29rJykK"},
                "repository_file",
            ),
            (
                "gitlab.commit.read",
                {"sha": "a" * 40},
                base + "/repository/commits/" + "a" * 40,
                {"id": "a" * 40, "title": "safe change"},
                "commit",
            ),
            (
                "gitlab.commit.diff.read",
                {"sha": "b" * 40, "page": 1, "per_page": 100},
                base + "/repository/commits/" + "b" * 40 + "/diff?page=1&per_page=100",
                [{"old_path": "app.py", "new_path": "app.py", "diff": "@@ -1 +1 @@"}],
                "commit_diff",
            ),
            (
                "gitlab.compare.read",
                {"from_ref": "main", "to_ref": "feature/safe"},
                base + "/repository/compare?from=main&to=feature%2Fsafe",
                {"commits": [{"id": "c" * 40}], "diffs": [{"new_path": "app.py", "diff": "@@"}]},
                "compare",
            ),
            (
                "gitlab.merge_request.commits.read",
                {"merge_request_iid": 7, "page": 1, "per_page": 100},
                base + "/merge_requests/7/commits?page=1&per_page=100",
                [{"id": "d" * 40, "title": "mr commit"}],
                "merge_request_commits",
            ),
            (
                "gitlab.merge_request.diffs.read",
                {"merge_request_iid": 7, "page": 1, "per_page": 100},
                base + "/merge_requests/7/diffs?page=1&per_page=100",
                [{"old_path": "a.py", "new_path": "a.py", "diff": "@@"}],
                "merge_request_diffs",
            ),
            (
                "gitlab.pipeline.jobs.read",
                {"pipeline_id": 11, "page": 1, "per_page": 100},
                base + "/pipelines/11/jobs?page=1&per_page=100",
                [{"id": 12, "name": "test", "status": "success"}],
                "pipeline_jobs",
            ),
        )

        for action, parameters, url, payload, kind in cases:
            with self.subTest(action=action):
                transport = StrictTransport([("GET", url, None, payload)])
                adapter = GitLabProviderAdapter(
                    {"corp": "https://gitlab.example.test"},
                    transport=transport,
                    simulated=True,
                )

                result = adapter.execute(self._request(action, **parameters), self._context())

                self.assertEqual(kind, result["kind"])
                self.assertIn("payload_sha256", result)
                self.assertNotIn(json.dumps(payload, ensure_ascii=False), json.dumps({key: value for key, value in result.items() if key != "__local_response__"}, ensure_ascii=False))
                self.assertEqual(payload, result["__local_response__"]["payload"])
                self.assertFalse(result["__local_response__"]["truncated"])
                self.assertEqual([], transport.responses)

    def test_invalid_code_evidence_parameters_fail_before_credential_or_transport(self) -> None:
        cases = (
            self._request("gitlab.repository.file.read", file_path="../secret", ref="main"),
            self._request("gitlab.repository.file.read", file_path="config/.env.production", ref="main"),
            self._request("gitlab.repository.file.read", file_path="src/app.py", ref="../main"),
            self._request("gitlab.commit.read", sha="not-a-sha"),
            self._request("gitlab.commit.diff.read", sha="a" * 40, page=0, per_page=100),
            self._request("gitlab.compare.read", from_ref="main", to_ref="main"),
            self._request("gitlab.merge_request.commits.read", merge_request_iid=True, page=1, per_page=100),
            self._request("gitlab.merge_request.diffs.read", merge_request_iid=7, page=1, per_page=101),
            self._request("gitlab.pipeline.jobs.read", pipeline_id=0, page=1, per_page=100),
        )
        adapter = GitLabProviderAdapter(
            {"corp": "https://gitlab.example.test"},
            transport=StrictTransport([]),
            simulated=True,
        )

        for request in cases:
            with self.subTest(action=request.action):
                context = self._context()
                with self.assertRaises(ValueError):
                    adapter.execute(request, context)
                self.assertFalse(context.credential_resolver_called)

    def test_confirmed_gitlab_diff_read_returns_ephemeral_payload_without_persisting_diff_text(self) -> None:
        secret_diff = "@@ -1 +1 @@\n-password=private\n+password=safer"
        base = "https://gitlab.example.test/api/v4/projects/group%2Fproject"
        transport = StrictTransport([
            ("GET", base + "/repository/commits/" + "a" * 40 + "/diff?page=1&per_page=100", None, [{"new_path": "app.py", "diff": secret_diff}]),
        ])
        adapter = GitLabProviderAdapter({"corp": "https://gitlab.example.test"}, transport=transport, simulated=True)
        with tempfile.TemporaryDirectory() as directory:
            previous = database.DB_PATH
            database.DB_PATH = Path(directory) / "manager.sqlite"
            try:
                repository = ManagerProviderRepository()
                profile = repository.upsert_profile(scope_type="local", scope_key="default", provider="gitlab", profile_key="corp", display_name="Corp", enabled=True, connection={"host": "corp"})
                authorizer = ProviderActionAuthorizer(repository, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc))
                service = ProviderExecutionService(repository, authorizer, adapters={"gitlab": adapter}, credential_resolver=lambda _id, _field: "fake-token")
                parameters = {"host_alias": "corp", "project_alias": "group/project", "sha": "a" * 40, "page": 1, "per_page": 100}
                target = adapter.normalize_request_target(parameters)
                plan = authorizer.create_plan(profile_id=profile.id, action="gitlab.commit.diff.read", target_alias=target, parameters=parameters, requested_by="manager")
                request = ProviderExecutionRequest(plan.id, "manager", plan.action, parameters)

                authorization = authorizer.confirm(plan.id, actor="manager", ttl_seconds=60)
                result = service.execute(authorization, request)

                self.assertEqual("succeeded", result["status"])
                self.assertEqual(secret_diff, result["local_response"]["payload"][0]["diff"])
                self.assertRegex(result["result_summary"]["payload_sha256"], r"[0-9a-f]{64}")
                self.assertGreater(result["result_summary"]["payload_bytes"], len(secret_diff.encode("utf-8")))
                self.assertEqual(1, result["simulated_dispatch_count"])
                self.assertFalse(result["external_calls"])
                self.assertFalse(result["write_performed"])
                self.assertEqual("not_applicable", result["write_effect_status"])
                durable_audit = json.dumps(repository.list_action_audits(), ensure_ascii=False)
                self.assertNotIn(secret_diff, durable_audit)
                self.assertNotIn("local_response", durable_audit)
            finally:
                database.DB_PATH = previous

    def test_remote_writes_have_one_request_then_one_resource_readback_and_unknown_when_incomplete(self) -> None:
        base = "https://gitlab.example.test/api/v4/projects/group%2Fproject/merge_requests/7"
        transport = StrictTransport([
            ("POST", base + "/notes", {"body": "reviewed"}, {"id": 8}),
            ("GET", base + "/notes/8", None, {"id": 8, "body": "reviewed"}),
        ])
        adapter = GitLabProviderAdapter({"corp": "https://gitlab.example.test"}, transport=transport, simulated=True)
        request = self._request("merge_request.comment.write", merge_request_iid=7, body="reviewed")
        context = self._context()
        target = adapter.normalize_request_target(request.parameters)

        adapter.execute(request, context)
        self.assertEqual("verified_applied", adapter.verify("merge_request.read", request.action, request, target, context))
        self.assertEqual([], transport.responses)

        incomplete = StrictTransport([("POST", base + "/notes", {"body": "reviewed"}, {"id": 8}), ("GET", base + "/notes/8", None, {"id": 9, "body": "reviewed"})])
        adapter = GitLabProviderAdapter({"corp": "https://gitlab.example.test"}, transport=incomplete, simulated=True)
        context = self._context()
        adapter.execute(request, context)
        self.assertEqual("unknown", adapter.verify("merge_request.read", request.action, request, target, context))

    def test_merge_request_create_reads_back_the_exact_created_resource(self) -> None:
        base = "https://gitlab.example.test/api/v4/projects/group%2Fproject"
        transport = StrictTransport([
            ("POST", base + "/merge_requests", {"source_branch": "feature/safe", "target_branch": "main", "title": "Safe change"}, {"iid": 12}),
            ("GET", base + "/merge_requests/12", None, {"iid": 12, "source_branch": "feature/safe", "target_branch": "main", "title": "Safe change"}),
        ])
        adapter = GitLabProviderAdapter({"corp": "https://gitlab.example.test"}, transport=transport, simulated=True)
        request = self._request("merge_request.create", source_branch="feature/safe", target_branch="main", title="Safe change")
        context = self._context()
        target = adapter.normalize_request_target(request.parameters)

        adapter.execute(request, context)
        self.assertEqual("verified_applied", adapter.verify("merge_request.read", request.action, request, target, context))
        self.assertEqual([], transport.responses)
        self.assertEqual(
            adapter.normalize_request_target({"host_alias": "corp", "project_alias": "group/project", "merge_request_iid": 12}),
            context.network_targets[-1],
        )

    def test_host_alias_injection_or_unknown_action_fails_before_token_or_transport(self) -> None:
        transport = StrictTransport([])
        adapter = GitLabProviderAdapter({"corp": "https://gitlab.example.test"}, transport=transport, simulated=True)
        context = self._context()
        for request in (
            self._request("project.read", host_alias="https://evil.example"),
            self._request("merge_request.delete", merge_request_iid=7),
            self._request("project.read", project_alias="group/../../evil"),
        ):
            with self.subTest(action=request.action):
                with self.assertRaises(ValueError):
                    adapter.execute(request, context)
        self.assertFalse(context.credential_resolver_called)
        self.assertEqual([], transport.calls)

    def test_length_delimited_targets_are_injective_and_reject_noncanonical_fabrications(self) -> None:
        adapter = GitLabProviderAdapter({"corp": "https://gitlab.example.test"}, transport=StrictTransport([]), simulated=True)
        first = adapter.normalize_request_target({"host_alias": "corp", "project_alias": "a.b/c"})
        second = adapter.normalize_request_target({"host_alias": "corp", "project_alias": "a/b.c"})
        project = adapter.normalize_request_target({"host_alias": "corp", "project_alias": "group/project.mr7"})
        mr = adapter.normalize_request_target({"host_alias": "corp", "project_alias": "group/project", "merge_request_iid": 7})

        self.assertNotEqual(first, second)
        self.assertNotEqual(project, mr)
        self.assertEqual(first, adapter.normalize_target_alias(first))
        with self.assertRaisesRegex(ValueError, "gitlab_target_invalid"):
            adapter.normalize_target_alias("gl-h4-corp-g1-a-p1-b-m7-trailing")
        with self.assertRaisesRegex(ValueError, "gitlab_target_invalid"):
            adapter.normalize_target_alias("corp/group/project!7")

    def test_default_https_port_is_canonicalized_for_the_same_gitlab_host(self) -> None:
        adapter = GitLabProviderAdapter({"corp": "https://gitlab.example.test:443"}, transport=StrictTransport([]), simulated=True)

        self.assertEqual("https://gitlab.example.test", adapter._hosts["corp"])

    def test_live_https_transport_explicitly_disables_environment_proxy_resolution(self) -> None:
        """A GitLab token must be sent only to the fixed HTTPS endpoint."""
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
                observed["token"] = request.get_header("Private-token")
                observed["timeout"] = timeout
                return Opened()

        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.invalid:8080", "ALL_PROXY": "socks5://proxy.invalid:1080"}, clear=False), mock.patch("app.providers.gitlab.urllib.request.build_opener", return_value=Opener()) as build_opener:
            response = gitlab_module._https_transport(
                method="GET",
                url="https://gitlab.example.test/api/v4/projects/group%2Fproject",
                headers={"Private-Token": "do-not-proxy-this-token"},
                body=None,
                timeout_seconds=5,
            )

        handlers = build_opener.call_args.args
        proxy_handler = next(handler for handler in handlers if isinstance(handler, gitlab_module.urllib.request.ProxyHandler))
        self.assertEqual({}, proxy_handler.proxies)
        self.assertEqual("https://gitlab.example.test/api/v4/projects/group%2Fproject", observed["url"])
        self.assertEqual("do-not-proxy-this-token", observed["token"])
        self.assertEqual(200, response.status_code)

    def test_comment_write_service_requires_confirmation_then_records_simulated_dispatch_and_reuse(self) -> None:
        base = "https://gitlab.example.test/api/v4/projects/group%2Fproject/merge_requests/7"
        transport = StrictTransport([
            ("POST", base + "/notes", {"body": "reviewed"}, {"id": 8}),
            ("GET", base + "/notes/8", None, {"id": 8, "body": "reviewed"}),
        ])
        adapter = GitLabProviderAdapter({"corp": "https://gitlab.example.test"}, transport=transport, simulated=True)
        with tempfile.TemporaryDirectory() as directory:
            previous = database.DB_PATH
            database.DB_PATH = Path(directory) / "manager.sqlite"
            try:
                repository = ManagerProviderRepository()
                profile = repository.upsert_profile(scope_type="local", scope_key="default", provider="gitlab", profile_key="corp", display_name="Corp", enabled=True, connection={"host": "corp"})
                authorizer = ProviderActionAuthorizer(repository, clock=lambda: datetime(2026, 8, 9, tzinfo=timezone.utc))
                service = ProviderExecutionService(repository, authorizer, adapters={"gitlab": adapter}, credential_resolver=lambda _id, _field: "fake-token")
                parameters = {"host_alias": "corp", "project_alias": "group/project", "merge_request_iid": 7, "body": "reviewed"}
                plan = authorizer.create_plan(profile_id=profile.id, action="merge_request.comment.write", target_alias="gl-h4-corp-g5-group-p7-project-m7", parameters=parameters, requested_by="manager")
                request = ProviderExecutionRequest(plan.id, "manager", plan.action, parameters)

                blocked = service.execute(None, request)
                self.assertEqual("authorization_required", blocked["reason"])
                self.assertEqual([], transport.calls)
                authorization = authorizer.confirm(plan.id, actor="manager", ttl_seconds=60)
                result = service.execute(authorization, request)
                reused = service.execute(authorization, request)

                self.assertEqual("succeeded", result["status"])
                self.assertEqual("unknown", result["write_effect_status"])
                self.assertEqual(0, result["network_call_count"])
                self.assertEqual(2, result["simulated_dispatch_count"])
                self.assertEqual("simulated", result["execution_provenance"])
                self.assertEqual("authorization_reused", reused["reason"])
                self.assertEqual(2, len(transport.calls))
            finally:
                database.DB_PATH = previous

    def test_plan_creation_rejects_legacy_gitlab_target_before_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = database.DB_PATH
            database.DB_PATH = Path(directory) / "manager.sqlite"
            try:
                repository = ManagerProviderRepository()
                profile = repository.upsert_profile(scope_type="local", scope_key="default", provider="gitlab", profile_key="corp", display_name="Corp", enabled=True, connection={"host": "corp"})
                authorizer = ProviderActionAuthorizer(repository, clock=lambda: datetime(2026, 8, 9, tzinfo=timezone.utc))
                parameters = {"host_alias": "corp", "project_alias": "group/project", "merge_request_iid": 7, "body": "reviewed"}

                with self.assertRaisesRegex(ValueError, "gitlab_target_invalid"):
                    authorizer.create_plan(profile_id=profile.id, action="merge_request.comment.write", target_alias="gitlab.corp.group.project.mr7", parameters=parameters, requested_by="manager")

                self.assertEqual([], repository.list_action_audits())
            finally:
                database.DB_PATH = previous


if __name__ == "__main__":
    unittest.main()
