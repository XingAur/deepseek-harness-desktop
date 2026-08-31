from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from app.requirement_calibration import default_value_precedence_is_resolved
from app.requirement_governance import RequirementGovernanceResult


SINGLE_PASS_CHANGE_CONTRACT_SCHEMA_VERSION = "single-pass-change-contract.v1"
SINGLE_PASS_CHANGE_CONTRACT_STATUSES = {"ready", "blocked"}
_OWNERSHIP_LAYERS = ("frontend", "backend", "database", "configuration")
_OWNERSHIP_READY_STATUSES = {"required", "not_required", "already_satisfied"}
_CAPABILITY_NAME = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_COMMAND = re.compile(r"^[A-Za-z0-9_./:-]+(?:\s+[-A-Za-z0-9_./:=]+)*$")
_PROVIDER_AUTHORITY_KEYS = {
    "allowed_paths", "recommended_allowed_paths", "commands", "verify_commands",
    "recommended_verify_commands", "capabilities", "required_capabilities",
    "authorization", "explicit", "approved", "approval", "rollback_strategy",
}
_PROVIDER_SINGULAR_AUTHORITY_KEYS = {"path", "paths", "command", "capability", "rollback"}
_PROVIDER_AUTHORITY_PROSE = re.compile(
    r"(?:\b(?:please|must|should|add|grant|approve|allow|execute|run|use|set|apply)\b.{0,64}"
    r"\b(?:allowed[ _]?paths?|commands?|capabilit(?:y|ies)|authori[sz]ation|approval|rollback)\b"
    r"|(?:请|必须|应当|添加|增加|授予|批准|允许|执行|使用).{0,48}(?:允许路径|命令|能力|授权|审批|回退))",
    re.IGNORECASE,
)
_HIGH_RISK_PATHS = (
    ("医保", ("paths-to-verify: ordinary_insurance", "paths-to-verify: mobile_insurance", "paths-to-verify: self_pay", "paths-to-verify: conversion_to_insurance")),
    ("收费", ("paths-to-verify: ordinary_insurance", "paths-to-verify: mobile_insurance", "paths-to-verify: self_pay", "paths-to-verify: conversion_to_insurance")),
    ("退费", ("paths-to-verify: partial_refund", "paths-to-verify: full_refund")),
    ("结算", ("paths-to-verify: settlement_and_clearing",)),
    ("清算", ("paths-to-verify: settlement_and_clearing",)),
    ("金额", ("paths-to-verify: rounding", "paths-to-verify: precision", "paths-to-verify: aggregation_order")),
    ("对账", ("paths-to-verify: reconciliation",)),
)


