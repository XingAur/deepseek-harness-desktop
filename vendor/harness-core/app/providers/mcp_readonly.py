from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.capability_contracts import CapabilityRequest
from app.mcp_runtime_factory import build_persistent_mcp_runtime
from app.provider_execution import ProviderExecutionContext, ProviderExecutionRequest


_PROVIDERS = frozenset({"yunxiao", "gitlab", "database"})
_READ_ACTIONS = {
    "yunxiao": frozenset({"yunxiao.connection_test", "workitem.read", "workitem.comments.read"}),
    "gitlab": frozenset(
        {
            "gitlab.connection_test",
            "project.read",
            "merge_request.read",
            "gitlab.repository.file.read",
            "gitlab.commit.read",
        }
    ),
    "database": frozenset({"database.connection_test", "database.schema.read"}),
}
_TARGET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_WORKITEM = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,31}-[0-9]{1,20}$")
_PROJECT = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_DATABASE_TARGET = re.compile(r"^db-[a-z0-9][a-z0-9._-]{0,123}$")


class McpPrimaryProviderError(RuntimeError):
    """A stable fail-closed Manager error from the MCP primary route."""

    def __init__(self, provider_reason: str) -> None:
        super().__init__(provider_reason)
        self.provider_reason = provider_reason


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def _default_runtime():
    root = _root()
    config = json.loads((root / "config" / "capabilities.json").read_text(encoding="utf-8"))
    plugin_roots = config.get("plugin_roots")
    if not isinstance(plugin_roots, list) or any(not isinstance(item, str) for item in plugin_roots):
        raise RuntimeError("mcp_plugin_roots_unavailable")
    state_value = os.environ.get("HARNESS_MCP_STATE_ROOT", "").strip()
    state_root = Path(state_value).expanduser() if state_value else root / "data" / "mcp-runtime"
    return build_persistent_mcp_runtime(
        harness_root=root,
        manifest_path=root / "config" / "mcp_capabilities.json",
        plugin_inventory_path=root / "config" / "plugin_inventory.json",
        plugin_roots=[Path(item) for item in plugin_roots],
        state_root=state_root.resolve(),
        environment=dict(os.environ),
    ).runtime


