from __future__ import annotations

import json
import re

from app.contract_plugins import apply_contract_plugins


CALIBRATION_VERSION = "0.16-requirement-calibration"

OVERRIDE_MARKERS = ["按照我说", "按我说", "不要按照需求图", "不按需求图", "以我说的为准", "用户补充"]
HIGH_RISK_TERMS = ["医保", "结算", "收费", "报表", "对账", "政策", "金额", "基金", "统筹", "回写"]
HARNESS_RULES_BLOCK = re.compile(r"```harness-rules\s*(.*?)```", flags=re.IGNORECASE | re.DOTALL)
TITLE_CONTEXT_SEPARATOR = re.compile(r"\s*--\s*", flags=re.DOTALL)
TITLE_HOSPITAL_PREFIX = re.compile(r"^\s*(?:【[^】]+】\s*)+")
CONTEXT_ONLY_PREFIX_RISK_TERMS = {"收费"}
NEGATED_SCOPE_CLAUSE = re.compile(
    r"(?:本轮)?(?:不|不要|无需|无须|不应|不得|禁止)(?:自动|再)?"
    r"(?:修改|调整|新增|增加|变更|涉及|影响|处理|关心|改变|执行|写入|回写|部署|发布|提交|推送|显示|另查|查询|经过)"
    r"[^。；\n]*"
)
GROUPED_NEGATED_SCOPE_CLAUSE = re.compile(
    r"(?:前端|客户端|页面|BFF|API|公共\s*API|后端|服务端|数据库|数据表|表结构|数据库字段)"
    r"(?:[、,/和及\s]*(?:前端|客户端|页面|BFF|API|公共\s*API|后端|服务端|数据库|数据表|表结构|数据库字段))*"
    r"[^。；\n]{0,12}(?:均|都)?(?:不应|不需要|无需|不必|不再|不得)"
    r"(?:修改|调整|处理|变更|新增|增加|写入)[^。；\n]*",
    flags=re.IGNORECASE,
)
DELIVERY_CONTEXT_CLAUSE = re.compile(
    r"[^。；\n]*(?:Harness\s*隔离\s*worktree|原工作区|原仓库有\s*rebase_merge|Git\s*远程写入|云效写入)"
    r"[^。；\n]*[。；]?",
    flags=re.IGNORECASE,
)
CALIBRATION_CONTEXT_SECTION = re.compile(
    r"\n\s*(?:只读代码证据|当前本地仓库边界)\s*[：:]",
    flags=re.IGNORECASE,
)
CONTEXTUAL_MODULE_RISK_LABEL = re.compile(
    r"挂号收费(?=(?:列表|页面|界面|模块|系统|--|—|-))"
)
NATURAL_VALUE_TOKEN = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]{2,})\s*(?:[=＝]|为|是|等于)\s*([A-Za-z0-9_.-]+)"
)
NATURAL_IDENTIFIER_TOKEN = re.compile(r"\b[A-Za-z][A-Za-z0-9_]{2,}\b")
NATURAL_FIELD_CONTEXT = (
    "字段", "参数", "表", "菜单", "接口", "来自", "对应", "关联", "来源", "组合", "数据",
    "前提", "必须", "满足", "判断", "自费", "上传", "住院", "门诊",
)
NATURAL_IDENTIFIER_IGNORES = frozenset(
    {"API", "BFF", "DFHIS", "GitHub", "GitLab", "HIS", "Harness", "JSON", "Jira", "OpenAPI", "TAPD", "URL", "Yunxiao"}
)
COMPOSITE_FLAG_SPECS = (
    ("门诊自费", ("门诊自费",), "menzhenbz", "zifeibz"),
    ("门诊部上传", ("门诊部上传", "门诊上传", "门诊不上传"), "menzhenbz", "bushangchuanbz"),
    ("住院自费", ("住院自费", "住院一样"), "zhuyuanbz", "zifeibz"),
    ("住院部上传", ("住院部上传", "住院上传", "住院不上传", "住院一样"), "zhuyuanbz", "bushangchuanbz"),
)
COMPOSITE_FLAG_FIELDS = frozenset(item for _, _, gate, flag in COMPOSITE_FLAG_SPECS for item in (gate, flag))
DEFAULT_VALUE_PRECEDENCE_SOURCES = (
    "common_form_setting",
    "parameter_setting",
    "page_hardcoded_default",
    "no_default",
)
DEFAULT_VALUE_PRECEDENCE_SOURCE_MARKERS = {
    "common_form_setting": ("通用表单", "表单设置"),
    "parameter_setting": ("参数设置", "参数默认", "参数配置"),
    "page_hardcoded_default": ("写死", "硬编码", "页面默认"),
    "no_default": ("没有默认值", "无默认值", "不设置默认值"),
}


