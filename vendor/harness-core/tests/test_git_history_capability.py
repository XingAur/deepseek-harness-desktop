from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from app import database
from app.code_evidence_artifacts import EvidenceArtifactRecord, EvidenceArtifactStore
from app.code_evidence_history import GitHistoryEvidenceService
from app.code_evidence_repository import CodeEvidenceRepository
from app.repository_scope import RepositoryScope


class GitHistoryEvidenceCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="his_harness_history_evidence_")
        root = Path(self.temp_dir.name)
        self.repo_path = root / "repo"
        self.repo_path.mkdir()
        self._git("init")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "Harness Test")
        (self.repo_path / "src").mkdir()
        path = self.repo_path / "src" / "logic.py"
        path.write_text("VALUE = 1\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "initial logic")
        path.write_text("VALUE = 2\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "update logic")
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = root / "manager.sqlite"
        self.repository = CodeEvidenceRepository()
        self.store = EvidenceArtifactStore(root / "evidence")
        self.service = GitHistoryEvidenceService(
            self.repository,
            self.store,
            {"repo-a": RepositoryScope("repo-a", self.repo_path, allowed_paths=("src",))},
        )

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def _git(self, *arguments: str) -> str:
        return subprocess.run(["/usr/bin/git", "-C", str(self.repo_path), *arguments], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout

    def _artifact(self, bundle_id: int, kind: str) -> bytes:
        item = next(value for value in self.repository.get_bundle(bundle_id)["artifacts"] if value["kind"] == kind)
        return self.store.reopen(EvidenceArtifactRecord(
            bundle_id=int(item["bundle_id"]), kind=str(item["kind"]), relative_path=str(item["relative_path"]),
            sha256=str(item["sha256"]), size_bytes=int(item["size_bytes"]), device=int(item["device"]),
            inode=int(item["inode"]), mode=int(item["mode"]), link_count=int(item["link_count"]),
        ))

    def test_head_log_show_and_blame_are_sealed_and_hash_bound(self) -> None:
        result = self.service.capture(
            repository_alias="repo-a", path="src/logic.py", limit=2,
            bundle_key="history-a", conversation_key="conversation-a", task_key="task-a",
        )

        self.assertEqual("sealed", result["status"])
        self.assertEqual(2, result["commit_count"])
        self.assertTrue(result["snapshot_consistent"])
        history = json.loads(self._artifact(int(result["bundle_id"]), "history"))
        self.assertEqual(["update logic", "initial logic"], [item["subject"] for item in history["commits"]])
        self.assertEqual("VALUE = 2", history["blame"][0]["content"])
        self.assertIn(b"-VALUE = 1", self._artifact(int(result["bundle_id"]), "diff_patch"))
        self.assertIn(b"+VALUE = 2", self._artifact(int(result["bundle_id"]), "diff_patch"))

    def test_invalid_ref_expression_escape_secret_and_unsafe_git_config_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            self.service.capture(repository_alias="repo-a", path="../logic.py", limit=2, bundle_key="history-bad", conversation_key="conversation-a", task_key="task-a")
        with self.assertRaises(ValueError):
            self.service.capture(repository_alias="repo-a", path="src/logic.py", limit=2, ref="HEAD~1..HEAD", bundle_key="history-ref", conversation_key="conversation-a", task_key="task-a")

        (self.repo_path / "src" / "logic.py").write_text("Bearer " + "A9" * 24, encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "unsafe content")
        with self.assertRaisesRegex(ValueError, "code_evidence_sensitive"):
            self.service.capture(repository_alias="repo-a", path="src/logic.py", limit=2, bundle_key="history-secret", conversation_key="conversation-a", task_key="task-a")

        self._git("config", "diff.hostile.textconv", "/usr/bin/false")
        with self.assertRaises(ValueError):
            self.service.capture(repository_alias="repo-a", path="src/logic.py", limit=2, bundle_key="history-config", conversation_key="conversation-a", task_key="task-a")


if __name__ == "__main__":
    unittest.main()
