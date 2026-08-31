from __future__ import annotations

import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from app.capability_contracts import CapabilityRequest
from app.mcp_audit import (
    InMemoryMcpAuditSink,
    InMemoryMcpEvidenceSink,
    McpAuditError,
)
from app.mcp_capability_registry import McpCapabilityRegistry
from app.mcp_contracts import MCP_RESULT_SCHEMA_VERSION
from app.mcp_gateway import McpGateway
from app.mcp_transport import McpTransportUnavailable


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config/mcp_capabilities.json"


def request(
    *,
    capability: str = "workitem.read",
    provider: str = "yunxiao",
    mutation_level: str = "L1",
    scopes: list[str] | None = None,
    input_payload: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
) -> CapabilityRequest:
    return CapabilityRequest.from_dict(
        {
            "schema_version": "his-capability-request.v1",
            "request_id": "request-001",
            "capability": capability,
            "provider": provider,
            "mode": "preview",
            "mutation_level": mutation_level,
            "authorization": {
                "explicit": False,
                "scope": ["workitem:read"] if scopes is None else scopes,
            },
            "input": {
                "work_item_id": "DFHIS-100",
                "include_comments": True,
                "include_attachments": False,
                "page_cursor": "",
                "page_size": 20,
            }
            if input_payload is None
            else dict(input_payload),
            "context": {"task_id": "task-1", "run_id": "run-1"}
            if context is None
            else dict(context),
        }
    )


def success_envelope(*, data: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": MCP_RESULT_SCHEMA_VERSION,
        "request_id": "request-001",
        "capability": "workitem.read",
        "provider": "yunxiao",
        "status": "success",
        "data": {"item": {"id": "DFHIS-100", "title": "Requirement"}}
        if data is None
        else dict(data),
        "evidence_ref": "source-evidence:DFHIS-100:v1",
        "source": {
            "system": "yunxiao",
            "object_id": "DFHIS-100",
            "version": "1",
            "observed_at": "2026-08-30T00:00:00Z",
        },
        "freshness": {"status": "fresh", "expires_at": "2026-08-30T00:05:00Z"},
        "pagination": {"truncated": False, "next_cursor": ""},
        "redaction": {"applied": False, "fields": []},
        "error": {"code": "", "retryable": False, "recovery": ""},
        "trace": {
            "mcp_server": "yunxiao",
            "tool": "workitem_get",
            "server_version": "1.0.0",
            "trace_id": "request-001",
        },
    }


