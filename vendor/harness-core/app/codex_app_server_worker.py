"""Bounded stdio JSON-RPC adapter for the local Codex App Server.

This is deliberately an optional backend.  The Harness core does not import
or start Codex unless ``codex-app-server`` is explicitly selected.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from app.codex_cli_worker import (
    CODEX_EXECUTABLE,
    MAX_OUTPUT_BYTES,
    MAX_OUTPUT_LINE_BYTES,
    MAX_PROMPT_BYTES,
    CodexWorkerRequest,
    CodexWorkerResult,
    WorkerRole,
    _WorkerPhaseError,
    _preflight_executable,
    _prompt_bytes,
    _read_process_start_identity,
    _revalidate_executable,
)
from app.sensitive_text import contains_sensitive_text


_MAX_EVENTS = 256
_MAX_TIMEOUT_SECONDS = 3_600
_IDENTITY_PREFIX = "darwin-proc-bsdinfo-v1:"


class CodexAppServerWorker:
    """Run one isolated Harness attempt through ``codex app-server --stdio``.

    The server is launched as a fresh ephemeral thread.  It receives no model
    identifier, provider secret, host thread id, network permission, or
    writable root beyond the already isolated Harness worktree.
    """

    def __init__(
        self,
        *,
        process_factory: Callable[..., Any] = subprocess.Popen,
        executable: str | os.PathLike[str] | None = None,
        process_identity_reader: Callable[[int], str | None] = _read_process_start_identity,
        executable_preflight: Callable[[float], Any | None] = _preflight_executable,
        executable_revalidator: Callable[[Any], None] = _revalidate_executable,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._process_factory = process_factory
        self._executable = os.fspath(CODEX_EXECUTABLE if executable is None else executable)
        self._process_identity_reader = process_identity_reader
        self._executable_preflight = executable_preflight
        self._executable_revalidator = executable_revalidator
        self._clock = monotonic_clock

    def start(self, request: CodexWorkerRequest, sink: Any) -> CodexWorkerResult:
        stdout_hash = hashlib.sha256()
        stderr_hash = hashlib.sha256()
        process: Any | None = None
        pid: int | None = None
        identity: str | None = None
        event_count = 0
        final_response: dict[str, object] | None = None
        primary = ""
        try:
            _validate_request(request)
            _prompt_bytes(request)
            deadline = self._clock() + request.timeout_seconds
            executable = self._executable_preflight(deadline)
            worktree = _worktree(request.worktree_path)
            # The Desktop-bundled App Server currently emits a terminal error
            # when ``outputSchema`` is supplied.  Keep the same schema file
            # integrity validation here; the returned reviewer JSON is still
            # parsed and validated by LocalAgentReviewer before any decision.
            _review_schema(request)
            if executable is not None:
                self._executable_revalidator(executable)
            process = self._process_factory(
                [self._executable, "app-server", "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=os.fspath(worktree),
                shell=False,
                start_new_session=True,
                close_fds=True,
            )
            if executable is not None:
                self._executable_revalidator(executable)
            pid = _pid(process)
            identity = _identity(self._process_identity_reader(pid))
            sink.on_started(pid, identity)

            sequence = _RpcSession(process, stdout_hash, deadline, self._clock)
            sequence.request(1, "initialize", {
                "clientInfo": {"name": "his-harness", "version": "1"},
            })
            sequence.expect_response(1)
            sequence.notify("initialized")
            sequence.request(2, "thread/start", {
                "cwd": os.fspath(worktree),
                "ephemeral": True,
                "approvalPolicy": "never",
                "sandbox": "workspace-write",
            })
            thread = _thread_id(sequence.expect_response(2))
            sequence.request(3, "turn/start", {
                "threadId": thread,
                "input": _turn_input(request),
                "approvalPolicy": "never",
                "sandboxPolicy": {
                    "type": "workspaceWrite",
                    "networkAccess": False,
                    "writableRoots": [],
                },
            })
            turn = _turn_id(sequence.expect_response(3))
            final_response, event_count = _drain_turn(
                sequence, request.role, thread, turn, sink, event_count,
            )
        except (_AppServerFailure, _WorkerPhaseError) as failure:
            primary = failure.code
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            primary = "worker_protocol_invalid" if process is not None else "worker_request_invalid"
        except Exception:
            primary = "worker_internal_error"
        finally:
            _stop(process)
            _hash_stderr(process, stderr_hash)

        response_hash = _canonical_hash(final_response)
        error = primary
        return CodexWorkerResult(
            0 if not error else _exit_code(process),
            error,
            primary,
            "",
            pid,
            identity,
            stdout_hash.hexdigest(),
            stderr_hash.hexdigest(),
            event_count,
            final_response,
            response_hash,
            False,
            final_response is not None,
            response_hash,
            None,
        )


class _AppServerFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _RpcSession:
    def __init__(self, process: Any, stdout_hash: Any, deadline: float, clock: Callable[[], float]) -> None:
        if getattr(process, "stdin", None) is None or getattr(process, "stdout", None) is None:
            raise _AppServerFailure("worker_spawn_failed")
        self._process, self._hash, self._deadline, self._clock = process, stdout_hash, deadline, clock

    def request(self, request_id: int, method: str, params: dict[str, object]) -> None:
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})

    def notify(self, method: str) -> None:
        self._write({"jsonrpc": "2.0", "method": method})

    def expect_response(self, request_id: int) -> dict[str, object]:
        while True:
            message, _ = self.read()
            if message.get("id") != request_id:
                if "method" in message:
                    continue
                raise _AppServerFailure("worker_protocol_invalid")
            if "error" in message:
                raise _AppServerFailure("worker_protocol_failed")
            result = message.get("result")
            if not isinstance(result, dict):
                raise _AppServerFailure("worker_protocol_invalid")
            return result

    def read(self) -> tuple[dict[str, object], bytes]:
        if self._clock() >= self._deadline:
            raise _AppServerFailure("worker_timeout")
        raw = self._process.stdout.readline()
        if not isinstance(raw, bytes) or not raw or len(raw) > MAX_OUTPUT_LINE_BYTES:
            raise _AppServerFailure("worker_protocol_invalid")
        self._hash.update(raw)
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _AppServerFailure("worker_protocol_invalid") from error
        # The installed App Server schema describes JSON-RPC, but its actual
        # stdio responses omit the optional version member and return only
        # ``id``/``result``.  Accept that observed response form while still
        # rejecting an explicitly incompatible protocol version.
        if (
            not isinstance(value, dict)
            or ("jsonrpc" in value and value.get("jsonrpc") != "2.0")
        ):
            raise _AppServerFailure("worker_protocol_invalid")
        return value, raw

    def _write(self, value: dict[str, object]) -> None:
        if self._clock() >= self._deadline:
            raise _AppServerFailure("worker_timeout")
        try:
            raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
            self._process.stdin.write(raw + b"\n")
            self._process.stdin.flush()
        except (OSError, ValueError, TypeError):
            raise _AppServerFailure("worker_stdin_failed") from None


def _drain_turn(session: _RpcSession, role: WorkerRole, thread: str, turn: str, sink: Any, count: int) -> tuple[dict[str, object] | None, int]:
    response_text: str | None = None
    total_bytes = 0
    while count < _MAX_EVENTS:
        message, raw = session.read()
        method = message.get("method")
        params = message.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            raise _AppServerFailure("worker_protocol_invalid")
        if _is_global_status_notification(method, params):
            total_bytes += len(raw)
            if total_bytes > MAX_OUTPUT_BYTES:
                raise _AppServerFailure("worker_output_too_large")
            continue
        if params.get("threadId") != thread:
            raise _AppServerFailure("worker_protocol_invalid")
        if method == "item/completed":
            if params.get("turnId") != turn:
                raise _AppServerFailure("worker_protocol_invalid")
            item = params.get("item")
            if not isinstance(item, dict) or item.get("type") != "agentMessage":
                continue
            text = item.get("text")
            if role is WorkerRole.REVIEWER:
                if not isinstance(text, str) or not text or len(text.encode("utf-8")) > MAX_PROMPT_BYTES or contains_sensitive_text(text):
                    raise _AppServerFailure("worker_reviewer_response_invalid")
                response_text = text
            _emit(sink, "item.completed", raw, count + 1, "agent_message")
            count += 1
            total_bytes += len(raw)
        elif method == "turn/completed":
            terminal = params.get("turn")
            if not isinstance(terminal, dict) or terminal.get("id") != turn:
                raise _AppServerFailure("worker_protocol_invalid")
            if terminal.get("status") != "completed":
                raise _AppServerFailure("worker_protocol_failed")
            _emit(sink, "turn.completed", raw, count + 1)
            count += 1
            if role is WorkerRole.WORKER:
                return None, count
            if response_text is None:
                raise _AppServerFailure("worker_reviewer_response_invalid")
            try:
                response = json.loads(response_text)
            except json.JSONDecodeError as error:
                raise _AppServerFailure("worker_reviewer_response_invalid") from error
            if not isinstance(response, dict):
                raise _AppServerFailure("worker_reviewer_response_invalid")
            return response, count
        elif method == "error":
            raise _AppServerFailure("worker_protocol_failed")
        total_bytes += len(raw)
        if total_bytes > MAX_OUTPUT_BYTES:
            raise _AppServerFailure("worker_output_too_large")
    raise _AppServerFailure("worker_event_overflow")


def _is_global_status_notification(method: str, params: dict[str, object]) -> bool:
    """Ignore only observed threadless App Server status broadcasts."""
    if method == "remoteControl/status/changed":
        return (
            set(params) == {"environmentId", "installationId", "serverName", "status"}
            and all(isinstance(params.get(key), str) for key in params)
        )
    return method == "account/rateLimits/updated" and set(params) == {"rateLimits"}


def _emit(sink: Any, event_type: str, raw: bytes, sequence_no: int, item_type: str | None = None) -> None:
    event: dict[str, object] = {
        "type": event_type,
        "sequence_no": sequence_no,
        "raw_line_sha256": hashlib.sha256(raw).hexdigest(),
    }
    if item_type is not None:
        event["item_type"] = item_type
    try:
        sink.on_event(event)
    except Exception:
        raise _AppServerFailure("worker_event_sink_failed") from None


def _validate_request(request: object) -> None:
    if not isinstance(request, CodexWorkerRequest) or not isinstance(request.timeout_seconds, int) or isinstance(request.timeout_seconds, bool) or not 1 <= request.timeout_seconds <= _MAX_TIMEOUT_SECONDS:
        raise ValueError("worker_request_invalid")


def _worktree(path: object) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("worker_request_invalid")
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("worker_request_invalid")
    return resolved


def _review_schema(request: CodexWorkerRequest) -> dict[str, object] | None:
    if request.role is WorkerRole.WORKER:
        if request.output_schema_path is not None or request.expected_schema_sha256 is not None:
            raise ValueError("worker_request_invalid")
        return None
    if request.role is not WorkerRole.REVIEWER or not isinstance(request.output_schema_path, Path) or not isinstance(request.expected_schema_sha256, str):
        raise ValueError("worker_request_invalid")
    raw = request.output_schema_path.read_bytes()
    if len(raw) > MAX_PROMPT_BYTES or hashlib.sha256(raw).hexdigest() != request.expected_schema_sha256:
        raise ValueError("worker_request_invalid")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("worker_request_invalid")
    return parsed


def _turn_input(request: CodexWorkerRequest) -> list[dict[str, object]]:
    return [{"type": "text", "text": request.prompt}]


def _thread_id(result: dict[str, object]) -> str:
    thread = result.get("thread")
    value = thread.get("id") if isinstance(thread, dict) else None
    if not isinstance(value, str) or not value:
        raise _AppServerFailure("worker_protocol_invalid")
    return value


def _turn_id(result: dict[str, object]) -> str:
    turn = result.get("turn")
    value = turn.get("id") if isinstance(turn, dict) else None
    if not isinstance(value, str) or not value:
        raise _AppServerFailure("worker_protocol_invalid")
    return value


def _pid(process: Any) -> int:
    pid = getattr(process, "pid", None)
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise _AppServerFailure("worker_process_invalid")
    return pid


def _identity(value: object) -> str:
    if not isinstance(value, str) or not value.startswith(_IDENTITY_PREFIX):
        raise _AppServerFailure("worker_identity_unavailable")
    return value


def _hash_stderr(process: Any | None, digest: Any) -> None:
    stream = getattr(process, "stderr", None)
    if stream is None:
        return
    try:
        value = stream.read()
    except (OSError, ValueError):
        return
    if isinstance(value, bytes):
        digest.update(value[:MAX_OUTPUT_BYTES])


def _stop(process: Any | None) -> None:
    if process is None:
        return
    try:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=1)
    except (OSError, ValueError, subprocess.SubprocessError):
        return


def _exit_code(process: Any | None) -> int:
    if process is None:
        return 1
    try:
        code = process.poll()
    except (OSError, ValueError):
        return 1
    return code if isinstance(code, int) else 1


def _canonical_hash(value: dict[str, object] | None) -> str:
    if value is None:
        return ""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
