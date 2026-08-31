from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path
from typing import Any


WORKTREE_MARKER_SCHEMA_VERSION = "1.0-harness-worktree-marker"
MARKER_DIRECTORY_NAME = ".harness_worktree_markers"
HARNESS_WORKTREE_PREFIXES = ("run_", "precommit_")
_LOCAL_AGENT_ROOT = re.compile(r"^/private/tmp/his_harness_stage_f_[A-Za-z0-9_-]{1,96}$")


def prepare_local_agent_worktree(
    *,
    project_path: Path,
    worktree_root: Path,
    run_id: int,
    generation: int | None = None,
) -> dict[str, Any]:
    """Create exactly one owned local-agent worktree without removing anything.

    The runner supplies a freshly-created, owner-private `/private/tmp` root.
    A pre-existing target is never reclaimed: a caller must use the explicit
    lifecycle cleanup flow instead of letting an execution overwrite evidence.
    """

    if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id <= 0:
        raise ValueError("local_agent_worktree_invalid")
    project = _directory_no_follow(project_path)
    root = _private_local_agent_root(worktree_root)
    if root != worktree_root or project != project_path:
        raise ValueError("local_agent_worktree_invalid")
    # The private runner root is reusable, but may only contain its private
    # control directory and independently-marked owned runs.  Never reclaim a
    # target merely because its name happens to match.
    for child in root.iterdir():
        if child.name in {".harness_local_agent_control", MARKER_DIRECTORY_NAME}:
            continue
        if not child.name.startswith("run_") or child.is_symlink() or not child.is_dir():
            raise ValueError("local_agent_worktree_root_not_fresh")
    if generation is not None and (not isinstance(generation, int) or isinstance(generation, bool) or generation < 2):
        raise ValueError("local_agent_worktree_invalid")
    target = root / (f"run_{run_id}" if generation is None else f"run_{run_id}_attempt_{generation}")
    if target.exists() or target.is_symlink():
        raise ValueError("local_agent_worktree_exists")
    from app.worktree_executor import SafeGitBoundary
    boundary = SafeGitBoundary(project)
    before = _worktree_listing(project, boundary=boundary)
    source_git_before = capture_git_metadata(project)
    added = boundary.run(["worktree", "add", "--detach", str(target), "HEAD"], cwd=project)
    if added["returncode"] != 0:
        raise ValueError("local_agent_worktree_create_failed")
    after = _worktree_listing(project, boundary=boundary)
    source_git_after = capture_git_metadata(project)
    if str(target) not in after or set(after) != set(before) | {str(target)}:
        raise ValueError("local_agent_worktree_registration_invalid")
    if not _only_expected_git_worktree_metadata_change(source_git_before, source_git_after):
        raise ValueError("local_agent_source_git_metadata_changed")
    worktree = _directory_no_follow(target)
    git_entry = _regular_file(worktree / ".git")
    marker = create_worktree_marker(
        worktree_root=root,
        worktree_path=worktree,
        project_path=project,
        run_id=run_id,
        role="local-agent",
    )
    return {
        "project_path": str(project),
        "worktree_root": str(root),
        "worktree_path": str(worktree),
        "marker_path": str(marker),
        "source_identity": _identity(project),
        "source_git_identity": _identity(_directory_no_follow(project / ".git")),
        "worktree_identity": _identity(worktree),
        "worktree_git_entry_identity": _identity(git_entry),
        "worktrees_before": sorted(before),
        "worktrees_after": sorted(after),
        "source_git_metadata": source_git_after,
    }


def capture_git_metadata(project_path: Path) -> dict[str, tuple[int, int, int, str]]:
    """Return a no-follow metadata digest for source `.git`, never its content."""

    project = _directory_no_follow(project_path)
    git_dir = _directory_no_follow(project / ".git")
    result: dict[str, tuple[int, int, int, str]] = {}
    for current, directories, files in os.walk(git_dir, followlinks=False):
        current_path = Path(current)
        for name in sorted([*directories, *files]):
            path = current_path / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                raise ValueError("local_agent_source_git_metadata_invalid")
            relative = path.relative_to(git_dir).as_posix()
            digest = ""
            if stat.S_ISREG(metadata.st_mode):
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            result[relative] = (metadata.st_dev, metadata.st_ino, stat.S_IMODE(metadata.st_mode), digest)
    return result


def _private_local_agent_root(path: Path) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or _LOCAL_AGENT_ROOT.fullmatch(path.as_posix()) is None:
        raise ValueError("local_agent_worktree_root_invalid")
    return _private_directory(path)


def _private_directory(path: Path) -> Path:
    result = _directory_no_follow(path)
    metadata = path.lstat()
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise ValueError("local_agent_worktree_not_private")
    return result