class McpReadonlyProviderAdapter:
    """Manager adapter whose external read always executes through frozen MCP."""

    def __init__(
        self,
        provider: str,
        *,
        runtime_loader: Callable[[], object] | None = None,
        fallback: Callable[..., object] | None = None,
    ) -> None:
        if provider not in _PROVIDERS:
            raise ValueError("mcp_primary_provider_not_registered")
        if runtime_loader is not None and not callable(runtime_loader):
            raise TypeError("mcp_runtime_loader_invalid")
        if fallback is not None and not callable(fallback):
            raise TypeError("mcp_fallback_invalid")
        self.provider = provider
        self._runtime_loader = runtime_loader or _default_runtime
        # `fallback` is accepted only to make negative tests explicit. It is
        # deliberately not retained and can never run after an MCP failure.

    def normalize_target_alias(self, value: object) -> str:
        if not isinstance(value, str) or _TARGET.fullmatch(value) is None:
            raise ValueError("mcp_target_invalid")
        if self.provider == "yunxiao":
            if value.count(".") != 1:
                raise ValueError("mcp_target_invalid")
            organization, workitem = value.split(".")
            if not organization or _WORKITEM.fullmatch(workitem.upper()) is None:
                raise ValueError("mcp_target_invalid")
            return f"{organization}.{workitem.lower()}"
        if self.provider == "gitlab":
            if not value.startswith("gl-h"):
                raise ValueError("mcp_target_invalid")
            return value
        if _DATABASE_TARGET.fullmatch(value) is None:
            raise ValueError("mcp_target_invalid")
        return value

    def normalize_request_target(self, parameters: Mapping[str, object]) -> str:
        if not isinstance(parameters, Mapping):
            raise ValueError("mcp_parameters_invalid")
        if self.provider == "yunxiao":
            organization = parameters.get("organization_alias")
            workitem = parameters.get("work_item_alias")
            if not isinstance(organization, str) or not isinstance(workitem, str):
                raise ValueError("mcp_parameters_invalid")
            return self.normalize_target_alias(f"{organization}.{workitem.lower()}")
        if self.provider == "gitlab":
            explicit = parameters.get("target_alias")
            if isinstance(explicit, str):
                return self.normalize_target_alias(explicit)
            host = parameters.get("host_alias")
            project = parameters.get("project_alias")
            if not isinstance(host, str) or not isinstance(project, str) or _PROJECT.fullmatch(project) is None:
                raise ValueError("mcp_parameters_invalid")
            group, name = project.split("/", 1)
            iid = parameters.get("merge_request_iid")
            suffix = f"-m{iid}" if isinstance(iid, int) and not isinstance(iid, bool) and iid > 0 else ""
            target = f"gl-h{len(host)}-{host}-g{len(group)}-{group}-p{len(name)}-{name}{suffix}"
            return self.normalize_target_alias(target)
        alias = parameters.get("database_alias")
        return self.normalize_target_alias(alias)

    def validate_profile_binding(self, *, profile_id: int, target_alias: object) -> str:
        if isinstance(profile_id, bool) or not isinstance(profile_id, int) or profile_id < 1:
            raise ValueError("mcp_profile_invalid")
        return self.normalize_target_alias(target_alias)

    def render_plan(self, request: ProviderExecutionRequest) -> dict[str, object]:
        self._ensure_read_action(request.action)
        target = self.normalize_request_target(request.parameters)
        # Building the exact MCP arguments is the parameter-validation gate;
        # no runtime or credential is touched while rendering a plan.
        self._mcp_request(request)
        return {
            "provider": self.provider,
            "action": request.action,
            "target_alias": target,
            "change": {"field": "read", "after": "no_external_change"},
            "execution_kind": "mcp",
        }

    def execute(
        self,
        request: ProviderExecutionRequest,
        context: ProviderExecutionContext,
    ) -> Mapping[str, object]:
        self._ensure_read_action(request.action)
        capability_request = self._mcp_request(request)
        try:
            runtime = self._runtime_loader()
            execute = getattr(runtime, "execute")
            execution = execute(capability_request)
            result = execution.result
        except McpPrimaryProviderError:
            raise
        except Exception:
            raise McpPrimaryProviderError("mcp_runtime_unavailable") from None
        if result.status != "success":
            code = result.audit.get("error_code", "MCP_READ_FAILED")
            if not isinstance(code, str) or not code:
                code = "MCP_READ_FAILED"
            reason = re.sub(r"[^a-z0-9_.-]", "_", code.lower())[:80]
            raise McpPrimaryProviderError(reason or "mcp_read_failed")
        target = self.normalize_request_target(request.parameters)
        context.record_network_dispatch(target, simulated=False)
        evidence_ref = ""
        if result.evidence and isinstance(result.evidence[0].get("ref"), str):
            evidence_ref = str(result.evidence[0]["ref"])
        payload = json.dumps(
            dict(result.data),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return {
            "source": self.provider,
            "execution_kind": "mcp",
            "capability": capability_request.capability,
            "summary": result.summary,
            "evidence_ref": evidence_ref,
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "item_count": _item_count(result.data),
            "truncated": False,
        }

    def verify(
        self,
        _verifier_action: str,
        _original_write_action: str,
        _request: ProviderExecutionRequest,
        _target_alias: str,
        _context: ProviderExecutionContext,
    ) -> str:
        return "unknown"

    def _ensure_read_action(self, action: object) -> str:
        if not isinstance(action, str) or action not in _READ_ACTIONS[self.provider]:
            raise McpPrimaryProviderError("mcp_write_capability_unavailable")
        return action

    def _mcp_request(self, request: ProviderExecutionRequest) -> CapabilityRequest:
        action = self._ensure_read_action(request.action)
        capability, provider, scope, input_payload = self._mapping(action, request.parameters)
        return CapabilityRequest.from_dict(
            {
                "schema_version": "his-capability-request.v1",
                "request_id": f"mcp-{provider}-{request.plan_id}",
                "capability": capability,
                "provider": provider,
                "mode": "preview",
                "mutation_level": "L1",
                "authorization": {"explicit": False, "scope": [scope]},
                "input": input_payload,
                "context": {
                    "task_id": f"provider-plan-{request.plan_id}",
                    "run_id": f"provider-action-{request.plan_id}",
                },
            }
        )

    def _mapping(
        self, action: str, parameters: Mapping[str, object]
    ) -> tuple[str, str, str, dict[str, object]]:
        if self.provider == "yunxiao":
            workitem = parameters.get("work_item_alias") or parameters.get("work_item_id")
            if not isinstance(workitem, str) or _WORKITEM.fullmatch(workitem.upper()) is None:
                raise McpPrimaryProviderError("mcp_arguments_invalid")
            return (
                "workitem.read",
                "yunxiao",
                "workitem:read",
                {
                    "work_item_id": workitem.upper(),
                    "include_comments": action == "workitem.comments.read" or bool(parameters.get("include_comments", True)),
                    "include_attachments": bool(parameters.get("include_attachments", False)),
                    "page_cursor": str(parameters.get("page_cursor", "")),
                    "page_size": int(parameters.get("page_size", 20)),
                },
            )
        if self.provider == "gitlab":
            project = parameters.get("project_alias") or parameters.get("project")
            if not isinstance(project, str) or _PROJECT.fullmatch(project) is None:
                raise McpPrimaryProviderError("mcp_arguments_invalid")
            operation = "project"
            ref = ""
            path = ""
            object_id = ""
            if action == "merge_request.read":
                operation = "merge_request"
                object_id = str(parameters.get("merge_request_iid", ""))
            elif action == "gitlab.repository.file.read":
                operation = "repository_file"
                ref = str(parameters.get("ref", ""))
                path = str(parameters.get("file_path", ""))
            elif action == "gitlab.commit.read":
                operation = "commit"
                object_id = str(parameters.get("sha", ""))
            return (
                "gitlab.read",
                "gitlab",
                "gitlab:read",
                {
                    "project": project,
                    "operation": operation,
                    "ref": ref,
                    "path": path,
                    "object_id": object_id,
                },
            )
        connection_alias = parameters.get("connection_alias")
        if not isinstance(connection_alias, str):
            raise McpPrimaryProviderError("mcp_arguments_invalid")
        operation = parameters.get("operation", "schemas" if action == "database.connection_test" else "tables")
        return (
            "database.inspect",
            "postgresql",
            "database:inspect",
            {
                "connection_alias": connection_alias,
                "operation": operation,
                "schema": parameters.get("schema", ""),
                "table": parameters.get("table", ""),
            },
        )


def _item_count(data: Mapping[str, Any]) -> int:
    for key in ("rows", "items", "work_items"):
        value = data.get(key)
        if isinstance(value, (list, tuple)):
            return len(value)
    return 1 if data else 0
