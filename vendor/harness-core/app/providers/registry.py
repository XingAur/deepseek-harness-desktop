from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.providers.git import GitProviderAdapter
from app.providers.github import GitHubProviderAdapter
from app.providers.gitlab import GitLabProviderAdapter
from app.providers.database_readonly import DatabaseReadonlyProviderAdapter
from app.providers.model_smoke import ManagerModelSmokeProviderAdapter
from app.providers.yunxiao import YunxiaoProviderAdapter


def build_manager_adapter_registry(
    *,
    provider: str | None = None,
) -> Mapping[str, object]:
    """Construct the allowlisted Manager adapter registry.

    Legacy Yunxiao modules are intentionally neither imported nor accepted as
    injectable objects.  Additional providers must be reviewed and registered
    in this module explicitly.
    """

    if provider is not None and provider not in {"yunxiao", "git", "gitlab", "github", "database", "model"}:
        raise ValueError("manager_provider_not_registered")
    adapters: dict[str, object] = {
        "yunxiao": YunxiaoProviderAdapter(),
        "git": GitProviderAdapter(),
        "gitlab": GitLabProviderAdapter(),
        "github": GitHubProviderAdapter(),
        "database": DatabaseReadonlyProviderAdapter(),
        "model": ManagerModelSmokeProviderAdapter(),
    }
    return MappingProxyType(adapters if provider is None else {provider: adapters[provider]})
