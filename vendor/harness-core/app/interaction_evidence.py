from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.behavior_acceptance import build_behavior_acceptance, parse_unified_diff


INTERACTION_EVIDENCE_VERSION = "0.10.2"


def build_interaction_evidence_package(
    *,
    title: str,
    demand_text: str = "",
    diff_text: str = "",
    changed_paths: list[str] | None = None,
    behavior_acceptance: dict | None = None,
    method_evidence: dict | None = None,
    ui_evidence_paths: list[str] | None = None,
) -> dict:
    changed_paths = changed_paths or []
    behavior_acceptance = behavior_acceptance or build_behavior_acceptance(
        title=title,
        demand_text=demand_text,
        diff_text=diff_text,
        changed_paths=changed_paths,
    )
    interaction_related = bool(behavior_acceptance.get("interaction_related"))
    settlement_related = bool(behavior_acceptance.get("settlement_related"))
    behavior_test_plan = build_behavior_test_plan(
        title=title,
        demand_text=demand_text,
        diff_text=diff_text,
        changed_paths=changed_paths,
        interaction_related=interaction_related,
        settlement_related=settlement_related,
    )
    method_regression_result = build_method_regression_result(
        plan=behavior_test_plan,
        method_evidence=method_evidence or {},
        interaction_related=interaction_related,
    )
    ui_evidence_manifest = build_ui_evidence_manifest(ui_evidence_paths or [])
    status = classify_interaction_status(
        behavior_acceptance=behavior_acceptance,
        method_regression_result=method_regression_result,
        interaction_related=interaction_related,
    )
    gate = build_interaction_gate(
        status=status,
        interaction_related=interaction_related,
        ui_evidence_manifest=ui_evidence_manifest,
    )
    return {
        "version": INTERACTION_EVIDENCE_VERSION,
        "status": status,
        "summary": summarize_interaction_status(
            status=status,
            interaction_related=interaction_related,
            method_regression_result=method_regression_result,
            ui_evidence_manifest=ui_evidence_manifest,
        ),
        "interaction_related": interaction_related,
        "settlement_related": settlement_related,
        "changed_paths": changed_paths,
        "behavior_acceptance_status": behavior_acceptance.get("status") or "skipped",
        "behavior_test_plan": behavior_test_plan,
        "method_regression_result": method_regression_result,
        "ui_evidence_manifest": ui_evidence_manifest,
        "gate": gate,
    }


def build_behavior_test_plan(
    *,
    title: str,
    demand_text: str,
    diff_text: str,
    changed_paths: list[str],
    interaction_related: bool,
    settlement_related: bool,
) -> dict:
    diff = parse_unified_diff(diff_text)
    cases: list[dict[str, Any]] = []
    if interaction_related:
        cases.extend(
            [
                method_case(
                    "METHOD-ALERT-RESOLVE",
                    "提示弹窗点击确定后继续执行原业务收尾",
                    "模拟 Element UI alert/confirm resolve，确认原成功/失败收尾流程不被跳过。",
                    required=True,
                ),
                method_case(
                    "METHOD-ALERT-CLOSE",
                    "提示弹窗右上角关闭或取消后不进入外层业务失败 catch",
                    "模拟 close/cancel reject，确认不会出现空提示、重复提示或泛化失败文案。",
                    required=True,
                ),
                method_case(
                    "METHOD-NO-REPEATED-ALERT",
                    "异常路径只保留真实提示原因",
                    "模拟接口失败、超时或本地 reject，确认不会把真实原因替换成新增兜底文案。",
                    required=True,
                ),
            ]
        )
    if settlement_related:
        cases.extend(
            [
                method_case(
                    "METHOD-SETTLEMENT-CLEANUP",
                    "收费/结算/医保/退费路径保留进度关闭和 return 收尾",
                    "模拟成功、失败、取消、超时路径，确认 closeSettlementProgress/loading/return 顺序不变。",
                    required=True,
                ),
                method_case(
                    "METHOD-SETTLEMENT-STATE-PRESERVED",
                    "收费状态边界不被 UI 关闭动作改变",
                    "确认 UI 提示关闭不会新增结算、退费、医保状态副作用。",
                    required=False,
                ),
            ]
        )
    return {
        "version": INTERACTION_EVIDENCE_VERSION,
        "title": title,
        "demand_text": demand_text,
        "status": "required" if cases else "skipped",
        "changed_paths": changed_paths,
        "diff_files": diff.get("files") or [],
        "cases": cases,
        "note": "方法级测试应优先覆盖 alert/confirm 的 resolve、close、cancel、接口失败和结算收尾路径。",
    }


