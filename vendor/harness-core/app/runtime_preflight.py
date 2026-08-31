from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import importlib.util
from pathlib import Path
from typing import Any


def _check_writable(path: str | Path, *, create: bool = False) -> dict[str, Any]:
    candidate = Path(path).expanduser()
    try:
        if create:
            candidate.mkdir(parents=True, exist_ok=True)
        parent = candidate if candidate.exists() else candidate.parent
        parent = parent.resolve()
        if not parent.is_dir():
            raise NotADirectoryError(str(parent))
        probe = parent / f".harness-write-probe-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return {"status": "ready", "severity": "info", "retryable": False, "fallback": "", "path": str(candidate)}
    except (OSError, ValueError) as exc:
        return {
            "status": "failed",
            "severity": "error",
            "retryable": True,
            "fallback": "use_private_temp",
            "path": str(candidate),
            "message": f"{type(exc).__name__}: {exc}",
        }


def run_runtime_preflight(
    *,
    database_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    worktree_root: str | Path | None = None,
    require_git: bool = False,
    mutation_requested: bool = False,
    allow_mock: bool = False,
) -> dict[str, Any]:
    """Return a safe, serializable startup diagnosis without changing user data."""
    checks: dict[str, dict[str, Any]] = {
        "python": {
            "status": "ready",
            "severity": "info",
            "retryable": False,
            "fallback": "",
            "version": sys.version.split()[0],
            # Keep the invocation path rather than resolving the venv symlink;
            # users need to see whether the command actually entered .venv.
            "executable": str(Path(sys.executable).absolute()),
        },
        "sqlite": {"status": "ready" if sqlite3.sqlite_version else "failed", "severity": "info", "retryable": False, "fallback": ""},
        "git": {"status": "ready" if shutil.which("git") else "failed", "severity": "error", "retryable": False, "fallback": "", "message": "git executable missing" if not shutil.which("git") else ""},
    }
    missing_dependencies = [name for name in ("cryptography",) if importlib.util.find_spec(name) is None]
    checks["dependencies"] = {
        "status": "failed" if missing_dependencies else "ready",
        "severity": "warning" if missing_dependencies else "info",
        "retryable": True if missing_dependencies else False,
        "fallback": "install_runtime_dependencies",
        "missing": missing_dependencies,
        "message": f"缺少依赖：{', '.join(missing_dependencies)}" if missing_dependencies else "",
    }
    if database_path is not None:
        checks["database"] = _check_writable(Path(database_path).expanduser().parent, create=False)
    if output_dir is not None:
        checks["output"] = _check_writable(output_dir, create=False)
    if worktree_root is not None:
        checks["worktree"] = _check_writable(worktree_root, create=False)
    failed = [name for name, item in checks.items() if item.get("status") == "failed"]
    mutation_blockers: list[str] = []
    if require_git and checks["git"].get("status") != "ready":
        mutation_blockers.append("git_unavailable")
    if mutation_requested and failed:
        mutation_blockers.extend(f"runtime_{name}_unavailable" for name in failed)
    status = "ready" if not failed else ("blocked" if mutation_requested else "degraded_readonly")
    return {
        "schema_version": "1.0-runtime-preflight",
        "status": status,
        "read_only": bool(status != "ready" or not mutation_requested),
        "allow_mock": bool(allow_mock),
        "checks": checks,
        "failed_checks": failed,
        "mutation_blockers": list(dict.fromkeys(mutation_blockers)),
        "recovery_action": "使用 Harness 私有临时目录并重试；修改模式需先修复失败检查项。" if failed else "无需恢复动作。",
    }


def choose_private_runtime_root(*, prefix: str = "his_harness_") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def format_preflight_report(report: dict[str, Any]) -> str:
    lines = [f"运行前诊断：{report.get('status') or 'unknown'}"]
    for name, item in (report.get("checks") or {}).items():
        lines.append(f"- {name}: {item.get('status')} {item.get('message') or ''}".rstrip())
    if report.get("mutation_blockers"):
        lines.append(f"- 修改阻断：{', '.join(report['mutation_blockers'])}")
    lines.append(f"- 恢复动作：{report.get('recovery_action') or '-'}")
    return "\n".join(lines)
