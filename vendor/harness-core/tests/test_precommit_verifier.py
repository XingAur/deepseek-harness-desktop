from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.precommit_verifier import remove_untracked_file_additions


class PrecommitVerifierTests(unittest.TestCase):
    def test_removes_only_named_untracked_file_additions_from_baseline_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp)
            added_file = worktree / "src" / "new-file.js"
            added_file.parent.mkdir(parents=True)
            added_file.write_text("module.exports = true\n", encoding="utf-8")
            retained_file = worktree / "src" / "retained.js"
            retained_file.write_text("module.exports = false\n", encoding="utf-8")

            result = remove_untracked_file_additions(
                worktree_path=worktree,
                untracked_paths=["src/new-file.js"],
            )

            self.assertEqual("success", result["status"])
            self.assertEqual(["src/new-file.js"], result["removed_paths"])
            self.assertFalse(added_file.exists())
            self.assertTrue(retained_file.exists())

    def test_rejects_path_outside_baseline_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp)

            result = remove_untracked_file_additions(
                worktree_path=worktree,
                untracked_paths=["../outside.js"],
            )

            self.assertEqual("failed", result["status"])
            self.assertTrue(result["errors"])


if __name__ == "__main__":
    unittest.main()
