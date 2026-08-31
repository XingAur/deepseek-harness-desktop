"""Compatibility adapter; canonical Git delivery is owned by his-engineering."""
from __future__ import annotations

import json
import hashlib
import os
import stat
import sys
import types
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.plugin_inventory import PluginInventoryError, load_plugin_inventory


_ERROR = "his-engineering plugin is required for Git delivery; no repository changes were made."
PLUGIN_REQUIRED_MESSAGE = _ERROR
_FIXED_ROOT = Path("/Users/lym/plugins/his-engineering")
_BUNDLED_ROOT = Path(__file__).resolve().parents[2] / "plugins" / "his-engineering"
_INVENTORY_PATH = Path(__file__).resolve().parents[1] / "config" / "plugin_inventory.json"
_MAX_PLUGIN_SOURCE_BYTES = 4 * 1024 * 1024
_REQUIRED_FILES = (
    "capabilities.json",
    ".codex-plugin/plugin.json",
    "scripts/delivery_closure.py",
    "scripts/delivery_store.py",
    "scripts/git_delivery.py",
    "scripts/git_push.py",
    "scripts/gitlab_read.py",
    "scripts/gitlab_write.py",
    "scripts/github_write.py",
)
FROZEN_DELIVERY_STATE_SEQUENCE = (
    "waiting_release_runtime_acceptance",
    "release_runtime_accepted",
    "task_commit_created",
    "waiting_rc_runtime_acceptance",
    "rc_runtime_accepted",
    "gitlab_delivery_pending",
    "github_delivery_pending",
    "completed",
)


class _PluginResolutionError(RuntimeError):
    pass


def _expected_plugin_identity():
    try:
        inventory = load_plugin_inventory(_INVENTORY_PATH)
        return next(item for item in inventory.plugins if item.name == "his-engineering")
    except (OSError, PluginInventoryError, StopIteration) as exc:
        raise _PluginResolutionError(_ERROR) from exc


