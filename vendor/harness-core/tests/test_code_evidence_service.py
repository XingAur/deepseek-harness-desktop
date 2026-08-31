from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

from app import database
from app.code_evidence_artifacts import EvidenceArtifactStore
from app.code_evidence_repository import CodeEvidenceRepository
from app.code_evidence_service import CodeEvidenceService
from app.local_agent_review import canonical_review_hash
from app.repository_scope import RepositoryScope
from app.server import (
    build_manager_code_evidence_artifact,
    build_manager_code_evidence_status,
    render_code_evidence_page,
)

from app.code_evidence_service import plan_code_evidence
from app.task_intent_router import IntentContext, classify_task_intent
from app.task_intent_service import TaskIntentRoutingResult


def _routing(message: str, *, mutation: bool = False) -> TaskIntentRoutingResult:
    decision = classify_task_intent(
        message,
        IntentContext(conversation_key="evidence-routing"),
    )
    return TaskIntentRoutingResult(decision=decision, event_id=1, mutation_requested=mutation)


class CodeEvidencePlanningTests(unittest.TestCase):
    def test_general_knowledge_question_skips_code_and_yunxiao(self) -> None:
        result = plan_code_evidence(
            "Python 装饰器是什么？", _routing("Python 装饰器是什么？"), repository_aliases=()
        )

        self.assertEqual("knowledge", result.route)
        self.assertEqual((), result.required_capabilities)
        self.assertFalse(result.yunxiao_required)

    def test_source_and_history_questions_choose_smallest_read_plan(self) -> None:
        source = plan_code_evidence(
            "这个方法的调用链在哪个文件？",
            _routing("这个方法的调用链在哪个文件？"),
            repository_aliases=("repo-a",),
        )
        history = plan_code_evidence(
            "这个文件是谁改的，历史提交为什么这样改？",
            _routing("这个文件是谁改的，历史提交为什么这样改？"),
            repository_aliases=("repo-a",),
        )

        self.assertEqual(("source.search", "source.read"), source.required_capabilities)
        self.assertEqual(
            ("source.search", "source.read", "git.history"),
            history.required_capabilities,
        )
        self.assertFalse(source.mutation_allowed)

    def test_code_review_cannot_skip_diff_verification_or_reviewer(self) -> None:
        result = plan_code_evidence(
            "审核这 51 个改动是否正确，有没有多余代码？",
            _routing("审核这 51 个改动是否正确，有没有多余代码？"),
            repository_aliases=("repo-a",),
        )

        self.assertEqual("code_review", result.route)
        self.assertEqual(
            ("git.diff", "verification.run-local", "code.review-local"),
            result.required_capabilities,
        )
        self.assertEqual((), result.blockers)

    def test_gitlab_review_selects_remote_evidence_skills_and_never_falls_back_to_local_diff(self) -> None:
        message = "审核 GitLab 上 MR 17 的改动、提交记录和流水线是否正确合理"

        result = plan_code_evidence(
            message,
            _routing(message),
            repository_aliases=("repo-a",),
        )

        self.assertEqual("gitlab_code_review", result.route)
        self.assertEqual(
            (
                "merge_request.read",
                "gitlab.merge_request.commits.read",
                "gitlab.merge_request.diffs.read",
                "gitlab.repository.file.read",
                "gitlab.pipeline.jobs.read",
                "code.review-local",
            ),
            result.required_capabilities,
        )
        self.assertEqual(
            ("gitlab_remote_evidence_orchestrator_unavailable",),
            result.blockers,
        )
        self.assertNotIn("git.diff", result.required_capabilities)
        self.assertFalse(result.mutation_allowed)

    def test_github_review_selects_matching_remote_evidence_without_gitlab_fallback(self) -> None:
        message = "审核 GitHub 上 PR #17 的改动、提交记录和 Actions 是否正确合理"

        result = plan_code_evidence(
            message,
            _routing(message),
            repository_aliases=("repo-a",),
        )

        self.assertEqual("github_code_review", result.route)
        self.assertEqual(
            (
                "github.pull_request.read",
                "github.pull_request.commits.read",
                "github.pull_request.diffs.read",
                "github.repository.file.read",
                "github.actions.run.jobs.read",
                "code.review-local",
            ),
            result.required_capabilities,
        )
        self.assertEqual(
            ("github_remote_evidence_orchestrator_unavailable",),
            result.blockers,
        )
        self.assertNotIn("git.diff", result.required_capabilities)
        self.assertFalse(result.mutation_allowed)

    def test_requirement_inquiry_and_mutation_never_downgrade_without_yunxiao(self) -> None:
        inquiry_message = "这个需求会影响哪些代码路径？"
        mutation_message = "请修改并修复这个需求"
        inquiry = plan_code_evidence(
            inquiry_message, _routing(inquiry_message), repository_aliases=("repo-a",)
        )
        mutation = plan_code_evidence(
            mutation_message,
            _routing(mutation_message, mutation=True),
            repository_aliases=("repo-a",),
        )

        self.assertEqual("requirement_workflow", inquiry.route)
        self.assertEqual("requirement_workflow", mutation.route)
        self.assertEqual(inquiry.required_capabilities, mutation.required_capabilities)
        self.assertFalse(inquiry.mutation_allowed)
        self.assertTrue(mutation.mutation_allowed)
        self.assertFalse(inquiry.yunxiao_required)

    def test_code_or_requirement_plan_without_repository_fails_closed(self) -> None:
        message = "审核这些代码改动"
        result = plan_code_evidence(message, _routing(message), repository_aliases=())
        self.assertEqual(("code_evidence_repository_unavailable",), result.blockers)


