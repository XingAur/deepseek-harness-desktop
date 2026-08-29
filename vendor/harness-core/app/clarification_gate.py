from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field


_INTERNAL_RECOVERABLE_PREFIXES = (
    "archive_",
    "mcp_",
    "gitlab_",
    "yunxiao_",
    "database_",
    "project_scan_",
    "verification_",
    "worker_",
    "harness_",
)


def classify_harness_interaction(
    *,
    error_code: str | None = None,
    ambiguity_kind: str | None = None,
    evidence_available: bool = False,
) -> str:
    """Decide whether a stop is an internal recovery or a real user decision.

    This gate is intentionally conservative about asking the user: archive,
    MCP, project-scan, worker, verification, and Harness orchestration faults
    are implementation failures and must be retried/redecided by Harness.
    Only unresolved business choices or explicitly requested external writes
    may become user-facing interaction gates.
    """
    normalized_code = (error_code or "").strip().lower()
    if normalized_code.startswith(_INTERNAL_RECOVERABLE_PREFIXES):
        return "internal_recoverable"
    if ambiguity_kind in {"external_authorization", "external_write"}:
        return "external_authorization"
    if ambiguity_kind in {"business_choice", "requirement_choice", "scope_choice"}:
        return "business_clarification"
    if evidence_available and normalized_code in {"", "unknown"}:
        return "business_clarification"
    return "internal_recoverable"


@dataclass
class PatchReadinessResult:
    status: str
    can_patch: bool
    summary: str
    missing_items: list[str] = field(default_factory=list)
    confirmed_facts: list[str] = field(default_factory=list)
    candidate_root_causes: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    suggested_verify_commands: list[str] = field(default_factory=list)
    manual_confirmation_owner: str = "需求提交人 / HIS 产品负责人 / 现场实施"

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        lines = [
            "## v0.7.4 业务澄清闸口",
            "",
            f"- 状态：{self.status}",
            f"- 是否允许生成 patch：{'是' if self.can_patch else '否'}",
            f"- 结论：{self.summary}",
            f"- 默认修改路径：{', '.join(self.allowed_paths) if self.allowed_paths else '-'}",
            f"- 人工确认责任：{self.manual_confirmation_owner}",
            "",
            "### 已确认事实",
            "",
        ]
        lines.extend(f"- {item}" for item in self.confirmed_facts) if self.confirmed_facts else lines.append("- -")
        lines.extend(["", "### 候选根因", ""])
        lines.extend(f"- {item}" for item in self.candidate_root_causes) if self.candidate_root_causes else lines.append("- -")
        lines.extend(["", "### 缺失信息", ""])
        lines.extend(f"- {item}" for item in self.missing_items) if self.missing_items else lines.append("- -")
        lines.extend(["", "### 建议验证命令", ""])
        lines.extend(f"- `{item}`" for item in self.suggested_verify_commands) if self.suggested_verify_commands else lines.append("- -")
        return "\n".join(lines)