def build_method_regression_result(*, plan: dict, method_evidence: dict, interaction_related: bool) -> dict:
    required_cases = [case for case in plan.get("cases") or [] if case.get("required")]
    if not interaction_related:
        return {
            "version": INTERACTION_EVIDENCE_VERSION,
            "status": "skipped",
            "passed": [],
            "failed": [],
            "missing": [],
            "summary": "未识别到交互敏感改动，跳过方法级交互测试。",
        }
    evidence_by_id = normalize_method_evidence(method_evidence)
    passed: list[dict] = []
    failed: list[dict] = []
    missing: list[dict] = []
    for case in required_cases:
        case_id = str(case.get("id") or "")
        evidence = evidence_by_id.get(case_id)
        if not evidence:
            missing.append({"id": case_id, "name": case.get("name"), "reason": "未提供方法级执行结果。"})
            continue
        status = str(evidence.get("status") or "").lower()
        item = {
            "id": case_id,
            "name": case.get("name"),
            "status": status,
            "evidence": evidence.get("evidence") or evidence.get("message") or "-",
        }
        if status == "pass":
            passed.append(item)
        else:
            failed.append(item)
    if failed:
        status = "failed"
        summary = "方法级交互测试失败：" + "；".join(str(item.get("id")) for item in failed)
    elif missing:
        status = "needs_evidence"
        summary = "缺少方法级交互测试结果：" + "；".join(str(item.get("id")) for item in missing)
    else:
        status = "pass"
        summary = "方法级交互测试通过，已覆盖必需交互路径。"
    return {
        "version": INTERACTION_EVIDENCE_VERSION,
        "status": status,
        "passed": passed,
        "failed": failed,
        "missing": missing,
        "summary": summary,
    }


def build_ui_evidence_manifest(paths: list[str]) -> dict:
    items = [build_ui_evidence_item(path) for path in paths]
    existing = [item for item in items if item.get("exists")]
    missing = [item for item in items if not item.get("exists")]
    if existing and missing:
        status = "partial"
    elif existing:
        status = "present"
    elif items:
        status = "missing"
    else:
        status = "missing"
    return {
        "version": INTERACTION_EVIDENCE_VERSION,
        "status": status,
        "items": items,
        "summary": summarize_ui_manifest(status=status, existing_count=len(existing), missing_count=len(missing)),
    }


def interaction_evidence_to_json(package: dict) -> str:
    return json.dumps(package, ensure_ascii=False, indent=2)


