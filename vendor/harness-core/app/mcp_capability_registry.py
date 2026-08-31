from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from app.capability_contracts import MutationLevel
from app.mcp_schema_validation import McpSchemaValidationError, check_supported_schema
from app.sensitive_text import contains_sensitive_text


MCP_CAPABILITY_SCHEMA_VERSION = "his-mcp-capabilities.v1"
_MANIFEST_FIELDS = frozenset({"schema_version", "capabilities"})
_DESCRIPTOR_FIELDS = frozenset(
    {
        "capability",
        "provider",
        "server",
        "tool",
        "contract_version",
        "mutation_level",
        "required_scopes",
        "timeout_seconds",
        "max_result_bytes",
        "input_schema_path",
        "input_schema_sha256",
        "result_schema_path",
        "result_schema_sha256",
        "enabled",
        "disabled_reason",
    }
)
_CAPABILITY = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_SERVER_TOOL = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_CONTRACT_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SCOPE = re.compile(r"^[a-z][a-z0-9:_-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GENERIC_TOOLS = frozenset({"request", "execute", "proxy", "raw_sql", "shell", "command"})


class McpCapabilityRegistryError(ValueError):
    """Base class for strict MCP registry failures."""


class McpCapabilityManifestError(McpCapabilityRegistryError):
    """The MCP manifest or a referenced schema is malformed or unsafe."""


class McpCapabilityNotFound(McpCapabilityRegistryError):
    """An exact capability/provider pair is not registered."""


@dataclass(frozen=True)
class McpCapabilityDescriptor:
    capability: str
    provider: str
    server: str
    tool: str
    contract_version: str
    mutation_level: MutationLevel
    required_scopes: tuple[str, ...]
    timeout_seconds: int
    max_result_bytes: int
    input_schema_path: Path
    input_schema_sha256: str
    input_schema: Mapping[str, Any]
    result_schema_path: Path
    result_schema_sha256: str
    result_schema: Mapping[str, Any]
    enabled: bool
    disabled_reason: str


class McpCapabilityRegistry:
    def __init__(self, descriptors: Sequence[McpCapabilityDescriptor]) -> None:
        ordered = tuple(sorted(descriptors, key=lambda item: (item.capability, item.provider)))
        self._descriptors = ordered
        self._by_key = {(item.capability, item.provider): item for item in ordered}

    @classmethod
    def from_file(cls, path: Path, *, harness_root: Path) -> "McpCapabilityRegistry":
        root = harness_root.resolve()
        manifest_path = path.resolve()
        if not _within(manifest_path, root) or not manifest_path.is_file() or path.is_symlink():
            raise McpCapabilityManifestError("MCP manifest must be a regular Harness file")
        payload = _read_json_once(manifest_path, "MCP capability manifest")
        _exact_fields(payload, _MANIFEST_FIELDS, "manifest")
        if payload["schema_version"] != MCP_CAPABILITY_SCHEMA_VERSION:
            raise McpCapabilityManifestError(
                f"schema_version must be {MCP_CAPABILITY_SCHEMA_VERSION}"
            )
        raw_descriptors = payload["capabilities"]
        if not isinstance(raw_descriptors, list):
            raise McpCapabilityManifestError("capabilities must be an array")
        descriptors: list[McpCapabilityDescriptor] = []
        seen: set[tuple[str, str]] = set()
        for index, raw_descriptor in enumerate(raw_descriptors):
            descriptor = _parse_descriptor(raw_descriptor, index=index, harness_root=root)
            key = (descriptor.capability, descriptor.provider)
            if key in seen:
                raise McpCapabilityManifestError("duplicate capability/provider")
            seen.add(key)
            descriptors.append(descriptor)
        return cls(descriptors)

    def resolve(self, capability: str, provider: str) -> McpCapabilityDescriptor:
        try:
            return self._by_key[(capability, provider)]
        except KeyError as exc:
            raise McpCapabilityNotFound("MCP capability/provider is not registered") from exc

    def list_capabilities(self) -> tuple[McpCapabilityDescriptor, ...]:
        return self._descriptors


def _read_json_once(path: Path, label: str) -> Mapping[str, Any]:
    try:
        content = path.read_bytes()
        payload = json.loads(content.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise McpCapabilityManifestError(f"cannot read {label}") from exc
    if not isinstance(payload, dict):
        raise McpCapabilityManifestError(f"{label} must be an object")
    return payload


def _exact_fields(payload: Mapping[str, Any], fields: frozenset[str], label: str) -> None:
    if set(payload) != fields:
        raise McpCapabilityManifestError(f"{label} fields are not exact")


def _text(value: Any, label: str, pattern: re.Pattern[str], *, allow_empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or (not value and not allow_empty)
        or (value and pattern.fullmatch(value) is None)
        or (value and contains_sensitive_text(value))
    ):
        raise McpCapabilityManifestError(f"invalid {label}")
    return value


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise McpCapabilityManifestError(f"invalid {label}")
    return value


def _schema_snapshot(
    path_value: Any,
    hash_value: Any,
    *,
    harness_root: Path,
    label: str,
) -> tuple[Path, str, Mapping[str, Any]]:
    if not isinstance(path_value, str) or not path_value.strip():
        raise McpCapabilityManifestError(f"invalid {label} path")
    relative = Path(path_value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or any(character in path_value for character in "*?[]{}")
    ):
        raise McpCapabilityManifestError(f"unsafe {label} path")
    if not isinstance(hash_value, str) or _SHA256.fullmatch(hash_value) is None:
        raise McpCapabilityManifestError(f"invalid {label} hash")
    declared_path = harness_root / relative
    if declared_path.is_symlink():
        raise McpCapabilityManifestError(f"symlink {label} path is not allowed")
    resolved = declared_path.resolve()
    if not _within(resolved, harness_root) or not resolved.is_file():
        raise McpCapabilityManifestError(f"unsafe {label} path")
    try:
        content = resolved.read_bytes()
    except OSError as exc:
        raise McpCapabilityManifestError(f"cannot read {label} schema") from exc
    actual_hash = hashlib.sha256(content).hexdigest()
    if actual_hash != hash_value:
        raise McpCapabilityManifestError(f"{label} schema hash mismatch")
    try:
        schema = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise McpCapabilityManifestError(f"invalid {label} schema JSON") from exc
    if not isinstance(schema, dict):
        raise McpCapabilityManifestError(f"{label} schema must be an object")
    try:
        check_supported_schema(schema)
    except McpSchemaValidationError as exc:
        raise McpCapabilityManifestError(f"unsupported {label} schema") from exc
    return resolved, actual_hash, _deep_freeze(schema)


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _parse_descriptor(
    payload: Any,
    *,
    index: int,
    harness_root: Path,
) -> McpCapabilityDescriptor:
    if not isinstance(payload, dict):
        raise McpCapabilityManifestError(f"capabilities[{index}] must be an object")
    _exact_fields(payload, _DESCRIPTOR_FIELDS, f"capabilities[{index}]")
    capability = _text(payload["capability"], "capability", _CAPABILITY)
    provider = _text(payload["provider"], "provider", _CAPABILITY)
    server = _text(payload["server"], "server", _SERVER_TOOL)
    tool = _text(payload["tool"], "tool", _SERVER_TOOL)
    if tool in _GENERIC_TOOLS:
        raise McpCapabilityManifestError("generic MCP tool names are forbidden")
    contract_version = _text(
        payload["contract_version"], "contract_version", _CONTRACT_VERSION
    )
    mutation_value = payload["mutation_level"]
    if mutation_value not in {"L0", "L1"}:
        raise McpCapabilityManifestError("Phase 1A accepts only L0/L1 MCP capabilities")
    mutation_level = MutationLevel[mutation_value]
    raw_scopes = payload["required_scopes"]
    if (
        not isinstance(raw_scopes, list)
        or len(raw_scopes) > 32
        or any(not isinstance(item, str) or _SCOPE.fullmatch(item) is None for item in raw_scopes)
        or len(raw_scopes) != len(set(raw_scopes))
        or any(contains_sensitive_text(item) for item in raw_scopes)
    ):
        raise McpCapabilityManifestError("invalid required_scopes")
    timeout_seconds = _integer(payload["timeout_seconds"], "timeout_seconds", 1, 60)
    max_result_bytes = _integer(
        payload["max_result_bytes"], "max_result_bytes", 1024, 1048576
    )
    input_schema_path, input_schema_sha256, input_schema = _schema_snapshot(
        payload["input_schema_path"],
        payload["input_schema_sha256"],
        harness_root=harness_root,
        label="input",
    )
    result_schema_path, result_schema_sha256, result_schema = _schema_snapshot(
        payload["result_schema_path"],
        payload["result_schema_sha256"],
        harness_root=harness_root,
        label="result",
    )
    enabled = payload["enabled"]
    if not isinstance(enabled, bool):
        raise McpCapabilityManifestError("enabled must be a boolean")
    disabled_reason = payload["disabled_reason"]
    if (
        not isinstance(disabled_reason, str)
        or disabled_reason != disabled_reason.strip()
        or len(disabled_reason) > 256
        or contains_sensitive_text(disabled_reason)
        or (enabled and disabled_reason)
        or (not enabled and not disabled_reason)
    ):
        raise McpCapabilityManifestError("disabled_reason contract invalid")
    return McpCapabilityDescriptor(
        capability=capability,
        provider=provider,
        server=server,
        tool=tool,
        contract_version=contract_version,
        mutation_level=mutation_level,
        required_scopes=tuple(sorted(raw_scopes)),
        timeout_seconds=timeout_seconds,
        max_result_bytes=max_result_bytes,
        input_schema_path=input_schema_path,
        input_schema_sha256=input_schema_sha256,
        input_schema=input_schema,
        result_schema_path=result_schema_path,
        result_schema_sha256=result_schema_sha256,
        result_schema=result_schema,
        enabled=enabled,
        disabled_reason=disabled_reason,
    )


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
