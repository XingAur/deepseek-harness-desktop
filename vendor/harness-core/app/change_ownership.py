from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any


CHANGE_OWNERSHIP_SCHEMA_VERSION = "1.0-change-ownership-matrix"
LAYER_ORDER = ("frontend", "backend", "database", "configuration")
VALID_STATUSES = {"required", "not_required", "already_satisfied", "unresolved"}


@dataclass(frozen=True)
class ChangeOwnershipRow:
    layer: str
    status: str
    reason: str
    evidence: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChangeOwnershipMatrix:
    schema_version: str
    status: str
    rows: tuple[ChangeOwnershipRow, ...]
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    source_policy: str = ""

    def row(self, layer: str) -> ChangeOwnershipRow:
        for item in self.rows:
            if item.layer == layer:
                return item
        raise KeyError(layer)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        labels = {
            "frontend": "前端",
            "backend": "后端",
            "database": "数据库",
            "configuration": "配置/路由",
        }
        lines = [
            "## v0.58 需求变更归属矩阵",
            "",
            f"- 状态：{self.status}",
            f"- 证据规则：{self.source_policy}",
            "",
            "| 层级 | 结论 | 原因 | 证据 |",
            "| --- | --- | --- | --- |",
        ]
        for item in self.rows:
            evidence = "; ".join(
                str(entry.get("path") or entry.get("summary") or entry.get("source_kind") or "-")
                for entry in item.evidence
            ) or "-"
            lines.append(
                f"| {labels.get(item.layer, item.layer)} | {item.status} | "
                f"{item.reason.replace('|', '/')} | {evidence.replace('|', '/')} |"
            )
        if self.blockers:
            lines.extend(["", "### 阻断项", "", *[f"- {item}" for item in self.blockers]])
        if self.warnings:
            lines.extend(["", "### 提醒", "", *[f"- {item}" for item in self.warnings]])
        return "\n".join(lines)


