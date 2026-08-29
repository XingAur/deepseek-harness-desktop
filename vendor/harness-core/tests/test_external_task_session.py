from __future__ import annotations

import json
import io
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from app.external_task_session import ExternalTaskSession
from app.agent_backend import AgentBackendRole
from app.agent_backend_protocol import AgentBackendRequest, AgentBackendResult, request_hash
from tools.harness_host_server import run_external_task_once, run_host_bridge_once


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


class ExternalTaskSessionTests(unittest.TestCase):
    def test_jsonl_host_bridge_can_archive_yunxiao_intake_without_starting_an_agent(self):
        inbound = io.StringIO(json.dumps({
            "schema_version": "harness-host-session.v1",
            "type": "task.start",
            "request_id": "intake-1",
            "payload": {
                "schema_version": "harness-external-task.v1",
                "archive_root": "/private/tmp/harness-intake-archive",
                "intake_source": "DFHIS-39999",
                "intake_include_comments": True,
                "worktree_root": "/private/tmp/harness-intake-worktree",
                "knowledge_home": "/private/tmp/harness-intake-knowledge",
                "authorization_id": "harness-intake",
            },
        }) + "\n")
        outbound = io.StringIO()
        calls: list[object] = []

        with patch(
            "tools.harness_host_server.prepare_yunxiao_harness_package",
            return_value={
                "ticket_id": "DFHIS-39999",
                "package_dir": "/private/tmp/harness-intake-archive/DFHIS-39999/harness",
                "package_status": "partial",
                "pending_count": 9,
            },
        ) as prepare:
            result = run_external_task_once(
                input_stream=inbound,
                output_stream=outbound,
                runner_factory=lambda *_args, **_kwargs: calls.append("runner"),
            )

        self.assertEqual("completed", result["status"])
        self.assertEqual("DFHIS-39999", result["snapshot"]["ticket_id"])
        self.assertEqual([], calls)
        prepare.assert_called_once_with(
            archive_root="/private/tmp/harness-intake-archive",
            yunxiao_url="DFHIS-39999",
            include_comments=True,
        )
        message = json.loads(outbound.getvalue())
        self.assertEqual("task.result", message["type"])
        self.assertEqual("/private/tmp/harness-intake-archive/DFHIS-39999/harness", message["payload"]["snapshot"]["package_dir"])

    def test_jsonl_host_bridge_turns_main_chat_into_a_governed_task_with_source_selection(self):
        inbound = io.StringIO(json.dumps({
            "schema_version": "harness-host-session.v1",
            "type": "task.start",
            "request_id": "chat-1",
            "payload": {
                "schema_version": "harness-external-task.v1",
                "archive_root": "/private/tmp/harness-chat-archive",
                "chat_prompt": "按云效需求修复页面",
                "intake_source": "DFHIS-12345",
                "chat_evidence_paths": ["/private/tmp/需求.png"],
                "workspace_id": "workspace-1",
                "worktree_root": "/private/tmp/harness-chat-worktree",
                "knowledge_home": "/private/tmp/harness-chat-knowledge",
                "authorization_id": "harness-chat",
                "selected_model_id": "gpt-5.6-sol",
            },
        }) + "\n")
        outbound = io.StringIO()
        package = {
            "ticket_id": "DFHIS-12345",
            "package_dir": "/private/tmp/harness-chat-archive/DFHIS-12345/harness",
            "package_status": "partial",
            "pending_count": 10,
        }
        with patch("tools.harness_host_server.prepare_chat_harness_package", return_value=package) as prepare, patch.object(
            ExternalTaskSession,
            "execute",
            return_value={"status": "completed", "snapshot": {}},
        ) as execute:
            result = run_external_task_once(input_stream=inbound, output_stream=outbound)

        self.assertEqual("completed", result["status"])
        self.assertEqual("DFHIS-12345", result["snapshot"]["ticket_id"])
        prepare.assert_called_once_with(
            archive_root="/private/tmp/harness-chat-archive",
            prompt="按云效需求修复页面",
            workspace_id="workspace-1",
            yunxiao_source="DFHIS-12345",
            evidence_paths=["/private/tmp/需求.png"],
        )
        request = execute.call_args.args[0]
        self.assertEqual("gpt-5.6-sol", request["selected_model_id"])
        self.assertEqual("/private/tmp/harness-chat-archive/DFHIS-12345/harness", request["archive_root"])

    def test_archive_task_package_resolves_generated_contract_and_understanding_without_manual_paths(self):
        with tempfile.TemporaryDirectory(prefix="harness-archive-task-") as directory:
            package = Path(directory)
            (package / "analysis").mkdir(parents=True, exist_ok=True)
            understanding = _write(
                package / "analysis" / "requirement_understanding.json",
                {
                    "schema_version": "requirement-understanding.v1",
                    "status": "ready_for_change",
                    "can_modify": True,
                    "checks": [{"name": name, "status": "pass"} for name in (
                        "business_background", "usage_scenario", "target_and_boundary",
                        "project_selection", "entry_and_call_chain", "conversation_alignment",
                        "error_chain_closure", "change_and_impact_scope", "verification_baseline",
                    )],
                    "blockers": [],
                },
            )
            (package / "engineering").mkdir(parents=True, exist_ok=True)
            calls: list[Path] = []
            session = ExternalTaskSession(
                runner_factory=lambda *_args, **_kwargs: None,
                task_loader=lambda path: calls.append(path) or "loaded-task",
            )
            result = session.start({
                "schema_version": "harness-external-task.v1",
                "archive_root": str(package),
                "worktree_root": "/private/tmp/his_harness_stage_f_archive",
                "knowledge_home": "/private/tmp/his_harness_knowledge_archive",
                "authorization_id": "desktop-harness-archive-001",
            })

            self.assertEqual("accepted", result["status"])
            self.assertEqual([package / "engineering" / "task_contract.json"], calls)
            self.assertTrue(understanding.is_file())

    def test_jsonl_host_bridge_round_trip_is_one_request_and_one_result(self):
        request = AgentBackendRequest(
            role=AgentBackendRole.WORKER,
            worktree_path=Path("/tmp/harness-worktree"),
            prompt="只执行 Harness 决策",
            timeout_seconds=30,
            output_contract={"name": "none", "schema_version": "none"},
            capabilities=("source.search",),
        )
        result = AgentBackendResult(
            exit_code=0,
            error_code="",
            event_count=0,
            final_response_sha256="a" * 64,
            canonical_final_response_sha256="b" * 64,
            final_response_validated=True,
            final_response={"ok": True},
        )
        inbound = io.StringIO(json.dumps({
            "schema_version": "harness-host-session.v1",
            "type": "agent.result",
            "request_id": "7" * 64,
            "payload": result.to_dict(),
        }) + "\n")
        outbound = io.StringIO()

        actual = run_host_bridge_once(
            request,
            input_stream=inbound,
            output_stream=outbound,
            request_id=lambda value: "7" * 64,
        )

        self.assertEqual(0, actual.exit_code)
        message = json.loads(outbound.getvalue())
        self.assertEqual("agent.request", message["type"])
        self.assertEqual(request.to_dict(), message["payload"])

    def test_intake_with_selected_model_drafts_documents_over_the_host_bridge(self):
        """归档 + 模型起草在同一次 intake 里闭环，结果快照带回生成事实。"""

        with tempfile.TemporaryDirectory(prefix="harness-intake-gen-") as directory:
            ticket_dir = Path(directory) / "DFHIS-39999"
            yunxiao = ticket_dir / "yunxiao"
            yunxiao.mkdir(parents=True)
            (ticket_dir / "runs").mkdir()
            (ticket_dir / "requirement.md").write_text("原始需求：门诊发票重打记录历史。", encoding="utf-8")
            (yunxiao / "snapshot.json").write_text("{}", encoding="utf-8")
            from app.requirement_package import export_requirement_package

            exported = export_requirement_package(ticket_dir=ticket_dir, run_id=0)

            def make_model_text() -> str:
                from app.requirement_intake_model import INTAKE_DOCUMENTS

                body = "基于归档证据的需求分析正文。" * 20
                return json.dumps(
                    {
                        "documents": {name: body for name in INTAKE_DOCUMENTS},
                        "open_questions": ["重打记录是否需要按操作员过滤？"],
                    },
                    ensure_ascii=False,
                )

            task_start = json.dumps({
                "schema_version": "harness-host-session.v1",
                "type": "task.start",
                "request_id": "intake-gen-1",
                "payload": {
                    "schema_version": "harness-external-task.v1",
                    "archive_root": directory,
                    "intake_source": "DFHIS-39999",
                    "intake_include_comments": True,
                    "selected_model_id": "deepseek-reasoner",
                    "worktree_root": "/private/tmp/harness-intake-worktree",
                    "knowledge_home": "/private/tmp/harness-intake-knowledge",
                    "authorization_id": "harness-intake",
                },
            }, ensure_ascii=False) + "\n"
            outbound = io.StringIO()

            class ScriptedHostInput(io.StringIO):
                """先回放 task.start，再把 outbound 中最后的 agent.request 应答成 agent.result。"""

                def readline(self, *args, **kwargs):  # type: ignore[override]
                    if not self.tell():
                        line = super().readline(*args, **kwargs)
                        return line
                    frames = [line for line in outbound.getvalue().splitlines() if line.strip()]
                    request_frame = next(
                        frame for frame in reversed(frames) if json.loads(frame)["type"] == "agent.request"
                    )
                    request_id = json.loads(request_frame)["request_id"]
                    result = AgentBackendResult(
                        exit_code=0,
                        error_code="",
                        event_count=0,
                        final_response_sha256="",
                        canonical_final_response_sha256="",
                        final_response_validated=False,
                        final_response={
                            "schema_version": "harness-intake-documents.v1",
                            "text": make_model_text(),
                        },
                    )
                    return json.dumps({
                        "schema_version": "harness-host-session.v1",
                        "type": "agent.result",
                        "request_id": request_id,
                        "payload": result.to_dict(),
                    }, ensure_ascii=False) + "\n"

            inbound = ScriptedHostInput(task_start)
            with patch(
                "tools.harness_host_server.prepare_yunxiao_harness_package",
                return_value={
                    "ticket_id": "DFHIS-39999",
                    "ticket_dir": str(ticket_dir),
                    "package_dir": exported["package_dir"],
                    "package_status": exported["status"],
                    "pending_count": exported["pending_count"],
                },
            ):
                result = run_external_task_once(input_stream=inbound, output_stream=outbound)

            self.assertEqual("completed", result["status"])
            snapshot = result["snapshot"]
            self.assertEqual("generated", snapshot["generation_status"])
            self.assertEqual(8, snapshot["generated_count"])
            self.assertEqual(["重打记录是否需要按操作员过滤？"], snapshot["open_questions"])
            package = Path(exported["package_dir"])
            drafted = (package / "analysis" / "prd.md").read_text(encoding="utf-8")
            self.assertIn("- 状态：model_generated", drafted)
            manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
            entry = next(item for item in manifest["files"] if item["path"] == "analysis/prd.md")
            self.assertEqual("model_generated", entry["status"])
            lines = [line for line in outbound.getvalue().splitlines() if line.strip()]
            self.assertTrue(any(json.loads(line)["type"] == "agent.request" for line in lines))

    def test_intake_generation_failure_keeps_archive_completed_and_reports_reason(self):
        """模型执行失败不影响归档完成，失败原因作为可恢复事实带回。"""

        with tempfile.TemporaryDirectory(prefix="harness-intake-fail-") as directory:
            ticket_dir = Path(directory) / "DFHIS-39999"
            yunxiao = ticket_dir / "yunxiao"
            yunxiao.mkdir(parents=True)
            (ticket_dir / "runs").mkdir()
            (ticket_dir / "requirement.md").write_text("原始需求：门诊发票重打记录历史。", encoding="utf-8")
            (yunxiao / "snapshot.json").write_text("{}", encoding="utf-8")
            from app.requirement_package import export_requirement_package

            exported = export_requirement_package(ticket_dir=ticket_dir, run_id=0)

            task_start = json.dumps({
                "schema_version": "harness-host-session.v1",
                "type": "task.start",
                "request_id": "intake-gen-2",
                "payload": {
                    "schema_version": "harness-external-task.v1",
                    "archive_root": directory,
                    "intake_source": "DFHIS-39999",
                    "intake_include_comments": True,
                    "selected_model_id": "deepseek-reasoner",
                    "worktree_root": "/private/tmp/harness-intake-worktree",
                    "knowledge_home": "/private/tmp/harness-intake-knowledge",
                    "authorization_id": "harness-intake",
                },
            }, ensure_ascii=False) + "\n"
            outbound = io.StringIO()

            class FailingHostInput(io.StringIO):
                def readline(self, *args, **kwargs):  # type: ignore[override]
                    if not self.tell():
                        return super().readline(*args, **kwargs)
                    frames = [line for line in outbound.getvalue().splitlines() if line.strip()]
                    request_frame = next(
                        frame for frame in reversed(frames) if json.loads(frame)["type"] == "agent.request"
                    )
                    request_id = json.loads(request_frame)["request_id"]
                    result = AgentBackendResult(
                        exit_code=1,
                        error_code="worker_backend_unavailable",
                        event_count=0,
                        final_response_sha256="",
                        canonical_final_response_sha256="",
                        final_response_validated=False,
                    )
                    return json.dumps({
                        "schema_version": "harness-host-session.v1",
                        "type": "agent.result",
                        "request_id": request_id,
                        "payload": result.to_dict(),
                    }) + "\n"

            with patch(
                "tools.harness_host_server.prepare_yunxiao_harness_package",
                return_value={
                    "ticket_id": "DFHIS-39999",
                    "ticket_dir": str(ticket_dir),
                    "package_dir": exported["package_dir"],
                    "package_status": exported["status"],
                    "pending_count": exported["pending_count"],
                },
            ):
                result = run_external_task_once(
                    input_stream=FailingHostInput(task_start),
                    output_stream=outbound,
                )

            self.assertEqual("completed", result["status"])
            self.assertEqual("failed", result["snapshot"]["generation_status"])
            self.assertEqual("worker_backend_unavailable", result["snapshot"]["generation_error_code"])
            self.assertEqual(0, result["snapshot"]["generated_count"])
            drafted = (Path(exported["package_dir"]) / "analysis" / "prd.md").read_text(encoding="utf-8")
            self.assertIn("- 状态：pending", drafted)

    def test_incomplete_understanding_blocks_before_runner_or_agent_request(self):
        with tempfile.TemporaryDirectory(prefix="harness-external-session-") as directory:
            root = Path(directory)
            understanding = _write(
                root / "understanding.json",
                {
                    "schema_version": "requirement-understanding.v1",
                    "status": "blocked_needs_requirement_context",
                    "can_modify": False,
                    "checks": [],
                    "blockers": ["缺少业务背景"],
                    "next_readonly_actions": ["继续读取需求正文"],
                },
            )
            calls: list[object] = []
            session = ExternalTaskSession(
                runner_factory=lambda *_args, **_kwargs: calls.append("runner"),
            )

            result = session.start({
                "schema_version": "harness-external-task.v1",
                "task_contract_path": str(root / "task.json"),
                "understanding_path": str(understanding),
                "worktree_root": "/private/tmp/his_harness_stage_f_external",
                "knowledge_home": "/private/tmp/his_harness_knowledge_external",
                "authorization_id": "desktop-harness-task-001",
            })

            self.assertEqual("blocked", result["status"])
            self.assertEqual("requirement_understanding_incomplete", result["error_code"])
            self.assertEqual([], calls)

    def test_ready_understanding_executes_the_existing_runner_with_the_host_handler(self):
        with tempfile.TemporaryDirectory(prefix="harness-external-session-ready-") as directory:
            root = Path(directory)
            understanding = _write(
                root / "understanding.json",
                {
                    "schema_version": "requirement-understanding.v1",
                    "status": "ready_for_change",
                    "can_modify": True,
                    "checks": [
                        {"name": name, "status": "pass"}
                        for name in (
                            "business_background", "usage_scenario", "target_and_boundary",
                            "project_selection", "entry_and_call_chain", "conversation_alignment",
                            "error_chain_closure", "change_and_impact_scope", "verification_baseline",
                        )
                    ],
                    "blockers": [],
                },
            )
            seen: list[object] = []

            class Runner:
                def execute(self, task, preflight):
                    seen.append((task, preflight))
                    return {"run": {"id": 9, "status": "awaiting_human_confirmation"}, "attempts": [], "events": [], "artifacts": []}

            session = ExternalTaskSession(
                runner_factory=lambda request, host_handler: Runner(),
                task_loader=lambda path: "loaded-task",
                preflight_factory=lambda *, allow_real_agent, authorization_id: f"preflight:{authorization_id}",
            )
            host_handler = lambda request, sink: None

            result = session.execute({
                "schema_version": "harness-external-task.v1",
                "task_contract_path": str(root / "task.json"),
                "understanding_path": str(understanding),
                "worktree_root": "/private/tmp/his_harness_stage_f_external_ready",
                "knowledge_home": "/private/tmp/his_harness_knowledge_external_ready",
                "authorization_id": "desktop-harness-task-002",
            }, host_handler=host_handler)

            self.assertEqual("completed", result["status"])
            self.assertEqual("", result["error_code"])
            self.assertEqual([("loaded-task", "preflight:desktop-harness-task-002")], seen)
            self.assertEqual("awaiting_human_confirmation", result["snapshot"]["status"])

    def test_external_task_stdio_round_trip_emits_agent_request_then_task_result(self):
        with tempfile.TemporaryDirectory(prefix="harness-external-server-") as directory:
            root = Path(directory)
            understanding = _write(
                root / "understanding.json",
                {
                    "schema_version": "requirement-understanding.v1",
                    "status": "ready_for_change",
                    "can_modify": True,
                    "checks": [{"name": name, "status": "pass"} for name in (
                        "business_background", "usage_scenario", "target_and_boundary",
                        "project_selection", "entry_and_call_chain", "conversation_alignment",
                        "error_chain_closure", "change_and_impact_scope", "verification_baseline",
                    )],
                    "blockers": [],
                },
            )
            request = AgentBackendRequest(
                role=AgentBackendRole.WORKER,
                worktree_path=Path("/private/tmp/his_harness_stage_f_external_ready"),
                prompt="execute-only",
                timeout_seconds=30,
                output_contract={"name": "none", "schema_version": "none"},
                capabilities=("source.search",),
            )
            result = AgentBackendResult(
                exit_code=0,
                error_code="",
                event_count=0,
                final_response_sha256="a" * 64,
                canonical_final_response_sha256="b" * 64,
                final_response_validated=False,
            )
            inbound = io.StringIO("\n".join([
                json.dumps({
                    "schema_version": "harness-host-session.v1",
                    "type": "task.start",
                    "request_id": "task-1",
                    "payload": {
                        "schema_version": "harness-external-task.v1",
                        "task_contract_path": str(root / "task.json"),
                        "understanding_path": str(understanding),
                        "worktree_root": "/private/tmp/his_harness_stage_f_external_ready",
                        "knowledge_home": "/private/tmp/his_harness_knowledge_external_ready",
                        "authorization_id": "desktop-harness-task-003",
                    },
                }),
                json.dumps({
                    "schema_version": "harness-host-session.v1",
                    "type": "agent.result",
                    "request_id": request_hash(request),
                    "payload": result.to_dict(),
                }),
                "",
            ]))
            outbound = io.StringIO()

            class Runner:
                def __init__(self, host_handler):
                    self.host_handler = host_handler

                def execute(self, task, preflight):
                    actual = self.host_handler(request, None)
                    self.actual = actual
                    return {"run": {"id": 10, "task_key": "DFHIS-32178", "status": "awaiting_human_confirmation", "contract_hash": "c" * 64, "initial_head": "d" * 40}, "attempts": [], "events": [], "artifacts": []}

            result_payload = run_external_task_once(
                input_stream=inbound,
                output_stream=outbound,
                runner_factory=lambda start, host_handler: Runner(host_handler),
                task_loader=lambda path: "loaded-task",
                preflight_factory=lambda *, allow_real_agent, authorization_id: "preflight",
            )

            messages = [json.loads(line) for line in outbound.getvalue().splitlines()]
            self.assertEqual("completed", result_payload["status"])
            self.assertEqual(["agent.request", "task.result"], [item["type"] for item in messages])
            self.assertEqual(request.to_dict(), messages[0]["payload"])
            self.assertEqual("completed", messages[1]["payload"]["status"])


if __name__ == "__main__":
    unittest.main()