def build_requirement_calibration(
    *,
    title: str,
    demand_text: str,
    yunxiao_evidence: dict | None = None,
    requirement_evidence: dict | None = None,
    user_instruction: str = "",
    project_paths: list[str] | None = None,
) -> dict:
    user_text = extract_calibration_requirement_text(user_instruction.strip() or demand_text.strip())
    demand_requirement_text = extract_calibration_requirement_text(demand_text)
    yunxiao_text = extract_calibration_requirement_text(clean_yunxiao_text(yunxiao_evidence))
    requirement_evidence_text = extract_calibration_requirement_text(
        clean_requirement_evidence_text(requirement_evidence)
    )
    combined_text = "\n".join(
        part
        for part in [title, demand_requirement_text, user_text, yunxiao_text, requirement_evidence_text]
        if part
    )
    user_overrides = has_any(user_text, OVERRIDE_MARKERS) or bool(extract_explicit_harness_rules(user_text))
    contract_plugin_matches = apply_contract_plugins(combined_text, user_overrides=user_overrides)
    parameters = extract_resolved_parameters(
        combined_text,
        user_overrides=user_overrides,
        contract_plugin_matches=contract_plugin_matches,
    )
    default_value_precedence = build_default_value_precedence(combined_text)
    composite_rules = extract_composite_flag_rules(combined_text, parameters)
    high_risk_hits = find_high_risk_terms(title=title, demand_text="\n".join([demand_text, user_text, yunxiao_text]))
    complexity = classify_complexity(
        combined_text=combined_text,
        parameters=parameters,
        high_risk_hits=high_risk_hits,
        default_value_precedence=default_value_precedence,
    )
    proposed_subtasks = build_proposed_subtasks(combined_text=combined_text, complexity_level=complexity["level"])
    warnings = build_calibration_warnings(
        user_overrides=user_overrides,
        user_text=user_text,
        yunxiao_text=yunxiao_text,
        parameters=parameters,
        composite_rules=composite_rules,
        default_value_precedence=default_value_precedence,
    )
    must_confirm = build_must_confirm_items(
        combined_text=combined_text,
        complexity_level=complexity["level"],
        parameters=parameters,
        default_value_precedence=default_value_precedence,
        user_overrides=user_overrides,
        high_risk_hits=high_risk_hits,
    )
    decision = build_calibration_decision(
        complexity_level=complexity["level"],
        parameters=parameters,
        composite_rules=composite_rules,
        default_value_precedence=default_value_precedence,
        must_confirm=must_confirm,
        warnings=warnings,
        user_overrides=user_overrides,
    )
    technical_investigation = build_default_value_technical_investigation(
        default_value_precedence=default_value_precedence,
    )
    if decision["can_enter_development"]:
        status = "ready_for_development"
    elif decision["can_enter_technical_analysis"]:
        status = "needs_technical_evidence"
    else:
        status = "needs_human_confirmation"
    return {
        "version": CALIBRATION_VERSION,
        "readonly": True,
        "yunxiao_write_enabled": False,
        "status": status,
        "title": title,
        "entity_id": infer_entity_id(title=title, demand_text=combined_text, yunxiao_evidence=yunxiao_evidence),
        "source_priority": build_source_priority(
            user_overrides=user_overrides,
            yunxiao_evidence=yunxiao_evidence,
            requirement_evidence=requirement_evidence,
        ),
        "resolved_scope": build_resolved_scope(
            combined_text=combined_text,
            parameters=parameters,
            user_overrides=user_overrides,
            high_risk_hits=high_risk_hits,
            contract_plugin_matches=contract_plugin_matches,
        ),
        "resolved_parameters": parameters,
        "default_value_precedence": default_value_precedence,
        "technical_investigation": technical_investigation,
        "composite_rules": composite_rules,
        "matched_contract_plugins": [
            {
                "pack_id": item["pack_id"],
                "pack_version": item["pack_version"],
                "plugin_id": item["plugin_id"],
                "plugin_version": item["plugin_version"],
            }
            for item in contract_plugin_matches
        ],
        "complexity": complexity,
        "proposed_subtasks": proposed_subtasks,
        "must_confirm": must_confirm,
        "warnings": warnings,
        "decision": decision,
        "project_paths": project_paths or [],
        "boundaries": [
            "本卡只校准需求理解，不自动修改业务代码。",
            "不自动写云效、不自动流转状态、不改负责人、不调迭代、不关闭任务。",
            "复杂或高风险需求必须先人工确认拆分结果和验收口径。",
        ],
    }


