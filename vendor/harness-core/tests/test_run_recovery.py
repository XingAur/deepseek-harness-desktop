from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import database


class StaleRunRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "harness.sqlite"
        database.init_db()

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_only_old_running_runs_are_marked_interrupted_with_audit_artifact(self) -> None:
        now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
        old_run = database.create_run("team", "old", "manual", "old", 0)
        recent_run = database.create_run("team", "recent", "manual", "recent", 0)
        completed_run = database.create_run("team", "completed", "manual", "completed", 0)
        database.update_run(old_run, started_at=(now - timedelta(hours=25)).isoformat())
        database.update_run(recent_run, started_at=(now - timedelta(hours=2)).isoformat())
        database.update_run(
            completed_run,
            status="success",
            started_at=(now - timedelta(hours=30)).isoformat(),
            finished_at=(now - timedelta(hours=29)).isoformat(),
        )

        result = database.reconcile_stale_runs(max_age_hours=24, now=now)

        self.assertEqual([old_run], result["recovered_run_ids"])
        self.assertEqual("interrupted", database.get_run(old_run)["status"])
        self.assertEqual("interrupted_recovered", database.get_run(old_run)["evaluation_status"])
        self.assertEqual("running", database.get_run(recent_run)["status"])
        self.assertEqual("success", database.get_run(completed_run)["status"])
        artifacts = database.get_artifacts(old_run)
        self.assertEqual(["startup_recovery_json"], [item["kind"] for item in artifacts])


if __name__ == "__main__":
    unittest.main()
