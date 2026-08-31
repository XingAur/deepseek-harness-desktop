from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from app.external_io_inventory import ExternalIoInventory, ScanRoot
from app.role_capability_skill_registry import (
    RoleCapabilitySkillRegistryError,
    load_role_capability_skill_registry,
)


POLICY_SCHEMA_VERSION = "his-external-io-boundaries.v1"
BOUNDARY_DISPOSITIONS = frozenset(
    {
        "mcp_required",
        "worker_allowed",
        "control_plane_internal",
        "compatibility_quarantine",
        "forbidden",
    }
)
_ROOT_SOURCES = frozenset({"harness_root", "capability_config"})
_CATEGORIES = frozenset({"credential", "database", "network", "process"})
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_WILDCARD_CHARACTERS = frozenset({"*", "?", "[", "]", "{" , "}"})


class ExternalIoPolicyError(ValueError):
    """The external-I/O boundary policy is malformed or cannot be trusted."""


@dataclass(frozen=True)
class BoundaryRule:
    root_id: str
    relative_path: str
    file_sha256: str
    findings: tuple[tuple[str, str, int], ...]
    disposition: str
    owner: str
    rationale: str


@dataclass(frozen=True)
class ExternalIoPolicy:
    schema_version: str
    roots: tuple[ScanRoot, ...]
    rules: tuple[BoundaryRule, ...]


@dataclass(frozen=True)
class ExternalIoPolicyReport:
    status: str
    finding_count: int
    unclassified_count: int
    source_drift_count: int
    forbidden_count: int
    compatibility_debt_count: int
    skill_contract_error_count: int
    details: tuple[dict[str, object], ...]


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExternalIoPolicyError(f"cannot read {label}") from exc
    if not isinstance(payload, dict):
        raise ExternalIoPolicyError(f"{label} must be an object")
    return payload


def _exact_fields(
    payload: Mapping[str, Any],
    expected: set[str] | frozenset[str],
    label: str,
) -> None:
    actual = set(payload)
    if actual != set(expected):
        raise ExternalIoPolicyError(f"{label} fields are not exact")


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExternalIoPolicyError(f"{label} must be a non-empty string")
    return value.strip()


def _safe_relative_path(value: Any, label: str) -> str:
    text = _required_text(value, label)
    path = Path(text)
    if (
        path.is_absolute()
        or ".." in path.parts
        or any(character in text for character in _WILDCARD_CHARACTERS)
    ):
        raise ExternalIoPolicyError(f"{label} must be an exact safe relative path")
    return path.as_posix()


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _plugin_roots(capabilities_config_path: Path) -> dict[str, Path]:
    payload = _read_json(capabilities_config_path, "capability config")
    raw_roots = payload.get("plugin_roots")
    if not isinstance(raw_roots, list):
        raise ExternalIoPolicyError("capability config plugin_roots must be an array")
    config_directory = capabilities_config_path.expanduser().resolve().parent
    result: dict[str, Path] = {}
    for raw_root in raw_roots:
        if not isinstance(raw_root, str) or not raw_root.strip():
            raise ExternalIoPolicyError("capability config contains an invalid plugin root")
        candidate = Path(raw_root).expanduser()
        root = (candidate if candidate.is_absolute() else config_directory / candidate).resolve()
        manifest = _read_json(root / "capabilities.json", "plugin capability manifest")
        plugin = _required_text(manifest.get("plugin"), "plugin name")
        if plugin in result:
            raise ExternalIoPolicyError(f"duplicate plugin root: {plugin}")
        result[plugin] = root
    return result


def _plugin_inventory(plugin_inventory_path: Path) -> dict[str, Mapping[str, Any]]:
    payload = _read_json(plugin_inventory_path, "plugin inventory")
    if payload.get("schema_version") != "his-plugin-inventory.v1":
        raise ExternalIoPolicyError("plugin inventory schema version is unsupported")
    plugins = payload.get("plugins")
    if not isinstance(plugins, list):
        raise ExternalIoPolicyError("plugin inventory plugins must be an array")
    result: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(plugins):
        if not isinstance(item, dict):
            raise ExternalIoPolicyError(f"plugin inventory item {index} must be an object")
        name = _required_text(item.get("name"), f"plugin inventory item {index} name")
        if name in result:
            raise ExternalIoPolicyError(f"duplicate inventory plugin: {name}")
        result[name] = item
    return result