@dataclass(frozen=True)
class SinglePassChangeContract:
    schema_version: str
    status: str
    objective: str
    in_scope: tuple[str, ...]
    out_of_scope: tuple[str, ...]
    repositories: tuple[dict[str, Any], ...]
    allowed_paths: tuple[str, ...]
    business_rules: tuple[dict[str, Any], ...]
    preserved_behaviors: tuple[str, ...]
    adjacent_paths: tuple[str, ...]
    database_impacts: tuple[dict[str, Any], ...]
    configuration_impacts: tuple[dict[str, Any], ...]
    verify_commands: tuple[str, ...]
    automatic_acceptance: tuple[str, ...]
    manual_acceptance: tuple[str, ...]
    rollback_strategy: str
    blockers: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != SINGLE_PASS_CHANGE_CONTRACT_SCHEMA_VERSION:
            raise ValueError("变更契约 schema 版本无效。")
        if self.status not in SINGLE_PASS_CHANGE_CONTRACT_STATUSES:
            raise ValueError("变更契约状态无效。")
        _require_text(self.objective, "目标")
        _require_text(self.rollback_strategy, "回退策略")
        for name in ("in_scope", "out_of_scope", "allowed_paths", "preserved_behaviors", "adjacent_paths", "verify_commands", "automatic_acceptance", "manual_acceptance", "blockers"):
            _validate_text_tuple(getattr(self, name), name)
        _validate_paths(self.allowed_paths)
        _validate_commands(self.verify_commands)
        _validate_repositories(self.repositories)
        _validate_json_dict_tuple(self.business_rules, "业务规则")
        _validate_json_dict_tuple(self.database_impacts, "数据库影响")
        _validate_json_dict_tuple(self.configuration_impacts, "配置影响")
        if self.status == "ready":
            if self.blockers:
                raise ValueError("ready 变更契约不能包含阻断项。")
            if not (self.repositories and self.allowed_paths and self.verify_commands and self.automatic_acceptance and self.manual_acceptance):
                raise ValueError("ready 变更契约缺少可执行闭环信息。")
            if self.rollback_strategy == "not_available":
                raise ValueError("ready 变更契约必须声明本地回退策略。")
        else:
            if not self.blockers:
                raise ValueError("blocked 变更契约必须包含阻断项。")
            if any((self.repositories, self.allowed_paths, self.business_rules, self.database_impacts, self.configuration_impacts, self.verify_commands, self.automatic_acceptance, self.manual_acceptance)):
                raise ValueError("blocked 变更契约不能包含可用的修改计划。")
            if self.rollback_strategy != "not_available":
                raise ValueError("blocked 变更契约不得声明可执行回退策略。")

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self), ensure_ascii=False, sort_keys=True))

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)

    def to_markdown(self) -> str:
        lines = [
            "# HIS 一次改好变更契约",
            "",
            f"- Schema：{self.schema_version}",
            f"- 状态：{self.status}",
            f"- 目标：{self.objective}",
            f"- 回退策略：{self.rollback_strategy}",
        ]
        for title, values in (("范围内", self.in_scope), ("范围外", self.out_of_scope), ("允许路径", self.allowed_paths), ("相邻路径核验", self.adjacent_paths), ("验证命令（仅数据，不执行）", self.verify_commands), ("自动验收", self.automatic_acceptance), ("人工验收", self.manual_acceptance), ("阻断项", self.blockers)):
            lines.extend(["", f"## {title}", ""])
            lines.extend(f"- {value}" for value in values) if values else lines.append("- 无")
        return "\n".join(lines)