def _directory_no_follow(path: Path) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("local_agent_worktree_invalid")
    current = Path("/")
    for part in path.parts[1:]:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as error:
            raise ValueError("local_agent_worktree_invalid") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("local_agent_worktree_invalid")
    return path


def _regular_file(path: Path) -> Path:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ValueError("local_agent_worktree_invalid") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("local_agent_worktree_invalid")
    return path


def _identity(path: Path) -> tuple[int, int, int]:
    metadata = path.lstat()
    return metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode)


def _worktree_listing(project_path: Path, *, boundary: Any | None = None) -> set[str]:
    if boundary is None:
        from app.worktree_executor import SafeGitBoundary
        boundary = SafeGitBoundary(project_path)
    listed = boundary.run(["worktree", "list", "--porcelain"], cwd=project_path)
    if listed["returncode"] != 0:
        raise ValueError("local_agent_worktree_listing_failed")
    return {
        str(Path(line.removeprefix("worktree ")).resolve())
        for line in bytes(listed["stdout"]).decode("utf-8", "strict").splitlines()
        if line.startswith("worktree ")
    }


def _only_expected_git_worktree_metadata_change(
    before: dict[str, tuple[int, int, int, str]], after: dict[str, tuple[int, int, int, str]]
) -> bool:
    for path in set(before) | set(after):
        if before.get(path) == after.get(path):
            continue
        if path != "worktrees" and not path.startswith("worktrees/"):
            return False
    return True


def create_worktree_marker(
    *,
    worktree_root: str | Path,
    worktree_path: str | Path,
    project_path: str | Path,
    run_id: str | int,
    role: str,
    created_at_epoch: float | None = None,
) -> Path:
    root = Path(worktree_root).expanduser().resolve()
    path = Path(worktree_path).expanduser().resolve()
    project = Path(project_path).expanduser().resolve()
    if path.parent != root or not is_harness_worktree_name(path.name):
        raise ValueError(f"worktree 路径不属于 Harness 根目录：{path}")
    marker_dir = root / MARKER_DIRECTORY_NAME
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker_path = marker_path_for(root, path)
    payload = {
        "schema_version": WORKTREE_MARKER_SCHEMA_VERSION,
        "owner": "his-harness",
        "worktree_path": str(path),
        "project_path": str(project),
        "run_id": str(run_id),
        "role": str(role).strip() or "unknown",
        "created_at_epoch": float(created_at_epoch if created_at_epoch is not None else time.time()),
    }
    temporary = marker_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, marker_path)
    return marker_path


def remove_worktree_marker(*, worktree_root: str | Path, worktree_path: str | Path) -> None:
    root = Path(worktree_root).expanduser().resolve()
    marker = marker_path_for(root, Path(worktree_path).expanduser().resolve())
    marker.unlink(missing_ok=True)