def interaction_evidence_to_markdown(package: dict) -> str:
    lines = [
        "## v0.10.2 方法级交互测试与 UI 证据",
        "",
        f"- 状态：{package.get('status') or '-'}",
        f"- 结论：{package.get('summary') or '-'}",
        f"- 交互相关：{bool(package.get('interaction_related'))}",
        f"- 收费/结算敏感：{bool(package.get('settlement_related'))}",
        f"- 行为门禁状态：{package.get('behavior_acceptance_status') or '-'}",
        "",
        "### 方法级测试计划",
        "",
        "| 用例 | 必需 | 目标 | 依据 |",
        "| --- | --- | --- | --- |",
    ]
    for case in (package.get("behavior_test_plan") or {}).get("cases") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(case.get("id") or "-"),
                    str(bool(case.get("required"))),
                    str(case.get("name") or "-"),
                    str(case.get("reason") or "-"),
                ]
            )
            + " |"
        )
    if not ((package.get("behavior_test_plan") or {}).get("cases") or []):
        lines.append("| - | False | 未识别到交互敏感改动 | - |")
    method_result = package.get("method_regression_result") or {}
    lines.extend(
        [
            "",
            "### 方法级执行结果",
            "",
            f"- 状态：{method_result.get('status') or '-'}",
            f"- 结论：{method_result.get('summary') or '-'}",
            "",
        ]
    )
    for label, key in [("通过", "passed"), ("失败", "failed"), ("缺失", "missing")]:
        items = method_result.get(key) or []
        lines.append(f"- {label}：{len(items)}")
        for item in items:
            lines.append(f"  - {item.get('id')}: {item.get('evidence') or item.get('reason') or '-'}")
    manifest = package.get("ui_evidence_manifest") or {}
    lines.extend(
        [
            "",
            "### UI 证据清单",
            "",
            f"- 状态：{manifest.get('status') or '-'}",
            f"- 结论：{manifest.get('summary') or '-'}",
            "",
            "| 文件 | 存在 | 大小 | SHA256 |",
            "| --- | --- | --- | --- |",
        ]
    )
    for item in manifest.get("items") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("name") or "-"),
                    str(bool(item.get("exists"))),
                    str(item.get("size") if item.get("size") is not None else "-"),
                    str(item.get("sha256") or "-"),
                ]
            )
            + " |"
        )
    if not (manifest.get("items") or []):
        lines.append("| - | False | - | - |")
    gate = package.get("gate") or {}
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


def behavior_test_plan_to_markdown(plan: dict) -> str:
    lines = [
        "## behavior_test_plan",
        "",
        f"- 版本：{plan.get('version') or INTERACTION_EVIDENCE_VERSION}",
        f"- 状态：{plan.get('status') or '-'}",
        f"- 标题：{plan.get('title') or '-'}",
        "",
        "| 用例 | 必需 | 目标 | 依据 |",
        "| --- | --- | --- | --- |",
    ]
    for case in plan.get("cases") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(case.get("id") or "-"),
                    str(bool(case.get("required"))),
                    str(case.get("name") or "-"),
                    str(case.get("reason") or "-"),
                ]
            )
            + " |"
        )
    if not (plan.get("cases") or []):
        lines.append("| - | False | 未识别到交互敏感改动 | - |")
    return "\n".join(lines)


def method_regression_result_to_markdown(result: dict) -> str:
    lines = [
        "## method_regression_result",
        "",
        f"- 版本：{result.get('version') or INTERACTION_EVIDENCE_VERSION}",
        f"- 状态：{result.get('status') or '-'}",
        f"- 结论：{result.get('summary') or '-'}",
    ]
    for label, key in [("通过", "passed"), ("失败", "failed"), ("缺失", "missing")]:
        lines.extend(["", f"### {label}", ""])
        items = result.get(key) or []
        if not items:
            lines.append("- 无。")
        for item in items:
            lines.append(f"- {item.get('id')}: {item.get('evidence') or item.get('reason') or '-'}")
    return "\n".join(lines)


def ui_evidence_manifest_to_markdown(manifest: dict) -> str:
    lines = [
        "## ui_evidence_manifest",
        "",
        f"- 版本：{manifest.get('version') or INTERACTION_EVIDENCE_VERSION}",
        f"- 状态：{manifest.get('status') or '-'}",
        f"- 结论：{manifest.get('summary') or '-'}",
        "",
        "| 文件 | 存在 | 大小 | SHA256 | 路径 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in manifest.get("items") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("name") or "-"),
                    str(bool(item.get("exists"))),
                    str(item.get("size") if item.get("size") is not None else "-"),
                    str(item.get("sha256") or "-"),
                    str(item.get("path") or "-"),
                ]
            )
            + " |"
        )
    if not (manifest.get("items") or []):
        lines.append("| - | False | - | - | - |")
    return "\n".join(lines)


