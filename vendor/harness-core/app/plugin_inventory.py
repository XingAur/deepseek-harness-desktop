from __future__ import annotations

import json
import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


INVENTORY_SCHEMA_VERSION = "his-plugin-inventory.v1"
_ROOT_FIELDS = frozenset({"schema_version", "plugins"})
_PLUGIN_FIELDS = frozenset(
    {
        "name",
        "version",
        "capabilities_sha256",
        "capabilities",
        "sources_sha256",
    }
)
_PLUGIN_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
_CAPABILITY_NAME = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
ENABLED_HIGH_RISK_ALLOWLIST = frozenset(
    ("git.push", "gitlab.write", "github.write")
)


class PluginInventoryError(ValueError):
    pass


def validate_high_risk_allowlist(capabilities: object) -> None:
    """Reject manifest-enabled L4/L5 capabilities outside the reviewed set."""
    if not isinstance(capabilities, list):
        raise PluginInventoryError("插件 capability 无法验证。")
    for capability in capabilities:
        if not isinstance(capability, dict):
            raise PluginInventoryError("插件 capability 无法验证。")
        if (
            capability.get("mutation_level") in {"L4", "L5"}
            and capability.get("enabled") is True
            and capability.get("name") not in ENABLED_HIGH_RISK_ALLOWLIST
        ):
            raise PluginInventoryError("高风险 capability 不在冻结启用白名单。")


@dataclass(frozen=True)
class PluginInventoryItem:
    name: str
    version: str
    capabilities_sha256: str
    capabilities: tuple[str, ...]
    sources_sha256: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PluginInventory:
    schema_version: str
    plugins: tuple[PluginInventoryItem, ...]


@dataclass(frozen=True)
class VerifiedPlugin:
    root: Path
    sources: tuple[tuple[str, bytes], ...]

    def source(self, relative_path: str) -> bytes:
        try:
            return dict(self.sources)[relative_path]
        except KeyError as exc:
            raise PluginInventoryError("冻结清单缺少所需来源。") from exc


def resolve_plugin_source_root(
    repository_root: str | Path,
    formal_plugin_root: str | Path,
) -> Path:
    staged_root = Path(repository_root) / "plugins"
    if staged_root.is_dir() and not staged_root.is_symlink():
        return staged_root
    return Path(formal_plugin_root)


def load_plugin_inventory(path: str | Path) -> PluginInventory:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PluginInventoryError("插件冻结清单无法解析。") from exc
    return parse_plugin_inventory(payload)


def parse_plugin_inventory(payload: Any) -> PluginInventory:
    root = _mapping(payload)
    _exact_fields(root, _ROOT_FIELDS)
    if root["schema_version"] != INVENTORY_SCHEMA_VERSION:
        raise PluginInventoryError(
            f"schema_version 必须为 {INVENTORY_SCHEMA_VERSION}。"
        )
    raw_plugins = root["plugins"]
    if not isinstance(raw_plugins, list) or not raw_plugins:
        raise PluginInventoryError("plugins 必须是非空数组。")

    plugins: list[PluginInventoryItem] = []
    plugin_names: set[str] = set()
    capability_names: set[str] = set()
    for raw_item in raw_plugins:
        item = _mapping(raw_item)
        _exact_fields(item, _PLUGIN_FIELDS)
        name = _text(item["name"])
        version = _text(item["version"])
        digest = _text(item["capabilities_sha256"])
        raw_capabilities = item["capabilities"]
        raw_sources = item["sources_sha256"]
        if not _PLUGIN_NAME.fullmatch(name) or name in plugin_names:
            raise PluginInventoryError("插件名称无效或重复。")
        if not _SHA256.fullmatch(digest):
            raise PluginInventoryError("capabilities_sha256 必须是 64 位小写十六进制。")
        if not isinstance(raw_capabilities, list) or not raw_capabilities:
            raise PluginInventoryError("capabilities 必须是非空数组。")
        capabilities: list[str] = []
        for raw_capability in raw_capabilities:
            capability = _text(raw_capability)
            if (
                not _CAPABILITY_NAME.fullmatch(capability)
                or capability in capabilities
                or capability in capability_names
            ):
                raise PluginInventoryError("capability 名称无效或重复。")
            capabilities.append(capability)
            capability_names.add(capability)
        plugin_names.add(name)
        if not isinstance(raw_sources, dict) or not raw_sources:
            raise PluginInventoryError("sources_sha256 必须是非空对象。")
        sources: list[tuple[str, str]] = []
        for raw_path, raw_source_digest in sorted(raw_sources.items()):
            source_path = _safe_relative_path(raw_path)
            source_digest = _text(raw_source_digest)
            if not _SHA256.fullmatch(source_digest):
                raise PluginInventoryError(
                    "sources_sha256 的值必须是 64 位小写十六进制。"
                )
            sources.append((source_path, source_digest))
        plugins.append(
            PluginInventoryItem(
                name=name,
                version=version,
                capabilities_sha256=digest,
                capabilities=tuple(capabilities),
                sources_sha256=tuple(sources),
            )
        )
    return PluginInventory(
        schema_version=INVENTORY_SCHEMA_VERSION,
        plugins=tuple(plugins),
    )