def requirement_calibration_to_markdown(card: dict) -> str:
    lines = [
        "## v0.15 需求理解确认卡",
        "",
        f"- 版本：{card.get('version') or CALIBRATION_VERSION}",
        f"- 状态：{card.get('status') or '-'}",
        f"- 只读：{'是' if card.get('readonly') else '否'}",
        f"- 云效写入：{'关闭' if not card.get('yunxiao_write_enabled') else '开启'}",
        f"- 结论：{(card.get('decision') or {}).get('summary') or '-'}",
        "",
        "### 来源优先级",
        "",
    ]
    for item in card.get("source_priority") or []:
        label = "用户补充规则优先" if item.get("source") == "user_instruction" and item.get("priority") == 1 else item.get("source")
        lines.append(f"- P{item.get('priority')}: {label}，原因：{item.get('reason') or '-'}")
    lines.extend(["", "### 本次理解范围", ""])
    scope = card.get("resolved_scope") or {}
    lines.append(f"- 要做：{scope.get('do') or '-'}")
    lines.append(f"- 不做：{'; '.join(scope.get('do_not') or []) or '-'}")
    lines.extend(["", "### 字段 / 参数", ""])
    parameters = card.get("resolved_parameters") or []
    if not parameters:
        lines.append("- 未识别到明确字段或参数。")
    for parameter in parameters:
        lines.append(
            f"- `{parameter.get('name')}`：位置={parameter.get('location') or '-'}，"
            f"来源={parameter.get('source') or '-'}"
        )
        values = parameter.get("allowed_values") or {}
        for value, meaning in values.items():
            lines.append(f"  - `{value}`：{meaning}")
    lines.extend(["", "### 默认值来源优先级", ""])
    precedence = card.get("default_value_precedence") or {}
    if not precedence.get("required"):
        lines.append("- 本需求未识别到多来源默认值覆盖规则。")
    elif precedence.get("status") != "resolved":
        lines.append(f"- 未闭合：{precedence.get('reason') or '默认值来源优先级不完整。'}")
    else:
        for step in precedence.get("steps") or []:
            lines.append(
                f"- P{step.get('priority')}: `{step.get('source')}`，"
                f"条件={step.get('condition') or '-'}；结果={step.get('behavior') or '-'}"
            )
    investigation = card.get("technical_investigation") or {}
    if investigation.get("required"):
        lines.extend(["", "### 自动源码追踪", ""])
        lines.append(f"- 状态：{investigation.get('status') or '-'}")
        lines.append("- 顺序：" + " -> ".join(investigation.get("source_order") or []) + "。")
        for target in investigation.get("targets") or []:
            lines.append(f"- 将自动追踪：{target}")
    lines.extend(["", "### 组合业务规则", ""])
    composite_rules = card.get("composite_rules") or []
    if not composite_rules:
        lines.append("- 未识别到组合条件规则。")
    for rule in composite_rules:
        conditions = " 且 ".join(
            f"`{item.get('field')}`=`{item.get('value')}`" for item in rule.get("conditions") or []
        )
        lines.append(f"- `{rule.get('name')}`：{conditions or '-'}；{rule.get('behavior') or '-'}")
    lines.extend(["", "### Contract Plugins", ""])
    plugins = card.get("matched_contract_plugins") or []
    if not plugins:
        lines.append("- 未命中版本化规则插件。")
    for plugin in plugins:
        lines.append(
            f"- `{plugin.get('plugin_id')}` v{plugin.get('plugin_version')} "
            f"(pack `{plugin.get('pack_id')}` v{plugin.get('pack_version')})"
        )
    lines.extend(["", "### 复杂度与拆分", ""])
    complexity = card.get("complexity") or {}
    lines.append(f"- 等级：{complexity.get('level') or '-'}")
    lines.append(f"- 原因：{'; '.join(complexity.get('reasons') or []) or '-'}")
    subtasks = card.get("proposed_subtasks") or []
    if not subtasks:
        lines.append("- 子任务：无需拆分。")
    for item in subtasks:
        lines.append(f"- {item.get('id') or '-'}：{item.get('title') or '-'}，边界：{item.get('boundary') or '-'}")
    lines.extend(["", "### 必须确认", ""])
    must_confirm = card.get("must_confirm") or []
    if not must_confirm:
        if (card.get("technical_investigation") or {}).get("required"):
            lines.append("- 无需用户确认；Harness 将先继续自动源码追踪。")
        else:
            lines.append("- 无；可进入受控开发前审查。")
    for item in must_confirm:
        lines.append(f"- {item}")
    lines.extend(["", "### Warning", ""])
    warnings = card.get("warnings") or []
    if not warnings:
        lines.append("- 无。")
    for warning in warnings:
        lines.append(f"- [{warning.get('type') or 'warning'}] {warning.get('message') or '-'}")
    lines.extend(["", "### 边界", ""])
    for item in card.get("boundaries") or []:
        lines.append(f"- {item}")
    return "\n".join(lines)


def requirement_calibration_to_json(card: dict) -> str:
    return json.dumps(card, ensure_ascii=False, indent=2)


