"""Compatibility adapter for the his-engineering PostgreSQL evidence provider."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

from app.capability_contracts import MutationLevel
from app.capability_registry import (
    CapabilityDescriptor,
    CapabilityRegistry,
    CapabilityRegistryError,
    PathIdentity,
    _path_identity as registry_path_identity,
)
from tools.capability_check import CliError, load_runtime_config


_HARNESS_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_CONFIG = _HARNESS_ROOT / "config" / "capabilities.json"
_PROVIDER_MODULE_PREFIX = "_harness_his_engineering_pg_evidence"
_MAX_PROVIDER_BYTES = 4 * 1024 * 1024
_EXPECTED_SCOPES = ("database:metadata:read", "database:rows:read")
_READONLY_CREDENTIAL_PATTERN = re.compile(
    r"^pg_[a-z0-9_]+_readonly_(?:dsn|user|password)$"
)


def _configured_roots(config_path: Path) -> tuple[Path, ...]:
    if not config_path.is_file():
        return ()
    try:
        config = load_runtime_config(str(config_path))
    except CliError as exc:
        raise ImportError(
            "his-engineering runtime config is invalid; no database connection was attempted."
        ) from exc
    return tuple(Path(item) for item in config.plugin_roots)


def _candidate_roots(
    *,
    include_staging: bool,
    config_path: Path,
) -> tuple[Path, ...]:
    values: list[Path] = []
    test_root = os.environ.get("HARNESS_STAGED_PLUGIN_ROOT", "")
    if (
        include_staging
        and os.environ.get("HARNESS_ENABLE_STAGED_PLUGIN_TESTS") == "1"
        and test_root
        and Path(test_root).is_absolute()
    ):
        values.append(Path(test_root) / "his-engineering")
    values.extend(_configured_roots(config_path))
    unique: list[Path] = []
    seen: set[str] = set()
    for value in values:
        key = str(value.absolute())
        if key not in seen:
            seen.add(key)
            unique.append(value)
    return tuple(unique)


def _exact_database_descriptor(root: Path) -> CapabilityDescriptor | None:
    try:
        registry = CapabilityRegistry.from_plugin_roots([root])
        descriptor = registry.resolve("database.inspect", "postgresql")
        resolved_root = root.resolve(strict=True)
        expected_entrypoint = (
            resolved_root / "scripts" / "database_read.py"
        ).resolve(strict=True)
    except (OSError, RuntimeError, CapabilityRegistryError):
        return None
    if (
        descriptor.plugin != "his-engineering"
        or descriptor.name != "database.inspect"
        or descriptor.provider != "postgresql"
        or descriptor.contract_version != "pg-evidence.v2"
        or descriptor.mutation_level is not MutationLevel.L1
        or descriptor.credential_class != "database_readonly"
        or descriptor.enabled is not True
        or descriptor.disabled_reason
        or descriptor.scopes != _EXPECTED_SCOPES
        or descriptor.plugin_root != resolved_root
        or descriptor.entrypoint != expected_entrypoint
        or descriptor.declared_entrypoint
        != resolved_root / "scripts" / "database_read.py"
        or descriptor.plugin_root_identity is None
        or descriptor.entrypoint_identity is None
    ):
        return None
    return descriptor


def _descriptor_identity_is_current(descriptor: CapabilityDescriptor) -> bool:
    try:
        return (
            descriptor.plugin_root is not None
            and descriptor.entrypoint is not None
            and registry_path_identity(descriptor.plugin_root)
            == descriptor.plugin_root_identity
            and registry_path_identity(descriptor.entrypoint)
            == descriptor.entrypoint_identity
        )
    except (OSError, RuntimeError):
        return False


def _provider_source(
    descriptor: CapabilityDescriptor,
) -> tuple[Path, PathIdentity, bytes] | None:
    if descriptor.plugin_root is None or not _descriptor_identity_is_current(descriptor):
        return None
    try:
        root = descriptor.plugin_root.resolve(strict=True)
        declared = root / "scripts" / "pg_evidence.py"
        provider_path = declared.resolve(strict=True)
        provider_path.relative_to(root)
        if provider_path != declared or not provider_path.is_file():
            return None
        identity = registry_path_identity(provider_path)
        source = _read_verified_source(provider_path, identity)
    except (OSError, RuntimeError, ValueError):
        return None
    if not _descriptor_identity_is_current(descriptor):
        return None
    return provider_path, identity, source


def _read_verified_source(path: Path, expected: PathIdentity) -> bytes:
    if expected[3] > _MAX_PROVIDER_BYTES:
        raise ValueError("provider source is too large")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        metadata = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
        )
        if (
            metadata != expected[:5]
            or not stat.S_ISREG(before.st_mode)
            or before.st_size > _MAX_PROVIDER_BYTES
        ):
            raise ValueError("provider source identity changed")
        chunks: list[bytes] = []
        remaining = _MAX_PROVIDER_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        source = b"".join(chunks)
        after = os.fstat(descriptor)
        after_metadata = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
        )
        if (
            len(source) != expected[3]
            or after_metadata != expected[:5]
            or hashlib.sha256(source).hexdigest() != expected[5]
        ):
            raise ValueError("provider source identity changed")
        return source
    finally:
        os.close(descriptor)


def _module_from_verified_source(
    provider_path: Path,
    provider_identity: PathIdentity,
    source: bytes,
    descriptor: CapabilityDescriptor,
) -> ModuleType | None:
    module_name = (
        _PROVIDER_MODULE_PREFIX
        + "_"
        + hashlib.sha256(str(provider_path).encode("utf-8")).hexdigest()[:16]
    )
    loaded = sys.modules.get(module_name)
    if (
        isinstance(loaded, ModuleType)
        and Path(loaded.__file__ or "").resolve() == provider_path
        and getattr(loaded, "__provider_identity__", None) == provider_identity
        and _descriptor_identity_is_current(descriptor)
    ):
        return loaded
    module = ModuleType(module_name)
    module.__file__ = str(provider_path)
    module.__package__ = ""
    module.__provider_identity__ = provider_identity
    sys.modules[module_name] = module
    try:
        exec(compile(source, str(provider_path), "exec"), module.__dict__)
    except Exception:
        sys.modules.pop(module_name, None)
        return None
    if (
        not _descriptor_identity_is_current(descriptor)
        or registry_path_identity(provider_path) != provider_identity
    ):
        sys.modules.pop(module_name, None)
        return None
    return module


def _load_provider(
    *,
    include_staging: bool | None = None,
    config_path: Path = _RUNTIME_CONFIG,
) -> tuple[ModuleType, Path]:
    if include_staging is None:
        include_staging = (
            os.environ.get("HARNESS_ENABLE_STAGED_PLUGIN_TESTS") == "1"
        )
    for root in _candidate_roots(
        include_staging=include_staging,
        config_path=config_path,
    ):
        descriptor = _exact_database_descriptor(root)
        if descriptor is None:
            continue
        provider = _provider_source(descriptor)
        if provider is None:
            continue
        provider_path, provider_identity, source = provider
        try:
            module = _module_from_verified_source(
                provider_path,
                provider_identity,
                source,
                descriptor,
            )
        except (OSError, RuntimeError, ValueError):
            module = None
        if module is not None:
            return module, provider_path
    raise ImportError(
        "his-engineering PostgreSQL evidence provider is required; no database connection was attempted."
    )


def build_database_capability_service(
    credentials_path: Path,
    *,
    credential_aliases: Mapping[str, str] | None = None,
):
    """Build the routed database service while keeping credentials out of CLI."""
    from app.capability_runtime import CapabilityRuntime
    from app.capability_service import CapabilityService

    try:
        payload = json.loads(credentials_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    credentials = {
        str(key): value
        for key, value in payload.items()
        if isinstance(payload, dict)
        and isinstance(key, str)
        and _READONLY_CREDENTIAL_PATTERN.fullmatch(key)
        and isinstance(value, str)
    }
    credentials = apply_readonly_credential_aliases(
        credentials,
        credential_aliases or {},
    )
    registry = CapabilityRegistry.from_plugin_roots(
        [Path(__provider_root__)]
    )
    return CapabilityService(
        CapabilityRuntime(
            registry,
            environment_allowlist=tuple(sorted(credentials)),
        ),
        routing_mode="enforce",
        runtime_environment=credentials,
    )


def apply_readonly_credential_aliases(
    credentials: Mapping[str, str],
    aliases: Mapping[str, str],
) -> dict[str, str]:
    """Bind an explicit logical profile to an existing readonly key triplet.

    The copy is process-local: no credentials file is changed and a complete
    target triplet always wins over an alias.  Partial target credentials are
    deliberately left incomplete so values from different profiles cannot be
    mixed by accident.
    """

    resolved = dict(credentials)
    for target_raw, source_raw in aliases.items():
        target = str(target_raw).strip().lower()
        source = str(source_raw).strip().lower()
        if not target or not source or target == source:
            continue
        target_prefix = f"pg_{target}_readonly"
        source_prefix = f"pg_{source}_readonly"
        target_keys = [f"{target_prefix}_{suffix}" for suffix in ("dsn", "user", "password")]
        source_keys = [f"{source_prefix}_{suffix}" for suffix in ("dsn", "user", "password")]
        if any(resolved.get(key) for key in target_keys):
            continue
        if not all(resolved.get(key) for key in source_keys):
            continue
        for target_key, source_key in zip(target_keys, source_keys):
            resolved[target_key] = resolved[source_key]
    return resolved


_provider, _provider_path = _load_provider()
__provider_source__ = str(_provider_path)
__provider_root__ = str(_provider_path.parents[1])
__all__ = tuple(name for name in vars(_provider) if not name.startswith("_"))

for _name in __all__:
    globals()[_name] = getattr(_provider, _name)


def __getattr__(name: str) -> Any:
    return getattr(_provider, name)
