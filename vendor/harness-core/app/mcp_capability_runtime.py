from __future__ import annotations

from typing import Mapping

from app.capability_contracts import CapabilityRequest, CapabilityResult
from app.capability_permissions import evaluate_capability_permission
from app.capability_registry import CapabilityDescriptor
from app.capability_runtime import CapabilityExecution, CapabilityPreflight
from app.mcp_capability_registry import McpCapabilityDescriptor, McpCapabilityRegistry
from app.mcp_gateway import McpGateway


class McpCapabilityRuntime:
    """Governed executable runtime for frozen readonly MCP capabilities."""

    def __init__(
        self,
        *,
        registry: McpCapabilityRegistry,
        gateway: McpGateway,
    ) -> None:
        self.registry = registry
        self.gateway = gateway

    def preflight(self, request: CapabilityRequest) -> CapabilityPreflight:
        if not isinstance(request, CapabilityRequest):
            raise TypeError("request must be a CapabilityRequest")
        mcp_descriptor = self.registry.resolve(request.capability, request.provider)
        descriptor = self._descriptor(mcp_descriptor)
        permission = evaluate_capability_permission(
            request=request,
            declared_level=mcp_descriptor.mutation_level,
            declared_scopes=mcp_descriptor.required_scopes,
            external_writes_default=False,
        )
        return CapabilityPreflight(descriptor=descriptor, permission=permission)

    def execute(
        self,
        request: CapabilityRequest,
        *,
        timeout_seconds: int | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> CapabilityExecution:
        preflight = self.preflight(request)
        mcp_descriptor = self.registry.resolve(request.capability, request.provider)
        if not preflight.descriptor.enabled:
            return self._blocked_execution(
                preflight,
                request,
                status="unsupported",
                error_code="MCP_CAPABILITY_DISABLED",
            )
        if not preflight.permission.allowed:
            return self._blocked_execution(
                preflight,
                request,
                status="blocked",
                error_code="MCP_PERMISSION_DENIED",
            )
        if environment:
            return self._blocked_execution(
                preflight,
                request,
                status="blocked",
                error_code="MCP_ENVIRONMENT_FORBIDDEN",
            )
        if (
            timeout_seconds is not None
            and (
                isinstance(timeout_seconds, bool)
                or not isinstance(timeout_seconds, int)
                or timeout_seconds != mcp_descriptor.timeout_seconds
            )
        ):
            return self._blocked_execution(
                preflight,
                request,
                status="blocked",
                error_code="MCP_TIMEOUT_POLICY_MISMATCH",
            )
        gateway_execution = self.gateway.execute(request)
        return CapabilityExecution(
            descriptor=preflight.descriptor,
            permission=preflight.permission,
            result=gateway_execution.result,
            duration_ms=gateway_execution.duration_ms,
        )

    @staticmethod
    def _descriptor(item: McpCapabilityDescriptor) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            plugin=f"mcp:{item.server}",
            plugin_version=item.contract_version,
            name=item.capability,
            provider=item.provider,
            contract_version=item.contract_version,
            mutation_level=item.mutation_level,
            credential_class="none",
            entrypoint=None,
            enabled=item.enabled,
            disabled_reason=item.disabled_reason,
            scopes=item.required_scopes,
        )

    @staticmethod
    def _blocked_execution(
        preflight: CapabilityPreflight,
        request: CapabilityRequest,
        *,
        status: str,
        error_code: str,
    ) -> CapabilityExecution:
        return CapabilityExecution(
            descriptor=preflight.descriptor,
            permission=preflight.permission,
            result=CapabilityResult(
                request_id=request.request_id,
                capability=request.capability,
                provider=request.provider,
                status=status,
                mutation_level=request.mutation_level,
                changed=False,
                summary=error_code,
                data={},
                evidence=(),
                warnings=(),
                blockers=(error_code,),
                audit={"error_code": error_code},
            ),
            duration_ms=0,
        )