def _verify_plugin_identity(
    plugin: str,
    root: Path,
    inventory_item: Mapping[str, Any],
) -> None:
    manifest_path = root / "capabilities.json"
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExternalIoPolicyError(f"cannot verify plugin identity: {plugin}") from exc
    if not isinstance(manifest, dict):
        raise ExternalIoPolicyError(f"invalid plugin manifest: {plugin}")
    if manifest.get("plugin") != plugin:
        raise ExternalIoPolicyError(f"plugin identity mismatch: {plugin}")
    if manifest.get("plugin_version") != inventory_item.get("version"):
        raise ExternalIoPolicyError(f"plugin version mismatch: {plugin}")
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    if manifest_hash != inventory_item.get("capabilities_sha256"):
        raise ExternalIoPolicyError(f"plugin capability hash mismatch: {plugin}")
    source_hashes = inventory_item.get("sources_sha256")
    if not isinstance(source_hashes, dict):
        raise ExternalIoPolicyError(f"plugin source inventory missing: {plugin}")
    for relative_path, expected_hash in source_hashes.items():
        safe_path = _safe_relative_path(relative_path, f"plugin source path for {plugin}")
        if not isinstance(expected_hash, str) or not _SHA256_PATTERN.fullmatch(expected_hash):
            raise ExternalIoPolicyError(f"plugin source hash invalid: {plugin}")
        target = (root / safe_path).resolve()
        if not _within(target, root) or not target.is_file():
            raise ExternalIoPolicyError(f"plugin source missing: {plugin}/{safe_path}")
        try:
            actual_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError as exc:
            raise ExternalIoPolicyError(f"plugin source unreadable: {plugin}/{safe_path}") from exc
        if actual_hash != expected_hash:
            raise ExternalIoPolicyError(f"plugin source hash mismatch: {plugin}/{safe_path}")


