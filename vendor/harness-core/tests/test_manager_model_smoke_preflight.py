from __future__ import annotations

import os
import unittest
from unittest import mock

from app.manager_model_smoke_preflight import build_model_smoke_preflight


class ManagerModelSmokePreflightTests(unittest.TestCase):
    def test_preflight_is_blocked_without_master_key_and_never_executes(self) -> None:
        profile = {
            "id": 7,
            "provider": "model",
            "profile_key": "demo",
            "display_name": "Demo",
            "enabled": True,
            "connection": {
                "provider_kind": "openai_compatible",
                "base_url": "https://api.example.test/v1",
                "allowed_endpoint_host": "api.example.test",
                "model": "demo-model",
                "timeout_seconds": "10",
                "max_output_tokens": "16",
            },
            "credentials": {"api_key": "configured"},
        }

        with mock.patch.dict(
            os.environ,
            {"HARNESS_MANAGER_CREDENTIAL_MASTER_KEY": ""},
            clear=False,
        ), mock.patch(
            "app.model_provider_runtime.ControlledModelProviderRuntime.run_smoke",
            side_effect=AssertionError("preflight must not run a model"),
        ) as run_smoke, mock.patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("preflight must not call a network"),
        ) as urlopen:
            payload = build_model_smoke_preflight(profile)

        self.assertEqual("his-manager-model-smoke-preflight.v1", payload["schema_version"])
        self.assertEqual("blocked", payload["status"])
        self.assertEqual("encryption_unavailable", payload["reason"])
        self.assertEqual("demo", payload["profile"]["profile_key"])
        self.assertTrue(payload["credential_configured"])
        self.assertFalse(payload["credentials_read"])
        self.assertFalse(payload["external_calls"])
        self.assertFalse(payload["write_performed"])
        run_smoke.assert_not_called()
        urlopen.assert_not_called()

    def test_preflight_ready_only_means_configuration_is_prepared(self) -> None:
        profile = {
            "id": 7,
            "provider": "model",
            "profile_key": "demo",
            "display_name": "Demo",
            "enabled": True,
            "connection": {
                "provider_kind": "openai_compatible",
                "base_url": "https://api.example.test/v1",
                "allowed_endpoint_host": "api.example.test",
                "model": "demo-model",
                "timeout_seconds": "10",
                "max_output_tokens": "16",
            },
            "credentials": {"api_key": "configured"},
        }

        with mock.patch.dict(
            os.environ,
            {
                "HARNESS_MANAGER_CREDENTIAL_MASTER_KEY": (
                    "bW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW0="
                )
            },
            clear=False,
        ):
            payload = build_model_smoke_preflight(profile)

        prerequisites = {item["id"]: item["status"] for item in payload["prerequisites"]}
        self.assertEqual("ready", payload["status"])
        self.assertEqual("configuration_preflight_only", payload["reason"])
        self.assertEqual("passed", prerequisites["model_profile_enabled"])
        self.assertEqual("passed", prerequisites["model_connection_configured"])
        self.assertEqual("passed", prerequisites["api_key_configured"])
        self.assertEqual("passed", prerequisites["single_node_smoke_contract"])
        self.assertFalse(payload["runtime_verified"])
        self.assertFalse(payload["credentials_read"])
        self.assertFalse(payload["external_calls"])
        self.assertFalse(payload["write_performed"])

    def test_preflight_rejects_non_model_or_missing_profile_without_echoing_connection(self) -> None:
        sentinel = "SENTINEL_NOT_RENDERED"
        with mock.patch.dict(
            os.environ,
            {
                "HARNESS_MANAGER_CREDENTIAL_MASTER_KEY": (
                    "bW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW0="
                )
            },
            clear=False,
        ):
            missing = build_model_smoke_preflight(None, requested_profile_key="missing")
            wrong_provider = build_model_smoke_preflight(
                {
                    "provider": "gitlab",
                    "profile_key": "wrong",
                    "display_name": sentinel,
                    "enabled": True,
                    "connection": {"host": sentinel},
                    "credentials": {},
                }
            )

        self.assertEqual("profile_not_found", missing["reason"])
        self.assertEqual("profile_not_model", wrong_provider["reason"])
        self.assertNotIn(sentinel, str(wrong_provider))

    def test_preflight_rejects_malformed_master_key_without_decrypt_or_execution(self) -> None:
        profile = {
            "id": 7,
            "provider": "model",
            "profile_key": "demo",
            "display_name": "Demo",
            "enabled": True,
            "connection": {
                "provider_kind": "openai_compatible",
                "base_url": "https://api.example.test/v1",
                "allowed_endpoint_host": "api.example.test",
                "model": "demo-model",
                "timeout_seconds": "10",
                "max_output_tokens": "16",
            },
            "credentials": {"api_key": "configured"},
        }

        with mock.patch(
            "app.manager_provider_repository.ManagerProviderRepository.resolve_credential_for_authorized_executor",
            side_effect=AssertionError("preflight must not resolve credentials"),
        ) as resolve, mock.patch(
            "app.manager_credential_crypto.AesGcmCredentialCipher.decrypt",
            side_effect=AssertionError("preflight must not decrypt credentials"),
        ) as decrypt, mock.patch(
            "app.model_provider_runtime.ControlledModelProviderRuntime.run_smoke",
            side_effect=AssertionError("preflight must not run a model"),
        ) as run_smoke, mock.patch(
            "app.model_provider_runtime.OpenAICompatibleSmokeTransport.request",
            side_effect=AssertionError("preflight must not call a transport"),
        ) as transport, mock.patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("preflight must not call a network"),
        ) as urlopen:
            for master_key in ("not-base64!", "c2hvcnQ="):
                with self.subTest(master_key=master_key), mock.patch.dict(
                    os.environ,
                    {"HARNESS_MANAGER_CREDENTIAL_MASTER_KEY": master_key},
                    clear=False,
                ):
                    payload = build_model_smoke_preflight(profile)
                    self.assertEqual("blocked", payload["status"])
                    self.assertEqual("encryption_unavailable", payload["reason"])
                    self.assertFalse(payload["credentials_read"])
                    self.assertFalse(payload["external_calls"])

        resolve.assert_not_called()
        decrypt.assert_not_called()
        run_smoke.assert_not_called()
        transport.assert_not_called()
        urlopen.assert_not_called()

    def test_preflight_requires_all_typed_manager_smoke_controls(self) -> None:
        profile = {
            "id": 7,
            "provider": "model",
            "profile_key": "demo",
            "enabled": True,
            "connection": {
                "provider_kind": "openai_compatible",
                "base_url": "https://api.example.test/v1",
                "model": "demo-model",
            },
            "credentials": {"api_key": "configured"},
        }

        with mock.patch.dict(
            os.environ,
            {
                "HARNESS_MANAGER_CREDENTIAL_MASTER_KEY": (
                    "bW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW1tbW0="
                )
            },
            clear=False,
        ):
            payload = build_model_smoke_preflight(profile)

        self.assertEqual("blocked", payload["status"])
        self.assertEqual("connection_incomplete", payload["reason"])
        self.assertEqual("configuration_missing", payload["smoke_state"])


if __name__ == "__main__":
    unittest.main()