def inspect_worktree_root(
    *,
    worktree_root: str | Path,
    project_paths: list[Path],
    max_age_hours: int = 24,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    root = Path(worktree_root).expanduser().resolve()
    now = float(now_epoch if now_epoch is not None else time.time())
    allowed_projects = {str(Path(path).expanduser().resolve()) for path in project_paths}
    result: dict[str, Any] = {
        "root": str(root),
        "max_age_hours": max_age_hours,
        "candidates": [],
        "skipped": [],
        "marker_errors": [],
    }
    if max_age_hours < 1:
        raise ValueError("max_age_hours 必须大于等于 1。")
    if not root.exists():
        return finalize_inspection(result)
    if not is_allowed_tmp_root(root):
        raise ValueError(f"拒绝检查非 Harness 临时路径：{root}")

    markers = load_markers(root, errors=result["marker_errors"])
    markers_by_path = {item["worktree_path"]: item for item in markers}
    observed_marker_paths: set[str] = set()
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if child.name == MARKER_DIRECTORY_NAME:
            continue
        if not is_harness_worktree_name(child.name):
            result["skipped"].append(skip_item(child, "unowned", "非 Harness worktree 前缀目录"))
            continue
        path = child.resolve()
        marker = markers_by_path.get(str(path))
        if marker is None or marker.get("owner") != "his-harness":
            result["skipped"].append(skip_item(path, "unowned", "缺少有效 Harness 生命周期标记"))
            continue
        observed_marker_paths.add(str(path))
        project = str(Path(marker["project_path"]).expanduser().resolve())
        if project not in allowed_projects:
            result["skipped"].append(skip_item(path, "project_not_allowed", "标记项目不在本次显式项目白名单"))
            continue
        age_hours = max(0.0, (now - float(marker["created_at_epoch"])) / 3600)
        if age_hours < max_age_hours:
            result["skipped"].append(skip_item(path, "active_recent", "未达到孤儿超时阈值", age_hours=age_hours))
            continue
        if str(path) not in registered_worktrees(Path(project)):
            result["skipped"].append(skip_item(path, "unregistered_blocked", "Git 未登记该 worktree，拒绝直接删除", age_hours=age_hours))
            continue
        status = run_git(["status", "--porcelain"], cwd=path)
        if status["returncode"] != 0:
            result["skipped"].append(skip_item(path, "inspection_failed", "无法读取 worktree 状态", age_hours=age_hours))
            continue
        if status["stdout"].strip():
            result["skipped"].append(skip_item(path, "dirty_blocked", "worktree 存在改动，禁止自动清理", age_hours=age_hours))
            continue
        result["candidates"].append(
            {
                "path": str(path),
                "project_path": project,
                "run_id": marker["run_id"],
                "role": marker["role"],
                "age_hours": round(age_hours, 3),
                "marker_path": marker["_marker_path"],
                "status": "stale_clean_owned",
                "action": "remove_worktree",
            }
        )
    for marker in markers:
        if marker["worktree_path"] in observed_marker_paths:
            continue
        marker_path = Path(marker["worktree_path"])
        project = str(Path(marker["project_path"]).expanduser().resolve())
        age_hours = max(0.0, (now - float(marker["created_at_epoch"])) / 3600)
        if project not in allowed_projects:
            result["skipped"].append(skip_item(marker_path, "project_not_allowed", "孤立标记项目不在本次显式项目白名单"))
        elif age_hours < max_age_hours:
            result["skipped"].append(skip_item(marker_path, "marker_pending", "创建中标记尚未达到超时阈值", age_hours=age_hours))
        else:
            result["candidates"].append(
                {
                    "path": str(marker_path),
                    "project_path": project,
                    "run_id": marker["run_id"],
                    "role": marker["role"],
                    "age_hours": round(age_hours, 3),
                    "marker_path": marker["_marker_path"],
                    "status": "stale_marker_only",
                    "action": "remove_marker",
                }
            )
    return finalize_inspection(result)


def load_markers(root: Path, *, errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    marker_dir = root / MARKER_DIRECTORY_NAME
    if not marker_dir.is_dir():
        return []
    markers: list[dict[str, Any]] = []
    for path in sorted(marker_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema_version") != WORKTREE_MARKER_SCHEMA_VERSION:
                raise ValueError("schema_version 无效")
            required = ("worktree_path", "project_path", "run_id", "role", "created_at_epoch")
            if any(payload.get(key) in (None, "") for key in required):
                raise ValueError("缺少必填字段")
            worktree_path = Path(payload["worktree_path"]).expanduser().resolve()
            if worktree_path.parent != root or not is_harness_worktree_name(worktree_path.name):
                raise ValueError("worktree_path 越界")
            payload["_marker_path"] = str(path)
            markers.append(payload)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            errors.append({"path": str(path), "error": str(exc)})
    return markers


def registered_worktrees(project_path: Path) -> set[str]:
    completed = run_git(["worktree", "list", "--porcelain"], cwd=project_path)
    if completed["returncode"] != 0:
        return set()
    return {
        str(Path(line.removeprefix("worktree ")).expanduser().resolve())
        for line in completed["stdout"].splitlines()
        if line.startswith("worktree ")
    }


def run_git(arguments: list[str], *, cwd: Path) -> dict[str, Any]:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def marker_path_for(root: Path, worktree_path: Path) -> Path:
    digest = hashlib.sha256(str(worktree_path).encode("utf-8")).hexdigest()
    return root / MARKER_DIRECTORY_NAME / f"{digest}.json"


def skip_item(path: Path, status: str, reason: str, *, age_hours: float | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"path": str(path), "status": status, "reason": reason}
    if age_hours is not None:
        item["age_hours"] = round(age_hours, 3)
    return item


def finalize_inspection(result: dict[str, Any]) -> dict[str, Any]:
    stable_plan = [
        {
            "action": item.get("action"),
            "path": item.get("path"),
            "project_path": item.get("project_path"),
            "run_id": item.get("run_id"),
            "role": item.get("role"),
            "status": item.get("status"),
        }
        for item in result["candidates"]
    ]
    canonical = json.dumps(stable_plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result["plan_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    result["required_confirmation"] = f"CLEANUP:{result['plan_hash']}"
    return result


def is_allowed_tmp_root(root: Path) -> bool:
    text = root.as_posix()
    return text.startswith("/tmp/his_harness") or text.startswith("/private/tmp/his_harness")


def is_harness_worktree_name(name: str) -> bool:
    return any(name.startswith(prefix) for prefix in HARNESS_WORKTREE_PREFIXES)