def build_change_ownership_matrix(
    *,
    user_instruction: str,
    requirement_text: str,
    technical_decision: dict[str, Any],
) -> ChangeOwnershipMatrix:
    user_text = (user_instruction or "").strip()
    source_text = (requirement_text or "").strip()
    selected_projects = technical_decision.get("selected_projects") or []
    provenance = technical_decision.get("field_provenance") or {}
    boundary_conflicts = [
        item
        for item in (provenance.get("service_graph") or {}).get("boundary_findings") or []
        if isinstance(item, dict)
        and item.get("status") == "conflict"
        and (
            item.get("architecture_decision") != "auto_resolved"
            or item.get("requires_code_change")
        )
    ]
    business_rule_conflicts = [
        item
        for item in (provenance.get("service_graph") or {}).get("business_rule_findings") or []
        if isinstance(item, dict) and item.get("status") == "conflict"
    ]
    contract = technical_decision.get("contract_verification") or {}
    layers = contract.get("layers") or {}

    frontend_evidence = project_evidence(selected_projects, "frontend")
    frontend_evidence.extend(provenance_evidence(provenance))
    backend_evidence = project_evidence(selected_projects, "backend")
    server_contract = layers.get("server_contract") or {}
    server_contract_evidence = source_contract_evidence(server_contract)
    client_contract = layers.get("client_request") or {}
    client_contract_evidence = source_contract_evidence(client_contract)

    user_backend_confirmation = has_backend_completion_confirmation(user_text)
    source_backend_claim = has_backend_completion_confirmation(source_text)
    explicit_backend_no_change = has_explicit_backend_no_change(user_text)
    explicit_backend_change = has_explicit_backend_change(user_text) and not user_backend_confirmation
    service_contract_required = bool(contract.get("required"))
    explicit_frontend_no_change = has_explicit_frontend_no_change(user_text)
    frontend_expected = not explicit_frontend_no_change and bool(
        frontend_evidence
        or client_contract_evidence
        or contains_any(user_text, ("前端", "客户端", "页面", "界面", "标签页", "科室树", "弹框", "按钮"))
        or service_contract_required
    )

    rows: list[ChangeOwnershipRow] = []
    if frontend_expected:
        evidence = unique_evidence([*frontend_evidence, *client_contract_evidence])
        if evidence:
            rows.append(ChangeOwnershipRow("frontend", "required", "需求涉及客户端行为，且已定位前端工程证据。", tuple(evidence)))
        else:
            rows.append(ChangeOwnershipRow("frontend", "unresolved", "需求涉及客户端行为，但尚未定位可修改的前端源码证据。"))
    else:
        rows.append(ChangeOwnershipRow("frontend", "not_required", "未发现前端行为或客户端契约变更信号。"))

    if boundary_conflicts or business_rule_conflicts:
        boundary_message = "；".join(
            str(item.get("message") or "存在未决的数据来源服务边界冲突")
            for item in [*boundary_conflicts, *business_rule_conflicts]
        )
        reason_prefixes = []
        if boundary_conflicts:
            reason_prefixes.append("数据来源边界存在跨服务直连证据")
        if business_rule_conflicts:
            reason_prefixes.append("审批属性规则存在非严格标志判断")
        reason_prefix = "、".join(reason_prefixes)
        rows.append(
            ChangeOwnershipRow(
                "backend",
                "unresolved",
                reason_prefix + "，必须先确认后端责任归属和严格标志语义。" + boundary_message,
                tuple(
                    evidence
                    for item in [*boundary_conflicts, *business_rule_conflicts]
                    for evidence in (
                        [{
                            "source_kind": "service_boundary" if item in boundary_conflicts else "business_rule",
                            "path": str(item.get("path") or ""),
                            "summary": str(item.get("message") or ""),
                        }]
                    )
                ),
            )
        )
    elif explicit_backend_change:
        evidence = unique_evidence([*backend_evidence, *server_contract_evidence])
        if evidence:
            rows.append(ChangeOwnershipRow("backend", "required", "用户明确要求本次修改后端，且已定位后端证据。", tuple(evidence)))
        else:
            rows.append(ChangeOwnershipRow("backend", "unresolved", "用户明确要求修改后端，但尚未定位后端源码或接口契约证据。"))
    elif user_backend_confirmation:
        rows.append(
            ChangeOwnershipRow(
                "backend",
                "already_satisfied",
                "用户在本次指令中明确确认后端已完成或已验证，本次不自动改后端。",
                ({"source_kind": "user_confirmation", "summary": compact(user_text)},),
            )
        )
    elif explicit_backend_no_change and not service_contract_required:
        rows.append(ChangeOwnershipRow("backend", "not_required", "用户明确限定本次不修改后端，且需求未触发跨层接口契约核验。"))
    elif service_contract_required:
        if server_contract.get("status") == "verified" and server_contract_evidence:
            rows.append(
                ChangeOwnershipRow(
                    "backend",
                    "already_satisfied",
                    "后端接口契约已由源码证据核验，本次无需推测性修改后端。",
                    tuple(server_contract_evidence),
                )
            )
        else:
            reason = "跨层参数或接口需求缺少后端源码契约证据。"
            if source_backend_claim:
                reason += "需求正文或评论中的‘后端已改’只能作为线索，不能单独放行。"
            rows.append(ChangeOwnershipRow("backend", "unresolved", reason))
    else:
        rows.append(ChangeOwnershipRow("backend", "not_required", "未发现需要后端参与的接口或服务端变更信号。"))

    database_change = has_explicit_database_change(user_text)
    if database_change:
        rows.append(ChangeOwnershipRow("database", "unresolved", "用户明确要求数据库结构或 SQL 变更，但当前技术证据未形成数据库变更契约。"))
    else:
        rows.append(ChangeOwnershipRow("database", "not_required", "未发现数据库结构、SQL 或存储过程变更要求；只读查询也不等于代码变更。"))

    configuration_change = contains_any(user_text, ("菜单参数", "菜单/路由参数", "路由参数", "配置项", "参数配置"))
    if configuration_change:
        evidence = unique_evidence(frontend_evidence)
        if evidence:
            rows.append(ChangeOwnershipRow("configuration", "required", "需求明确涉及菜单、路由或配置参数，已关联到客户端工程证据。", tuple(evidence)))
        else:
            rows.append(ChangeOwnershipRow("configuration", "unresolved", "需求涉及菜单、路由或配置参数，但尚未定位配置消费位置。"))
    else:
        rows.append(ChangeOwnershipRow("configuration", "not_required", "未发现菜单、路由或配置项变更信号。"))

    ordered_rows = tuple(next(item for item in rows if item.layer == layer) for layer in LAYER_ORDER)
    blockers = tuple(
        f"需求变更归属未闭合：{row.layer} - {row.reason}"
        for row in ordered_rows
        if row.status == "unresolved"
    )
    warnings: list[str] = []
    if source_backend_claim and not user_backend_confirmation and not server_contract_evidence:
        warnings.append("检测到正文或评论声称后端已完成，但没有后端源码契约证据。")
    return ChangeOwnershipMatrix(
        schema_version=CHANGE_OWNERSHIP_SCHEMA_VERSION,
        status="blocked" if blockers else "ready",
        rows=ordered_rows,
        blockers=blockers,
        warnings=tuple(warnings),
        source_policy="用户当次明确指令优先；评论仅作线索；服务端完成状态必须由源码契约证据或用户明确确认支撑。",
    )


def project_evidence(projects: list[Any], role: str) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for item in projects:
        if not isinstance(item, dict) or item.get("role") != role or not item.get("exists"):
            continue
        evidence.append(
            {
                "source_kind": "project_scan",
                "project": str(item.get("name") or ""),
                "path": str(item.get("path") or ""),
            }
        )
    return evidence


