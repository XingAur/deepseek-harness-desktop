from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.worktree_lifecycle import inspect_worktree_root, remove_worktree_marker


DEFAULT_WORKTREE_ROOT = "/tmp/his_harness_worktrees"


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean HIS Harness temporary worktree directories.")
    parser.add_argument("--worktree-dir", default=DEFAULT_WORKTREE_ROOT, help="Harness temporary worktree root")
    parser.add_argument("--project-path", action="append", default=[], help="Git project root used to run git worktree prune; repeatable")
    parser.add_argument("--max-age-hours", type=int, default=24, help="only clean owned worktrees older than this threshold")
    parser.add_argument("--apply", action="store_true", help="apply an exact preview plan")
    parser.add_argument("--confirm", default="", help="exact CLEANUP:<plan_hash> confirmation from preview")
    args = parser.parse_args()

    root = Path(args.worktree_dir).expanduser().resolve()
    result = cleanup_worktree_root(
        root=root,
        project_paths=[Path(path).expanduser().resolve() for path in args.project_path],
        max_age_hours=args.max_age_hours,
        apply=args.apply,
        confirm=args.confirm,
    )
    print(format_cleanup_result(result))
    if result["status"] in {"failed", "confirmation_required"}:
        raise SystemExit(1)


def cleanup_worktree_root(
    *,
    root: Path,
    project_paths: list[Path],
    max_age_hours: int = 24,
    apply: bool = False,
    confirm: str = "",
) -> dict:
    try:
        inspection = inspect_worktree_root(
            worktree_root=root,
            project_paths=project_paths,
            max_age_hours=max_age_hours,
        )
    except ValueError as exc:
        return {
            "status": "failed",
            "root": str(root),
            "message": str(exc),
            "candidates": [],
            "skipped": [],
            "removed": [],
            "prune": [],
        }
    result = {
        **inspection,
        "status": "preview",
        "apply": apply,
        "removed": [],
        "prune": [],
        "message": "仅生成安全清理预览，未修改 worktree。",
    }
    if not apply:
        return result
    if confirm != inspection["required_confirmation"]:
        result["status"] = "confirmation_required"
        result["message"] = f"拒绝清理：需要精确确认 {inspection['required_confirmation']}"
        return result

    for candidate in inspection["candidates"]:
        path = Path(candidate["path"])
        project = Path(candidate["project_path"])
        if candidate.get("action") == "remove_marker":
            remove_worktree_marker(worktree_root=root, worktree_path=path)
            result["removed"].append({"path": str(path), "status": "marker_removed", "returncode": 0, "stdout": "", "stderr": ""})
            continue
        completed = subprocess.run(
            ["git", "worktree", "remove", str(path)],
            cwd=str(project),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        item = {
            "path": str(path),
            "status": "removed" if completed.returncode == 0 and not path.exists() else "failed",
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        result["removed"].append(item)
        if item["status"] == "removed":
            remove_worktree_marker(worktree_root=root, worktree_path=path)
        else:
            result["status"] = "failed"

    for project_path in project_paths:
        if not project_path.exists():
            result["prune"].append({"project_path": str(project_path), "status": "skipped", "message": "项目路径不存在"})
            continue
        completed = subprocess.run(
            ["git", "worktree", "prune"],
            cwd=str(project_path),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        result["prune"].append(
            {
                "project_path": str(project_path),
                "status": "success" if completed.returncode == 0 else "failed",
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
        if completed.returncode != 0:
            result["status"] = "failed"
    if result["status"] != "failed":
        result["status"] = "success"
    result["message"] = "安全清理完成。" if result["status"] == "success" else "清理存在失败项，未使用强制目录删除兜底。"
    return result


def format_cleanup_result(result: dict) -> str:
    lines = [
        f"status: {result.get('status')}",
        f"root: {result.get('root')}",
        f"apply: {result.get('apply')}",
        f"plan_hash: {result.get('plan_hash') or '-'}",
        f"required_confirmation: {result.get('required_confirmation') or '-'}",
        f"message: {result.get('message') or '-'}",
        f"candidates: {len(result.get('candidates') or [])}",
        f"removed: {len(result.get('removed') or [])}",
        f"skipped: {len(result.get('skipped') or [])}",
        f"prune: {len(result.get('prune') or [])}",
    ]
    for item in result.get("candidates") or []:
        lines.append(f"- candidate: {item.get('path')} age={item.get('age_hours')}h")
    for item in result.get("removed") or []:
        lines.append(f"- {item.get('status')}: {item.get('path')}")
    for item in result.get("prune") or []:
        lines.append(f"- prune {item.get('status')}: {item.get('project_path')}")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
