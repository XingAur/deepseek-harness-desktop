"""Small stdio transport for the provider-neutral Harness Host Bridge.

The task/session dispatcher is layered above this transport.  Keeping the
stream code separate lets the desktop app use the same bounded framing in
tests, development, and the packaged sidecar.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Callable, TextIO
from pathlib import Path

from app.agent_backend_protocol import AgentBackendRequest, AgentBackendResult, request_hash
from app.external_task_session import ExternalTaskSession
from app.host_bridge_session import HostBridgeSession
from app.requirement_archive import prepare_yunxiao_harness_package


HOST_SESSION_SCHEMA_VERSION = "harness-host-session.v1"
_MAX_FRAME_BYTES = 256 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def run_host_bridge_once(
    request: AgentBackendRequest,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    request_id: Callable[[AgentBackendRequest], str] = request_hash,
) -> AgentBackendResult:
    """Run one bounded agent request over stdin/stdout JSONL."""

    def send(message: dict[str, object]) -> None:
        serialized = json.dumps(
            message,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(serialized.encode("utf-8")) > 256 * 1024:
            raise ValueError("host_session_frame_invalid")
        output_stream.write(serialized + "\n")
        output_stream.flush()

    def receive() -> str:
        line = input_stream.readline()
        if not isinstance(line, str) or not line:
            raise ValueError("host_session_eof")
        return line.rstrip("\r\n")

    return HostBridgeSession(
        send=send,
        receive=receive,
        request_id=request_id,
    ).execute(request)


def run_external_task_once(
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    runner_factory: Callable[..., object] | None = None,
    task_loader: Callable[[Path], object] | None = None,
    preflight_factory: Callable[..., object] | None = None,
) -> dict[str, object]:
    """Run one desktop task session and keep Agent execution bidirectional."""

    try:
        start_message = _read_message(input_stream)
        if start_message["type"] != "task.start":
            raise ValueError("external_task_request_invalid")
        request_id = str(start_message["request_id"])
        payload = start_message["payload"]
        if "intake_source" in payload:
            result = _run_yunxiao_intake(
                payload,
                input_stream=input_stream,
                output_stream=output_stream,
            )
            _write_message(output_stream, {
                "schema_version": HOST_SESSION_SCHEMA_VERSION,
                "type": "task.result",
                "request_id": request_id,
                "payload": result,
            })
            return result
        session = ExternalTaskSession(
            runner_factory=runner_factory or _default_runner_factory,
            **({} if task_loader is None else {"task_loader": task_loader}),
            **({} if preflight_factory is None else {"preflight_factory": preflight_factory}),
        )

        def host_handler(request: AgentBackendRequest, sink: object) -> AgentBackendResult:
            return HostBridgeSession(
                send=lambda message: _write_message(output_stream, message),
                receive=lambda: _read_message(input_stream),
            ).execute(request, sink)

        result = session.execute(start_message["payload"], host_handler=host_handler)
    except ValueError as error:
        result = {"schema_version": "harness-external-task.v1", "status": "blocked", "error_code": str(error)}
        request_id = locals().get("request_id", "harness-task-invalid")
    except Exception:
        result = {"schema_version": "harness-external-task.v1", "status": "failed", "error_code": "external_task_execution_failed"}
        request_id = locals().get("request_id", "harness-task-failed")

    _write_message(output_stream, {
        "schema_version": HOST_SESSION_SCHEMA_VERSION,
        "type": "task.result",
        "request_id": request_id,
        "payload": result,
    })
    return result


def _run_yunxiao_intake(
    payload: dict[str, object],
    *,
    input_stream: TextIO,
    output_stream: TextIO,
) -> dict[str, object]:
    """Archive source evidence, then draft analysis docs with the host model.

    The read-only archive always runs first.  When the desktop task selected a
    model or backend, the requirement-side analysis documents are drafted by
    that same model through the host bridge; any generation failure keeps the
    pending placeholders and is reported as a recoverable fact, never as a
    user question.
    """

    source = payload.get("intake_source")
    archive_root = payload.get("archive_root")
    include_comments = payload.get("intake_include_comments", True)
    if not isinstance(source, str) or not source or not isinstance(archive_root, str) or not os.path.isabs(archive_root):
        raise ValueError("external_task_intake_invalid")
    if not isinstance(include_comments, bool):
        raise ValueError("external_task_intake_invalid")
    package = prepare_yunxiao_harness_package(
        archive_root=archive_root,
        yunxiao_url=source,
        include_comments=include_comments,
    )
    from app.database_probe import probe_readonly_database

    database_probe = probe_readonly_database(package_dir=str(package["package_dir"]))
    generation = _draft_documents_with_host_model(
        payload,
        package=package,
        input_stream=input_stream,
        output_stream=output_stream,
    )
    snapshot: dict[str, object] = {
        "ticket_id": package["ticket_id"],
        "package_dir": package["package_dir"],
        "package_status": package["package_status"],
        "pending_count": package["pending_count"],
    }
    if database_probe is not None:
        snapshot["database_probe_status"] = database_probe["status"]
        if database_probe["status"] != "connected":
            snapshot["database_probe_error"] = database_probe["error"]
    if generation is not None:
        snapshot["generation_status"] = generation["status"]
        snapshot["generation_error_code"] = generation["error_code"]
        snapshot["generated_count"] = generation["generated_count"]
        snapshot["skipped_count"] = generation["skipped_count"]
        snapshot["open_questions"] = generation["open_questions"]
        snapshot["model_generated_count"] = generation["model_generated_count"]
    return {
        "schema_version": "harness-external-task.v1",
        "status": "completed",
        "error_code": "",
        "snapshot": snapshot,
    }


def _draft_documents_with_host_model(
    payload: dict[str, object],
    *,
    package: dict[str, object],
    input_stream: TextIO,
    output_stream: TextIO,
) -> dict[str, object] | None:
    """Draft intake documents through the desktop host's selected model."""

    from app.host_bridge_session import HostBridgeSession
    from app.requirement_intake_model import draft_intake_analysis_documents

    selected_model_id = payload.get("selected_model_id")
    agent_backend = payload.get("agent_backend")
    has_model = isinstance(selected_model_id, str) and bool(selected_model_id)
    has_backend = isinstance(agent_backend, str) and bool(agent_backend)
    if not has_model and not has_backend:
        return None
    session = HostBridgeSession(
        send=lambda message: _write_message(output_stream, message),
        receive=lambda: _read_message(input_stream),
    )
    try:
        return draft_intake_analysis_documents(
            package_dir=str(package["package_dir"]),
            ticket_dir=str(package["ticket_dir"]),
            ticket_id=str(package["ticket_id"]),
            host_execute=session.execute,
            selected_model_id=selected_model_id if has_model else None,
        )
    except Exception:
        return {
            "status": "failed",
            "error_code": "intake_generation_failed",
            "generated_count": 0,
            "skipped_count": 0,
            "open_questions": [],
            "model_generated_count": 0,
        }