def evaluate_patch_readiness(
    *,
    demand_text: str,
    yunxiao_evidence: dict | None,
    requirement_evidence: dict | None = None,
    evidence_bundle: dict | None,
    technical_decision: dict | None = None,
    allowed_paths: list[str],
    verify_commands: list[str],
    yunxiao_read_requested: bool,
) -> PatchReadinessResult:
    combined_text = build_combined_text(
        demand_text=demand_text,
        yunxiao_evidence=yunxiao_evidence,
        requirement_evidence=requirement_evidence,
    )
    missing: list[str] = []
    facts: list[str] = []
    causes: list[str] = []

    if yunxiao_read_requested:
        status = (yunxiao_evidence or {}).get("status")
        if status not in {"success", "partial"}:
            missing.append("云效只读详情未成功读取，不能确认需求复现、期望和附件证据。")
        else:
            if status == "partial":
                facts.append("云效主需求已读取；部分评论/附件/正文图片证据不可用，已记录警告并继续分析，不执行任何云效写动作。")
            else:
                facts.append("云效只读证据已读取成功，后续只作为分析输入，不执行任何云效写动作。")
    elif requirement_evidence:
        if (
            requirement_evidence.get("readonly") is True
            and requirement_evidence.get("title")
            and requirement_evidence.get("description_text")
        ):
            facts.append("本地归一化需求证据已读取成功，后续只作为分析输入，不执行任何外部写动作。")
        else:
            missing.append("本地需求证据不完整，缺少只读标记、标题或正文。")

    if technical_decision:
        decision = technical_decision.get("implementation_decision") or {}
        provenance = technical_decision.get("field_provenance") or {}
        facts.append("v0.8.8 技术自治决策已生成，Harness 将按代码上下文决定前后端边界和改动文件。")
        if provenance.get("target_field"):
            facts.append(f"技术自治识别目标字段：{provenance.get('target_field')}")
        if decision.get("summary"):
            facts.append(f"技术自治结论：{decision.get('summary')}")
        if not decision.get("can_patch"):
            missing.extend(decision.get("blockers") or ["技术自治决策未允许自动 patch。"])
        for path in decision.get("allowed_paths") or []:
            causes.append(f"技术自治推荐改动路径：{path}")

    if is_legacy_discount_time_case(combined_text):
        if contains_any(combined_text, ["DFHIS-31195", "优惠项目", "优惠类别", "youHuiLb"]):
            facts.append("需求定位到优惠类别/优惠项目相关页面。")
        if contains_any(combined_text, ["不限时", "限时"]):
            facts.append("需求文本包含“不限时/限时”现象。")

        if not has_time_semantic(combined_text):
            missing.append("缺少“不限时”的明确含义：需确认是有效时间 youXiaoSJ/date 被清空、展示错误、查询条件错误，还是保存规则错误。")
        if not has_reproduction_or_expectation(combined_text, yunxiao_evidence):
            missing.append("缺少复现步骤、期望结果、实际结果、影响菜单或截图/附件证据。")

        for cause in collect_candidate_causes(evidence_bundle):
            causes.append(cause)
        if not causes:
            missing.append("工程证据中未确认有效时间字段和保存链路，不能安全定位改动点。")
    elif not evidence_bundle:
        missing.append("缺少工程证据包，不能安全定位改动点。")

    normalized_allowed_paths = [path for path in allowed_paths if path]
    if not normalized_allowed_paths:
        missing.append("缺少受控 patch 白名单路径。")
    elif normalized_allowed_paths != ["src/pages/feiYongGl/youHuiLb.vue"]:
        facts.append("本轮白名单由命令显式指定，Harness 将按白名单限制 patch 范围。")
    else:
        facts.append("本轮默认只允许修改主页面 src/pages/feiYongGl/youHuiLb.vue。")

    normalized_verify_commands = [command for command in verify_commands if command]
    if not normalized_verify_commands:
        missing.append("缺少显式验证命令；v0.7.4 不自动猜测 lint/build。")

    if missing:
        return PatchReadinessResult(
            status="blocked_needs_clarification",
            can_patch=False,
            summary="证据不足，已阻断 patch 生成；需要先补齐云效详情或人工确认项。",
            missing_items=unique_keep_order(missing),
            confirmed_facts=unique_keep_order(facts),
            candidate_root_causes=unique_keep_order(causes),
            allowed_paths=normalized_allowed_paths,
            suggested_verify_commands=normalized_verify_commands,
        )

    return PatchReadinessResult(
        status="ready",
        can_patch=True,
        summary="需求语义、工程证据和验证命令满足 v0.7.4 受控 patch 前置条件。",
        confirmed_facts=unique_keep_order(facts),
        candidate_root_causes=unique_keep_order(causes),
        allowed_paths=normalized_allowed_paths,
        suggested_verify_commands=normalized_verify_commands,
    )


def build_combined_text(
    *,
    demand_text: str,
    yunxiao_evidence: dict | None = None,
    requirement_evidence: dict | None = None,
) -> str:
    parts = [demand_text]
    for evidence in (yunxiao_evidence, requirement_evidence):
        if not evidence:
            continue
        parts.append(str(evidence.get("title") or ""))
        parts.append(str(evidence.get("description_text") or ""))
        parts.append(str(evidence.get("clean_text") or ""))
        parts.append(str(evidence.get("text_excerpt") or ""))
        parts.append(json.dumps(evidence.get("work_item") or {}, ensure_ascii=False))
        parts.append(json.dumps(evidence.get("attachments") or [], ensure_ascii=False))
        parts.append(json.dumps(evidence.get("inline_files") or [], ensure_ascii=False))
        parts.append(json.dumps(evidence.get("file_details") or [], ensure_ascii=False))
    return "\n".join(parts)


