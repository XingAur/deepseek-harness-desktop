from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from app.capability_contracts import CapabilityRequest, CapabilityResult
from app.capability_runtime import CapabilityExecution, CapabilityPreflight
from app.capability_service import CapabilityService
from app.mcp_capability_registry import McpCapabilityRegistry
from app.mcp_capability_runtime import McpCapabilityRuntime
from app.mcp_gateway import McpGatewayExecution


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config/mcp_capabilities.json"


def request() -> CapabilityRequest:
    return CapabilityRequest.from_dict(
        {
            "schema_version": "his-capability-request.v1",
            "request_id": "runtime-request-1",
            "capability": "workitem.read",
            "provider": "yunxiao",
            "mode": "preview",
            "mutation_level": "L1",
            "authorization": {"explicit": False, "scope": ["workitem:read"]},
            "input": {
                "work_item_id": "DFHIS-100",
                "include_comments": True,
                "include_attachments": False,
                "page_cursor": "",
                "page_size": 20,
            },
            "context": {},
        }
    )


def result(item: CapabilityRequest, *, status: str = "success") -> CapabilityResult:
    return CapabilityResult(
        request_id=item.request_id,
        capability=item.capability,
        provider=item.provider,
        status=status,
        mutation_level=item.mutation_level,
        changed=False,
        summary="fixture",
        data={"id": "DFHIS-100"} if status == "success" else {},
        evidence=({"ref": "mcp-evidence:fixture"},) if status == "success" else (),
        warnings=(),
        blockers=(),
        audit={"error_code": ""},
    )


class FakeGateway:
    def __init__(self, execution: McpGatewayExecution) -> None:
        self.execution = execution
        self.calls: list[CapabilityRequest] = []

    def execute(self, item: CapabilityRequest) -> McpGatewayExecution:
        self.calls.append(item)
        return self.execution


class McpCapabilityRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        loaded = McpCapabilityRegistry.from_file(MANIFEST, harness_root=ROOT)
        self.enabled = loaded.resolve("workitem.read", "yunxiao")
        self.disabled = replace(
            self.enabled,
            enabled=False,
            disabled_reason="test_disabled_descriptor",
        )
        self.item = request()

    def runtime(self, descriptor=None, gateway_result: CapabilityResult | None = None):
        selected = self.enabled if descriptor is None else descriptor
        gateway = FakeGateway(
            McpGatewayExecution(
                descriptor=selected,
                result=result(self.item) if gateway_result is None else gateway_result,
                duration_ms=7,
            )
        )
        runtime = McpCapabilityRuntime(
            registry=McpCapabilityRegistry([selected]),
            gateway=gateway,
        )
        return runtime, gateway

    def test_preflight_exposes_existing_descriptor_contract_without_filesystem_uri(self) -> None:
        runtime, gateway = self.runtime()

        preflight = runtime.preflight(self.item)

        self.assertIsInstance(preflight, CapabilityPreflight)
        self.assertEqual("mcp:yunxiao", preflight.descriptor.plugin)
        self.assertEqual("workitem-read.v1", preflight.descriptor.plugin_version)
        self.assertEqual("yunxiao", preflight.descriptor.provider)
        self.assertEqual("none", preflight.descriptor.credential_class)
        self.assertIsNone(preflight.descriptor.entrypoint)
        self.assertEqual(("workitem:read",), preflight.descriptor.scopes)
        self.assertTrue(preflight.permission.allowed)
        self.assertEqual([], gateway.calls)

    def test_explicitly_disabled_descriptor_is_not_delegated(self) -> None:
        runtime, gateway = self.runtime(descriptor=self.disabled)

        execution = runtime.execute(self.item)

        self.assertIsInstance(execution, CapabilityExecution)
        self.assertEqual("unsupported", execution.result.status)
        self.assertEqual("MCP_CAPABILITY_DISABLED", execution.result.audit["error_code"])
        self.assertEqual([], gateway.calls)

    def test_enabled_descriptor_delegates_once_and_preserves_gateway_result_exactly(self) -> None:
        expected = result(self.item)
        runtime, gateway = self.runtime(gateway_result=expected)

        execution = runtime.execute(self.item)

        self.assertEqual([self.item], gateway.calls)
        self.assertIs(expected, execution.result)
        self.assertEqual(7, execution.duration_ms)
        self.assertEqual("mcp:yunxiao", execution.descriptor.plugin)

    def test_non_empty_environment_and_timeout_override_are_rejected_before_gateway(self) -> None:
        cases = (
            ({"MCP_TOKEN": "must-not-cross"}, None, "MCP_ENVIRONMENT_FORBIDDEN"),
            (None, 31, "MCP_TIMEOUT_POLICY_MISMATCH"),
        )
        for environment, timeout, error_code in cases:
            with self.subTest(error_code=error_code):
                runtime, gateway = self.runtime()
                execution = runtime.execute(
                    self.item,
                    environment=environment,
                    timeout_seconds=timeout,
                )
                self.assertEqual("blocked", execution.result.status)
                self.assertEqual(error_code, execution.result.audit["error_code"])
                self.assertEqual([], gateway.calls)

        runtime, gateway = self.runtime()
        execution = runtime.execute(self.item, environment={}, timeout_seconds=30)
        self.assertEqual("success", execution.result.status)
        self.assertEqual([self.item], gateway.calls)

    def test_capability_service_enforce_accepts_the_adapter_without_route_changes(self) -> None:
        runtime, gateway = self.runtime()

        routed = CapabilityService(runtime, routing_mode="enforce").route(self.item)

        self.assertEqual("capability", routed.selected)
        self.assertEqual("success", routed.result["status"])
        self.assertEqual([self.item], gateway.calls)


if __name__ == "__main__":
    unittest.main()