class FakeTransport:
    def __init__(self, response: Mapping[str, Any] | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def call(self, **kwargs: Any) -> Mapping[str, Any]:
        self.calls.append(copy.deepcopy(kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return copy.deepcopy(self.response)


class McpGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        loaded = McpCapabilityRegistry.from_file(MANIFEST, harness_root=ROOT)
        self.base_descriptor = loaded.resolve("workitem.read", "yunxiao")

    def gateway(
        self,
        response: Mapping[str, Any] | Exception | None = None,
        *,
        descriptor=None,
    ) -> tuple[McpGateway, FakeTransport, InMemoryMcpEvidenceSink, InMemoryMcpAuditSink]:
        selected = self.base_descriptor if descriptor is None else descriptor
        registry = McpCapabilityRegistry([selected])
        transport = FakeTransport(success_envelope() if response is None else response)
        evidence = InMemoryMcpEvidenceSink()
        audit = InMemoryMcpAuditSink()
        return McpGateway(
            registry=registry,
            transport=transport,
            evidence_sink=evidence,
            audit_sink=audit,
        ), transport, evidence, audit

    def enabled(self, **changes: Any):
        return replace(
            self.base_descriptor,
            enabled=True,
            disabled_reason="",
            **changes,
        )

    def test_disabled_and_missing_capabilities_are_unsupported_without_transport(self) -> None:
        disabled_descriptor = replace(
            self.base_descriptor,
            enabled=False,
            disabled_reason="test_disabled_descriptor",
        )
        gateway, transport, _, audit = self.gateway(descriptor=disabled_descriptor)
        disabled = gateway.execute(request())
        missing = gateway.execute(request(capability="missing.read", provider="missing"))

        self.assertEqual("unsupported", disabled.result.status)
        self.assertEqual("MCP_CAPABILITY_DISABLED", disabled.result.audit["error_code"])
        self.assertEqual("unsupported", missing.result.status)
        self.assertEqual("MCP_CAPABILITY_NOT_FOUND", missing.result.audit["error_code"])
        self.assertEqual([], transport.calls)
        self.assertEqual(2, len(audit.events))

    def test_scope_and_any_request_above_l1_are_denied_before_transport(self) -> None:
        gateway, transport, _, audit = self.gateway(descriptor=self.enabled())
        scope_denied = gateway.execute(request(scopes=[]))

        malformed_level = replace(self.enabled(), mutation_level=request(mutation_level="L3").mutation_level)
        high_gateway, high_transport, _, high_audit = self.gateway(descriptor=malformed_level)
        high = high_gateway.execute(request(mutation_level="L3"))

        self.assertEqual("blocked", scope_denied.result.status)
        self.assertEqual("MCP_PERMISSION_DENIED", scope_denied.result.audit["error_code"])
        self.assertEqual("blocked", high.result.status)
        self.assertEqual("MCP_MUTATION_LEVEL_DENIED", high.result.audit["error_code"])
        self.assertEqual([], transport.calls)
        self.assertEqual([], high_transport.calls)
        self.assertEqual("blocked", audit.events[0]["status"])
        self.assertEqual("blocked", high_audit.events[0]["status"])

    def test_valid_request_calls_transport_once_with_validated_input_only(self) -> None:
        gateway, transport, _, _ = self.gateway(descriptor=self.enabled())
        item = request(
            context={
                "task_id": "task-1",
                "authorization": "Bearer must-not-cross",
                "dsn": "must-not-cross",
                "raw_environment": {"SECRET": "must-not-cross"},
            }
        )

        result = gateway.execute(item)

        self.assertEqual("success", result.result.status)
        self.assertEqual(1, len(transport.calls))
        call = transport.calls[0]
        self.assertEqual(dict(item.input), call["arguments"])
        rendered = json.dumps(call, sort_keys=True)
        for forbidden in ("authorization", "credential", "dsn", "environment", "must-not-cross"):
            self.assertNotIn(forbidden, rendered.lower())

    def test_arguments_are_exactly_validated_before_transport(self) -> None:
        invalid_inputs = (
            {"work_item_id": "DFHIS-100"},
            {
                "work_item_id": "DFHIS-100",
                "include_comments": True,
                "include_attachments": False,
                "page_cursor": "",
                "page_size": 20,
                "authorization": "not-forwarded",
            },
        )
        for payload in invalid_inputs:
            with self.subTest(fields=sorted(payload)):
                gateway, transport, evidence, audit = self.gateway(descriptor=self.enabled())
                execution = gateway.execute(request(input_payload=payload))
                self.assertEqual("blocked", execution.result.status)
                self.assertEqual("MCP_ARGUMENTS_INVALID", execution.result.audit["error_code"])
                self.assertEqual([], transport.calls)
                self.assertEqual((), evidence.records)
                self.assertEqual(1, len(audit.events))

    def test_oversized_result_is_rejected_before_evidence_storage(self) -> None:
        descriptor = self.enabled(max_result_bytes=1024)
        response = success_envelope(data={"text": "x" * 2048})
        gateway, transport, evidence, audit = self.gateway(response, descriptor=descriptor)

        result = gateway.execute(request())

        self.assertEqual("blocked", result.result.status)
        self.assertEqual("MCP_RESULT_TOO_LARGE", result.result.audit["error_code"])
        self.assertEqual(1, len(transport.calls))
        self.assertEqual((), evidence.records)
        self.assertEqual("blocked", audit.events[0]["status"])

    def test_invalid_identity_and_secret_result_are_rejected_without_storage(self) -> None:
        identity = success_envelope()
        identity["provider"] = "gitlab"
        secret = success_envelope(data={"item": {"token": "must-not-store"}})
        for response in (identity, secret):
            with self.subTest(kind="identity" if response is identity else "secret"):
                gateway, transport, evidence, audit = self.gateway(
                    response, descriptor=self.enabled()
                )
                execution = gateway.execute(request())
                self.assertEqual("blocked", execution.result.status)
                self.assertEqual("MCP_RESULT_INVALID", execution.result.audit["error_code"])
                self.assertEqual(1, len(transport.calls))
                self.assertEqual((), evidence.records)
                self.assertEqual(1, len(audit.events))

    def test_success_stores_validated_snapshot_and_returns_harness_owned_ref(self) -> None:
        response = success_envelope()
        gateway, _, evidence, audit = self.gateway(response, descriptor=self.enabled())

        execution = gateway.execute(request())
        response["data"]["item"]["title"] = "mutated-after-call"

        self.assertEqual("success", execution.result.status)
        self.assertFalse(execution.result.changed)
        ref = execution.result.evidence[0]["ref"]
        self.assertTrue(ref.startswith("mcp-evidence:request-001:"))
        self.assertEqual(ref, audit.events[0]["evidence_ref"])
        self.assertEqual("source-evidence:DFHIS-100:v1", evidence.records[0]["payload"]["evidence_ref"])
        self.assertEqual("Requirement", evidence.records[0]["payload"]["data"]["item"]["title"])
        self.assertNotIn("data", audit.events[0])

    def test_failed_denied_unavailable_and_invalid_envelopes_map_to_existing_statuses(self) -> None:
        expected = {
            "failed": "failed",
            "denied": "blocked",
            "unavailable": "unsupported",
            "invalid": "blocked",
        }
        for source_status, result_status in expected.items():
            with self.subTest(status=source_status):
                response = success_envelope(data={})
                response.update(status=source_status, evidence_ref="")
                response["error"] = {
                    "code": "REMOTE_FAILURE",
                    "retryable": source_status == "unavailable",
                    "recovery": "Retry after source recovery.",
                }
                gateway, _, evidence, audit = self.gateway(response, descriptor=self.enabled())
                execution = gateway.execute(request())
                self.assertEqual(result_status, execution.result.status)
                self.assertFalse(execution.result.changed)
                self.assertEqual((), evidence.records)
                self.assertEqual(result_status, audit.events[0]["status"])

    def test_transport_exception_is_unavailable_and_never_retried(self) -> None:
        gateway, transport, evidence, audit = self.gateway(
            McpTransportUnavailable("sentinel-secret"), descriptor=self.enabled()
        )

        execution = gateway.execute(request())

        self.assertEqual("unsupported", execution.result.status)
        self.assertEqual("MCP_TRANSPORT_UNAVAILABLE", execution.result.audit["error_code"])
        self.assertIn("transport", execution.result.blockers[0].lower())
        self.assertEqual(1, len(transport.calls))
        self.assertEqual((), evidence.records)
        self.assertNotIn("sentinel-secret", json.dumps(audit.events))

    def test_audit_context_is_allowlisted_scalar_metadata_only(self) -> None:
        gateway, _, _, audit = self.gateway(descriptor=self.enabled())
        execution = gateway.execute(
            request(
                context={
                    "task_id": "task-1",
                    "run_id": "run-1",
                    "project_id": "project-1",
                    "repository_id": "repository-1",
                    "context_pack_id": "pack-1",
                    "payload": {"rows": ["must-not-audit"]},
                }
            )
        )

        self.assertEqual("success", execution.result.status)
        event = audit.events[0]
        self.assertEqual("pack-1", event["context_pack_id"])
        self.assertNotIn("payload", event)
        self.assertNotIn("must-not-audit", json.dumps(event))

    def test_in_memory_sinks_copy_payloads_and_reject_unsafe_audit_shape(self) -> None:
        evidence = InMemoryMcpEvidenceSink()
        payload = {"data": {"title": "safe"}}
        first = evidence.store(
            request_id="request-001",
            capability="workitem.read",
            provider="yunxiao",
            payload=payload,
        )
        second = evidence.store(
            request_id="request-001",
            capability="workitem.read",
            provider="yunxiao",
            payload=payload,
        )
        payload["data"]["title"] = "mutated"

        self.assertEqual(first, second)
        self.assertEqual("safe", evidence.records[0]["payload"]["data"]["title"])
        audit = InMemoryMcpAuditSink()
        with self.assertRaises(McpAuditError):
            audit.record({"unexpected": "field"})
        with self.assertRaises(McpAuditError):
            audit.record(
                {
                    key: ("Authorization: Bearer abc" if key == "task_id" else "")
                    for key in audit.allowed_fields
                }
            )


if __name__ == "__main__":
    unittest.main()
