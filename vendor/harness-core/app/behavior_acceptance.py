from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


BEHAVIOR_VERSION = "0.10.1"

INTERACTION_TERMS = [
    "弹框",
    "弹窗",
    "提示",
    "确定",
    "取消",
    "叉",
    "关闭",
    "进度条",
    "进度",
    "loading",
    "$alert",
    "$confirm",
    "catch",
]

SETTLEMENT_TERMS = ["收费", "结算", "医保", "三方支付", "退费", "支付超时", "进度详情"]

SENSITIVE_CODE_TERMS = [
    "$alert",
    "$confirm",
    "catch",
    "closeSettlementProgress",
    "failActiveSettlementProgressStep",
    "loading.close",
    "jieSuanDis",
    "diSanFangTf",
    "menZhenSfJs",
]

GENERIC_ALERT_FALLBACKS = ["收费结算失败", "结算失败", "操作失败", "失败"]


def build_behavior_acceptance(
    *,
    title: str,
    demand_text: str = "",
    diff_text: str = "",
    changed_paths: list[str] | None = None,
) -> dict:
    changed_paths = changed_paths or []
    human_context = "\n".join([title or "", demand_text or "", "\n".join(changed_paths)]).strip()
    diff = parse_unified_diff(diff_text)
    interaction_related = is_interaction_related(context_text=human_context, diff=diff)
    settlement_related = is_settlement_related(
        context_text=human_context,
        diff=diff,
        changed_paths=changed_paths,
        interaction_related=interaction_related,
    )

    assertions: dict[str, list[dict]] = {
        "must_happen": [],
        "must_not_happen": [],
        "preserve": [],
    }
    if interaction_related:
        assertions["must_happen"].append(
            assertion(
                "BEH-MUST-001",
                "关闭或取消提示类弹窗时，原本应继续执行的业务收尾流程必须继续执行。",
                "用户关闭 Element UI alert/confirm 会产生 reject，不能被误判为业务失败。",
            )
        )
        assertions["must_not_happen"].append(
            assertion(
                "BEH-NOT-001",
                "不得在原业务提示关闭后再额外弹出空提示、重复提示或与真实原因不一致的兜底提示。",
                "这类问题会让用户看到空白提示或错误原因被替换，属于交互回归。",
            )
        )
        assertions["preserve"].append(
            assertion(
                "BEH-PRESERVE-001",
                "保留原提示时机、关闭顺序、return 分支和 loading/progress 收尾顺序。",
                "交互修复只能隔离关闭动作副作用，不能重写业务失败语义。",
            )
        )
    if settlement_related:
        assertions["preserve"].append(
            assertion(
                "BEH-PRESERVE-SETTLEMENT-001",
                "收费/结算/医保/退费相关改动必须保留原结算、退费、进度条和异常路径边界。",
                "高敏感收费链路不能为了修复 UI 提示而新增业务规则或改变结算状态。",
            )
        )

    checks = evaluate_behavior_checks(diff=diff, interaction_related=interaction_related, settlement_related=settlement_related)
    status = classify_status(checks=checks, interaction_related=interaction_related)
    return {
        "version": BEHAVIOR_VERSION,
        "status": status,
        "summary": summarize_status(status=status, interaction_related=interaction_related, checks=checks),
        "interaction_related": interaction_related,
        "settlement_related": settlement_related,
        "changed_paths": changed_paths,
        "assertions": assertions,
        "checks": checks,
        "manual_acceptance": build_manual_acceptance(
            interaction_related=interaction_related,
            settlement_related=settlement_related,
            title=title,
        ),
        "gate": {
            "auto_commit_allowed": status in {"pass", "skipped"},
            "yunxiao_comment_allowed": status in {"pass", "skipped"},
            "yunxiao_transition_allowed": False,
            "note": "行为验收未通过时，只允许继续修改 patch；不允许自动提交、云效交付评论或状态流转。",
        },
    }


