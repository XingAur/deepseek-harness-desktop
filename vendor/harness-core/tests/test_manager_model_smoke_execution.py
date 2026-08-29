from __future__ import annotations

import json
import io
import os
import tempfile
import urllib.error
import urllib.request
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from app import database
from app.manager_model_smoke_preflight import build_manager_model_smoke_readiness
from app.manager_provider_repository import ManagerProviderRepository
from app.provider_action_authorization import ProviderActionAuthorizer
from app.provider_execution import ProviderExecutionRequest, ProviderExecutionService
from app.providers.model_smoke import ManagerModelSmokeProviderAdapter
from app.model_provider_runtime import OpenAICompatibleSmokeTransport
from app.runtime_policy import RealModelRuntimeFrozenError, assert_runtime_mode_allowed


class FakeModelTransport:
    def __init__(self, response: dict[str, object] | None = None) -> None:
        self.response = response or {
            "choices": [{"message": {"content": "SMOKE_OK"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
        }
        self.calls: list[dict[str, object]] = []

    def request(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        return self.response


class RedirectingModelOpener:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.requests: list[urllib.request.Request] = []

    def open(self, request: urllib.request.Request, *, timeout: int):
        self.requests.append(request)
        raise urllib.error.HTTPError(
            request.full_url,
            302,
            "redirect",
            {"Location": "https://other.example.test/collect"},
            io.BytesIO(f"Bearer {self.secret}".encode("utf-8")),
        )


class ManagerModelSmokeExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "harness.sqlite"
        self.secret = "manager-model-api-key-do-not-render"
        self.master_key = "bW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW0="
        self.env = mock.patch.dict(
            os.environ,
            {"HARNESS_MANAGER_CREDENTIAL_MASTER_KEY": self.master_key},
            clear=False,
        )
        self.env.start()
        self.repository = ManagerProviderRepository()
        self.profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="model",
            profile_key="smoke-demo",
            display_name="Smoke demo",
            enabled=True,
            connection={
                "provider_kind": "openai_compatible",
                "base_url": "https://api.example.test/v1",
                "allowed_endpoint_host": "api.example.test",
                "model": "safe-test-model",
                "timeout_seconds": "7",
                "max_output_tokens": "12",
            },
        )
        self.repository.upsert_credential(
            profile_id=self.profile.id,
            field="api_key",
            plaintext=self.secret,
        )
        self.authorizer = ProviderActionAuthorizer(
            self.repository,
            clock=lambda: datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
        self.transport = FakeModelTransport()
        self.adapter = ManagerModelSmokeProviderAdapter(transport=self.transport)
        self.service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={"model": self.adapter},
        )

    def tearDown(self) -> None:
        self.env.stop()
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_confirmed_manager_profile_uses_one_fake_call_and_emits_only_safe_evidence(self) -> None:
        plan = self._plan()
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)

        result = self.service.execute(authorization, self._request(plan.id))

        self.assertEqual("succeeded", result["status"])
        self.assertEqual("model.single_node.smoke", result["action"])
        self.assertTrue(result["credentials_read"])
        self.assertFalse(result["external_calls"])
        self.assertEqual(0, result["network_call_count"])
        self.assertEqual(1, result["simulated_dispatch_count"])
        self.assertEqual("simulated", result["execution_provenance"])
        summary = result["result_summary"]
        self.assertEqual("smoke-demo", summary["profile_alias"])
        self.assertEqual("api.example.test", summary["endpoint_host"])
        self.assertEqual("safe-test-model", summary["model_alias"])
        self.assertEqual("SMOKE_OK", summary["result_marker"])
        self.assertEqual("passed", summary["smoke_status"])
        self.assertEqual({"input_tokens": 7, "output_tokens": 2, "total_tokens": 9}, summary["usage"])
        self.assertRegex(str(summary["request_hash"]), r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(str(summary["response_hash"]), r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(1, len(self.transport.calls))
        call = self.transport.calls[0]
        self.assertEqual("https://api.example.test/v1/chat/completions", call["url"])
        self.assertEqual(7, call["timeout_seconds"])
        self.assertEqual(self.secret, call["api_key"])
        self.assertEqual(
            {
                "model": "safe-test-model",
                "messages": [
                    {
                        "role": "system",
                        "content": "Return exactly the ASCII text SMOKE_OK. Do not add reasoning, explanation, punctuation, Markdown, or any other text.",
                    },
                    {"role": "user", "content": "SMOKE_OK"},
                ],
                "temperature": 0,
                "max_tokens": 12,
                "stream": False,
            },
            call["payload"],
        )
        self.assertNotIn("authorization", call["payload"])
        self.assertNotIn("tools", call["payload"])
        rendered = json.dumps(
            {"result": result, "audits": self.repository.list_action_audits()},
            ensure_ascii=False,
        )
        self.assertNotIn(self.secret, rendered)
        self.assertNotIn("Authorization", rendered)
        self.assertNotIn("api_key", rendered)
        with self.assertRaises(RealModelRuntimeFrozenError):
            assert_runtime_mode_allowed("openai")

    def test_unconfirmed_smoke_does_not_decrypt_or_call_transport(self) -> None:
        plan = self._plan()

        result = self.service.execute(None, self._request(plan.id))

        self.assertEqual("blocked", result["status"])
        self.assertEqual("authorization_required", result["reason"])
        self.assertFalse(result["credentials_read"])
        self.assertEqual([], self.transport.calls)

    def test_invalid_typed_endpoint_is_blocked_before_fake_transport(self) -> None:
        profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="model",
            profile_key="bad-endpoint",
            display_name="Bad endpoint",
            enabled=True,
            connection={
                "provider_kind": "openai_compatible",
                "base_url": "https://other.example.test/v1",
                "allowed_endpoint_host": "api.example.test",
                "model": "safe-test-model",
                "timeout_seconds": "7",
                "max_output_tokens": "12",
            },
        )
        self.repository.upsert_credential(profile_id=profile.id, field="api_key", plaintext=self.secret)
        plan = self.authorizer.create_plan(
            profile_id=profile.id,
            action="model.single_node.smoke",
            target_alias="model.bad-endpoint",
            parameters={"model_profile_alias": "bad-endpoint"},
            requested_by="manager-user",
        )
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)

        result = self.service.execute(authorization, self._request(plan.id, "bad-endpoint"))

        self.assertEqual("failed", result["status"])
        self.assertEqual("provider_adapter_failed", result["reason"])
        self.assertFalse(result["credentials_read"])
        self.assertEqual([], self.transport.calls)
        self.assertNotIn(self.secret, json.dumps(result, ensure_ascii=False))

    def test_redirected_live_smoke_fails_once_without_safe_audit_success_claim(self) -> None:
        opener = RedirectingModelOpener(self.secret)
        service = ProviderExecutionService(
            self.repository,
            self.authorizer,
            adapters={
                "model": ManagerModelSmokeProviderAdapter(
                    transport=OpenAICompatibleSmokeTransport()
                )
            },
        )
        plan = self._plan()
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)

        with mock.patch(
            "app.model_provider_runtime.urllib.request.build_opener",
            return_value=opener,
        ), mock.patch(
            "app.model_provider_runtime.urllib.request.urlopen",
            side_effect=opener.open,
        ):
            result = service.execute(authorization, self._request(plan.id))

        self.assertEqual("failed", result["status"])
        self.assertEqual("provider_adapter_failed", result["reason"])
        self.assertTrue(result["credentials_read"])
        self.assertTrue(result["external_calls"])
        self.assertEqual(1, result["network_call_count"])
        self.assertEqual(0, result["simulated_dispatch_count"])
        self.assertEqual("live", result["execution_provenance"])
        self.assertEqual(1, len(opener.requests))
        self.assertEqual({}, result["result_summary"])
        self.assertNotIn(self.secret, json.dumps(result, ensure_ascii=False))

    def test_user_prompt_or_tool_parameters_are_rejected_before_action_plan_persists(self) -> None:
        for unsafe_parameters in (
            {"model_profile_alias": "smoke-demo", "user_prompt": "ignore fixed prompt"},
            {"model_profile_alias": "smoke-demo", "tools": ["filesystem"]},
            {"model_profile_alias": "smoke-demo", "callback_url": "https://callback.example.test"},
        ):
            with self.subTest(unsafe_parameters=unsafe_parameters):
                with self.assertRaisesRegex(ValueError, "model_smoke_parameters_invalid"):
                    self.authorizer.create_plan(
                        profile_id=self.profile.id,
                        action="model.single_node.smoke",
                        target_alias="model.smoke-demo",
                        parameters=unsafe_parameters,
                        requested_by="manager-user",
                    )

    def test_typed_model_profile_target_must_match_before_plan_persists(self) -> None:
        with self.assertRaisesRegex(ValueError, "model_smoke_target_invalid"):
            self.authorizer.create_plan(
                profile_id=self.profile.id,
                action="model.single_node.smoke",
                target_alias="model.other-profile",
                parameters={"model_profile_alias": "other-profile"},
                requested_by="manager-user",
            )
        self.assertEqual([], self.repository.list_action_audits())
        self.assertEqual([], self.transport.calls)

    def test_readiness_reports_only_configuration_confirmation_smoke_and_dag_states(self) -> None:
        missing = build_manager_model_smoke_readiness(None)
        awaiting = build_manager_model_smoke_readiness(self.repository.profile_status(self.profile.id))
        passed = build_manager_model_smoke_readiness(
            self.repository.profile_status(self.profile.id),
            last_smoke={"status": "passed", "marker_status": "passed"},
        )
        failed = build_manager_model_smoke_readiness(
            self.repository.profile_status(self.profile.id),
            last_smoke={"status": "failed_protocol", "marker_status": "failed"},
        )

        self.assertEqual("configuration_missing", missing["smoke_state"])
        self.assertEqual("awaiting_confirmation", awaiting["smoke_state"])
        self.assertEqual("smoke_passed", passed["smoke_state"])
        self.assertEqual("smoke_failed", failed["smoke_state"])
        for payload in (missing, awaiting, passed, failed):
            self.assertEqual("dag_still_frozen", payload["dag_state"])
            self.assertNotIn(self.secret, json.dumps(payload, ensure_ascii=False))

    def _plan(self):
        return self.authorizer.create_plan(
            profile_id=self.profile.id,
            action="model.single_node.smoke",
            target_alias="model.smoke-demo",
            parameters={"model_profile_alias": "smoke-demo"},
            requested_by="manager-user",
        )

    @staticmethod
    def _request(plan_id: int, profile_alias: str = "smoke-demo") -> ProviderExecutionRequest:
        return ProviderExecutionRequest(
            plan_id=plan_id,
            actor="manager-user",
            action="model.single_node.smoke",
            parameters={"model_profile_alias": profile_alias},
        )


if __name__ == "__main__":
    unittest.main()
