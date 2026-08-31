#!/usr/bin/env python3
"""Compatibility adapter for the installed or staged Yunxiao read plugin.

The legacy skill intentionally owns no Yunxiao provider implementation.  Its
public API is re-exported from one trusted plugin location so existing callers
retain their imports while new work uses ``$yunxiao-workitem-read``.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType


_INSTALLED_PLUGIN_ROOT = Path("/Users/lym/plugins/yunxiao")
_TEST_PLUGIN_ROOT = os.environ.get("HARNESS_STAGED_PLUGIN_ROOT", "")
PLUGIN_ROOT_CANDIDATES = (
    _INSTALLED_PLUGIN_ROOT,
    *(
        (Path(_TEST_PLUGIN_ROOT) / "yunxiao",)
        if (
            os.environ.get("HARNESS_ENABLE_STAGED_PLUGIN_TESTS") == "1"
            and _TEST_PLUGIN_ROOT
            and Path(_TEST_PLUGIN_ROOT).is_absolute()
        )
        else ()
    ),
)
_RELATIVE_IMPLEMENTATION = (
    Path("skills")
    / "yunxiao-workitem-read"
    / "scripts"
    / "yunxiao_evidence.py"
)
_PRIVATE_MODULE_NAME = "_yunxiao_plugin_evidence_adapter"
_INSTALLATION_ERROR = (
    "Yunxiao plugin is not installed; install the Yunxiao plugin and use "
    "$yunxiao-workitem-read."
)
_REQUIRED_EXPORTS = (
    "CONTRACT_VERSION",
    "DEFAULT_BASE_URL",
    "DEFAULT_CREDENTIALS_FILE",
    "DEFAULT_MAX_DOWNLOAD_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "SafeApiRedirectHandler",
    "YunxiaoClient",
    "collect_evidence",
    "load_credentials",
    "parse_work_item_id",
    "redact_for_output",
    "render_markdown",
    "validate_evidence",
    "write_outputs",
)


def _is_unambiguous_regular_file(root: Path, path: Path) -> bool:
    """Accept only a non-symlink module wholly below a non-symlink plugin root."""
    try:
        if root.is_symlink() or not root.is_dir():
            return False
        candidate = root / _RELATIVE_IMPLEMENTATION
        if candidate != path or candidate.is_symlink() or not candidate.is_file():
            return False
        current = root
        for part in _RELATIVE_IMPLEMENTATION.parts:
            current = current / part
            if current.is_symlink():
                return False
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _load_plugin_module() -> tuple[ModuleType, Path, dict[str, object]]:
    """Load only from the fixed trusted plugin-root candidates."""
    for root in PLUGIN_ROOT_CANDIDATES:
        candidate = root / _RELATIVE_IMPLEMENTATION
        if not _is_unambiguous_regular_file(root, candidate):
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                _PRIVATE_MODULE_NAME,
                candidate,
            )
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[_PRIVATE_MODULE_NAME] = module
            spec.loader.exec_module(module)
            exports = {name: getattr(module, name) for name in _REQUIRED_EXPORTS}
            return module, candidate, exports
        except Exception:
            sys.modules.pop(_PRIVATE_MODULE_NAME, None)
    raise RuntimeError(_INSTALLATION_ERROR)


_PLUGIN_MODULE, PLUGIN_IMPLEMENTATION_PATH, _PLUGIN_EXPORTS = _load_plugin_module()

# Keep the legacy public surface explicitly small and auditable.  Both legacy
# CLIs, intake, and tests import only these names.
CONTRACT_VERSION = _PLUGIN_EXPORTS["CONTRACT_VERSION"]
DEFAULT_BASE_URL = _PLUGIN_EXPORTS["DEFAULT_BASE_URL"]
DEFAULT_CREDENTIALS_FILE = _PLUGIN_EXPORTS["DEFAULT_CREDENTIALS_FILE"]
DEFAULT_MAX_DOWNLOAD_BYTES = _PLUGIN_EXPORTS["DEFAULT_MAX_DOWNLOAD_BYTES"]
DEFAULT_TIMEOUT_SECONDS = _PLUGIN_EXPORTS["DEFAULT_TIMEOUT_SECONDS"]
SafeApiRedirectHandler = _PLUGIN_EXPORTS["SafeApiRedirectHandler"]
YunxiaoClient = _PLUGIN_EXPORTS["YunxiaoClient"]
collect_evidence = _PLUGIN_EXPORTS["collect_evidence"]
load_credentials = _PLUGIN_EXPORTS["load_credentials"]
parse_work_item_id = _PLUGIN_EXPORTS["parse_work_item_id"]
redact_for_output = _PLUGIN_EXPORTS["redact_for_output"]
render_markdown = _PLUGIN_EXPORTS["render_markdown"]
validate_evidence = _PLUGIN_EXPORTS["validate_evidence"]
write_outputs = _PLUGIN_EXPORTS["write_outputs"]

__all__ = (
    "CONTRACT_VERSION",
    "DEFAULT_BASE_URL",
    "DEFAULT_CREDENTIALS_FILE",
    "DEFAULT_MAX_DOWNLOAD_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "PLUGIN_IMPLEMENTATION_PATH",
    "SafeApiRedirectHandler",
    "YunxiaoClient",
    "collect_evidence",
    "load_credentials",
    "parse_work_item_id",
    "redact_for_output",
    "render_markdown",
    "validate_evidence",
    "write_outputs",
)
