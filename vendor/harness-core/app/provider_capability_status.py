"""Read-only bridge from Manager provider profiles to canonical capabilities."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.provider_execution import ACTION_DESCRIPTORS
from app.provider_profiles import build_provider_profile_status


_MANIFEST_UNAVAILABLE = "unavailable"
_MANIFEST_MALFORMED = "malformed"
_FORCED_DISABLED_CAPABILITIES = frozenset((
    "workitem.write",
    "database.change",
))
_EXECUTION_BOUNDARIES = {
    # These labels are deliberately descriptive rather than availability
    # claims.  They let the UI explain what the user must (and must not) do
    # without turning an unregistered executor into a false "ready" state.
    "workitem.read": "readonly_mcp_remote_read",
    "workitem.write": "remote_write_explicit_confirmation",
    "git.inspect": "local_readonly",
    "git.diff": "local_readonly",
    "source.read": "local_readonly",
    "source.search": "local_readonly",
    "git.history": "local_readonly",
    "verification.run-local": "local_readonly_command_execution",
    "code.review-local": "local_readonly_review",
    "git.apply-local": "local_write_after_worktree_and_diff_review",
    "git.commit-local": "local_write_explicit_delivery",
    "git.push": "remote_write_explicit_confirmation",
    "gitlab.read": "readonly_mcp_remote_read",
    "gitlab.write": "remote_write_explicit_confirmation",
    "github.read": "remote_read_credential_and_connection",
    "github.write": "remote_write_explicit_confirmation",
    "database.inspect": "readonly_mcp_catalog_inspection",
    "database.change-plan": "readonly_database_change_plan",
    "database.change": "database_write_disabled_by_default",
    "knowledge.retrieve": "local_readonly_knowledge",
    "knowledge.answer": "local_readonly_knowledge",
}
_MCP_PRIMARY_CAPABILITIES = frozenset({
    "workitem.read",
    "gitlab.read",
    "database.inspect",
})
_MCP_PRIMARY_ACTIONS = {
    "yunxiao": frozenset({
        "yunxiao.connection_test", "workitem.read", "workitem.comments.read",
    }),
    "gitlab": frozenset({
        "gitlab.connection_test", "project.read", "merge_request.read",
        "gitlab.repository.file.read", "gitlab.commit.read",
    }),
    "database": frozenset({
        "database.connection_test", "database.schema.read",
    }),
}
_PROVIDER_BRIDGES = {
    "yunxiao": {"plugin": "yunxiao", "primary": "workitem.read", "skills": {
        "workitem.read": "yunxiao-workitem-read", "workitem.write": "yunxiao-workitem-write"}},
    "git": {"plugin": "his-engineering", "primary": "git.inspect", "skills": {
        "git.inspect": "his-git-local", "git.diff": "his-code-evidence",
        "source.read": "his-code-evidence", "source.search": "his-code-evidence",
        "git.history": "his-code-evidence",
        "verification.run-local": "his-code-evidence",
        "code.review-local": "his-code-evidence",
        "git.apply-local": "his-git-local",
        "git.commit-local": "his-git-delivery", "git.push": "his-git-delivery"}},
    "gitlab": {"plugin": "his-engineering", "primary": "gitlab.read", "skills": {
        "gitlab.read": "his-gitlab", "gitlab.write": "his-gitlab"}},
    "github": {"plugin": "his-engineering", "primary": "github.read", "skills": {
        "github.read": "his-github", "github.write": "his-github"}},
    "database": {"plugin": "his-engineering", "primary": "database.inspect", "skills": {
        "database.inspect": "his-database-read", "database.change-plan": "his-database-change",
        "database.change": "his-database-change"}},
    "knowledge": {"plugin": "his-knowledge", "primary": "knowledge.retrieve", "skills": {
        "knowledge.retrieve": "his-knowledge-retrieve", "knowledge.answer": "his-knowledge-answer",
        "knowledge.candidate.create": "his-knowledge-maintain",
        "knowledge.candidate.review": "his-knowledge-maintain",
        "knowledge.item.promote": "his-knowledge-maintain"}},
}
_PROVIDER_ACTIONS = {
    "yunxiao": (
        "yunxiao.connection_test", "workitem.read", "workitem.comments.read",
        "workitem.comment.write", "workitem.owner.update", "workitem.status.update",
    ),
    "git": (
        "git.connection_test", "git.readonly_smoke", "repo.status.read",
        "repo.log.read", "repo.diff.read", "branch.create", "commit.create",
        "remote.fetch", "remote.push", "git.operation.plan", "reset.local", "cherry-pick.local",
        "merge.local",
    ),
    "gitlab": (
        "gitlab.connection_test", "project.read", "merge_request.read",
        "gitlab.repository.file.read", "gitlab.commit.read",
        "gitlab.commit.diff.read", "gitlab.compare.read",
        "gitlab.merge_request.commits.read", "gitlab.merge_request.diffs.read",
        "gitlab.pipeline.jobs.read",
        "merge_request.comment.write", "merge_request.create",
    ),
    "github": (
        "github.connection_test", "github.repository.read", "github.issue.read",
        "github.pull_request.read", "github.repository.file.read",
        "github.commit.read", "github.commit.diff.read", "github.compare.read",
        "github.pull_request.commits.read", "github.pull_request.diffs.read",
        "github.actions.run.jobs.read", "github.pull_request.comment.write",
        "github.pull_request.create",
    ),
    "database": (
        "database.connection_test", "database.schema.read", "database.query.read",
    ),
    "model": ("model.connection_test", "model.single_node.smoke"),
    "knowledge": ("knowledge.connection_test",),
}


def build_provider_capability_status(
    profiles: Sequence[Mapping[str, Any]],
    manifest_path: str | None = None,
    *,
    manifest_paths: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build an inert, redacted capability contract without invoking providers."""

    normalized = build_provider_profile_status(profiles)["profiles"]
    manifests = _load_manifests(manifest_path, manifest_paths)
    return {
        "schema_version": "his-provider-capability-status.v1",
        "changed": False,
        "credentials_read": False,
        "external_calls": False,
        "write_performed": False,
        "items": [_build_item(profile, manifests) for profile in normalized],
    }


