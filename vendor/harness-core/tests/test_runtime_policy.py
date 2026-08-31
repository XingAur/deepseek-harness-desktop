from __future__ import annotations

import copy
import dataclasses
import os
import hashlib
import pickle
import unittest
from unittest.mock import patch

import app.runtime_policy as runtime_policy_module
from app.llm_client import (
    AnthropicCompatibleClient,
    MockLLMClient,
    OpenAICompatibleClient,
    get_legacy_llm_client,
    get_llm_client,
    smoke_test,
)
from app.runtime_policy import (
    REAL_MODEL_RUNTIME_FROZEN,
    LocalAgentActivationPreflight,
    LocalAgentRunNotAllowedError,
    RealModelRuntimeFrozenError,
    assert_local_agent_run_allowed,
    assert_model_provider_smoke_allowed,
)
from app.runtime_policy import runtime_policy_snapshot


class RuntimePolicyTests(unittest.TestCase):
    def test_local_agent_activation_requires_explicit_preflight(self) -> None:
        authorization_id = "approved-local-agent-run-001"

        with self.assertRaises(LocalAgentRunNotAllowedError):
            assert_local_agent_run_allowed(
                allow_real_agent=False,
                authorization_id=authorization_id,
            )

        preflight = assert_local_agent_run_allowed(
            allow_real_agent=True,
            authorization_id=authorization_id,
        )
        repeated_preflight = assert_local_agent_run_allowed(
            allow_real_agent=True,
            authorization_id=authorization_id,
        )

        verifier = getattr(
            runtime_policy_module, "verify_local_agent_activation_preflight", None
        )
        self.assertTrue(callable(verifier), "opaque preflight verifier is required")
        expected_hash = (
            "sha256:" + hashlib.sha256(authorization_id.encode("utf-8")).hexdigest()
        )
        self.assertEqual(expected_hash, verifier(preflight))
        self.assertEqual(expected_hash, verifier(repeated_preflight))
        self.assertFalse(hasattr(preflight, "authorization_hash"))
        self.assertFalse(hasattr(preflight, "consumed"))
        self.assertNotIn(authorization_id, repr(preflight))
        self.assertNotIn(expected_hash, repr(preflight))
        self.assertTrue(REAL_MODEL_RUNTIME_FROZEN)

    def test_local_agent_preflight_rejects_construction_and_forgery(self) -> None:
        preflight = assert_local_agent_run_allowed(
            allow_real_agent=True,
            authorization_id="approved-local-agent-run-002",
        )
        with self.assertRaises(TypeError):
            dataclasses.replace(
                preflight,
                authorization_hash="sha256:" + "0" * 64,
            )
        verifier = getattr(
            runtime_policy_module, "verify_local_agent_activation_preflight", None
        )
        self.assertTrue(callable(verifier), "opaque preflight verifier is required")

        with self.assertRaises(LocalAgentRunNotAllowedError):
            LocalAgentActivationPreflight()

        class FakePreflight:
            authorization_hash = "sha256:" + "0" * 64

        for forged in (
            copy.copy(preflight),
            copy.deepcopy(preflight),
            object.__new__(LocalAgentActivationPreflight),
            FakePreflight(),
        ):
            with self.subTest(forged_type=type(forged).__name__), self.assertRaises(
                LocalAgentRunNotAllowedError
            ):
                verifier(forged)

    def test_local_agent_preflight_rejects_serialization(self) -> None:
        preflight = assert_local_agent_run_allowed(
            allow_real_agent=True,
            authorization_id="approved-local-agent-run-003",
        )

        try:
            pickle.dumps(preflight)
        except Exception as raised:
            self.assertIsInstance(raised, LocalAgentRunNotAllowedError)
        else:
            self.fail("opaque preflight must not be serializable")

    def test_real_modes_are_frozen_before_local_credentials_are_read(self) -> None:
        for mode in ("openai", "real", "anthropic", "claude", "zhipu"):
            with self.subTest(mode=mode), patch(
                "app.llm_client.load_claude_settings_env_if_requested",
                side_effect=AssertionError("Claude settings must not be read"),
            ), patch(
                "app.llm_client.load_local_llm_credentials_env_if_available",
                side_effect=AssertionError("local credentials must not be read"),
            ), self.assertRaises(RealModelRuntimeFrozenError) as raised:
                get_llm_client(mode)

            self.assertEqual("real_model_runtime_frozen", raised.exception.code)

    def test_mock_mode_remains_available_only_with_explicit_allow_mock(self) -> None:
        with patch(
            "app.llm_client.load_local_llm_credentials_env_if_available",
            side_effect=AssertionError("mock mode must not read credentials"),
        ):
            client = get_llm_client("mock", allow_mock=True)

        self.assertIsInstance(client, MockLLMClient)
        with self.assertRaisesRegex(RuntimeError, "allow_mock=True"):
            get_llm_client("mock")

    def test_unspecified_mode_defaults_to_local_mock_without_reading_credentials(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch(
            "app.llm_client.load_local_llm_credentials_env_if_available",
            side_effect=AssertionError("default mock mode must not read credentials"),
        ):
            client = get_llm_client(allow_mock=True)

        self.assertIsInstance(client, MockLLMClient)

    def test_legacy_real_mode_is_frozen_before_credentials_are_read(self) -> None:
        with patch.dict(os.environ, {"HARNESS_LLM_MODE": "openai"}, clear=False), patch(
            "app.llm_client.load_local_llm_credentials_env_if_available",
            side_effect=AssertionError("local credentials must not be read"),
        ), self.assertRaises(RealModelRuntimeFrozenError):
            get_legacy_llm_client()

    def test_direct_real_clients_and_smoke_are_frozen(self) -> None:
        with self.assertRaises(RealModelRuntimeFrozenError):
            OpenAICompatibleClient()
        with self.assertRaises(RealModelRuntimeFrozenError):
            AnthropicCompatibleClient()

        class RealLikeClient(MockLLMClient):
            mode = "openai"
            is_mock = False

        with self.assertRaises(RealModelRuntimeFrozenError):
            smoke_test(RealLikeClient())

    def test_policy_reports_separate_real_runtime_and_smoke_switches(self) -> None:
        snapshot = runtime_policy_snapshot()

        self.assertTrue(snapshot.real_model_runtime_frozen)
        self.assertTrue(snapshot.real_model_smoke_allowed)
        self.assertFalse(snapshot.paid_network_calls_allowed)
        assert_model_provider_smoke_allowed()
        self.assertEqual(
            {
                "real_model_runtime_frozen": True,
                "real_model_smoke_allowed": True,
                "paid_network_calls_allowed": False,
            },
            {
                key: snapshot.to_dict()[key]
                for key in (
                    "real_model_runtime_frozen",
                    "real_model_smoke_allowed",
                    "paid_network_calls_allowed",
                )
            },
        )


if __name__ == "__main__":
    unittest.main()