def verify_plugin_inventory(
    inventory_path: str | Path,
    plugin_roots: tuple[str, ...] | list[str] | tuple[Path, ...] | list[Path],
    *,
    registry: object | None = None,
) -> dict[str, VerifiedPlugin]:
    """Verify exact configured plugin roots and every frozen executable source."""
    inventory = load_plugin_inventory(inventory_path)
    if len(plugin_roots) != len(inventory.plugins):
        raise PluginInventoryError("配置的插件根目录与冻结清单不一致。")
    verified: dict[str, VerifiedPlugin] = {}
    for root_value, item in zip(plugin_roots, inventory.plugins):
        root = Path(root_value)
        try:
            if not root.is_absolute() or root.is_symlink():
                raise PluginInventoryError("插件根目录不安全。")
            resolved_root = root.resolve(strict=True)
            if resolved_root != root or not resolved_root.is_dir():
                raise PluginInventoryError("插件根目录不安全。")
            manifest_path = resolved_root / "capabilities.json"
            plugin_path = resolved_root / ".codex-plugin" / "plugin.json"
            manifest_bytes = _read_frozen_file(resolved_root, manifest_path)
            plugin_payload = json.loads(
                _read_frozen_file(resolved_root, plugin_path).decode("utf-8")
            )
            manifest_payload = json.loads(manifest_bytes.decode("utf-8"))
        except PluginInventoryError:
            raise
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise PluginInventoryError("插件冻结来源无法验证。") from exc
        if not isinstance(manifest_payload, dict) or not isinstance(
            plugin_payload, dict
        ):
            raise PluginInventoryError("插件冻结来源结构无效。")
        if (
            manifest_payload.get("plugin") != item.name
            or manifest_payload.get("plugin_version") != item.version
            or plugin_payload.get("name") != item.name
            or plugin_payload.get("version") != item.version
            or hashlib.sha256(manifest_bytes).hexdigest()
            != item.capabilities_sha256
        ):
            raise PluginInventoryError("插件版本或 manifest 哈希与冻结清单不一致。")
        raw_capabilities = manifest_payload.get("capabilities")
        validate_high_risk_allowlist(raw_capabilities)
        if not isinstance(raw_capabilities, list) or [
            capability.get("name") if isinstance(capability, dict) else None
            for capability in raw_capabilities
        ] != list(item.capabilities):
            raise PluginInventoryError("插件 capability 与冻结清单不一致。")
        required_sources = {
            "capabilities.json",
            ".codex-plugin/plugin.json",
        }
        for capability in raw_capabilities:
            if not isinstance(capability, dict):
                raise PluginInventoryError("插件 capability 无法验证。")
            entrypoint = capability.get("entrypoint")
            if entrypoint is not None:
                required_sources.add(_safe_relative_path(entrypoint))
            dependencies = capability.get("dependencies", [])
            if not isinstance(dependencies, list):
                raise PluginInventoryError("插件 dependencies 无法验证。")
            required_sources.update(
                _safe_relative_path(dependency)
                for dependency in dependencies
            )
        frozen_sources = dict(item.sources_sha256)
        if not required_sources.issubset(frozen_sources):
            raise PluginInventoryError("冻结清单缺少 capability 执行来源。")
        verified_sources: list[tuple[str, bytes]] = []
        for relative_path, expected_digest in frozen_sources.items():
            source = _read_frozen_file(
                resolved_root,
                resolved_root / relative_path,
            )
            if hashlib.sha256(source).hexdigest() != expected_digest:
                raise PluginInventoryError("插件执行来源哈希与冻结清单不一致。")
            verified_sources.append((relative_path, source))
        if registry is not None:
            _verify_registry_identities(registry, item.name, resolved_root)
        verified[item.name] = VerifiedPlugin(
            root=resolved_root,
            sources=tuple(verified_sources),
        )
    return verified