def behavior_to_json(behavior: dict) -> str:
    return json.dumps(behavior, ensure_ascii=False, indent=2)


def behavior_to_markdown(behavior: dict) -> str:
    lines = [
        "## v0.10 行为验收矩阵",
        "",
        f"- 版本：{behavior.get('version') or BEHAVIOR_VERSION}",
        f"- 状态：{behavior.get('status') or '-'}",
        f"- 结论：{behavior.get('summary') or '-'}",
        f"- 交互相关：{bool(behavior.get('interaction_related'))}",
        f"- 收费/结算敏感：{bool(behavior.get('settlement_related'))}",
        "",
        "### 必须发生",
        "",
    ]
    append_assertions(lines, ((behavior.get("assertions") or {}).get("must_happen") or []))
    lines.extend(["", "### 禁止发生", ""])
    append_assertions(lines, ((behavior.get("assertions") or {}).get("must_not_happen") or []))
    lines.extend(["", "### 必须保持", ""])
    append_assertions(lines, ((behavior.get("assertions") or {}).get("preserve") or []))

    lines.extend(["", "### 自动检查", "", "| 检查项 | 状态 | 证据 |", "| --- | --- | --- |"])
    for item in behavior.get("checks") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("name") or item.get("id") or "-"),
                    str(item.get("status") or "-"),
                    str(item.get("evidence") or item.get("message") or "-").replace("\n", "<br>"),
                ]
            )
            + " |"
        )
    if not behavior.get("checks"):
        lines.append("| - | skipped | 未识别到交互行为改动 |")

    lines.extend(["", "### 人工/自动验收建议", ""])
    manual = behavior.get("manual_acceptance") or []
    if not manual:
        lines.append("- 未识别到额外行为验收项。")
    for item in manual:
        lines.append(f"- {item}")

    gate = behavior.get("gate") or {}
    lines.extend(
        [
            "",
            "### 放权边界",
            "",
            f"- 是否允许自动提交：{gate.get('auto_commit_allowed')}",
            f"- 是否允许云效交付评论：{gate.get('yunxiao_comment_allowed')}",
            f"- 是否允许云效状态流转：{gate.get('yunxiao_transition_allowed')}",
            f"- 说明：{gate.get('note') or '-'}",
        ]
    )
    return "\n".join(lines)


def parse_unified_diff(diff_text: str) -> dict:
    added: list[str] = []
    removed: list[str] = []
    files: list[str] = []
    for line in (diff_text or "").splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                files.append(parts[3].removeprefix("b/"))
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return {"added": added, "removed": removed, "files": files, "raw": diff_text or ""}


def is_interaction_related(*, context_text: str, diff: dict) -> bool:
    if any(term in context_text for term in INTERACTION_TERMS):
        return True
    changed_code = "\n".join((diff.get("added") or []) + (diff.get("removed") or []))
    return any(term in changed_code for term in SENSITIVE_CODE_TERMS)


def is_settlement_related(*, context_text: str, diff: dict, changed_paths: list[str], interaction_related: bool) -> bool:
    changed_code = "\n".join((diff.get("added") or []) + (diff.get("removed") or []))
    if any("shouFeiJs" in path for path in changed_paths):
        return True
    if any(term in changed_code for term in ["closeSettlementProgress", "failActiveSettlementProgressStep", "diSanFangTf", "menZhenSfJs"]):
        return True
    return interaction_related and any(term in context_text for term in SETTLEMENT_TERMS)


def evaluate_behavior_checks(*, diff: dict, interaction_related: bool, settlement_related: bool) -> list[dict]:
    if not interaction_related:
        return []
    added = diff.get("added") or []
    removed = diff.get("removed") or []
    added_text = "\n".join(added)
    removed_text = "\n".join(removed)
    raw = diff.get("raw") or ""
    checks: list[dict] = []

    if "$alert" in added_text or "$alert" in removed_text:
        checks.append(check_no_empty_alert(added_text=added_text, raw=raw))
        checks.append(check_alert_close_is_local(added_text=added_text, removed_text=removed_text))

    if "catch" in added_text or "catch" in removed_text or "$alert" in added_text:
        checks.append(check_close_cancel_not_business_error(added_text=added_text, raw=raw))

    if settlement_related:
        checks.append(check_no_settlement_generic_prompt_replacement(added_text=added_text, raw=raw))
        checks.append(check_settlement_cleanup_terms(raw=raw))

    return checks


