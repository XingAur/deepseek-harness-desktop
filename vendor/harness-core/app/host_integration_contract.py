"""Provider-neutral host descriptors and capability negotiation.

Hosts are transports/clients, not authorization principals.  The Harness
policy remains the source of truth for mutation level even when a host advertises
more execution capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.agent_backend import AgentBackendRole


HOST_DESCRIPTOR_SCHEMA_VERSION = "his-agent-host-descriptor.v1"
HOST_NEGOTIATION_SCHEMA_VERSION = "his-agent-host-negotiation.v1"
_IDENTIFIER_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._-")
_MUTATION_LEVELS = {f"L{index}": index for index in range(6)}
_MAX_CAPABILITIES = 128
_OPAQUE_CAPABILITIES = frozenset({
    "thread_id", "turn_id", "item_id", "provider_payload", "raw_payload",
    "model", "provider", "api_key", "token", "secret",
})
_COMMON_CAPABILITIES = (
    "harness.artifacts.read",
    "harness.human-gate",
    "source.read",
    "source.search",
    "git.diff",
    "verification.run-local",
    "code.review-local",
    "git.apply-local",
)


def _valid_identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and 2 <= len(value) <= 128
        and value[0].isalpha()
        and set(value) <= _IDENTIFIER_CHARS
    )


def _level(value: object, *, error_code: str) -> int:
    if not isinstance(value, str) or value not in _MUTATION_LEVELS:
        raise ValueError(error_code)
    return _MUTATION_LEVELS[value]


def _safe_capability(value: object) -> bool:
    return _valid_identifier(value) and value not in _OPAQUE_CAPABILITIES


@dataclass(frozen=True)
class HostDescriptor:
    host_id: str
    display_name: str
    transport: str
    backend_id: str
    supported_roles: tuple[AgentBackendRole, ...]
    capabilities: tuple[str, ...]
    max_mutation_level: str

    def __post_init__(self) -> None:
        if (
            not _valid_identifier(self.host_id)
            or not isinstance(self.display_name, str)
            or not self.display_name.strip()
            or not _valid_identifier(self.transport)
            or not _valid_identifier(self.backend_id)
            or not isinstance(self.supported_roles, tuple)
            or not self.supported_roles
            or any(not isinstance(role, AgentBackendRole) for role in self.supported_roles)
            or len(set(self.supported_roles)) != len(self.supported_roles)
            or not isinstance(self.capabilities, tuple)
            or not self.capabilities
            or len(self.capabilities) > _MAX_CAPABILITIES
            or any(not _safe_capability(item) for item in self.capabilities)
            or len(set(self.capabilities)) != len(self.capabilities)
        ):
            raise ValueError("host_descriptor_invalid")
        _level(self.max_mutation_level, error_code="host_descriptor_invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": HOST_DESCRIPTOR_SCHEMA_VERSION,
            "host_id": self.host_id,
            "display_name": self.display_name,
            "transport": self.transport,
            "backend_id": self.backend_id,
            "supported_roles": [role.value for role in self.supported_roles],
            "capabilities": list(self.capabilities),
            "max_mutation_level": self.max_mutation_level,
        }


@dataclass(frozen=True)
class HostNegotiationRequest:
    host_id: str
    role: AgentBackendRole
    required_capabilities: tuple[str, ...]
    requested_mutation_level: str

    def __post_init__(self) -> None:
        if (
            not _valid_identifier(self.host_id)
            or not isinstance(self.role, AgentBackendRole)
            or not isinstance(self.required_capabilities, tuple)
            or len(self.required_capabilities) > _MAX_CAPABILITIES
            or any(not _safe_capability(item) for item in self.required_capabilities)
            or len(set(self.required_capabilities)) != len(self.required_capabilities)
        ):
            raise ValueError("host_negotiation_invalid")
        _level(self.requested_mutation_level, error_code="host_negotiation_invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": HOST_NEGOTIATION_SCHEMA_VERSION,
            "host_id": self.host_id,
            "role": self.role.value,
            "required_capabilities": list(self.required_capabilities),
            "requested_mutation_level": self.requested_mutation_level,
        }


DEFAULT_HOST_DESCRIPTORS = (
    HostDescriptor(
        host_id="terminal",
        display_name="Terminal",
        transport="process",
        backend_id="host-bridge",
        supported_roles=(AgentBackendRole.WORKER, AgentBackendRole.REVIEWER),
        capabilities=_COMMON_CAPABILITIES,
        max_mutation_level="L3",
    ),
    HostDescriptor(
        host_id="codex-app",
        display_name="Codex App",
        transport="stdio-jsonrpc",
        backend_id="codex-app-server",
        supported_roles=(AgentBackendRole.WORKER, AgentBackendRole.REVIEWER),
        capabilities=_COMMON_CAPABILITIES,
        max_mutation_level="L2",
    ),
    HostDescriptor(
        host_id="codex-cli",
        display_name="Codex CLI",
        transport="local-process",
        backend_id="codex-cli",
        supported_roles=(AgentBackendRole.WORKER, AgentBackendRole.REVIEWER),
        capabilities=_COMMON_CAPABILITIES,
        max_mutation_level="L2",
    ),
    HostDescriptor(
        host_id="deepseek-harness-desktop",
        display_name="DeepSeek-Harness-Desktop",
        transport="stdio-jsonl",
        backend_id="host-bridge",
        supported_roles=(AgentBackendRole.WORKER, AgentBackendRole.REVIEWER),
        capabilities=_COMMON_CAPABILITIES,
        max_mutation_level="L2",
    ),
)


def parse_host_negotiation_request(value: object) -> HostNegotiationRequest:
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "host_id", "role", "required_capabilities", "requested_mutation_level",
    } or value.get("schema_version") != HOST_NEGOTIATION_SCHEMA_VERSION:
        raise ValueError("host_negotiation_invalid")
    try:
        role = AgentBackendRole(value["role"])
        capabilities = value["required_capabilities"]
        if not isinstance(capabilities, list) or any(not isinstance(item, str) for item in capabilities):
            raise ValueError
        return HostNegotiationRequest(
            host_id=value["host_id"],
            role=role,
            required_capabilities=tuple(capabilities),
            requested_mutation_level=value["requested_mutation_level"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("host_negotiation_invalid") from exc


def negotiate_host(
    request: HostNegotiationRequest,
    *,
    authorized_mutation_level: str,
    descriptors: Iterable[HostDescriptor] = DEFAULT_HOST_DESCRIPTORS,
) -> dict[str, object]:
    if not isinstance(request, HostNegotiationRequest):
        raise ValueError("host_negotiation_invalid")
    authorized_level = _level(authorized_mutation_level, error_code="host_negotiation_invalid")
    descriptor_list = tuple(descriptors)
    descriptor = next((item for item in descriptor_list if item.host_id == request.host_id), None)
    if descriptor is None:
        raise ValueError("host_unknown")
    if request.role not in descriptor.supported_roles:
        raise ValueError("host_role_unsupported")
    missing = sorted(set(request.required_capabilities) - set(descriptor.capabilities))
    if missing:
        raise ValueError("host_capability_unsupported")
    requested_level = _level(request.requested_mutation_level, error_code="host_negotiation_invalid")
    if requested_level > _level(descriptor.max_mutation_level, error_code="host_descriptor_invalid"):
        raise ValueError("host_mutation_unsupported")
    # Host identity never upgrades Harness authorization.  The same policy
    # check applies to terminal, Codex App, Codex CLI, and DeepSeek Desktop.
    if requested_level > authorized_level:
        raise ValueError("host_mutation_not_authorized")
    return {
        "schema_version": HOST_NEGOTIATION_SCHEMA_VERSION,
        "negotiated": True,
        "host_id": descriptor.host_id,
        "backend_id": descriptor.backend_id,
        "role": request.role.value,
        "capabilities": list(request.required_capabilities),
        "requested_mutation_level": request.requested_mutation_level,
        "authorized_mutation_level": authorized_mutation_level,
        "authorization_source": "harness_policy",
    }


def build_host_integration_status() -> dict[str, object]:
    return {
        "schema_version": "his-agent-host-status.v1",
        "hosts": [item.to_dict() for item in DEFAULT_HOST_DESCRIPTORS],
    }
