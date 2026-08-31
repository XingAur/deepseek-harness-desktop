from __future__ import annotations

import unittest

from app.capability_contracts import CapabilityResult, MutationLevel
from app.provider_execution import ProviderExecutionRequest


class _FakeRuntime:
    def __init__(self, result: CapabilityResult) -> None:
        self.result = result
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        return type("Execution", (), {"result": self.result})()


class _Context:
    profile_id = 1
    profile_key = "readonly"

    def __init__(self) -> None:
        self.targets: list[str] = []

    def credential(self, _field: str) -> str:
        raise AssertionError("MCP adapter must not ask Harness for credential values")

    def record_network_dispatch(self, target: str, *, simulated: bool) -> None:
        self.targets.append(target)
        if simulated:
            raise AssertionError("primary MCP execution cannot be relabelled simulated")


def _result(status: str = "success") -> CapabilityResult:
    return CapabilityResult(
        request_id="mcp-yunxiao-1",
        capability="workitem.read",
        provider="yunxiao",
        status=status,
        mutation_level=MutationLevel.L1,
        changed=False,
        summary="done" if status == "success" else "MCP_TRANSPORT_UNAVAILABLE",
        data={"item": {"id": "DFHIS-1"}} if status == "success" else {},
        evidence=({"ref": "mcp-evidence:1"},) if status == "success" else (),
        warnings=(),
        blockers=() if status == "success" else ("MCP transport unavailable",),
        audit={"error_code": "" if status == "success" else "MCP_TRANSPORT_UNAVAILABLE"},
    )


class McpPrimaryProviderAdapterTests(unittest.TestCase):
    def test_workitem_read_maps_to_mcp_without_harness_credential_resolution(self) -> None:
        from app.providers.mcp_readonly import McpReadonlyProviderAdapter

        runtime = _FakeRuntime(_result())
        adapter = McpReadonlyProviderAdapter("yunxiao", runtime_loader=lambda: runtime)
        context = _Context()
        output = adapter.execute(
            ProviderExecutionRequest(
                plan_id=1,
                actor="user",
                action="workitem.read",
                parameters={
                    "organization_alias": "org",
                    "project_alias": "DFHIS",
                    "work_item_alias": "DFHIS-1",
                    "timeout_seconds": 15,
                },
            ),
            context,
        )

        self.assertEqual("mcp", output["execution_kind"])
        self.assertEqual("mcp-evidence:1", output["evidence_ref"])
        self.assertEqual(1, len(runtime.requests))
        request = runtime.requests[0]
        self.assertEqual("workitem.read", request.capability)
        self.assertEqual("yunxiao", request.provider)
        self.assertFalse(request.authorization.explicit)
        self.assertEqual({"workitem:read"}, set(request.authorization.scope))
        self.assertEqual("DFHIS-1", request.input["work_item_id"])
        self.assertEqual(["org.dfhis-1"], context.targets)

    def test_mcp_failure_is_fail_closed_and_never_calls_a_fallback(self) -> None:
        from app.providers.mcp_readonly import (
            McpPrimaryProviderError,
            McpReadonlyProviderAdapter,
        )

        runtime = _FakeRuntime(_result("unsupported"))
        fallback_calls: list[object] = []
        adapter = McpReadonlyProviderAdapter(
            "yunxiao",
            runtime_loader=lambda: runtime,
            fallback=lambda *args: fallback_calls.append(args),
        )
        with self.assertRaises(McpPrimaryProviderError) as raised:
            adapter.execute(
                ProviderExecutionRequest(
                    plan_id=1,
                    actor="user",
                    action="workitem.read",
                    parameters={
                        "organization_alias": "org",
                        "project_alias": "DFHIS",
                        "work_item_alias": "DFHIS-1",
                    },
                ),
                _Context(),
            )

        self.assertEqual("mcp_transport_unavailable", raised.exception.provider_reason)
        self.assertEqual([], fallback_calls)

    def test_remote_write_is_not_an_mcp_read_fallback(self) -> None:
        from app.providers.mcp_readonly import McpPrimaryProviderError, McpReadonlyProviderAdapter

        adapter = McpReadonlyProviderAdapter("yunxiao", runtime_loader=lambda: _FakeRuntime(_result()))
        with self.assertRaises(McpPrimaryProviderError) as raised:
            adapter.execute(
                ProviderExecutionRequest(
                    plan_id=2,
                    actor="user",
                    action="workitem.comment.write",
                    parameters={},
                ),
                _Context(),
            )
        self.assertEqual("mcp_write_capability_unavailable", raised.exception.provider_reason)


if __name__ == "__main__":
    unittest.main()
