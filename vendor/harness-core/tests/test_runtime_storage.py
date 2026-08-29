from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from app import database
from app.runtime_storage import ephemeral_runtime_storage


class EphemeralRuntimeStorageTests(unittest.TestCase):
    def test_uses_temporary_database_and_removes_it_after_completion(self) -> None:
        original_db_path = database.DB_PATH

        with ephemeral_runtime_storage(prefix="test") as runtime:
            self.assertEqual(runtime.root / "harness.sqlite", database.DB_PATH)
            database.init_db()
            self.assertTrue(database.DB_PATH.is_file())
            runtime_root = runtime.root

        self.assertEqual(original_db_path, database.DB_PATH)
        self.assertFalse(runtime_root.exists())

    def test_persistent_mode_keeps_requested_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            output_dir = Path(temporary_root) / "retained"
            runtime = ephemeral_runtime_storage(
                prefix="ignored", retain_output=True, output_dir=output_dir
            )

            with runtime as active_runtime:
                self.assertEqual(output_dir, active_runtime.output_dir)
                self.assertEqual(output_dir / "harness.sqlite", active_runtime.database_path)
                self.assertEqual(database.DB_PATH, active_runtime.database_path)
                database.init_db()
                self.assertTrue(active_runtime.database_path.is_file())

            self.assertEqual(database.DB_PATH, active_runtime.original_database_path)
