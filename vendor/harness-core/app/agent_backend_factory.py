"""Construct provider-neutral agent backends for the Harness runtime.

The factory is intentionally the only place that imports the Codex CLI
adapter.  Harness orchestration can therefore start without a local Codex
executable and can select another host/backend explicitly.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.agent_backend import AgentBackendDescriptor, AgentBackendRegistry, AgentBackendRole
from app.agent_backend_protocol import AgentBackendRequest, AgentBackendResult


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config" / "agent_backends.json"
CONFIG_SCHEMA_VERSION = "his-agent-backend-config.v1"


class HostBridgeAgentBackend:
    """Embedding point for Codex App/Desktop/another host.

    The host callback is deliberately injected by the embedding client.  The
    standalone Harness has no implicit process, network, or credential path;
    without a callback it returns a bounded failure result.
    """

    def __init__(self, descriptor: AgentBackendDescriptor, host_handler: Callable[..., Any] | None = None):
        self.descriptor = descriptor
        self._host_handler = host_handler

    def start(self, request: Any, sink: Any) -> Any:
        if self._host_handler is None:
            return _bounded_result(
                error_code="worker_backend_unavailable",
                primary_error_code="worker_backend_unavailable",
            )

        try:
            generic_request = _to_generic_request(request)
            result = self._host_handler(generic_request, sink)
        except Exception:
            return _bounded_result(
                error_code="worker_backend_rejected",
                primary_error_code="worker_backend_rejected",
            )

        if isinstance(result, AgentBackendResult):
            return _compatibility_result(result, sink)
        if _is_worker_result_shape(result):
            return result

        return _bounded_result(
            error_code="worker_backend_rejected",
            primary_error_code="worker_backend_rejected",
        )


def load_agent_backend_registry(
    config_path: str | os.PathLike[str] | None = None,
) -> tuple[AgentBackendRegistry, str]:
    path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("agent_backend_config_invalid") from exc

    if not isinstance(raw, dict) or raw.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ValueError("agent_backend_config_invalid")

    default_backend = raw.get("default_backend")
    backends = raw.get("backends")
    if not isinstance(default_backend, str) or not isinstance(backends, list):
        raise ValueError("agent_backend_config_invalid")

    descriptors = []
    for item in backends:
        if not isinstance(item, dict):
            raise ValueError("agent_backend_config_invalid")
        try:
            descriptors.append(
                AgentBackendDescriptor(
                    backend_id=item["backend_id"],
                    display_name=item["display_name"],
                    transport=item["transport"],
                    supported_roles=tuple(
                        AgentBackendRole(role) for role in item["supported_roles"]
                    ),
                    requires_local_executable=item["requires_local_executable"],
                    external_calls=item["external_calls"],
                    enabled=item["enabled"],
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("agent_backend_config_invalid") from exc

    registry = AgentBackendRegistry(tuple(descriptors))
    registry.resolve(default_backend)
    return registry, default_backend


def build_agent_backend(
    backend_id: str | None = None,
    *,
    config_path: str | os.PathLike[str] | None = None,
    host_handler: Callable[..., Any] | None = None,
) -> Any:
    registry, configured_default = load_agent_backend_registry(config_path)
    selected = backend_id or os.environ.get("HARNESS_AGENT_BACKEND") or configured_default
    descriptor = registry.resolve(selected)

    if descriptor.backend_id == "codex-cli":
        # Keep this import lazy: importing the Harness core must not require
        # Codex CLI, a signed executable, or any provider-specific runtime.
        from app.codex_cli_worker import CodexCliWorker

        return CodexCliWorker()

    if descriptor.backend_id == "codex-app-server":
        # The App Server is the official local programmatic surface exposed by
        # Codex Desktop.  Keep it lazy for the same provider-neutral reason as
        # the legacy CLI backend.
        from app.codex_app_server_worker import CodexAppServerWorker

        return CodexAppServerWorker()

    if descriptor.backend_id == "host-bridge":
        return HostBridgeAgentBackend(descriptor, host_handler=host_handler)

    raise ValueError("agent_backend_not_implemented")


def resolve_agent_backend_id(
    backend_id: str | None = None,
    *,
    config_path: str | os.PathLike[str] | None = None,
) -> str:
    registry, configured_default = load_agent_backend_registry(config_path)
    selected = backend_id or os.environ.get("HARNESS_AGENT_BACKEND") or configured_default
    return registry.resolve(selected).backend_id


def build_agent_backend_status(
    config_path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    registry, default_backend = load_agent_backend_registry(config_path)
    return {
        "schema_version": "his-agent-backend-status.v1",
        "default_backend": default_backend,
        "environment_override": bool(os.environ.get("HARNESS_AGENT_BACKEND")),
        "backends": [item.to_dict() for item in registry.descriptors],
    }


def _to_generic_request(request: Any) -> AgentBackendRequest:
    role = getattr(getattr(request, "role", None), "value", getattr(request, "role", None))
    is_reviewer = role == AgentBackendRole.REVIEWER.value
    return AgentBackendRequest(
        role=AgentBackendRole(role),
        worktree_path=Path(request.worktree_path),
        prompt=request.prompt,
        timeout_seconds=request.timeout_seconds,
        output_contract=dict(
            getattr(
                request,
                "output_contract",
                {"name": "his-local-agent-review", "schema_version": "his-local-agent-review.v1"}
                if is_reviewer
                else {"name": "none", "schema_version": "none"},
            )
        ),
        capabilities=tuple(getattr(request, "capabilities", ())),
    )


def _is_worker_result_shape(result: Any) -> bool:
    return all(
        hasattr(result, name)
        for name in (
            "exit_code", "error_code", "primary_error_code", "cleanup_error_code",
            "pid", "process_start_identity", "event_count", "final_response",
            "final_response_sha256", "final_response_validated",
            "untrusted_final_response", "canonical_final_response_sha256",
        )
    )


@dataclass(frozen=True)
class _BoundedWorkerResult:
    exit_code: int | None
    error_code: str
    primary_error_code: str
    cleanup_error_code: str | None
    pid: int | None
    process_start_identity: str | None
    stdout_sha256: str
    stderr_sha256: str
    event_count: int
    final_response: dict[str, object] | None
    final_response_sha256: str
    final_response_validated: bool
    untrusted_final_response: bool
    canonical_final_response_sha256: str
    protocol_rejection: object | None = None


def _bounded_result(*, error_code: str, primary_error_code: str) -> _BoundedWorkerResult:
    return _BoundedWorkerResult(
        exit_code=1,
        error_code=error_code,
        primary_error_code=primary_error_code,
        cleanup_error_code=None,
        pid=None,
        process_start_identity=None,
        stdout_sha256="",
        stderr_sha256="",
        event_count=0,
        final_response=None,
        final_response_sha256="",
        final_response_validated=False,
        untrusted_final_response=False,
        canonical_final_response_sha256="",
        protocol_rejection=None,
    )


def _compatibility_result(result: AgentBackendResult, sink: Any) -> _BoundedWorkerResult:
    return _BoundedWorkerResult(
        exit_code=result.exit_code,
        error_code=result.error_code,
        primary_error_code=result.error_code,
        cleanup_error_code=None,
        pid=getattr(sink, "pid", None),
        process_start_identity=getattr(sink, "identity", None),
        stdout_sha256="",
        stderr_sha256="",
        event_count=result.event_count,
        final_response=result.final_response,
        final_response_sha256=result.final_response_sha256,
        final_response_validated=result.final_response_validated,
        untrusted_final_response=result.final_response is not None and not result.final_response_validated,
        canonical_final_response_sha256=result.canonical_final_response_sha256,
        protocol_rejection=None,
    )