def build_single_pass_change_contract(
    *,
    governance_result: RequirementGovernanceResult | object | None,
    objective: str,
    requirement_calibration: Mapping[str, Any] | object,
    technical_decision: Mapping[str, Any] | object,
    change_ownership: Mapping[str, Any] | object,
    acceptance_matrix: Mapping[str, Any] | object,
    normalized_requirement_evidence: Mapping[str, Any] | object | None = None,
    available_capabilities: Sequence[str] | object = (),
    trusted_authorization: Mapping[str, Any] | object | None = None,
) -> SinglePassChangeContract:
    """Build data only from trusted structured inputs; provider authority attempts fail closed."""
    safe_objective = objective.strip() if isinstance(objective, str) and objective.strip() else "未提供明确目标"
    if not isinstance(governance_result, RequirementGovernanceResult) or governance_result.status != "ready_for_local_change":
        return _blocked(safe_objective, "治理结果未被批准为 ready_for_local_change。")
    if _provider_evidence_attempts_authority(normalized_requirement_evidence):
        return _blocked(safe_objective, "不可信 provider 证据尝试扩大路径、命令、能力、审批或回退权限。")
    calibration = _mapping(requirement_calibration)
    technical = _mapping(technical_decision)
    ownership = _mapping(change_ownership)
    acceptance = _mapping(acceptance_matrix)
    if None in (calibration, technical, ownership, acceptance) or safe_objective == "未提供明确目标":
        return _blocked(safe_objective, "受信结构化变更输入不完整或格式无效。")

    blockers: list[str] = []
    _verify_patch_decision(technical.get("implementation_decision"), blockers)
    _verify_capability_shapes(technical, available_capabilities, trusted_authorization, blockers)
    repositories = _repositories(technical.get("selected_projects"), blockers)
    allowed_paths = _paths(technical.get("recommended_allowed_paths"), blockers)
    _verify_provenance(technical.get("field_provenance"), repositories, allowed_paths, blockers)
    _verify_default_value_precedence(calibration, technical.get("field_provenance"), blockers)
    _verify_interface(technical.get("contract_verification"), blockers)
    ownership_rows = _ownership_rows(ownership, blockers)
    verify_commands = _commands(technical.get("recommended_verify_commands"), blockers)
    _verify_acceptance_matrix(acceptance, blockers)
    automatic_acceptance = _automatic_acceptance(acceptance.get("auto_verification"), verify_commands, blockers)
    manual_acceptance = _acceptance(acceptance.get("manual_acceptance"), ("expected_result", "scenario", "statement", "path"), "人工验收", blockers)
    sibling_required = _sibling_required(acceptance.get("sibling_impact"), repositories, technical.get("selected_projects"), blockers)

    database_impacts = _impacts(ownership_rows, "database")
    configuration_impacts = _impacts(ownership_rows, "configuration")
    if _database_mutation_required(ownership_rows):
        _verify_database_authority(technical, available_capabilities, trusted_authorization, blockers)
    adjacent_paths = _adjacent_paths(safe_objective, acceptance, sibling_required, blockers)
    in_scope, out_of_scope, business_rules, preserved = _scope_and_rules(calibration, blockers)

    if blockers:
        return _blocked(safe_objective, *_unique(blockers))
    return SinglePassChangeContract(
        schema_version=SINGLE_PASS_CHANGE_CONTRACT_SCHEMA_VERSION,
        status="ready",
        objective=safe_objective,
        in_scope=tuple(in_scope),
        out_of_scope=tuple(out_of_scope),
        repositories=tuple(repositories),
        allowed_paths=tuple(allowed_paths),
        business_rules=tuple(business_rules),
        preserved_behaviors=tuple(preserved),
        adjacent_paths=tuple(adjacent_paths),
        database_impacts=tuple(database_impacts),
        configuration_impacts=tuple(configuration_impacts),
        verify_commands=tuple(verify_commands),
        automatic_acceptance=tuple(automatic_acceptance),
        manual_acceptance=tuple(manual_acceptance),
        rollback_strategy="restore_pre_change_local_files",
        blockers=(),
    )


def _blocked(objective: str, *blockers: str) -> SinglePassChangeContract:
    return SinglePassChangeContract(SINGLE_PASS_CHANGE_CONTRACT_SCHEMA_VERSION, "blocked", objective, (), (), (), (), (), (), (), (), (), (), (), (), "not_available", tuple(_unique(blockers)))


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _provider_evidence_attempts_authority(value: object, *, seen: set[int] | None = None, depth: int = 0, evidence_collection: str | None = None, evidence_item: bool = False) -> bool:
    if depth > 24:
        return True
    seen = set() if seen is None else seen
    if isinstance(value, Mapping):
        if id(value) in seen:
            return True
        seen.add(id(value))
        for key, item in value.items():
            key_name = key.lower() if isinstance(key, str) else ""
            local_evidence_path = evidence_item and key_name in {"path", "local_path"} and isinstance(item, str)
            if key_name in _PROVIDER_AUTHORITY_KEYS or (key_name in _PROVIDER_SINGULAR_AUTHORITY_KEYS and not local_evidence_path):
                return True
            child_collection = key_name if depth == 0 and key_name in {"attachments", "images"} else None
            if _provider_evidence_attempts_authority(item, seen=seen, depth=depth + 1, evidence_collection=child_collection, evidence_item=False):
                return True
        return False
    if isinstance(value, (list, tuple)):
        if id(value) in seen:
            return True
        seen.add(id(value))
        return any(_provider_evidence_attempts_authority(item, seen=seen, depth=depth + 1, evidence_collection=evidence_collection, evidence_item=evidence_collection in {"attachments", "images"}) for item in value)
    return isinstance(value, str) and bool(_PROVIDER_AUTHORITY_PROSE.search(value))


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}必须是非空字符串。")
    return value.strip()