def check_no_empty_alert(*, added_text: str, raw: str) -> dict:
    if re.search(r"\$alert\s*\(\s*(e|err|error)\.message\s*,", added_text):
        return failed_check(
            "BEH-CHECK-NO-EMPTY-ALERT",
            "新增了直接使用异常 message 的提示，可能在 close/cancel 时弹空提示。",
            "禁止 `$alert(e.message, ...)` 这类未判空提示。",
        )
    if "$alert(jieSuanErrorMessage" in added_text and "if (jieSuanErrorMessage)" not in raw:
        return failed_check(
            "BEH-CHECK-NO-EMPTY-ALERT",
            "新增错误提示未受非空消息保护。",
            "错误提示必须先判断存在真实错误文案。",
        )
    return passed_check(
        "BEH-CHECK-NO-EMPTY-ALERT",
        "未发现新增空 message 直接弹窗模式。",
    )


def check_alert_close_is_local(*, added_text: str, removed_text: str) -> dict:
    changed_alert = "$alert" in added_text or "$alert" in removed_text
    if changed_alert and ".catch(" not in added_text:
        return needs_review_check(
            "BEH-CHECK-ALERT-CLOSE-LOCAL",
            "改动了提示弹窗，但没有看到本地处理关闭动作。",
            "Element UI alert/confirm 的关闭动作会 reject，必须证明不会落入外层业务失败 catch。",
        )
    return passed_check(
        "BEH-CHECK-ALERT-CLOSE-LOCAL",
        "提示弹窗关闭动作已在本地处理或未改动提示关闭路径。",
    )


def check_close_cancel_not_business_error(*, added_text: str, raw: str) -> dict:
    if any(term in raw for term in ["'cancel'", '"cancel"', "'close'", '"close"']):
        return passed_check(
            "BEH-CHECK-CLOSE-CANCEL",
            "已识别 close/cancel，不会直接按业务失败文案处理。",
        )
    if "$alert" in added_text or "catch" in added_text:
        return needs_review_check(
            "BEH-CHECK-CLOSE-CANCEL",
            "交互/异常路径发生变化，但未看到 close/cancel 分类。",
            "需要补充方法级测试或人工证明关闭弹窗不会变成业务失败。",
        )
    return passed_check("BEH-CHECK-CLOSE-CANCEL", "未发现需要 close/cancel 分类的新增路径。")


def check_no_settlement_generic_prompt_replacement(*, added_text: str, raw: str) -> dict:
    for fallback in GENERIC_ALERT_FALLBACKS:
        if re.search(r"\$alert\s*\([^)]*" + re.escape(fallback), added_text):
            return failed_check(
                "BEH-CHECK-NO-GENERIC-SETTLEMENT-PROMPT",
                f"新增了可能替换真实原因的兜底提示：{fallback}",
                "收费/结算失败应优先保留原具体原因；没有真实原因时不得新增二次提示。",
            )
    if "jieSuanFlowErrorMessage" in raw:
        return failed_check(
            "BEH-CHECK-NO-GENERIC-SETTLEMENT-PROMPT",
            "检测到流程级兜底文案变量，可能把上游业务提示带入外层 catch 再弹。",
            "应隔离关闭动作副作用，而不是在外层 catch 重弹业务提示。",
        )
    return passed_check(
        "BEH-CHECK-NO-GENERIC-SETTLEMENT-PROMPT",
        "未发现把收费/结算原因替换成新增兜底提示的模式。",
    )