def is_legacy_discount_time_case(text: str) -> bool:
    return contains_any(text, ["DFHIS-31195", "优惠项目", "优惠类别", "youHuiLb"]) or (
        contains_any(text, ["优惠", "减免"]) and contains_any(text, ["不限时", "限时", "有效期"])
    )


def has_time_semantic(text: str) -> bool:
    evidence_text = strip_uncertain_placeholder_lines(text)
    if contains_any(
        evidence_text,
        [
            "youXiaoSJ",
            "有效时间",
            "有效期",
            "date",
            "开始时间",
            "结束时间",
            "生效时间",
            "失效时间",
            "时间控件",
            "查询条件",
            "保存规则",
            "展示错误",
            "被清空",
            "清空",
            "为空",
        ],
    ):
        return True
    return any(re.search(pattern, evidence_text) for pattern in [r"保存后.*不限时", r"新增后.*不限时"])


def has_reproduction_or_expectation(text: str, yunxiao_evidence: dict | None) -> bool:
    evidence_text = strip_uncertain_placeholder_lines(text)
    if contains_any(evidence_text, ["复现", "步骤", "期望", "实际", "结果", "截图", "菜单", "账号", "角色", "保存后", "新增后"]):
        return True
    evidence = yunxiao_evidence or {}
    return bool(evidence.get("attachments") or evidence.get("inline_files") or evidence.get("inline_file_downloads"))


def strip_uncertain_placeholder_lines(text: str) -> str:
    uncertain_markers = ["待确认", "需要从云效", "需要人工", "需人工", "需要补充", "待补充", "需要提供"]
    lines = []
    in_uncertain_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            in_uncertain_block = False
            lines.append(line)
            continue
        if any(marker in line for marker in uncertain_markers):
            in_uncertain_block = True
            continue
        if in_uncertain_block and stripped.startswith(("-", "*")):
            continue
        if in_uncertain_block and re.match(r"\d+[.、)]", stripped):
            continue
        lines.append(line)
    return "\n".join(lines)


def collect_candidate_causes(evidence_bundle: dict | None) -> list[str]:
    causes: list[str] = []
    if not evidence_bundle:
        return causes
    raw = json.dumps(evidence_bundle, ensure_ascii=False)
    if "youXiaoSJ" in raw:
        causes.append("类型定义或代码证据中存在有效时间字段 youXiaoSJ。")
    if "delete item.date" in raw or ("date" in raw and "youHuiXmMxList" in raw):
        causes.append("优惠明细保存链路存在 date 字段清理或明细列表保存证据，需要确认是否导致“不限时”。")
    for item in evidence_bundle.get("evidence_files", []):
        path = item.get("path", "")
        if not is_discount_category_evidence_path(path):
            continue
        keywords = item.get("matched_keywords") or item.get("keywords") or []
        snippets = "\n".join(item.get("snippets", []))
        if any(keyword in keywords for keyword in ["youXiao", "youXiaoSJ", "有效时间", "有效期", "开始时间", "结束时间", "时间"]):
            causes.append(f"{path} 命中有效时间/日期相关关键词，需要作为候选证据核对。")
        if "delete item" in snippets and path.endswith(".vue"):
            causes.append(f"{path} 存在页面保存前清理字段片段，需要确认是否影响有效时间。")
        if path.endswith("youHuiLb.vue") and ("date" in snippets or "youXiao" in snippets):
            causes.append(f"{path} 命中有效时间/日期相关片段。")
    return unique_keep_order(causes)


def is_discount_category_evidence_path(path: str) -> bool:
    return any(
        marker in path
        for marker in [
            "src/pages/feiYongGl/youHuiLb",
            "src/apis/feiYongGl/youHuiLb",
            "src/types/modules/feiYongGl.d.ts",
        ]
    )


def contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def unique_keep_order(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