def _validate_text_tuple(value: object, label: str) -> None:
    if not isinstance(value, tuple) or any(not isinstance(item, str) or not item.strip() for item in value) or len(set(value)) != len(value):
        raise ValueError(f"{label}必须是无重复的非空字符串元组。")


def _safe_relative_path(value: object) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip() or "\\" in value or any(ord(character) < 32 or ord(character) == 127 for character in value) or value.startswith(("/", "~", "./")) or re.match(r"^[A-Za-z]:", value):
        return None
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or ".." in path.parts or "." in path.parts or "" in path.parts:
        return None
    normalized = path.as_posix()
    return normalized if normalized == value else None


def _safe_repository_path(value: object) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip() or "\\" in value or not value.startswith("/"):
        return None
    path = PurePosixPath(value)
    if path.is_absolute() is False or ".." in path.parts or "." in path.parts or len(path.parts) < 3:
        return None
    normalized = path.as_posix()
    return normalized if normalized == value else None


def _validate_paths(paths: object) -> None:
    if any(_safe_relative_path(path) is None for path in paths):
        raise ValueError("允许路径必须是规范的相对路径。")


def _safe_command(value: object) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip() or not _COMMAND.fullmatch(value):
        return None
    return value


def _validate_commands(commands: object) -> None:
    if any(_safe_command(command) is None for command in commands):
        raise ValueError("验证命令格式无效。")


def _json_dict(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        result = json.loads(json.dumps(dict(value), ensure_ascii=False, sort_keys=True))
    except (TypeError, ValueError):
        return None
    return result if isinstance(result, dict) else None


def _validate_json_dict_tuple(values: object, label: str) -> None:
    if not isinstance(values, tuple) or any(_json_dict(item) is None for item in values):
        raise ValueError(f"{label}必须是 JSON 对象元组。")


def _validate_repositories(repositories: object) -> None:
    if not isinstance(repositories, tuple):
        raise ValueError("仓库必须是元组。")
    exact_records: set[tuple[str, str, str]] = set()
    for repository in repositories:
        if not isinstance(repository, Mapping) or set(repository) != {"name", "path", "role"}:
            raise ValueError("仓库记录格式无效。")
        name, path, role = repository.get("name"), repository.get("path"), repository.get("role")
        canonical_path = _safe_repository_path(path)
        if not all(isinstance(value, str) and value.strip() for value in (name, path, role)) or canonical_path is None:
            raise ValueError("仓库记录内容无效。")
        exact_record = (name, path, role)
        if exact_record in exact_records:
            raise ValueError("仓库记录不能完全重复。")
        exact_records.add(exact_record)


def _repositories(value: object, blockers: list[str]) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)) or not value:
        blockers.append("未提供已识别的仓库记录。")
        return []
    rows: list[dict[str, Any]] = []
    names: set[str] = set()
    paths: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or item.get("exists") is not True:
            blockers.append("仓库记录不完整或未确认存在。")
            return []
        name, path, role = item.get("name"), item.get("path"), item.get("role")
        canonical_path = _safe_repository_path(path)
        if not all(isinstance(part, str) and part.strip() for part in (name, path, role)) or canonical_path is None or name in names or canonical_path in paths:
            blockers.append("仓库记录格式无效。")
            return []
        names.add(name)
        paths.add(canonical_path)
        rows.append({"name": name, "path": canonical_path, "role": role})
    return rows


def _paths(value: object, blockers: list[str]) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        blockers.append("缺少规范的允许修改路径。")
        return []
    paths = [_safe_relative_path(item) for item in value]
    if any(path is None for path in paths) or len(set(paths)) != len(paths):
        blockers.append("允许修改路径必须全部为规范且无重复的相对路径。")
        return []
    return [path for path in paths if path is not None]