def load_external_io_policy(
    path: Path,
    *,
    harness_root: Path,
    capabilities_config_path: Path,
    plugin_inventory_path: Path,
) -> ExternalIoPolicy:
    payload = _read_json(path, "external I/O policy")
    _exact_fields(payload, {"schema_version", "roots", "rules"}, "policy")
    if payload["schema_version"] != POLICY_SCHEMA_VERSION:
        raise ExternalIoPolicyError(f"schema_version must be {POLICY_SCHEMA_VERSION}")
    raw_roots = payload["roots"]
    raw_rules = payload["rules"]
    if not isinstance(raw_roots, list) or not isinstance(raw_rules, list):
        raise ExternalIoPolicyError("policy roots and rules must be arrays")

    resolved_harness_root = harness_root.resolve()
    plugin_roots: dict[str, Path] | None = None
    inventory: dict[str, Mapping[str, Any]] | None = None
    roots: list[ScanRoot] = []
    roots_by_id: dict[str, Path] = {}
    for index, item in enumerate(raw_roots):
        if not isinstance(item, dict):
            raise ExternalIoPolicyError(f"roots[{index}] must be an object")
        _exact_fields(item, {"root_id", "source", "value"}, f"roots[{index}]")
        root_id = _required_text(item["root_id"], f"roots[{index}].root_id")
        source = _required_text(item["source"], f"roots[{index}].source")
        value = _required_text(item["value"], f"roots[{index}].value")
        if source not in _ROOT_SOURCES:
            raise ExternalIoPolicyError(f"unknown root source: {source}")
        if root_id in roots_by_id:
            raise ExternalIoPolicyError(f"duplicate root id: {root_id}")
        if source == "harness_root":
            safe_value = _safe_relative_path(value, f"roots[{index}].value")
            resolved = (resolved_harness_root / safe_value).resolve()
            if not _within(resolved, resolved_harness_root):
                raise ExternalIoPolicyError(f"unsafe harness root reference: {root_id}")
        else:
            if any(character in value for character in _WILDCARD_CHARACTERS) or Path(value).name != value:
                raise ExternalIoPolicyError(f"invalid plugin reference: {value}")
            if plugin_roots is None:
                plugin_roots = _plugin_roots(capabilities_config_path)
                inventory = _plugin_inventory(plugin_inventory_path)
            resolved = plugin_roots.get(value)
            inventory_item = inventory.get(value) if inventory is not None else None
            if resolved is None or inventory_item is None:
                raise ExternalIoPolicyError(f"unknown plugin reference: {value}")
            if root_id != f"plugin:{value}":
                raise ExternalIoPolicyError(f"plugin root id mismatch: {root_id}")
            _verify_plugin_identity(value, resolved, inventory_item)
        if not resolved.is_dir():
            raise ExternalIoPolicyError(f"resolved root is not a directory: {root_id}")
        roots_by_id[root_id] = resolved
        roots.append(ScanRoot(root_id, resolved))

    rules: list[BoundaryRule] = []
    seen_rules: set[tuple[str, str]] = set()
    for index, item in enumerate(raw_rules):
        if not isinstance(item, dict):
            raise ExternalIoPolicyError(f"rules[{index}] must be an object")
        _exact_fields(
            item,
            {
                "root_id",
                "relative_path",
                "file_sha256",
                "findings",
                "disposition",
                "owner",
                "rationale",
            },
            f"rules[{index}]",
        )
        root_id = _required_text(item["root_id"], f"rules[{index}].root_id")
        if root_id not in roots_by_id:
            raise ExternalIoPolicyError(f"rule references unknown root: {root_id}")
        relative_path = _safe_relative_path(
            item["relative_path"], f"rules[{index}].relative_path"
        )
        rule_key = (root_id, relative_path)
        if rule_key in seen_rules:
            raise ExternalIoPolicyError(f"duplicate boundary rule: {root_id}/{relative_path}")
        seen_rules.add(rule_key)
        file_sha256 = _required_text(item["file_sha256"], f"rules[{index}].file_sha256")
        if not _SHA256_PATTERN.fullmatch(file_sha256):
            raise ExternalIoPolicyError(f"rules[{index}].file_sha256 is invalid")
        raw_findings = item["findings"]
        if not isinstance(raw_findings, list) or not raw_findings:
            raise ExternalIoPolicyError(f"rules[{index}].findings must not be empty")
        findings: list[tuple[str, str, int]] = []
        for finding_index, finding in enumerate(raw_findings):
            if not isinstance(finding, dict):
                raise ExternalIoPolicyError("policy finding must be an object")
            _exact_fields(
                finding,
                {"category", "symbol", "occurrence"},
                f"rules[{index}].findings[{finding_index}]",
            )
            category = _required_text(finding["category"], "finding category")
            symbol = _required_text(finding["symbol"], "finding symbol")
            occurrence = finding["occurrence"]
            if category not in _CATEGORIES:
                raise ExternalIoPolicyError(f"unknown finding category: {category}")
            if isinstance(occurrence, bool) or not isinstance(occurrence, int) or occurrence < 1:
                raise ExternalIoPolicyError("finding occurrence must be a positive integer")
            findings.append((category, symbol, occurrence))
        if len(findings) != len(set(findings)):
            raise ExternalIoPolicyError(f"duplicate finding in rule: {root_id}/{relative_path}")
        disposition = _required_text(item["disposition"], f"rules[{index}].disposition")
        if disposition not in BOUNDARY_DISPOSITIONS:
            raise ExternalIoPolicyError(f"unknown boundary disposition: {disposition}")
        rules.append(
            BoundaryRule(
                root_id=root_id,
                relative_path=relative_path,
                file_sha256=file_sha256,
                findings=tuple(sorted(findings)),
                disposition=disposition,
                owner=_required_text(item["owner"], f"rules[{index}].owner"),
                rationale=_required_text(item["rationale"], f"rules[{index}].rationale"),
            )
        )
    return ExternalIoPolicy(
        schema_version=POLICY_SCHEMA_VERSION,
        roots=tuple(sorted(roots, key=lambda item: item.root_id)),
        rules=tuple(sorted(rules, key=lambda item: (item.root_id, item.relative_path))),
    )


