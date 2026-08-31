from __future__ import annotations

import json
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.capability_contracts import MutationLevel


MANIFEST_SCHEMA_VERSION = "his-capabilities.v1"
PathIdentity = tuple[int, int, int, int, int, str]
_MANIFEST_FIELDS = frozenset({"schema_version", "plugin", "plugin_version", "capabilities"})
_CAPABILITY_FIELDS = frozenset({
    "name", "provider", "contract_version", "mutation_level", "credential_class",
    "entrypoint", "dependencies", "enabled", "disabled_reason", "scopes",
})
_CAPABILITY_REQUIRED_FIELDS = frozenset({
    "name", "provider", "contract_version", "mutation_level", "credential_class",
    "enabled", "scopes",
})


class CapabilityRegistryError(ValueError):
    """Base error for capability manifest loading and lookup."""


class CapabilityManifestError(CapabilityRegistryError):
    """A capability manifest is absent, malformed, or unsafe."""


class CapabilityResolutionError(CapabilityRegistryError):
    """A requested capability/provider pair cannot be resolved."""


class CapabilityAmbiguityError(CapabilityResolutionError):
    """A capability-only lookup matches multiple providers."""


@dataclass(frozen=True)
class CapabilityDescriptor:
    plugin: str
    plugin_version: str
    name: str
    provider: str
    contract_version: str
    mutation_level: MutationLevel
    credential_class: str
    entrypoint: Path | None
    enabled: bool
    disabled_reason: str
    scopes: tuple[str, ...]
    plugin_root: Path | None = None
    declared_entrypoint: Path | None = None
    plugin_root_identity: PathIdentity | None = None
    entrypoint_identity: PathIdentity | None = None
    dependency_identities: tuple[tuple[Path, PathIdentity], ...] = ()


class CapabilityRegistry:
    def __init__(self, descriptors: Sequence[CapabilityDescriptor]) -> None:
        self._descriptors = tuple(descriptors)
        self._by_key = {(item.name, item.provider): item for item in self._descriptors}

    @classmethod
    def from_plugin_roots(cls, plugin_roots: Sequence[str | Path]) -> "CapabilityRegistry":
        descriptors: list[CapabilityDescriptor] = []
        seen: set[tuple[str, str]] = set()
        for root_value in plugin_roots:
            try:
                plugin_root = Path(root_value)
                if not plugin_root.is_dir():
                    raise CapabilityManifestError(f"插件根目录不存在：{plugin_root}。")
                plugin_root = plugin_root.resolve()
                manifest_path = plugin_root / "capabilities.json"
                if not manifest_path.is_file():
                    raise CapabilityManifestError(f"capabilities.json 不存在：{manifest_path}。")
                payload = _read_manifest(manifest_path)
                plugin = _required_text(payload, "plugin")
                plugin_version = _required_text(payload, "plugin_version")
                for index, item in enumerate(_required_list(payload, "capabilities")):
                    descriptor = _descriptor_from_payload(item, plugin_root, plugin, plugin_version, index)
                    key = (descriptor.name, descriptor.provider)
                    if key in seen:
                        raise CapabilityManifestError(
                            f"重复 capability/provider：{descriptor.name}/{descriptor.provider}。"
                        )
                    seen.add(key)
                    descriptors.append(descriptor)
            except CapabilityRegistryError:
                raise
            except (OSError, RuntimeError, UnicodeError) as exc:
                raise CapabilityManifestError("capabilities.json 无法加载。") from exc
        return cls(descriptors)

    @property
    def descriptors(self) -> tuple[CapabilityDescriptor, ...]:
        return self._descriptors

    def __len__(self) -> int:
        return len(self._descriptors)

    def resolve(self, capability: str, provider: str = "") -> CapabilityDescriptor:
        if provider:
            try:
                return self._by_key[(capability, provider)]
            except KeyError as exc:
                raise CapabilityResolutionError(
                    f"未注册 capability/provider：{capability}/{provider}。"
                ) from exc
        matches = [item for item in self._descriptors if item.name == capability]
        if not matches:
            raise CapabilityResolutionError(f"未注册 capability：{capability}。")
        if len(matches) > 1:
            providers = ", ".join(sorted(item.provider for item in matches))
            raise CapabilityAmbiguityError(
                f"capability '{capability}' 存在多个 provider: {providers}。"
            )
        return matches[0]


