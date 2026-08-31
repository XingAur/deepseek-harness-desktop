from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.agent_backend_protocol import AgentBackendRequest, AgentBackendResult
from app.external_task_session import ExternalTaskSession
from app.local_agent_contract import load_local_agent_task
from app.requirement_understanding_completion import (
    COMPLETION_CONTRACT_SCHEMA,
    complete_task_understanding,
)
from app.requirement_package import export_requirement_package


def _result_with_payload(payload: object) -> AgentBackendResult:
    return AgentBackendResult(
        exit_code=0,
        error_code="",
        event_count=0,
        final_response_sha256="",
        canonical_final_response_sha256="",
        final_response_validated=False,
        final_response={"schema_version": COMPLETION_CONTRACT_SCHEMA, "text": json.dumps(payload, ensure_ascii=False)},
    )


def _failed_result() -> AgentBackendResult:
    return AgentBackendResult(
        exit_code=1,
        error_code="worker_backend_unavailable",
        event_count=0,
        final_response_sha256="",
        canonical_final_response_sha256="",
        final_response_validated=False,
    )


def _model_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "checks": [
            {"name": name, "status": "pass", "summary": f"{name} 的证据支撑结论，来源归档证据与项目事实。"}
            for name in (
                "business_background", "usage_scenario", "target_and_boundary", "project_selection",
                "entry_and_call_chain", "conversation_alignment", "error_chain_closure",
                "change_and_impact_scope", "verification_baseline",
            )
        ],
        "request": "在目标项目中按归档需求完成发票重打历史记录功能，只改动列出的路径，不扩大范围。",
        "allowed_paths": ["src/main.py"],
        "verification_tests": ["tests.test_invoice_reprint"],
        "acceptance_criteria": ["重打后生成可追溯的历史记录"],
        "business_questions": [],
    }
    payload.update(overrides)
    return payload