class _Reviewer:
    def start(self, request, sink):
        sink.on_started(123, "darwin-proc-bsdinfo-v1:123:456")
        for sequence, event_type, item_type in (
            (1, "thread.started", ""), (2, "turn.started", ""),
            (3, "item.completed", "agent_message"), (4, "turn.completed", ""),
        ):
            event = {"type": event_type, "sequence_no": sequence, "raw_line_sha256": str(sequence) * 64}
            if item_type:
                event["item_type"] = item_type
            sink.on_event(event)
        response = {"schema_version": "his-local-agent-review.v1", "verdict": "approved", "findings": [], "summary": "Frozen evidence is complete."}
        response["review_hash"] = canonical_review_hash(response)
        digest = hashlib.sha256(json.dumps(response, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return SimpleNamespace(pid=123, process_start_identity="darwin-proc-bsdinfo-v1:123:456", exit_code=0,
                               error_code="", primary_error_code="", cleanup_error_code="", final_response=response,
                               canonical_final_response_sha256=digest, protocol_rejection=None,
                               final_response_validated=False, untrusted_final_response=True)


class CompleteCodeEvidenceFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="harness-code-flow-")
        self.root = Path(self.temp.name)
        self.previous_db = database.DB_PATH
        database.DB_PATH = self.root / "manager.sqlite"
        self.repository = CodeEvidenceRepository()
        self.store = EvidenceArtifactStore(self.root / "evidence")

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db
        self.temp.cleanup()

    def _repo(self, alias: str) -> tuple[Path, RepositoryScope]:
        path = self.root / alias
        path.mkdir()
        for args in (("init",), ("config", "user.email", "test@example.invalid"), ("config", "user.name", "Harness Test")):
            subprocess.run(["/usr/bin/git", "-C", str(path), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (path / "calculator.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        (path / "test_calculator.py").write_text("import unittest\nfrom calculator import add\nclass T(unittest.TestCase):\n    def test_add(self): self.assertEqual(5, add(2, 3))\n", encoding="utf-8")
        subprocess.run(["/usr/bin/git", "-C", str(path), "add", "."], check=True)
        subprocess.run(["/usr/bin/git", "-C", str(path), "commit", "-m", "baseline"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (path / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        return path, RepositoryScope(alias, path)

    @staticmethod
    def _verify(_command, *, cwd, timeout, source_path=None):
        return {"returncode": 0, "timed_out": False, "cleanup": "not_needed", "duration_ms": 1,
                "stdout_sha256": "a" * 64, "stderr_sha256": "b" * 64}

    def test_single_and_multi_repository_review_are_immutable_and_restart_safe(self) -> None:
        repo_a, scope_a = self._repo("repo-a")
        repo_b, scope_b = self._repo("repo-b")
        scopes = {"repo-a": scope_a, "repo-b": scope_b}
        service = CodeEvidenceService(self.repository, self.store, scopes, verification_runner=self._verify, reviewer_worker=_Reviewer())
        commands = {alias: ((sys.executable, "-m", "unittest", "-q", "test_calculator"),) for alias in scopes}
        before = {alias: subprocess.check_output(["/usr/bin/git", "-C", str(path), "status", "--porcelain=v2", "--untracked-files=all"]) for alias, path in (("repo-a", repo_a), ("repo-b", repo_b))}

        result = service.review_changes(conversation_key="conversation-flow", task_key="task-flow", repository_aliases=("repo-b", "repo-a"), commands=commands)

        self.assertEqual("approved", result["status"])
        self.assertFalse(result["external_calls"])
        self.assertEqual(2, result["evidence_set"]["repository_count"])
        self.assertEqual(["repo-a", "repo-b"], [item["repository_alias"] for item in result["evidence_set"]["members"]])
        manager = build_manager_code_evidence_status(self.repository, self.store)
        self.assertEqual("ready", manager["status"])
        self.assertTrue(any(item["review_verdict"] == "approved" for item in manager["bundles"]))
        diff_id = int(result["repositories"][0]["diff"]["bundle_id"])
        artifact = build_manager_code_evidence_artifact(
            bundle_id=diff_id, kind="diff_patch", offset=0, limit=128,
            repository=self.repository, artifact_store=self.store,
        )
        self.assertEqual(128, artifact["next_offset"])
        self.assertIn("diff --git", artifact["content_text"])
        import app.server as manager_server
        with mock.patch.object(
            manager_server,
            "build_manager_code_evidence_status",
            return_value={**manager, "configured_repositories": ["repo-a", "repo-b"]},
        ):
            page = render_code_evidence_page()
        self.assertIn("代码证据与审核", page)
        self.assertIn("状态 JSON", page)
        reopened = CodeEvidenceService(self.repository, self.store, scopes, verification_runner=self._verify, reviewer_worker=_Reviewer())
        self.assertEqual("sealed", reopened._sets.validate(int(result["evidence_set"]["evidence_set_id"]))["status"])
        for alias, path in (("repo-a", repo_a), ("repo-b", repo_b)):
            self.assertEqual(before[alias], subprocess.check_output(["/usr/bin/git", "-C", str(path), "status", "--porcelain=v2", "--untracked-files=all"]))

        (repo_b / "unrelated.py").write_text("changed = True\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "code_evidence_set_changed"):
            reopened._sets.validate(int(result["evidence_set"]["evidence_set_id"]))

    def test_manager_external_reviewer_is_disabled_by_default_and_requires_exact_switch(self) -> None:
        repository_path, _scope = self._repo("manager-repo")
        projects = json.dumps(
            {
                "manager-repo": {
                    "path": str(repository_path),
                    "allowed_paths": ["calculator.py"],
                    "verification_commands": [
                        [sys.executable, "-m", "unittest", "-q", "test_calculator"]
                    ],
                }
            }
        )
        import app.server as manager_server

        with mock.patch.dict(
            "os.environ",
            {"HARNESS_CODE_EVIDENCE_PROJECTS_JSON": projects},
            clear=False,
        ):
            with mock.patch.dict(
                "os.environ", {"HARNESS_CODE_EVIDENCE_REVIEWER_ENABLED": "0"}
            ):
                disabled, aliases, _commands = manager_server._manager_code_evidence_configuration()
            self.assertIsNotNone(disabled)
            self.assertEqual(("manager-repo",), aliases)
            self.assertFalse(disabled.reviewer_external_model_enabled)

            with mock.patch.dict(
                "os.environ", {"HARNESS_CODE_EVIDENCE_REVIEWER_ENABLED": "1"}
            ):
                enabled, aliases, _commands = manager_server._manager_code_evidence_configuration()
            self.assertIsNotNone(enabled)
            self.assertEqual(("manager-repo",), aliases)
            self.assertTrue(enabled.reviewer_external_model_enabled)

            with mock.patch.dict(
                "os.environ", {"HARNESS_CODE_EVIDENCE_REVIEWER_ENABLED": "true"}
            ):
                invalid, aliases, commands = manager_server._manager_code_evidence_configuration()
            self.assertIsNone(invalid)
            self.assertEqual((), aliases)
            self.assertEqual({}, commands)


if __name__ == "__main__":
    unittest.main()
