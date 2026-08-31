import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agent_backend_factory import (
    HostBridgeAgentBackend,
    build_agent_backend,
    build_agent_backend_status,
    load_agent_backend_registry,
)
from app.agent_backend_protocol import AgentBackendResult
from app.codex_cli_worker import CodexWorkerRequest


class AgentBackendFactoryTests(unittest.TestCase):
    def test_loads_configured_backends_and_selects_host_bridge_by_default(self):
        registry, default_backend = load_agent_backend_registry()

        self.assertEqual("host-bridge", default_backend)
        self.assertEqual("stdio-jsonl", registry.resolve("host-bridge").transport)
        self.assertEqual("local-process", registry.resolve("codex-cli").transport)

        backend = build_agent_backend()
        self.assertIsInstance(backend, HostBridgeAgentBackend)

    def test_codex_cli_is_loaded_only_when_explicitly_selected(self):
        sentinel = object()

        with patch("app.codex_cli_worker.CodexCliWorker", return_value=sentinel) as constructor:
            backend = build_agent_backend("codex-cli")

        self.assertIs(sentinel, backend)
        constructor.assert_called_once_with()

    def test_codex_app_server_is_loaded_only_when_explicitly_selected(self):
        sentinel = object()

        with patch("app.codex_app_server_worker.CodexAppServerWorker", return_value=sentinel) as constructor:
            backend = build_agent_backend("codex-app-server")

        self.assertIs(sentinel, backend)
        constructor.assert_called_once_with()

    def test_host_bridge_fails_closed_without_a_host_handler(self):
        backend = build_agent_backend("host-bridge")
        with tempfile.TemporaryDirectory() as worktree:
            request = CodexWorkerRequest.worker(
                worktree_path=Path(worktree),
                prompt="只执行安全的验证",
                timeout_seconds=30,
            )
            result = backend.start(request, sink=None)

        self.assertEqual("worker_backend_unavailable", result.error_code)
        self.assertEqual("worker_backend_unavailable", result.primary_error_code)
        self.assertIsNone(result.pid)
        self.assertFalse(result.final_response_validated)

    def test_unknown_backend_is_rejected_before_construction(self):
        with self.assertRaisesRegex(ValueError, "agent_backend_unknown"):
            build_agent_backend("missing-backend")

    def test_host_bridge_exposes_only_provider_neutral_request_and_result(self):
        calls = []

        def handler(request, sink):
            calls.append(request.to_dict())
            return AgentBackendResult(
                exit_code=0,
                error_code="",
                event_count=1,
                final_response_sha256="",
                canonical_final_response_sha256="",
                final_response_validated=False,
                final_response={"ok": True},
            )

        class Sink:
            pid = 123
            identity = "host-process"

        backend = build_agent_backend("host-bridge", host_handler=handler)
        with tempfile.TemporaryDirectory() as worktree:
            request = CodexWorkerRequest.worker(
                worktree_path=Path(worktree), prompt="执行验证", timeout_seconds=30
            )
            result = backend.start(request, Sink())

        self.assertEqual("worker", calls[0]["role"])
        self.assertNotIn("model", calls[0])
        self.assertEqual(123, result.pid)
        self.assertEqual("host-process", result.process_start_identity)
        self.assertEqual({"ok": True}, result.final_response)

    def test_backend_status_is_safe_for_frontend_discovery(self):
        status = build_agent_backend_status()

        self.assertEqual("his-agent-backend-status.v1", status["schema_version"])
        self.assertEqual("host-bridge", status["default_backend"])
        self.assertFalse(status["environment_override"])
        self.assertEqual({"codex-cli", "codex-app-server", "host-bridge"}, {
            item["backend_id"] for item in status["backends"]
        })


if __name__ == "__main__":
    unittest.main()
