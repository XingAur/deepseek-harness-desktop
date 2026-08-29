from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any


METHOD_TEST_RUNNER_VERSION = "0.10.3A"
DEFAULT_METHOD_TEST_TIMEOUT_SECONDS = 120


def run_method_test_commands(
    *,
    behavior_test_plan: dict,
    commands: list[str],
    cwd: str | Path,
    timeout_seconds: int = DEFAULT_METHOD_TEST_TIMEOUT_SECONDS,
) -> dict:
    started_at = time.time()
    cases: list[dict[str, Any]] = []
    command_results: list[dict[str, Any]] = []
    cwd_path = Path(cwd).expanduser().resolve()
    for index, command in enumerate(commands or [], start=1):
        command_result = run_method_test_command(command=command, cwd=cwd_path, timeout_seconds=timeout_seconds)
        command_result["index"] = index
        parsed = parse_method_evidence_stdout(command_result.get("stdout") or "")
        command_result["parsed"] = parsed.get("status")
        command_result["parse_error"] = parsed.get("error") or ""
        command_cases = parsed.get("cases") or []
        for item in command_cases:
            cases.append(normalize_runner_case(item=item, command=command, command_index=index, returncode=command_result.get("returncode")))
        if command_result.get("returncode") != 0 and not command_cases:
            cases.append(
                {
                    "id": f"METHOD-TEST-COMMAND-{index}",
                    "status": "failed",
                    "evidence": f"方法级测试命令执行失败：{command_result.get('stderr') or command_result.get('stdout') or 'returncode=' + str(command_result.get('returncode'))}",
                    "source": "method_test_runner",
                    "command": command,
                    "command_index": index,
                    "returncode": command_result.get("returncode"),
                }
            )
        if parsed.get("status") == "failed" and command_result.get("returncode") == 0:
            cases.append(
                {
                    "id": f"METHOD-TEST-COMMAND-{index}",
                    "status": "failed",
                    "evidence": parsed.get("error") or "方法级测试命令未输出有效 JSON cases。",
                    "source": "method_test_runner",
                    "command": command,
                    "command_index": index,
                    "returncode": command_result.get("returncode"),
                }
            )
        command_results.append(command_result)
    required_ids = [
        str(item.get("id") or "")
        for item in behavior_test_plan.get("cases") or []
        if item.get("required") and item.get("id")
    ]
    case_by_id = {str(item.get("id") or ""): item for item in cases if item.get("id")}
    missing = [case_id for case_id in required_ids if case_id not in case_by_id]
    failed = [item for item in cases if str(item.get("status") or "").lower() != "pass"]
    if not commands:
        status = "skipped"
        summary = "未提供方法级测试命令。"
    elif failed:
        status = "failed"
        summary = "方法级测试命令失败：" + "；".join(str(item.get("id")) for item in failed)
    elif missing:
        status = "needs_evidence"
        summary = "方法级测试命令未覆盖必需用例：" + "；".join(missing)
    else:
        status = "pass"
        summary = "方法级测试命令通过，已生成必需用例证据。"
    return {
        "version": METHOD_TEST_RUNNER_VERSION,
        "status": status,
        "summary": summary,
        "cwd": str(cwd_path),
        "started_at_epoch": started_at,
        "finished_at_epoch": time.time(),
        "required_case_ids": required_ids,
        "missing_case_ids": missing,
        "cases": cases,
        "commands": command_results,
    }


def run_method_test_command(*, command: str, cwd: Path, timeout_seconds: int) -> dict:
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
        )
        return {
            "command": command,
            "cwd": str(cwd),
            "returncode": completed.returncode,
            "stdout": truncate_text(completed.stdout or "", 4000),
            "stderr": truncate_text(completed.stderr or "", 4000),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "cwd": str(cwd),
            "returncode": 124,
            "stdout": truncate_text(exc.stdout or "", 4000),
            "stderr": truncate_text(exc.stderr or f"timeout after {timeout_seconds}s", 4000),
            "timed_out": True,
        }


def parse_method_evidence_stdout(stdout: str) -> dict:
    text = (stdout or "").strip()
    if not text:
        return {"status": "failed", "error": "stdout 为空，无法解析方法级测试证据。", "cases": []}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return {"status": "failed", "error": f"stdout 不是合法 JSON：{exc}", "cases": []}
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list):
        return {"status": "failed", "error": "JSON 中缺少 cases 数组。", "cases": []}
    return {"status": "pass", "error": "", "cases": cases}


def normalize_runner_case(*, item: dict, command: str, command_index: int, returncode: int | None) -> dict:
    status = str(item.get("status") or "").lower()
    if status not in {"pass", "failed", "needs_evidence"}:
        status = "failed"
    return {
        "id": str(item.get("id") or f"METHOD-TEST-COMMAND-{command_index}"),
        "status": status,
        "evidence": str(item.get("evidence") or item.get("message") or "-"),
        "source": "method_test_runner",
        "command": command,
        "command_index": command_index,
        "returncode": returncode,
    }


def method_test_runner_to_json(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)


def method_test_runner_to_markdown(result: dict) -> str:
    lines = [
        "## v0.10.3A 方法级测试执行器",
        "",
        f"- 版本：{result.get('version') or METHOD_TEST_RUNNER_VERSION}",
        f"- 状态：{result.get('status') or '-'}",
        f"- 结论：{result.get('summary') or '-'}",
        f"- 工作目录：{result.get('cwd') or '-'}",
        "",
        "### 用例结果",
        "",
        "| 用例 | 状态 | 证据 | 命令 |",
        "| --- | --- | --- | --- |",
    ]
    for item in result.get("cases") or []:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("id") or "-"),
                    str(item.get("status") or "-"),
                    str(item.get("evidence") or "-").replace("\n", "<br>"),
                    str(item.get("command") or "-").replace("|", "\\|"),
                ]
            )
            + " |"
        )
    if not (result.get("cases") or []):
        lines.append("| - | skipped | 未生成方法级用例结果 | - |")
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