def _load_manifests(
    manifest_path: str | None, manifest_paths: Mapping[str, str] | None
) -> dict[str, Mapping[str, bool] | str]:
    paths = dict(manifest_paths) if isinstance(manifest_paths, Mapping) else {}
    if "his-engineering" not in paths and manifest_path is not None:
        paths["his-engineering"] = manifest_path
    return {
        plugin: _load_allowlisted_manifest(paths.get(plugin), plugin)
        for plugin in {bridge["plugin"] for bridge in _PROVIDER_BRIDGES.values()}
    }


def _load_allowlisted_manifest(
    manifest_path: object, plugin: str
) -> Mapping[str, bool] | str:
    if not isinstance(manifest_path, str) or not manifest_path:
        return _MANIFEST_UNAVAILABLE
    path = Path(manifest_path)
    if not path.is_absolute() or path.name != "capabilities.json":
        return _MANIFEST_UNAVAILABLE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return _MANIFEST_UNAVAILABLE
    return _normalize_canonical_manifest(payload, plugin)


def _normalize_canonical_manifest(payload: Any, plugin: str) -> Mapping[str, bool] | str:
    if not isinstance(payload, Mapping):
        return _MANIFEST_MALFORMED
    if payload.get("schema_version") != "his-capabilities.v1" or payload.get("plugin") != plugin:
        return _MANIFEST_MALFORMED
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, Sequence) or isinstance(capabilities, (str, bytes)):
        return _MANIFEST_MALFORMED
    normalized: dict[str, bool] = {}
    for capability in capabilities:
        if not isinstance(capability, Mapping):
            return _MANIFEST_MALFORMED
        name = capability.get("name")
        enabled = capability.get("enabled")
        if not isinstance(name, str) or not name.strip() or not isinstance(enabled, bool):
            return _MANIFEST_MALFORMED
        if name in normalized:
            return _MANIFEST_MALFORMED
        normalized[name] = enabled
    return normalized


