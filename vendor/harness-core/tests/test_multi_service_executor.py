from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.change_context_execution import ChangeContextExecutionBinding, ChangeContextExecutionVerifier
from app.harness import RequirementWorkflowRunner
from app.llm_client import MockLLMClient
from app.multi_service_executor import (
    MultiServiceExecutionOptions,
    MultiServiceWorktreeExecutor,
)
from tests.change_context_test_support import ReadyChangeContextService


class MultiServiceExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = ReadyChangeContextService()
        projection = self.context.result.projections["implementation"]
        self.binding = ChangeContextExecutionBinding(
            pack_id=self.context.result.pack.pack_id,
            projection_hash=projection.projection_hash,
            layer_hashes={
                layer.layer_type: layer.content_hash
                for layer in self.context.result.pack.layers
            },
        )
        self.verifier = ChangeContextExecutionVerifier(
            repository=self.context.repository,
            gate=self.context.gate,
        )

    def executor(self) -> MultiServiceWorktreeExecutor:
        return MultiServiceWorktreeExecutor(
            MockLLMClient(),
            change_context_verifier=self.verifier,
        )

    def create_repository(self, root: Path, relative_path: str, content: str = "export const value = true\n") -> Path:
        root.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "harness@example.test"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Harness Test"], cwd=root, check=True)
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True, text=True)
        return root.resolve()

    def ready_contract(self, source: Path, target: Path) -> dict:
        return {
            "schema_version": "multi-service-change-contract.v1",
            "status": "ready",
            "objective": "验证跨仓库受控改码",
            "continuation": {"status": "ready_for_execution", "default": "review_then_apply"},
            "rollback": {"status": "ready", "strategy": "临时 worktree 失败则不写回"},
            "repositories": {
                "01-source": {
                    "role": "frontend",
                    "project_path": str(source),
                    "allowed_paths": ["src/source.js"],
                    "verify_commands": ["git diff --check"],
                },
                "02-target": {
                    "role": "service",
                    "project_path": str(target),
                    "allowed_paths": ["src/target.js"],
                    "verify_commands": ["git diff --check"],
                },
            },
            "targets": [
                {
                    "source_project": "01-source",
                    "source_paths": ["src/source.js"],
                    "entry_paths": [],
                    "endpoint": "/api/demo",
                    "target_project": "02-target",
                    "target_path": "src/target.js",
                    "controller_verified": True,
                }
            ],
        }

    def options(self, contract: dict, worktree_root: Path, *, apply: bool = False) -> MultiServiceExecutionOptions:
        return MultiServiceExecutionOptions(
            contract=contract,
            run_id=123,
            demand_text="只修改已确认的跨服务目标",
            report_markdown="目标证据已核对",
            worktree_root=str(worktree_root),
            apply_to_projects=apply,
            cleanup_worktrees=False,
            change_context_binding=self.binding.to_dict(),
            change_context_projection=self.context.result.projections["implementation"].to_dict(),
        )

    def test_blocked_contract_does_not_create_worktree_or_call_patch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self.create_repository(root / "source", "src/source.js")
            target = self.create_repository(root / "target", "src/target.js")
            contract = self.ready_contract(source, target)
            contract["status"] = "blocked"
            worktree_root = root / "worktrees"

            result = self.executor().execute(
                self.options(contract, worktree_root)
            )

            self.assertEqual("blocked", result.status)
            self.assertFalse(worktree_root.exists())
            self.assertEqual({}, result.repositories)

    def test_all_repositories_pass_aggregate_review_and_originals_remain_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self.create_repository(root / "source", "src/source.js")
            target = self.create_repository(root / "target", "src/target.js")
            original_source = (source / "src/source.js").read_text(encoding="utf-8")
            original_target = (target / "src/target.js").read_text(encoding="utf-8")

            result = self.executor().execute(
                self.options(self.ready_contract(source, target), root / "worktrees")
            )

            self.assertEqual("success", result.status)
            self.assertEqual("passed", result.aggregate_review["status"])
            self.assertEqual({"01-source", "02-target"}, set(result.final_diffs))
            self.assertEqual(original_source, (source / "src/source.js").read_text(encoding="utf-8"))
            self.assertEqual(original_target, (target / "src/target.js").read_text(encoding="utf-8"))
            self.assertTrue(all(item["status"] == "skipped" for item in result.apply_to_projects.values()))

    def test_one_repository_failure_stops_later_repositories_and_never_writes_originals(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self.create_repository(root / "source", "src/source.js")
            target = self.create_repository(root / "target", "src/target.js")
            contract = self.ready_contract(source, target)
            contract["repositories"]["01-source"]["verify_commands"] = [
                "python3 -c \"from pathlib import Path; raise SystemExit(1 if 'HARNESS_WORKTREE_SELF_CHECK' in Path('src/source.js').read_text() else 0)\""
            ]
            original_source = (source / "src/source.js").read_text(encoding="utf-8")
            original_target = (target / "src/target.js").read_text(encoding="utf-8")

            result = self.executor().execute(
                self.options(contract, root / "worktrees")
            )

            self.assertEqual("failed", result.status)
            self.assertEqual("failed", result.repositories["01-source"]["status"])
            self.assertEqual("skipped", result.repositories["02-target"]["status"])
            self.assertEqual(original_source, (source / "src/source.js").read_text(encoding="utf-8"))
            self.assertEqual(original_target, (target / "src/target.js").read_text(encoding="utf-8"))

    def test_explicit_apply_requires_all_checks_and_writes_only_contract_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self.create_repository(root / "source", "src/source.js")
            target = self.create_repository(root / "target", "src/target.js")

            result = self.executor().execute(
                self.options(self.ready_contract(source, target), root / "worktrees", apply=True)
            )

            self.assertEqual("success", result.status)
            self.assertTrue(all(item["status"] == "success" for item in result.apply_to_projects.values()))
            self.assertIn("HARNESS_WORKTREE_SELF_CHECK", (source / "src/source.js").read_text(encoding="utf-8"))
            self.assertIn("HARNESS_WORKTREE_SELF_CHECK", (target / "src/target.js").read_text(encoding="utf-8"))

    def test_context_superseded_during_aggregate_review_blocks_batch_writeback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self.create_repository(root / "source", "src/source.js")
            target = self.create_repository(root / "target", "src/target.js")
            original_source = (source / "src/source.js").read_text(encoding="utf-8")
            original_target = (target / "src/target.js").read_text(encoding="utf-8")
            executor = self.executor()

            def review_then_supersede(*args, **kwargs):
                del args, kwargs
                self.context.repository.successor_pack_id = "ccp:sha256:" + "f" * 64
                return {"status": "passed", "reasons": []}

            executor._review_all = review_then_supersede  # type: ignore[method-assign]
            result = executor.execute(
                self.options(self.ready_contract(source, target), root / "worktrees", apply=True)
            )

            self.assertEqual("blocked", result.status)
            self.assertEqual("BLOCKED_CONTEXT_STALE", result.manifest["change_context_pre_apply_validation"]["code"])
            self.assertEqual(original_source, (source / "src/source.js").read_text(encoding="utf-8"))
            self.assertEqual(original_target, (target / "src/target.js").read_text(encoding="utf-8"))

    def test_harness_fullstack_routes_ready_generic_contract_to_verification_only_executor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = self.create_repository(root / "source", "src/source.js")
            target = self.create_repository(root / "target", "src/target.js")
            runner = object.__new__(RequirementWorkflowRunner)
            runner.llm_client = MockLLMClient()
            runner.capability_service = None
            runner.change_context_service = self.context

            with patch("app.harness.build_markdown_report", return_value=""):
                result = runner._run_fullstack_execution(
                    run_id=321,
                    demand_text="跨服务需求",
                    project_root=root,
                    technical_decision=SimpleNamespace(
                        multi_service_change_contract=self.ready_contract(source, target)
                    ),
                    verify_commands=[],
                    worktree_dir=root / "worktrees",
                    authority_mode="legacy",
                    change_context_result=self.context.result,
                )

            self.assertEqual("success", result.status)
            self.assertEqual("multi_service_worktree_executor", result.manifest["executor"])
            self.assertEqual("passed", result.manifest["aggregate_review"]["status"])
            self.assertTrue(all(item["status"] == "skipped" for item in result.apply_to_projects.values()))
            self.assertNotIn("HARNESS_WORKTREE_SELF_CHECK", (source / "src/source.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
