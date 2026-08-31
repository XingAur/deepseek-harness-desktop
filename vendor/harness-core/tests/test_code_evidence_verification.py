from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app import database
from app.code_evidence_artifacts import EvidenceArtifactRecord, EvidenceArtifactStore
from app.code_evidence_git import GitDiffEvidenceService
from app.code_evidence_repository import CodeEvidenceRepository
from app.code_evidence_verification import CodeEvidenceVerificationService
from app.repository_scope import RepositoryScope


class CodeEvidenceVerificationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="his_harness_code_verify_")
        root = Path(self.temp_dir.name)
        self.repo_path = root / "repo"
        self.repo_path.mkdir()
        self._git("init")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "Harness Test")
        (self.repo_path / "calculator.py").write_text(
            "def add(left, right):\n    return left - right\n", encoding="utf-8"
        )
        (self.repo_path / "test_calculator.py").write_text(
            "import unittest\nfrom calculator import add\n\n"
            "class CalculatorTests(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(5, add(2, 3))\n",
            encoding="utf-8",
        )
        self._git("add", ".")
        self._git("commit", "-m", "baseline")
        (self.repo_path / "calculator.py").write_text(
            "def add(left, right):\n    return left + right\n", encoding="utf-8"
        )
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = root / "manager.sqlite"
        self.repository = CodeEvidenceRepository()
        self.store = EvidenceArtifactStore(root / "evidence")
        self.scope = RepositoryScope("repo-a", self.repo_path)
        self.diff = GitDiffEvidenceService(
            self.repository, self.store, {"repo-a": self.scope}
        ).capture(
            repository_alias="repo-a",
            bundle_key="diff-a",
            conversation_key="conversation-a",
            task_key="task-a",
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

    @staticmethod
    def _success_runner(command: tuple[str, ...], *, cwd: Path, timeout: int, source_path: Path | None = None) -> dict[str, object]:
        assert command[1:4] == ("-m", "unittest", "-q")
        assert (cwd / "calculator.py").read_text(encoding="utf-8").endswith("left + right\n")
        return {
            "returncode": 0,
            "timed_out": False,
            "cleanup": "not_needed",
            "duration_ms": 4,
            "stdout_sha256": "a" * 64,
            "stderr_sha256": "b" * 64,
        }

    def _service(self, runner=None) -> CodeEvidenceVerificationService:
        return CodeEvidenceVerificationService(
            self.repository,
            self.store,
            {"repo-a": self.scope},
            command_runner=runner or self._success_runner,
        )

    def _verify(self, runner=None) -> dict[str, object]:
        return self._service(runner).verify(
            diff_bundle_id=int(self.diff["bundle_id"]),
            bundle_key="verify-a",
            conversation_key="conversation-a",
            task_key="task-a",
            commands=((sys.executable, "-m", "unittest", "-q", "test_calculator"),),
            timeout_seconds=30,
        )

    def test_replays_frozen_patch_in_private_snapshot_and_persists_bound_receipt(self) -> None:
        before = self._git("status", "--porcelain=v2", "--untracked-files=all")
        result = self._verify()

        self.assertEqual("passed", result["verification_status"])
        self.assertEqual(self.diff["seal_sha256"], result["evidence_bundle_sha256"])
        self.assertTrue(result["snapshot_consistent"])
        self.assertFalse(result["external_calls"])
        self.assertFalse(result["local_mutation"])
        bundle = self.repository.get_bundle(int(result["verification_bundle_id"]))
        self.assertEqual("sealed", bundle["status"])
        records = {item["kind"]: self._record(item) for item in bundle["artifacts"]}
        receipt = json.loads(self.store.reopen(records["verification_receipt"]))
        self.assertEqual(self.diff["bundle_id"], receipt["input_bundle_id"])
        self.assertEqual(self.diff["seal_sha256"], receipt["input_bundle_seal_sha256"])
        self.assertEqual(self.diff["patch_sha256"], receipt["patch_sha256"])
        self.assertEqual(before, self._git("status", "--porcelain=v2", "--untracked-files=all"))
        self.assertEqual(1, int(self._git("rev-list", "--count", "HEAD").strip()))

    def test_invalid_command_and_tampered_patch_fail_before_runner_or_receipt(self) -> None:
        calls: list[object] = []
        service = self._service(lambda *args, **kwargs: calls.append((args, kwargs)))
        with self.assertRaisesRegex(ValueError, "code_evidence_verification_command_invalid"):
            service.verify(
                diff_bundle_id=int(self.diff["bundle_id"]),
                bundle_key="verify-invalid",
                conversation_key="conversation-a",
                task_key="task-a",
                commands=(("/bin/sh", "-c", "echo unsafe"),),
                timeout_seconds=30,
            )
        self.assertEqual([], calls)

        bundle = self.repository.get_bundle(int(self.diff["bundle_id"]))
        patch = next(item for item in bundle["artifacts"] if item["kind"] == "diff_patch")
        (self.store.root / str(patch["relative_path"])).write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "code_evidence_artifact_changed"):
            self._verify()

    def test_failed_command_is_durable_but_verification_side_effect_fails_closed(self) -> None:
        def failed(command: tuple[str, ...], *, cwd: Path, timeout: int, source_path: Path | None = None) -> dict[str, object]:
            return {
                "returncode": 1, "timed_out": False, "cleanup": "not_needed", "duration_ms": 9,
                "stdout_sha256": "c" * 64, "stderr_sha256": "d" * 64,
            }

        result = self._verify(failed)
        self.assertEqual("failed", result["verification_status"])
        self.assertEqual("sealed", self.repository.get_bundle(int(result["verification_bundle_id"]))["status"])

        def mutating(command: tuple[str, ...], *, cwd: Path, timeout: int, source_path: Path | None = None) -> dict[str, object]:
            (cwd / "generated.txt").write_text("side effect", encoding="utf-8")
            return self._success_runner(command, cwd=cwd, timeout=timeout, source_path=source_path)

        with self.assertRaisesRegex(ValueError, "code_evidence_verification_side_effect"):
            self._verify(mutating)

    def test_source_change_during_verification_blocks_seal(self) -> None:
        def mutate_source(command: tuple[str, ...], *, cwd: Path, timeout: int, source_path: Path | None = None) -> dict[str, object]:
            (self.repo_path / "late.txt").write_text("late", encoding="utf-8")
            return self._success_runner(command, cwd=cwd, timeout=timeout, source_path=source_path)

        with self.assertRaisesRegex(ValueError, "code_evidence_repository_changed"):
            self._verify(mutate_source)

    @staticmethod
    def _record(item: dict[str, object]) -> EvidenceArtifactRecord:
        return EvidenceArtifactRecord(
            bundle_id=int(item["bundle_id"]), kind=str(item["kind"]),
            relative_path=str(item["relative_path"]), sha256=str(item["sha256"]),
            size_bytes=int(item["size_bytes"]), device=int(item["device"]),
            inode=int(item["inode"]), mode=int(item["mode"]),
            link_count=int(item["link_count"]),
        )


if __name__ == "__main__":
    unittest.main()