def playwright_screenshot_index_to_markdown(manifest: dict) -> str:
    lines = ["## playwright_screenshot_index", ""]
    items = manifest.get("items") or []
    if not items:
        lines.append("- 未提供截图、视频或 GIF 证据。")
        return "\n".join(lines)
    for item in items:
        status = "present" if item.get("exists") else "missing"
        lines.append(f"- {status} `{item.get('name') or '-'}` size={item.get('size') or '-'} sha256={item.get('sha256') or '-'}")
    return "\n".join(lines)


def method_case(item_id: str, name: str, reason: str, *, required: bool) -> dict:
    return {"id": item_id, "name": name, "reason": reason, "required": required}


def normalize_method_evidence(method_evidence: dict) -> dict[str, dict]:
    cases = method_evidence.get("cases") if isinstance(method_evidence, dict) else []
    if isinstance(cases, dict):
        return {str(key): dict(value or {}) for key, value in cases.items()}
    result: dict[str, dict] = {}
    for item in cases or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        if item_id:
            result[item_id] = item
    return result


def build_ui_evidence_item(path_text: str) -> dict:
    path = Path(path_text).expanduser().resolve()
    item = {
        "path": str(path),
        "name": path.name,
        "exists": path.is_file(),
        "size": None,
        "sha256": "",
        "type": infer_media_type(path),
    }
    if not path.is_file():
        return item
    content = path.read_bytes()
    item["size"] = len(content)
    item["sha256"] = hashlib.sha256(content).hexdigest()
    return item


def infer_media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return "screenshot"
    if suffix in {".mp4", ".mov", ".webm"}:
        return "video"
    if suffix == ".gif":
        return "gif"
    return "artifact"


def classify_interaction_status(*, behavior_acceptance: dict, method_regression_result: dict, interaction_related: bool) -> str:
    behavior_status = behavior_acceptance.get("status") or "skipped"
    if behavior_status == "failed":
        return "failed"
    if not interaction_related:
        return "skipped"
    method_status = method_regression_result.get("status")
    if method_status == "failed":
        return "failed"
    if method_status == "pass":
        return "pass"
    return "needs_evidence"


def build_interaction_gate(*, status: str, interaction_related: bool, ui_evidence_manifest: dict) -> dict:
    auto_commit_allowed = status in {"pass", "skipped"}
    has_ui_evidence = ui_evidence_manifest.get("status") in {"present", "partial"}
    return {
        "auto_commit_allowed": auto_commit_allowed,
        "yunxiao_comment_allowed": (not interaction_related and auto_commit_allowed) or (status == "pass" and has_ui_evidence),
        "yunxiao_transition_allowed": False,
        "note": (
            "交互敏感改动必须有方法级测试通过后才允许进入提交准备；"
            "有 UI 截图/视频/GIF 或等价证据后才允许云效交付评论；云效状态流转仍冻结。"
        ),
    }


def summarize_interaction_status(
    *,
    status: str,
    interaction_related: bool,
    method_regression_result: dict,
    ui_evidence_manifest: dict,
) -> str:
    if status == "skipped":
        return "未识别到交互敏感改动，跳过 v0.10.2 方法级交互证据门禁。"
    if status == "pass":
        if ui_evidence_manifest.get("status") in {"present", "partial"}:
            return "方法级交互测试通过，且已记录 UI 证据，可进入提交准备和云效交付评论准备。"
        return "方法级交互测试通过，但缺少 UI 证据；可进入提交准备，云效交付评论仍需截图/视频/GIF 或人工验收记录。"
    if status == "failed":
        return method_regression_result.get("summary") or "方法级交互证据门禁失败。"
    return method_regression_result.get("summary") or "缺少方法级交互测试证据。"


def summarize_ui_manifest(*, status: str, existing_count: int, missing_count: int) -> str:
    if status == "present":
        return f"已记录 {existing_count} 个 UI 证据文件。"
    if status == "partial":
        return f"已记录 {existing_count} 个 UI 证据文件，另有 {missing_count} 个路径不存在。"
    if missing_count:
        return f"提供了 {missing_count} 个 UI 证据路径，但文件不存在。"
    return "未提供截图、视频、GIF 或人工 UI 证据文件。"