def _verify_provenance(value: object, repositories: list[dict[str, Any]], paths: list[str], blockers: list[str]) -> None:
    evidence = value.get("evidence") if isinstance(value, Mapping) else None
    if not isinstance(evidence, (list, tuple)) or not evidence:
        blockers.append("缺少已核验的字段或路径来源证据。")
        return
    repository_names = {item["name"] for item in repositories}
    proven: set[str] = set()
    for item in evidence:
        if not isinstance(item, Mapping) or not isinstance(item.get("project"), str) or item["project"] not in repository_names:
            blockers.append("字段或路径来源证据未绑定到已识别仓库。")
            return
        path = _safe_relative_path(item.get("path"))
        if path is None:
            blockers.append("字段或路径来源证据路径不属于允许修改范围。")
            return
        if path in paths:
            proven.add(path)
            continue
        if item.get("kind") != "field_source":
            blockers.append("字段或路径来源证据路径不属于允许修改范围。")
            return
    if not paths or not set(paths).issubset(proven):
        blockers.append("允许修改路径缺少已核验的字段或路径来源证据。")


def _verify_interface(value: object, blockers: list[str]) -> None:
    if not isinstance(value, Mapping) or not isinstance(value.get("required"), bool):
        blockers.append("接口契约核验记录格式无效。")
        return
    if "blockers" in value:
        contract_blockers = value["blockers"]
        if not isinstance(contract_blockers, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in contract_blockers):
            blockers.append("接口契约核验记录格式无效。")
        elif contract_blockers:
            blockers.append("接口契约核验仍包含未闭合阻断项。")
    if (value["required"] and value.get("status") != "verified") or (not value["required"] and value.get("status") != "not_required"):
        blockers.append("必需接口契约尚未核验。")


def _verify_default_value_precedence(calibration: Mapping[str, Any], provenance: object, blockers: list[str]) -> None:
    policy = calibration.get("default_value_precedence")
    if not isinstance(policy, Mapping) or policy.get("required") is not True:
        return
    if not default_value_precedence_is_resolved(dict(policy)):
        blockers.append("默认值来源优先级未完成需求确认。")
        return
    if not isinstance(provenance, Mapping):
        blockers.append("默认值来源优先级缺少源码取证记录。")
        return
    evidence = provenance.get("default_value_precedence")
    if not isinstance(evidence, Mapping) or evidence.get("required") is not True or evidence.get("status") != "verified":
        blockers.append("默认值来源优先级尚未由通用表单、参数、页面硬编码和无默认值的源码证据闭合。")
        return
    sources = evidence.get("sources")
    expected = ["common_form_setting", "parameter_setting", "page_hardcoded_default", "no_default"]
    if not isinstance(sources, (list, tuple)) or [item.get("source") for item in sources if isinstance(item, Mapping)] != expected:
        blockers.append("默认值来源优先级源码证据缺少固定四层顺序。")
        return
    if any(
        not isinstance(item, Mapping)
        or item.get("status") != "verified"
        or not isinstance(item.get("evidence"), (list, tuple))
        or not item.get("evidence")
        for item in sources
    ):
        blockers.append("默认值来源优先级源码证据不完整。")
    if not isinstance(evidence.get("precedence_chain"), (list, tuple)) or not evidence.get("precedence_chain"):
        blockers.append("默认值来源优先级未证明为同一初始化链路。")


def _verify_patch_decision(value: object, blockers: list[str]) -> None:
    if not isinstance(value, Mapping) or value.get("can_patch") is not True or value.get("blockers") not in ([], (), None):
        blockers.append("技术决策未明确允许本地受控修改。")


def _ownership_rows(value: Mapping[str, Any], blockers: list[str]) -> list[dict[str, Any]]:
    rows = value.get("rows")
    if value.get("status") != "ready" or not isinstance(rows, (list, tuple)) or len(rows) != 4 or "blockers" not in value or value.get("blockers") not in ([], ()):
        blockers.append("变更归属矩阵未达到四层 ready。")
        return []
    result: list[dict[str, Any]] = []
    for expected, row in zip(_OWNERSHIP_LAYERS, rows):
        if not isinstance(row, Mapping) or row.get("layer") != expected or row.get("status") not in _OWNERSHIP_READY_STATUSES or not isinstance(row.get("reason"), str) or not row["reason"].strip():
            blockers.append("变更归属矩阵未达到四层 ready。")
            return []
        has_mutation_flag = "mutation_required" in row
        mutation_required = row.get("mutation_required", False)
        if has_mutation_flag and not isinstance(mutation_required, bool):
            blockers.append("数据库 mutation_required 必须为严格布尔值。")
            return []
        if expected == "database":
            if row["status"] == "required":
                if has_mutation_flag and mutation_required is False:
                    blockers.append("数据库 required 归属不能声明 mutation_required=false。")
                    return []
                mutation_required = True
            elif mutation_required is True:
                blockers.append("数据库 mutation_required 与非 required 归属状态矛盾。")
                return []
        result.append({"layer": expected, "status": row["status"], "reason": row["reason"].strip(), "mutation_required": mutation_required})
    return result


