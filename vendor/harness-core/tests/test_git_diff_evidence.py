from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from app import database
from app.code_evidence_artifacts import EvidenceArtifactRecord, EvidenceArtifactStore
from app.code_evidence_git import GitDiffEvidenceService
from app.code_evidence_repository import CodeEvidenceRepository
from app.repository_scope import RepositoryScope


class GitDiffEvidenceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="his_harness_git_diff_")
        root = Path(self.temp_dir.name)
        self.repo_path = root / "repo"
        self.repo_path.mkdir()
        self._git("init")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "Harness Test")
        (self.repo_path / "modify.txt").write_text("before\n", encoding="utf-8")
        (self.repo_path / "delete.txt").write_text("delete\n", encoding="utf-8")
        (self.repo_path / "rename.txt").write_text("rename\n", encoding="utf-8")
        (self.repo_path / "mode.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "baseline")
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = root / "manager.sqlite"
        self.repository = CodeEvidenceRepository()
        self.store = EvidenceArtifactStore(root / "evidence")
        self.scope = RepositoryScope("repo-a", self.repo_path)
        self.service = GitDiffEvidenceService(self.repository, self.store, {"repo-a": self.scope})

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def _git(self, *arguments: str) -> str:
        completed = subprocess.run(
            ["/usr/bin/git", "-C", str(self.repo_path), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return completed.stdout

    def _capture(self, suffix: str = "a") -> dict[str, object]:
        return self.service.capture(
            repository_alias="repo-a",
            bundle_key=f"diff-{suffix}",
            conversation_key="conversation-a",
            task_key="task-a",
        )

    def test_full_binary_diff_captures_tracked_staged_untracked_delete_rename_and_mode(self) -> None:
        (self.repo_path / "modify.txt").write_text("after\n", encoding="utf-8")
        (self.repo_path / "staged.txt").write_text("staged\n", encoding="utf-8")
        self._git("add", "staged.txt")
        (self.repo_path / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        (self.repo_path / "delete.txt").unlink()
        self._git("mv", "rename.txt", "renamed.txt")
        os.chmod(self.repo_path / "mode.sh", 0o755)
        (self.repo_path / "binary.bin").write_bytes(b"\x00\x01binary\xff")

        result = self._capture()

        self.assertEqual("sealed", result["status"])
        self.assertEqual("repo-a", result["repository_alias"])
        self.assertTrue(result["snapshot_consistent"])
        self.assertTrue(result["diff_complete"])
        self.assertEqual(0, result["diff_check_returncode"])
        self.assertEqual(
            {
                "binary.bin",
                "delete.txt",
                "mode.sh",
                "modify.txt",
                "rename.txt",
                "renamed.txt",
                "staged.txt",
                "untracked.txt",
            },
            set(result["changed_paths"]),
        )
        self.assertTrue({"added", "deleted", "modified", "renamed", "mode_changed", "binary"}.issubset(set(result["change_types"])))

        record = self.repository.get_bundle(int(result["bundle_id"]))
        self.assertEqual({"diff_manifest", "diff_patch", "bundle_seal"}, {item["kind"] for item in record["artifacts"]})
        artifact_records = {
            item["kind"]: EvidenceArtifactRecord(
                bundle_id=int(item["bundle_id"]),
                kind=str(item["kind"]),
                relative_path=str(item["relative_path"]),
                sha256=str(item["sha256"]),
                size_bytes=int(item["size_bytes"]),
                device=int(item["device"]),
                inode=int(item["inode"]),
                mode=int(item["mode"]),
                link_count=int(item["link_count"]),
            )
            for item in record["artifacts"]
        }
        patch = self.store.reopen(artifact_records["diff_patch"])
        manifest = json.loads(self.store.reopen(artifact_records["diff_manifest"]))
        self.assertIn(b"diff --git", patch)
        self.assertIn(b"GIT binary patch", patch)
        self.assertEqual(sorted(result["changed_paths"]), [item["path"] for item in manifest["files"]])
        by_path = {item["path"]: item for item in manifest["files"]}
        self.assertEqual((1, 1), (by_path["modify.txt"]["additions"], by_path["modify.txt"]["deletions"]))
        self.assertEqual((1, 0), (by_path["untracked.txt"]["additions"], by_path["untracked.txt"]["deletions"]))
        self.assertIsNone(by_path["binary.bin"]["additions"])
        self.assertIsNone(by_path["binary.bin"]["deletions"])
        self.assertEqual(result["patch_sha256"], artifact_records["diff_patch"].sha256)

        after = self._git("status", "--porcelain=v2", "--untracked-files=all")
        self.assertIn("modify.txt", after)
        self.assertEqual(1, int(self._git("rev-list", "--count", "HEAD").strip()))

    def test_sensitive_changed_path_and_sensitive_content_fail_before_bundle_creation(self) -> None:
        (self.repo_path / ".env").write_text("API_TOKEN=do-not-store\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "code_evidence_sensitive"):
            self._capture("path")
        with database.connect() as connection:
            self.assertEqual(0, int(connection.execute("select count(*) from code_evidence_bundles").fetchone()[0]))

        (self.repo_path / ".env").unlink()
        (self.repo_path / "modify.txt").write_text("Bearer " + "A9" * 24, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "code_evidence_sensitive"):
            self._capture("content")

    def test_hooks_filters_attributes_and_external_diff_are_blocked_without_execution(self) -> None:
        marker = Path(self.temp_dir.name) / "executed"
        self._git("config", "diff.hostile.textconv", f"/usr/bin/touch {marker}")
        (self.repo_path / ".gitattributes").write_text("*.txt diff=hostile\n", encoding="utf-8")
        (self.repo_path / "modify.txt").write_text("after\n", encoding="utf-8")

        with self.assertRaises(ValueError):
            self._capture("unsafe")
        self.assertFalse(marker.exists())

    def test_repository_change_after_snapshot_blocks_seal_and_preserves_source(self) -> None:
        (self.repo_path / "modify.txt").write_text("after\n", encoding="utf-8")
        original = self.service._capture_snapshot

        def mutate(scope: RepositoryScope):
            captured = original(scope)
            (self.repo_path / "late.txt").write_text("late\n", encoding="utf-8")
            return captured

        self.service._capture_snapshot = mutate  # type: ignore[method-assign]
        with self.assertRaisesRegex(ValueError, "code_evidence_repository_changed"):
            self._capture("race")
        self.assertEqual("late\n", (self.repo_path / "late.txt").read_text(encoding="utf-8"))
        with database.connect() as connection:
            statuses = [str(row[0]) for row in connection.execute("select status from code_evidence_bundles")]
        self.assertNotIn("sealed", statuses)


if __name__ == "__main__":
    unittest.main()
