from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable

from app.mcp_transport import McpTransportUnavailable
from app.plugin_inventory import VerifiedPlugin


_ROOT_FIELDS = frozenset({"mcpServers"})
_SERVER_FIELDS = frozenset({"command", "args", "cwd", "env_vars"})
_SERVER_NAME = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_DANGEROUS_ENVIRONMENT = frozenset(
    {
        "BASH_ENV",
        "ENV",
        "HOME",
        "IFS",
        "LD_PRELOAD",
        "PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "SHELL",
        "ZDOTDIR",
    }
)
_DANGEROUS_ENVIRONMENT_PREFIXES = ("DYLD_", "LD_")
_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,127}$")
_TRACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_PROTOCOL_VERSION = "2025-06-18"
_MAX_REQUEST_BYTES = 262_144


class StdioMcpConfigurationError(ValueError):
    """A frozen plugin MCP launch declaration is malformed or unsafe."""


class StdioMcpTransportProtocolError(McpTransportUnavailable):
    """The stdio peer violated the bounded read-only MCP contract."""


class StdioMcpTransportTimeout(McpTransportUnavailable):
    """The stdio peer did not complete within its declared deadline."""


class StdioMcpTransportCancelled(McpTransportUnavailable):
    """The governed request was cancelled before completion."""


@dataclass(frozen=True)
class StdioMcpServerConfig:
    server: str
    root: Path
    args: tuple[str, ...]
    env_vars: tuple[str, ...]
    source_sha256: str


