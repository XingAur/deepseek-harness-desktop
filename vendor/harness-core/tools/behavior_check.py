from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.behavior_acceptance import behavior_to_json, behavior_to_markdown, build_behavior_acceptance


def main() -> None:
    parser = argparse.ArgumentParser(description="Run HIS Harness v0.10 behavior acceptance check for a current diff.")
    parser.add_argument("--project-path", required=True, help="Git repository path")
    parser.add_argument("--allowed-path", action="append", default=[], help="relative path to include in git diff; repeatable")
    parser.add_argument("--title", default="", help="demand or bug title")
    parser.add_argument("--entity-id", default="", help="DFHIS id")
    parser.add_argument("--demand-text", default="", help="demand text")
    parser.add_argument("--demand-file", default="", help="file containing demand text")
    parser.add_argument("--output-dir", default="", help="optional output directory")
    args = parser.parse_args()

    project_path = Path(args.project_path).expanduser().resolve()
    demand_text = args.demand_text
    if args.demand_file:
        demand_text = Path(args.demand_file).expanduser().read_text(encoding="utf-8")
    diff_text = read_git_diff(project_path=project_path, allowed_paths=args.allowed_path)
    behavior = build_behavior_acceptance(
        title=args.title or args.entity_id,
        demand_text=demand_text,
        diff_text=diff_text,
        changed_paths=args.allowed_path,
    )
    markdown = behavior_to_markdown(behavior)
    print(markdown)
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "behavior_acceptance.json").write_text(behavior_to_json(behavior), encoding="utf-8")
        (output_dir / "behavior_acceptance.md").write_text(markdown, encoding="utf-8")
    if behavior.get("status") in {"failed", "needs_review"}:
        raise SystemExit(1)


def read_git_diff(*, project_path: Path, allowed_paths: list[str]) -> str:
    command = ["git", "diff", "--no-ext-diff"]
    if allowed_paths:
        command.extend(["--", *allowed_paths])
    result = subprocess.run(command, cwd=project_path, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "git diff failed")
    return result.stdout


if __name__ == "__main__":
    main()
