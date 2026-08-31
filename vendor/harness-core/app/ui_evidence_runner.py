from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any


UI_EVIDENCE_RUNNER_VERSION = "0.10.3B"
DEFAULT_UI_EVIDENCE_TIMEOUT_SECONDS = 180


def run_ui_evidence_commands(
    *,
    commands: list[str],
    cwd: str | Path,
    output_dir: str | Path,
    timeout_seconds: int = DEFAULT_UI_EVIDENCE_TIMEOUT_SECONDS,
) -> dict:
    started_at = time.time()
    cwd_path = Path(cwd).expanduser().resolve()
    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    command_results: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    assertions: list[dict[str, Any]] = []
    for index, command in enumerate(commands or [], start=1):
        command_result = run_ui_evidence_command(
            command=command,
            cwd=cwd_path,
            output_dir=output_path,
            timeout_seconds=timeout_seconds,
        )
        command_result["index"] = index
        parsed = parse_ui_evidence_stdout(command_result.get("stdout") or "")
        command_result["parsed"] = parsed.get("status")
        command_result["parse_error"] = parsed.get("error") or ""
        for item in parsed.get("artifacts") or []:
            artifacts.append(normalize_artifact(item=item, cwd=cwd_path, output_dir=output_path, command=command, command_index=index))
        for item in parsed.get("assertions") or []:
            assertions.append(normalize_assertion(item=item, command=command, command_index=index))
        if command_result.get("returncode") != 0 and not parsed.get("artifacts") and not parsed.get("assertions"):
            assertions.append(
                {
                    "name": f"UI-CAPTURE-COMMAND-{index}",
                    "status": "failed",
                    "evidence": f"UI 证据采集命令执行失败：{command_result.get('stderr') or command_result.get('stdout') or 'returncode=' + str(command_result.get('returncode'))}",
                    "source": "ui_evidence_runner",
                    "command": command,
                    "command_index": index,
                    "returncode": command_result.get("returncode"),
                }
            )
        if parsed.get("status") == "failed" and command_result.get("returncode") == 0:
            assertions.append(
                {
                    "name": f"UI-CAPTURE-COMMAND-{index}",
                    "status": "failed",
                    "evidence": parsed.get("error") or "UI 证据采集命令未输出有效 JSON。",
                    "source": "ui_evidence_runner",
                    "command": command,
                    "command_index": index,
                    "returncode": command_result.get("returncode"),
                }
            )
        command_results.append(command_result)
    artifact_paths = [str(item.get("resolved_path")) for item in artifacts if item.get("exists") and item.get("resolved_path")]
    missing_artifacts = [item for item in artifacts if not item.get("exists")]
    failed_assertions = [item for item in assertions if str(item.get("status") or "").lower() != "pass"]
    if not commands:
        status = "skipped"
        summary = "未提供 UI 证据采集命令。"
    elif failed_assertions:
        status = "failed"
        summary = "UI 状态断言失败：" + "；".join(str(item.get("name") or "-") for item in failed_assertions)
    elif missing_artifacts:
        status = "failed"
        summary = "UI 证据文件缺失：" + "；".join(str(item.get("path") or "-") for item in missing_artifacts)
    elif not artifact_paths:
        status = "needs_evidence"
        summary = "UI 证据采集命令未生成截图、视频、GIF 或人工记录文件。"
    else:
        status = "pass"
        summary = "UI 证据采集命令通过，已生成可归档 UI 证据。"
    return {
        "version": UI_EVIDENCE_RUNNER_VERSION,
        "status": status,
        "summary": summary,
        "cwd": str(cwd_path),
        "output_dir": str(output_path),
        "started_at_epoch": started_at,
        "finished_at_epoch": time.time(),
        "artifact_paths": artifact_paths,
        "artifacts": artifacts,
        "assertions": assertions,
        "commands": command_results,
    }


def run_ui_evidence_command(*, command: str, cwd: Path, output_dir: Path, timeout_seconds: int) -> dict:
    env = os.environ.copy()
    env["HARNESS_UI_EVIDENCE_DIR"] = str(output_dir)
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
            env=env,
        )
        return {
            "command": command,
            "cwd": str(cwd),
            "output_dir": str(output_dir),
            "returncode": completed.returncode,
            "stdout": truncate_text(completed.stdout or "", 4000),
            "stderr": truncate_text(completed.stderr or "", 4000),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "cwd": str(cwd),
            "output_dir": str(output_dir),
            "returncode": 124,
            "stdout": truncate_text(exc.stdout or "", 4000),
            "stderr": truncate_text(exc.stderr or f"timeout after {timeout_seconds}s", 4000),
            "timed_out": True,
        }


