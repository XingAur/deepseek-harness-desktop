import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agent_backend import AgentBackendRole
from app.agent_backend_protocol import AgentBackendRequest, AgentBackendResult
from app.host_adapter import HostAdapterSession
from app.local_agent_repository import LocalAgentRunRepository
from app.local_agent_runner import LocalAgentRunner


def _request() -> AgentBackendRequest:
    return AgentBackendRequest(
        role=AgentBackendRole.WORKER,
        worktree_path=Path(tempfile.mkdtemp()),
        prompt="只执行本地验证",
        timeout_seconds=30,
        output_contract={"name": "none", "schema_version": "none"},
        capabilities=("source.search",),
    )


class HostAdapterSessionTests(unittest.TestCase):
    def test_jsonl_request_is_delivered_as_provider_neutral_result(self):
        calls = []

        def handler(request, sink):
            calls.append((request, sink))
            return AgentBackendResult(
                exit_code=0,
                error_code="",
                event_count=1,
                final_response_sha256="",
                canonical_final_response_sha256="",
                final_response_validated=False,
                final_response={"ok": True},
            )

        session = HostAdapterSession(handler)
        payload = json.loads(session.handle_json_line(json.dumps(_request().to_dict())))

        self.assertEqual("his-agent-backend-result.v1", payload["schema_version"])
        self.assertEqual(0, payload["exit_code"])
        self.assertEqual({"ok": True}, payload["final_response"])
        self.assertEqual(1, len(calls))
        self.assertIsInstance(calls[0][0], AgentBackendRequest)
        self.assertEqual("worker", calls[0][0].role.value)
        self.assertNotIn("model", calls[0][0].to_dict())

    def test_embedded_request_uses_the_same_session_contract(self):
        session = HostAdapterSession(
            lambda request, sink: AgentBackendResult(
                exit_code=0,
                error_code="",
                event_count=0,
                final_response_sha256="",
                canonical_final_response_sha256="",
                final_response_validated=False,
            )
        )

        result = session.handle(_request())

        self.assertIsInstance(result, AgentBackendResult)
        self.assertEqual(0, result.exit_code)

    def test_invalid_request_returns_bounded_error_without_invoking_handler(self):
        calls = []
        session = HostAdapterSession(lambda request, sink: calls.append(request))

        payload = json.loads(session.handle_json_line('{"schema_version":"wrong"}'))

        self.assertEqual("his-agent-backend-result.v1", payload["schema_version"])
        self.assertEqual(2, payload["exit_code"])
        self.assertEqual("worker_request_invalid", payload["error_code"])
        self.assertEqual([], calls)

    def test_handler_failure_is_reduced_to_safe_error(self):
        def handler(request, sink):
            raise RuntimeError("provider secret must never escape")

        payload = json.loads(HostAdapterSession(handler).handle_json_line(json.dumps(_request().to_dict())))

        self.assertEqual(1, payload["exit_code"])
        self.assertEqual("worker_backend_rejected", payload["error_code"])
        self.assertNotIn("provider secret", json.dumps(payload))

    def test_non_generic_handler_result_is_rejected(self):
        payload = json.loads(
            HostAdapterSession(lambda request, sink: {"model": "opaque"}).handle_json_line(
                json.dumps(_request().to_dict())
            )
        )

        self.assertEqual("worker_backend_rejected", payload["error_code"])

    def test_runner_and_reviewer_receive_the_host_handler(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = LocalAgentRunRepository(root / "harness.sqlite")
            handler = lambda request, sink: None
            worker = object()
            reviewer_worker = object()

            with patch("app.local_agent_runner.build_agent_backend", return_value=worker) as build_worker:
                with patch("app.local_agent_review.build_agent_backend", return_value=reviewer_worker) as build_reviewer:
                    LocalAgentRunner(
                        repository=repository,
                        worktree_root=root,
                        host_handler=handler,
                    )

            build_worker.assert_called_once_with("host-bridge", host_handler=handler)
            build_reviewer.assert_called_once_with("host-bridge", host_handler=handler)


if __name__ == "__main__":
    unittest.main()
