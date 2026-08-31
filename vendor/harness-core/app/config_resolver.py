from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "1.0-resolved-config"
LAYER_SCHEMA_VERSION = "1.0"
LAYER_KINDS = {
    "builtin_defaults",
    "team_package",
    "project_config",
    "personal_override",
    "run_override",
}
MERGE_POLICIES = {"replace", "merge", "append", "union", "remove", "locked"}
SAFE_SECRET_PREFIXES = ("env:", "keychain:", "file:")
SECRET_KEY_HINTS = (
    "token",
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "pat",
)
SECRET_METADATA_PATHS = {"sharing.recommended_secret_sources"}

_MISSING = object()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return copy.deepcopy(value)


@dataclass(frozen=True)
class ConfigLayer:
    name: str
    kind: str
    source: str
    data: Mapping[str, Any]
    merge_policies: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedConfig:
    schema_version: str
    generated_at: str
    readonly: bool
    values: Mapping[str, Any]
    provenance: Mapping[str, Any]
    layers: tuple[Mapping[str, Any], ...]
    hard_guards: Mapping[str, Any]
    validation: Mapping[str, Any]
    content_hash: str

    @property
    def is_valid(self) -> bool:
        return self.validation.get("status") == "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "readonly": self.readonly,
            "values": _thaw(self.values),
            "provenance": _thaw(self.provenance),
            "layers": _thaw(self.layers),
            "hard_guards": _thaw(self.hard_guards),
            "validation": _thaw(self.validation),
            "content_hash": self.content_hash,
        }


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {
        "severity": "error",
        "code": code,
        "path": path,
        "message": message,
    }


def _is_secret_path(path: str) -> bool:
    if path in SECRET_METADATA_PATHS:
        return False
    key = path.rsplit(".", 1)[-1].lower().replace("-", "_")
    if "pat" in key.split("_"):
        return True
    return any(hint in key for hint in SECRET_KEY_HINTS if hint != "pat")


def _sanitize_secrets(
    value: Any,
    *,
    path: str,
    issues: list[dict[str, str]],
) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_secrets(
                item,
                path=f"{path}.{key}" if path else str(key),
                issues=issues,
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_secrets(item, path=path, issues=issues)
            for item in value
        ]
    if isinstance(value, str) and value and _is_secret_path(path):
        if not value.startswith(SAFE_SECRET_PREFIXES):
            issues.append(_issue(
                "literal_secret_forbidden",
                path,
                "Secret-looking configuration values must use env:, keychain:, or file: references.",
            ))
            return "<redacted-invalid-secret>"
    return copy.deepcopy(value)


def _drop_provenance(provenance: dict[str, Any], path: str) -> None:
    prefix = f"{path}." if path else ""
    for key in list(provenance):
        if key == path or (prefix and key.startswith(prefix)):
            provenance.pop(key, None)


def _record_provenance(
    provenance: dict[str, Any],
    value: Any,
    *,
    path: str,
    layer: ConfigLayer,
    policy: str,
) -> None:
    if isinstance(value, Mapping) and value:
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            _record_provenance(
                provenance,
                item,
                path=child_path,
                layer=layer,
                policy=policy,
            )
        return
    provenance[path] = {
        "layer_name": layer.name,
        "layer_kind": layer.kind,
        "source": layer.source,
        "policy": policy,
    }


def _replace_value(
    incoming: Any,
    *,
    path: str,
    layer: ConfigLayer,
    policy: str,
    provenance: dict[str, Any],
) -> Any:
    result = copy.deepcopy(incoming)
    _drop_provenance(provenance, path)
    _record_provenance(
        provenance,
        result,
        path=path,
        layer=layer,
        policy=policy,
    )
    return result


