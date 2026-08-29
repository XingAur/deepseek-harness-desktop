from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import app.provider_execution as provider_execution
from app import database
from app.manager_credential_crypto import AesGcmCredentialCipher
from app.manager_provider_repository import (
    CredentialResolutionUnavailable,
    ManagerProviderRepository,
)
from app.manager_model_smoke_preflight import build_model_smoke_preflight
from app.provider_action_authorization import ProviderActionAuthorizer
from app.provider_execution import (
    ProviderExecutionContext,
    ProviderExecutionRequest,
    ProviderExecutionService,
)
from app.providers.database_readonly import DatabaseReadonlyProviderAdapter


class CredentialReadingAdapter:
    def __init__(self) -> None:
        self.calls = 0
        self.credential_fingerprints: list[str] = []

    def execute(self, _request, context):
        self.calls += 1
        plaintext = context.credential("api_key")
        self.credential_fingerprints.append(
            hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        )
        return {"marker": "SMOKE_OK"}

    def verify(self, *_args):
        raise AssertionError("model smoke must not use a read-back verifier")


class ContextHoldingCredentialAdapter(CredentialReadingAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.context = None

    def execute(self, request, context):
        self.context = context
        return super().execute(request, context)


class CredentialEchoingDatabaseAdapter:
    def execute(self, _request, context):
        plaintext = context.credential("password")
        return {
            "ordinary": {"nested": plaintext, "binary": plaintext.encode("utf-8")},
            "__local_response__": {
                "rows": [{"ordinary": plaintext, "binary": plaintext.encode("utf-8")}]
            },
        }

    def verify(self, *_args):
        raise AssertionError("database read must not use a read-back verifier")


class CredentialThrowingDatabaseAdapter:
    def execute(self, _request, context):
        raise RuntimeError("adapter diagnostic must not escape: " + context.credential("password"))

    def verify(self, *_args):
        raise AssertionError("database read must not use a read-back verifier")


class CredentialDispatchingModelAdapter:
    def execute(self, _request, context):
        context.record_network_dispatch(context.credential("api_key"), simulated=True)
        return {"marker": "SMOKE_OK"}

    def verify(self, *_args):
        raise AssertionError("model smoke must not use a read-back verifier")


class PreCredentialDispatchingModelAdapter:
    """Records a target before resolving the same ordinary credential value."""

    def __init__(self, *, simulated: bool) -> None:
        self._simulated = simulated

    def execute(self, _request, context):
        target = "ordinaryvalue"
        context.record_network_dispatch(target, simulated=self._simulated)
        context.credential("api_key")
        return {"target": target, "nested": {"target": target}}

    def verify(self, *_args):
        raise AssertionError("model smoke must not use a read-back verifier")


class FailingContextHoldingCredentialAdapter(ContextHoldingCredentialAdapter):
    def execute(self, _request, context):
        self.context = context
        context.credential("api_key")
        raise RuntimeError("fixture adapter failure")


class CredentialActualTargetAdapter:
    def __init__(self, credential_field: str) -> None:
        self.credential_field = credential_field

    def execute(self, _request, context):
        context.record_network_dispatch("fake-provider", simulated=True)
        return {"marker": "SAFE_OK"}

    def verify(self, *_args):
        return "verified_applied"

    @staticmethod
    def normalize_target_alias(value):
        if not isinstance(value, str) or not value:
            raise ValueError("invalid target")
        return value

    def normalize_request_target(self, parameters):
        return self.normalize_target_alias(parameters.get("target_alias"))

    def read_back_target_alias(self, _action, _parameters, context):
        return context.credential(self.credential_field)


class DirectOriginalContextClosureAdapter:
    """Exercises the active production closure without Context.credential()."""

    def __init__(self) -> None:
        self.credential_fingerprints: list[str] = []

    def execute(self, _request, context):
        plaintext = context._credential_resolver(
            context, context.profile_id, "api_key"
        )
        self.credential_fingerprints.append(
            hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        )
        return {"ordinary": plaintext, "nested": {"ordinary": plaintext}}

    def verify(self, *_args):
        raise AssertionError("model smoke must not use a read-back verifier")


class CopiedContextClosureProbeAdapter:
    """A copied Context must not inherit a production closure's authority."""

    def __init__(self) -> None:
        self.copy_blocked = False

    def execute(self, _request, context):
        copied_context = ProviderExecutionContext(
            profile_id=context.profile_id,
            required_credential_fields=("api_key",),
            network_allowed=True,
        )
        try:
            context._credential_resolver(
                copied_context, context.profile_id, "api_key"
            )
        except CredentialResolutionUnavailable:
            self.copy_blocked = True
        return {"copy_blocked": self.copy_blocked}

    def verify(self, *_args):
        raise AssertionError("model smoke must not use a read-back verifier")


class WrongIdentityClosureProbeAdapter:
    """An arbitrary object is not an active ProviderExecutionContext."""

    def __init__(self) -> None:
        self.wrong_identity_blocked = False

    def execute(self, _request, context):
        try:
            context._credential_resolver(object(), context.profile_id, "api_key")
        except CredentialResolutionUnavailable:
            self.wrong_identity_blocked = True
        return {"wrong_identity_blocked": self.wrong_identity_blocked}

    def verify(self, *_args):
        raise AssertionError("model smoke must not use a read-back verifier")


class CrossProfileCredentialProbeAdapter:
    def __init__(self, other_profile_id: int) -> None:
        self.other_profile_id = other_profile_id
        self.cross_profile_blocked = False

    def execute(self, _request, context):
        try:
            context._credential_resolver(context, self.other_profile_id, "api_key")
        except CredentialResolutionUnavailable:
            self.cross_profile_blocked = True
        else:  # pragma: no cover - the assertion below makes this path fail
            raise AssertionError("cross-profile credential resolution unexpectedly succeeded")
        context.credential("api_key")
        return {"marker": "SMOKE_OK"}

    def verify(self, *_args):
        raise AssertionError("model smoke must not use a read-back verifier")


class AuthorizedCredentialResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager.sqlite"
        self.cipher = AesGcmCredentialCipher(b"k" * 32)
        self.repository = ManagerProviderRepository(cipher=self.cipher)
        self.authorizer = ProviderActionAuthorizer(
            self.repository,
            clock=lambda: datetime(2026, 8, 10, 4, 0, tzinfo=timezone.utc),
        )
        self.profile = self._profile("approved-model")
        self.other_profile = self._profile("other-model")
        self.repository.upsert_credential(
            profile_id=self.profile.id,
            field="api_key",
            plaintext="APPROVED_TEST_CREDENTIAL",
        )
        self.repository.upsert_credential(
            profile_id=self.other_profile.id,
            field="api_key",
            plaintext="OTHER_TEST_CREDENTIAL",
        )

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def _profile(self, key: str):
        return self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="model",
            profile_key=key,
            display_name=key,
            enabled=True,
            connection={"provider_kind": "openai_compatible", "model": "fixture-model"},
        )

    def _request(self, profile_id: int):
        parameters = {"timeout_seconds": 5, "marker": "SMOKE_OK"}
        plan = self.authorizer.create_plan(
            profile_id=profile_id,
            action="model.single_node.smoke",
            target_alias="model-fixture",
            parameters=parameters,
            requested_by="manager-user",
        )
        return plan, ProviderExecutionRequest(
            plan_id=plan.id,
            actor="manager-user",
            action="model.single_node.smoke",
            parameters=parameters,
        )

    def _database_profile(self, key: str, database_path: Path):
        return self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="database",
            profile_key=key,
            display_name=key,
            enabled=True,
            connection={
                "driver": "sqlite",
                "host": "local",
                "port": "0",
                "database": str(database_path),
                "schema": "main",
                "username": "readonly",
                "readonly_policy": "required",
            },
        )

    def _database_request(
        self, profile_id: int, profile_key: str, sql: str
    ) -> tuple[object, ProviderExecutionRequest]:
        parameters = {
            "database_alias": f"db-{profile_key}",
            "sql": sql,
            "timeout_seconds": 5,
        }
        plan = self.authorizer.create_plan(
            profile_id=profile_id,
            action="database.query.read",
            target_alias=f"db-{profile_key}",
            parameters=parameters,
            requested_by="manager-user",
        )
        return plan, ProviderExecutionRequest(
            plan_id=plan.id,
            actor="manager-user",
            action="database.query.read",
            parameters=parameters,
        )

    def _service(self, adapter: CredentialReadingAdapter) -> ProviderExecutionService:
        return ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"model": adapter},
        )

    @staticmethod
    def _fingerprint(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def test_default_execution_resolver_reads_only_the_consumed_plan_profile(self) -> None:
        plan, request = self._request(self.profile.id)
        adapter = CredentialReadingAdapter()
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)

        result = self._service(adapter).execute(authorization, request)

        rendered = json.dumps([result, self.repository.list_action_audits()], ensure_ascii=False)
        self.assertEqual("succeeded", result["status"])
        self.assertEqual(
            [self._fingerprint("APPROVED_TEST_CREDENTIAL")],
            adapter.credential_fingerprints,
        )
        self.assertNotIn(
            self._fingerprint("OTHER_TEST_CREDENTIAL"),
            adapter.credential_fingerprints,
        )
        self.assertNotIn("APPROVED_TEST_CREDENTIAL", rendered)
        self.assertNotIn("OTHER_TEST_CREDENTIAL", rendered)

    def test_direct_production_closure_on_its_original_context_registers_scrub_digest(
        self,
    ) -> None:
        credential = "ordinaryvalue"
        self.repository.upsert_credential(
            profile_id=self.profile.id,
            field="api_key",
            plaintext=credential,
        )
        plan, request = self._request(self.profile.id)
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)
        adapter = DirectOriginalContextClosureAdapter()

        result = self._service(adapter).execute(authorization, request)

        rendered = json.dumps([result, self.repository.list_action_audits()], ensure_ascii=False)
        self.assertEqual("succeeded", result["status"])
        self.assertTrue(result["credentials_read"])
        self.assertEqual([self._fingerprint(credential)], adapter.credential_fingerprints)
        self.assertEqual("REDACTED", result["result_summary"]["ordinary"])
        self.assertEqual("REDACTED", result["result_summary"]["nested"]["ordinary"])
        self.assertNotIn(credential, rendered)

    def test_copied_context_cannot_use_a_production_closure(self) -> None:
        plan, request = self._request(self.profile.id)
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)
        adapter = CopiedContextClosureProbeAdapter()

        result = self._service(adapter).execute(authorization, request)

        self.assertEqual("succeeded", result["status"])
        self.assertTrue(adapter.copy_blocked)

    def test_wrong_context_identity_cannot_call_a_production_closure(self) -> None:
        plan, request = self._request(self.profile.id)
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)
        adapter = WrongIdentityClosureProbeAdapter()

        result = self._service(adapter).execute(authorization, request)

        self.assertEqual("succeeded", result["status"])
        self.assertTrue(adapter.wrong_identity_blocked)

    def test_unconfirmed_or_reused_plan_never_decrypts_or_calls_adapter(self) -> None:
        unconfirmed_plan, unconfirmed_request = self._request(self.profile.id)
        adapter = CredentialReadingAdapter()
        service = self._service(adapter)

        with mock.patch.object(
            self.cipher,
            "decrypt",
            side_effect=AssertionError("unconfirmed plan must not decrypt"),
        ) as decrypt:
            blocked = service.execute(None, unconfirmed_request)

        self.assertEqual("blocked", blocked["status"])
        self.assertEqual(0, adapter.calls)
        decrypt.assert_not_called()

        plan, request = self._request(self.profile.id)
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)
        succeeded = service.execute(authorization, request)
        reused = service.execute(authorization, request)

        self.assertEqual("succeeded", succeeded["status"])
        self.assertEqual("blocked", reused["status"])
        self.assertEqual(1, adapter.calls)

    def test_listing_preflight_and_direct_repository_call_cannot_decrypt(self) -> None:
        profile_status = self.repository.profile_status(self.profile.id)

        with mock.patch.object(
            self.cipher,
            "decrypt",
            side_effect=AssertionError("non-execution route must not decrypt"),
        ) as decrypt:
            listed = self.repository.list_profiles()
            preflight = build_model_smoke_preflight(profile_status)
            with self.assertRaisesRegex(
                CredentialResolutionUnavailable,
                "^credential_resolution_unavailable$",
            ):
                self.repository.resolve_credential_for_authorized_executor(
                    profile_id=self.profile.id,
                    field="api_key",
                )

        self.assertEqual("configured", profile_status["credentials"]["api_key"])
        self.assertEqual(2, len(listed))
        self.assertFalse(preflight["credentials_read"])
        decrypt.assert_not_called()

    def test_nonexecution_context_has_no_production_credential_signing_seam(self) -> None:
        plan, request = self._request(self.profile.id)
        service = self._service(CredentialReadingAdapter())
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)
        decision = self.authorizer.consume(
            plan_id=plan.id,
            authorization=authorization,
            actor=request.actor,
            parameters=request.parameters,
        )
        self.assertTrue(decision.allowed)

        with mock.patch.object(
            self.cipher,
            "decrypt",
            side_effect=AssertionError("non-execution path must not decrypt"),
        ) as decrypt:
            self.assertFalse(
                hasattr(provider_execution, "_EXECUTION_CONTEXT_CONSTRUCTION_KEY")
            )
            self.assertFalse(
                hasattr(service, "_credential_resolver_for_consumed_plan")
            )
            self.assertFalse(
                hasattr(self.repository, "_issue_credential_resolution_capability")
            )
            self.assertFalse(
                hasattr(self.repository, "_resolve_credential_for_execution_context")
            )
            forged_context = ProviderExecutionContext(
                profile_id=self.profile.id,
                required_credential_fields=("api_key",),
                network_allowed=True,
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "^provider_credential_resolution_unavailable$",
            ):
                forged_context.credential("api_key")

        decrypt.assert_not_called()

    def test_consumed_execution_capability_is_revoked_after_its_context_returns(self) -> None:
        plan, request = self._request(self.profile.id)
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)
        adapter = ContextHoldingCredentialAdapter()

        result = self._service(adapter).execute(authorization, request)

        self.assertEqual("succeeded", result["status"])
        self.assertIsNotNone(adapter.context)
        with mock.patch.object(
            self.cipher,
            "decrypt",
            side_effect=AssertionError("completed context must not decrypt"),
        ) as decrypt:
            with self.assertRaisesRegex(
                CredentialResolutionUnavailable,
                "^credential_resolution_unavailable$",
            ):
                adapter.context.credential("api_key")
        decrypt.assert_not_called()

    def test_failed_execution_context_cannot_reuse_its_production_resolver(self) -> None:
        plan, request = self._request(self.profile.id)
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)
        adapter = FailingContextHoldingCredentialAdapter()

        result = self._service(adapter).execute(authorization, request)

        self.assertEqual("failed", result["status"])
        self.assertIsNotNone(adapter.context)
        with mock.patch.object(
            self.cipher,
            "decrypt",
            side_effect=AssertionError("failed Context must not decrypt"),
        ) as decrypt:
            with self.assertRaisesRegex(
                CredentialResolutionUnavailable,
                "^credential_resolution_unavailable$",
            ):
                adapter.context.credential("api_key")
            try:
                adapter.context._credential_resolver(
                    adapter.context, adapter.context.profile_id, "api_key"
                )
            except CredentialResolutionUnavailable:
                direct_closure_blocked = True
            except Exception:
                direct_closure_blocked = False
            else:  # pragma: no cover - assertion below makes this path fail
                direct_closure_blocked = False
        self.assertTrue(direct_closure_blocked)
        decrypt.assert_not_called()

    def test_live_context_capability_rejects_cross_profile_resolution(self) -> None:
        plan, request = self._request(self.profile.id)
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)
        adapter = CrossProfileCredentialProbeAdapter(self.other_profile.id)

        result = self._service(adapter).execute(authorization, request)

        self.assertEqual("succeeded", result["status"])
        self.assertTrue(adapter.cross_profile_blocked)

    def test_network_dispatch_rejects_a_low_entropy_current_credential_target(self) -> None:
        credential = "ordinaryvalue"
        self.repository.upsert_credential(
            profile_id=self.profile.id,
            field="api_key",
            plaintext=credential,
        )
        plan, request = self._request(self.profile.id)
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)

        result = self._service(CredentialDispatchingModelAdapter()).execute(
            authorization, request
        )

        rendered = json.dumps([result, self.repository.list_action_audits()], ensure_ascii=False)
        self.assertEqual("failed", result["status"])
        self.assertEqual("provider_adapter_failed", result["reason"])
        self.assertNotIn(credential, rendered)

    def test_precredential_dispatch_metadata_is_scrubbed_after_resolving_the_same_value(
        self,
    ) -> None:
        credential = "ordinaryvalue"
        self.repository.upsert_credential(
            profile_id=self.profile.id,
            field="api_key",
            plaintext=credential,
        )

        for simulated in (True, False):
            with self.subTest(simulated=simulated):
                plan, request = self._request(self.profile.id)
                authorization = self.authorizer.confirm(
                    plan.id, actor="manager-user", ttl_seconds=60
                )
                result = self._service(
                    PreCredentialDispatchingModelAdapter(simulated=simulated)
                ).execute(authorization, request)

                rendered = json.dumps(
                    [result, self.repository.list_action_audits()], ensure_ascii=False
                )
                self.assertEqual("succeeded", result["status"])
                self.assertEqual(["REDACTED"], result["network_targets"])
                self.assertEqual("REDACTED", result["result_summary"]["target"])
                self.assertEqual(
                    "REDACTED", result["result_summary"]["nested"]["target"]
                )
                self.assertEqual(1 if simulated else 0, result["simulated_dispatch_count"])
                self.assertEqual(0 if simulated else 1, result["network_call_count"])
                self.assertNotIn(credential, rendered)

    def test_readback_actual_target_cannot_return_a_resolved_credential_value(self) -> None:
        credential = "ordinaryvalue"
        profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="yunxiao",
            profile_key="credential-target",
            display_name="credential-target",
            enabled=True,
            connection={"project_key": "DFHIS"},
        )
        self.repository.upsert_credential(
            profile_id=profile.id,
            field="pat",
            plaintext=credential,
        )
        write_action = "workitem.comment.write"
        parameters = {
            "target_alias": "org-fixture.dfhis-31333",
            "work_item_alias": "DFHIS-31333",
            "comment": {"business_logic": "safe-summary"},
            "timeout_seconds": 5,
        }
        plan = self.authorizer.create_plan(
            profile_id=profile.id,
            action=write_action,
            target_alias="org-fixture.dfhis-31333",
            parameters=parameters,
            requested_by="manager-user",
        )
        request = ProviderExecutionRequest(
            plan_id=plan.id,
            actor="manager-user",
            action=write_action,
            parameters=parameters,
        )
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)

        result = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"yunxiao": CredentialActualTargetAdapter("pat")},
        ).execute(authorization, request)

        rendered = json.dumps([result, self.repository.list_action_audits()], ensure_ascii=False)
        self.assertEqual("succeeded", result["status"])
        self.assertIsNone(result["actual_target_alias"])
        self.assertNotIn(credential, rendered)

    def test_database_local_response_and_ordinary_summary_redact_current_password(self) -> None:
        external_db_path = Path(self.temp_dir.name) / "query-fixture.sqlite"
        password = "DbFixtureValue7Qx2Lm8Nv5"
        connection = sqlite3.connect(external_db_path)
        try:
            connection.execute("create table evidence(ordinary text)")
            connection.execute("insert into evidence(ordinary) values(?)", (password,))
            connection.commit()
        finally:
            connection.close()
        profile = self._database_profile("query-fixture", external_db_path)
        self.repository.upsert_credential(
            profile_id=profile.id,
            field="password",
            plaintext=password,
        )
        plan, request = self._database_request(
            profile.id, "query-fixture", "select ordinary from evidence"
        )
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)
        adapter = DatabaseReadonlyProviderAdapter(
            profile_loader=lambda _profile_id: {
                "provider": "database",
                "profile_key": "query-fixture",
                "enabled": True,
                "connection": {
                    "driver": "sqlite",
                    "host": "local",
                    "port": "0",
                    "database": str(external_db_path),
                    "schema": "main",
                    "username": "readonly",
                    "readonly_policy": "required",
                },
            }
        )

        result = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"database": adapter},
        ).execute(authorization, request)

        rendered = json.dumps([result, self.repository.list_action_audits()], ensure_ascii=False)
        self.assertEqual("succeeded", result["status"])
        self.assertEqual("REDACTED", result["local_response"]["rows"][0][0])
        self.assertNotIn(password, rendered)

    def test_nested_and_binary_provider_output_cannot_echo_current_password(self) -> None:
        password = "DbFixtureValue7Qx2Lm8Nv5"
        external_db_path = Path(self.temp_dir.name) / "echo-fixture.sqlite"
        external_db_path.touch()
        profile = self._database_profile("echo-fixture", external_db_path)
        self.repository.upsert_credential(
            profile_id=profile.id,
            field="password",
            plaintext=password,
        )
        plan, request = self._database_request(
            profile.id, "echo-fixture", "select 1 as ordinary"
        )
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)

        result = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"database": CredentialEchoingDatabaseAdapter()},
        ).execute(authorization, request)

        rendered = json.dumps([result, self.repository.list_action_audits()], ensure_ascii=False)
        self.assertEqual("succeeded", result["status"])
        self.assertEqual("REDACTED", result["result_summary"]["ordinary"]["nested"])
        self.assertEqual("REDACTED", result["result_summary"]["ordinary"]["binary"])
        self.assertEqual("REDACTED", result["local_response"]["rows"][0]["ordinary"])
        self.assertEqual("REDACTED", result["local_response"]["rows"][0]["binary"])
        self.assertNotIn(password, rendered)

    def test_adapter_exception_cannot_return_or_audit_the_current_password(self) -> None:
        password = "DbFixtureValue7Qx2Lm8Nv5"
        external_db_path = Path(self.temp_dir.name) / "exception-fixture.sqlite"
        external_db_path.touch()
        profile = self._database_profile("exception-fixture", external_db_path)
        self.repository.upsert_credential(
            profile_id=profile.id,
            field="password",
            plaintext=password,
        )
        plan, request = self._database_request(
            profile.id, "exception-fixture", "select 1 as ordinary"
        )
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)

        result = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"database": CredentialThrowingDatabaseAdapter()},
        ).execute(authorization, request)

        rendered = json.dumps([result, self.repository.list_action_audits()], ensure_ascii=False)
        self.assertEqual("failed", result["status"])
        self.assertEqual("provider_adapter_failed", result["reason"])
        self.assertNotIn(password, rendered)

    def test_missing_or_tampered_ciphertext_fails_closed_without_plaintext(self) -> None:
        adapter = CredentialReadingAdapter()
        plan, request = self._request(self.profile.id)
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)
        with database.connect() as connection:
            connection.execute(
                "update manager_provider_credentials set ciphertext = ? where profile_id = ?",
                ("aesgcm.v1.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", self.profile.id),
            )

        result = self._service(adapter).execute(authorization, request)

        rendered = json.dumps([result, self.repository.list_action_audits()], ensure_ascii=False)
        self.assertEqual("failed", result["status"])
        self.assertEqual("provider_adapter_failed", result["reason"])
        self.assertNotIn("APPROVED_TEST_CREDENTIAL", rendered)
        self.assertNotIn("OTHER_TEST_CREDENTIAL", rendered)

    def test_missing_master_key_or_credential_record_fails_closed_without_plaintext(self) -> None:
        adapter = CredentialReadingAdapter()
        missing_record = self._profile("missing-record")
        record_plan, record_request = self._request(missing_record.id)
        record_authorization = self.authorizer.confirm(
            record_plan.id, actor="manager-user", ttl_seconds=60
        )

        missing_record_result = self._service(adapter).execute(
            record_authorization, record_request
        )

        self.assertEqual("failed", missing_record_result["status"])
        self.assertEqual("provider_adapter_failed", missing_record_result["reason"])

        missing_key_plan, missing_key_request = self._request(self.profile.id)
        missing_key_authorization = self.authorizer.confirm(
            missing_key_plan.id, actor="manager-user", ttl_seconds=60
        )
        # Credentials were created with the test cipher above.  Removing that
        # in-memory key makes this execution follow the deployment-env path,
        # without constructing a second repository against the same database.
        self.repository._cipher = None

        with mock.patch.dict(os.environ, {}, clear=True):
            missing_key_result = self._service(adapter).execute(
                missing_key_authorization, missing_key_request
            )

        rendered = json.dumps(
            [missing_record_result, missing_key_result, self.repository.list_action_audits()],
            ensure_ascii=False,
        )
        self.assertEqual("failed", missing_key_result["status"])
        self.assertEqual("provider_adapter_failed", missing_key_result["reason"])
        self.assertNotIn("APPROVED_TEST_CREDENTIAL", rendered)
        self.assertNotIn("OTHER_TEST_CREDENTIAL", rendered)

    def test_default_resolver_does_not_cache_plaintext_between_consumed_plans(self) -> None:
        adapter = CredentialReadingAdapter()
        service = self._service(adapter)
        first_plan, first_request = self._request(self.profile.id)
        second_plan, second_request = self._request(self.profile.id)
        first_authorization = self.authorizer.confirm(
            first_plan.id, actor="manager-user", ttl_seconds=60
        )
        second_authorization = self.authorizer.confirm(
            second_plan.id, actor="manager-user", ttl_seconds=60
        )
        real_decrypt = self.cipher.decrypt

        with mock.patch.object(self.cipher, "decrypt", wraps=real_decrypt) as decrypt:
            self.assertEqual("succeeded", service.execute(first_authorization, first_request)["status"])
            self.assertEqual("succeeded", service.execute(second_authorization, second_request)["status"])

        self.assertEqual(2, decrypt.call_count)
        self.assertEqual(
            [self._fingerprint("APPROVED_TEST_CREDENTIAL")] * 2,
            adapter.credential_fingerprints,
        )


if __name__ == "__main__":
    unittest.main()
