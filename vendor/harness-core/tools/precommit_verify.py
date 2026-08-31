from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.precommit_verifier import PrecommitVerificationOptions, PrecommitVerifier
from app.technical_decision import DEFAULT_PROJECT_ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description="Run HIS Harness v0.9.1 precommit verification for current BFF/frontend local diffs.")
    parser.add_argument("--project-root", default=DEFAULT_PROJECT_ROOT, help="root directory containing HIS business repos")
    parser.add_argument("--project-path", default="", help="specific Git repo path for generic current-diff verification")
    parser.add_argument("--allowed-path", action="append", default=[], help="relative changed path allowed for generic current-diff verification; repeatable")
    parser.add_argument("--verify-command", action="append", default=[], help="verification command to run in temporary worktree; repeatable")
    parser.add_argument("--target-key", default="", help="generic verification target key")
    parser.add_argument("--target-name", default="", help="generic verification target display name")
    parser.add_argument("--target-role", default="frontend", help="generic verification target role")
    parser.add_argument("--title", default="", help="demand title used in generic reports")
    parser.add_argument("--entity-id", default="", help="demand or bug id used in generic reports")
    parser.add_argument("--demand-text", default="", help="demand text used for v0.10 behavior acceptance")
    parser.add_argument("--demand-file", default="", help="file containing demand text used for v0.10 behavior acceptance")
    parser.add_argument("--method-evidence-file", default="", help="JSON file with v0.10.2 method-level interaction results")
    parser.add_argument("--method-test-command", action="append", default=[], help="command run in temporary worktree to emit v0.10.3A method evidence JSON; repeatable")
    parser.add_argument("--ui-evidence-path", action="append", default=[], help="screenshot/video/GIF/manual evidence file path for v0.10.2; repeatable")
    parser.add_argument("--ui-capture-command", action="append", default=[], help="command run in temporary worktree to emit v0.10.3B UI evidence JSON and files; repeatable")
    parser.add_argument("--worktree-dir", default="/tmp/his_harness_precommit_worktrees", help="temporary worktree root")
    parser.add_argument("--output-dir", default="/tmp/his_harness_precommit_verify", help="output directory")
    parser.add_argument("--run-id", type=int, default=9000, help="logical run id for artifact naming")
    args = parser.parse_args()
    demand_text = args.demand_text
    if args.demand_file:
        demand_text = Path(args.demand_file).expanduser().read_text(encoding="utf-8")
    method_evidence = {}
    if args.method_evidence_file:
        method_evidence = json.loads(Path(args.method_evidence_file).expanduser().read_text(encoding="utf-8"))

    result = PrecommitVerifier().execute(
        PrecommitVerificationOptions(
            run_id=args.run_id,
            project_root=args.project_root,
            project_path=args.project_path,
            allowed_paths=args.allowed_path,
            verify_commands=args.verify_command,
            target_key=args.target_key,
            target_name=args.target_name,
            target_role=args.target_role,
            title=args.title,
            entity_id=args.entity_id,
            demand_text=demand_text,
            method_evidence=method_evidence,
            method_test_commands=args.method_test_command,
            ui_evidence_paths=args.ui_evidence_path,
            ui_capture_commands=args.ui_capture_command,
            worktree_root=args.worktree_dir,
        )
    )

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "precommit_manifest.json").write_text(result.to_json(), encoding="utf-8")
    (output_dir / "verification_matrix.json").write_text(result.matrix_json(), encoding="utf-8")
    (output_dir / "verification_matrix.md").write_text(result.matrix_markdown(), encoding="utf-8")
    (output_dir / "behavior_acceptance.json").write_text(result.behavior_json(), encoding="utf-8")
    (output_dir / "behavior_acceptance.md").write_text(result.behavior_markdown(), encoding="utf-8")
    (output_dir / "method_test_runner.json").write_text(result.method_test_runner_json(), encoding="utf-8")
    (output_dir / "method_test_runner.md").write_text(result.method_test_runner_markdown(), encoding="utf-8")
    (output_dir / "ui_evidence_runner.json").write_text(result.ui_evidence_runner_json(), encoding="utf-8")
    (output_dir / "ui_evidence_runner.md").write_text(result.ui_evidence_runner_markdown(), encoding="utf-8")
    (output_dir / "interaction_evidence.json").write_text(result.interaction_json(), encoding="utf-8")
    (output_dir / "interaction_evidence.md").write_text(result.interaction_markdown(), encoding="utf-8")
    (output_dir / "behavior_test_plan.json").write_text(result.behavior_test_plan_json(), encoding="utf-8")
    (output_dir / "behavior_test_plan.md").write_text(result.behavior_test_plan_markdown(), encoding="utf-8")
    (output_dir / "method_regression_result.json").write_text(result.method_regression_json(), encoding="utf-8")
    (output_dir / "method_regression_result.md").write_text(result.method_regression_markdown(), encoding="utf-8")
    (output_dir / "ui_evidence_manifest.json").write_text(result.ui_evidence_json(), encoding="utf-8")
    (output_dir / "ui_evidence_manifest.md").write_text(result.ui_evidence_markdown(), encoding="utf-8")
    (output_dir / "playwright_screenshot_index.md").write_text(result.playwright_screenshot_index_markdown(), encoding="utf-8")
    (output_dir / "code_review.md").write_text(result.code_review_markdown(), encoding="utf-8")
    (output_dir / "commit_ready_summary.md").write_text(result.commit_ready_markdown(), encoding="utf-8")

    print(result.matrix_markdown())
    print()
    print(result.commit_ready_markdown())
    print()
    print(f"Status: {result.status}")
    print(f"Output: {output_dir}")
    if result.status != "success":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