def _merge_value(
    existing: Any,
    incoming: Any,
    *,
    path: str,
    layer: ConfigLayer,
    incoming_policies: Mapping[str, str],
    effective_policies: dict[str, str],
    locked_paths: set[str],
    provenance: dict[str, Any],
    issues: list[dict[str, str]],
    inherited_policy: str | None = None,
) -> Any:
    if path in locked_paths:
        if existing is _MISSING or existing != incoming:
            issues.append(_issue(
                "locked_path_override",
                path,
                f"Layer {layer.name!r} cannot override a locked configuration path.",
            ))
        return copy.deepcopy(incoming if existing is _MISSING else existing)

    explicit_policy = incoming_policies.get(path)
    policy = explicit_policy or effective_policies.get(path)
    if policy is None:
        if isinstance(incoming, Mapping) and (existing is _MISSING or isinstance(existing, Mapping)):
            policy = "merge"
        else:
            policy = "replace"

    if explicit_policy is not None:
        effective_policies[path] = explicit_policy

    if policy == "locked":
        result = _replace_value(
            incoming,
            path=path,
            layer=layer,
            policy="locked",
            provenance=provenance,
        )
        locked_paths.add(path)
        effective_policies[path] = "locked"
        return result

    if policy == "merge":
        base = {} if existing is _MISSING else existing
        if not isinstance(base, Mapping) or not isinstance(incoming, Mapping):
            issues.append(_issue(
                "policy_type_mismatch",
                path,
                "The merge policy requires object values in both layers.",
            ))
            return copy.deepcopy(base)
        result = copy.deepcopy(dict(base))
        if not incoming:
            if existing is _MISSING:
                _record_provenance(
                    provenance,
                    result,
                    path=path,
                    layer=layer,
                    policy=inherited_policy or "merge",
                )
            return result
        provenance.pop(path, None)
        child_inherited = "merge" if explicit_policy == "merge" else inherited_policy
        for key, item in incoming.items():
            child_path = f"{path}.{key}" if path else str(key)
            result[str(key)] = _merge_value(
                result.get(str(key), _MISSING),
                item,
                path=child_path,
                layer=layer,
                incoming_policies=incoming_policies,
                effective_policies=effective_policies,
                locked_paths=locked_paths,
                provenance=provenance,
                issues=issues,
                inherited_policy=child_inherited,
            )
        return result

    if policy in {"append", "union", "remove"}:
        base = [] if existing is _MISSING else existing
        if not isinstance(base, (list, tuple)) or not isinstance(incoming, (list, tuple)):
            issues.append(_issue(
                "policy_type_mismatch",
                path,
                f"The {policy} policy requires list values in both layers.",
            ))
            return copy.deepcopy(base)
        result = list(copy.deepcopy(base))
        if policy == "append":
            result.extend(copy.deepcopy(list(incoming)))
        elif policy == "union":
            for item in incoming:
                if item not in result:
                    result.append(copy.deepcopy(item))
        else:
            result = [item for item in result if item not in incoming]
        return _replace_value(
            result,
            path=path,
            layer=layer,
            policy=policy,
            provenance=provenance,
        )

    provenance_policy = inherited_policy or "replace"
    return _replace_value(
        incoming,
        path=path,
        layer=layer,
        policy=provenance_policy,
        provenance=provenance,
    )


def _validate_declared_hard_guards(
    declared: Any,
    *,
    layer: ConfigLayer,
    hard_guards: Mapping[str, Any],
    issues: list[dict[str, str]],
) -> None:
    path = "hard_guards"
    if not isinstance(declared, Mapping):
        issues.append(_issue(
            "hard_guard_override",
            path,
            f"Layer {layer.name!r} must declare hard_guards as an object.",
        ))
        return
    for key in hard_guards:
        guard_path = f"{path}.{key}"
        if key not in declared:
            issues.append(_issue(
                "hard_guard_override",
                guard_path,
                f"Layer {layer.name!r} omitted a required hard guard declaration.",
            ))
        elif declared[key] != hard_guards[key]:
            issues.append(_issue(
                "hard_guard_override",
                guard_path,
                f"Layer {layer.name!r} attempted to change a system hard guard.",
            ))
    for key in declared:
        if key not in hard_guards:
            issues.append(_issue(
                "hard_guard_override",
                f"{path}.{key}",
                f"Layer {layer.name!r} declared an unknown hard guard.",
            ))


