from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.behavior_acceptance import build_behavior_acceptance
from app.interaction_evidence import (
    behavior_test_plan_to_markdown,
    interaction_evidence_to_json,
    interaction_evidence_to_markdown,
    build_interaction_evidence_package,
    method_regression_result_to_markdown,
    playwright_screenshot_index_to_markdown,
    ui_evidence_manifest_to_markdown,
)
from app.method_test_runner import method_test_runner_to_json, method_test_runner_to_markdown, run_method_test_commands
from app.ui_evidence_runner import ui_evidence_runner_to_json, ui_evidence_runner_to_markdown, run_ui_evidence_commands


def main() -> None:
    parser = argparse.ArgumentParser(description="Run HIS Harness v0.10.2 interaction evidence check for a current diff.")
    parser.add_argument("--project-path", required=True, help="Git repository path")
    parser.add_argument("--allowed-path", action="append", default=[], help="relative path to include in git diff; repeatable")
    parser.add_argument("--title", default="", help="demand or bug title")
    parser.add_argument("--entity-id", default="", help="DFHIS id")
    parser.add_argument("--demand-text", default="", help="demand text")
    parser.add_argument("--demand-file", default="", help="file containing demand text")
    parser.add_argument("--method-evidence-file", default="", help="JSON file with method-level case results")
    parser.add_argument("--method-test-command", action="append", default=[], help="command run in project path to emit v0.10.3A method evidence JSON; repeatable")
    parser.add_argument("--ui-evidence-path", action="append", default=[], help="screenshot/video/GIF/manual evidence file path; repeatable")
    parser.add_argument("--ui-capture-command", action="append", default=[], help="command run in project path to emit v0.10.3B UI evidence JSON and files; repeatable")
    parser.add_argument("--output-dir", default="", help="optional output directory")
    args = parser.parse_args()

    project_path = Path(args.project_path).expanduser().resolve()
    demand_text = args.demand_text
    if args.demand_file:
        demand_text = Path(args.demand_file).expanduser().read_text(encoding="utf-8")
    method_evidence = {}
    if args.method_evidence_file:
        method_evidence = json.loads(Path(args.method_evidence_file).expanduser().read_text(encoding="utf-8"))
    diff_text = read_git_diff(project_path=project_path, allowed_paths=args.allowed_path)
    title = args.title or args.entity_id
    behavior = build_behavior_acceptance(
        title=title,
        demand_text=demand_text,
        diff_text=diff_text,
        changed_paths=args.allowed_path,
    )
    method_test_runner = {}
    if args.method_test_command and not method_evidence:
        seed_package = build_interaction_evidence_package(
            title=title,
            demand_text=demand_text,
            diff_text=diff_text,
            changed_paths=args.allowed_path,
            behavior_acceptance=behavior,
            method_evidence={},
            ui_evidence_paths=args.ui_evidence_path,
        )
        method_test_runner = run_method_test_commands(
            behavior_test_plan=seed_package.get("behavior_test_plan") or {},
            commands=args.method_test_command,
            cwd=project_path,
        )
        method_evidence = method_test_runner
    ui_evidence_runner = {}
    ui_evidence_paths = list(args.ui_evidence_path or [])
    if args.ui_capture_command:
        runner_output_dir = (Path(args.output_dir).expanduser().resolve() / "ui_capture") if args.output_dir else Path("/tmp/his_harness_ui_evidence_check").resolve()
        ui_evidence_runner = run_ui_evidence_commands(
            commands=args.ui_capture_command,
            cwd=project_path,
            output_dir=runner_output_dir,
        )
        ui_evidence_paths.extend(ui_evidence_runner.get("artifact_paths") or [])
    package = build_interaction_evidence_package(
        title=title,
        demand_text=demand_text,
        diff_text=diff_text,
        changed_paths=args.allowed_path,
        behavior_acceptance=behavior,
        method_evidence=method_evidence,
        ui_evidence_paths=ui_evidence_paths,
    )
    markdown = interaction_evidence_to_markdown(package)
    print(markdown)
    if args.output_dir:
        write_outputs(Path(args.output_dir).expanduser().resolve(), package, method_test_runner, ui_evidence_runner)
    if package.get("status") in {"failed", "needs_evidence"}:
        raise SystemExit(1)


def read_git_diff(*, project_path: Path, allowed_paths: list[str]) -> str:
    command = ["git", "diff", "--no-ext-diff"]
    if allowed_paths:
        command.extend(["--", *allowed_paths])
    result = subprocess.run(command, cwd=project_path, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "git diff failed")
    return result.stdout


def write_outputs(output_dir: Path, package: dict, method_test_runner: dict | None = None, ui_evidence_runner: dict | None = None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    method_test_runner = method_test_runner or {}
    if method_test_runner:
        (output_dir / "method_test_runner.json").write_text(method_test_runner_to_json(method_test_runner), encoding="utf-8")
        (output_dir / "method_test_runner.md").write_text(method_test_runner_to_markdown(method_test_runner), encoding="utf-8")
    ui_evidence_runner = ui_evidence_runner or {}
    if ui_evidence_runner:
        (output_dir / "ui_evidence_runner.json").write_text(ui_evidence_runner_to_json(ui_evidence_runner), encoding="utf-8")
        (output_dir / "ui_evidence_runner.md").write_text(ui_evidence_runner_to_markdown(ui_evidence_runner), encoding="utf-8")
    (output_dir / "interaction_evidence.json").write_text(interaction_evidence_to_json(package), encoding="utf-8")
    (output_dir / "interaction_evidence.md").write_text(interaction_evidence_to_markdown(package), encoding="utf-8")
    plan = package.get("behavior_test_plan") or {}
    method_result = package.get("method_regression_result") or {}
    ui_manifest = package.get("ui_evidence_manifest") or {}
    (output_dir / "behavior_test_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "behavior_test_plan.md").write_text(behavior_test_plan_to_markdown(plan), encoding="utf-8")
    (output_dir / "method_regression_result.json").write_text(json.dumps(method_result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "method_regression_result.md").write_text(method_regression_result_to_markdown(method_result), encoding="utf-8")
    (output_dir / "ui_evidence_manifest.json").write_text(json.dumps(ui_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "ui_evidence_manifest.md").write_text(ui_evidence_manifest_to_markdown(ui_manifest), encoding="utf-8")
    (output_dir / "playwright_screenshot_index.md").write_text(playwright_screenshot_index_to_markdown(ui_manifest), encoding="utf-8")


if __name__ == "__main__":
    main()