class DeliveryError(RuntimeError):
    def __init__(self, code: str, message: str = _ERROR, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code, self.details = code, details or {}


def _regular_file_metadata(path: Path) -> tuple[int, int, int, int, int]:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise _PluginResolutionError(_ERROR)
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns


def _regular_file_identity(path: Path) -> tuple[int, int, int, int, int, str]:
    return (*_regular_file_metadata(path), hashlib.sha256(_read_regular_bytes(path)).hexdigest())


def _directory_identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise _PluginResolutionError(_ERROR)
    return info.st_dev, info.st_ino


def _read_regular_bytes(path: Path) -> bytes:
    expected = _regular_file_metadata(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        observed = (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
        )
        if observed != expected or not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_PLUGIN_SOURCE_BYTES:
            raise _PluginResolutionError(_ERROR)
        chunks: list[bytes] = []
        remaining = _MAX_PLUGIN_SOURCE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            len(value) > _MAX_PLUGIN_SOURCE_BYTES
            or (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
                after.st_mtime_ns,
            )
            != expected
        ):
            raise _PluginResolutionError(_ERROR)
        return value
    finally:
        os.close(descriptor)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_read_regular_bytes(path).decode("utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise _PluginResolutionError(_ERROR) from exc
    if not isinstance(value, dict):
        raise _PluginResolutionError(_ERROR)
    return value


def _validated_root(candidate: Path) -> Path:
    expected_plugin = _expected_plugin_identity()
    if not candidate.is_absolute():
        raise _PluginResolutionError(_ERROR)
    root_identity = _directory_identity(candidate)
    scripts_identity = _directory_identity(candidate / "scripts")
    plugin_directory_identity = _directory_identity(candidate / ".codex-plugin")
    expected_sources = dict(expected_plugin.sources_sha256)
    for relative in _REQUIRED_FILES:
        path = candidate / relative
        identity = _regular_file_identity(path)
        if expected_sources.get(relative) != identity[-1]:
            raise _PluginResolutionError(_ERROR)
    manifest_bytes = _read_regular_bytes(candidate / "capabilities.json")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise _PluginResolutionError(_ERROR) from exc
    plugin = _read_json(candidate / ".codex-plugin" / "plugin.json")
    if (
        manifest.get("schema_version") != "his-capabilities.v1"
        or manifest.get("plugin") != "his-engineering"
        or manifest.get("plugin_version") != expected_plugin.version
        or hashlib.sha256(manifest_bytes).hexdigest()
        != expected_plugin.capabilities_sha256
        or not isinstance(manifest.get("capabilities"), list)
        or plugin.get("name") != "his-engineering"
        or plugin.get("version") != expected_plugin.version
    ):
        raise _PluginResolutionError(_ERROR)
    if (
        _directory_identity(candidate) != root_identity
        or _directory_identity(candidate / "scripts") != scripts_identity
        or _directory_identity(candidate / ".codex-plugin") != plugin_directory_identity
    ):
        raise _PluginResolutionError(_ERROR)
    return candidate


def _root() -> Path:
    test_root = os.environ.get("HARNESS_STAGED_PLUGIN_ROOT", "")
    candidates: list[Path] = []
    if (
        os.environ.get("HARNESS_ENABLE_STAGED_PLUGIN_TESTS") == "1"
        and test_root
        and Path(test_root).is_absolute()
    ):
        candidates.append(Path(test_root) / "his-engineering")
    if not test_root:
        candidates.append(_BUNDLED_ROOT)
    candidates.append(_FIXED_ROOT)
    for candidate in candidates:
        try:
            return _validated_root(candidate)
        except (OSError, _PluginResolutionError):
            continue
    raise _PluginResolutionError(_ERROR)


def _load(name: str, path: Path):
    source = _read_regular_bytes(path)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = name.rpartition(".")[0]
    module.__spec__ = None
    sys.modules[name] = module
    code = compile(source, str(path), "exec")
    exec(code, module.__dict__)
    return module


def _canonical():
    root = _root()
    package = "_harness_his_engineering"
    holder = types.ModuleType(package)
    holder.__path__ = [str(root / "scripts")]
    holder.__package__ = package
    sys.modules[package] = holder
    try:
        store = _load(package + ".delivery_store", root / "scripts" / "delivery_store.py")
        closure = _load(package + ".delivery_closure", root / "scripts" / "delivery_closure.py")
        closure_exports = {
            "DeliveryError",
            "DeliveryPolicy",
            "DeliveryRequest",
            "DeliveryClosure",
            "inspect_repository",
            "build_delivery_plan",
            "stable_hash",
            "policy_snapshot",
            "delivery_plan_to_markdown",
            "DELIVERY_STATE_SEQUENCE",
        }
        if any(not hasattr(closure, name) for name in closure_exports) or not hasattr(store, "SQLiteDeliveryStore"):
            raise _PluginResolutionError(_ERROR)
        _validated_root(root)
    except Exception as exc:
        for name in tuple(sys.modules):
            if name == package or name.startswith(package + "."):
                sys.modules.pop(name, None)
        raise _PluginResolutionError(_ERROR) from exc
    return closure, store


try:
    _closure, _store = _canonical()
except Exception:
    _closure = _store = None


def _plugin_required() -> DeliveryError:
    return DeliveryError("his_engineering_plugin_required", _ERROR)


def _require():
    if _closure is None or _store is None:
        raise _plugin_required()
    return _closure, _store


def commit_capability_registry():
    """Load the complete manifest, then bind L3 to all executable sources."""
    from app.capability_registry import CapabilityRegistry

    try:
        expected_plugin = _expected_plugin_identity()
        root = _root()
        entrypoint = root / "scripts" / "git_delivery.py"
        dependencies = (
            root / "scripts" / "delivery_closure.py",
            root / "scripts" / "delivery_store.py",
        )
        verified_identities = {}
        for source in (entrypoint, *dependencies):
            identity = _regular_file_identity(source)
            compile(_read_regular_bytes(source), str(source), "exec")
            if _regular_file_identity(source) != identity:
                raise _PluginResolutionError(_ERROR)
            verified_identities[source] = identity
        registry = CapabilityRegistry.from_plugin_roots([root])
        descriptor = registry.resolve("git.commit-local", "his-engineering")
        if (
            descriptor.plugin != "his-engineering"
            or descriptor.plugin_version != expected_plugin.version
            or descriptor.enabled is not True
            or descriptor.mutation_level.name != "L3"
            or descriptor.credential_class != "none"
            or descriptor.contract_version != "git-commit-local.v1"
            or descriptor.scopes != ("repository:commit-local",)
            or descriptor.declared_entrypoint != entrypoint
            or descriptor.entrypoint != entrypoint
            or descriptor.entrypoint_identity != verified_identities[entrypoint]
        ):
            raise _PluginResolutionError(_ERROR)
        if (
            _validated_root(root) != root
            or any(
                _regular_file_identity(source) != identity
                for source, identity in verified_identities.items()
            )
        ):
            raise _PluginResolutionError(_ERROR)
        secured = replace(
            descriptor,
            dependency_identities=tuple(
                (source, verified_identities[source]) for source in dependencies
            ),
        )
        return CapabilityRegistry(
            [
                secured
                if (item.name, item.provider) == ("git.commit-local", "his-engineering")
                else item
                for item in registry.descriptors
            ]
        )
    except Exception as exc:
        raise _plugin_required() from exc


def build_delivery_capability_service():
    """Build the compatibility service without exposing plugin resolution to CLI."""
    from app.capability_runtime import CapabilityRuntime
    from app.capability_service import CapabilityService

    return CapabilityService(
        CapabilityRuntime(
            commit_capability_registry(),
            external_writes_default=True,
        ),
        routing_mode="enforce",
    )


def _policy_from_rule_pack(cls, path: str | Path | None = None):
    from app.harness_config import load_rule_pack

    rules = dict(load_rule_pack(path).get("git") or {})
    branches, names = dict(rules.get("base_branches") or {}), dict(rules.get("branch_name") or {})
    messages, permissions = dict(rules.get("commit_message") or {}), dict(rules.get("permissions") or {})
    values = {
        "push_feature_default": bool(permissions.get("auto_push_task_branch", False)),
        "cherry_pick_integration_default": bool(permissions.get("auto_integrate_rc", False)),
        "push_integration_default": bool(permissions.get("auto_push_rc", False)),
    }
    translated = {
        "base_branch": branches.get("release"),
        "integration_branch": rules.get("integration_branch") or branches.get("default"),
        "remote_name": rules.get("remote"),
        "requirement_branch_template": names.get("requirement"),
        "bug_branch_template": names.get("bug"),
        "task_branch_template": names.get("task"),
        "requirement_commit_template": messages.get("requirement"),
        "bug_commit_template": messages.get("bug"),
        "task_commit_template": messages.get("task"),
    }
    values.update({field: value for field, value in translated.items() if isinstance(value, str) and value})
    return cls.from_payload(values)


if _closure is not None:
    DeliveryError = _closure.DeliveryError
    DeliveryRequest = _closure.DeliveryRequest
    DeliveryPolicy = _closure.DeliveryPolicy
    DeliveryPolicy.from_rule_pack = classmethod(_policy_from_rule_pack)
    inspect_repository = _closure.inspect_repository
    build_delivery_plan = _closure.build_delivery_plan
    stable_hash = _closure.stable_hash
    delivery_plan_to_markdown = _closure.delivery_plan_to_markdown
    DELIVERY_STATE_SEQUENCE = _closure.DELIVERY_STATE_SEQUENCE

    def audit_cherry_pick_parity(*_args, **_kwargs):
        raise DeliveryError(
            "git_remote_delivery_disabled",
            "独立 RC 审计入口不具备交付授权；请使用不可变交付事务执行。",
        )
else:
    @dataclass
    class DeliveryRequest:
        entity_kind: str = ""
        entity_id: str = ""
        title: str = ""
        url: str = ""
        project_path: str = ""
        expected_diff: str = ""
        allowed_paths: list[str] | None = None
        output_dir: str = ""

    class DeliveryPolicy:
        @classmethod
        def from_payload(cls, *_args, **_kwargs):
            raise _plugin_required()

        @classmethod
        def from_rule_pack(cls, *_args, **_kwargs):
            raise _plugin_required()

    def _missing(*_args, **_kwargs):
        raise _plugin_required()

    inspect_repository = build_delivery_plan = stable_hash = audit_cherry_pick_parity = delivery_plan_to_markdown = _missing
    DELIVERY_STATE_SEQUENCE = FROZEN_DELIVERY_STATE_SEQUENCE


class DeliveryClosure:
    def __init__(self, *, policy=None, store=None, on_state_change=None) -> None:
        closure, storage = _require()
        self.policy = policy or DeliveryPolicy.from_rule_pack()
        self.store = store or storage.SQLiteDeliveryStore()
        self.store.init()
        self._canonical = closure.DeliveryClosure(
            store=self.store,
            policy=self.policy,
            on_state_change=on_state_change,
        )

    def __getattr__(self, name: str):
        return getattr(self._canonical, name)