def resolve_config(
    *,
    layers: Sequence[ConfigLayer],
    hard_guards: Mapping[str, Any],
) -> ResolvedConfig:
    values: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    layer_records: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    effective_policies: dict[str, str] = {}
    locked_paths: set[str] = set()

    for layer in layers:
        if layer.kind not in LAYER_KINDS:
            issues.append(_issue(
                "invalid_layer_kind",
                "layer.kind",
                f"Layer {layer.name!r} has unsupported kind {layer.kind!r}.",
            ))
            continue
        if not isinstance(layer.data, Mapping):
            issues.append(_issue(
                "invalid_layer_root",
                "config",
                f"Layer {layer.name!r} config must be an object.",
            ))
            continue
        if not isinstance(layer.merge_policies, Mapping):
            issues.append(_issue(
                "invalid_layer_root",
                "merge_policies",
                f"Layer {layer.name!r} merge_policies must be an object.",
            ))
            raw_policies: Mapping[str, Any] = {}
        else:
            raw_policies = layer.merge_policies

        valid_policies: dict[str, str] = {}
        for raw_path, raw_policy in raw_policies.items():
            policy_path = str(raw_path)
            if raw_policy not in MERGE_POLICIES:
                issues.append(_issue(
                    "invalid_merge_policy",
                    f"merge_policies.{policy_path}",
                    f"Layer {layer.name!r} uses unsupported policy {raw_policy!r}.",
                ))
                continue
            valid_policies[policy_path] = str(raw_policy)

        layer_records.append({
            "name": layer.name,
            "kind": layer.kind,
            "source": layer.source,
            "merge_policies": copy.deepcopy(valid_policies),
        })

        sanitized = _sanitize_secrets(layer.data, path="", issues=issues)
        if "hard_guards" in sanitized:
            declared_hard_guards = sanitized.pop("hard_guards")
            _validate_declared_hard_guards(
                declared_hard_guards,
                layer=layer,
                hard_guards=hard_guards,
                issues=issues,
            )

        for key, item in sanitized.items():
            path = str(key)
            values[path] = _merge_value(
                values.get(path, _MISSING),
                item,
                path=path,
                layer=layer,
                incoming_policies=valid_policies,
                effective_policies=effective_policies,
                locked_paths=locked_paths,
                provenance=provenance,
                issues=issues,
            )

    validation = {
        "status": "failed" if any(item["severity"] == "error" for item in issues) else "pass",
        "issues": issues,
    }
    hash_payload = {
        "values": values,
        "provenance": provenance,
        "layers": layer_records,
        "hard_guards": dict(hard_guards),
        "validation": validation,
    }
    canonical = json.dumps(
        hash_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    return ResolvedConfig(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        readonly=True,
        values=_freeze(values),
        provenance=_freeze(provenance),
        layers=tuple(_freeze(item) for item in layer_records),
        hard_guards=_freeze(dict(hard_guards)),
        validation=_freeze(validation),
        content_hash=content_hash,
    )


def load_layer_document(path: str | Path, *, expected_kind: str) -> ConfigLayer:
    source_path = Path(path).expanduser().resolve()
    try:
        document = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{source_path}: invalid JSON document: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{source_path}: root must be an object")

    allowed_root = {"schema_version", "layer", "merge_policies", "config"}
    unknown_root = sorted(set(document) - allowed_root)
    if unknown_root:
        raise ValueError(f"{source_path}: unsupported root fields: {', '.join(unknown_root)}")
    if document.get("schema_version") != LAYER_SCHEMA_VERSION:
        raise ValueError(f"{source_path}: schema_version must be {LAYER_SCHEMA_VERSION!r}")

    layer_meta = document.get("layer")
    if not isinstance(layer_meta, dict):
        raise ValueError(f"{source_path}: layer must be an object")
    unknown_layer = sorted(set(layer_meta) - {"kind", "id"})
    if unknown_layer:
        raise ValueError(f"{source_path}: unsupported layer fields: {', '.join(unknown_layer)}")
    if layer_meta.get("kind") != expected_kind:
        raise ValueError(f"{source_path}: layer.kind must be {expected_kind!r}")
    layer_id = layer_meta.get("id")
    if not isinstance(layer_id, str) or not layer_id.strip():
        raise ValueError(f"{source_path}: layer.id must be a non-empty string")

    config = document.get("config")
    if not isinstance(config, dict):
        raise ValueError(f"{source_path}: config must be an object")
    policies = document.get("merge_policies", {})
    if not isinstance(policies, dict):
        raise ValueError(f"{source_path}: merge_policies must be an object")
    for policy_path, policy in policies.items():
        if policy not in MERGE_POLICIES:
            raise ValueError(
                f"{source_path}: merge_policies.{policy_path} has invalid value {policy!r}"
            )

    return ConfigLayer(
        name=layer_id,
        kind=expected_kind,
        source=str(source_path),
        data=config,
        merge_policies=policies,
    )


def _flatten_values(value: Any, *, path: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, Mapping) and value:
        rows: list[tuple[str, Any]] = []
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            rows.extend(_flatten_values(item, path=child_path))
        return rows
    return [(path, value)]


def _markdown_cell(value: Any) -> str:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return rendered.replace("|", "\\|").replace("\n", "<br>")


def resolved_config_to_markdown(resolved: ResolvedConfig | Mapping[str, Any]) -> str:
    payload = resolved.to_dict() if isinstance(resolved, ResolvedConfig) else _thaw(resolved)
    lines = [
        "# HIS Harness ResolvedConfig",
        "",
        f"- Schema: `{payload.get('schema_version', '')}`",
        f"- Read-only: `{str(bool(payload.get('readonly'))).lower()}`",
        f"- Content hash: `{payload.get('content_hash', '')}`",
        f"- Validation: `{(payload.get('validation') or {}).get('status', '')}`",
        "",
        "## Layers",
        "",
    ]
    layers = payload.get("layers") or []
    if layers:
        for index, layer in enumerate(layers, start=1):
            lines.append(
                f"{index}. `{layer.get('kind', '')}` / `{layer.get('name', '')}` / "
                f"`{layer.get('source', '')}`"
            )
    else:
        lines.append("No layers.")

    lines.extend(["", "## Hard Guards", ""])
    for key, value in (payload.get("hard_guards") or {}).items():
        lines.append(f"- `{key}`: `{_markdown_cell(value)}`")

    lines.extend([
        "",
        "## Resolved Values",
        "",
        "| Path | Value | Layer | Policy |",
        "| --- | --- | --- | --- |",
    ])
    provenance = payload.get("provenance") or {}
    for path, value in _flatten_values(payload.get("values") or {}):
        source = provenance.get(path, {})
        lines.append(
            f"| `{path}` | `{_markdown_cell(value)}` | "
            f"`{source.get('layer_kind', '')}:{source.get('layer_name', '')}` | "
            f"`{source.get('policy', '')}` |"
        )

    lines.extend(["", "## Validation Issues", ""])
    issues = (payload.get("validation") or {}).get("issues") or []
    if issues:
        for item in issues:
            lines.append(
                f"- `{item.get('code', '')}` at `{item.get('path', '')}`: "
                f"{item.get('message', '')}"
            )
    else:
        lines.append("No validation issues.")

    lines.extend([
        "",
        "本报告只解析配置，不应用配置、不读取远端账号、不执行外部写入。",
        "",
    ])
    return "\n".join(lines)


def write_resolved_config_outputs(
    *,
    output_dir: str | Path,
    resolved: ResolvedConfig,
) -> dict[str, str]:
    target_dir = Path(output_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / "harness_resolved_config.json"
    markdown_path = target_dir / "harness_resolved_config.md"
    json_path.write_text(
        json.dumps(resolved.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(resolved_config_to_markdown(resolved), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}
