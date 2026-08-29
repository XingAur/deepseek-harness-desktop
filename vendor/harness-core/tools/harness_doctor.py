from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.harness_doctor import format_doctor_report, run_harness_doctor
from app.runtime_bootstrap import reexec_in_project_venv


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only HIS Harness environment doctor")
    parser.add_argument("--database-path", default="")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--worktree-root", default="")
    parser.add_argument("--plugin-root", action="append", default=[])
    parser.add_argument("--repository", action="append", default=[])
    parser.add_argument("--database-profile", default="")
    parser.add_argument("--database-policy", default="")
    parser.add_argument("--credentials-file", default="")
    parser.add_argument("--require-database", action="store_true")
    parser.add_argument("--require-git", action="store_true")
    parser.add_argument("--mutation", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = run_harness_doctor(
        database_path=args.database_path or None,
        output_dir=args.output_dir or None,
        worktree_root=args.worktree_root or None,
        plugin_roots=args.plugin_root,
        repository_paths=args.repository,
        database_profile=args.database_profile,
        database_policy_path=args.database_policy or None,
        credentials_file=args.credentials_file or None,
        environment=os.environ,
        require_git=args.require_git,
        mutation_requested=args.mutation,
        require_database=args.require_database,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else format_doctor_report(report))
    if args.strict and report["status"] != "ready":
        return 1
    return 0


if __name__ == "__main__":
    reexec_in_project_venv(PROJECT_ROOT)
    raise SystemExit(main())
