from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from app import database
from app.code_evidence_artifacts import EvidenceArtifactRecord, EvidenceArtifactStore
from app.code_evidence_repository import CodeEvidenceRepository
from app.code_evidence_source import SourceEvidenceService
from app.repository_scope import RepositoryScope


class SourceEvidenceCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="his_harness_source_evidence_")
        root = Path(self.temp_dir.name)
        self.repo_path = root / "repo"
        self.repo_path.mkdir()
        self._git("init")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "Harness Test")
        (self.repo_path / "src").mkdir()
        (self.repo_path / "src" / "alpha.py").write_text("def alpha():\n    return '甲'\n", encoding="utf-8")
        (self.repo_path / "src" / "beta.py").write_text("from .alpha import alpha\n\ndef beta():\n    return alpha()\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "baseline")
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = root / "manager.sqlite"
        self.repository = CodeEvidenceRepository()
        self.store = EvidenceArtifactStore(root / "evidence")
        self.service = SourceEvidenceService(
            self.repository,
            self.store,
            {"repo-a": RepositoryScope("repo-a", self.repo_path, allowed_paths=("src",))},
        )

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def _git(self, *arguments: str) -> str:
        return subprocess.run(
            ["/usr/bin/git", "-C", str(self.repo_path), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout

    def _artifact_content(self, bundle_id: int, kind: str) -> list[bytes]:
        result: list[bytes] = []
        for item in self.repository.get_bundle(bundle_id)["artifacts"]:
            if item["kind"] != kind:
                continue
            result.append(self.store.reopen(EvidenceArtifactRecord(
                bundle_id=int(item["bundle_id"]), kind=str(item["kind"]),
                relative_path=str(item["relative_path"]), sha256=str(item["sha256"]),
                size_bytes=int(item["size_bytes"]), device=int(item["device"]),
                inode=int(item["inode"]), mode=int(item["mode"]), link_count=int(item["link_count"]),
            )))
        return result

    def test_read_exact_scoped_utf8_files_with_hash_line_and_sealed_artifacts(self) -> None:
        result = self.service.read(
            repository_alias="repo-a", paths=("src/alpha.py", "src/beta.py"),
            bundle_key="source-read-a", conversation_key="conversation-a", task_key="task-a",
        )

        self.assertEqual("sealed", result["status"])
        self.assertEqual(["src/alpha.py", "src/beta.py"], result["paths"])
        self.assertTrue(result["snapshot_consistent"])
        sources = self._artifact_content(int(result["bundle_id"]), "source")
        self.assertEqual(2, len(sources))
        self.assertIn("甲", sources[0].decode("utf-8"))
        manifest = json.loads(self._artifact_content(int(result["bundle_id"]), "source_manifest")[0])
        self.assertEqual([2, 4], [item["line_count"] for item in manifest["files"]])

    def test_search_returns_bounded_line_context_and_marks_complete(self) -> None:
        result = self.service.search(
            repository_alias="repo-a", pattern="alpha", path_prefix="src",
            bundle_key="source-search-a", conversation_key="conversation-a", task_key="task-a",
            max_matches=8,
        )

        self.assertEqual("sealed", result["status"])
        self.assertTrue(result["search_complete"])
        self.assertEqual(3, result["match_count"])
        matches = json.loads(self._artifact_content(int(result["bundle_id"]), "search_manifest")[0])["matches"]
        self.assertEqual([1, 1, 4], [item["line"] for item in matches])
        self.assertEqual({"src/alpha.py", "src/beta.py"}, {item["path"] for item in matches})

    def test_path_escape_symlink_binary_secret_and_search_overflow_fail_closed(self) -> None:
        for paths in (("../outside",), ("README.md",)):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                self.service.read(repository_alias="repo-a", paths=paths, bundle_key="bad-path", conversation_key="conversation-a", task_key="task-a")

        (self.repo_path / "src" / "secret.txt").write_text("Bearer " + "A9" * 24, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "code_evidence_sensitive"):
            self.service.read(repository_alias="repo-a", paths=("src/secret.txt",), bundle_key="bad-secret", conversation_key="conversation-a", task_key="task-a")
        (self.repo_path / "src" / "secret.txt").unlink()

        (self.repo_path / "src" / "binary.bin").write_bytes(b"\x00\xff")
        with self.assertRaisesRegex(ValueError, "code_evidence_source_binary"):
            self.service.read(repository_alias="repo-a", paths=("src/binary.bin",), bundle_key="bad-binary", conversation_key="conversation-a", task_key="task-a")
        (self.repo_path / "src" / "binary.bin").unlink()

        with self.assertRaisesRegex(ValueError, "code_evidence_search_incomplete"):
            self.service.search(repository_alias="repo-a", pattern="a", path_prefix="src", bundle_key="overflow", conversation_key="conversation-a", task_key="task-a", max_matches=1)


if __name__ == "__main__":
    unittest.main()
