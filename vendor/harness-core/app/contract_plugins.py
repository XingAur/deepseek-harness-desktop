from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CONTRACT_PLUGIN_PACK_SCHEMA_VERSION = "1.0-contract-plugin-pack"
DEFAULT_CONTRACT_PLUGIN_PACK = (
    Path(__file__).resolve().parents[1] / "config" / "contract_plugins" / "dfhis.common.v1.json"
)


def load_contract_plugin_pack(path: str | Path = DEFAULT_CONTRACT_PLUGIN_PACK) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 contract plugin pack：{exc}") from exc
    validate_contract_plugin_pack(payload)
    payload["_source_path"] = str(source)
    return payload


def validate_contract_plugin_pack(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ValueError("contract plugin pack 根节点必须是 JSON 对象。")
    if payload.get("schema_version") != CONTRACT_PLUGIN_PACK_SCHEMA_VERSION:
        raise ValueError(f"schema_version 必须为 {CONTRACT_PLUGIN_PACK_SCHEMA_VERSION}。")
    if not normalized_text(payload.get("pack_id")):
        raise ValueError("contract plugin pack 缺少 pack_id。")
    if not normalized_text(payload.get("version")):
        raise ValueError("contract plugin pack 缺少 version。")
    plugins = payload.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        raise ValueError("contract plugin pack.plugins 必须是非空数组。")
    seen: set[str] = set()
    for index, plugin in enumerate(plugins):
        prefix = f"plugins[{index}]"
        if not isinstance(plugin, dict):
            raise ValueError(f"{prefix} 必须是对象。")
        plugin_id = normalized_text(plugin.get("id"))
        if not plugin_id:
            raise ValueError(f"{prefix}.id 不能为空。")
        if plugin_id in seen:
            raise ValueError(f"contract plugin id 重复：{plugin_id}")
        seen.add(plugin_id)
        if not normalized_text(plugin.get("version")):
            raise ValueError(f"{prefix}.version 不能为空。")
        validate_match(plugin.get("match"), prefix=f"{prefix}.match")
        outputs = plugin.get("outputs")
        if not isinstance(outputs, dict):
            raise ValueError(f"{prefix}.outputs 必须是对象。")
        parameters = outputs.get("parameters") or []
        if not isinstance(parameters, list):
            raise ValueError(f"{prefix}.outputs.parameters 必须是数组。")
        for parameter_index, parameter in enumerate(parameters):
            validate_parameter(parameter, prefix=f"{prefix}.outputs.parameters[{parameter_index}]")


def validate_match(value: Any, *, prefix: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{prefix} 必须是对象。")
    all_tokens = normalized_text_list(value.get("all"))
    any_groups = value.get("any_groups") or []
    if not isinstance(any_groups, list) or any(not normalized_text_list(group) for group in any_groups):
        raise ValueError(f"{prefix}.any_groups 必须是非空字符串数组的数组。")
    if not all_tokens and not any_groups:
        raise ValueError(f"{prefix} 至少声明 all 或 any_groups。")
    if value.get("none") is not None and not isinstance(value.get("none"), list):
        raise ValueError(f"{prefix}.none 必须是数组。")


def validate_parameter(value: Any, *, prefix: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{prefix} 必须是对象。")
    if not normalized_text(value.get("name")):
        raise ValueError(f"{prefix}.name 不能为空。")
    if not normalized_text(value.get("default_location")):
        raise ValueError(f"{prefix}.default_location 不能为空。")
    allowed_values = value.get("allowed_values")
    if not isinstance(allowed_values, dict) or not normalize_mapping(allowed_values):
        raise ValueError(f"{prefix}.allowed_values 必须是非空对象。")
    for conditional in value.get("conditional_allowed_values") or []:
        if not isinstance(conditional, dict) or not normalized_text_list(conditional.get("when_any")):
            raise ValueError(f"{prefix}.conditional_allowed_values 条目缺少 when_any。")
        if not normalize_mapping(conditional.get("values")):
            raise ValueError(f"{prefix}.conditional_allowed_values 条目缺少 values。")


def apply_contract_plugins(
    text: str,
    *,
    pack: dict[str, Any] | None = None,
    user_overrides: bool = False,
) -> list[dict[str, Any]]:
    active_pack = pack or load_contract_plugin_pack()
    validate_contract_plugin_pack(active_pack)
    results: list[dict[str, Any]] = []
    for plugin in active_pack["plugins"]:
        if plugin.get("enabled", True) is False or not plugin_matches(text, plugin["match"]):
            continue
        outputs = plugin["outputs"]
        parameters = [
            materialize_parameter(parameter, text=text, user_overrides=user_overrides)
            for parameter in outputs.get("parameters") or []
        ]
        scope_config = outputs.get("scope") or {}
        do_not = normalized_text_list(scope_config.get("do_not"))
        if user_overrides:
            do_not.extend(normalized_text_list(scope_config.get("do_not_when_user_overrides")))
        results.append(
            {
                "pack_id": active_pack["pack_id"],
                "pack_version": active_pack["version"],
                "plugin_id": plugin["id"],
                "plugin_version": plugin["version"],
                "parameters": parameters,
                "scope": {
                    "do": normalized_text(scope_config.get("do")),
                    "do_not": unique_keep_order(do_not),
                },
            }
        )
    return results


def plugin_matches(text: str, match: dict[str, Any]) -> bool:
    if any(token not in text for token in normalized_text_list(match.get("all"))):
        return False
    if any(not any(token in text for token in normalized_text_list(group)) for group in match.get("any_groups") or []):
        return False
    return not any(token in text for token in normalized_text_list(match.get("none")))


def materialize_parameter(parameter: dict[str, Any], *, text: str, user_overrides: bool) -> dict[str, Any]:
    location = normalized_text(parameter.get("default_location"))
    for rule in parameter.get("location_rules") or []:
        if any(token in text for token in normalized_text_list(rule.get("when_any"))):
            location = normalized_text(rule.get("value")) or location
            break
    values = normalize_mapping(parameter.get("allowed_values"))
    for conditional in parameter.get("conditional_allowed_values") or []:
        if any(token in text for token in normalized_text_list(conditional.get("when_any"))):
            values.update(normalize_mapping(conditional.get("values")))
    return {
        "name": normalized_text(parameter.get("name")),
        "location": location,
        "source": "user_instruction"
        if user_overrides and parameter.get("source_when_user_overrides")
        else normalized_text(parameter.get("source")) or "demand_text",
        "allowed_values": values,
        "contract_plugin": normalized_text(parameter.get("contract_plugin")),
    }


def normalized_text(value: Any) -> str:
    return str(value or "").strip()


def normalized_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := normalized_text(item))]


def normalize_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key).strip(): str(item).strip()
        for key, item in value.items()
        if str(key).strip() and str(item).strip()
    }


def unique_keep_order(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))