def _build_item(
    profile: Mapping[str, Any], manifests: Mapping[str, Mapping[str, bool] | str]
) -> dict[str, Any]:
    provider = str(profile["provider"])
    profile_key = str(profile["profile_key"])
    bridge = _PROVIDER_BRIDGES.get(provider)
    descriptive = {
        "configuration_status": (
            "configured" if not profile.get("issues") else "configuration_invalid"
        ),
        "availability_status": "blocked",
        "availability_reason": "provider_adapter_not_registered",
        "actions": _build_action_descriptions(provider),
    }
    if bridge is None:
        return {
            "provider": provider,
            "profile_key": profile_key,
            "capabilities": [],
            "status": "blocked",
            "reason": "canonical_provider_contract_unregistered",
            "execution_status": "blocked",
            "execution_reason": "canonical_provider_contract_unregistered",
            **descriptive,
        }

    manifest = manifests[bridge["plugin"]]
    capabilities = [
        _build_capability(name, skill, manifest)
        for name, skill in bridge["skills"].items()
    ]
    primary = next(item for item in capabilities if item["name"] == bridge["primary"])
    if primary["execution_status"] == "available":
        descriptive.update(
            availability_status="available",
            availability_reason=str(primary["execution_reason"]),
        )
    item: dict[str, Any] = {
        "provider": provider,
        "profile_key": profile_key,
        "provider_plugin": bridge["plugin"],
        "capabilities": capabilities,
        "status": "enabled" if primary["contract_status"] == "enabled" else "blocked",
        "reason": _contract_reason(str(primary["contract_status"])),
        "execution_status": str(primary["execution_status"]),
        "execution_reason": str(primary["execution_reason"]),
        "capability_state": (
            "blocked_missing_credentials"
            if profile.get("issues")
            else "available"
            if primary["contract_status"] == "enabled"
            else "unsupported"
        ),
        **descriptive,
    }
    if provider == "git":
        item.update(
            skill=bridge["skills"]["git.inspect"],
            inspect_capability="git.inspect",
        )
    return item


def _build_action_descriptions(provider: str) -> list[dict[str, Any]]:
    return [
        {
            "action": descriptor.action,
            "risk": descriptor.risk,
            "max_timeout_seconds": descriptor.max_timeout_seconds,
            "max_result_bytes": descriptor.max_result_bytes,
            "required_credential_fields": list(descriptor.required_credential_fields),
            "read_back_verifier": descriptor.read_back_verifier,
            "availability_status": (
                "available"
                if descriptor.action in _MCP_PRIMARY_ACTIONS.get(provider, ())
                else "blocked"
            ),
            "availability_reason": (
                "mcp_primary_adapter_registered"
                if descriptor.action in _MCP_PRIMARY_ACTIONS.get(provider, ())
                else "provider_adapter_not_registered"
            ),
        }
        for action in _PROVIDER_ACTIONS.get(provider, ())
        if (descriptor := ACTION_DESCRIPTORS.get(action)) is not None
    ]


def _build_capability(
    name: str, skill: str, manifest: Mapping[str, bool] | str
) -> dict[str, str]:
    status = _contract_status(name, manifest)
    orchestrated = (
        status == "enabled"
        and (name in _MCP_PRIMARY_CAPABILITIES or name in {
            "git.diff", "source.read", "source.search", "git.history",
            "verification.run-local", "code.review-local",
        })
    )
    return {
        "name": name,
        "skill": skill,
        "contract_status": status,
        "execution_status": "available" if orchestrated else "blocked",
        "execution_reason": _execution_reason(name, status),
        "execution_boundary": _EXECUTION_BOUNDARIES.get(name, "provider_specific_boundary"),
    }


def _contract_status(name: str, manifest: Mapping[str, bool] | str) -> str:
    if manifest in (_MANIFEST_UNAVAILABLE, _MANIFEST_MALFORMED):
        return str(manifest)
    if name not in manifest:
        return "missing"
    if name in _FORCED_DISABLED_CAPABILITIES:
        return "disabled"
    return "enabled" if manifest[name] else "disabled"


def _contract_reason(status: str) -> str:
    return {
        "enabled": "canonical_provider_capability_enabled",
        "disabled": "canonical_provider_capability_disabled",
        "missing": "canonical_provider_capability_missing",
        "unavailable": "canonical_provider_manifest_unavailable",
        "malformed": "canonical_provider_manifest_malformed",
    }[status]


def _execution_reason(name: str, status: str) -> str:
    if status != "enabled":
        return _contract_reason(status)
    if name in _MCP_PRIMARY_CAPABILITIES:
        return "mcp_primary_adapter_registered"
    if name == "git.inspect":
        return "git_inspect_os_sandbox_executor_unregistered"
    if name in {
        "git.diff", "source.read", "source.search", "git.history",
        "verification.run-local", "code.review-local",
    }:
        return "code_evidence_orchestrator_registered"
    return "canonical_provider_executor_unregistered"