def audit_plugin_layout_drift(
    *,
    inventory_path: str | Path,
    active_roots: tuple[str | Path, ...] | list[str | Path],
    candidate_roots: tuple[str | Path, ...] | list[str | Path],
) -> dict[str, Any]:
    """Compare another installed layout with the active frozen sources; never repairs or copies files."""
    inventory = load_plugin_inventory(inventory_path)
    if len(active_roots) != len(inventory.plugins) or len(candidate_roots) != len(inventory.plugins):
        raise PluginInventoryError("插件布局与冻结清单数量不一致。")
    reports: list[dict[str, Any]] = []
    active_inventory_drift = False
    candidate_drift = False
    for item, active_value, candidate_value in zip(
        inventory.plugins,
        active_roots,
        candidate_roots,
    ):
        active_root = _safe_plugin_root(active_value)
        candidate_root = _safe_plugin_root(candidate_value)
        changed_sources: list[str] = []
        missing_sources: list[str] = []
        active_changed_sources: list[str] = []
        for relative_path, expected_digest in item.sources_sha256:
            active_digest = _source_digest_or_empty(active_root, relative_path)
            candidate_digest = _source_digest_or_empty(candidate_root, relative_path)
            if active_digest != expected_digest:
                active_changed_sources.append(relative_path)
            if candidate_digest == "":
                missing_sources.append(relative_path)
            elif candidate_digest != active_digest:
                changed_sources.append(relative_path)
        active_inventory_drift = active_inventory_drift or bool(active_changed_sources)
        candidate_drift = candidate_drift or bool(changed_sources or missing_sources)
        reports.append(
            {
                "name": item.name,
                "active_root": str(active_root),
                "candidate_root": str(candidate_root),
                "active_inventory_drift_sources": active_changed_sources,
                "changed_sources": changed_sources,
                "missing_sources": missing_sources,
            }
        )
    status = (
        "active_inventory_drift"
        if active_inventory_drift
        else "drift_detected"
        if candidate_drift
        else "aligned"
    )
    return {
        "schema_version": "his-plugin-layout-drift.v1",
        "status": status,
        "mutation_performed": False,
        "plugins": reports,
    }


def _safe_plugin_root(value: str | Path) -> Path:
    root = Path(value)
    if not root.is_absolute() or root.is_symlink():
        raise PluginInventoryError("插件根目录不安全。")
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise PluginInventoryError("插件根目录不安全。") from exc
    if resolved != root or not resolved.is_dir():
        raise PluginInventoryError("插件根目录不安全。")
    return resolved


def _source_digest_or_empty(root: Path, relative_path: str) -> str:
    try:
        source = _read_frozen_file(root, root / relative_path)
    except PluginInventoryError:
        return ""
    return hashlib.sha256(source).hexdigest()


def _read_frozen_file(root: Path, path: Path) -> bytes:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise PluginInventoryError("冻结来源不能逃逸插件根目录。") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise PluginInventoryError("冻结来源不能包含符号链接。")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PluginInventoryError("冻结来源无法读取。") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise PluginInventoryError("冻结来源必须是普通文件。")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise PluginInventoryError("冻结来源在读取期间发生变化。")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _verify_registry_identities(
    registry: object,
    plugin_name: str,
    plugin_root: Path,
) -> None:
    from app.capability_registry import (
        CapabilityRegistry,
        _path_identity,
    )

    if not isinstance(registry, CapabilityRegistry):
        raise PluginInventoryError("capability registry 无法与冻结清单绑定。")
    descriptors = tuple(
        descriptor
        for descriptor in registry.descriptors
        if descriptor.plugin == plugin_name
    )
    if not descriptors:
        raise PluginInventoryError("冻结插件未注册 capability。")
    try:
        for descriptor in descriptors:
            if (
                descriptor.plugin_root != plugin_root
                or descriptor.plugin_root_identity != _path_identity(plugin_root)
            ):
                raise PluginInventoryError("插件根目录身份发生变化。")
            if descriptor.entrypoint is not None and (
                descriptor.entrypoint_identity
                != _path_identity(descriptor.entrypoint)
            ):
                raise PluginInventoryError("capability entrypoint 身份发生变化。")
            for dependency, identity in descriptor.dependency_identities:
                if identity != _path_identity(dependency):
                    raise PluginInventoryError("capability dependency 身份发生变化。")
    except OSError as exc:
        raise PluginInventoryError("capability 执行来源身份无法验证。") from exc


def _safe_relative_path(value: Any) -> str:
    text = _text(value)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != text:
        raise PluginInventoryError("sources_sha256 路径必须是安全相对路径。")
    return text


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise PluginInventoryError("插件冻结清单结构无效。")
    return value


def _exact_fields(
    value: Mapping[str, Any],
    expected: frozenset[str],
) -> None:
    if set(value) != expected:
        raise PluginInventoryError("插件冻结清单字段无效。")


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise PluginInventoryError("插件冻结清单文本字段无效。")
    return value