def _default_runner_factory(start: object, *, host_handler: Callable[..., object] | None = None) -> object:
    from app import database
    from app.local_agent_repository import LocalAgentRunRepository
    from app.local_agent_runner import LocalAgentRunner

    database_raw = os.environ.get("HARNESS_DB_PATH", "")
    if not database_raw.startswith("/"):
        raise ValueError("external_task_database_unavailable")
    database_path = Path(database_raw).expanduser().resolve()
    database.DB_PATH = database_path
    database.init_db()
    os.environ["HIS_KNOWLEDGE_HOME"] = str(start.knowledge_home)  # type: ignore[union-attr]
    repository = LocalAgentRunRepository(database_path)
    return LocalAgentRunner(
        repository=repository,
        worktree_root=start.worktree_root,  # type: ignore[union-attr]
        backend_id=start.agent_backend or "host-bridge",  # type: ignore[union-attr]
        host_handler=host_handler,
    )


def _read_message(input_stream: TextIO) -> dict[str, object]:
    line = input_stream.readline()
    if not isinstance(line, str) or not line or len(line.encode("utf-8")) > _MAX_FRAME_BYTES:
        raise ValueError("host_session_eof")
    try:
        value = json.loads(line)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise ValueError("host_session_response_invalid") from None
    if not isinstance(value, dict) or set(value) != {"schema_version", "type", "request_id", "payload"}:
        raise ValueError("host_session_response_invalid")
    if value.get("schema_version") != HOST_SESSION_SCHEMA_VERSION or not isinstance(value.get("type"), str):
        raise ValueError("host_session_response_invalid")
    request_id = value.get("request_id")
    payload = value.get("payload")
    if not isinstance(request_id, str) or _IDENTIFIER.fullmatch(request_id) is None or not isinstance(payload, dict):
        raise ValueError("host_session_response_invalid")
    if _contains_sensitive(payload):
        raise ValueError("host_session_response_invalid")
    return value


def _write_message(output_stream: TextIO, message: dict[str, object]) -> None:
    serialized = json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(serialized.encode("utf-8")) > _MAX_FRAME_BYTES:
        raise ValueError("host_session_frame_invalid")
    output_stream.write(serialized + "\n")
    output_stream.flush()


def _contains_sensitive(value: object, key: str = "") -> bool:
    if re.search(r"^(?:api[_-]?key|authorization|password|secret|token|provider[_-]?payload|raw[_-]?payload)$", key, re.IGNORECASE):
        return True
    if isinstance(value, str):
        return bool(re.search(r"\b(?:basic|bearer)\s+\S+", value, re.IGNORECASE))
    if isinstance(value, dict):
        return any(_contains_sensitive(item, str(name)) for name, item in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    return False


def main() -> int:
    """Serve exactly one desktop task on stdin/stdout.

    The sidecar is intentionally a module entrypoint instead of a shell
    script.  A host supplies the absolute Harness checkout as cwd and starts
    this function with ``python -u -m tools.harness_host_server``.  All
    diagnostics remain bounded and non-secret; stdout is reserved for JSONL.
    """

    run_external_task_once(input_stream=sys.stdin, output_stream=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
