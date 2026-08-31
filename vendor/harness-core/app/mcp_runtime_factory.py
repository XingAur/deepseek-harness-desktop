from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.mcp_capability_registry import McpCapabilityRegistry
from app.mcp_capability_runtime import McpCapabilityRuntime
from app.mcp_gateway import McpGateway
from app.mcp_persistence import SqliteMcpStore
from app.mcp_stdio_transport import StdioMcpTransport, load_stdio_server_configs
from app.plugin_inventory import verify_plugin_inventory


class McpRuntimeFactoryError(ValueError):
    """A persistent MCP runtime cannot be assembled from the frozen inputs."""


@dataclass(frozen=True)
class PersistentMcpRuntimeBundle:
    registry: McpCapabilityRegistry
    store: SqliteMcpStore
    transport: StdioMcpTransport
    gateway: McpGateway
    runtime: McpCapabilityRuntime


def build_persistent_mcp_runtime(
    *,
    harness_root: Path,
    manifest_path: Path,
    plugin_inventory_path: Path,
    plugin_roots: Sequence[Path],
    state_root: Path,
    environment: Mapping[str, str] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> PersistentMcpRuntimeBundle:
    """Build the governed runtime used by enabled frozen MCP routes."""

    root = _existing_safe_directory(harness_root, "Harness root")
    if isinstance(plugin_roots, (str, bytes)) or not isinstance(plugin_roots, Sequence):
        raise McpRuntimeFactoryError("plugin roots must be an ordered sequence")
    inventory_path = _safe_file_within(
        plugin_inventory_path,
        root=root,
        label="plugin inventory",
    )

    # Inventory verification must happen before .mcp.json is interpreted. The
    # returned bytes are the only source accepted by the launch parser.
    verified_plugins = verify_plugin_inventory(
        inventory_path,
        list(plugin_roots),
    )
    server_configs = load_stdio_server_configs(verified_plugins)
    registry = McpCapabilityRegistry.from_file(
        Path(manifest_path),
        harness_root=root,
    )
    descriptors = registry.list_capabilities()
    declared_servers = {descriptor.server for descriptor in descriptors}
    routed_server_configs = {
        server: config
        for server, config in server_configs.items()
        if server in declared_servers
    }
    for descriptor in descriptors:
        if descriptor.enabled and descriptor.server not in routed_server_configs:
            raise McpRuntimeFactoryError(
                "enabled MCP descriptor has no frozen server configuration"
            )

    state = _safe_state_root(state_root)
    store = SqliteMcpStore(state / "mcp.sqlite")
    transport = StdioMcpTransport(
        servers=routed_server_configs,
        environment=environment,
        python_executable=_mcp_python_executable(root),
        cancelled=cancelled,
    )
    gateway = McpGateway(
        registry=registry,
        transport=transport,
        evidence_sink=store,
        audit_sink=store,
    )
    runtime = McpCapabilityRuntime(registry=registry, gateway=gateway)
    return PersistentMcpRuntimeBundle(
        registry=registry,
        store=store,
        transport=transport,
        gateway=gateway,
        runtime=runtime,
    )


def _mcp_python_executable(harness_root: Path) -> Path:
    """Prefer the Harness virtual environment without resolving its launcher."""

    candidate = Path(harness_root) / ".venv" / "bin" / "python"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    return Path(sys.executable).absolute()


def _existing_safe_directory(value: Path, label: str) -> Path:
    path = Path(value)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise McpRuntimeFactoryError(f"{label} is unavailable") from exc
    if (
        not path.is_absolute()
        or path.is_symlink()
        or resolved != path
        or not path.is_dir()
    ):
        raise McpRuntimeFactoryError(f"{label} is unsafe")
    return resolved


def _safe_file_within(value: Path, *, root: Path, label: str) -> Path:
    path = Path(value)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise McpRuntimeFactoryError(f"{label} is outside the Harness root") from exc
    if not path.is_absolute() or path.is_symlink() or resolved != path or not path.is_file():
        raise McpRuntimeFactoryError(f"{label} is unsafe")
    return resolved


def _safe_state_root(value: Path) -> Path:
    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        raise McpRuntimeFactoryError("MCP state root is unsafe")
    if path.exists():
        return _existing_safe_directory(path, "MCP state root")
    parent = _existing_safe_directory(path.parent, "MCP state parent")
    candidate = parent / path.name
    try:
        os.mkdir(candidate, 0o700)
    except OSError as exc:
        raise McpRuntimeFactoryError("MCP state root cannot be created") from exc
    return _existing_safe_directory(candidate, "MCP state root")
