from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app import database


ROOT = Path(__file__).resolve().parents[1]


class RetentionGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = self.root / "harness.sqlite"
        database.init_db()
        self.as_of = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def create_run(self, *, title: str, started_at: str, status: str = "success") -> int:
        run_id = database.create_run(
            team_key="his_requirement_workflow",
            title=title,
            source_type="test-retention",
            demand_text="fixture",
            total_steps=0,
            llm_mode="mock",
        )
        database.update_run(run_id, status=status, started_at=started_at, finished_at=started_at)
        database.add_artifact(run_id, "fixture", title, "x" * 100)
        return run_id

    def seed_runs(self) -> dict[str, int]:
        old_candidate_a = self.create_run(title="old-a", started_at="2026-05-01T00:00:00+00:00")
        old_candidate_b = self.create_run(title="old-b", started_at="2026-05-02T00:00:00+00:00")
        protected_task = self.create_run(title="task-linked", started_at="2026-05-03T00:00:00+00:00")
        protected_audit = self.create_run(title="audit-linked", started_at="2026-05-04T00:00:00+00:00")
        protected_running = self.create_run(
            title="still-running",
            started_at="2026-05-05T00:00:00+00:00",
            status="running",
        )
        recent_a = self.create_run(title="recent-a", started_at="2026-07-15T00:00:00+00:00")
        recent_b = self.create_run(title="recent-b", started_at="2026-07-16T00:00:00+00:00")
        task_id = database.upsert_task(
            {
                "task_key": "RETENTION-PROTECTED",
                "entity_kind": "requirement",
                "entity_id": "DFHIS-RETENTION",
                "entity_title": "protected fixture",
                "source_type": "test",
            }
        )
        database.add_task_run({"task_id": task_id, "run_id": protected_task, "status": "success"})
        with database.connect() as conn:
            conn.execute(
                """
                insert into yunxiao_audit_events(
                    run_id, project_key, entity_kind, entity_id, action, status, decision,
                    idempotency_key, actor, created_at
                ) values(?, 'fixture', 'requirement', 'DFHIS-AUDIT', 'read', 'success', 'allow', ?, 'test', ?)
                """,
                (protected_audit, f"retention-audit-{protected_audit}", database.now_iso()),
            )
        return {
            "old_candidate_a": old_candidate_a,
            "old_candidate_b": old_candidate_b,
            "protected_task": protected_task,
            "protected_audit": protected_audit,
            "protected_running": protected_running,
            "recent_a": recent_a,
            "recent_b": recent_b,
        }

    def test_preview_uses_union_retention_and_protects_referenced_runs(self) -> None:
        ids = self.seed_runs()

        plan = database.build_retention_plan(
            keep_days=7,
            keep_recent_runs=2,
            as_of=self.as_of,
        )

        self.assertEqual([ids["old_candidate_a"], ids["old_candidate_b"]], plan["candidate_run_ids"])
        self.assertEqual(2, plan["candidate_count"])
        self.assertEqual(0, plan["will_modify_files"])
        self.assertEqual(f"PRUNE:{plan['plan_hash']}", plan["required_confirmation"])
        self.assertGreaterEqual(plan["protected_counts"]["task_or_change"], 1)
        self.assertGreaterEqual(plan["protected_counts"]["audit"], 1)
        self.assertGreaterEqual(plan["protected_counts"]["running"], 1)

    def test_exact_confirmation_prunes_transactionally_and_backup_restores(self) -> None:
        ids = self.seed_runs()
        plan = database.build_retention_plan(keep_days=7, keep_recent_runs=2, as_of=self.as_of)

        with self.assertRaisesRegex(PermissionError, "PRUNE"):
            database.apply_retention_plan(plan, confirmation="")
        self.assertIsNotNone(database.get_run(ids["old_candidate_a"]))

        result = database.apply_retention_plan(
            plan,
            confirmation=f"PRUNE:{plan['plan_hash']}",
        )

        self.assertEqual("success", result["status"])
        self.assertEqual(2, result["deleted_run_count"])
        self.assertIsNone(database.get_run(ids["old_candidate_a"]))
        self.assertIsNone(database.get_run(ids["old_candidate_b"]))
        for key in ("protected_task", "protected_audit", "protected_running", "recent_a", "recent_b"):
            self.assertIsNotNone(database.get_run(ids[key]))
        self.assertEqual("ok", database.database_health_snapshot()["integrity_check"])

        backup = result["archive_backup"]
        database.restore_database_backup(
            backup_path=Path(backup["backup_path"]),
            confirmation=f"RESTORE:{backup['sha256']}",
        )
        self.assertIsNotNone(database.get_run(ids["old_candidate_a"]))
        self.assertIsNotNone(database.get_run(ids["old_candidate_b"]))

    def test_plan_drift_rejects_without_deleting_any_run(self) -> None:
        ids = self.seed_runs()
        plan = database.build_retention_plan(keep_days=7, keep_recent_runs=2, as_of=self.as_of)
        drifted = self.create_run(title="drifted-old", started_at="2026-04-01T00:00:00+00:00")

        with self.assertRaisesRegex(RuntimeError, "retention plan drift"):
            database.apply_retention_plan(plan, confirmation=f"PRUNE:{plan['plan_hash']}")

        self.assertIsNotNone(database.get_run(ids["old_candidate_a"]))
        self.assertIsNotNone(database.get_run(drifted))

    def test_cli_preview_never_modifies_database(self) -> None:
        ids = self.seed_runs()
        output = self.root / "retention-plan.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "database_admin.py"),
                "retention-preview",
                "--database",
                str(database.DB_PATH),
                "--keep-days",
                "7",
                "--keep-recent-runs",
                "2",
                "--as-of",
                self.as_of.isoformat(),
                "--output",
                str(output),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertFalse(payload["will_modify_files"])
        self.assertIsNotNone(database.get_run(ids["old_candidate_a"]))


if __name__ == "__main__":
    unittest.main()