def _commands(value: object, blockers: list[str]) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        blockers.append("缺少专项自动验证命令。")
        return []
    commands = [_safe_command(item) for item in value]
    if any(command is None for command in commands) or len(set(commands)) != len(commands):
        blockers.append("专项自动验证命令格式无效。")
        return []
    return [command for command in commands if command is not None]


def _verify_acceptance_matrix(acceptance: Mapping[str, Any], blockers: list[str]) -> None:
    value = acceptance.get("blockers")
    if value is None:
        return
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in value):
        blockers.append("验收矩阵阻断项格式无效。")
    elif value:
        blockers.append("验收矩阵存在未闭合阻断项。")


def _capability_list_is_valid(value: object) -> bool:
    return isinstance(value, (list, tuple)) and all(isinstance(item, str) and _CAPABILITY_NAME.fullmatch(item) for item in value)


def _verify_capability_shapes(technical: Mapping[str, Any], available: object, authorization: object, blockers: list[str]) -> None:
    required = technical.get("required_capabilities", ())
    if not _capability_list_is_valid(required) or not _capability_list_is_valid(available):
        blockers.append("能力列表必须全部为合法 capability 字符串。")
        return
    if authorization is not None:
        if not isinstance(authorization, Mapping) or not _capability_list_is_valid(authorization.get("capabilities", ())):
            blockers.append("受信授权能力列表格式无效。")


def _automatic_acceptance(value: object, trusted_commands: list[str], blockers: list[str]) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        blockers.append("缺少自动验收路径。")
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            blockers.append("自动验收记录格式无效。")
            return []
        source = item.get("source")
        raw_command = item.get("command")
        if (
            item.get("explicitly_executable") is False
            and source in {"evidence_suggested", "project_profile"}
            and raw_command not in trusted_commands
        ):
            continue
        command = _safe_command(raw_command)
        policy = item.get("execute_policy") or item.get("side_effect_policy")
        text = next((item.get(field) for field in ("expected_result", "scenario") if isinstance(item.get(field), str) and item[field].strip()), None)
        if command is None or command not in trusted_commands or not isinstance(source, str) or not source.strip() or not isinstance(policy, str) or not policy.strip():
            blockers.append("自动验收必须包含已批准验证命令、来源和执行策略。")
            return []
        result.append(text.strip() if text is not None else f"验证命令：{command}")
    return _unique(result)


def _acceptance(value: object, fields: tuple[str, ...], label: str, blockers: list[str]) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        blockers.append(f"缺少{label}路径。")
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            blockers.append(f"{label}记录格式无效。")
            return []
        text = next((item.get(field) for field in fields if isinstance(item.get(field), str) and item[field].strip()), None)
        if text is None:
            blockers.append(f"{label}记录缺少明确场景或预期。")
            return []
        result.append(text.strip())
    return _unique(result)


