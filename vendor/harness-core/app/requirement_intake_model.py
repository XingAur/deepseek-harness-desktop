"""Draft intake analysis documents with the desktop-selected model.

The intake phase archives read-only source evidence first (see
``requirement_archive.prepare_yunxiao_harness_package``).  When the desktop
task carries a selected model, this module asks the host-provided model to
draft the requirement-side analysis documents from that evidence.

Drafts are written with an explicit ``model_generated`` marker: they are
working documents for the user, never confirmed business facts.  Project-,
engineering- and execution-side documents stay pending because they need the
target project and governed run.  Any internal failure (missing executor,
timeout, unparsable output) keeps the previous pending placeholder and records
a bounded reason instead of fabricating content or asking the user to fix
Harness internals.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from app.agent_backend_protocol import AgentBackendRequest, AgentBackendResult
from app.agent_backend import AgentBackendRole
from app.requirement_package import rebuild_requirement_package_manifest
from app.sensitive_text import contains_sensitive_text, redact_sensitive_text


INTAKE_DOCUMENT_SCHEMA = "harness-intake-documents.v1"
INTAKE_REPORT_SCHEMA = "harness-intake-generation-report.v1"
_MAX_EVIDENCE_BYTES = 16_000
#: 每篇文档的上限以“字符”计。协议侧对整段结果 JSON 有约 64KB UTF-8 的
#: 敏感扫描上限，8 篇中文文档合计必须留出安全余量（中文约 3 字节/字符）。
_MAX_DOCUMENT_CHARS = 2_000
_MAX_OPEN_QUESTIONS = 5
_MAX_QUESTION_CHARS = 300
_MIN_DOCUMENT_CHARS = 200
_REQUEST_TIMEOUT_SECONDS = 600

#: Requirement-side documents the model may draft at intake time.  Each entry
#: is the instruction handed to the model for that document.  Project and
#: engineering documents are intentionally absent: they need the selected
#: target project and a governed run, not requirement text alone.
INTAKE_DOCUMENTS: dict[str, str] = {
    "requirement_understanding.md": "需求理解：业务背景、要解决的问题、涉及角色、范围与边界",
    "goals_and_wishes.md": "目标与愿望：业务方和使用者期望达成的目标、愿望与成功标准",
    "scenarios.md": "使用场景：主要用户角色与端到端使用场景（含前置条件与主要流程）",
    "functional_requirements.md": "功能需求：逐条列出功能点，每条有编号、描述和优先级",
    "acceptance_criteria.md": "验收标准：可验证的验收条目，与功能需求对应",
    "constraints_and_non_goals.md": "约束与非目标：明确不做什么、技术/业务约束、依赖前提",
    "requirement_plan.md": "需求规划：需求拆解、依赖关系与建议优先级",
    "prd.md": "PRD：完整产品需求文档（背景、目标、场景、功能清单、验收标准、边界）",
}


def draft_intake_analysis_documents(
    *,
    package_dir: str | Path,
    ticket_dir: str | Path,
    ticket_id: str,
    host_execute: Callable[[AgentBackendRequest], AgentBackendResult],
    selected_model_id: str | None = None,
) -> dict[str, object]:
    """Draft the intake analysis documents through the host model executor.

    Returns a bounded summary.  This function never raises: every failure is
    reported as ``status`` with ``error_code`` so the caller can keep the
    pending placeholders and surface a recoverable reason.
    """

    package = Path(package_dir).expanduser().resolve()
    ticket = Path(ticket_dir).expanduser().resolve()
    report: dict[str, object] = {
        "schema": INTAKE_REPORT_SCHEMA,
        "ticket_id": ticket_id,
        "status": "failed",
        "error_code": "",
        "selected_model_id": selected_model_id or "",
        "generated": [],
        "skipped": [],
        "open_questions": [],
    }
    try:
        evidence = _bounded_evidence(package)
        if evidence == "":
            report["status"] = "skipped_no_evidence"
            report["error_code"] = "intake_evidence_unavailable"
            return _finalize(package=package, ticket=ticket, report=report)
        request = _build_request(
            package=package,
            evidence=evidence,
        )
        result = host_execute(request)
        if result.error_code:
            report["error_code"] = result.error_code
            return _finalize(package=package, ticket=ticket, report=report)
        documents, open_questions = _parse_model_output(result.final_response)
        if not documents:
            report["error_code"] = "intake_model_output_invalid"
            return _finalize(package=package, ticket=ticket, report=report)
        generated: list[str] = []
        skipped: list[str] = []
        for filename in INTAKE_DOCUMENTS:
            content = documents.get(filename, "")
            if _valid_document(content):
                _write_generated_document(
                    package / "analysis" / filename,
                    filename=filename,
                    content=content,
                    selected_model_id=selected_model_id,
                )
                generated.append(filename)
            else:
                skipped.append(filename)
        report["generated"] = generated
        report["skipped"] = skipped
        report["open_questions"] = open_questions
        report["status"] = "generated" if generated else "failed"
        if not generated:
            report["error_code"] = "intake_model_output_invalid"
        return _finalize(package=package, ticket=ticket, report=report)
    except Exception:
        report["status"] = "failed"
        report["error_code"] = "intake_generation_failed"
        return _finalize(package=package, ticket=ticket, report=report)


def _bounded_evidence(package: Path) -> str:
    """Return the bounded, redacted requirement evidence for the prompt.

    Archived comments often contain phone numbers or credential-shaped text;
    the agent request protocol rejects such prompts outright, so the evidence
    is redacted (never dropped) before it reaches the model.
    """

    requirement = package / "source" / "requirement.md"
    if not requirement.is_file():
        return ""
    raw = requirement.read_text(encoding="utf-8", errors="ignore")
    encoded = raw.encode("utf-8")[:_MAX_EVIDENCE_BYTES]
    text = encoded.decode("utf-8", errors="ignore")
    redacted = redact_sensitive_text(text)
    if contains_sensitive_text(redacted):
        return ""
    return redacted.strip()


def _build_request(*, package: Path, evidence: str) -> AgentBackendRequest:
    document_lines = "\n".join(
        f"- `{filename}`：{instruction}" for filename, instruction in INTAKE_DOCUMENTS.items()
    )
    prompt = "\n".join(
        [
            "你是 Harness 的需求分析模型。只依据下方归档的云效需求证据起草分析文档，不编造证据中没有的业务事实。",
            "项目尚未选定，不要对项目结构、代码、表结构做任何假设；涉及项目的内容写明需要项目证据后补齐。",
            "",
            "需要起草的文档：",
            document_lines,
            "",
            "输出要求：只输出一个 JSON 对象，不要输出其他文字。结构为：",
            '{"documents": {"<文件名>": "<Markdown 正文>"}, "open_questions": ["<问题>"]}',
            "documents 的键只使用上面列出的文件名；每篇文档正文不超过 2000 字，聚焦归档证据中的事实。",
            "open_questions 只在证据存在真实业务歧义时给出（最多 5 条），没有歧义输出空数组。",
            "",
            "归档证据如下：",
            evidence,
        ]
    )
    return AgentBackendRequest(
        role=AgentBackendRole.WORKER,
        worktree_path=package,
        prompt=prompt,
        timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
        output_contract={"name": "harness_intake_documents", "schema_version": INTAKE_DOCUMENT_SCHEMA},
        capabilities=(),
    )


def _parse_model_output(
    final_response: dict[str, object] | None,
) -> tuple[dict[str, str], list[str]]:
    if not isinstance(final_response, dict):
        return {}, []
    text = final_response.get("text")
    if not isinstance(text, str) or not text.strip():
        return {}, []
    payload = _extract_json_object(text)
    if not isinstance(payload, dict):
        return {}, []
    documents: dict[str, str] = {}
    raw_documents = payload.get("documents")
    if isinstance(raw_documents, dict):
        for name, value in raw_documents.items():
            if name in INTAKE_DOCUMENTS and isinstance(value, str):
                documents[name] = value
    questions: list[str] = []
    raw_questions = payload.get("open_questions")
    if isinstance(raw_questions, list):
        for item in raw_questions:
            if isinstance(item, str):
                question = item.strip()[:_MAX_QUESTION_CHARS]
                if question and not contains_sensitive_text(question):
                    questions.append(question)
            if len(questions) >= _MAX_OPEN_QUESTIONS:
                break
    return documents, questions


def _extract_json_object(text: str) -> object | None:
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", candidate, re.DOTALL)
    if fence is not None:
        candidate = fence.group(1)
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(candidate[start : end + 1])
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return None


def _valid_document(content: str) -> bool:
    """Accept any substantial draft; over-length content is capped on write."""

    return len(content.strip()) >= _MIN_DOCUMENT_CHARS and "\x00" not in content


def _write_generated_document(
    path: Path,
    *,
    filename: str,
    content: str,
    selected_model_id: str | None,
) -> None:
    title = filename.rsplit(".", 1)[0].replace("_", " ").title()
    header = [
        f"# {title}",
        "",
        "- 状态：model_generated",
        "- 生成方式：当前任务选择的模型基于归档证据起草，未经人工确认，不代表已确认的需求事实",
        f"- 起草模型：{selected_model_id or 'host-selected'}",
        "- 证据来源：source/ 云效只读归档",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    body = content.strip()[:_MAX_DOCUMENT_CHARS]
    path.write_text("\n".join(header) + body + "\n", encoding="utf-8")


def _finalize(*, package: Path, ticket: Path, report: dict[str, object]) -> dict[str, object]:
    bounded = {
        "schema": report["schema"],
        "ticket_id": report["ticket_id"],
        "status": report["status"],
        "error_code": report["error_code"],
        "selected_model_id": report["selected_model_id"],
        "generated": list(report["generated"])[: len(INTAKE_DOCUMENTS)],
        "skipped": list(report["skipped"])[: len(INTAKE_DOCUMENTS)],
        "open_questions": list(report["open_questions"])[:_MAX_OPEN_QUESTIONS],
    }
    report_path = package / "analysis" / "intake_generation_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(bounded, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = rebuild_requirement_package_manifest(
        package_dir=package,
        ticket_dir=ticket,
        run_id=0,
    )
    generated_count = len(bounded["generated"])
    return {
        "status": bounded["status"],
        "error_code": bounded["error_code"],
        "generated_count": generated_count,
        "skipped_count": len(bounded["skipped"]),
        "open_questions": list(bounded["open_questions"]),
        "package_status": manifest["status"],
        "pending_count": manifest["pending_count"],
        "model_generated_count": manifest["model_generated_count"],
        "report_path": str(report_path),
    }
