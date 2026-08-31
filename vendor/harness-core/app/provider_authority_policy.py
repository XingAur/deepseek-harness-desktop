from __future__ import annotations

import re
from dataclasses import dataclass


_KNOWN_RISKS = frozenset({"read", "model_smoke", "local_mutation", "remote_write"})
_PERSONAL_TOKEN_PROVIDERS = frozenset({"yunxiao", "gitlab", "github"})
_DESTRUCTIVE_DATABASE_TERMS = frozenset({"delete", "drop", "truncate"})
_DATABASE_MUTATION_TERMS = frozenset(
    {
        "alter",
        "copy",
        "create",
        "delete",
        "drop",
        "grant",
        "insert",
        "merge",
        "replace",
        "revoke",
        "truncate",
        "update",
        "upsert",
        "vacuum",
    }
)
_ACTION_PATTERN = re.compile(r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+")


@dataclass(frozen=True)
class ProviderAuthorityPolicy:
    """Separate technical access authority from Harness behavior governance."""

    technical_authority_source: str
    harness_authorization_required: bool
    exact_scope_authorization_required: bool
    destructive_scope_authorization_required: bool


def provider_authority_policy(
    *,
    provider: str,
    action: str,
    risk: str,
) -> ProviderAuthorityPolicy:
    """Return the fixed authority contract for one Provider action.

    Tokens, readonly database endpoints/credentials and local filesystem
    permissions decide whether a read is technically possible. Harness still
    binds and audits the read, but it does not manufacture a second human
    approval. Mutations continue to require explicit, exact authorization.
    """

    if (
        not isinstance(provider, str)
        or not isinstance(action, str)
        or not isinstance(risk, str)
        or risk not in _KNOWN_RISKS
        or not _ACTION_PATTERN.fullmatch(action)
        or not _provider_matches_action(provider, action)
    ):
        raise ValueError("provider_authority_policy:invalid_action")

    action_terms = frozenset(action.split("."))
    if (
        provider == "database"
        and risk == "read"
        and action_terms.intersection(_DATABASE_MUTATION_TERMS)
    ):
        raise ValueError("provider_authority_policy:invalid_action")

    if risk == "read":
        if provider in _PERSONAL_TOKEN_PROVIDERS:
            authority_source = "personal_token"
        elif provider == "database":
            authority_source = "readonly_endpoint_or_credential"
        else:
            authority_source = "local_permissions"
        return ProviderAuthorityPolicy(
            technical_authority_source=authority_source,
            harness_authorization_required=False,
            exact_scope_authorization_required=False,
            destructive_scope_authorization_required=False,
        )

    destructive_database_action = (
        provider == "database"
        and bool(action_terms.intersection(_DESTRUCTIVE_DATABASE_TERMS))
    )
    return ProviderAuthorityPolicy(
        technical_authority_source="explicit_user_authorization",
        harness_authorization_required=True,
        exact_scope_authorization_required=True,
        destructive_scope_authorization_required=destructive_database_action,
    )


def _provider_matches_action(provider: str, action: str) -> bool:
    prefixes = {
        "yunxiao": ("yunxiao.", "workitem."),
        "gitlab": ("gitlab.", "project.", "merge_request."),
        "github": ("github.",),
        "git": (
            "git.",
            "repo.",
            "branch.",
            "commit.",
            "remote.",
            "reset.",
            "cherry-pick.",
            "merge.",
        ),
        "database": ("database.",),
        "model": ("model.",),
        "knowledge": ("knowledge.",),
    }
    provider_prefixes = prefixes.get(provider)
    return provider_prefixes is not None and action.startswith(provider_prefixes)
