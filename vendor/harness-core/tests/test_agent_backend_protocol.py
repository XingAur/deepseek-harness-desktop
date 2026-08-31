from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.agent_backend import AgentBackendRole
from app.agent_backend_protocol import (
    AgentBackendEvent,
    AgentBackendRequest,
    AgentBackendResult,
    parse_request,
)


def _request() -> dict[str, object]:
    return {
        "schema_version": "his-agent-backend-request.v1",
        "role": "worker",
        "worktree_path": "/private/tmp/harness-fixture",
        "prompt": "Read the isolated fixture and make the requested local change.",
        "timeout_seconds": 60,
        "output_contract": {"name": "none", "schema_version": "none"},
        "capabilities": ["source.search", "verification.run-local"],
    }


class AgentBackendProtocolTests(unittest.TestCase):
    def test_request_round_trips_without_provider_specific_fields(self) -> None:
        request = parse_request(_request())

        self.assertEqual(AgentBackendRole.WORKER, request.role)
        self.assertEqual(
            {
                "schema_version": "his-agent-backend-request.v1",
                "role": "worker",
                "worktree_path": "/private/tmp/harness-fixture",
                "prompt": "Read the isolated fixture and make the requested local change.",
                "timeout_seconds": 60,
                "output_contract": {"name": "none", "schema_version": "none"},
                "capabilities": ["source.search", "verification.run-local"],
            },
            request.to_dict(),
        )

    def test_request_rejects_sensitive_or_opaque_provider_data(self) -> None:
        for field, value in (
            ("prompt", "Use token=secret-value to call the provider."),
            ("thread_id", "opaque-thread-id"),
            ("provider_payload", {"model": "hidden"}),
        ):
            payload = _request()
            payload[field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "agent_backend_request_invalid"):
                    parse_request(payload)

    def test_event_and_result_are_bounded_and_do_not_carry_model_identifiers(self) -> None:
        event = AgentBackendEvent(type="progress", sequence_no=1, item_type="message")
        self.assertEqual(
            {
                "schema_version": "his-agent-backend-event.v1",
                "type": "progress",
                "sequence_no": 1,
                "item_type": "message",
            },
            event.to_dict(),
        )
        result = AgentBackendResult(
            exit_code=0,
            error_code="",
            event_count=1,
            final_response_sha256="a" * 64,
            canonical_final_response_sha256="b" * 64,
            final_response_validated=False,
        )
        self.assertEqual("his-agent-backend-result.v1", result.to_dict()["schema_version"])
        with self.assertRaisesRegex(ValueError, "agent_backend_event_invalid"):
            AgentBackendEvent(type="progress", sequence_no=1, item_type="Bearer secret-value")

    def test_bridge_cli_is_json_only_and_never_starts_a_provider(self) -> None:
        root = Path(__file__).resolve().parents[1]
        command = [sys.executable, str(root / "tools" / "harness_agent_bridge.py"), "describe"]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(0, completed.returncode)
        self.assertEqual("", completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual("his-agent-backend-bridge.v1", payload["schema_version"])
        self.assertEqual(["describe", "validate-request", "negotiate"], payload["operations"])
        self.assertEqual(
            {"terminal", "codex-app", "codex-cli", "deepseek-harness-desktop"},
            {item["host_id"] for item in payload["hosts"]},
        )

        with tempfile.TemporaryDirectory() as directory:
            request_file = Path(directory) / "request.json"
            request_file.write_text(json.dumps(_request()), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(root / "tools" / "harness_agent_bridge.py"),
                    "validate-request",
                    "--request-file",
                    str(request_file),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode)
            self.assertEqual("", completed.stderr)
            self.assertTrue(json.loads(completed.stdout)["valid"])

            negotiation_file = Path(directory) / "negotiation.json"
            negotiation_file.write_text(json.dumps({
                "schema_version": "his-agent-host-negotiation.v1",
                "host_id": "codex-app",
                "role": "worker",
                "required_capabilities": ["source.search"],
                "requested_mutation_level": "L0",
            }), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(root / "tools" / "harness_agent_bridge.py"),
                    "negotiate",
                    "--request-file",
                    str(negotiation_file),
                    "--authorized-mutation-level",
                    "L0",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, completed.returncode)
            self.assertTrue(json.loads(completed.stdout)["negotiation"]["negotiated"])

            negotiation_file.write_text(json.dumps({
                "schema_version": "his-agent-host-negotiation.v1",
                "host_id": "codex-app",
                "role": "worker",
                "required_capabilities": ["source.search"],
                "requested_mutation_level": "L2",
            }), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(root / "tools" / "harness_agent_bridge.py"),
                    "negotiate",
                    "--request-file",
                    str(negotiation_file),
                    "--authorized-mutation-level",
                    "L0",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(2, completed.returncode)
            self.assertEqual("host_mutation_not_authorized", json.loads(completed.stdout)["error_code"])


if __name__ == "__main__":
    unittest.main()