class StdioMcpTransport:
    """One-process-per-call, hash-pinned MCP JSONL transport.

    The transport deliberately owns only process and protocol mechanics. Result
    envelope validation, evidence persistence, authorization and audit remain in
    the MCP gateway so there is one governed execution path.
    """

    def __init__(
        self,
        *,
        servers: Mapping[str, StdioMcpServerConfig],
        environment: Mapping[str, str] | None = None,
        python_executable: str | Path | None = None,
        cancelled: Callable[[], bool] | None = None,
        max_stdout_bytes: int = 1_048_576,
        max_stderr_bytes: int = 65_536,
    ) -> None:
        if not isinstance(servers, Mapping):
            raise TypeError("servers must be a mapping")
        copied_servers: dict[str, StdioMcpServerConfig] = {}
        for name, config in servers.items():
            if (
                not isinstance(name, str)
                or not isinstance(config, StdioMcpServerConfig)
                or name != config.server
            ):
                raise ValueError("invalid MCP server configuration")
            copied_servers[name] = config
        source_environment = os.environ if environment is None else environment
        if not isinstance(source_environment, Mapping):
            raise TypeError("environment must be a mapping")
        copied_environment: dict[str, str] = {}
        for name, value in source_environment.items():
            if isinstance(name, str) and isinstance(value, str):
                copied_environment[name] = value
        executable = Path(python_executable or sys.executable).absolute()
        try:
            resolved_executable = executable.resolve(strict=True)
            executable_stat = resolved_executable.stat()
        except OSError as exc:
            raise ValueError("Python executable is unavailable") from exc
        if not resolved_executable.is_file() or not os.access(resolved_executable, os.X_OK):
            raise ValueError("Python executable is unavailable")
        if cancelled is not None and not callable(cancelled):
            raise TypeError("cancelled must be callable")
        if (
            isinstance(max_stdout_bytes, bool)
            or not isinstance(max_stdout_bytes, int)
            or max_stdout_bytes < 1
            or isinstance(max_stderr_bytes, bool)
            or not isinstance(max_stderr_bytes, int)
            or max_stderr_bytes < 1
        ):
            raise ValueError("stdio byte limits must be positive integers")
        self._servers = MappingProxyType(copied_servers)
        self._environment = MappingProxyType(copied_environment)
        # Preserve the launcher path so a project virtual environment keeps its
        # pyvenv.cfg context. The resolved target and file identity are pinned
        # separately and rechecked immediately before every process launch.
        self._python_launcher = executable
        self._python_target = resolved_executable
        self._python_target_identity = (
            executable_stat.st_dev,
            executable_stat.st_ino,
            executable_stat.st_size,
            executable_stat.st_mtime_ns,
        )
        self._cancelled = cancelled or (lambda: False)
        self._max_stdout_bytes = max_stdout_bytes
        self._max_stderr_bytes = max_stderr_bytes

    def call(
        self,
        *,
        server: str,
        tool: str,
        arguments: Mapping[str, Any],
        timeout_seconds: int,
        trace_id: str,
    ) -> Mapping[str, Any]:
        if self._cancel_requested():
            raise StdioMcpTransportCancelled("MCP call was cancelled")
        config = self._validate_call(
            server=server,
            tool=tool,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
            trace_id=trace_id,
        )
        entrypoint = self._verified_entrypoint(config)
        request_bytes = self._request_bytes(
            tool=tool,
            arguments=arguments,
            trace_id=trace_id,
        )
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                [self._verified_python_launcher(), str(entrypoint)],
                cwd=str(config.root),
                env=self._child_environment(config),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                start_new_session=True,
            )
            stdout, stderr, return_code = self._exchange(
                process,
                request_bytes,
                timeout_seconds=timeout_seconds,
            )
            if return_code != 0 or stderr:
                raise StdioMcpTransportProtocolError("MCP peer failed closed")
            return self._validated_result(stdout, server=server, tool=tool)
        except (
            StdioMcpTransportCancelled,
            StdioMcpTransportProtocolError,
            StdioMcpTransportTimeout,
        ):
            raise
        except (BrokenPipeError, OSError, UnicodeError, ValueError):
            raise McpTransportUnavailable("MCP stdio transport is unavailable") from None
        finally:
            if process is not None:
                self._terminate_process_group(process)

    def _verified_python_launcher(self) -> str:
        try:
            current_target = self._python_launcher.resolve(strict=True)
            current_stat = current_target.stat()
        except OSError:
            raise McpTransportUnavailable("Python executable is unavailable") from None
        current_identity = (
            current_stat.st_dev,
            current_stat.st_ino,
            current_stat.st_size,
            current_stat.st_mtime_ns,
        )
        if (
            current_target != self._python_target
            or current_identity != self._python_target_identity
            or not current_target.is_file()
            or not os.access(current_target, os.X_OK)
        ):
            raise McpTransportUnavailable("Python executable is unavailable")
        return str(self._python_launcher)

    def _validate_call(
        self,
        *,
        server: object,
        tool: object,
        arguments: object,
        timeout_seconds: object,
        trace_id: object,
    ) -> StdioMcpServerConfig:
        if not isinstance(server, str) or _SERVER_NAME.fullmatch(server) is None:
            raise McpTransportUnavailable("MCP server is unavailable")
        config = self._servers.get(server)
        if config is None:
            raise McpTransportUnavailable("MCP server is unavailable")
        if not isinstance(tool, str) or _TOOL_NAME.fullmatch(tool) is None:
            raise StdioMcpTransportProtocolError("MCP tool identity is invalid")
        if not isinstance(arguments, Mapping) or any(
            not isinstance(key, str) for key in arguments
        ):
            raise StdioMcpTransportProtocolError("MCP arguments are invalid")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= 60
        ):
            raise StdioMcpTransportProtocolError("MCP timeout is invalid")
        if not isinstance(trace_id, str) or _TRACE_ID.fullmatch(trace_id) is None:
            raise StdioMcpTransportProtocolError("MCP trace identity is invalid")
        return config

    @staticmethod
    def _verified_entrypoint(config: StdioMcpServerConfig) -> Path:
        try:
            entrypoint = _safe_regular_file(config.root, config.args[0])
            source = entrypoint.read_bytes()
        except (IndexError, OSError, StdioMcpConfigurationError):
            raise McpTransportUnavailable("MCP server source is unavailable") from None
        if hashlib.sha256(source).hexdigest() != config.source_sha256:
            raise McpTransportUnavailable("MCP server source is unavailable")
        return entrypoint

    @staticmethod
    def _request_bytes(
        *,
        tool: str,
        arguments: Mapping[str, Any],
        trace_id: str,
    ) -> bytes:
        messages = (
            {
                "jsonrpc": "2.0",
                "id": "harness-initialize",
                "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "his-harness", "version": "1"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": "harness-tools-list",
                "method": "tools/list",
                "params": {},
            },
            {
                "jsonrpc": "2.0",
                "id": "harness-tools-call",
                "method": "tools/call",
                "params": {
                    "name": tool,
                    "arguments": dict(arguments),
                    "_meta": {"request_id": trace_id, "trace_id": trace_id},
                },
            },
        )
        try:
            payload = b"".join(
                json.dumps(
                    message,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                + b"\n"
                for message in messages
            )
        except (TypeError, ValueError, UnicodeError):
            raise StdioMcpTransportProtocolError("MCP arguments are not JSON-safe") from None
        if len(payload) > _MAX_REQUEST_BYTES:
            raise StdioMcpTransportProtocolError("MCP request exceeds the byte limit")
        return payload

    def _child_environment(self, config: StdioMcpServerConfig) -> dict[str, str]:
        child = {
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "LC_ALL": "C",
        }
        for name in config.env_vars:
            value = self._environment.get(name)
            if value is None:
                continue
            if "\x00" in value:
                raise McpTransportUnavailable("MCP environment is unavailable")
            child[name] = value
        return child

    def _exchange(
        self,
        process: subprocess.Popen[bytes],
        request_bytes: bytes,
        *,
        timeout_seconds: int,
    ) -> tuple[bytes, bytes, int]:
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise McpTransportUnavailable("MCP process streams are unavailable")
        streams = {
            "stdin": process.stdin,
            "stdout": process.stdout,
            "error": process.stderr,
        }
        selector = selectors.DefaultSelector()
        stdout = bytearray()
        error_output = bytearray()
        sent = 0
        deadline = time.monotonic() + timeout_seconds
        try:
            for stream in streams.values():
                os.set_blocking(stream.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "error")
            while selector.get_map():
                if self._cancel_requested():
                    raise StdioMcpTransportCancelled("MCP call was cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise StdioMcpTransportTimeout("MCP call exceeded its deadline")
                for key, _ in selector.select(min(0.05, remaining)):
                    label = key.data
                    stream = key.fileobj
                    if label == "stdin":
                        try:
                            written = os.write(stream.fileno(), request_bytes[sent:])
                        except BlockingIOError:
                            continue
                        sent += written
                        if sent == len(request_bytes):
                            selector.unregister(stream)
                            stream.close()
                        continue
                    try:
                        chunk = os.read(stream.fileno(), 65_536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    target = stdout if label == "stdout" else error_output
                    target.extend(chunk)
                    limit = (
                        self._max_stdout_bytes
                        if label == "stdout"
                        else self._max_stderr_bytes
                    )
                    if len(target) > limit:
                        raise StdioMcpTransportProtocolError(
                            "MCP peer exceeded an output byte limit"
                        )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StdioMcpTransportTimeout("MCP call exceeded its deadline")
            try:
                return_code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise StdioMcpTransportTimeout("MCP call exceeded its deadline") from None
            return bytes(stdout), bytes(error_output), return_code
        finally:
            selector.close()
            for stream in streams.values():
                if not stream.closed:
                    stream.close()

    def _cancel_requested(self) -> bool:
        try:
            result = self._cancelled()
        except Exception:
            raise McpTransportUnavailable("MCP cancellation state is unavailable") from None
        if not isinstance(result, bool):
            raise McpTransportUnavailable("MCP cancellation state is unavailable")
        return result

    @staticmethod
    def _validated_result(stdout: bytes, *, server: str, tool: str) -> Mapping[str, Any]:
        try:
            text = stdout.decode("utf-8")
            lines = [line for line in text.splitlines() if line.strip()]
            responses = [json.loads(line) for line in lines]
        except (UnicodeError, json.JSONDecodeError):
            raise StdioMcpTransportProtocolError("MCP peer returned invalid JSON") from None
        if len(responses) != 3:
            raise StdioMcpTransportProtocolError("MCP peer returned an invalid response count")
        expected_ids = (
            "harness-initialize",
            "harness-tools-list",
            "harness-tools-call",
        )
        results: list[Mapping[str, Any]] = []
        for response, expected_id in zip(responses, expected_ids):
            if (
                not isinstance(response, dict)
                or set(response) != {"jsonrpc", "id", "result"}
                or response.get("jsonrpc") != "2.0"
                or response.get("id") != expected_id
                or not isinstance(response.get("result"), dict)
            ):
                raise StdioMcpTransportProtocolError("MCP peer returned an invalid response")
            results.append(response["result"])
        initialize, listed, called = results
        server_info = initialize.get("serverInfo")
        if (
            initialize.get("protocolVersion") != _PROTOCOL_VERSION
            or not isinstance(server_info, dict)
            or server_info.get("name") != server
            or not isinstance(server_info.get("version"), str)
            or not server_info.get("version")
        ):
            raise StdioMcpTransportProtocolError("MCP peer identity is invalid")
        listed_tools = listed.get("tools")
        if not isinstance(listed_tools, list):
            raise StdioMcpTransportProtocolError("MCP tool inventory is invalid")
        matching = [
            item
            for item in listed_tools
            if isinstance(item, dict) and item.get("name") == tool
        ]
        if len(matching) != 1:
            raise StdioMcpTransportProtocolError("MCP tool is unavailable")
        annotations = matching[0].get("annotations")
        if (
            not isinstance(annotations, dict)
            or annotations.get("readOnlyHint") is not True
            or annotations.get("destructiveHint") is not False
        ):
            raise StdioMcpTransportProtocolError("MCP tool is not read-only")
        structured = called.get("structuredContent")
        if called.get("isError") is not False or not isinstance(structured, dict):
            raise StdioMcpTransportProtocolError("MCP tool result is invalid")
        try:
            return json.loads(
                json.dumps(
                    structured,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            raise StdioMcpTransportProtocolError("MCP tool result is not JSON-safe") from None

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
        for termination_signal in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, termination_signal)
            except ProcessLookupError:
                pass
            except OSError:
                pass
            if termination_signal == signal.SIGTERM:
                time.sleep(0.02)
        try:
            process.wait(timeout=0.2)
        except (subprocess.TimeoutExpired, OSError):
            pass


def load_stdio_server_configs(
    verified_plugins: Mapping[str, VerifiedPlugin],
) -> Mapping[str, StdioMcpServerConfig]:
    if not isinstance(verified_plugins, Mapping):
        raise StdioMcpConfigurationError("verified plugins must be a mapping")
    configs: dict[str, StdioMcpServerConfig] = {}
    for plugin_name in sorted(verified_plugins):
        plugin = verified_plugins[plugin_name]
        if not isinstance(plugin_name, str) or not plugin_name:
            raise StdioMcpConfigurationError("invalid verified plugin name")
        if not isinstance(plugin, VerifiedPlugin):
            raise StdioMcpConfigurationError("invalid verified plugin snapshot")
        sources = dict(plugin.sources)
        manifest_bytes = sources.get(".mcp.json")
        if manifest_bytes is None:
            continue
        payload = _json_object(manifest_bytes, "MCP manifest")
        _exact_fields(payload, _ROOT_FIELDS, "MCP manifest")
        raw_servers = payload["mcpServers"]
        if not isinstance(raw_servers, dict) or not raw_servers:
            raise StdioMcpConfigurationError("mcpServers must be a non-empty object")
        for server_name in sorted(raw_servers):
            if server_name in configs:
                raise StdioMcpConfigurationError("duplicate MCP server")
            configs[server_name] = _server_config(
                server_name,
                raw_servers[server_name],
                plugin=plugin,
                frozen_sources=sources,
            )
    return MappingProxyType(configs)


def _server_config(
    server_name: object,
    payload: object,
    *,
    plugin: VerifiedPlugin,
    frozen_sources: Mapping[str, bytes],
) -> StdioMcpServerConfig:
    if not isinstance(server_name, str) or _SERVER_NAME.fullmatch(server_name) is None:
        raise StdioMcpConfigurationError("invalid MCP server name")
    if not isinstance(payload, dict):
        raise StdioMcpConfigurationError("MCP server declaration must be an object")
    _exact_fields(payload, _SERVER_FIELDS, "MCP server declaration")
    if payload["command"] != "python3" or payload["cwd"] != ".":
        raise StdioMcpConfigurationError("unsupported MCP server launch command")
    raw_args = payload["args"]
    if not isinstance(raw_args, list) or len(raw_args) != 1:
        raise StdioMcpConfigurationError("MCP server requires one Python entrypoint")
    relative_path = _safe_entrypoint(raw_args[0])
    frozen_source = frozen_sources.get(relative_path)
    if frozen_source is None:
        raise StdioMcpConfigurationError("MCP entrypoint is not frozen")
    root = _safe_root(plugin.root)
    entrypoint = _safe_regular_file(root, relative_path)
    try:
        current_source = entrypoint.read_bytes()
    except OSError as exc:
        raise StdioMcpConfigurationError("MCP entrypoint cannot be read") from exc
    if current_source != frozen_source:
        raise StdioMcpConfigurationError("MCP entrypoint source drift")
    raw_env_vars = payload["env_vars"]
    if (
        not isinstance(raw_env_vars, list)
        or len(raw_env_vars) > 32
        or len(raw_env_vars) != len(set(raw_env_vars))
    ):
        raise StdioMcpConfigurationError("invalid MCP environment allowlist")
    env_vars: list[str] = []
    for value in raw_env_vars:
        if (
            not isinstance(value, str)
            or _ENV_NAME.fullmatch(value) is None
            or value in _DANGEROUS_ENVIRONMENT
            or value.startswith(_DANGEROUS_ENVIRONMENT_PREFIXES)
        ):
            raise StdioMcpConfigurationError("unsafe MCP environment name")
        env_vars.append(value)
    return StdioMcpServerConfig(
        server=server_name,
        root=root,
        args=(relative_path,),
        env_vars=tuple(env_vars),
        source_sha256=hashlib.sha256(frozen_source).hexdigest(),
    )


def _json_object(content: bytes, label: str) -> dict[str, object]:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise StdioMcpConfigurationError(f"invalid {label}") from exc
    if not isinstance(payload, dict):
        raise StdioMcpConfigurationError(f"{label} must be an object")
    return payload


def _exact_fields(payload: Mapping[str, object], expected: frozenset[str], label: str) -> None:
    if set(payload) != expected:
        raise StdioMcpConfigurationError(f"{label} fields are not exact")


def _safe_entrypoint(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise StdioMcpConfigurationError("invalid MCP entrypoint")
    normalized = value[2:] if value.startswith("./") else value
    relative = Path(normalized)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.suffix != ".py"
        or len(relative.parts) < 2
        or any(character in normalized for character in "*?[]{}")
    ):
        raise StdioMcpConfigurationError("unsafe MCP entrypoint")
    return relative.as_posix()


def _safe_root(value: Path) -> Path:
    root = Path(value)
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise StdioMcpConfigurationError("unsafe MCP plugin root") from exc
    if not root.is_absolute() or root.is_symlink() or resolved != root or not root.is_dir():
        raise StdioMcpConfigurationError("unsafe MCP plugin root")
    return root


def _safe_regular_file(root: Path, relative_path: str) -> Path:
    current = root
    for part in Path(relative_path).parts:
        current = current / part
        if current.is_symlink():
            raise StdioMcpConfigurationError("MCP entrypoint cannot contain symlinks")
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise StdioMcpConfigurationError("unsafe MCP entrypoint") from exc
    if not resolved.is_file():
        raise StdioMcpConfigurationError("MCP entrypoint must be a regular file")
    return resolved