def evaluate_inventory(
    inventory: ExternalIoInventory,
    policy: ExternalIoPolicy,
    *,
    matrix_path: Path | None = None,
) -> ExternalIoPolicyReport:
    rules = {(item.root_id, item.relative_path): item for item in policy.rules}
    unclassified: set[tuple[str, str, str, str, int]] = set()
    drifted_files: set[tuple[str, str]] = set()
    forbidden: set[tuple[str, str, str, str, int]] = set()
    compatibility: set[tuple[str, ...]] = set()
    skill_contract_errors: set[tuple[str, ...]] = set()
    details: list[dict[str, object]] = []

    for finding in inventory.findings:
        rule = rules.get((finding.root_id, finding.relative_path))
        identity = (
            finding.root_id,
            finding.relative_path,
            finding.category,
            finding.symbol,
            finding.occurrence,
        )
        if rule is None:
            unclassified.add(identity)
            disposition = "unclassified"
        elif rule.file_sha256 != finding.file_sha256:
            drifted_files.add((finding.root_id, finding.relative_path))
            disposition = "source_drift"
        elif (finding.category, finding.symbol, finding.occurrence) not in rule.findings:
            unclassified.add(identity)
            disposition = "unclassified"
        else:
            disposition = rule.disposition
            if disposition == "forbidden":
                forbidden.add(identity)
            elif disposition == "compatibility_quarantine":
                compatibility.add(("finding",) + tuple(str(item) for item in identity))
        if (
            finding.relative_path.endswith("SKILL.md")
            and finding.category in {"credential", "database", "network"}
        ):
            skill_contract_errors.add(
                (finding.root_id, finding.relative_path, finding.category, finding.symbol)
            )
        details.append(
            {
                "kind": "finding",
                "root_id": finding.root_id,
                "relative_path": finding.relative_path,
                "category": finding.category,
                "symbol": finding.symbol,
                "occurrence": finding.occurrence,
                "disposition": disposition,
            }
        )

    if matrix_path is not None:
        try:
            registry = load_role_capability_skill_registry(matrix_path)
        except RoleCapabilitySkillRegistryError:
            skill_contract_errors.add(("matrix", "invalid"))
            details.append(
                {
                    "kind": "matrix_error",
                    "code": "ROLE_CAPABILITY_SKILL_MATRIX_INVALID",
                }
            )
        else:
            for route in registry.capability_routes:
                skill = registry.skills[route.skill]
                if route.required_boundary == "worker_allowed":
                    disposition = "worker_allowed"
                elif route.required_boundary == "control_plane_internal":
                    disposition = "control_plane_internal"
                elif route.migration_state == "compatibility":
                    disposition = "compatibility_quarantine"
                    compatibility.add(("route", route.capability, route.provider))
                else:
                    disposition = "mcp_required"
                if skill.kind == "mcp_skill" and not skill.mcp_server:
                    skill_contract_errors.add(("skill", skill.name, "missing_mcp_server"))
                details.append(
                    {
                        "kind": "matrix_route",
                        "capability": route.capability,
                        "provider": route.provider,
                        "skill": route.skill,
                        "skill_kind": skill.kind,
                        "execution_kind": route.execution_kind,
                        "required_boundary": route.required_boundary,
                        "migration_state": route.migration_state,
                        "mcp_server": route.mcp_server or "",
                        "disposition": disposition,
                    }
                )

    failed = bool(unclassified or drifted_files or forbidden or skill_contract_errors)
    return ExternalIoPolicyReport(
        status="failed" if failed else "passed",
        finding_count=len(inventory.findings),
        unclassified_count=len(unclassified),
        source_drift_count=len(drifted_files),
        forbidden_count=len(forbidden),
        compatibility_debt_count=len(compatibility),
        skill_contract_error_count=len(skill_contract_errors),
        details=tuple(
            sorted(
                details,
                key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
            )
        ),
    )