def check_settlement_cleanup_terms(*, raw: str) -> dict:
    # diff 片段不一定包含完整 loading 上下文；这里硬拦进度关闭和 return 收尾，
    # loading 顺序继续放到人工/方法级复测项里确认。
    required_terms = ["closeSettlementProgress", "return"]
    missing = [term for term in required_terms if term not in raw]
    if missing:
        return needs_review_check(
            "BEH-CHECK-SETTLEMENT-CLEANUP",
            "收费/结算交互改动没有同时覆盖关键收尾词：" + ", ".join(missing),
            "需要人工确认未破坏进度条关闭和 return 分支。",
        )
    return passed_check(
        "BEH-CHECK-SETTLEMENT-CLEANUP",
        "diff 中仍包含进度关闭和 return 收尾路径；loading 顺序需结合目标文件上下文或人工场景复测。",
    )


def classify_status(*, checks: list[dict], interaction_related: bool) -> str:
    if not interaction_related:
        return "skipped"
    if any(item.get("status") == "failed" for item in checks):
        return "failed"
    if any(item.get("status") == "needs_review" for item in checks):
        return "needs_review"
    return "pass"


def summarize_status(*, status: str, interaction_related: bool, checks: list[dict]) -> str:
    if status == "skipped":
        return "未识别到提示、关闭、loading、进度或异常路径改动，跳过行为门禁。"
    if status == "pass":
        return "行为门禁通过：未发现空提示、重复提示或关闭动作误入业务失败路径的明显模式。"
    if status == "needs_review":
        names = [str(item.get("name") or item.get("id")) for item in checks if item.get("status") == "needs_review"]
        return "行为门禁需要人工复核：" + "；".join(names)
    names = [str(item.get("name") or item.get("id")) for item in checks if item.get("status") == "failed"]
    return "行为门禁失败：" + "；".join(names)


def build_manual_acceptance(*, interaction_related: bool, settlement_related: bool, title: str) -> list[str]:
    items: list[str] = []
    if interaction_related:
        items.extend(
            [
                "复现原始交互路径，分别点击“确定”和右上角关闭，确认两条路径都符合旧业务流程。",
                "确认关闭提示后不会出现空提示、重复提示或与真实原因不一致的兜底提示。",
                "确认 loading、进度条、弹窗关闭顺序与原流程一致。",
            ]
        )
    if settlement_related:
        items.extend(
            [
                "收费/结算场景必须用真实或现场等价测试数据复测成功、失败、取消、超时路径。",
                "确认自动退费、HIS 结算、医保结算状态不会因 UI 提示关闭动作被误改。",
            ]
        )
    if title:
        items.append(f"按需求/缺陷标题复测：{title}")
    return unique_keep_order(items)


def append_assertions(lines: list[str], items: list[dict]) -> None:
    if not items:
        lines.append("- 无。")
        return
    for item in items:
        lines.append(f"- {item.get('id')}: {item.get('statement')}")
        if item.get("reason"):
            lines.append(f"  - 依据：{item.get('reason')}")


def assertion(item_id: str, statement: str, reason: str) -> dict:
    return {"id": item_id, "statement": statement, "reason": reason}


def passed_check(item_id: str, message: str) -> dict:
    return {"id": item_id, "name": item_id, "status": "pass", "message": message, "evidence": message}


def needs_review_check(item_id: str, message: str, evidence: str) -> dict:
    return {"id": item_id, "name": item_id, "status": "needs_review", "message": message, "evidence": evidence}


def failed_check(item_id: str, message: str, evidence: str) -> dict:
    return {"id": item_id, "name": item_id, "status": "failed", "message": message, "evidence": evidence}


def unique_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def write_behavior_artifacts(output_dir: Path, behavior: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "behavior_acceptance.json").write_text(behavior_to_json(behavior), encoding="utf-8")
    (output_dir / "behavior_acceptance.md").write_text(behavior_to_markdown(behavior), encoding="utf-8")
