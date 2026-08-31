from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.providers.git import GitProviderAdapter
from app.providers.github import GitHubProviderAdapter
from app.providers.gitlab import GitLabProviderAdapter
from app.providers.database_readonly import DatabaseReadonlyProviderAdapter
from app.providers.mcp_readonly import McpReadonlyProviderAdapter
from app.providers.model_smoke import ManagerModelSmokeProviderAdapter
from app.providers.yunxiao import YunxiaoProviderAdapter


def build_manager_adapter_registry(
    *,
    provider: str | None = None,
    compatibility_mode: str = "mcp",
    mcp_runtime_loader=None,
) -> Mapping[str, object]:
    """Construct the allowlisted Manager adapter registry.

    MCP is the default for Yunxiao, GitLab and database reads. Legacy direct
    adapters are constructed only for the explicit provider_rollback mode;
    they are never selected after an MCP error. Additional providers must be
    reviewed and registered in this module explicitly.
    """

    if compatibility_mode not in {"mcp", "provider_rollback"}:
        raise ValueError("manager_compatibility_mode_invalid")
    if provider is not None and provider not in {"yunxiao", "git", "gitlab", "github", "database", "model"}:
        raise ValueError("manager_provider_not_registered")
    if compatibility_mode == "provider_rollback":
        external = {
            "yunxiao": YunxiaoProviderAdapter(),
            "gitlab": GitLabProviderAdapter(),
            "database": DatabaseReadonlyProviderAdapter(),
        }
    else:
        external = {
            name: McpReadonlyProviderAdapter(name, runtime_loader=mcp_runtime_loader)
            for name in ("yunxiao", "gitlab", "database")
        }
    adapters: dict[str, object] = {
        "yunxiao": external["yunxiao"],
        "git": GitProviderAdapter(),
        "gitlab": external["gitlab"],
        "github": GitHubProviderAdapter(),
        "database": external["database"],
        "model": ManagerModelSmokeProviderAdapter(),
    }
    return MappingProxyType(adapters if provider is None else {provider: adapters[provider]})