def parse_ui_evidence_stdout(stdout: str) -> dict:
    text = (stdout or "").strip()
    if not text:
        return {"status": "failed", "error": "stdout 为空，无法解析 UI 证据采集结果。", "artifacts": [], "assertions": []}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return {"status": "failed", "error": f"stdout 不是合法 JSON：{exc}", "artifacts": [], "assertions": []}
    if not isinstance(payload, dict):
        return {"status": "failed", "error": "stdout JSON 必须是对象。", "artifacts": [], "assertions": []}
    artifacts = payload.get("artifacts") or []
    assertions = payload.get("assertions") or []
    if not isinstance(artifacts, list):
        return {"status": "failed", "error": "JSON 中 artifacts 必须是数组。", "artifacts": [], "assertions": []}
    if not isinstance(assertions, list):
        return {"status": "failed", "error": "JSON 中 assertions 必须是数组。", "artifacts": [], "assertions": []}
    return {"status": "pass", "error": "", "artifacts": artifacts, "assertions": assertions}


def normalize_artifact(*, item: dict, cwd: Path, output_dir: Path, command: str, command_index: int) -> dict:
    raw_path = str(item.get("path") or "")
    resolved_path = resolve_artifact_path(raw_path, cwd=cwd, output_dir=output_dir)
    return {
        "path": raw_path,
        "resolved_path": str(resolved_path) if resolved_path else "",
        "exists": bool(resolved_path and resolved_path.is_file()),
        "kind": str(item.get("kind") or infer_artifact_kind(raw_path)),
        "label": str(item.get("label") or item.get("name") or "-"),
        "source": "ui_evidence_runner",
        "command": command,
        "command_index": command_index,
    }


def normalize_assertion(*, item: dict, command: str, command_index: int) -> dict:
    status = str(item.get("status") or "").lower()
    if status not in {"pass", "failed", "needs_evidence"}:
        status = "failed"
    return {
        "name": str(item.get("name") or f"UI-CAPTURE-ASSERTION-{command_index}"),
        "status": status,
        "evidence": str(item.get("evidence") or item.get("message") or "-"),
        "source": "ui_evidence_runner",
        "command": command,
        "command_index": command_index,
    }


def resolve_artifact_path(path: str, *, cwd: Path, output_dir: Path) -> Path | None:
    if not path:
        return None
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    output_candidate = (output_dir / candidate).resolve()
    if output_candidate.exists():
        return output_candidate
    return (cwd / candidate).resolve()


def infer_artifact_kind(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return "screenshot"
    if suffix in {".mp4", ".webm", ".mov"}:
        return "video"
    if suffix == ".gif":
        return "gif"
    if suffix in {".md", ".txt", ".json"}:
        return "manual_record"
    return "artifact"


def ui_evidence_runner_to_json(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)


def ui_evidence_runner_to_markdown(result: dict) -> str:
    lines = [
        "## v0.10.3B UI 证据采集执行器",
        "",
        f"- 版本：{result.get('version') or UI_EVIDENCE_RUNNER_VERSION}",
        f"- 状态：{result.get('status') or '-'}",
        f"- 结论：{result.get('summary') or '-'}",
        f"- 工作目录：{result.get('cwd') or '-'}",
        f"- 证据目录：{result.get('output_dir') or '-'}",
        "",
        "### UI 证据文件",
        "",
        "| 类型 | 标签 | 路径 | 存在 |",
        "| --- | --- | --- | --- |",
    ]
    for item in result.get("artifacts") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("kind") or "-"),
                    str(item.get("label") or "-"),
                    str(item.get("resolved_path") or item.get("path") or "-").replace("|", "\\|"),
                    str(bool(item.get("exists"))),
                ]
            )
            + " |"
        )
    if not (result.get("artifacts") or []):
        lines.append("| - | - | 未生成 UI 证据文件 | False |")
    lines.extend(["", "### UI 状态断言", "", "| 断言 | 状态 | 证据 |", "| --- | --- | --- |"])
    for item in result.get("assertions") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("name") or "-"),
                    str(item.get("status") or "-"),
                    str(item.get("evidence") or "-").replace("\n", "<br>"),
                ]
            )
            + " |"
        )
    if not (result.get("assertions") or []):
        lines.append("| - | skipped | 未输出 UI 状态断言 |")
    lines.extend(["", "### 命令结果", ""])
    for item in result.get("commands") or []:
        lines.append(f"- returncode={item.get('returncode')} `{item.get('command')}`")
        if item.get("parse_error"):
            lines.append(f"  - parse_error：{item.get('parse_error')}")
    if not (result.get("commands") or []):
        lines.append("- 未执行命令。")
    return "\n".join(lines)


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...（内容已截断）"
