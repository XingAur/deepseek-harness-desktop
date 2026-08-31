from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class McpTransportError(RuntimeError):
    """Base class for normalized MCP transport failures."""


class McpTransportUnavailable(McpTransportError):
    """The configured MCP transport cannot currently serve the request."""


class McpTransport(Protocol):
    def call(
        self,
        *,
        server: str,
        tool: str,
        arguments: Mapping[str, Any],
        timeout_seconds: int,
        trace_id: str,
    ) -> Mapping[str, Any]:
        """Return exactly one MCP result envelope."""


class DisabledMcpTransport:
    def call(self, **kwargs: Any) -> Mapping[str, Any]:
        raise McpTransportUnavailable("MCP transport is not configured")
