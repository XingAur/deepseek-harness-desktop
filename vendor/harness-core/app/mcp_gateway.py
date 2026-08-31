from __future__ import annotations

import copy
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from app.capability_contracts import CapabilityRequest, CapabilityResult, MutationLevel
from app.mcp_audit import McpAuditSink, McpEvidenceSink
from app.mcp_capability_registry import (
    McpCapabilityDescriptor,
    McpCapabilityNotFound,
    McpCapabilityRegistry,
)
from app.mcp_contracts import (
    McpContractError,
    canonical_json_size,
    mcp_envelope_to_dict,
    parse_mcp_result_envelope,
)
from app.mcp_schema_validation import McpSchemaValidationError, validate_mcp_arguments
from app.mcp_transport import McpTransport
from app.sensitive_text import contains_sensitive_text


_AUDIT_CONTEXT_KEYS = (
    "task_id",
    "run_id",
    "project_id",
    "repository_id",
    "context_pack_id",
)
_AUDIT_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")


@dataclass(frozen=True)
class McpGatewayExecution:
    descriptor: McpCapabilityDescriptor | None
    result: CapabilityResult
    duration_ms: int


class McpGateway:
    def __init__(
        self,
        *,
        registry: McpCapabilityRegistry,
        transport: McpTransport,
        evidence_sink: McpEvidenceSink,
        audit_sink: McpAuditSink,
    ) -> None:
        self.registry = registry
        self.transport = transport
        self.evidence_sink = evidence_sink
        self.audit_sink = audit_sink

    def execute(self, request: CapabilityRequest) -> McpGatewayExecution:
        if not isinstance(request, CapabilityRequest):
            raise TypeError("request must be a CapabilityRequest")
        started = time.monotonic()
        descriptor: McpCapabilityDescriptor | None = None
        trace_id = self._audit_identifier(request.request_id)
        try:
            descriptor = self.registry.resolve(request.capability, request.provider)
        except McpCapabilityNotFound:
            return self._finish(
                descriptor=None,
                request=request,
                status="unsupported",
                error_code="MCP_CAPABILITY_NOT_FOUND",
                summary="MCP capability/provider is not registered.",
                blockers=("MCP capability/provider is not registered.",),
                retryable=False,
                trace_id=trace_id,
                started=started,
            )

        if request.mutation_level > MutationLevel.L1 or descriptor.mutation_level > MutationLevel.L1:
            return self._finish(
                descriptor=descriptor,
                request=request,
                status="blocked",
                error_code="MCP_MUTATION_LEVEL_DENIED",
                summary="Phase 1A permits only L0/L1 MCP reads.",
                blockers=("Phase 1A permits only L0/L1 MCP reads.",),
                retryable=False,
                trace_id=trace_id,
                started=started,
            )
        if not descriptor.enabled:
            return self._finish(
                descriptor=descriptor,
                request=request,
                status="unsupported",
                error_code="MCP_CAPABILITY_DISABLED",
                summary="MCP capability is registered but disabled.",
                blockers=("MCP capability is registered but disabled.",),
                retryable=False,
                trace_id=trace_id,
                started=started,
            )
        if (
            request.mutation_level != descriptor.mutation_level
            or not set(descriptor.required_scopes).issubset(request.authorization.scope)
        ):
            return self._finish(
                descriptor=descriptor,
                request=request,
                status="blocked",
                error_code="MCP_PERMISSION_DENIED",
                summary="MCP request authorization does not satisfy the registered descriptor.",
                blockers=("MCP request authorization or scope is insufficient.",),
                retryable=False,
                trace_id=trace_id,
                started=started,
            )
        if not trace_id:
            return self._finish(
                descriptor=descriptor,
                request=request,
                status="blocked",
                error_code="MCP_REQUEST_ID_INVALID",
                summary="MCP request identity is not safe for transport.",
                blockers=("MCP request identity is invalid.",),
                retryable=False,
                trace_id="",
                started=started,
            )

        try:
            validate_mcp_arguments(descriptor.input_schema, request.input)
            arguments = copy.deepcopy(dict(request.input))
        except (MemoryError, McpSchemaValidationError, TypeError, ValueError):
            return self._finish(
                descriptor=descriptor,
                request=request,
                status="blocked",
                error_code="MCP_ARGUMENTS_INVALID",
                summary="MCP arguments do not satisfy the registered contract.",
                blockers=("MCP arguments are invalid.",),
                retryable=False,
                trace_id=trace_id,
                started=started,
            )

        try:
            raw_result = self.transport.call(
                server=descriptor.server,
                tool=descriptor.tool,
                arguments=arguments,
                timeout_seconds=descriptor.timeout_seconds,
                trace_id=trace_id,
            )
        except Exception:
            return self._finish(
                descriptor=descriptor,
                request=request,
                status="unsupported",
                error_code="MCP_TRANSPORT_UNAVAILABLE",
                summary="MCP transport is unavailable.",
                blockers=("MCP transport is unavailable; use the governed read-only fallback.",),
                retryable=True,
                trace_id=trace_id,
                started=started,
            )

        try:
            if canonical_json_size(raw_result) > descriptor.max_result_bytes:
                return self._finish(
                    descriptor=descriptor,
                    request=request,
                    status="blocked",
                    error_code="MCP_RESULT_TOO_LARGE",
                    summary="MCP result exceeds the registered byte limit.",
                    blockers=("MCP result is too large.",),
                    retryable=False,
                    trace_id=trace_id,
                    started=started,
                )
            validate_mcp_arguments(descriptor.result_schema, raw_result)
            envelope = parse_mcp_result_envelope(
                raw_result,
                expected_request_id=request.request_id,
                expected_capability=request.capability,
                expected_provider=request.provider,
            )
            if (
                envelope.trace.mcp_server != descriptor.server
                or envelope.trace.tool != descriptor.tool
                or envelope.trace.trace_id != trace_id
            ):
                raise McpContractError("MCP trace identity mismatch")
            snapshot = mcp_envelope_to_dict(envelope)
        except (McpContractError, McpSchemaValidationError, TypeError, ValueError):
            return self._finish(
                descriptor=descriptor,
                request=request,
                status="blocked",
                error_code="MCP_RESULT_INVALID",
                summary="MCP result did not satisfy the registered result contract.",
                blockers=("MCP result is invalid.",),
                retryable=False,
                trace_id=trace_id,
                started=started,
            )

        if envelope.status == "invalid":
            return self._finish(
                descriptor=descriptor,
                request=request,
                status="blocked",
                error_code="MCP_RESULT_INVALID",
                summary="MCP source marked the result invalid.",
                blockers=(envelope.error.recovery,),
                retryable=envelope.error.retryable,
                trace_id=trace_id,
                started=started,
            )
        if envelope.status != "success":
            mapped_status = {
                "failed": "failed",
                "denied": "blocked",
                "unavailable": "unsupported",
            }[envelope.status]
            blocker = envelope.error.recovery
            if envelope.status == "denied":
                blocker = f"Authorization denied: {blocker}"
            return self._finish(
                descriptor=descriptor,
                request=request,
                status=mapped_status,
                error_code=envelope.error.code,
                summary=envelope.error.code,
                blockers=(blocker,),
                retryable=envelope.error.retryable,
                trace_id=trace_id,
                started=started,
            )

        try:
            evidence_ref = self.evidence_sink.store(
                request_id=request.request_id,
                capability=request.capability,
                provider=request.provider,
                payload=snapshot,
            )
        except Exception:
            return self._finish(
                descriptor=descriptor,
                request=request,
                status="failed",
                error_code="MCP_EVIDENCE_STORE_FAILED",
                summary="Validated MCP evidence could not be stored.",
                blockers=("Harness evidence storage failed.",),
                retryable=False,
                trace_id=trace_id,
                started=started,
            )
        return self._finish(
            descriptor=descriptor,
            request=request,
            status="success",
            error_code="",
            summary="MCP read completed with validated evidence.",
            data=snapshot["data"],
            evidence_ref=evidence_ref,
            source_identity=f"{envelope.source.system}:{envelope.source.object_id}",
            source_version=envelope.source.version,
            freshness_status=envelope.freshness.status,
            freshness_expires_at=envelope.freshness.expires_at,
            blockers=(),
            retryable=False,
            trace_id=trace_id,
            started=started,
        )

    def _finish(
        self,
        *,
        descriptor: McpCapabilityDescriptor | None,
        request: CapabilityRequest,
        status: str,
        error_code: str,
        summary: str,
        blockers: tuple[str, ...],
        retryable: bool,
        trace_id: str,
        started: float,
        data: Mapping[str, Any] | None = None,
        evidence_ref: str = "",
        source_identity: str = "",
        source_version: str = "",
        freshness_status: str = "unknown",
        freshness_expires_at: str = "",
    ) -> McpGatewayExecution:
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        collected_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        result = CapabilityResult(
            request_id=request.request_id,
            capability=request.capability,
            provider=request.provider,
            status=status,
            mutation_level=request.mutation_level,
            changed=False,
            summary=summary,
            data={} if data is None else copy.deepcopy(dict(data)),
            evidence=({"ref": evidence_ref},) if evidence_ref else (),
            warnings=(),
            blockers=blockers,
            audit={
                "error_code": error_code,
                "retryable": retryable,
                "trace_id": trace_id,
                "evidence_ref": evidence_ref,
                "execution_kind": "mcp",
                "source_identity": source_identity,
                "source_version": source_version,
                "freshness_status": freshness_status,
                "freshness_expires_at": freshness_expires_at,
                "collected_at": collected_at,
            },
        )
        event: dict[str, Any] = {
            "request_id": self._audit_identifier(request.request_id),
            "capability": self._audit_identifier(request.capability),
            "provider": self._audit_identifier(request.provider),
            "mutation_level": request.mutation_level.name,
            "status": status,
            "trace_id": trace_id,
            "server": "" if descriptor is None else descriptor.server,
            "tool": "" if descriptor is None else descriptor.tool,
            "duration_ms": duration_ms,
            "evidence_ref": evidence_ref,
            "error_code": error_code,
            "retryable": retryable,
            "timestamp": collected_at,
            **self._audit_context(request.context),
        }
        self.audit_sink.record(event)
        return McpGatewayExecution(descriptor, result, duration_ms)

    @staticmethod
    def _audit_identifier(value: Any) -> str:
        if (
            not isinstance(value, str)
            or _AUDIT_IDENTIFIER.fullmatch(value) is None
            or contains_sensitive_text(value)
        ):
            return ""
        return value

    @classmethod
    def _audit_context(cls, context: Mapping[str, Any]) -> dict[str, str]:
        return {
            key: cls._audit_identifier(context.get(key, ""))
            for key in _AUDIT_CONTEXT_KEYS
        }