def provenance_evidence(provenance: dict[str, Any]) -> list[dict[str, Any]]:
    if not provenance.get("target_ui_found"):
        return []
    evidence: list[dict[str, Any]] = []
    target_ui_paths = {str(path) for path in provenance.get("target_ui_paths") or [] if str(path).strip()}
    for item in provenance.get("evidence") or []:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        kind = str(item.get("kind") or "")
        path = str(item.get("path") or "")
        if kind not in {"target_ui", "explicit_target_ui", "explicit_allowlisted_source"} and path not in target_ui_paths:
            continue
        evidence.append(
            {
                "source_kind": "source_scan",
                "project": str(item.get("project") or ""),
                "path": path,
                "summary": str(item.get("reason") or ""),
            }
        )
    return evidence


def source_contract_evidence(layer: dict[str, Any]) -> list[dict[str, Any]]:
    if layer.get("status") != "verified":
        return []
    return [
        {"source_kind": "source_contract", "path": str(path), "summary": str(layer.get("summary") or "")}
        for path in layer.get("evidence_paths") or []
        if str(path).strip()
    ]


def has_backend_completion_confirmation(text: str) -> bool:
    return bool(
        re.search(
            r"(?:后端|服务端)[^，,。；\n]{0,16}(?:已经|已|有人)[^，,。；\n]{0,12}(?:改|调整|完成|支持|验证通过)",
            text,
        )
    )


def has_explicit_backend_change(text: str) -> bool:
    if has_explicit_backend_no_change(text):
        return False
    return bool(
        re.search(r"(?:后端|服务端)[^，,。；\n]{0,24}(?:新增|增加|修改|调整|实现|开发)", text)
        or re.search(r"(?:新增|增加|修改|调整)[^，,。；\n]{0,20}(?:后端|服务端)", text)
    )


def has_explicit_backend_no_change(text: str) -> bool:
    return bool(
        re.search(r"(?:后端|服务端)[^，,。；\n]{0,12}(?:不需要|无需|不必|不再)[^，,。；\n]{0,8}(?:修改|调整|处理|变更)", text)
        or re.search(r"(?:不需要|无需|不必|不再)[^，,。；\n]{0,8}(?:修改|调整|处理|变更)(?:后端|服务端)", text)
        or re.search(
            r"(?:不|不要|无需|无须)(?:新增|增加|修改|调整|处理|变更)[^，,。；\n]{0,24}(?:BFF|后端|服务端)",
            text,
            flags=re.IGNORECASE,
        )
        or has_grouped_no_change(text, ("BFF", "API", "后端", "服务端"))
    )


def has_explicit_frontend_no_change(text: str) -> bool:
    return bool(
        re.search(r"(?:前端|客户端|页面)[^，,。；\n]{0,12}(?:不需要|无需|不必|不再)[^，,。；\n]{0,8}(?:修改|调整|处理|变更)", text)
        or re.search(r"(?:不需要|无需|不必|不再)[^，,。；\n]{0,8}(?:修改|调整|处理|变更)(?:前端|客户端|页面)", text)
        or re.search(r"本次[^，,。；\n]{0,8}不修改(?:前端|客户端|页面)", text)
    )


def has_explicit_database_change(text: str) -> bool:
    if has_grouped_no_change(text, ("数据库", "数据表", "表结构", "数据库字段")):
        return False
    if re.search(r"(?:不需要|无需|不要)[^。；\n]{0,12}(?:查询)?数据库", text) or re.search(
        r"(?:不|不要|无需|无须)(?:新增|增加|修改|调整|删除|迁移|落库|建表)[^，,。；\n]{0,24}(?:数据库|数据表|表结构|数据库字段)",
        text,
    ):
        return False
    database_term = r"(?:数据库|数据表|表结构|数据库字段|SQL|sql|存储过程)"
    action = r"(?:新增|增加|修改|调整|删除|迁移|落库|建表)"
    return bool(
        re.search(database_term + r"[^。；\n]{0,24}" + action, text)
        or re.search(action + r"[^。；\n]{0,24}" + database_term, text)
    )


def has_grouped_no_change(text: str, terms: tuple[str, ...]) -> bool:
    grouped_terms = r"(?:" + "|".join(re.escape(term) for term in terms) + r")"
    all_layers = r"(?:前端|客户端|页面|BFF|API|公共\s*API|后端|服务端|数据库|数据表|表结构|数据库字段)"
    pattern = re.compile(
        all_layers
        + r"(?:[、,/和及\s]*"
        + all_layers
        + r")*[^。；\n]{0,12}(?:均|都)?(?:不应|不需要|无需|不必|不再|不得)"
        + r"(?:修改|调整|处理|变更|新增|增加|写入)",
        flags=re.IGNORECASE,
    )
    return any(re.search(grouped_terms, match.group(0), flags=re.IGNORECASE) for match in pattern.finditer(text or ""))


def contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def unique_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        key = (
            str(item.get("source_kind") or ""),
            str(item.get("project") or ""),
            str(item.get("path") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def compact(text: str, limit: int = 240) -> str:
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 3] + "..."