def _sibling_required(value: object, repositories: list[dict[str, Any]], source_repositories: object, blockers: list[str]) -> bool:
    if value is None:
        return False
    if not isinstance(value, Mapping) or not isinstance(value.get("required"), bool) or not isinstance(value.get("status"), str) or value.get("blockers") not in ([], (), None):
        blockers.append("sibling 影响记录格式无效。")
        return False
    if value["required"] and value["status"] not in {"identified", "verified"}:
        blockers.append("sibling 影响尚未完整识别。")
    if value["required"]:
        paths = {item["path"] for item in repositories}
        roles = {item["role"] for item in repositories}
        side_records = source_repositories if isinstance(source_repositories, (list, tuple)) else ()
        sides = {
            item["sibling_side"]
            for item in side_records if isinstance(item, Mapping)
            and isinstance(item.get("sibling_side"), str) and item["sibling_side"].strip()
        }
        if len(repositories) < 2 or len(paths) < 2 or (len(roles) < 2 and len(sides) < 2):
            blockers.append("sibling 影响要求可证明的不同仓库路径及两侧身份。")
    if not value["required"] and value["status"] != "not_required":
        blockers.append("sibling 影响状态矛盾。")
    return value["required"]


def _impacts(rows: list[dict[str, Any]], layer: str) -> list[dict[str, Any]]:
    row = next((item for item in rows if item["layer"] == layer), None)
    return [{"layer": layer, "status": row["status"], "reason": row["reason"]}] if row and row["status"] == "required" else []


def _database_mutation_required(rows: list[dict[str, Any]]) -> bool:
    return any(item["layer"] == "database" and item["mutation_required"] is True for item in rows)


def _verify_database_authority(technical: Mapping[str, Any], available: object, authorization: object, blockers: list[str]) -> None:
    capabilities = technical.get("required_capabilities")
    required = capabilities if isinstance(capabilities, (list, tuple)) else []
    valid_available = _capability_list_is_valid(available)
    auth = authorization if isinstance(authorization, Mapping) else {}
    approved = auth.get("explicit") is True and auth.get("approved") is True and _capability_list_is_valid(auth.get("capabilities")) and "database.mutate" in auth.get("capabilities", ())
    if not isinstance(required, (list, tuple)) or "database.mutate" not in required or not valid_available or "database.mutate" not in available or not approved:
        blockers.append("数据库变更缺少受信 database.mutate 能力或明确批准。")


def _adjacent_paths(objective: str, acceptance: Mapping[str, Any], sibling_required: bool, blockers: list[str]) -> list[str]:
    risk = acceptance.get("risk")
    risk_reasons: list[str] = []
    if isinstance(risk, Mapping) and risk.get("level") in {"high", "critical"}:
        reasons = risk.get("reasons")
        if not isinstance(reasons, (list, tuple)) or not reasons or any(not isinstance(item, str) or not item.strip() for item in reasons):
            blockers.append("高风险验收矩阵缺少有效风险原因。")
        else:
            risk_reasons = list(reasons)
    required: list[str] = []
    for term, paths in _HIGH_RISK_PATHS:
        if term in objective or any(term in reason for reason in risk_reasons):
            required.extend(paths)
    if sibling_required:
        required.append("paths-to-verify: sibling_parity")
    required = _unique(required)
    supplied = acceptance.get("adjacent_paths")
    if supplied is not None:
        if not isinstance(supplied, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in supplied) or not set(required).issubset(set(supplied)):
            blockers.append("高风险 HIS 相邻路径核验不完整。")
    return required


def _scope_and_rules(calibration: Mapping[str, Any], blockers: list[str]) -> tuple[list[str], list[str], list[dict[str, Any]], list[str]]:
    scope = calibration.get("resolved_scope")
    parameters = calibration.get("resolved_parameters")
    if not isinstance(scope, Mapping) or not isinstance(scope.get("do"), str) or not scope["do"].strip() or not isinstance(scope.get("do_not"), (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in scope["do_not"]):
        blockers.append("需求范围或保留行为记录格式无效。")
        return [], [], [], []
    if not isinstance(parameters, (list, tuple)) or any(_json_dict(item) is None for item in parameters):
        blockers.append("业务规则记录格式无效。")
        return [], [], [], []
    rules = [_json_dict(item) for item in parameters]
    return [scope["do"].strip()], [item.strip() for item in scope["do_not"]], [item for item in rules if item is not None], [item.strip() for item in scope["do_not"]]


def _unique(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, str) and value and value not in result:
            result.append(value)
    return result
