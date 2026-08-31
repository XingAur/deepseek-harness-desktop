from __future__ import annotations

import json
import builtins
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

from app import database
from app.manager_provider_repository import ManagerProviderRepository
from app.provider_action_authorization import ProviderActionAuthorizer, canonical_json_hash
from app.provider_execution import (
    ACTION_DESCRIPTORS,
    ProviderExecutionRequest,
    ProviderExecutionService,
)
from app.providers.yunxiao import YunxiaoProviderAdapter


class FakeAdapter:
    def __init__(
        self,
        *,
        output=None,
        failure: Exception | None = None,
        verify_result: object = True,
        verify_failure: Exception | None = None,
        resolve_credentials: bool = True,
    ) -> None:
        self.output = output if output is not None else {"marker": "SAFE_OK"}
        self.failure = failure
        self.verify_result = verify_result
        self.verify_failure = verify_failure
        self.resolve_credentials = resolve_credentials
        self.calls = 0
        self.verify_calls = 0
        self.verify_actions: list[str] = []
        self.original_write_actions: list[str] = []
        self.verify_timeouts: list[object] = []
        self.verify_targets: list[str] = []
        self.events: list[str] = []

    def execute(self, request, context):
        self.calls += 1
        self.events.append("adapter")
        if self.resolve_credentials:
            for field in context.required_credential_fields:
                context.credential(field)
        self.events.append(f"network:{context.network_allowed}")
        if context.network_allowed:
            context.record_network_dispatch("fake-test-target", simulated=True)
        if self.failure is not None:
            raise self.failure
        return self.output

    def verify(self, verifier_action, original_write_action, request, target_alias, context):
        self.verify_calls += 1
        self.verify_actions.append(request.action)
        self.original_write_actions.append(original_write_action)
        self.verify_timeouts.append(request.parameters.get("timeout_seconds"))
        self.verify_targets.append(target_alias)
        if self.verify_failure is not None:
            raise self.verify_failure
        return self.verify_result


class TargetBindingAdapter(FakeAdapter):
    def normalize_target_alias(self, value):
        if not isinstance(value, str) or not value:
            raise ValueError("invalid target")
        return value.upper()


class GitTargetBindingAdapter(FakeAdapter):
    """A no-I/O Git adapter seam for authorization-order tests."""

    @staticmethod
    def normalize_target_alias(value):
        if value != "repo":
            raise ValueError("git_target_invalid")
        return value

    def normalize_request_target(self, parameters):
        if not isinstance(parameters, dict):
            raise ValueError("git_parameters_invalid")
        return self.normalize_target_alias(parameters.get("repository_alias"))


class ProviderExecutionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager.sqlite"
        self.repository = ManagerProviderRepository()
        self.profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="model",
            profile_key="demo",
            display_name="Demo",
            enabled=True,
            connection={"provider_kind": "openai_compatible", "model": "demo"},
        )
        self.authorizer = ProviderActionAuthorizer(
            self.repository,
            clock=lambda: datetime(2026, 8, 9, 3, 0, tzinfo=timezone.utc),
        )

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def request(self, *, action: str = "model.single_node.smoke", parameters=None):
        safe_parameters = parameters or {"timeout_seconds": 5, "marker": "SMOKE_OK"}
        plan = self.authorizer.create_plan(
            profile_id=self.profile.id,
            action=action,
            target_alias="model-demo",
            parameters=safe_parameters,
            requested_by="manager-user",
        )
        return ProviderExecutionRequest(
            plan_id=plan.id,
            actor="manager-user",
            action=action,
            parameters=safe_parameters,
        )

    def historical_unvalidated_request(self, *, action: str, parameters=None):
        safe_parameters = parameters or {"timeout_seconds": 5, "marker": "SMOKE_OK"}
        plan = self.repository.create_action_plan(
            profile_id=self.profile.id,
            action_type=action,
            target_alias="model-demo",
            parameter_hash=canonical_json_hash(safe_parameters),
            reviewed_parameter_summary=safe_parameters,
            requested_by="manager-user",
            created_at="2026-08-09T03:00:00+00:00",
        )
        return ProviderExecutionRequest(
            plan_id=int(plan["id"]),
            actor="manager-user",
            action=action,
            parameters=safe_parameters,
        )

    def service(self, adapter: FakeAdapter, credential_events: list[str]):
        def credential_resolver(profile_id: int, field: str) -> str:
            self.assertEqual(self.profile.id, profile_id)
            credential_events.append(f"credential:{field}")
            return "FAKE_TEST_CREDENTIAL"

        return ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"model": adapter},
            credential_resolver=credential_resolver,
        )

    def remote_write_request(self):
        profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="yunxiao",
            profile_key="company",
            display_name="Company",
            enabled=True,
            connection={"project_key": "DFHIS"},
        )
        action = "workitem.comment.write"
        parameters = {"work_item_alias": "DFHIS-1", "comment_hash": "safe-hash"}
        plan = self.authorizer.create_plan(
            profile_id=profile.id,
            action=action,
            target_alias="dfhis-1",
            parameters=parameters,
            requested_by="manager-user",
        )
        request = ProviderExecutionRequest(
            plan_id=plan.id,
            actor="manager-user",
            action=action,
            parameters=parameters,
        )
        return action, plan, request

    def test_provider_target_mismatch_blocks_before_consumption_credentials_or_adapter(self) -> None:
        profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="yunxiao",
            profile_key="target-bound",
            display_name="Target bound",
            enabled=True,
            connection={"project_key": "DFHIS"},
        )
        parameters = {"work_item_alias": "DFHIS-2"}
        plan = self.authorizer.create_plan(
            profile_id=profile.id,
            action="workitem.read",
            target_alias="dfhis-1",
            parameters=parameters,
            requested_by="manager-user",
        )
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)
        adapter = TargetBindingAdapter(resolve_credentials=True)
        credential_calls: list[tuple[int, str]] = []
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"yunxiao": adapter},
            credential_resolver=lambda profile_id, field: (credential_calls.append((profile_id, field)) or "fake"),
        )

        result = service.execute(
            authorization,
            ProviderExecutionRequest(
                plan_id=plan.id,
                actor="manager-user",
                action="workitem.read",
                parameters=parameters,
            ),
        )

        self.assertEqual("provider_target_mismatch", result["reason"])
        self.assertEqual("confirmed", self.repository.get_action_plan(plan.id)["state"])
        self.assertEqual(0, adapter.calls)
        self.assertEqual([], credential_calls)

    def test_rendered_yunxiao_plan_uses_the_same_bound_target_as_execution_audit(self) -> None:
        profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="yunxiao",
            profile_key="rendered",
            display_name="Rendered",
            enabled=True,
            connection={"project_key": "DFHIS"},
        )
        parameters = {
            "organization_alias": "org-main",
            "project_alias": "DFHIS",
            "work_item_alias": "DFHIS-42",
            "comment": {
                "business_logic": "按确认结果同步处理结论",
                "trigger_condition": "完成需求验证后",
                "handling_result": "已生成待测试结论",
                "covered_scenarios": "正常提交和空数据场景",
            },
        }
        plan = self.authorizer.create_plan(
            profile_id=profile.id,
            action="workitem.comment.write",
            target_alias="org-main.dfhis-42",
            parameters=parameters,
            requested_by="manager-user",
        )
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"yunxiao": YunxiaoProviderAdapter(transport=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("no transport during plan rendering")))},
        )

        rendered = service.render_plan(
            ProviderExecutionRequest(
                plan_id=plan.id,
                actor="manager-user",
                action="workitem.comment.write",
                parameters=parameters,
            )
        )

        self.assertEqual("org-main.dfhis-42", rendered["target_alias"])
        self.assertEqual(plan.id, rendered["plan_id"])
        self.assertEqual(plan.parameter_hash, rendered["parameter_hash"])

    def test_render_plan_rejects_any_parameter_hash_mismatch_before_rendering(self) -> None:
        profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="yunxiao",
            profile_key="rendered-mismatch",
            display_name="Rendered mismatch",
            enabled=True,
            connection={"project_key": "DFHIS"},
        )
        parameters = {
            "organization_alias": "org-main",
            "project_alias": "DFHIS",
            "work_item_alias": "DFHIS-42",
            "comment": {
                "business_logic": "按确认结果同步处理结论",
                "trigger_condition": "完成需求验证后",
                "handling_result": "已生成待测试结论",
                "covered_scenarios": "正常提交和空数据场景",
            },
        }
        plan = self.authorizer.create_plan(
            profile_id=profile.id,
            action="workitem.comment.write",
            target_alias="org-main.dfhis-42",
            parameters=parameters,
            requested_by="manager-user",
        )
        transport_calls: list[object] = []
        credential_calls: list[object] = []
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"yunxiao": YunxiaoProviderAdapter(transport=lambda **kwargs: transport_calls.append(kwargs))},
            credential_resolver=lambda *_args: (credential_calls.append("credential") or "fake"),
        )
        changed_parameters = []
        for key, replacement in (
            ("organization_alias", "org-other"),
            ("project_alias", "DFHIS2"),
            ("timeout_seconds", 10),
        ):
            changed = dict(parameters)
            changed[key] = replacement
            changed_parameters.append(changed)
        changed_comment = dict(parameters)
        changed_comment["comment"] = {**parameters["comment"], "handling_result": "渲染出的另一处理结果"}
        changed_parameters.append(changed_comment)

        for changed in changed_parameters:
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(ValueError, "provider_parameters_plan_mismatch"):
                    service.render_plan(
                        ProviderExecutionRequest(
                            plan_id=plan.id,
                            actor="manager-user",
                            action="workitem.comment.write",
                            parameters=changed,
                        )
                    )
        self.assertEqual([], transport_calls)
        self.assertEqual([], credential_calls)

    def test_yunxiao_same_workitem_in_another_organization_is_target_mismatch(self) -> None:
        profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="yunxiao",
            profile_key="organization-bound",
            display_name="Organization bound",
            enabled=True,
            connection={"project_key": "DFHIS"},
        )
        confirmed_parameters = {
            "organization_alias": "org-main",
            "project_alias": "DFHIS",
            "work_item_alias": "DFHIS-42",
        }
        plan = self.authorizer.create_plan(
            profile_id=profile.id,
            action="workitem.read",
            target_alias="org-main.dfhis-42",
            parameters=confirmed_parameters,
            requested_by="manager-user",
        )
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)
        transport_calls: list[object] = []
        credential_calls: list[object] = []
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"yunxiao": YunxiaoProviderAdapter(transport=lambda **kwargs: transport_calls.append(kwargs))},
            credential_resolver=lambda *_args: (credential_calls.append("credential") or "fake"),
        )

        result = service.execute(
            authorization,
            ProviderExecutionRequest(
                plan_id=plan.id,
                actor="manager-user",
                action="workitem.read",
                parameters={**confirmed_parameters, "organization_alias": "org-other"},
            ),
        )

        self.assertEqual("provider_target_mismatch", result["reason"])
        self.assertEqual("confirmed", self.repository.get_action_plan(plan.id)["state"])
        self.assertEqual([], transport_calls)
        self.assertEqual([], credential_calls)

    def test_yunxiao_organization_case_variant_is_rejected_before_render_or_execution(self) -> None:
        profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="yunxiao",
            profile_key="organization-case-bound",
            display_name="Organization case bound",
            enabled=True,
            connection={"project_key": "DFHIS"},
        )
        parameters = {
            "organization_alias": "ORG-MAIN",
            "project_alias": "DFHIS",
            "work_item_alias": "DFHIS-42",
        }
        plan = self.authorizer.create_plan(
            profile_id=profile.id,
            action="workitem.read",
            target_alias="org-main.dfhis-42",
            parameters=parameters,
            requested_by="manager-user",
        )
        transport_calls: list[object] = []
        credential_calls: list[object] = []
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"yunxiao": YunxiaoProviderAdapter(transport=lambda **kwargs: transport_calls.append(kwargs))},
            credential_resolver=lambda *_args: (credential_calls.append("credential") or "fake"),
        )
        request = ProviderExecutionRequest(
            plan_id=plan.id,
            actor="manager-user",
            action="workitem.read",
            parameters=parameters,
        )

        with self.assertRaisesRegex(ValueError, "provider_target_mismatch"):
            service.render_plan(request)
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)
        result = service.execute(authorization, request)

        self.assertEqual("provider_target_mismatch", result["reason"])
        self.assertEqual("confirmed", self.repository.get_action_plan(plan.id)["state"])
        self.assertEqual([], transport_calls)
        self.assertEqual([], credential_calls)

    def remote_write_service(self, adapter: FakeAdapter, credential: str):
        return ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"yunxiao": adapter},
            credential_resolver=lambda _profile_id, _field: credential,
        )

    def execution_audit(self, action: str) -> dict[str, object]:
        audits = self.repository.list_action_audits(action_type=action)
        execution_audits = [
            row["details"]
            for row in audits
            if "verification_status" in row["details"]
        ]
        self.assertEqual(1, len(execution_audits))
        return execution_audits[0]

    def test_adapter_gets_no_credentials_or_network_permission_before_authorization_consumes(self) -> None:
        adapter = FakeAdapter()
        credential_events: list[str] = []
        service = self.service(adapter, credential_events)
        request = self.request()

        blocked = service.execute(None, request)

        self.assertEqual("blocked", blocked["status"])
        self.assertEqual("authorization_required", blocked["reason"])
        self.assertEqual(0, adapter.calls)
        self.assertEqual([], credential_events)

        authorization = self.authorizer.confirm(
            request.plan_id, actor="manager-user", ttl_seconds=60
        )
        succeeded = service.execute(authorization, request)

        self.assertEqual("succeeded", succeeded["status"])
        self.assertEqual(1, adapter.calls)
        self.assertEqual(["credential:api_key"], credential_events)
        self.assertEqual(["adapter", "network:True"], adapter.events)
        self.assertEqual("consumed", self.repository.get_action_plan(request.plan_id)["state"])

    def test_git_invalid_ref_is_blocked_before_consumption_or_adapter_execution(self) -> None:
        profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="git",
            profile_key="repo",
            display_name="Repository",
            enabled=True,
            connection={"repository_path": "/private/tmp/repo"},
        )
        base = "a" * 40
        legal_parameters = {
            "repository_alias": "repo",
            "branch_name": "feature/nested/x",
            "expected_base_sha": base,
        }
        adapter = GitTargetBindingAdapter(resolve_credentials=False)
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"git": adapter},
        )

        for invalid_branch in (".foo", "feature/foo.lock"):
            # Simulate a previously persisted plan that predates the stricter
            # Git ref grammar.  Its parameter hash intentionally matches the
            # request, so only the pre-consume validator can protect it.
            invalid_parameters = {**legal_parameters, "branch_name": invalid_branch}
            legacy = self.repository.create_action_plan(
                profile_id=profile.id,
                action_type="branch.create",
                target_alias="repo",
                parameter_hash=canonical_json_hash(invalid_parameters),
                requested_by="manager-user",
                created_at="2026-08-09T03:00:00+00:00",
            )
            legacy_plan = self.authorizer.get_plan(int(legacy["id"]))
            authorization = self.authorizer.confirm(
                legacy_plan.id, actor="manager-user", ttl_seconds=60
            )
            with mock.patch.object(self.authorizer, "consume", wraps=self.authorizer.consume) as consume:
                with self.subTest(branch=invalid_branch):
                    result = service.execute(
                        authorization,
                        ProviderExecutionRequest(
                            plan_id=legacy_plan.id,
                            actor="manager-user",
                            action="branch.create",
                            parameters=invalid_parameters,
                        ),
                    )
                    self.assertEqual("blocked", result["status"])
                    self.assertEqual("provider_parameters_invalid", result["reason"])
                    self.assertEqual("confirmed", self.repository.get_action_plan(legacy_plan.id)["state"])
                self.assertEqual(0, consume.call_count)

        self.assertEqual(0, adapter.calls)

        plan = self.authorizer.create_plan(
            profile_id=profile.id,
            action="branch.create",
            target_alias="repo",
            parameters=legal_parameters,
            requested_by="manager-user",
        )
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)

        legal_result = service.execute(
            authorization,
            ProviderExecutionRequest(
                plan_id=plan.id,
                actor="manager-user",
                action="branch.create",
                parameters=legal_parameters,
            ),
        )
        self.assertEqual("succeeded", legal_result["status"])
        self.assertEqual(1, adapter.calls)
        self.assertEqual("consumed", self.repository.get_action_plan(plan.id)["state"])

    def test_blocked_succeeded_and_failed_results_share_one_redacted_audit_shape(self) -> None:
        sentinel = "Authorization: Bearer FAKE_PROVIDER_SECRET_9Zx7Qp4Lm2Nv8Bc6"
        adapter = FakeAdapter(output={"accessToken": sentinel, "marker": "SAFE_OK"})
        service = self.service(adapter, [])

        blocked_request = self.request()
        blocked = service.execute(None, blocked_request)

        success_request = self.request()
        success_auth = self.authorizer.confirm(
            success_request.plan_id, actor="manager-user", ttl_seconds=60
        )
        succeeded = service.execute(success_auth, success_request)

        adapter.failure = RuntimeError(sentinel)
        failed_request = self.request()
        failed_auth = self.authorizer.confirm(
            failed_request.plan_id, actor="manager-user", ttl_seconds=60
        )
        failed = service.execute(failed_auth, failed_request)

        self.assertEqual(["blocked", "succeeded", "failed"], [
            blocked["status"], succeeded["status"], failed["status"]
        ])
        result_audits = [
            row for row in self.repository.list_action_audits(limit=20)
            if "plan_id" in row["details"] and "verification_status" in row["details"]
        ]
        self.assertEqual(3, len(result_audits))
        self.assertEqual(
            {frozenset(row["details"]) for row in result_audits},
            {frozenset(result_audits[0]["details"])},
        )
        rendered = json.dumps([blocked, succeeded, failed, result_audits])
        self.assertNotIn(sentinel, rendered)
        self.assertEqual("provider_adapter_failed", failed["reason"])

    def test_every_failed_controlled_execution_creates_hashed_manager_candidates(self) -> None:
        sentinel = "Authorization: Bearer FAILED_PROVIDER_SECRET_9Zx7Qp4Lm2Nv8Bc6"
        adapter = FakeAdapter(failure=RuntimeError(sentinel))
        service = self.service(adapter, [])
        request = self.request()
        authorization = self.authorizer.confirm(
            request.plan_id, actor="manager-user", ttl_seconds=60
        )

        result = service.execute(authorization, request)

        self.assertEqual("failed", result["status"])
        with database.connect() as connection:
            rows = connection.execute(
                """
                select c.safe_summary_json, c.source_action_audit_id
                from manager_learning_candidates c
                order by c.id
                """
            ).fetchall()
        self.assertEqual(4, len(rows))
        rendered = json.dumps([dict(row) for row in rows], ensure_ascii=False)
        self.assertNotIn(sentinel, rendered)
        self.assertNotIn("model-demo", rendered)
        self.assertIn("sha256:", rendered)
        self.assertTrue(all(row["source_action_audit_id"] for row in rows))

    def test_candidate_persistence_error_keeps_consumed_audit_and_returns_redacted_failure(self) -> None:
        sentinel = "CANDIDATE_PERSISTENCE_SECRET_MUST_NOT_ESCAPE"
        adapter = FakeAdapter(failure=RuntimeError("adapter failure"))
        service = self.service(adapter, [])
        request = self.request()
        authorization = self.authorizer.confirm(
            request.plan_id, actor="manager-user", ttl_seconds=60
        )

        with mock.patch(
            "app.learning_loop.persist_manager_learning_candidates",
            side_effect=RuntimeError(sentinel),
        ):
            result = service.execute(authorization, request)

        self.assertEqual("failed", result["status"])
        self.assertEqual("candidate_persistence_failed", result["learning_candidate_status"])
        self.assertEqual(
            "learning_candidate_persistence_failed", result["learning_candidate_reason"]
        )
        self.assertEqual("consumed", self.repository.get_action_plan(request.plan_id)["state"])
        audits = self.repository.list_action_audits(action_type=request.action)
        self.assertTrue(any(row["status"] == "failed" for row in audits))
        self.assertNotIn(sentinel, json.dumps([result, audits], ensure_ascii=False))

    def test_candidate_module_import_error_is_returned_as_stable_redacted_failure(self) -> None:
        sentinel = "CANDIDATE_IMPORT_SECRET_MUST_NOT_ESCAPE"
        adapter = FakeAdapter(failure=RuntimeError("adapter failure"))
        service = self.service(adapter, [])
        request = self.request()
        authorization = self.authorizer.confirm(
            request.plan_id, actor="manager-user", ttl_seconds=60
        )
        original_import = builtins.__import__

        def reject_candidate_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "app.learning_loop":
                raise ImportError(sentinel)
            return original_import(name, globals, locals, fromlist, level)

        with mock.patch("builtins.__import__", side_effect=reject_candidate_import):
            result = service.execute(authorization, request)

        self.assertEqual("failed", result["status"])
        self.assertEqual("candidate_persistence_failed", result["learning_candidate_status"])
        self.assertEqual(
            "learning_candidate_persistence_failed", result["learning_candidate_reason"]
        )
        self.assertEqual("consumed", self.repository.get_action_plan(request.plan_id)["state"])
        audits = self.repository.list_action_audits(action_type=request.action)
        self.assertTrue(any(row["status"] == "failed" for row in audits))
        self.assertNotIn(sentinel, json.dumps([result, audits], ensure_ascii=False))

    def test_oversized_adapter_output_is_rejected_without_returning_content(self) -> None:
        descriptor = ACTION_DESCRIPTORS["model.single_node.smoke"]
        adapter = FakeAdapter(output={"text": "x" * (descriptor.max_result_bytes + 1)})
        service = self.service(adapter, [])
        request = self.request()
        authorization = self.authorizer.confirm(
            request.plan_id, actor="manager-user", ttl_seconds=60
        )

        result = service.execute(authorization, request)

        self.assertEqual("failed", result["status"])
        self.assertEqual("provider_result_too_large", result["reason"])
        self.assertEqual({}, result["result_summary"])
        self.assertEqual(1, adapter.calls)

    def test_unknown_action_and_missing_adapter_fail_closed_without_consuming(self) -> None:
        adapter = FakeAdapter()
        service = self.service(adapter, [])
        unknown = self.historical_unvalidated_request(action="provider.unknown")
        unknown_auth = self.authorizer.confirm(
            unknown.plan_id, actor="manager-user", ttl_seconds=60
        )

        unknown_result = service.execute(unknown_auth, unknown)

        self.assertEqual("provider_action_not_registered", unknown_result["reason"])
        self.assertEqual("confirmed", self.repository.get_action_plan(unknown.plan_id)["state"])
        self.assertEqual(0, adapter.calls)

        service_without_adapter = ProviderExecutionService(
            self.repository, self.authorizer
        )
        missing = self.request()
        missing_auth = self.authorizer.confirm(
            missing.plan_id, actor="manager-user", ttl_seconds=60
        )
        missing_result = service_without_adapter.execute(missing_auth, missing)
        self.assertEqual("provider_adapter_not_registered", missing_result["reason"])
        self.assertEqual("confirmed", self.repository.get_action_plan(missing.plan_id)["state"])

    def test_action_registered_for_another_provider_is_denied_before_consumption(self) -> None:
        adapter = FakeAdapter()
        service = self.service(adapter, [])
        request = self.historical_unvalidated_request(
            action="workitem.read", parameters={"work_item_alias": "dfhis-1"}
        )
        authorization = self.authorizer.confirm(
            request.plan_id, actor="manager-user", ttl_seconds=60
        )

        result = service.execute(authorization, request)

        self.assertEqual("provider_action_provider_mismatch", result["reason"])
        self.assertEqual("confirmed", self.repository.get_action_plan(request.plan_id)["state"])
        self.assertEqual(0, adapter.calls)

    def test_secret_shaped_untrusted_authorization_is_rejected_before_unknown_action_audit(self) -> None:
        service = self.service(FakeAdapter(), [])
        request = self.historical_unvalidated_request(action="provider.unknown")
        sentinel = "OpaqueAuthorizationToken9Zx7Qp4Lm2Nv8Bc6"

        with self.assertRaisesRegex(ValueError, "sensitive_public_input") as raised:
            service.execute({"accessToken": sentinel}, request)  # type: ignore[arg-type]

        self.assertNotIn(sentinel, str(raised.exception))
        self.assertEqual([], self.repository.list_action_audits())

    def test_remote_write_exception_still_has_one_attempt_and_one_read_back(self) -> None:
        action, plan, request = self.remote_write_request()
        sentinel = "FAKE_CREDENTIAL_MUST_NOT_ESCAPE"
        adapter = FakeAdapter(failure=RuntimeError(sentinel))
        service = self.remote_write_service(adapter, sentinel)
        authorization = self.authorizer.confirm(
            plan.id, actor="manager-user", ttl_seconds=60
        )

        result = service.execute(authorization, request)

        descriptor = ACTION_DESCRIPTORS[action]
        self.assertEqual("remote_write", descriptor.risk)
        self.assertEqual("workitem.comments.read", descriptor.read_back_verifier)
        self.assertEqual("failed", result["status"])
        self.assertEqual("provider_adapter_failed", result["reason"])
        self.assertEqual("verified", result["verification_status"])
        self.assertTrue(result["credentials_read"])
        self.assertFalse(result["external_calls"])
        self.assertIsNone(result["write_performed"])
        self.assertEqual("unknown", result["write_effect_status"])
        self.assertEqual(1, adapter.calls)
        self.assertEqual(1, adapter.verify_calls)
        self.assertEqual(["workitem.comments.read"], adapter.verify_actions)
        self.assertEqual([action], adapter.original_write_actions)
        self.assertEqual([15], adapter.verify_timeouts)
        self.assertEqual(["dfhis-1"], adapter.verify_targets)
        audit = self.execution_audit(action)
        self.assertFalse(audit["external_calls"])
        self.assertIsNone(audit["write_performed"])
        self.assertEqual("unknown", audit["write_effect_status"])
        self.assertNotIn(sentinel, json.dumps(result))

    def test_remote_write_oversized_output_still_runs_read_back_without_echo(self) -> None:
        action, plan, request = self.remote_write_request()
        sentinel = "SENTINEL_OVERSIZED_REMOTE_WRITE_RESULT"
        adapter = FakeAdapter(
            output={"text": sentinel * ACTION_DESCRIPTORS[action].max_result_bytes}
        )
        service = self.remote_write_service(adapter, "FAKE_TEST_CREDENTIAL")
        authorization = self.authorizer.confirm(
            plan.id, actor="manager-user", ttl_seconds=60
        )

        result = service.execute(authorization, request)

        audits = self.repository.list_action_audits(action_type=action)
        rendered = json.dumps([result, audits], ensure_ascii=False)
        self.assertEqual("failed", result["status"])
        self.assertEqual("provider_result_too_large", result["reason"])
        self.assertEqual("verified", result["verification_status"])
        self.assertTrue(result["credentials_read"])
        self.assertFalse(result["external_calls"])
        self.assertIsNone(result["write_performed"])
        self.assertEqual("unknown", result["write_effect_status"])
        self.assertEqual({}, result["result_summary"])
        self.assertEqual(1, adapter.calls)
        self.assertEqual(1, adapter.verify_calls)
        audit = self.execution_audit(action)
        self.assertFalse(audit["external_calls"])
        self.assertIsNone(audit["write_performed"])
        self.assertEqual("unknown", audit["write_effect_status"])
        self.assertNotIn(sentinel, rendered)

    def test_remote_write_read_back_failure_is_recorded_without_exception_text(self) -> None:
        action, plan, request = self.remote_write_request()
        sentinel = "SENTINEL_READ_BACK_FAILURE"
        adapter = FakeAdapter(verify_failure=RuntimeError(sentinel))
        service = self.remote_write_service(adapter, "FAKE_TEST_CREDENTIAL")
        authorization = self.authorizer.confirm(
            plan.id, actor="manager-user", ttl_seconds=60
        )

        result = service.execute(authorization, request)

        audits = self.repository.list_action_audits(action_type=action)
        rendered = json.dumps([result, audits], ensure_ascii=False)
        self.assertEqual("succeeded", result["status"])
        self.assertEqual("provider_action_succeeded", result["reason"])
        self.assertEqual("failed", result["verification_status"])
        self.assertTrue(result["credentials_read"])
        self.assertFalse(result["external_calls"])
        self.assertIsNone(result["write_performed"])
        self.assertEqual("unknown", result["write_effect_status"])
        self.assertEqual(1, adapter.calls)
        self.assertEqual(1, adapter.verify_calls)
        audit = self.execution_audit(action)
        self.assertIsNone(audit["write_performed"])
        self.assertEqual("unknown", audit["write_effect_status"])
        self.assertNotIn(sentinel, rendered)

    def test_remote_write_unverified_read_back_keeps_effect_unknown(self) -> None:
        action, plan, request = self.remote_write_request()
        adapter = FakeAdapter(verify_result=False)
        service = self.remote_write_service(adapter, "FAKE_TEST_CREDENTIAL")
        authorization = self.authorizer.confirm(
            plan.id, actor="manager-user", ttl_seconds=60
        )

        result = service.execute(authorization, request)

        self.assertEqual("unverified", result["verification_status"])
        self.assertIsNone(result["write_performed"])
        self.assertEqual("unknown", result["write_effect_status"])
        audit = self.execution_audit(action)
        self.assertIsNone(audit["write_performed"])
        self.assertEqual("unknown", audit["write_effect_status"])

    def test_remote_write_explicit_negative_read_back_is_verified_not_applied(self) -> None:
        action, plan, request = self.remote_write_request()
        adapter = FakeAdapter(verify_result="verified_not_applied")
        service = self.remote_write_service(adapter, "FAKE_TEST_CREDENTIAL")
        authorization = self.authorizer.confirm(
            plan.id, actor="manager-user", ttl_seconds=60
        )

        result = service.execute(authorization, request)

        self.assertEqual("verified", result["verification_status"])
        self.assertIsNone(result["write_performed"])
        self.assertEqual("unknown", result["write_effect_status"])
        audit = self.execution_audit(action)
        self.assertIsNone(audit["write_performed"])
        self.assertEqual("unknown", audit["write_effect_status"])

    def test_context_reports_only_actual_credential_resolver_use(self) -> None:
        action, plan, request = self.remote_write_request()
        adapter = FakeAdapter(
            failure=RuntimeError("SAFE_FAKE_FAILURE"),
            resolve_credentials=False,
        )
        service = self.remote_write_service(adapter, "FAKE_TEST_CREDENTIAL")
        authorization = self.authorizer.confirm(
            plan.id, actor="manager-user", ttl_seconds=60
        )

        result = service.execute(authorization, request)

        self.assertFalse(result["credentials_read"])
        self.assertFalse(result["external_calls"])
        self.assertEqual("unknown", result["write_effect_status"])
        audit = self.execution_audit(action)
        self.assertEqual(
            [],
            [key for key in audit if "credential" in key],
        )
        rendered = json.dumps(audit, ensure_ascii=False)
        self.assertNotIn("pat", rendered.lower())
        self.assertNotIn("FAKE_TEST_CREDENTIAL", rendered)

    def test_descriptors_use_only_exact_risks_and_bounded_limits(self) -> None:
        self.assertTrue(ACTION_DESCRIPTORS)
        for descriptor in ACTION_DESCRIPTORS.values():
            self.assertIn(
                descriptor.risk,
                {"read", "local_mutation", "remote_write", "model_smoke"},
            )
            self.assertGreater(descriptor.max_timeout_seconds, 0)
            self.assertGreater(descriptor.max_result_bytes, 0)
            self.assertIsInstance(descriptor.required_credential_fields, tuple)


if __name__ == "__main__":
    unittest.main()
