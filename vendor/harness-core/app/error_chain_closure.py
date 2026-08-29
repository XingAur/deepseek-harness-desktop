"""Conservative static proof for screenshot-error engineering work.

The gate is intentionally fail-closed.  It does not let a broad business
description (for example “医生申请退费”) stand in for the concrete executable
path that produced an error.  Missing local source proof is a reason to keep
mutation closed, never a reason to invent the missing hop.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.conversation_evidence import conversation_fact_terms
from app.project_context import DEFAULT_EXCLUDE_DIRS, TEXT_EXTENSIONS


ERROR_CHAIN_VERSION = "error-chain-closure.v1"
_HIGH_RISK = ("医保", "结算", "退费", "收费", "预结算", "外部调用")
# “相关截图” only says that an image exists; it does not establish that the
# image is an error screen.  Keep the error-chain gate tied to explicit error
# semantics so API tables and design documents do not enter a UI-only gate.
_ERROR_HINTS = ("报错", "失败", "错误", "提示", "不能", "异常", "无法")
_HIGH_RISK_NEGATION = re.compile(
    r"(?:不涉及|不包含|不影响|无需|不需要|无关(?:于)?)"
    r"[^。；;\n]{0,24}(?:医保|结算|退费|收费|预结算|外部调用)",
)
_CLICK = re.compile(r"@click|onClick|addEventListener\s*\(\s*['\"]click|\.click\s*\(")
_MENU = re.compile(r"菜单|menu|route|router|path\s*[:=]|name\s*[:=]", re.IGNORECASE)
_ENDPOINT = re.compile(r"['\"](?P<endpoint>/?[-A-Za-z0-9_]+(?:/[-A-Za-z0-9_]+)+)['\"]")
_EXTERNAL_CALL = re.compile(
    r"\b(?:yiBao(?:ServiceApi|Api|Client|Gateway|Adapter)|"
    r"yb(?:Service|Api|Client|Gateway|Adapter)|"
    r"medical(?:Service|Api|Client|Gateway|Adapter)|"
    r"医保(?:服务|接口|适配|客户端|网关))"
    r"\s*\.\s*[A-Za-z_]\w*\s*\(",
    re.IGNORECASE,
)
_JAVA_METHOD_CALL = re.compile(r"\b(?:this\s*\.\s*)?(?P<name>[A-Za-z_]\w*)\s*\(")
_THIS_JAVA_METHOD_CALL = re.compile(r"\bthis\s*\.\s*(?P<name>[A-Za-z_]\w*)\s*\(")


def requires_error_chain_closure(*, demand_text: str, conversation_evidence: Mapping[str, Any] | None, requirement_evidence: Mapping[str, Any] | None = None) -> bool:
    # Contract facts in an attachment may legitimately contain words such as
    # “不能同时为空”; they must not classify a document/table as an error
    # screenshot.  Error classification therefore uses the demand and
    # conversation only, plus an explicitly extracted UI error fact below.
    text = demand_text + "\n" + json.dumps(conversation_evidence or {}, ensure_ascii=False)
    # This is a safety gate, not an evidence-presence gate.  If the host did
    # not export the current conversation/screenshot, the closure must still
    # run and fail closed; otherwise a mutation could bypass the very proof it
    # is supposed to require by omitting the evidence package.
    if not _has_high_risk_signal(text):
        return False
    if any(term in text for term in _ERROR_HINTS):
        return True
    visual = requirement_evidence.get("visual_evidence") if isinstance(requirement_evidence, Mapping) else {}
    visual_facts = visual.get("facts") if isinstance(visual, Mapping) else []
    if not isinstance(visual_facts, list):
        visual_facts = []
    return any(
        isinstance(item, Mapping)
        and str(item.get("fact_type") or "ui_trace").strip().lower() == "ui_trace"
        and str(item.get("error_text") or "").strip()
        for item in visual_facts
    )


def _has_high_risk_signal(text: str) -> bool:
    """Treat explicit scope exclusions as exclusions, not as risk signals."""
    without_explicit_exclusions = _HIGH_RISK_NEGATION.sub("", text)
    return any(term in without_explicit_exclusions for term in _HIGH_RISK)


def build_error_chain_closure(
    *,
    demand_text: str,
    conversation_evidence: Mapping[str, Any] | None,
    technical_decision: Mapping[str, Any] | None,
    requirement_evidence: Mapping[str, Any] | None = None,
) -> dict:
    """Prove the six required hops from local, read-only source evidence.

    The scanner is purposefully narrow: it only marks a hop as passed when a
    file/path/endpoint is recorded.  It may report false negatives for a
    dynamic framework path, but it must never report a guessed hop as proved.
    """
    required = requires_error_chain_closure(
        demand_text=demand_text,
        conversation_evidence=conversation_evidence,
        requirement_evidence=requirement_evidence,
    )
    if not required:
        return {"version": ERROR_CHAIN_VERSION, "required": False, "status": "not_required", "can_modify": True, "steps": []}
    evidence = conversation_evidence or {}
    technical = technical_decision or {}
    terms = _select_specific_locator_terms(conversation_fact_terms(evidence))
    facts = evidence.get("confirmed_facts") if isinstance(evidence, Mapping) else []
    screenshot_fact = next(
        (item for item in facts or [] if isinstance(item, Mapping) and item.get("kind") in {"screenshot_observed", "user_correction"}),
        None,
    )
    visual = requirement_evidence.get("visual_evidence") if isinstance(requirement_evidence, Mapping) and isinstance(requirement_evidence.get("visual_evidence"), Mapping) else {}
    visual_fact = next((item for item in visual.get("facts") or [] if isinstance(item, Mapping)), None)
    screenshot_text = _text(screenshot_fact.get("statement")) if screenshot_fact else _text(visual_fact.get("error_text")) if visual_fact else ""
    frontend_files = _frontend_sources(technical, terms)
    menu_files = [item for item in frontend_files if _MENU.search(item["text"])]
    click_files = [item for item in frontend_files if _CLICK.search(item["text"])]
    endpoint_matches = {
        endpoint
        for endpoint in _endpoints(frontend_files)
        if _endpoint_matches_locator(endpoint, terms)
    }
    branches = _branches(technical)
    branch_endpoints = {str(item.get("endpoint") or "") for item in branches}
    matched_branches = [
        item
        for item in branches
        if _normalize_endpoint(str(item.get("endpoint") or "")) in endpoint_matches
        and item.get("controller_verified") is True
    ]
    external_files = _external_sources(technical, matched_branches)
    steps = [
        _step("screenshot_error_text", bool(screenshot_text), "截图错误文本/用户纠正已被结构化记录。", "缺少截图错误文本的结构化观察，不能从图片路径猜测报错含义。", {"fact": screenshot_text}),
        _step("menu", bool(menu_files), "已找到菜单或路由源码锚点。", "未找到与错误链相关的菜单/路由源码锚点。", _file_ref(menu_files)),
        _step("click_event", bool(click_files), "已找到同链路的点击事件源码锚点。", "未找到同链路的点击事件源码锚点。", _file_ref(click_files)),
        _step("frontend_api", bool(endpoint_matches), "已从前端源码提取接口端点。", "未从前端链路源码提取到接口端点。", {"endpoints": sorted(endpoint_matches)}),
        _step("backend_branch", bool(matched_branches), "前端端点已映射到本地 Controller 分支。", "前端端点尚未映射到同一后端 Controller 分支。", {"branches": _compact_branches(matched_branches), "available_endpoints": sorted(branch_endpoints)}),
        _step("external_insurance_call", bool(external_files), "已在已映射后端分支中找到外部医保调用锚点。", "未在已映射后端分支中找到外部医保调用源码锚点。", _file_ref(external_files)),
    ]
    blockers = [str(item["blocker"]) for item in steps if item["status"] != "pass"]
    return {
        "version": ERROR_CHAIN_VERSION,
        "required": True,
        "status": "closed" if not blockers else "blocked_needs_error_chain_closure",
        "can_modify": not blockers,
        "required_code_terms": list(terms),
        "steps": steps,
        "blockers": blockers,
        "next_readonly_action": "按截图报错文本 -> 菜单 -> 点击事件 -> 前端接口 -> 后端分支 -> 外部医保调用逐段补齐本地源码证据。",
    }


def error_chain_closure_to_markdown(closure: Mapping[str, Any]) -> str:
    lines = ["## 截图错误链路闭环门禁", "", f"- 是否要求：{'是' if closure.get('required') else '否'}", f"- 结论：`{closure.get('status') or '-'}`", f"- 是否允许改码：{'是' if closure.get('can_modify') else '否'}", ""]
    for step in closure.get("steps") or []:
        if isinstance(step, Mapping):
            lines.append(f"- {step.get('name')}：`{step.get('status')}`；{step.get('summary')}")
            if step.get("status") != "pass":
                lines.append(f"  - 阻断：{step.get('blocker')}")
    return "\n".join(lines)


def _frontend_sources(technical: Mapping[str, Any], terms: Sequence[str]) -> list[dict[str, str]]:
    if not terms:
        return []
    authoritative_frontends = [
        item
        for item in technical.get("selected_projects") or []
        if isinstance(item, Mapping)
        and item.get("role") == "frontend"
        and item.get("exists") is True
        and item.get("authoritative_code_match") is True
    ]
    projects = authoritative_frontends or [
        item
        for item in technical.get("selected_projects") or []
        if isinstance(item, Mapping)
        and item.get("role") == "frontend"
        and item.get("exists") is True
    ]
    result: list[dict[str, str]] = []
    for project in projects:
        root = Path(str(project.get("path") or ""))
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS or any(part in DEFAULT_EXCLUDE_DIRS for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if any(term in text for term in terms):
                result.append({"path": str(path), "text": text})
    return result[:80]


def _endpoints(files: Sequence[Mapping[str, str]]) -> set[str]:
    return {
        _normalize_endpoint(match.group("endpoint"))
        for item in files
        for match in _ENDPOINT.finditer(item.get("text") or "")
    }


def _normalize_endpoint(value: str) -> str:
    endpoint = str(value or "").strip()
    return endpoint if endpoint.startswith("/") else "/" + endpoint


def _endpoint_matches_locator(endpoint: str, terms: Sequence[str]) -> bool:
    normalized_endpoint = _normalize_endpoint(endpoint).lstrip("/")
    endpoint_method = normalized_endpoint.rsplit("/", 1)[-1]
    for raw_term in terms:
        term = str(raw_term or "").strip().lstrip("/")
        if not term:
            continue
        if "/" in term and normalized_endpoint == term:
            return True
        if "/" not in term and endpoint_method == term:
            return True
    return False


def _select_specific_locator_terms(terms: Sequence[str]) -> tuple[str, ...]:
    values = tuple(dict.fromkeys(str(term).strip() for term in terms if str(term).strip()))
    return tuple(
        term
        for term in values
        if not any(
            other != term
            and "/" not in term
            and "/" not in other
            and len(other) > len(term)
            and other.startswith(term)
            for other in values
        )
    )


def _branches(technical: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    provenance = technical.get("field_provenance") if isinstance(technical.get("field_provenance"), Mapping) else {}
    graph = provenance.get("service_graph") if isinstance(provenance.get("service_graph"), Mapping) else {}
    return [item for item in graph.get("branches") or [] if isinstance(item, Mapping)]


def _external_sources(technical: Mapping[str, Any], branches: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    selected = {str(item.get("name") or ""): Path(str(item.get("path") or "")) for item in technical.get("selected_projects") or [] if isinstance(item, Mapping)}
    result: list[dict[str, str]] = []
    for branch in branches:
        target = selected.get(str(branch.get("target_project") or ""))
        target_path = str(branch.get("target_path") or "").split(":", 1)[-1]
        if not target or not target.is_dir() or not target_path:
            continue
        candidate = target / target_path
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        operation = str(branch.get("endpoint") or "").rsplit("/", 1)[-1]
        controller_evidence = _linked_external_evidence(
            source_path=candidate,
            source_text=text,
            operation=operation,
            project_root=target,
        )
        result.extend(controller_evidence)
    return result


def _linked_external_evidence(
    *,
    source_path: Path,
    source_text: str,
    operation: str,
    project_root: Path,
) -> list[dict[str, Any]]:
    """Find an external call from the exact endpoint method, not its file.

    A Controller often delegates to a Service implementation, and that
    implementation may delegate once more to a private helper.  Follow only
    those bounded, same-project method calls.  Generic words such as
    ``医保服务`` in an import, comment, or unrelated method are not evidence.
    """
    if not operation:
        return []
    method = _extract_java_method(source_text, operation)
    if method is None:
        return []
    method_text, method_line = method
    direct = _external_call_matches(method_text)
    if direct:
        return [_evidence_ref(source_path, method_line, operation, method_text, direct)]

    service_sources = _load_service_impl_sources(project_root)
    result: list[dict[str, str]] = []
    for service_path, service_text in service_sources:
        service_method = _extract_java_method(service_text, operation)
        if service_method is None:
            continue
        result.extend(
            _follow_java_helper_calls(
                source_path=service_path,
                source_text=service_text,
                method_name=operation,
                method=service_method,
            )
        )
        if result:
            break
    return result


def _follow_java_helper_calls(
    *,
    source_path: Path,
    source_text: str,
    method_name: str,
    method: tuple[str, int],
    visited: set[str] | None = None,
) -> list[dict[str, str]]:
    visited = set(visited or ())
    if method_name in visited:
        return []
    visited.add(method_name)
    method_text, method_line = method
    direct = _external_call_matches(method_text)
    if direct:
        return [_evidence_ref(source_path, method_line, method_name, method_text, direct)]

    helper_names = []
    for match in _THIS_JAVA_METHOD_CALL.finditer(method_text):
        name = match.group("name")
        if name != method_name and name not in helper_names:
            helper_names.append(name)
    for match in _JAVA_METHOD_CALL.finditer(method_text):
        name = match.group("name")
        if name == method_name or name in helper_names:
            continue
        helper_names.append(name)
    for helper_name in helper_names[:96]:
        helper = _extract_java_method(source_text, helper_name)
        if helper is None:
            continue
        evidence = _follow_java_helper_calls(
            source_path=source_path,
            source_text=source_text,
            method_name=helper_name,
            method=helper,
            visited=visited,
        )
        if evidence:
            return evidence
    return []


def _load_service_impl_sources(project_root: Path, max_files: int = 1200) -> list[tuple[Path, str]]:
    if not project_root.is_dir():
        return []
    result: list[tuple[Path, str]] = []
    scanned = 0
    for path in project_root.rglob("*"):
        if (
            not path.is_file()
            or path.suffix.lower() != ".java"
            or "ServiceImpl" not in path.name
            or any(part in DEFAULT_EXCLUDE_DIRS for part in path.parts)
        ):
            continue
        scanned += 1
        if scanned > max_files:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        result.append((path, text))
    return result


def _extract_java_method(text: str, method_name: str) -> tuple[str, int] | None:
    if not text or not method_name:
        return None
    pattern = re.compile(rf"\b{re.escape(method_name)}\s*\([^;{{}}]*\)\s*\{{")
    for match in pattern.finditer(text):
        prefix = text[max(0, match.start() - 300) : match.start()]
        if not re.search(r"\b(?:public|private|protected|static|default|final)\b", prefix):
            continue
        opening = text.find("{", match.start(), match.end())
        closing = _matching_brace_end(text, opening)
        if opening < 0 or closing < 0:
            continue
        line = text.count("\n", 0, match.start()) + 1
        return text[match.start() : closing + 1], line
    return None


def _matching_brace_end(text: str, opening_index: int) -> int:
    if opening_index < 0:
        return -1
    depth = 0
    for index in range(opening_index, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _external_call_matches(method_text: str) -> list[str]:
    return [match.group(0).strip() for match in _EXTERNAL_CALL.finditer(method_text)]


def _evidence_ref(
    path: Path,
    line: int,
    method_name: str,
    method_text: str,
    matches: Sequence[str],
) -> dict[str, Any]:
    return {
        "path": f"{path}:{line}",
        "method": method_name,
        "external_calls": list(matches),
        "text": f"精确方法源码（第 {line} 行起）：\n{method_text}\n外部调用：{'; '.join(matches)}",
    }


def _step(name: str, passed: bool, summary: str, blocker: str, refs: Mapping[str, Any]) -> dict:
    return {"name": name, "status": "pass" if passed else "blocked", "summary": summary if passed else "源码/对话证据不足，改码门禁保持关闭。", "blocker": "" if passed else blocker, "evidence": dict(refs)}


def _file_ref(files: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    refs = {"paths": [str(item.get("path") or "") for item in files[:8]]}
    details = [
        {
            "method": str(item.get("method") or ""),
            "external_calls": list(item.get("external_calls") or []),
        }
        for item in files[:8]
        if item.get("method") or item.get("external_calls")
    ]
    if details:
        refs["details"] = details
    return refs


def _compact_branches(branches: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{key: item.get(key) for key in ("endpoint", "source_path", "target_project", "target_path")} for item in branches[:8]]


def _text(value: object) -> str:
    return str(value or "").strip()