class _GitProject:
    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "tests").mkdir(parents=True, exist_ok=True)
        (root / "src" / "main.py").write_text("print('project')\n", encoding="utf-8")
        (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (root / "README.md").write_text("demo project\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], cwd=root, check=True, capture_output=True)


class UnderstandingCompletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        base = Path(self._temp.name)
        ticket_dir = base / "DFHIS-39999"
        yunxiao = ticket_dir / "yunxiao"
        yunxiao.mkdir(parents=True)
        (ticket_dir / "runs").mkdir()
        (ticket_dir / "requirement.md").write_text("原始需求：门诊发票重打需要记录历史。\n", encoding="utf-8")
        (yunxiao / "snapshot.json").write_text("{}", encoding="utf-8")
        self.package = Path(export_requirement_package(ticket_dir=ticket_dir, run_id=0)["package_dir"])
        self.project = _GitProject(base / "project")

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _complete(self, payload: object) -> dict[str, object]:
        return complete_task_understanding(
            package_dir=self.package,
            worktree_root=self.project.root,
            authorization_id="DFHIS-39999-change-1",
            host_execute=lambda request: _result_with_payload(payload),
            selected_model_id="deepseek-reasoner",
        )

    def test_ready_completion_produces_gate_passing_artifacts(self) -> None:
        result = self._complete(_model_payload())

        self.assertEqual(result["status"], "ready")
        understanding = json.loads((self.package / "analysis" / "requirement_understanding.json").read_text(encoding="utf-8"))
        self.assertEqual("ready_for_change", understanding["status"])
        self.assertTrue(understanding["can_modify"])
        self.assertEqual([], understanding["blockers"])
        self.assertEqual(9, len(understanding["checks"]))
        self.assertTrue(all(check["status"] == "pass" for check in understanding["checks"]))
        task = load_local_agent_task(Path(result["contract_path"]))
        self.assertEqual(str(self.project.root.resolve()), str(task.project_path))
        self.assertEqual(("src/main.py",), task.allowed_paths)
        self.assertEqual(1, len(task.verification_commands))
        self.assertIn("unittest", task.verification_commands[0])

    def test_business_questions_block_instead_of_fabricating_readiness(self) -> None:
        result = self._complete(_model_payload(business_questions=["重打记录是否需要按操作员过滤？"]))

        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("business_question" in blocker for blocker in result["blockers"]))
        understanding = json.loads((self.package / "analysis" / "requirement_understanding.json").read_text(encoding="utf-8"))
        self.assertEqual("not_ready", understanding["status"])

    def test_non_git_worktree_blocks_even_when_the_model_claims_pass(self) -> None:
        plain = Path(self._temp.name) / "plain"
        plain.mkdir()
        result = complete_task_understanding(
            package_dir=self.package,
            worktree_root=plain,
            authorization_id="a-1",
            host_execute=lambda request: _result_with_payload(_model_payload()),
        )
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("worktree_not_git_root" in blocker for blocker in result["blockers"]))

    def test_model_failure_is_reported_without_fabrication(self) -> None:
        result = complete_task_understanding(
            package_dir=self.package,
            worktree_root=self.project.root,
            authorization_id="a-1",
            host_execute=lambda request: _failed_result(),
        )
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("model_executor_unavailable" in blocker for blocker in result["blockers"]))

    def test_deterministic_floor_drops_missing_paths_and_blocks_without_any(self) -> None:
        result = self._complete(_model_payload(allowed_paths=["src/main.py", "does/not/exist.py"]))

        # 存在的路径保留，不存在的被确定性剔除；仍有可用路径时不因此阻断。
        self.assertEqual(result["status"], "ready")
        task = load_local_agent_task(Path(result["contract_path"]))
        self.assertEqual(("src/main.py",), task.allowed_paths)

        result = self._complete(_model_payload(allowed_paths=["does/not/exist.py"]))
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("allowed_path" in blocker for blocker in result["blockers"]))

    def test_session_executes_after_completing_understanding(self) -> None:
        calls: list[str] = []
        session = ExternalTaskSession(
            runner_factory=lambda start, **_kwargs: calls.append("runner") or _RecordingRunner(),
            task_loader=lambda path: load_local_agent_task(path),
            preflight_factory=lambda **_kwargs: object(),
        )
        result = session.execute(
            {
                "schema_version": "harness-external-task.v1",
                "archive_root": str(self.package),
                "worktree_root": str(self.project.root),
                "knowledge_home": str(Path(self._temp.name) / "knowledge"),
                "authorization_id": "DFHIS-39999-change-1",
                "selected_model_id": "deepseek-reasoner",
            },
            host_handler=lambda request: _result_with_payload(_model_payload()),
        )
        self.assertEqual("completed", result["status"])
        self.assertEqual(["runner"], calls)

    def test_business_answers_reach_the_model_as_top_priority_context(self) -> None:
        answers = self.package / "analysis" / "business_answers.md"
        answers.write_text(
            "# 业务答复（用户已确认）\n\n- 重打记录按操作员过滤，保留最近 3 个月。\n",
            encoding="utf-8",
        )
        prompts: list[str] = []

        def host_execute(request: AgentBackendRequest) -> AgentBackendResult:
            prompts.append(request.prompt)
            return _result_with_payload(_model_payload())

        result = complete_task_understanding(
            package_dir=self.package,
            worktree_root=self.project.root,
            authorization_id="a-1",
            host_execute=host_execute,
        )
        self.assertEqual(result["status"], "ready")
        self.assertTrue(prompts)
        self.assertIn("用户已确认的业务答复（最高优先级）", prompts[0])
        self.assertIn("重打记录按操作员过滤", prompts[0])

    def test_blocked_session_returns_concrete_blockers_to_the_ui(self) -> None:
        session = ExternalTaskSession(
            runner_factory=lambda *_a, **_k: _RecordingRunner(),
            task_loader=lambda path: load_local_agent_task(path),
            preflight_factory=lambda **_kwargs: object(),
        )
        result = session.execute(
            {
                "schema_version": "harness-external-task.v1",
                "archive_root": str(self.package),
                "worktree_root": str(self.project.root),
                "knowledge_home": str(Path(self._temp.name) / "knowledge"),
                "authorization_id": "DFHIS-39999-change-1",
            },
            host_handler=lambda request: _result_with_payload(
                _model_payload(business_questions=["重打记录是否需要按操作员过滤？"])
            ),
        )
        self.assertEqual("blocked", result["status"])
        self.assertEqual("requirement_understanding_incomplete", result["error_code"])
        blockers = result.get("understanding_blockers")
        self.assertIsInstance(blockers, list)
        self.assertTrue(any("按操作员过滤" in blocker for blocker in blockers))


class _RecordingRunner:
    def execute(self, task: object, preflight: object) -> dict[str, object]:
        return {
            "run": {"id": 1, "task_key": "k", "status": "completed", "contract_hash": "h", "initial_head": "a"},
            "attempts": [],
            "events": [],
            "artifacts": [],
        }


if __name__ == "__main__":
    unittest.main()
