from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app.mcp_stdio_transport import (
    StdioMcpConfigurationError,
    StdioMcpTransport,
    StdioMcpTransportCancelled,
    StdioMcpTransportProtocolError,
    StdioMcpTransportTimeout,
    load_stdio_server_configs,
)
from app.mcp_transport import McpTransportUnavailable
from app.plugin_inventory import VerifiedPlugin


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "mcp_stdio_fixture_server.py"


class StdioMcpConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "fixture-plugin"
        (self.root / "scripts").mkdir(parents=True)
        self.script = self.root / "scripts" / "fixture_mcp.py"
        self.script.write_text("print('fixture')\n", encoding="utf-8")

    def verified_plugin(
        self,
        *,
        command: str = "python3",
        args: list[str] | None = None,
        cwd: str = ".",
        env_vars: list[str] | None = None,
        extra_root: dict[str, object] | None = None,
        extra_server: dict[str, object] | None = None,
        include_script: bool = True,
    ) -> VerifiedPlugin:
        server = {
            "command": command,
            "args": ["./scripts/fixture_mcp.py"] if args is None else args,
            "cwd": cwd,
            "env_vars": ["MCP_FIXTURE_MODE"] if env_vars is None else env_vars,
            **(extra_server or {}),
        }
        payload = {
            "mcpServers": {"fixture": server},
            **(extra_root or {}),
        }
        manifest = json.dumps(payload, sort_keys=True).encode("utf-8")
        sources = [(".mcp.json", manifest)]
        if include_script:
            sources.append(("scripts/fixture_mcp.py", self.script.read_bytes()))
        return VerifiedPlugin(root=self.root.resolve(), sources=tuple(sources))

    def test_loads_one_hash_pinned_python_server(self) -> None:
        configs = load_stdio_server_configs({"fixture-plugin": self.verified_plugin()})

        self.assertEqual(["fixture"], list(configs))
        config = configs["fixture"]
        self.assertEqual("fixture", config.server)
        self.assertEqual(self.root.resolve(), config.root)
        self.assertEqual(("scripts/fixture_mcp.py",), config.args)
        self.assertEqual(("MCP_FIXTURE_MODE",), config.env_vars)
        self.assertEqual(
            hashlib.sha256(self.script.read_bytes()).hexdigest(),
            config.source_sha256,
        )

    def test_rejects_unknown_manifest_or_server_fields(self) -> None:
        fixtures = (
            self.verified_plugin(extra_root={"unexpected": True}),
            self.verified_plugin(extra_server={"unexpected": True}),
        )

        for fixture in fixtures:
            with self.subTest(), self.assertRaises(StdioMcpConfigurationError):
                load_stdio_server_configs({"fixture-plugin": fixture})

    def test_rejects_shell_absolute_escape_and_unfrozen_entrypoints(self) -> None:
        fixtures = (
            self.verified_plugin(command="sh"),
            self.verified_plugin(args=["/tmp/server.py"]),
            self.verified_plugin(args=["../server.py"]),
            self.verified_plugin(args=["./scripts/server.sh"]),
            self.verified_plugin(include_script=False),
            self.verified_plugin(cwd="scripts"),
        )

        for fixture in fixtures:
            with self.subTest(), self.assertRaises(StdioMcpConfigurationError):
                load_stdio_server_configs({"fixture-plugin": fixture})

    def test_rejects_dangerous_or_duplicate_environment_names(self) -> None:
        fixtures = (
            self.verified_plugin(env_vars=["PATH"]),
            self.verified_plugin(env_vars=["PYTHONPATH"]),
            self.verified_plugin(env_vars=["LD_PRELOAD"]),
            self.verified_plugin(env_vars=["MCP_FIXTURE_MODE", "MCP_FIXTURE_MODE"]),
            self.verified_plugin(env_vars=["bad-name"]),
        )

        for fixture in fixtures:
            with self.subTest(), self.assertRaises(StdioMcpConfigurationError):
                load_stdio_server_configs({"fixture-plugin": fixture})

    def test_rejects_duplicate_server_across_plugins(self) -> None:
        fixture = self.verified_plugin()

        with self.assertRaises(StdioMcpConfigurationError):
            load_stdio_server_configs({"one": fixture, "two": fixture})

    def test_rejects_symlinked_entrypoint(self) -> None:
        target = self.root / "scripts" / "target.py"
        target.write_text("print('target')\n", encoding="utf-8")
        self.script.unlink()
        self.script.symlink_to(target)
        fixture = self.verified_plugin()

        with self.assertRaises(StdioMcpConfigurationError):
            load_stdio_server_configs({"fixture-plugin": fixture})


class StdioMcpTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "fixture-plugin"
        (self.root / "scripts").mkdir(parents=True)
        self.script = self.root / "scripts" / "fixture_mcp.py"
        self.script.write_bytes(FIXTURE.read_bytes())
        manifest = json.dumps(
            {
                "mcpServers": {
                    "fixture": {
                        "command": "python3",
                        "args": ["./scripts/fixture_mcp.py"],
                        "cwd": ".",
                        "env_vars": ["MCP_FIXTURE_MODE", "MCP_FIXTURE_PID_FILE"],
                    }
                }
            },
            sort_keys=True,
        ).encode("utf-8")
        verified = VerifiedPlugin(
            root=self.root.resolve(),
            sources=(
                (".mcp.json", manifest),
                ("scripts/fixture_mcp.py", self.script.read_bytes()),
            ),
        )
        self.configs = load_stdio_server_configs({"fixture-plugin": verified})

    def transport(
        self,
        mode: str = "healthy",
        **kwargs: object,
    ) -> StdioMcpTransport:
        environment = {
            "MCP_FIXTURE_MODE": mode,
            "MCP_FIXTURE_PID_FILE": str(Path(self.temporary.name) / "child.pid"),
            "SENTINEL_PARENT_ONLY": "must-not-be-passed",
        }
        return StdioMcpTransport(
            servers=self.configs,
            environment=environment,
            **kwargs,
        )

    def call(self, transport: StdioMcpTransport | None = None, *, tool: str = "fixture_read"):
        selected = transport or self.transport()
        return selected.call(
            server="fixture",
            tool=tool,
            arguments={"work_item_id": "DFHIS-1"},
            timeout_seconds=2,
            trace_id="request-1",
        )

    def test_real_stdio_round_trip_returns_only_structured_content(self) -> None:
        result = self.call()

        self.assertEqual("his-mcp-result-envelope.v1", result["schema_version"])
        self.assertEqual("request-1", result["request_id"])
        self.assertEqual({"fixture": "ok"}, result["data"])
        self.assertNotIn("content", result)

    def test_launch_uses_argv_new_session_and_allowlisted_environment_once(self) -> None:
        original = __import__("subprocess").Popen
        calls: list[tuple[object, dict[str, object]]] = []

        def recording_popen(command, **kwargs):
            calls.append((command, kwargs))
            return original(command, **kwargs)

        with mock.patch("app.mcp_stdio_transport.subprocess.Popen", side_effect=recording_popen):
            self.call()

        self.assertEqual(1, len(calls))
        command, kwargs = calls[0]
        self.assertIsInstance(command, list)
        self.assertEqual(sys.executable, command[0])
        self.assertEqual(str(self.script.resolve()), command[1])
        self.assertIs(kwargs["shell"], False)
        self.assertIs(kwargs["start_new_session"], True)
        environment = kwargs["env"]
        self.assertEqual("healthy", environment["MCP_FIXTURE_MODE"])
        self.assertNotIn("SENTINEL_PARENT_ONLY", environment)
        self.assertNotIn("HOME", environment)
        self.assertNotIn("PYTHONPATH", environment)

    def test_rejects_unknown_server_tool_and_source_drift_without_retry(self) -> None:
        transport = self.transport()
        with self.assertRaises(McpTransportUnavailable):
            transport.call(
                server="missing",
                tool="fixture_read",
                arguments={},
                timeout_seconds=2,
                trace_id="request-1",
            )
        with self.assertRaises(StdioMcpTransportProtocolError):
            self.call(transport, tool="missing_tool")

        self.script.write_text("print('changed')\n", encoding="utf-8")
        with mock.patch("app.mcp_stdio_transport.subprocess.Popen") as popen:
            with self.assertRaises(McpTransportUnavailable):
                self.call(transport)
        popen.assert_not_called()

    def test_protocol_drift_and_nonzero_exit_fail_closed(self) -> None:
        for mode in (
            "malformed_json",
            "wrong_server",
            "unsafe_tool",
            "extra_response",
            "call_error",
            "nonzero",
        ):
            with self.subTest(mode=mode), self.assertRaises(McpTransportUnavailable):
                self.call(self.transport(mode))

    def test_stdout_and_stderr_are_bounded_and_never_echoed(self) -> None:
        fixtures = (
            ("oversized_stdout", {"max_stdout_bytes": 1024}),
            ("oversized_stderr", {"max_stderr_bytes": 1024}),
            ("stderr_secret", {}),
        )
        for mode, limits in fixtures:
            with self.subTest(mode=mode):
                with self.assertRaises(McpTransportUnavailable) as caught:
                    self.call(self.transport(mode, **limits))
                self.assertNotIn("SENTINEL", str(caught.exception))
                self.assertNotIn("stderr", str(caught.exception).lower())

    def test_timeout_and_cancellation_terminate_the_process_group(self) -> None:
        with self.assertRaises(StdioMcpTransportTimeout):
            self.transport("hang_child").call(
                server="fixture",
                tool="fixture_read",
                arguments={},
                timeout_seconds=1,
                trace_id="timeout-1",
            )
        self._assert_child_gone()

        started = time.monotonic()
        cancelled = lambda: time.monotonic() - started > 0.1
        with self.assertRaises(StdioMcpTransportCancelled):
            self.transport("hang_child", cancelled=cancelled).call(
                server="fixture",
                tool="fixture_read",
                arguments={},
                timeout_seconds=2,
                trace_id="cancel-1",
            )
        self._assert_child_gone()

    def test_pre_cancelled_call_never_launches(self) -> None:
        with mock.patch("app.mcp_stdio_transport.subprocess.Popen") as popen:
            with self.assertRaises(StdioMcpTransportCancelled):
                self.call(self.transport(cancelled=lambda: True))
        popen.assert_not_called()

    def test_python_launcher_target_drift_fails_closed_before_launch(self) -> None:
        launcher = Path(self.temporary.name) / "python"
        launcher.symlink_to(Path(sys.executable).resolve())
        transport = self.transport(python_executable=launcher)
        replacement = Path(self.temporary.name) / "replacement-python"
        replacement.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        replacement.chmod(0o700)
        launcher.unlink()
        launcher.symlink_to(replacement)

        with mock.patch("app.mcp_stdio_transport.subprocess.Popen") as popen:
            with self.assertRaises(McpTransportUnavailable):
                self.call(transport)
        popen.assert_not_called()

    def test_broken_cancellation_callback_is_normalized_without_launch(self) -> None:
        def broken_callback() -> bool:
            raise RuntimeError("SENTINEL_CALLBACK_SECRET")

        with mock.patch("app.mcp_stdio_transport.subprocess.Popen") as popen:
            with self.assertRaises(McpTransportUnavailable) as caught:
                self.call(self.transport(cancelled=broken_callback))
        popen.assert_not_called()
        self.assertNotIn("SENTINEL", str(caught.exception))

    def _assert_child_gone(self) -> None:
        pid_file = Path(self.temporary.name) / "child.pid"
        self.assertTrue(pid_file.is_file())
        pid = int(pid_file.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.02)
        self.fail("fixture child process survived MCP transport cleanup")


if __name__ == "__main__":
    unittest.main()
