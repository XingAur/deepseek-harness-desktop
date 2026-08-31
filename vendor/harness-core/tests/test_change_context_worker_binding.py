from __future__ import annotations

import unittest

from app.change_context_execution import (
    ChangeContextExecutionBinding,
    ChangeContextExecutionVerifier,
)
from app.fullstack_executor import FullstackExecutionOptions, FullstackWorktreeExecutor
from app.llm_client import MockLLMClient
from app.multi_service_executor import MultiServiceExecutionOptions, MultiServiceWorktreeExecutor
from app.precommit_verifier import PrecommitVerificationOptions, PrecommitVerifier
from app.worktree_executor import WorktreeCodeExecutor, WorktreeExecutionOptions
from tests.change_context_test_support import ReadyChangeContextService


class ChangeContextWorkerBindingTests(unittest.TestCase):
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

    def test_verifier_reopens_pack_and_rejects_wrong_or_stale_binding(self) -> None:
        self.assertEqual("ready", self.verifier.validate(self.binding).status)
        wrong = ChangeContextExecutionBinding(
            pack_id=self.binding.pack_id,
            projection_hash="sha256:" + "0" * 64,
            layer_hashes=self.binding.layer_hashes,
        )
        self.assertEqual("BLOCKED_CONTEXT_HASH_MISMATCH", self.verifier.validate(wrong).code)
        self.context.repository.successor_pack_id = "ccp:sha256:" + "f" * 64
        self.assertEqual("BLOCKED_CONTEXT_STALE", self.verifier.validate(self.binding).code)

    def test_all_executors_block_missing_binding_before_workspace_access(self) -> None:
        worktree = WorktreeCodeExecutor(MockLLMClient(), change_context_verifier=self.verifier).execute(
            WorktreeExecutionOptions(
                project_path="/path/that/must/not/be-opened",
                run_id=1,
                demand_text="test",
                report_markdown="test",
                allowed_paths=["src/view.vue"],
                verify_commands=["true"],
            )
        )
        self.assertEqual("blocked", worktree.status)
        self.assertEqual("BLOCKED_CONTEXT_BINDING_MISSING", worktree.manifest["change_context_validation"]["code"])

        fullstack = FullstackWorktreeExecutor(change_context_verifier=self.verifier).execute(
            FullstackExecutionOptions(
                run_id=1,
                demand_text="test",
                report_markdown="test",
                project_root="/path/that/must/not/be-opened",
                authority_mode="legacy",
            )
        )
        self.assertEqual("blocked", fullstack.status)
        self.assertEqual("BLOCKED_CONTEXT_BINDING_MISSING", fullstack.manifest["change_context_validation"]["code"])

        multi = MultiServiceWorktreeExecutor(
            MockLLMClient(),
            change_context_verifier=self.verifier,
        ).execute(
            MultiServiceExecutionOptions(
                contract={},
                run_id=1,
                demand_text="test",
                report_markdown="test",
            )
        )
        self.assertEqual("blocked", multi.status)
        self.assertEqual("BLOCKED_CONTEXT_BINDING_MISSING", multi.manifest["change_context_validation"]["code"])

        precommit = PrecommitVerifier(change_context_verifier=self.verifier).execute(
            PrecommitVerificationOptions(
                run_id=1,
                project_root="/path/that/must/not/be-opened",
            )
        )
        self.assertEqual("blocked", precommit.status)
        self.assertEqual("BLOCKED_CONTEXT_BINDING_MISSING", precommit.manifest["change_context_validation"]["code"])

    def test_valid_binding_is_serialized_for_worker_manifests(self) -> None:
        result = WorktreeCodeExecutor(
            MockLLMClient(),
            change_context_verifier=self.verifier,
        ).execute(
            WorktreeExecutionOptions(
                project_path="/path/that/does/not/exist",
                run_id=1,
                demand_text="test",
                report_markdown="test",
                allowed_paths=["src/view.vue"],
                verify_commands=["true"],
                change_context_binding=self.binding.to_dict(),
                change_context_projection=self.context.result.projections["implementation"].to_dict(),
            )
        )
        self.assertEqual("ready", result.manifest["change_context_validation"]["status"])
        self.assertEqual(self.binding.to_dict(), result.manifest["change_context_binding"])

        review_projection = self.context.result.projections["review"]
        review_binding = ChangeContextExecutionBinding(
            pack_id=self.context.result.pack.pack_id,
            projection_hash=review_projection.projection_hash,
            layer_hashes=self.binding.layer_hashes,
        )
        precommit = PrecommitVerifier(change_context_verifier=self.verifier).execute(
            PrecommitVerificationOptions(
                run_id=1,
                project_root="/path/that/does/not/exist",
                project_path="/path/that/does/not/exist",
                allowed_paths=["src/view.vue"],
                verify_commands=["true"],
                change_context_binding=review_binding.to_dict(),
                change_context_projection=review_projection.to_dict(),
            )
        )
        self.assertEqual("ready", precommit.manifest["change_context_validation"]["status"])


if __name__ == "__main__":
    unittest.main()
