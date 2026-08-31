from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import database


def main() -> int:
    parser = argparse.ArgumentParser(description="HIS Harness v0.59 local SQLite administration.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="read SQLite health without changing data")
    status.add_argument("--database", default=str(database.DB_PATH))

    backup = subparsers.add_parser("backup", help="create and integrity-check a local SQLite backup")
    backup.add_argument("--database", default=str(database.DB_PATH))
    backup.add_argument("--output-dir", default="")
    backup.add_argument("--reason", default="manual")

    restore = subparsers.add_parser("restore", help="restore a backup only with exact SHA-256 confirmation")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--database", default=str(database.DB_PATH))
    restore.add_argument("--confirm", default="")

    retention_preview = subparsers.add_parser("retention-preview", help="preview protected local run retention")
    retention_preview.add_argument("--database", default=str(database.DB_PATH))
    retention_preview.add_argument("--keep-days", type=int, default=30)
    retention_preview.add_argument("--keep-recent-runs", type=int, default=100)
    retention_preview.add_argument("--as-of", default="")
    retention_preview.add_argument("--output", default="")

    retention_apply = subparsers.add_parser("retention-apply", help="archive and prune an exact retention plan")
    retention_apply.add_argument("--plan", required=True)
    retention_apply.add_argument("--confirm", default="")

    args = parser.parse_args()
    if args.command == "status":
        payload = database.database_health_snapshot(Path(args.database))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("status") == "healthy" else 2
    if args.command == "backup":
        payload = database.backup_database(
            reason=args.reason,
            source_path=Path(args.database),
            destination_dir=Path(args.output_dir) if args.output_dir else None,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.command == "retention-preview":
        from datetime import datetime

        payload = database.build_retention_plan(
            keep_days=args.keep_days,
            keep_recent_runs=args.keep_recent_runs,
            as_of=datetime.fromisoformat(args.as_of) if args.as_of else None,
            database_path=Path(args.database),
        )
        rendered = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.output:
            database.atomic_write_database_text(Path(args.output).expanduser().resolve(), rendered + "\n")
        print(rendered)
        return 0
    if args.command == "retention-apply":
        plan_path = Path(args.plan).expanduser().resolve()
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
        expected = f"PRUNE:{payload.get('plan_hash') or ''}"
        if args.confirm != expected:
            print(
                json.dumps(
                    {
                        "status": "confirmation_required",
                        "plan_path": str(plan_path),
                        "required_confirmation": expected,
                        "will_modify_files": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        result = database.apply_retention_plan(payload, confirmation=args.confirm)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    backup_path = Path(args.backup).expanduser().resolve()
    if not backup_path.is_file():
        raise FileNotFoundError(f"Harness database backup does not exist: {backup_path}")
    sha256 = database.sha256_file(backup_path)
    expected = f"RESTORE:{sha256}"
    if args.confirm != expected:
        print(
            json.dumps(
                {
                    "status": "confirmation_required",
                    "backup_path": str(backup_path),
                    "target_path": str(Path(args.database).expanduser().resolve()),
                    "required_confirmation": expected,
                    "will_modify_files": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    payload = database.restore_database_backup(
        backup_path=backup_path,
        target_path=Path(args.database),
        confirmation=args.confirm,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
