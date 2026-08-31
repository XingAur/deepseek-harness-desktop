from __future__ import annotations

import json
import io
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import unittest
from pathlib import Path
from unittest import mock

from app import database
from app.model_provider_runtime import (
    MODEL_PROVIDER_RUNTIME_SCHEMA_VERSION,
    ControlledModelProviderRuntime,
    OpenAICompatibleSmokeTransport,
    ProviderSmokeTransportError,
    _CONTROLLED_SMOKE_PERMIT,
    model_provider_smoke_exit_code,
    resolve_manager_provider_profile,
    write_model_provider_smoke_outputs,
)
from app.runtime_policy import RealModelRuntimeFrozenError, RealModelSmokeNotAllowedError
from tools.self_check import run_model_provider_checks
from tools import task_manager


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeTransport:
    def __init__(self, response: dict | None = None, error: Exception | None = None) -> None:
        self.response = response or {
            "choices": [{"message": {"content": "SMOKE_OK"}}],
            "usage": {
                "prompt_tokens": 8,
                "completion_tokens": 2,
                "total_tokens": 10,
            },
        }
        self.error = error
        self.calls: list[dict] = []

    def request(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class RedirectingOpener:
    def __init__(self, *, status: int, location: str, body: bytes) -> None:
        self.status = status
        self.location = location
        self.body = body
        self.requests: list[urllib.request.Request] = []

    def open(self, request: urllib.request.Request, *, timeout: int):
        self.requests.append(request)
        raise urllib.error.HTTPError(
            request.full_url,
            self.status,
            "redirect",
            {"Location": self.location},
            io.BytesIO(self.body),
        )


class ControlledModelProviderRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = self.root / "harness.sqlite"
        database.init_db()
        self.policy_path = self.root / "providers.json"
        self.credentials_path = self.root / "credentials.json"
        self.secret = "test-secret-must-never-be-persisted"
        self.policy_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0-controlled-model-provider-profiles",
                    "profiles": {
                        "test-provider": {
                            "provider_kind": "openai_compatible",
                            "enabled": True,
                            "smoke_enabled": True,
                            "credential_keys": {
                                "api_key": ["openai_api_key"],
                                "base_url": ["openai_base_url"],
                                "model": ["openai_model"],
                            },
                            "allowed_endpoint_hosts": ["api.example.test"],
                            "timeout_seconds": 20,
                            "max_output_tokens": 16,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        self.credentials_path.write_text(
            json.dumps(
                {
                    "openai_api_key": self.secret,
                    "openai_base_url": "https://api.example.test/v1",
                    "openai_model": "test-model",
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_double_gate_rejects_before_reading_credentials_or_calling_transport(self) -> None:
        missing_credentials = self.root / "must-not-be-read.json"
        transport = FakeTransport()
        runtime = ControlledModelProviderRuntime(transport=transport)

        for allow_credentials, allow_network in ((False, False), (True, False), (False, True)):
            with self.subTest(
                allow_credentials=allow_credentials,
                allow_network=allow_network,
            ), self.assertRaisesRegex(PermissionError, "双开关"):
                runtime.run_smoke(
                    profile_policy_path=self.policy_path,
                    profile_key="test-provider",
                    credentials_path=missing_credentials,
                    allow_credentials=allow_credentials,
                    allow_network=allow_network,
                    authorization_id="explicit-test-authorization",
                )

        self.assertFalse(missing_credentials.exists())
        self.assertEqual([], transport.calls)

    def test_disabled_single_node_smoke_rejects_before_policy_or_credentials_are_read(self) -> None:
        missing_policy = self.root / "must-not-read-policy.json"
        missing_credentials = self.root / "must-not-read-credentials.json"
        transport = FakeTransport()
        runtime = ControlledModelProviderRuntime(transport=transport)

        with mock.patch("app.runtime_policy.REAL_MODEL_SMOKE_ALLOWED", False), self.assertRaises(
            RealModelSmokeNotAllowedError
        ) as raised:
            runtime.run_smoke(
                profile_policy_path=missing_policy,
                profile_key="test-provider",
                credentials_path=missing_credentials,
                allow_credentials=True,
                allow_network=True,
                authorization_id="explicit-test-authorization",
            )

        self.assertEqual("real_model_smoke_not_allowed", raised.exception.code)
        self.assertFalse(missing_policy.exists())
        self.assertFalse(missing_credentials.exists())
        self.assertEqual([], transport.calls)

    def test_authorized_single_node_smoke_does_not_require_general_runtime_unfreeze(self) -> None:
        transport = FakeTransport()
        result = ControlledModelProviderRuntime(transport=transport).run_smoke(
            profile_policy_path=self.policy_path,
            profile_key="test-provider",
            credentials_path=self.credentials_path,
            allow_credentials=True,
            allow_network=True,
            authorization_id="single-node-smoke-authorization",
        )

        self.assertEqual("passed", result["smoke"]["status"])
        self.assertTrue(result["single_node_only"])
        self.assertFalse(result["dag_enabled"])
        self.assertEqual(1, len(transport.calls))

    def test_direct_transport_call_remains_blocked_without_controlled_runtime(self) -> None:
        with mock.patch(
            "app.model_provider_runtime.urllib.request.urlopen",
            side_effect=AssertionError("direct transport must not reach the network"),
        ), self.assertRaises(RealModelRuntimeFrozenError):
            OpenAICompatibleSmokeTransport().request(
                url="https://api.example.test/v1/chat/completions",
                payload={"model": "test-model", "messages": []},
                api_key="test-secret",
                timeout_seconds=20,
                _controlled_smoke_permit=object(),
            )

    def test_controlled_transport_rejects_all_redirects_without_proxy_or_second_request(self) -> None:
        secret = "redirect-secret-must-not-leak"
        for status in (301, 302, 303, 307, 308):
            for location in (
                "https://other.example.test/collect",
                "https://api.example.test/other",
            ):
                with self.subTest(status=status, location=location):
                    opener = RedirectingOpener(
                        status=status,
                        location=location,
                        body=f"redirect body Bearer {secret}".encode("utf-8"),
                    )
                    with mock.patch.dict(
                        os.environ,
                        {"HTTPS_PROXY": "http://proxy.example.test:8080", "ALL_PROXY": "http://proxy.example.test:8080"},
                        clear=False,
                    ), mock.patch(
                        "app.model_provider_runtime.urllib.request.build_opener",
                        return_value=opener,
                    ) as build_opener, mock.patch(
                        "app.model_provider_runtime.urllib.request.urlopen",
                        side_effect=opener.open,
                    ), self.assertRaises(ProviderSmokeTransportError) as raised:
                        OpenAICompatibleSmokeTransport().request(
                            url="https://api.example.test/v1/chat/completions",
                            payload={"model": "test-model", "messages": []},
                            api_key=secret,
                            timeout_seconds=20,
                            _controlled_smoke_permit=_CONTROLLED_SMOKE_PERMIT,
                        )

                    self.assertEqual("redirect_not_allowed", raised.exception.code)
                    self.assertEqual("model smoke redirect rejected", raised.exception.detail)
                    self.assertEqual(1, len(opener.requests))
                    self.assertNotIn(location, str(raised.exception))
                    self.assertNotIn(secret, str(raised.exception))
                    handlers = build_opener.call_args.args
                    proxy_handler = handlers[0]
                    self.assertEqual({}, proxy_handler.proxies)
                    self.assertEqual("Bearer " + secret, opener.requests[0].get_header("Authorization"))
                    self.assertNotIn(location, opener.requests[0].full_url)

    def test_fixed_smoke_is_persistent_redacted_and_idempotent(self) -> None:
        transport = FakeTransport()
        runtime = ControlledModelProviderRuntime(transport=transport)
        first = self.run_authorized(runtime)
        repeated = self.run_authorized(runtime)

        self.assertEqual(MODEL_PROVIDER_RUNTIME_SCHEMA_VERSION, database.get_schema_meta("model_provider_runtime"))
        self.assertEqual("passed", first["smoke"]["status"])
        self.assertEqual("passed", first["smoke"]["transport_status"])
        self.assertEqual("passed", first["smoke"]["protocol_status"])
        self.assertEqual("passed", first["smoke"]["marker_status"])
        self.assertTrue(first["connectivity_verified"])
        self.assertEqual("api.example.test", first["smoke"]["endpoint_host"])
        self.assertEqual("test-model", first["smoke"]["model"])
        self.assertEqual("openai_api_key", first["smoke"]["credential_key_names"]["api_key"])
        self.assertEqual({"input_tokens": 8, "output_tokens": 2, "total_tokens": 10}, first["smoke"]["usage"])
        self.assertTrue(first["response_verified"])
        self.assertEqual(first["smoke"]["id"], repeated["smoke"]["id"])
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(1, len(transport.calls))
        self.assertEqual("SMOKE_OK", transport.calls[0]["payload"]["messages"][1]["content"])
        self.assertEqual(16, transport.calls[0]["payload"]["max_tokens"])

        persisted = json.dumps(first, ensure_ascii=False)
        self.assertNotIn(self.secret, persisted)
        self.assertNotIn("Bearer", persisted)
        self.assertNotIn("SMOKE_OK", first["smoke"].get("response_preview", ""))
        self.assertEqual(
            ["authorized", "credentials_resolved", "network_completed", "validated", "persisted"],
            [event["event_type"] for event in first["events"]],
        )

    def test_transport_failure_is_redacted_persisted_and_not_retried(self) -> None:
        transport = FakeTransport(
            error=ProviderSmokeTransportError(
                "http_error",
                f"upstream rejected Authorization: Bearer {self.secret}",
            )
        )
        runtime = ControlledModelProviderRuntime(transport=transport)
        result = self.run_authorized(runtime, authorization_id="failed-request")

        self.assertEqual("failed_transport", result["smoke"]["status"])
        self.assertEqual("failed", result["smoke"]["transport_status"])
        self.assertEqual("not_run", result["smoke"]["protocol_status"])
        self.assertEqual("not_run", result["smoke"]["marker_status"])
        self.assertEqual("http_error", result["smoke"]["error_code"])
        self.assertEqual(1, len(transport.calls))
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(self.secret, rendered)
        self.assertNotIn("Bearer", rendered)
        self.assertIn("[REDACTED]", result["smoke"]["error_detail"])

    def test_non_exact_response_fails_protocol_without_persisting_content(self) -> None:
        transport = FakeTransport(
            response={
                "choices": [{"message": {"content": "SMOKE_OK extra"}}],
                "usage": {},
            }
        )
        result = self.run_authorized(
            ControlledModelProviderRuntime(transport=transport),
            authorization_id="protocol-failure",
        )

        self.assertEqual("failed_protocol", result["smoke"]["status"])
        self.assertEqual("passed", result["smoke"]["transport_status"])
        self.assertEqual("passed", result["smoke"]["protocol_status"])
        self.assertEqual("failed", result["smoke"]["marker_status"])
        self.assertTrue(result["connectivity_verified"])
        self.assertFalse(result["response_verified"])
        self.assertNotIn("SMOKE_OK extra", json.dumps(result, ensure_ascii=False))

    def test_invalid_response_shape_records_one_network_attempt(self) -> None:
        transport = FakeTransport(response={"choices": []})
        result = self.run_authorized(
            ControlledModelProviderRuntime(transport=transport),
            authorization_id="invalid-response-shape",
        )

        self.assertEqual("failed_protocol", result["smoke"]["status"])
        self.assertEqual("passed", result["smoke"]["transport_status"])
        self.assertEqual("failed", result["smoke"]["protocol_status"])
        self.assertEqual("not_run", result["smoke"]["marker_status"])
        self.assertFalse(result["connectivity_verified"])
        self.assertEqual("response_shape_invalid", result["smoke"]["error_code"])
        self.assertEqual(1, len(transport.calls))
        self.assertEqual(
            ["authorized", "credentials_resolved", "network_completed", "validated", "persisted"],
            [event["event_type"] for event in result["events"]],
        )
        self.assertEqual("passed", result["events"][2]["status"])
        self.assertEqual("failed", result["events"][3]["status"])

    def test_outputs_are_redacted_and_marked_single_node_only(self) -> None:
        result = self.run_authorized(ControlledModelProviderRuntime(transport=FakeTransport()))
        files = write_model_provider_smoke_outputs(self.root / "outputs", result)
        rendered = "\n".join(path.read_text(encoding="utf-8") for path in files)

        self.assertEqual(3, len(files))
        self.assertNotIn(self.secret, rendered)
        self.assertIn("single-node-smoke-only", rendered)
        self.assertIn("api.example.test", rendered)

    def test_cli_exit_code_requires_all_three_smoke_layers_to_pass(self) -> None:
        passed = self.run_authorized(ControlledModelProviderRuntime(transport=FakeTransport()))
        marker_failed = self.run_authorized(
            ControlledModelProviderRuntime(
                transport=FakeTransport(
                    response={
                        "choices": [{"message": {"content": "SMOKE_OK extra"}}],
                        "usage": {},
                    }
                )
            ),
            authorization_id="marker-exit-code",
        )

        self.assertEqual(0, model_provider_smoke_exit_code(passed))
        self.assertEqual(2, model_provider_smoke_exit_code(marker_failed))

    def test_cli_rejects_before_credentials_read_when_one_switch_is_missing(self) -> None:
        missing_credentials = self.root / "cli-must-not-be-read.json"
        env = os.environ.copy()
        env["HARNESS_DB_PATH"] = str(self.root / "cli.sqlite")
        completed = subprocess.run(
            [
                sys.executable,
                "tools/task_manager.py",
                "run-model-provider-smoke",
                "--profile-policy",
                str(self.policy_path),
                "--profile-key",
                "test-provider",
                "--credentials-file",
                str(missing_credentials),
                "--allow-credentials",
                "--authorization-id",
                "explicit-cli-test-authorization",
            ],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(2, completed.returncode)
        self.assertIn("Legacy model provider smoke is blocked.", completed.stdout)
        self.assertNotIn("双开关", completed.stderr)
        self.assertFalse(missing_credentials.exists())

    def test_legacy_cli_is_blocked_even_with_all_flags_and_never_constructs_runtime(self) -> None:
        stdout = io.StringIO()
        original_read_text = Path.read_text

        def reject_legacy_input(path: Path, *args: object, **kwargs: object) -> str:
            if path in {self.policy_path, self.credentials_path}:
                raise AssertionError("legacy CLI must not read credential or policy files")
            return original_read_text(path, *args, **kwargs)

        with mock.patch.object(
            sys,
            "argv",
            [
                "task_manager.py",
                "run-model-provider-smoke",
                "--profile-policy",
                str(self.policy_path),
                "--profile-key",
                "test-provider",
                "--credentials-file",
                str(self.credentials_path),
                "--allow-credentials",
                "--allow-network",
                "--authorization-id",
                "legacy-authorization-id-must-not-be-used",
                "--json",
            ],
        ), mock.patch(
            "tools.task_manager.ControlledModelProviderRuntime",
            side_effect=AssertionError("legacy CLI must not construct a runtime"),
        ) as runtime_constructor, mock.patch(
            "app.runtime_policy.REAL_MODEL_SMOKE_ALLOWED", True
        ), mock.patch.object(
            Path,
            "read_text",
            autospec=True,
            side_effect=reject_legacy_input,
        ) as read_text, mock.patch("sys.stdout", stdout), self.assertRaises(SystemExit) as raised:
            task_manager.main()

        self.assertEqual(2, raised.exception.code)
        runtime_constructor.assert_not_called()
        self.assertNotIn(
            self.policy_path,
            [call.args[0] for call in read_text.call_args_list],
        )
        self.assertNotIn(
            self.credentials_path,
            [call.args[0] for call in read_text.call_args_list],
        )
        rendered = stdout.getvalue()
        self.assertIn("legacy_model_provider_smoke_disabled", rendered)
        self.assertNotIn(self.secret, rendered)
        self.assertNotIn("legacy-authorization-id-must-not-be-used", rendered)

    def test_provider_self_check_is_repeatable_with_retained_database(self) -> None:
        output_dir = self.root / "self-check"
        first = run_model_provider_checks(output_dir=output_dir)
        second = run_model_provider_checks(output_dir=output_dir)

        self.assertTrue(all(item["status"] == "pass" for item in first))
        self.assertTrue(all(item["status"] == "pass" for item in second))

    def test_manager_resolver_rejects_non_allowlisted_endpoint_without_echoing_api_key(self) -> None:
        sentinel = "manager-resolver-secret-must-not-render"

        with self.assertRaises(PermissionError) as raised:
            resolve_manager_provider_profile(
                profile_key="manager-demo",
                connection={
                    "provider_kind": "openai_compatible",
                    "base_url": "https://other.example.test/v1",
                    "allowed_endpoint_host": "api.example.test",
                    "model": "safe-test-model",
                    "timeout_seconds": "10",
                    "max_output_tokens": "16",
                },
                api_key=sentinel,
            )

        self.assertNotIn(sentinel, str(raised.exception))

    def run_authorized(
        self,
        runtime: ControlledModelProviderRuntime,
        *,
        authorization_id: str = "explicit-test-authorization",
    ) -> dict:
        return runtime.run_smoke(
            profile_policy_path=self.policy_path,
            profile_key="test-provider",
            credentials_path=self.credentials_path,
            allow_credentials=True,
            allow_network=True,
            authorization_id=authorization_id,
            allow_frozen_test_transport=True,
        )


if __name__ == "__main__":
    unittest.main()
