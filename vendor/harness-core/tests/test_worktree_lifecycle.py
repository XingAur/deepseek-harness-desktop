from __future__ import annotations

import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from app.worktree_lifecycle import create_worktree_marker, inspect_worktree_root
from app.fullstack_executor import create_fullstack_worktree
from app.review_executor import ReviewExecutionOptions, ReviewWorktreeExecutor
from tools.cleanup_worktrees import cleanup_worktree_root


class WorktreeLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="his_harness_worktree_lifecycle_")
        self.base = Path(self.temp_dir.name)
        self.project = self.base / "project"
        self.root = Path("/tmp") / f"his_harness_worktree_lifecycle_{time.time_ns()}"
        self.project.mkdir()
        self.root.mkdir()
        subprocess.run(["git", "init"], cwd=self.project, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "harness@example.test"], cwd=self.project, check=True)
        subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=self.project, check=True)
        (self.project / "tracked.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.project, check=True, capture_output=True, text=True)

    def tearDown(self) -> None:
        subprocess.run(["git", "worktree", "prune"], cwd=self.project, check=False, capture_output=True, text=True)
        if self.root.exists():
            for child in list(self.root.iterdir()):
                if child.name.startswith(("run_", "precommit_")):
                    subprocess.run(
                        ["git", "worktree", "remove", "--force", str(child)],
                        cwd=self.project,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
            import shutil

            shutil.rmtree(self.root, ignore_errors=True)
        self.temp_dir.cleanup()

    def add_marked_worktree(self, *, name: str = "run_101", age_hours: int = 48) -> Path:
        path = self.root / name
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(path), "HEAD"],
            cwd=self.project,
            check=True,
            capture_output=True,
            text=True,
        )
        create_worktree_marker(
            worktree_root=self.root,
            worktree_path=path,
            project_path=self.project,
            run_id="101",
            role="patch",
            created_at_epoch=time.time() - age_hours * 3600,
        )
        return path

    def test_default_cleanup_is_preview_and_requires_exact_confirmation(self) -> None:
        path = self.add_marked_worktree()

        preview = cleanup_worktree_root(root=self.root, project_paths=[self.project])
        self.assertEqual("preview", preview["status"])
        self.assertTrue(path.exists())
        self.assertEqual(1, len(preview["candidates"]))

        rejected = cleanup_worktree_root(
            root=self.root,
            project_paths=[self.project],
            apply=True,
            confirm="CLEANUP:wrong",
        )
        self.assertEqual("confirmation_required", rejected["status"])
        self.assertTrue(path.exists())

        applied = cleanup_worktree_root(
            root=self.root,
            project_paths=[self.project],
            apply=True,
            confirm=preview["required_confirmation"],
        )
        self.assertEqual("success", applied["status"])
        self.assertFalse(path.exists())

    def test_unowned_recent_and_dirty_worktrees_are_never_candidates(self) -> None:
        unowned = self.root / "run_unowned"
        unowned.mkdir()
        recent = self.add_marked_worktree(name="run_recent", age_hours=1)
        dirty = self.add_marked_worktree(name="run_dirty", age_hours=48)
        (dirty / "tracked.txt").write_text("dirty\n", encoding="utf-8")

        inspection = inspect_worktree_root(
            worktree_root=self.root,
            project_paths=[self.project],
            max_age_hours=24,
        )

        self.assertEqual([], inspection["candidates"])
        statuses = {Path(item["path"]).name: item["status"] for item in inspection["skipped"]}
        self.assertEqual("unowned", statuses[unowned.name])
        self.assertEqual("active_recent", statuses[recent.name])
        self.assertEqual("dirty_blocked", statuses[dirty.name])

    def test_marker_does_not_dirty_the_git_worktree(self) -> None:
        path = self.add_marked_worktree()

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
        marker_files = list((self.root / ".harness_worktree_markers").glob("*.json"))

        self.assertEqual("", status.stdout)
        self.assertEqual(1, len(marker_files))
        marker = json.loads(marker_files[0].read_text(encoding="utf-8"))
        self.assertEqual(str(path.resolve()), marker["worktree_path"])

    def test_precommit_worktree_prefix_is_owned_when_marked(self) -> None:
        path = self.add_marked_worktree(name="precommit_303_frontend", age_hours=48)

        inspection = inspect_worktree_root(
            worktree_root=self.root,
            project_paths=[self.project],
            max_age_hours=24,
        )

        self.assertEqual([str(path.resolve())], [item["path"] for item in inspection["candidates"]])

    def test_existing_unowned_run_directory_is_not_overwritten(self) -> None:
        path = self.root / "run_existing"
        path.mkdir()
        sentinel = path / "keep.txt"
        sentinel.write_text("keep\n", encoding="utf-8")

        error = create_fullstack_worktree(
            project_path=self.project,
            worktree_root=self.root,
            worktree_path=path,
        )

        self.assertIn("拒绝覆盖", error)
        self.assertEqual("keep\n", sentinel.read_text(encoding="utf-8"))

    def test_stale_marker_without_directory_uses_same_exact_confirmation(self) -> None:
        path = self.root / "run_marker_only"
        create_worktree_marker(
            worktree_root=self.root,
            worktree_path=path,
            project_path=self.project,
            run_id="marker-only",
            role="patch",
            created_at_epoch=time.time() - 48 * 3600,
        )

        preview = cleanup_worktree_root(root=self.root, project_paths=[self.project])
        self.assertEqual("remove_marker", preview["candidates"][0]["action"])
        applied = cleanup_worktree_root(
            root=self.root,
            project_paths=[self.project],
            apply=True,
            confirm=preview["required_confirmation"],
        )

        self.assertEqual("success", applied["status"])
        self.assertEqual([], list((self.root / ".harness_worktree_markers").glob("*.json")))

    def test_review_executor_cleans_both_owned_worktrees_and_markers(self) -> None:
        (self.project / "tracked.txt").write_text("head\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-m", "change"], cwd=self.project, check=True, capture_output=True, text=True)

        result = ReviewWorktreeExecutor().execute(
            ReviewExecutionOptions(
                project_path=str(self.project),
                run_id=202,
                review_commit="HEAD",
                review_base="HEAD^",
                worktree_root=str(self.root),
                allowed_paths=["tracked.txt"],
            )
        )

        self.assertEqual("success", result.status)
        self.assertTrue((self.root / "run_202_base").exists())
        self.assertTrue((self.root / "run_202_head").exists())
        self.assertEqual("manual_cleanup_required", result.manifest["cleanup"]["base"]["status"])
        self.assertEqual("manual_cleanup_required", result.manifest["cleanup"]["head"]["status"])


if __name__ == "__main__":
    unittest.main()
