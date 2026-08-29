"""Complete the task understanding gate with the desktop-selected model.

The intake phase drafts requirement-side documents, but governed execution
requires ``analysis/requirement_understanding.json`` with all nine checks
passing plus a loadable ``engineering/task_contract.json``.  This module runs
at execution time, when the target project is finally known: it gathers
bounded, read-only evidence from the archived requirement and the actual
worktree, asks the selected model to produce the structured understanding,
and then applies a deterministic validation floor.

Nothing the model says becomes a pass on its own:

- the worktree must be a plain git repository root;
- every ``allowed_path`` must exist inside the worktree;
- verification tests are rewritten to the Core interpreter's unittest form;
- any failed check, missing evidence or genuine business question keeps the
  task blocked with concrete blockers instead of fabricating readiness.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from app.agent_backend import AgentBackendRole
from app.agent_backend_protocol import AgentBackendRequest, AgentBackendResult
from app.requirement_package import rebuild_requirement_package_manifest
from app.sensitive_text import redact_sensitive_text


UNDERSTANDING_SCHEMA = "requirement-understanding.v1"
COMPLETION_CONTRACT_SCHEMA = "harness-understanding-completion.v1"
TASK_CONTRACT_SCHEMA = "his-local-agent-task.v1"

_CHECK_NAMES = (
    "business_background",
    "usage_scenario",
    "target_and_boundary",
    "project_selection",
    "entry_and_call_chain",
    "conversation_alignment",
    "error_chain_closure",
    "change_and_impact_scope",
    "verification_baseline",
)
_CHECK_LABELS = {
    "business_background": "业务背景",
    "usage_scenario": "使用场景",
    "target_and_boundary": "目标与范围边界",
    "project_selection": "目标项目",
    "entry_and_call_chain": "项目入口与调用链",
    "conversation_alignment": "对话确认链路核验",
    "error_chain_closure": "截图错误链路闭环",
    "change_and_impact_scope": "改动与影响范围",
    "verification_baseline": "验证基线",
}

_MAX_EVIDENCE_CHARS = 12_000
_MAX_PROJECT_CHARS = 8_000
_MAX_REQUEST_SECONDS = 900
_MAX_ALLOWED_PATHS = 64
_MAX_VERIFICATION_TESTS = 16
_TEST_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
_TASK_KEY = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}", re.IGNORECASE)


def complete_task_understanding(
    *,
    package_dir: str | Path,
    worktree_root: str | Path,
    authorization_id: str,
    host_execute: Callable[[AgentBackendRequest], AgentBackendResult],
    selected_model_id: str | None = None,
) -> dict[str, object]:
    """Produce the gated understanding artifacts or an honest blocker list."""

    package = Path(package_dir).expanduser().resolve()
    worktree = Path(worktree_root).expanduser().resolve()
    blockers: list[str] = []
    project_facts = gather_project_facts(worktree)
    blockers.extend(project_facts["blockers"])

    result: dict[str, object]
    model_output: Mapping[str, object] | None = None
    try:
        model_output = _request_model_understanding(
            package=package,
            worktree=worktree,
            project_facts=project_facts,
            host_execute=host_execute,
        )
    except _CompletionBlocked as error:
        blockers.append(str(error))
        model_output = None
    except Exception:
        blockers.append("intake_model_output_invalid:模型未返回可用的结构化理解结果")
        model_output = None

    checks: list[dict[str, object]] = []
    if model_output is not None:
        checks, model_blockers, contract_parts = _validate_model_output(model_output, worktree, project_facts)
        blockers.extend(model_blockers)
    else:
        contract_parts = None

    ready = model_output is not None and not blockers and all(check["status"] == "pass" for check in checks)
    understanding = {
        "schema_version": UNDERSTANDING_SCHEMA,
        "status": "ready_for_change" if ready else "not_ready",
        "can_modify": ready,
        "checks": checks,
        "blockers": blockers[:20],
        "generated_by": "selected_model" if model_output is not None else "harness",
        "selected_model_id": selected_model_id or "",
    }
    understanding_path = package / "analysis" / "requirement_understanding.json"
    understanding_path.parent.mkdir(parents=True, exist_ok=True)
    understanding_path.write_text(json.dumps(understanding, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    contract_path: str | None = None
    if ready and contract_parts is not None:
        contract = {
            "schema_version": TASK_CONTRACT_SCHEMA,
            "task_key": _task_key(package.name, authorization_id),
            "project_path": str(worktree),
            "request": contract_parts["request"],
            "allowed_paths": contract_parts["allowed_paths"],
            "verification_commands": contract_parts["verification_commands"],
            "acceptance_criteria": contract_parts["acceptance_criteria"],
            "timeout_seconds": 3600,
        }
        contract_path = str(package / "engineering" / "task_contract.json")
        Path(contract_path).parent.mkdir(parents=True, exist_ok=True)
        Path(contract_path).write_text(
            json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    manifest = rebuild_requirement_package_manifest(package_dir=package, ticket_dir=package.parent, run_id=0)
    return {
        "status": "ready" if ready else "blocked",
        "blockers": blockers[:20],
        "understanding_path": str(understanding_path),
        "contract_path": contract_path,
        "pending_count": manifest["pending_count"],
        "model_generated_count": manifest["model_generated_count"],
    }


class _CompletionBlocked(RuntimeError):
    """A deterministic pre-check failed before the model could be trusted."""


def gather_project_facts(worktree: Path) -> dict[str, object]:
    """Collect bounded, read-only facts about the target project."""

    blockers: list[str] = []
    if not worktree.is_dir() or worktree.is_symlink():
        return {"blockers": [f"worktree_invalid:目标项目目录不可用（{worktree}）"], "tree": "", "git_root": ""}
    toplevel = _git(worktree, "rev-parse", "--show-toplevel")
    if toplevel is None or Path(toplevel).resolve() != worktree:
        blockers.append("worktree_not_git_root:目标项目必须是普通 git 仓库根目录")
        git_root = ""
    else:
        git_root = toplevel
    tree_lines: list[str] = []
    try:
        entries = sorted(worktree.iterdir(), key=lambda item: item.name)
        for entry in entries[:120]:
            if entry.name == ".git":
                continue
            tree_lines.append(f"{entry.name}/" if entry.is_dir() else entry.name)
    except OSError:
        blockers.append("worktree_unreadable:无法读取目标项目目录")
    manifests = [
        name for name in (
            "package.json", "pom.xml", "build.gradle", "build.gradle.kts", "requirements.txt",
            "pyproject.toml", "go.mod", "Cargo.toml", "README.md", "readme.md",
        ) if (worktree / name).is_file()
    ]
    tree = "\n".join(tree_lines)[:_MAX_PROJECT_CHARS]
    return {
        "blockers": blockers,
        "tree": tree,
        "git_root": git_root,
        "manifests": ", ".join(manifests),
    }


def _request_model_understanding(
    *,
    package: Path,
    worktree: Path,
    project_facts: Mapping[str, object],
    host_execute: Callable[[AgentBackendRequest], AgentBackendResult],
) -> Mapping[str, object]:
    requirement = _bounded_text(package / "source" / "requirement.md", _MAX_EVIDENCE_CHARS)
    draft = _bounded_text(package / "analysis" / "requirement_understanding.md", 4_000)
    answers = _bounded_text(package / "analysis" / "business_answers.md", 4_000)
    if requirement == "" and draft == "":
        raise _CompletionBlocked("intake_evidence_unavailable:归档证据与需求理解草稿都为空")
    prompt = "\n".join(
        [
            "你是 Harness 的需求理解模型。基于云效归档证据、用户已确认的业务答复和目标项目的只读事实，产出改码前的结构化理解。",
            "只输出一个 JSON 对象，不要输出其他文字。结构为：",
            json.dumps(
                {
                    "checks": [{"name": "<检查名>", "status": "pass|fail", "summary": "<结论与证据摘要，30-300字>"}],
                    "request": "<给执行模型的改码任务说明，含目标、边界和禁止事项>",
                    "allowed_paths": ["<项目内相对路径，必须来自下方项目清单>"],
                    "verification_tests": ["<项目的 python unittest 点分模块名，如 tests.test_xxx>"],
                    "acceptance_criteria": ["<可验证的验收条目>"],
                    "business_questions": ["<确有业务歧义才填写，没有则空数组>"],
                },
                ensure_ascii=False,
            ),
            f"checks.name 必须且只能是这 9 项：{', '.join(_CHECK_NAMES)}。",
            "证据不足以支撑某项检查时必须标 fail 并在 summary 说明缺口，禁止编造。",
            "verification_tests 只能给目标项目里真实存在的 python unittest 模块名；项目没有就返回空数组（会导致 blocked，这是诚实结果）。",
            "allowed_paths 是预计允许改动的路径，必须来自下方项目清单里的真实路径。",
            "用户已确认的业务答复是最高优先级口径：其中已明确的问题不得再次提问，也不得与答复矛盾。",
            "",
            "=== 云效归档证据（已脱敏） ===",
            redact_sensitive_text(requirement),
            "",
            "=== 需求理解草稿（模型起草，未确认） ===",
            draft,
        ]
        + (
            [
                "",
                "=== 用户已确认的业务答复（最高优先级） ===",
                redact_sensitive_text(answers),
            ]
            if answers != ""
            else []
        )
        + [
            "",
            "=== 目标项目只读事实 ===",
            f"路径：{worktree}",
            f"git 仓库根：{project_facts['git_root'] or '不可用'}",
            f"根目录清单：{project_facts['tree'] or '不可用'}",
            f"工程清单文件：{project_facts['manifests'] or '未识别'}",
        ],
    )
    request = AgentBackendRequest(
        role=AgentBackendRole.WORKER,
        worktree_path=worktree,
        prompt=prompt,
        timeout_seconds=_MAX_REQUEST_SECONDS,
        output_contract={"name": "harness_task_understanding", "schema_version": COMPLETION_CONTRACT_SCHEMA},
        capabilities=(),
    )
    result = host_execute(request)
    if result.error_code:
        raise _CompletionBlocked(f"model_executor_unavailable:模型执行失败（{result.error_code}）")
    payload = result.final_response or {}
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise _CompletionBlocked("model_output_empty:模型没有返回内容")
    parsed = _extract_json(text)
    if not isinstance(parsed, Mapping):
        raise _CompletionBlocked("model_output_invalid:模型输出不是 JSON 对象")
    return parsed


def _validate_model_output(
    output: Mapping[str, object],
    worktree: Path,
    project_facts: Mapping[str, object],
) -> tuple[list[dict[str, object]], list[str], dict[str, object]]:
    """Deterministic validation floor: model claims never pass on their own."""

    blockers: list[str] = []
    raw_checks = output.get("checks")
    checks: list[dict[str, object]] = []
    seen: set[str] = set()
    if isinstance(raw_checks, list):
        for item in raw_checks:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "")
            summary = str(item.get("summary") or "").strip()
            if name not in _CHECK_NAMES or name in seen:
                continue
            seen.add(name)
            status = "pass" if item.get("status") == "pass" and 10 <= len(summary) <= 600 else "fail"
            if status == "fail" and 10 <= len(summary) <= 600:
                blockers.append(f"{name}:{summary[:120]}")
            checks.append({
                "name": name,
                "status": status,
                "summary": summary[:600],
                "blockers": [] if status == "pass" else [summary[:200]],
                "evidence_refs": [{"kind": "selected_model", "ref": name}],
            })
    for name in _CHECK_NAMES:
        if name not in seen:
            checks.append({
                "name": name,
                "status": "fail",
                "summary": f"模型未提供 {_CHECK_LABELS[name]} 的理解结论",
                "blockers": [f"{name}:missing"],
                "evidence_refs": [],
            })
            blockers.append(f"{name}:missing")

    request = _text_field(output.get("request"), 20, 12_000)
    if request == "":
        blockers.append("request_invalid:改码任务说明缺失或过短")

    allowed_paths: list[str] = []
    raw_paths = output.get("allowed_paths")
    if isinstance(raw_paths, list):
        for item in raw_paths:
            if not isinstance(item, str):
                continue
            candidate = item.strip().replace("\\", "/")
            parts = [part for part in candidate.split("/") if part not in ("", ".")]
            normalized = "/".join(parts)
            if normalized == "" or ".." in parts or normalized.startswith(".git") or len(normalized) > 400:
                continue
            if normalized in allowed_paths:
                continue
            if len(allowed_paths) >= _MAX_ALLOWED_PATHS:
                break
            allowed_paths.append(normalized)
    missing = [path for path in allowed_paths if not (worktree.joinpath(*path.split("/"))).exists()]
    # 模型幻觉的路径确定性剔除（可恢复，不阻断）；只有全部路径都不可用时才阻断。
    allowed_paths = [path for path in allowed_paths if path not in missing]
    if not allowed_paths:
        blockers.append("allowed_paths_invalid:没有可用的允许改动路径（必须来自项目真实路径）")

    verification_commands: list[list[str]] = []
    raw_tests = output.get("verification_tests")
    python_executable = str(Path(sys.executable).resolve())
    if isinstance(raw_tests, list):
        for item in raw_tests:
            if not isinstance(item, str) or _TEST_NAME.fullmatch(item) is None:
                continue
            if len(verification_commands) >= _MAX_VERIFICATION_TESTS:
                break
            verification_commands.append([python_executable, "-m", "unittest", "-q", item])
    if not verification_commands:
        blockers.append("verification_baseline_missing:目标项目没有可用的 python unittest 验证基线")

    acceptance: list[str] = []
    raw_acceptance = output.get("acceptance_criteria")
    if isinstance(raw_acceptance, list):
        for item in raw_acceptance:
            text = str(item).strip()
            if 4 <= len(text) <= 600 and len(acceptance) < 64:
                acceptance.append(text)
    if not acceptance:
        blockers.append("acceptance_criteria_invalid:缺少可验证的验收标准")

    questions = output.get("business_questions")
    if isinstance(questions, list):
        for item in questions:
            text = str(item).strip()[:200]
            if text:
                blockers.append(f"business_question:{text}")

    contract_parts = {
        "request": request or "（缺失）",
        "allowed_paths": allowed_paths,
        "verification_commands": verification_commands,
        "acceptance_criteria": acceptance or ["（缺失）"],
    }
    return checks, blockers, contract_parts


def _task_key(ticket_id: str, authorization_id: str) -> str:
    raw = f"{ticket_id}-{authorization_id}".lower()
    sanitized = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-")[:128]
    return sanitized if _TASK_KEY.fullmatch(sanitized) else f"harness-{abs(hash(raw)) % 10**10}"


def _bounded_text(path: Path, limit: int) -> str:
    if not path.is_file():
        return ""
    raw = path.read_text(encoding="utf-8", errors="ignore")
    return raw[:limit]


def _text_field(value: object, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return text if minimum <= len(text) <= maximum else ""


def _extract_json(text: str) -> object | None:
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


def _git(worktree: Path, *arguments: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(worktree), *arguments],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None
