"""Provider-neutral contracts for Harness agent execution backends.

The Harness Core owns governance and evidence.  This module deliberately has
no Codex, model-provider, network, credential, or database dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
_DISPLAY_NAME = re.compile(r"^\S(?:.{0,127}\S)?$", re.DOTALL)
_TRANSPORT = re.compile(r"^[a-z][a-z0-9._-]{1,31}$")


class AgentBackendRole(StrEnum):
    WORKER = "worker"
    REVIEWER = "reviewer"


@dataclass(frozen=True)
class AgentBackendDescriptor:
    """Safe, non-secret description advertised during host negotiation."""

    backend_id: str
    display_name: str
    transport: str
    supported_roles: tuple[AgentBackendRole, ...]
    requires_local_executable: bool
    external_calls: bool
    enabled: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.backend_id, str)
            or _IDENTIFIER.fullmatch(self.backend_id) is None
            or not isinstance(self.display_name, str)
            or _DISPLAY_NAME.fullmatch(self.display_name) is None
            or not isinstance(self.transport, str)
            or _TRANSPORT.fullmatch(self.transport) is None
            or not isinstance(self.supported_roles, tuple)
            or not self.supported_roles
            or any(not isinstance(role, AgentBackendRole) for role in self.supported_roles)
            or len(set(self.supported_roles)) != len(self.supported_roles)
            or not isinstance(self.requires_local_executable, bool)
            or not isinstance(self.external_calls, bool)
            or not isinstance(self.enabled, bool)
        ):
            raise ValueError("agent_backend_invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "his-agent-backend-descriptor.v1",
            "backend_id": self.backend_id,
            "display_name": self.display_name,
            "transport": self.transport,
            "supported_roles": [role.value for role in self.supported_roles],
            "requires_local_executable": self.requires_local_executable,
            "external_calls": self.external_calls,
            "enabled": self.enabled,
        }


class AgentBackend(Protocol):
    """Minimal execution seam consumed by the governed local-agent runner."""

    def start(self, request: Any, sink: Any) -> Any: ...


class AgentBackendRegistry:
    """Resolve only explicitly declared and enabled backend descriptors."""

    def __init__(self, descriptors: tuple[AgentBackendDescriptor, ...]) -> None:
        if not isinstance(descriptors, tuple) or any(
            not isinstance(item, AgentBackendDescriptor) for item in descriptors
        ):
            raise ValueError("agent_backend_invalid")
        ids = [item.backend_id for item in descriptors]
        if len(ids) != len(set(ids)):
            raise ValueError("agent_backend_duplicate")
        self._descriptors = descriptors

    @property
    def descriptors(self) -> tuple[AgentBackendDescriptor, ...]:
        return self._descriptors

    def resolve(self, backend_id: str) -> AgentBackendDescriptor:
        if not isinstance(backend_id, str) or _IDENTIFIER.fullmatch(backend_id) is None:
            raise ValueError("agent_backend_unknown")
        descriptor = next(
            (item for item in self._descriptors if item.backend_id == backend_id), None
        )
        if descriptor is None:
            raise ValueError("agent_backend_unknown")
        if not descriptor.enabled:
            raise ValueError("agent_backend_unavailable")
        return descriptor
