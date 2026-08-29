import unittest
import os
from pathlib import Path

from app.agent_backend import AgentBackendRole
from app.agent_backend_protocol import AgentBackendRequest, AgentBackendResult
from app.host_bridge_session import HostBridgeSession


def _request() -> AgentBackendRequest:
    return AgentBackendRequest(
        role=AgentBackendRole.WORKER,
        worktree_path=Path("/tmp/harness-worktree"),
        prompt="只执行 Harness 下发的验证步骤",
        timeout_seconds=30,
        output_contract={"name": "none", "schema_version": "none"},
        capabilities=("source.search",),
    )


def _result() -> AgentBackendResult:
    return AgentBackendResult(
        exit_code=0,
        error_code="",
        event_count=1,
        final_response_sha256="a" * 64,
        canonical_final_response_sha256="b" * 64,
        final_response_validated=True,
        final_response={"ok": True},
    )


class HostBridgeSessionTests(unittest.TestCase):
    def test_session_binds_the_sidecar_identity_for_the_existing_runner(self):
        class Sink:
            def __init__(self):
                self.started = None
                self.events = []

            def on_started(self, pid, identity):
                self.started = (pid, identity)

            def on_event(self, event):
                self.events.append(event)

        sink = Sink()
        session = HostBridgeSession(
            send=lambda message: None,
            receive=lambda: {
                "schema_version": "harness-host-session.v1",
                "type": "agent.result",
                "request_id": "7" * 64,
                "payload": _result().to_dict(),
            },
            request_id=lambda value: "7" * 64,
        )

        session.execute(_request(), sink=sink)

        self.assertIsNotNone(sink.started)
        self.assertEqual(os.getpid(), sink.started[0])
        self.assertTrue(sink.started[1].startswith("darwin-proc-bsdinfo-v1:"))

    def test_round_trip_sends_agent_request_and_accepts_result(self):
        sent = []
        request = _request()
        request_id = "7" * 64

        session = HostBridgeSession(
            send=sent.append,
            receive=lambda: {
                "schema_version": "harness-host-session.v1",
                "type": "agent.result",
                "request_id": request_id,
                "payload": _result().to_dict(),
            },
            request_id=lambda value: request_id,
        )

        result = session.execute(request)

        self.assertEqual(0, result.exit_code)
        self.assertEqual({"ok": True}, result.final_response)
        self.assertEqual("agent.request", sent[0]["type"])
        self.assertEqual(request_id, sent[0]["request_id"])
        self.assertEqual(request.to_dict(), sent[0]["payload"])

    def test_mismatched_result_is_rejected_without_leaking_payload(self):
        session = HostBridgeSession(
            send=lambda message: None,
            receive=lambda: {
                "schema_version": "harness-host-session.v1",
                "type": "agent.result",
                "request_id": "wrong",
                "payload": {"secret": "must-not-escape"},
            },
            request_id=lambda value: "expected",
        )

        with self.assertRaisesRegex(ValueError, "host_session_response_invalid"):
            session.execute(_request())


if __name__ == "__main__":
    unittest.main()
