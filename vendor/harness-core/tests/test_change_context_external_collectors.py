from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.capability_contracts import CapabilityResult, MutationLevel
from app.change_context_collectors import ChangeScopeCollector, GitLabProjectGraphCollector
from app.change_context_contracts import McpEvidenceReceipt
from app.task_context import TaskIntentContext


def mcp_result(request, *, source: str, version: str = "v1", status: str = "success", changed: bool = False):
    return CapabilityResult(
        request_id=request.request_id,
        capability=request.capability,
        provider=request.provider,
        status=status,
        mutation_level=MutationLevel.L1,
        changed=changed,
        summary="fixture",
        data={"operation": request.input.get("operation", "workitem"), "id": source},
        evidence=({"ref": f"mcp-evidence:{request.request_id}:abc"},) if status == "success" else (),
        warnings=(),
        blockers=() if status == "success" else ("unavailable",),
        audit={
            "error_code": "" if status == "success" else "MCP_TRANSPORT_UNAVAILABLE",
            "execution_kind": "mcp",
            "source_identity": source,
            "source_version": version,
            "freshness_status": "fresh",
            "freshness_expires_at": "2026-08-30T01:00:00Z",
            "collected_at": "2026-08-30T00:00:00Z",
        },
    )


class FakeRuntime:
    def __init__(self, responder=mcp_result) -> None:
        self.responder = responder
        self.calls = []

    def execute(self, request):
        self.calls.append(request)
        if request.capability == "workitem.read":
            source = f"yunxiao:{request.input['work_item_id']}"
        else:
            source = f"gitlab:{request.input['project']}"
        return SimpleNamespace(result=self.responder(request, source=source))


class ChangeContextExternalCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.intent = TaskIntentContext(
            background="需求背景",
            goal="调整页面",
            scenarios=("打开页面",),
            desired_outcome="页面正确",
        )
        self.requirement = {
            "source_type": "yunxiao",
            "ticket_id": "DFHIS-1",
            "revision": "rev-1",
            "comments": [],
            "attachments": [],
        }

    def test_yunxiao_scope_performs_exactly_one_mcp_read_when_receipt_missing(self) -> None:
        runtime = FakeRuntime()
        collected = ChangeScopeCollector(runtime=runtime).collect(
            task_context=self.intent,
            normalized_requirement_evidence=self.requirement,
            current_user_correction="",
            calibrated_scope={"do": "调整页面"},
            task_id="task-1",
            run_id="run-1",
        )
        self.assertEqual("complete", collected.status)
        self.assertEqual(1, len(runtime.calls))
        request = runtime.calls[0]
        self.assertEqual(("workitem.read", "yunxiao", "preview", MutationLevel.L1), (request.capability, request.provider, request.mode, request.mutation_level))
        self.assertEqual(["workitem:read"], request.to_dict()["authorization"]["scope"])
        self.assertEqual("DFHIS-1", request.input["work_item_id"])
        self.assertIn("mcp_receipt", collected.payload)

    def test_current_provider_stage_receipt_is_reused_without_second_call(self) -> None:
        runtime = FakeRuntime()
        request = SimpleNamespace(
            request_id="provider-stage-1",
            capability="workitem.read",
            provider="yunxiao",
            input={"work_item_id": "DFHIS-1"},
        )
        receipt = McpEvidenceReceipt.from_capability_result(
            mcp_result(request, source="yunxiao:DFHIS-1")
        )
        collected = ChangeScopeCollector(runtime=runtime).collect(
            task_context=self.intent,
            normalized_requirement_evidence=self.requirement,
            current_user_correction="",
            calibrated_scope={},
            mcp_receipt=receipt,
        )
        self.assertEqual("complete", collected.status)
        self.assertEqual([], runtime.calls)

    def test_provider_or_stale_receipt_is_rejected_without_fallback(self) -> None:
        runtime = FakeRuntime(lambda request, source: mcp_result(request, source=source, status="failed"))
        collected = ChangeScopeCollector(runtime=runtime).collect(
            task_context=self.intent,
            normalized_requirement_evidence=self.requirement,
            current_user_correction="",
            calibrated_scope={},
            task_id="task-1",
            run_id="run-1",
        )
        self.assertEqual("incomplete", collected.status)
        self.assertEqual(1, len(runtime.calls))
        self.assertIn("BLOCKED_CONTEXT_SOURCE_UNAVAILABLE", "\n".join(collected.blockers))

    def test_gitlab_remote_baseline_uses_project_and_commit_mcp_only(self) -> None:
        runtime = FakeRuntime()
        collected = GitLabProjectGraphCollector(runtime=runtime).collect(
            project="group/project",
            ref="RC_2.16.1_250514",
            object_id="a" * 40,
            task_id="task-1",
            run_id="run-1",
            remote_baseline_required=True,
        )
        self.assertEqual("complete", collected.status)
        self.assertEqual(["project", "commit"], [item.input["operation"] for item in runtime.calls])
        self.assertEqual({"project", "operation", "ref", "path", "object_id"}, set(runtime.calls[0].input))
        self.assertEqual("group/project", runtime.calls[1].input["project"])
        self.assertEqual("a" * 40, runtime.calls[1].input["object_id"])

    def test_gitlab_not_required_makes_no_call_and_failure_has_no_fallback(self) -> None:
        runtime = FakeRuntime()
        skipped = GitLabProjectGraphCollector(runtime=runtime).collect(
            project="group/project", ref="main", object_id="a" * 40,
            task_id="task-1", run_id="run-1", remote_baseline_required=False,
        )
        self.assertEqual("complete", skipped.status)
        self.assertEqual([], runtime.calls)
        failed_runtime = FakeRuntime(lambda request, source: mcp_result(request, source=source, status="failed"))
        failed = GitLabProjectGraphCollector(runtime=failed_runtime).collect(
            project="group/project", ref="main", object_id="a" * 40,
            task_id="task-1", run_id="run-1", remote_baseline_required=True,
        )
        self.assertEqual("incomplete", failed.status)
        self.assertEqual(1, len(failed_runtime.calls))


if __name__ == "__main__":
    unittest.main()
