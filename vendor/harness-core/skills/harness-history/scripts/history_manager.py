#!/usr/bin/env python3
"""Compatibility launcher for the plugin-owned Harness history manager."""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


FORMAL_PLUGIN_ROOT = Path("/Users/lym/plugins/his-harness-core")
PLUGIN_RELATIVE_PATH = Path(
    "skills/harness-history/scripts/history_manager.py"
)
PLUGIN_SCRIPT = FORMAL_PLUGIN_ROOT / PLUGIN_RELATIVE_PATH
MINIMAL_ENV = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}


def _validated_plugin_script() -> Path:
    root = Path(os.path.abspath(os.fspath(FORMAL_PLUGIN_ROOT)))
    script = Path(os.path.abspath(os.fspath(PLUGIN_SCRIPT)))
    if (
        not FORMAL_PLUGIN_ROOT.is_absolute()
        or not PLUGIN_SCRIPT.is_absolute()
        or script != root / PLUGIN_RELATIVE_PATH
    ):
        raise ValueError("plugin entrypoint escapes the fixed plugin root")

    current = Path(script.anchor)
    for part in script.parts[1:]:
        current /= part
        metadata = os.lstat(current)
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("plugin entrypoint path contains a symlink")
        if current == script:
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("plugin entrypoint is not a regular file")
        elif not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("plugin entrypoint parent is not a directory")
    return script


def main(argv: list[str] | None = None) -> int:
    try:
        script = _validated_plugin_script()
    except (OSError, ValueError):
        print(
            "his-harness-core 插件未安装或入口不安全：无法运行 "
            "$harness-history 兼容入口。",
            file=sys.stderr,
        )
        return 2
    arguments = list(sys.argv[1:] if argv is None else argv)
    os.execve(
        sys.executable,
        [sys.executable, "-I", str(script), *arguments],
        dict(MINIMAL_ENV),
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
