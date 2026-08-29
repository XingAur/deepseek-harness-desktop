from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from app.config_resolver import ConfigLayer, ResolvedConfig, load_layer_document, resolve_config
from app.harness_config import REQUIRED_HARD_GUARDS, load_rule_pack, read_json_object, resolve_profile


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_EXPERT_CONFIG_PATH = BASE_DIR / "prompts" / "default_experts.json"


def build_legacy_builtin_layer(
    *,
    rule_pack_path: str | Path | None = None,
    profile_config_path: str | Path | None = None,
    profile_key: str = "",
    expert_config_path: str | Path | None = None,
) -> tuple[ConfigLayer, dict[str, Any]]:
    rule_pack = load_rule_pack(rule_pack_path)
    profile = resolve_profile(
        profile_config_path=profile_config_path,
        profile_key=profile_key,
    )
    expert_path = (
        Path(expert_config_path).expanduser()
        if expert_config_path
        else DEFAULT_EXPERT_CONFIG_PATH
    )
    expert_config = read_json_object(expert_path)

    config = {
        "providers": {
            "requirement_sources": list((rule_pack.get("providers") or {}).get("requirement_sources") or []),
            "normalized_schema": list((rule_pack.get("providers") or {}).get("normalized_schema") or []),
            "active": dict(profile.get("requirement_provider") or {}),
        },
        "hard_guards": dict(rule_pack.get("hard_guards") or {}),
        "agents": {
            "legacy_team": dict(expert_config.get("team") or {}),
            "registry": list(expert_config.get("experts") or []),
        },
        "orchestration": {
            "mode": "legacy",
            "team": dict(expert_config.get("team") or {}),
            "legacy_steps": list(expert_config.get("steps") or []),
        },
        "git": dict(rule_pack.get("git") or {}),
        "comments": dict(rule_pack.get("comments") or {}),
        "status_flow": dict(rule_pack.get("status_flow") or {}),
        "verification": dict(rule_pack.get("verification") or {}),
        "risk": dict(rule_pack.get("risk") or {}),
        "sharing": dict(rule_pack.get("sharing") or {}),
        "projects": {"active_profile": dict(profile)},
        "credentials": {
            "refs": [
                *list(rule_pack.get("credential_refs") or []),
                *list(profile.get("credential_refs") or []),
            ],
        },
        "features": {
            "enabled": list(profile.get("enabled_features") or []),
            "flags": {"config_resolver_v2": False},
        },
    }
    return (
        ConfigLayer(
            name="v0.33-legacy-defaults",
            kind="builtin_defaults",
            source="legacy:v0.33",
            data=config,
            merge_policies={},
        ),
        dict(REQUIRED_HARD_GUARDS),
    )


def resolve_legacy_compatible_config(
    *,
    rule_pack_path: str | Path | None = None,
    profile_config_path: str | Path | None = None,
    profile_key: str = "",
    expert_config_path: str | Path | None = None,
    team_config_path: str | Path | None = None,
    project_config_path: str | Path | None = None,
    personal_config_path: str | Path | None = None,
    run_overrides: Mapping[str, Any] | None = None,
) -> ResolvedConfig:
    builtin_layer, hard_guards = build_legacy_builtin_layer(
        rule_pack_path=rule_pack_path,
        profile_config_path=profile_config_path,
        profile_key=profile_key,
        expert_config_path=expert_config_path,
    )
    layers = [builtin_layer]
    for path, kind in (
        (team_config_path, "team_package"),
        (project_config_path, "project_config"),
        (personal_config_path, "personal_override"),
    ):
        if path is not None:
            layers.append(load_layer_document(path, expected_kind=kind))
    if run_overrides is not None:
        if not isinstance(run_overrides, Mapping):
            raise ValueError("run_overrides must be an object")
        layers.append(ConfigLayer(
            name="cli-run-override",
            kind="run_override",
            source="cli:--run-override-json",
            data=dict(run_overrides),
            merge_policies={},
        ))
    return resolve_config(layers=layers, hard_guards=hard_guards)