def _read_manifest(manifest_path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CapabilityManifestError(f"capabilities.json 无法解析：{manifest_path}。") from exc
    data = _mapping(payload, "manifest")
    _exact_fields(data, _MANIFEST_FIELDS, "manifest")
    if data["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise CapabilityManifestError(f"schema_version 必须为 {MANIFEST_SCHEMA_VERSION}。")
    return data


def _descriptor_from_payload(
    payload: Any, plugin_root: Path, plugin: str, plugin_version: str, index: int
) -> CapabilityDescriptor:
    data = _mapping(payload, f"capabilities[{index}]")
    _fields_with_optional(data, _CAPABILITY_REQUIRED_FIELDS, _CAPABILITY_FIELDS, f"capabilities[{index}]")
    name = _required_text(data, "name")
    provider = _required_text(data, "provider")
    contract_version = _required_text(data, "contract_version")
    credential_class = _required_text(data, "credential_class")
    mutation_level = _mutation_level(data.get("mutation_level"))
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        raise CapabilityManifestError("enabled 必须是布尔值。")
    disabled_reason = data.get("disabled_reason", "")
    if disabled_reason != "" and not isinstance(disabled_reason, str):
        raise CapabilityManifestError("disabled_reason 必须是字符串。")
    if not enabled and (not isinstance(disabled_reason, str) or not disabled_reason.strip()):
        raise CapabilityManifestError("disabled capability 的 disabled_reason 不能为空。")
    if "entrypoint" in data and data["entrypoint"] is None:
        raise CapabilityManifestError("entrypoint 必须是非空字符串。")
    entrypoint = _entrypoint(data.get("entrypoint"), plugin_root, enabled)
    dependencies = _dependencies(
        data.get("dependencies", []),
        plugin_root,
        entrypoint,
    )
    return CapabilityDescriptor(
        plugin=plugin,
        plugin_version=plugin_version,
        name=name,
        provider=provider,
        contract_version=contract_version,
        mutation_level=mutation_level,
        credential_class=credential_class,
        entrypoint=entrypoint,
        enabled=enabled,
        disabled_reason=disabled_reason,
        scopes=_scopes(data.get("scopes")),
        plugin_root=plugin_root,
        declared_entrypoint=(plugin_root / Path(data["entrypoint"])) if "entrypoint" in data else None,
        plugin_root_identity=_path_identity(plugin_root),
        entrypoint_identity=_path_identity(entrypoint) if entrypoint is not None else None,
        dependency_identities=dependencies,
    )


def _entrypoint(value: Any, plugin_root: Path, enabled: bool) -> Path | None:
    if value is None:
        if enabled:
            raise CapabilityManifestError("enabled capability 的 entrypoint 不能为空。")
        return None
    if not isinstance(value, str) or not value.strip():
        raise CapabilityManifestError("entrypoint 必须是非空字符串。")
    relative_path = Path(value)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise CapabilityManifestError("entrypoint 必须是插件根目录内的相对路径。")
    entrypoint = plugin_root / relative_path
    if not entrypoint.exists():
        raise CapabilityManifestError(f"entrypoint 不存在：{value}。")
    resolved = entrypoint.resolve()
    try:
        resolved.relative_to(plugin_root)
    except ValueError as exc:
        raise CapabilityManifestError(f"entrypoint 不能逃逸插件根目录：{value}。") from exc
    if not resolved.is_file():
        raise CapabilityManifestError(f"entrypoint 必须是文件：{value}。")
    return resolved


def _dependencies(
    value: Any,
    plugin_root: Path,
    entrypoint: Path | None,
) -> tuple[tuple[Path, PathIdentity], ...]:
    if not isinstance(value, list):
        raise CapabilityManifestError("dependencies 必须是唯一的插件内相对文件路径数组。")
    if (
        not all(isinstance(item, str) and bool(item.strip()) for item in value)
        or len(value) != len(set(value))
    ):
        raise CapabilityManifestError("dependencies 必须是唯一的插件内相对文件路径数组。")
    if value and entrypoint is None:
        raise CapabilityManifestError("声明 dependencies 时必须声明 entrypoint。")
    dependencies: list[tuple[Path, PathIdentity]] = []
    seen_paths: set[Path] = set()
    for item in value:
        relative = Path(item)
        if relative.is_absolute() or ".." in relative.parts:
            raise CapabilityManifestError("dependencies 必须是插件内相对文件路径。")
        declared = plugin_root / relative
        current = plugin_root
        try:
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    raise CapabilityManifestError("dependencies 不能包含符号链接。")
            info = declared.lstat()
            resolved = declared.resolve(strict=True)
            resolved.relative_to(plugin_root)
        except CapabilityManifestError:
            raise
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            raise CapabilityManifestError("dependencies 文件不存在或逃逸插件根目录。") from exc
        if resolved == entrypoint:
            raise CapabilityManifestError("dependencies 不能包含 entrypoint。")
        if (
            resolved != declared
            or resolved in seen_paths
            or not stat.S_ISREG(info.st_mode)
        ):
            raise CapabilityManifestError("dependencies 必须是插件内普通文件。")
        seen_paths.add(resolved)
        dependencies.append((resolved, _path_identity(resolved)))
    return tuple(dependencies)


def _path_identity(path: Path) -> PathIdentity:
    metadata = os.stat(path)
    digest = ""
    if stat.S_ISREG(metadata.st_mode):
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        hasher = hashlib.sha256()
        try:
            before = os.fstat(descriptor)
            while True:
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    break
                hasher.update(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        expected = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_size,
            metadata.st_mtime_ns,
        )
        if expected != (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
        ) or expected != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise OSError("capability source identity changed while hashing")
        digest = hasher.hexdigest()
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        digest,
    )


def _exact_fields(data: Mapping[str, Any], fields: frozenset[str], label: str) -> None:
    missing = fields - data.keys()
    unexpected = data.keys() - fields
    if missing:
        raise CapabilityManifestError(f"{label} 缺少字段：{', '.join(sorted(missing))}。")
    if unexpected:
        raise CapabilityManifestError(f"{label} 存在未知字段：{', '.join(sorted(unexpected))}。")


def _fields_with_optional(
    data: Mapping[str, Any], required: frozenset[str], allowed: frozenset[str], label: str
) -> None:
    missing = required - data.keys()
    unexpected = data.keys() - allowed
    if missing:
        raise CapabilityManifestError(f"{label} 缺少字段：{', '.join(sorted(missing))}。")
    if unexpected:
        raise CapabilityManifestError(f"{label} 存在未知字段：{', '.join(sorted(unexpected))}。")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise CapabilityManifestError(f"{label} 必须是对象。")
    return value


def _required_text(data: Mapping[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CapabilityManifestError(f"{field} 必须是非空字符串。")
    return value


def _required_list(data: Mapping[str, Any], field: str) -> list[Any]:
    value = data.get(field)
    if not isinstance(value, list):
        raise CapabilityManifestError(f"{field} 必须是数组。")
    return value


def _mutation_level(value: Any) -> MutationLevel:
    if not isinstance(value, str):
        raise CapabilityManifestError("mutation_level 必须是字符串。")
    try:
        return MutationLevel[value]
    except KeyError as exc:
        raise CapabilityManifestError("mutation_level 必须为 L0 至 L5。") from exc


def _scopes(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CapabilityManifestError("scopes 必须是数组。")
    scopes = tuple(value)
    if not scopes or any(not isinstance(item, str) or not item.strip() for item in scopes) or len(set(scopes)) != len(scopes):
        raise CapabilityManifestError("scopes 必须是唯一非空字符串列表。")
    return scopes
