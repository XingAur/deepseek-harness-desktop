from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from app import database
from app.task_manager import (
    TaskChangeRecordOptions,
    TaskCreateOptions,
    TaskManager,
    TaskRollbackApplyOptions,
)


class TaskManagerRollbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = self.root / "harness.sqlite"
        self.manager = TaskManager()
        self.repository = self.create_repository(self.root / "repo")
        self.patch = """diff --git a/src/target.js b/src/target.js
--- a/src/target.js
+++ b/src/target.js
@@ -1 +1 @@
-export const target = true
+export const target = false
"""
        applied = subprocess.run(
            ["git", "apply", "-"],
            cwd=self.repository,
            input=self.patch,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, applied.returncode, applied.stderr)
        self.task = self.manager.create_task(
            TaskCreateOptions(
                title="DFHIS-LOCAL-ROLLBACK",
                entity_kind="requirement",
                entity_id="DFHIS-LOCAL-ROLLBACK",
                project_paths=[str(self.repository)],
            )
        )
        self.change = self.manager.record_change(
            TaskChangeRecordOptions(
                task_id=int(self.task["id"]),
                project_path=str(self.repository),
                allowed_paths=["src/target.js"],
                diff_text=self.patch,
                verification_status="passed",
            )
        )

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def create_repository(self, path: Path) -> Path:
        path.mkdir(parents=True)
        subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "harness@example.test"], cwd=path, check=True)
        subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=path, check=True)
        target = path / "src/target.js"
        target.parent.mkdir(parents=True)
        target.write_text("export const target = true\n", encoding="utf-8")
        (path / "src/unrelated.js").write_text("export const unrelated = true\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=path, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True, text=True)
        return path.resolve()

    def test_actual_rollback_requires_exact_confirmation_and_preserves_unrelated_change(self) -> None:
        unrelated = self.repository / "src/unrelated.js"
        unrelated.write_text("export const unrelated = false\n", encoding="utf-8")
        options = TaskRollbackApplyOptions(
            task_id=int(self.task["id"]),
            change_id=self.change["change_id"],
            confirmation="",
            verify_commands=[
                "python3 -c \"from pathlib import Path; assert 'target = true' in Path('src/target.js').read_text()\""
            ],
        )

        with self.assertRaisesRegex(PermissionError, "ROLLBACK"):
            self.manager.apply_change_rollback(options)
        self.assertEqual("export const target = false\n", (self.repository / "src/target.js").read_text(encoding="utf-8"))

        options.confirmation = f"ROLLBACK:{self.change['change_id']}"
        result = self.manager.apply_change_rollback(options)
        repeated = self.manager.apply_change_rollback(options)

        self.assertEqual("success", result["status"])
        self.assertFalse(result["idempotent"])
        self.assertEqual("export const target = true\n", (self.repository / "src/target.js").read_text(encoding="utf-8"))
        self.assertEqual("export const unrelated = false\n", unrelated.read_text(encoding="utf-8"))
        self.assertEqual("success", repeated["status"])
        self.assertTrue(repeated["idempotent"])
        recorded = database.get_task_change_by_change_id(self.change["change_id"])
        self.assertEqual("completed", recorded["rollback_status"])

    def test_failed_rollback_verification_restores_pre_rollback_change(self) -> None:
        result = self.manager.apply_change_rollback(
            TaskRollbackApplyOptions(
                task_id=int(self.task["id"]),
                change_id=self.change["change_id"],
                confirmation=f"ROLLBACK:{self.change['change_id']}",
                verify_commands=["python3 -c \"raise SystemExit(7)\""],
            )
        )

        self.assertEqual("verification_failed_restored", result["status"])
        self.assertEqual("success", result["recovery"]["status"])
        self.assertEqual(7, result["verification"][0]["returncode"])
        self.assertEqual("export const target = false\n", (self.repository / "src/target.js").read_text(encoding="utf-8"))
        self.assertTrue(Path(result["transaction"]["journal_path"]).is_file())
        self.assertTrue(Path(result["transaction"]["patch_path"]).is_file())

    def test_actual_rollback_blocks_when_target_changed_after_recording(self) -> None:
        (self.repository / "src/target.js").write_text(
            "export const target = false\nexport const userEdit = true\n",
            encoding="utf-8",
        )

        result = self.manager.apply_change_rollback(
            TaskRollbackApplyOptions(
                task_id=int(self.task["id"]),
                change_id=self.change["change_id"],
                confirmation=f"ROLLBACK:{self.change['change_id']}",
            )
        )

        self.assertEqual("blocked_target_drift", result["status"])
        self.assertIn("userEdit", (self.repository / "src/target.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