def requirement_calibration_to_prompt_context(card: dict, *, limit: int = 4000) -> str:
    text = requirement_calibration_to_markdown(card)
    if len(text) <= limit:
        return text
    return text[: limit // 2] + "\n\n...（需求理解确认卡已压缩）...\n\n" + text[-limit // 2 :]


def clean_yunxiao_text(yunxiao_evidence: dict | None) -> str:
    if not yunxiao_evidence:
        return ""
    parts = [str(yunxiao_evidence.get("clean_text") or yunxiao_evidence.get("text_excerpt") or "")]
    for comment in yunxiao_evidence.get("comments") or []:
        if isinstance(comment, dict):
            parts.append(str(comment.get("content") or ""))
    return "\n".join(part for part in parts if part)


def clean_requirement_evidence_text(requirement_evidence: dict | None) -> str:
    if not requirement_evidence:
        return ""
    parts = [
        str(requirement_evidence.get("title") or ""),
        str(requirement_evidence.get("description_text") or ""),
    ]
    for comment in requirement_evidence.get("comments") or []:
        if isinstance(comment, dict):
            parts.append(str(comment.get("content") or comment.get("text") or ""))
        else:
            parts.append(str(comment or ""))
    return "\n".join(part for part in parts if part)


def extract_calibration_requirement_text(text: str) -> str:
    semantic = (text or "").split("【Harness v", 1)[0]
    return CALIBRATION_CONTEXT_SECTION.split(semantic, maxsplit=1)[0].strip()


def build_source_priority(
    *,
    user_overrides: bool,
    yunxiao_evidence: dict | None,
    requirement_evidence: dict | None = None,
) -> list[dict]:
    provider_evidence = requirement_evidence or yunxiao_evidence or {}
    provider_status = provider_evidence.get("status")
    provider_quality = provider_evidence.get("evidence_quality") or {}
    provider_ready = provider_status in {
        "success", "partial", "ready_for_analysis", "ready_for_analysis_with_warnings"
    } or provider_quality.get("analysis_ready") is True
    provider_source = (
        "yunxiao_evidence"
        if yunxiao_evidence or provider_evidence.get("source_type") == "yunxiao"
        else "provider_evidence"
    )
    if user_overrides:
        return [
            {"priority": 1, "source": "user_instruction", "reason": "用户明确要求按补充规则执行，覆盖需求图或云效描述中的不一致表达。"},
            {"priority": 2, "source": provider_source, "reason": "外部需求证据只作为背景和原始来源，不覆盖用户补充规则。"},
        ]
    if provider_ready:
        suffix = "；部分可选证据缺失但主需求可用" if provider_status in {"partial", "ready_for_analysis_with_warnings"} else ""
        return [
            {"priority": 1, "source": provider_source, "reason": f"已读取外部只读证据，作为需求原始来源{suffix}。"},
            {"priority": 2, "source": "user_instruction", "reason": "用户补充用于收窄范围或补充验收口径。"},
        ]
    return [
        {"priority": 1, "source": "user_instruction", "reason": "未读取到可靠云效证据，只能以用户输入作为当前分析来源。"},
        {"priority": 2, "source": "yunxiao_evidence", "reason": "云效证据缺失或读取失败时不得当作已确认事实。"},
    ]


def build_resolved_scope(
    *,
    combined_text: str,
    parameters: list[dict],
    user_overrides: bool,
    high_risk_hits: list[str] | None = None,
    contract_plugin_matches: list[dict] | None = None,
) -> dict:
    plugin_scopes = [item.get("scope") or {} for item in contract_plugin_matches or []]
    plugin_do = next((scope.get("do") for scope in plugin_scopes if scope.get("do")), "")
    if plugin_do:
        do_text = plugin_do
    elif parameters:
        do_text = "按显式结构化规则完成最小改动，并保留规则声明的默认行为。"
    elif high_risk_hits:
        do_text = "先校准高风险需求范围，拆分报表、接口、金额/对账和验收边界。"
    else:
        do_text = "按需求描述形成最小可验证改动范围。"
    do_not = [
        "不自动写云效",
        "不自动提交、推送或发布",
        "不根据截图或标题擅自新增隐含业务规则",
    ]
    for scope in plugin_scopes:
        do_not.extend(scope.get("do_not") or [])
    return {
        "do": do_text,
        "do_not": do_not,
        # Keep both human-readable and machine-checkable boundaries.  The
        # understanding gate consumes these fields; leaving them implicit
        # would make every otherwise-ready calibration appear incomplete.
        "in_scope": [do_text],
        "out_of_scope": list(do_not),
    }


def extract_resolved_parameters(
    text: str,
    *,
    user_overrides: bool | None = None,
    contract_plugin_matches: list[dict] | None = None,
) -> list[dict]:
    parameters = extract_explicit_harness_rules(text)
    effective_user_overrides = has_any(text, OVERRIDE_MARKERS) if user_overrides is None else user_overrides
    matches = contract_plugin_matches
    if matches is None:
        matches = apply_contract_plugins(text, user_overrides=effective_user_overrides)
    inferred_parameters = [parameter for match in matches for parameter in match.get("parameters") or []]
    has_explicit_request_parameters = any(parameter.get("location") == "request_param" for parameter in parameters)
    explicit_names = {parameter.get("name") for parameter in parameters}
    parameters.extend(
        parameter
        for parameter in inferred_parameters
        if parameter.get("name") not in explicit_names
        and not (has_explicit_request_parameters and parameter.get("location") == "request_param")
    )
    known_names = {parameter.get("name") for parameter in parameters}
    natural_parameters = (
        []
        if has_explicit_request_parameters
        else extract_natural_language_parameters(HARNESS_RULES_BLOCK.sub("", text))
    )
    parameters.extend(
        parameter
        for parameter in natural_parameters
        if parameter.get("name") not in known_names
    )
    return unique_parameters(parameters)


def extract_explicit_harness_rules(text: str) -> list[dict]:
    parameters: list[dict] = []
    for block in HARNESS_RULES_BLOCK.findall(text):
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            location = str(item.get("location") or "").strip()
            allowed_values = item.get("allowed_values")
            if not name or not location or not isinstance(allowed_values, dict):
                continue
            normalized_values = {
                str(key): str(value).strip()
                for key, value in allowed_values.items()
                if str(key).strip() and str(value).strip()
            }
            if not is_complete_parameter({"name": name, "allowed_values": normalized_values}):
                continue
            parameter = {
                "name": name,
                "location": location,
                "source": "explicit_harness_rule",
                "allowed_values": normalized_values,
            }
            for key in ("evidence_tokens", "default_evidence_tokens"):
                values = item.get(key)
                if isinstance(values, list):
                    parameter[key] = [str(value).strip() for value in values if str(value).strip()]
            parameters.append(parameter)
    return parameters


def extract_natural_language_parameters(text: str) -> list[dict]:
    """Expose user-named code fields without treating arbitrary prose as a rule."""
    detected: dict[str, dict] = {}

    def context_for(match: re.Match[str]) -> str:
        start = max(0, match.start() - 32)
        end = min(len(text), match.end() + 32)
        return text[start:end]

    for match in NATURAL_VALUE_TOKEN.finditer(text):
        context = context_for(match)
        if not any(marker in context for marker in NATURAL_FIELD_CONTEXT):
            continue
        name, value = match.groups()
        item = detected.setdefault(
            name,
            {
                "name": name,
                "location": "data_field",
                "source": "user_instruction",
                "allowed_values": {},
            },
        )
        item["allowed_values"][value] = value

    for match in NATURAL_IDENTIFIER_TOKEN.finditer(text):
        name = match.group(0)
        if name in NATURAL_IDENTIFIER_IGNORES:
            continue
        if re.match(r"\.(?:vue|js|jsx|ts|tsx|java|kt|json|md)\b", text[match.end() : match.end() + 12], flags=re.IGNORECASE):
            continue
        if name not in COMPOSITE_FLAG_FIELDS and "_" not in name and not re.search(r"[a-z][A-Z]", name):
            continue
        context = context_for(match)
        if any(marker in context for marker in ("worktree", "工作区", "原仓库", "rebase", "Git 远程", "云效写入")):
            continue
        if not any(marker in context for marker in NATURAL_FIELD_CONTEXT):
            continue
        detected.setdefault(
            name,
            {
                "name": name,
                "location": "data_source",
                "source": "user_instruction",
                "allowed_values": {
                    "source": "需求明确指定数据来源或关联对象",
                },
            },
        )
    if len(detected) == 1 and any(term in text for term in ("显示", "展示", "加上", "增加")):
        if re.search(
            r"(?:没有维护|未维护|为空|空值|没有值)[^。；\n]{0,28}(?:保持空白|显示为空|不显示\s*(?:undefined|null)|不展示)",
            text,
            flags=re.IGNORECASE,
        ):
            next(iter(detected.values()))["allowed_values"].setdefault(
                "empty",
                "来源字段未维护或为空时保持空白，不显示占位值。",
            )
    return list(detected.values())


def extract_composite_flag_rules(text: str, parameters: list[dict] | None = None) -> list[dict]:
    """Turn the outpatient/inpatient display flags into explicit all-of rules.

    The gate field is deliberately part of every rule.  This prevents a
    ``zifeibz`` or ``bushangchuanbz`` value from being interpreted as a global
    flag that applies to both 门诊 and 住院 rows.
    """

    source_text = text or ""
    parameter_names = {str(item.get("name") or "") for item in parameters or []}
    explicit_values: dict[str, set[str]] = {}
    for match in NATURAL_VALUE_TOKEN.finditer(source_text):
        explicit_values.setdefault(match.group(1), set()).add(match.group(2))
    rules: list[dict] = []
    for name, keywords, gate_field, flag_field in COMPOSITE_FLAG_SPECS:
        if not any(keyword in source_text for keyword in keywords):
            continue
        implicit_inpatient_gate = gate_field == "zhuyuanbz" and "住院一样" in source_text
        if gate_field not in parameter_names and gate_field not in explicit_values and not implicit_inpatient_gate:
            continue
        if flag_field not in parameter_names and flag_field not in explicit_values and flag_field not in source_text:
            continue
        gate_explicit = "1" in explicit_values.get(gate_field, set())
        flag_explicit = "1" in explicit_values.get(flag_field, set())
        if not gate_explicit and gate_field not in explicit_values and not implicit_inpatient_gate:
            continue
        conditions = [
            {
                "field": gate_field,
                "value": "1",
                "role": "scope_gate",
                "value_source": "explicit" if gate_explicit else "user_rule",
            },
            {
                "field": flag_field,
                "value": "1",
                "role": "display_flag",
                "value_source": "explicit" if flag_explicit else "label_semantics",
            },
        ]
        rules.append(
            {
                "name": name,
                "kind": "all_of",
                "conditions": conditions,
                "behavior": "仅当全部条件同时满足时命中该界面标记；其他组合不命中，不改写底层字段。",
                "source": "user_instruction",
            }
        )
    return rules


def classify_complexity(
    *,
    combined_text: str,
    parameters: list[dict],
    high_risk_hits: list[str] | None = None,
    default_value_precedence: dict | None = None,
) -> dict:
    high_risk_hits = list(high_risk_hits or [])
    reasons: list[str] = []
    if high_risk_hits:
        reasons.append("命中高风险业务词：" + ", ".join(high_risk_hits[:8]))
    if "并影响" in combined_text or len(high_risk_hits) >= 4:
        reasons.append("需求可能跨报表、接口、金额、对账或结算回写，需要拆分确认。")
    if (
        any(is_complete_parameter(parameter) for parameter in parameters)
        or default_value_precedence_is_resolved(default_value_precedence)
    ) and not high_risk_hits:
        return {"level": "simple", "reasons": ["规则名、位置、可验证行为和默认行为明确，且未命中高风险业务词。"]}
    if high_risk_hits and ("并影响" in combined_text or len(high_risk_hits) >= 4):
        return {"level": "complex", "reasons": reasons}
    if high_risk_hits:
        return {"level": "medium", "reasons": reasons}
    return {"level": "medium", "reasons": reasons or ["需求信息未完全结构化，按中等复杂度保守处理。"]}


def build_proposed_subtasks(*, combined_text: str, complexity_level: str) -> list[dict]:
    if complexity_level != "complex":
        return []
    subtasks = [
        {"id": "SCOPE-001", "title": "确认医保/结算业务口径", "boundary": "只确认字段含义、金额来源和政策口径，不改代码。"},
        {"id": "FRONTEND-001", "title": "页面/报表展示调整", "boundary": "仅处理展示列、查询条件和空值展示。"},
        {"id": "BACKEND-001", "title": "接口/字段来源核对", "boundary": "确认 DTO/API/BFF 是否已有字段，不擅自新增返回结构。"},
        {"id": "VERIFY-001", "title": "金额、对账和回写验收", "boundary": "列出自动验证和人工验收，不自动判定业务通过。"},
    ]
    if "数据库" in combined_text or "报表" in combined_text or "对账" in combined_text:
        subtasks.insert(3, {"id": "DATA-001", "title": "报表/对账 SQL 或数据来源核对", "boundary": "先只读追踪 SQL/PRT/报表数据来源。"})
    return subtasks


def build_calibration_warnings(
    *,
    user_overrides: bool,
    user_text: str,
    yunxiao_text: str,
    parameters: list[dict],
    composite_rules: list[dict] | None = None,
    default_value_precedence: dict | None = None,
) -> list[dict]:
    warnings: list[dict] = []
    if user_overrides and yunxiao_text:
        warnings.append(
            {
                "type": "source_conflict",
                "message": "用户补充规则明确覆盖需求图或云效描述，后续实现必须以用户补充为准。",
            }
        )
    covered_fields = {
        str(condition.get("field") or "")
        for rule in composite_rules or []
        for condition in rule.get("conditions") or []
    }
    for parameter in parameters:
        if str(parameter.get("name") or "") in covered_fields:
            continue
        if parameter.get("location") == "unknown":
            warnings.append({"type": "parameter_location_unclear", "message": f"参数 {parameter.get('name')} 未明确是路由、菜单还是接口参数。"})
        allowed_values = parameter.get("allowed_values") or {}
        if not allowed_values:
            warnings.append({"type": "parameter_values_unclear", "message": f"参数 {parameter.get('name')} 未明确值域和默认行为。"})
        elif (
            parameter.get("location") in {"data_field", "request_param"}
            and not is_complete_parameter(parameter)
        ):
            has_rule = any(
                str(key) not in {"empty", "default", "other"} and str(value).strip()
                for key, value in allowed_values.items()
            )
            if has_rule:
                message = f"参数 {parameter.get('name')} 已识别值域，但未明确空值/其他值的默认行为。"
            else:
                message = f"参数 {parameter.get('name')} 已识别默认行为，但未明确有效值域。"
            warnings.append({"type": "parameter_values_unclear", "message": message})
    if not user_text and not yunxiao_text:
        warnings.append({"type": "missing_requirement_text", "message": "缺少可校准的需求正文。"})
    if (
        isinstance(default_value_precedence, dict)
        and default_value_precedence.get("required")
        and not default_value_precedence_is_resolved(default_value_precedence)
    ):
        warnings.append(
            {
                "type": "default_value_precedence_unresolved",
                "message": "需求涉及通用表单、参数或页面默认值，但未完整确认来源优先级和无默认值兜底。",
            }
        )
    return warnings


def build_must_confirm_items(
    *,
    combined_text: str,
    complexity_level: str,
    parameters: list[dict],
    user_overrides: bool,
    high_risk_hits: list[str] | None = None,
    default_value_precedence: dict | None = None,
) -> list[str]:
    items: list[str] = []
    if complexity_level == "complex":
        items.append("医保/结算/收费/报表/对账口径必须由产品、测试或业务负责人确认。")
        items.append("金额字段来源、统计口径、回写路径和历史兼容必须逐项确认。")
    if (
        not parameters
        and not default_value_precedence_is_resolved(default_value_precedence)
        and has_any(combined_text, ["参数", "字段", "路由", "菜单"])
    ):
        items.append("需求提到字段或参数，但未识别到明确名称和值域。")
    if parameters and any(not parameter.get("allowed_values") for parameter in parameters):
        items.append("已识别参数，但值域或默认行为不完整。")
    if user_overrides and not parameters:
        items.append("用户要求覆盖需求图，但缺少明确字段、参数或行为规则。")
    if (
        isinstance(default_value_precedence, dict)
        and default_value_precedence.get("required")
        and not default_value_precedence_is_resolved(default_value_precedence)
    ):
        items.append("默认值来源优先级未完整确认：需明确通用表单、参数、页面硬编码和无默认值的覆盖顺序。")
    return unique_keep_order(items)


def find_high_risk_terms(*, title: str, demand_text: str) -> list[str]:
    """Ignore a module label such as ``挂号收费--`` without weakening real business-risk gates."""
    prefix, scoped_text = split_requirement_scope(title=title, demand_text=demand_text)
    scoped_text = normalize_business_risk_text(scoped_text)
    hits = [term for term in HIGH_RISK_TERMS if term in scoped_text]
    hits.extend(
        term
        for term in HIGH_RISK_TERMS
        if term not in CONTEXT_ONLY_PREFIX_RISK_TERMS and term in prefix
    )
    return unique_keep_order(hits)


def remove_negated_scope_clauses(text: str) -> str:
    """Remove explicit non-change boundaries before contract and risk classification."""
    cleaned = GROUPED_NEGATED_SCOPE_CLAUSE.sub("", text or "")
    return NEGATED_SCOPE_CLAUSE.sub("", cleaned)


def normalize_business_risk_text(text: str) -> str:
    """Keep delivery safeguards and module labels from becoming business-risk signals."""
    cleaned = (text or "").split("【Harness v", 1)[0]
    cleaned = remove_negated_scope_clauses(cleaned)
    cleaned = DELIVERY_CONTEXT_CLAUSE.sub("", cleaned)
    return CONTEXTUAL_MODULE_RISK_LABEL.sub("挂号", cleaned)


def split_requirement_scope(*, title: str, demand_text: str) -> tuple[str, str]:
    normalized_title = TITLE_HOSPITAL_PREFIX.sub("", title or "").strip()
    parts = TITLE_CONTEXT_SEPARATOR.split(normalized_title, maxsplit=1)
    if len(parts) == 2:
        prefix, subject = parts
    else:
        prefix, subject = "", normalized_title
    scoped_text = "\n".join(part for part in [subject, remove_negated_scope_clauses(demand_text)] if part)
    return prefix, scoped_text


def build_calibration_decision(
    *,
    complexity_level: str,
    parameters: list[dict],
    must_confirm: list[str],
    warnings: list[dict],
    user_overrides: bool,
    composite_rules: list[dict] | None = None,
    default_value_precedence: dict | None = None,
) -> dict:
    precedence_ready = not (
        isinstance(default_value_precedence, dict)
        and default_value_precedence.get("required")
    ) or default_value_precedence_is_resolved(default_value_precedence)
    has_complete_parameter = (
        any(is_complete_parameter(parameter) for parameter in parameters)
        or bool(composite_rules)
        or (
            isinstance(default_value_precedence, dict)
            and default_value_precedence.get("required") is True
            and precedence_ready
        )
    )
    blocking_warning_types = {"parameter_location_unclear", "parameter_values_unclear", "missing_requirement_text"}
    has_blocking_warning = any(warning.get("type") in blocking_warning_types for warning in warnings)
    can_enter = complexity_level == "simple" and has_complete_parameter and precedence_ready and not must_confirm and not has_blocking_warning
    can_enter_technical_analysis = (
        precedence_ready
        and isinstance(default_value_precedence, dict)
        and default_value_precedence.get("required") is True
        and complexity_level != "complex"
        and not must_confirm
        and not has_blocking_warning
    )
    can_auto_code = can_enter and user_overrides
    if can_enter:
        summary = "需求来源优先级、参数名、值域和默认行为已明确，可进入受控开发前审查。"
        confidence = "high"
    elif complexity_level == "complex":
        summary = "复杂或高风险需求需要先确认拆分、业务口径和验收标准，不能直接自动改码。"
        confidence = "low"
    elif can_enter_technical_analysis:
        summary = "默认值业务优先级已明确；Harness 将自动追踪通用表单、参数、页面硬编码和无默认值的源码证据，证据闭合前不生成 patch。"
        confidence = "medium"
    elif parameters:
        summary = "已识别需求中的字段和数据来源，但默认行为或验收边界仍需确认。"
        confidence = "medium"
    else:
        summary = "需求仍有未确认字段、参数、来源或验收边界，需先补齐。"
        confidence = "medium"
    return {
        "can_enter_development": can_enter,
        "can_enter_technical_analysis": can_enter_technical_analysis,
        "can_auto_code": can_auto_code,
        "needs_human_confirmation": not can_enter and not can_enter_technical_analysis,
        "confidence": confidence,
        "summary": summary,
    }


def build_default_value_precedence(text: str) -> dict:
    """Return a fail-closed default-value source order when the request requires one."""
    normalized = text or ""
    requires_precedence = "默认值" in normalized and any(
        marker in normalized
        for markers in DEFAULT_VALUE_PRECEDENCE_SOURCE_MARKERS.values()
        for marker in markers
    )
    if not requires_precedence:
        return {
            "required": False,
            "status": "not_required",
            "steps": [],
            "reason": "本需求未声明多来源默认值覆盖。",
        }

    positions: dict[str, int] = {}
    for source, markers in DEFAULT_VALUE_PRECEDENCE_SOURCE_MARKERS.items():
        candidates = [normalized.find(marker) for marker in markers if normalized.find(marker) >= 0]
        if candidates:
            positions[source] = min(candidates)
    ordered_sources = list(DEFAULT_VALUE_PRECEDENCE_SOURCES)
    has_explicit_priority = "优先" in normalized
    has_complete_sources = set(positions) == set(ordered_sources)
    appears_in_declared_order = has_complete_sources and [positions[source] for source in ordered_sources] == sorted(positions.values())
    if not (has_explicit_priority and has_complete_sources and appears_in_declared_order):
        return {
            "required": True,
            "status": "unresolved",
            "steps": [],
            "reason": "默认值来源未同时具备明确优先语义、四级来源和从高到低的覆盖顺序。",
        }
    steps = [
        {
            "priority": 1,
            "source": "common_form_setting",
            "condition": "通用表单已配置默认值",
            "behavior": "使用通用表单设置的默认值",
        },
        {
            "priority": 2,
            "source": "parameter_setting",
            "condition": "通用表单未配置且参数已配置默认值",
            "behavior": "使用参数默认值",
        },
        {
            "priority": 3,
            "source": "page_hardcoded_default",
            "condition": "通用表单和参数均未配置且页面存在硬编码默认值",
            "behavior": "使用页面硬编码默认值",
        },
        {
            "priority": 4,
            "source": "no_default",
            "condition": "前三类来源均不存在",
            "behavior": "不设置默认值",
        },
    ]
    return {
        "required": True,
        "status": "resolved",
        "steps": steps,
        "reason": "已从需求文本识别四级默认值来源与覆盖顺序。",
    }


def build_default_value_technical_investigation(*, default_value_precedence: dict | None) -> dict:
    """Describe source tracing that Harness performs instead of asking for code locations."""
    if not isinstance(default_value_precedence, dict) or not default_value_precedence.get("required"):
        return {
            "required": False,
            "status": "not_required",
            "source_order": [],
            "targets": [],
        }
    if not default_value_precedence_is_resolved(default_value_precedence):
        return {
            "required": True,
            "status": "blocked_requirement",
            "source_order": list(DEFAULT_VALUE_PRECEDENCE_SOURCES),
            "targets": [],
        }
    return {
        "required": True,
        "status": "pending_source_trace",
        "source_order": list(DEFAULT_VALUE_PRECEDENCE_SOURCES),
        "targets": [
            "通用表单设置的字段读取和空值判定路径",
            "参数默认值读取和空值判定路径",
            "页面硬编码默认值及其初始化触发点",
            "前三者均不存在时不覆盖字段的兜底路径",
        ],
    }


def default_value_precedence_is_resolved(value: object) -> bool:
    if not isinstance(value, dict) or value.get("required") is not True or value.get("status") != "resolved":
        return False
    steps = value.get("steps")
    if not isinstance(steps, list) or len(steps) != len(DEFAULT_VALUE_PRECEDENCE_SOURCES):
        return False
    return [step.get("source") for step in steps if isinstance(step, dict)] == list(DEFAULT_VALUE_PRECEDENCE_SOURCES)


def infer_entity_id(*, title: str, demand_text: str, yunxiao_evidence: dict | None) -> str:
    evidence_id = str((yunxiao_evidence or {}).get("work_item_id") or "")
    if evidence_id:
        return evidence_id
    match = re.search(r"DFHIS-\d+", f"{title}\n{demand_text}", flags=re.IGNORECASE)
    return match.group(0).upper() if match else ""


def has_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def is_complete_parameter(parameter: dict) -> bool:
    allowed_values = parameter.get("allowed_values") or {}
    if not parameter.get("name") or not isinstance(allowed_values, dict):
        return False
    has_default_behavior = any(str(key) in {"empty", "default", "other"} and str(value).strip() for key, value in allowed_values.items())
    has_rule = any(str(key) not in {"empty", "default", "other"} and str(value).strip() for key, value in allowed_values.items())
    return has_default_behavior and has_rule


def unique_parameters(parameters: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen: set[str] = set()
    for parameter in parameters:
        key = json.dumps(parameter, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(parameter)
    return result


def unique_keep_order(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
