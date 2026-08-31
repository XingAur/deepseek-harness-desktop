from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from app.llm_client import MockLLMClient
from app.worktree_executor import (
    PATCH_TIMEOUT_SECONDS,
    WorktreeCodeExecutor,
    apply_final_diff_to_project,
    atomic_write_text,
    build_local_apply_application_id,
    build_allowed_file_context,
    capture_target_file_states,
    reconcile_local_apply_transactions,
    resolve_local_apply_transaction_root,
    verification_failure_matches_baseline,
    run_command,
)


class WorktreePreflightTests(unittest.TestCase):
    def create_repository(self, root: Path) -> Path:
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "harness@example.test"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=root, check=True)
        target = root / "src/target.js"
        target.parent.mkdir(parents=True)
        target.write_text("export const target = true\n", encoding="utf-8")
        (root / "src/unrelated.js").write_text("export const unrelated = true\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True, text=True)
        return root.resolve()

    def test_preflight_allows_unrelated_dirty_paths_but_blocks_allowed_path(self) -> None:
        executor = WorktreeCodeExecutor(MockLLMClient())
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = self.create_repository(Path(temp_dir))
            (repository / "src/unrelated.js").write_text("export const unrelated = false\n", encoding="utf-8")

            self.assertEqual("", executor._preflight(project_path=repository, allowed_paths=["src/target.js"]))

            (repository / "src/target.js").write_text("export const target = false\n", encoding="utf-8")
            error = executor._preflight(project_path=repository, allowed_paths=["src/target.js"])

        self.assertIn("白名单文件存在未提交改动", error)

    def test_allowed_file_context_includes_current_source_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            worktree = Path(temp_dir)
            target = worktree / "src/target.js"
            target.parent.mkdir(parents=True)
            target.write_text("export const archiveDefault = '建档_证件类型默认值'\n", encoding="utf-8")

            context = build_allowed_file_context(worktree_path=worktree, allowed_paths=["src/target.js"])

        self.assertIn("【当前源码：src/target.js】", context)
        self.assertIn("建档_证件类型默认值", context)

    def test_allowed_file_context_keeps_complete_single_file_within_total_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            worktree = Path(temp_dir)
            target = worktree / "src/target.js"
            target.parent.mkdir(parents=True)
            target.write_text("a" * 30_000 + "\nexport const targetName = 'GuaHaoChaXun'\n", encoding="utf-8")

            context = build_allowed_file_context(worktree_path=worktree, allowed_paths=["src/target.js"])

        self.assertIn("targetName = 'GuaHaoChaXun'", context)
        self.assertNotIn("日志已截断", context)

    def test_matching_baseline_verification_failure_is_not_a_patch_regression(self) -> None:
        self.assertTrue(
            verification_failure_matches_baseline(
                patched={"returncode": 1, "stdout": "", "stderr": "TypeError: broken rule\n"},
                baseline={"returncode": 1, "stdout": "", "stderr": "TypeError: broken rule\n"},
            )
        )
        self.assertFalse(
            verification_failure_matches_baseline(
                patched={"returncode": 1, "stdout": "", "stderr": "new lint error"},
                baseline={"returncode": 0, "stdout": "", "stderr": ""},
            )
        )

    def test_apply_final_diff_preserves_unrelated_dirty_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = self.create_repository(Path(temp_dir))
            (repository / "src/unrelated.js").write_text("export const unrelated = false\n", encoding="utf-8")
            patch = """diff --git a/src/target.js b/src/target.js
--- a/src/target.js
+++ b/src/target.js
@@ -1 +1 @@
-export const target = true
+export const target = false
"""

            result = apply_final_diff_to_project(project_path=repository, final_diff=patch)

            self.assertEqual("success", result["status"])
            self.assertEqual(["src/unrelated.js"], result["unrelated_dirty_paths"])
            self.assertIn("target = false", (repository / "src/target.js").read_text(encoding="utf-8"))
            self.assertIn("unrelated = false", (repository / "src/unrelated.js").read_text(encoding="utf-8"))

    def test_post_apply_diff_check_failure_rolls_back_and_preserves_unrelated_dirty_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = self.create_repository(Path(temp_dir))
            target = repository / "src/target.js"
            unrelated = repository / "src/unrelated.js"
            original_target = target.read_text(encoding="utf-8")
            unrelated.write_text("export const unrelated = false\n", encoding="utf-8")
            patch = (
                "diff --git a/src/target.js b/src/target.js\n"
                "--- a/src/target.js\n"
                "+++ b/src/target.js\n"
                "@@ -1 +1,2 @@\n"
                " export const target = true\n"
                "+export const invalid = true; \n"
            )

            result = apply_final_diff_to_project(project_path=repository, final_diff=patch)

            self.assertEqual("rolled_back", result["status"])
            self.assertEqual("success", result["recovery"]["status"])
            self.assertEqual(original_target, target.read_text(encoding="utf-8"))
            self.assertEqual("export const unrelated = false\n", unrelated.read_text(encoding="utf-8"))
            self.assertTrue(Path(result["transaction"]["journal_path"]).is_file())
            self.assertTrue(Path(result["transaction"]["patch_path"]).is_file())

    def test_successful_local_apply_is_idempotent_for_same_patch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = self.create_repository(Path(temp_dir))
            patch = """diff --git a/src/target.js b/src/target.js
--- a/src/target.js
+++ b/src/target.js
@@ -1 +1 @@
-export const target = true
+export const target = false
"""

            first = apply_final_diff_to_project(project_path=repository, final_diff=patch)
            repeated = apply_final_diff_to_project(project_path=repository, final_diff=patch)

            self.assertEqual("success", first["status"])
            self.assertFalse(first["idempotent"])
            self.assertEqual("success", repeated["status"])
            self.assertTrue(repeated["idempotent"])
            self.assertEqual("already_applied", repeated["transaction"]["state"])
            self.assertEqual(first["application_id"], repeated["application_id"])
            self.assertEqual("export const target = false\n", (repository / "src/target.js").read_text(encoding="utf-8"))

    def test_successful_patch_can_be_reapplied_after_exact_external_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = self.create_repository(Path(temp_dir))
            target = repository / "src/target.js"
            patch = """diff --git a/src/target.js b/src/target.js
--- a/src/target.js
+++ b/src/target.js
@@ -1 +1 @@
-export const target = true
+export const target = false
"""

            first = apply_final_diff_to_project(project_path=repository, final_diff=patch)
            target.write_text("export const target = true\n", encoding="utf-8")
            reapplied = apply_final_diff_to_project(project_path=repository, final_diff=patch)
            repeated = apply_final_diff_to_project(project_path=repository, final_diff=patch)

            self.assertEqual("success", first["status"])
            self.assertEqual("success", reapplied["status"])
            self.assertFalse(reapplied["idempotent"])
            self.assertEqual("externally_reverted", reapplied["recovery"]["status"])
            self.assertTrue(Path(reapplied["recovery"]["archived_journal_path"]).is_file())
            self.assertEqual("success", repeated["status"])
            self.assertTrue(repeated["idempotent"])
            self.assertEqual("export const target = false\n", target.read_text(encoding="utf-8"))

    def test_reconcile_recovers_interrupted_applied_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = self.create_repository(Path(temp_dir))
            patch = """diff --git a/src/target.js b/src/target.js
--- a/src/target.js
+++ b/src/target.js
@@ -1 +1 @@
-export const target = true
+export const target = false
"""
            paths = ["src/target.js"]
            pre_status = run_command(["git", "status", "--porcelain"], cwd=repository, timeout=PATCH_TIMEOUT_SECONDS)
            pre_states = capture_target_file_states(repository, paths)
            patch_hash = __import__("hashlib").sha256(patch.encode("utf-8")).hexdigest()
            application_id = build_local_apply_application_id(project_path=repository, patch_hash=patch_hash)
            transaction_root = Path(resolve_local_apply_transaction_root(repository)["path"])
            transaction_dir = transaction_root / application_id
            patch_path = transaction_dir / "final.diff"
            journal_path = transaction_dir / "journal.json"
            atomic_write_text(patch_path, patch)
            applied = run_command(["git", "apply", "-"], cwd=repository, input_text=patch, timeout=PATCH_TIMEOUT_SECONDS)
            self.assertEqual(0, applied["returncode"])
            journal = {
                "schema_version": "1.0-local-apply-transaction",
                "application_id": application_id,
                "project_path": str(repository),
                "patch_path": str(patch_path),
                "patch_hash": "sha256:" + patch_hash,
                "changed_paths": paths,
                "pre_apply_status": pre_status,
                "pre_file_states": pre_states,
                "post_file_states": capture_target_file_states(repository, paths),
                "state": "post_check_failed",
            }
            atomic_write_text(journal_path, __import__("json").dumps(journal))

            summary = reconcile_local_apply_transactions(repository)

            self.assertEqual(1, summary["recovered_count"])
            self.assertEqual("export const target = true\n", (repository / "src/target.js").read_text(encoding="utf-8"))
            persisted = __import__("json").loads(journal_path.read_text(encoding="utf-8"))
            self.assertEqual("rolled_back", persisted["state"])

    def test_reconcile_marks_prepared_but_unapplied_transaction_as_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = self.create_repository(Path(temp_dir))
            patch = """diff --git a/src/target.js b/src/target.js
--- a/src/target.js
+++ b/src/target.js
@@ -1 +1 @@
-export const target = true
+export const target = false
"""
            paths = ["src/target.js"]
            patch_hash = __import__("hashlib").sha256(patch.encode("utf-8")).hexdigest()
            application_id = build_local_apply_application_id(project_path=repository, patch_hash=patch_hash)
            transaction_dir = Path(resolve_local_apply_transaction_root(repository)["path"]) / application_id
            patch_path = transaction_dir / "final.diff"
            journal_path = transaction_dir / "journal.json"
            atomic_write_text(patch_path, patch)
            journal = {
                "schema_version": "1.0-local-apply-transaction",
                "application_id": application_id,
                "project_path": str(repository),
                "patch_path": str(patch_path),
                "patch_hash": "sha256:" + patch_hash,
                "changed_paths": paths,
                "pre_apply_status": run_command(["git", "status", "--porcelain"], cwd=repository, timeout=PATCH_TIMEOUT_SECONDS),
                "pre_file_states": capture_target_file_states(repository, paths),
                "state": "prepared",
            }
            atomic_write_text(journal_path, __import__("json").dumps(journal))

            summary = reconcile_local_apply_transactions(repository)

            self.assertEqual(1, summary["cancelled_count"])
            persisted = __import__("json").loads(journal_path.read_text(encoding="utf-8"))
            self.assertEqual("cancelled_not_applied", persisted["state"])

    def test_git_apply_recount_accepts_valid_context_with_incorrect_hunk_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = self.create_repository(Path(temp_dir))
            patch = """--- a/src/target.js
+++ b/src/target.js
@@ -1,99 +1,100 @@
 export const target = true
+export const archiveDefault = '建档_证件类型默认值'
"""

            standard = run_command(
                ["git", "apply", "--check", "-"],
                cwd=repository,
                input_text=patch,
                timeout=PATCH_TIMEOUT_SECONDS,
            )
            recounted = run_command(
                ["git", "apply", "--check", "--recount", "-"],
                cwd=repository,
                input_text=patch,
                timeout=PATCH_TIMEOUT_SECONDS,
            )

        self.assertNotEqual(0, standard["returncode"])
        self.assertEqual(0, recounted["returncode"])
