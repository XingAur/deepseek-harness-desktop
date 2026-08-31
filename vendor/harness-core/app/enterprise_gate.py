from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.version import VERSION


ENTERPRISE_GATE_SCHEMA_VERSION = "1.0-enterprise-core-gate"
STAGE_TIMEOUT_SECONDS = 300
UNIT_STAGE_TIMEOUT_SECONDS = 1200
DEFAULT_GATE_STAGES = ("compile", "unit", "selfcheck", "replay", "secret")
SOURCE_SCAN_DIRECTORIES = ("app", "tools", "harnesses", "fixtures", "config", "prompts")
SECRET_ENV_EXACT_NAMES = frozenset({"PAT", "TOKEN", "PASSWORD", "SECRET", "CREDENTIALS"})
SECRET_ENV_SEGMENTS = frozenset({"API_KEY", "AUTH_TOKEN", "ACCESS_TOKEN", "PRIVATE_KEY", "PASSWORD", "CREDENTIAL"})
SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer_token", re.compile(r"Authorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/=-]{24,}", re.IGNORECASE)),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b")),
)


def sanitize_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Remove credential-bearing variables before starting offline gate subprocesses."""
    return {
        str(key): str(value)
        for key, value in environment.items()
        if not is_secret_environment_name(str(key))
    }


def is_secret_environment_name(name: str) -> bool:
    upper = name.upper()
    if upper in SECRET_ENV_EXACT_NAMES:
        return True
    if upper.endswith("_PAT") or upper.startswith("PAT_"):
        return True
    if upper.endswith("_TOKEN") or upper.startswith("TOKEN_"):
        return True
    if upper.endswith("_SECRET") or upper.startswith("SECRET_"):
        return True
    return any(segment in upper for segment in SECRET_ENV_SEGMENTS)


def scan_source_secrets(root: str | Path) -> dict[str, Any]:
    project_root = Path(root).expanduser().resolve()
    findings: list[dict[str, Any]] = []
    files_scanned = 0
    for directory_name in SOURCE_SCAN_DIRECTORIES:
        directory = project_root / directory_name
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if not should_scan_source_file(path):
                continue
            files_scanned += 1
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line_number, line in enumerate(content.splitlines(), start=1):
                for kind, pattern in SECRET_PATTERNS:
                    match = pattern.search(line)
                    if match is None:
                        continue
                    findings.append(
                        {
                            "path": path.relative_to(project_root).as_posix(),
                            "line": line_number,
                            "kind": kind,
                            "fingerprint": hashlib.sha256(match.group(0).encode("utf-8")).hexdigest()[:12],
                        }
                    )
    return {
        "status": "failed" if findings else "passed",
        "files_scanned": files_scanned,
        "findings": findings,
    }


def should_scan_source_file(path: Path) -> bool:
    if not path.is_file() or "__pycache__" in path.parts:
        return False
    if path.name in {"harness.sqlite", ".DS_Store"}:
        return False
    return path.suffix.lower() in {
        ".py",
        ".json",
        ".md",
        ".toml",
        ".yaml",
        ".yml",
        ".ini",
        ".cfg",
        ".txt",
        ".sh",
    }


def run_enterprise_gate(
    *,
    project_root: str | Path,
    output_dir: str | Path,
    iterations: int = 1,
    stages: Sequence[str] = DEFAULT_GATE_STAGES,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    selected_stages = validate_stages(stages)
    if iterations < 1:
        raise ValueError("iterations 必须大于等于 1。")

    iteration_results: list[dict[str, Any]] = []
    write_json_atomic(
        destination / "enterprise_gate_checkpoint.json",
        {
            "schema_version": ENTERPRISE_GATE_SCHEMA_VERSION,
            "status": "running",
            "iterations_requested": iterations,
            "iterations_completed": 0,
            "external_calls": False,
        },
    )
    for iteration in range(1, iterations + 1):
        iteration_dir = destination / f"iteration_{iteration:02d}"
        iteration_dir.mkdir(parents=True, exist_ok=True)
        stage_results = [
            run_gate_stage(
                stage,
                project_root=root,
                output_dir=iteration_dir,
                iteration=iteration,
            )
            for stage in selected_stages
        ]
        iteration_result = {
            "iteration": iteration,
            "status": "passed" if all(item["status"] == "passed" for item in stage_results) else "failed",
            "stages": stage_results,
        }
        iteration_results.append(iteration_result)
        write_json_atomic(iteration_dir / "iteration_result.json", iteration_result)
        write_json_atomic(
            destination / "enterprise_gate_checkpoint.json",
            {
                "schema_version": ENTERPRISE_GATE_SCHEMA_VERSION,
                "status": "running",
                "iterations_requested": iterations,
                "iterations_completed": iteration,
                "iterations_passed": sum(item["status"] == "passed" for item in iteration_results),
                "external_calls": False,
            },
        )

    replay_hashes = sorted(
        {
            str(stage.get("result_hash"))
            for item in iteration_results
            for stage in item["stages"]
            if stage["name"] == "replay" and stage.get("result_hash")
        }
    )
    replay_consistent = len(replay_hashes) <= 1
    passed = all(item["status"] == "passed" for item in iteration_results) and replay_consistent
    result: dict[str, Any] = {
        "schema_version": ENTERPRISE_GATE_SCHEMA_VERSION,
        "status": "passed" if passed else "failed",
        "version": VERSION,
        "interpreter": sys.executable,
        "python_version": platform.python_version(),
        "stage_timeout_seconds": STAGE_TIMEOUT_SECONDS,
        "unit_stage_timeout_seconds": UNIT_STAGE_TIMEOUT_SECONDS,
        "technical_valid": passed,
        "business_valid": False,
        "runtime_verified": False,
        "promotion_enabled": False,
        "external_calls": False,
        "real_git_remote_writes_used": False,
        "local_git_fixture_only": True,
        "persistent_database_used": False,
        "real_model_runtime_used": False,
        "iterations_requested": iterations,
        "iterations_passed": sum(item["status"] == "passed" for item in iteration_results),
        "stages": list(selected_stages),
        "replay_result_hashes": replay_hashes,
        "replay_deterministic": replay_consistent,
        "iterations": iteration_results,
        "boundaries": [
            "该门禁仅证明 Harness 本地离线技术闭环通过。",
            "未验证真实 HIS 业务结果、浏览器运行时、生产环境或人工验收。",
            "未读取凭证、未调用模型或网络、未访问业务数据库、未写云效或真实 Git 远端。",
            "Git 交付测试仅使用进程内创建的临时仓库和本地 bare remote fixture。",
        ],
    }
    result["result_hash"] = stable_result_hash(result)
    write_json_atomic(
        destination / "enterprise_gate_checkpoint.json",
        {
            "schema_version": ENTERPRISE_GATE_SCHEMA_VERSION,
            "status": "completed",
            "result_status": result["status"],
            "iterations_requested": iterations,
            "iterations_completed": len(iteration_results),
            "iterations_passed": result["iterations_passed"],
            "result_hash": result["result_hash"],
            "external_calls": False,
        },
    )
    return result


def validate_stages(stages: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(str(stage).strip().lower() for stage in stages if str(stage).strip())
    unknown = sorted(set(normalized) - set(DEFAULT_GATE_STAGES))
    if unknown:
        raise ValueError(f"不支持的门禁阶段：{', '.join(unknown)}。")
    if not normalized:
        raise ValueError("至少选择一个门禁阶段。")
    if len(set(normalized)) != len(normalized):
        raise ValueError("门禁阶段不能重复。")
    return normalized


def stage_timeout_seconds(stage: str) -> int:
    return UNIT_STAGE_TIMEOUT_SECONDS if stage == "unit" else STAGE_TIMEOUT_SECONDS

def run_gate_stage(
    stage: str,
    *,
    project_root: Path,
    output_dir: Path,
    iteration: int,
) -> dict[str, Any]:
    started = time.monotonic()
    if stage == "secret":
        scan = scan_source_secrets(project_root)
        return {
            "name": stage,
            "status": scan["status"],
            "duration_ms": round((time.monotonic() - started) * 1000),
            "files_scanned": scan["files_scanned"],
            "findings": scan["findings"],
        }

    command, stage_output_dir = build_stage_command(
        stage,
        project_root=project_root,
        output_dir=output_dir,
    )
    environment = sanitize_environment(os.environ)
    environment.update(
        {
            "HARNESS_DB_PATH": str(output_dir / f"harness_gate_{iteration:02d}_{stage}.sqlite"),
            "HARNESS_RUNTIME_MODE": "mock",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    try:
        completed = subprocess.run(
            command,
            cwd=project_root,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=stage_timeout_seconds(stage),
            check=False,
        )
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        result: dict[str, Any] = {
            "name": stage,
            "status": "passed" if completed.returncode == 0 else "failed",
            "returncode": completed.returncode,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "output_digest": hashlib.sha256(output.encode("utf-8")).hexdigest(),
            "output_tail": redact_output_tail(output),
        }
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(str(part or "") for part in (exc.stdout, exc.stderr))
        result = {
            "name": stage,
            "status": "failed",
            "returncode": None,
            "reason": "timeout",
            "duration_ms": round((time.monotonic() - started) * 1000),
            "error": "timeout",
            "output_digest": hashlib.sha256(output.encode("utf-8")).hexdigest(),
            "output_tail": redact_output_tail(output),
        }
    if stage == "replay" and stage_output_dir is not None:
        replay_path = stage_output_dir / "real_replay_result.json"
        if replay_path.is_file():
            replay_result = json.loads(replay_path.read_text(encoding="utf-8"))
            result["result_hash"] = replay_result.get("result_hash")
            result["scenario_count"] = replay_result.get("summary", {}).get("total")
    return result


def build_stage_command(
    stage: str,
    *,
    project_root: Path,
    output_dir: Path,
) -> tuple[list[str], Path | None]:
    python = sys.executable
    if stage == "compile":
        return [python, str(project_root / "tools" / "syntax_check.py")], None
    if stage == "unit":
        return [python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"], None
    if stage == "selfcheck":
        stage_output = output_dir / "selfcheck"
        return [
            python,
            str(project_root / "tools" / "self_check.py"),
            "--mode",
            "mock",
            "--output-dir",
            str(stage_output),
            "--retain-output",
        ], stage_output
    if stage == "replay":
        stage_output = output_dir / "replay"
        return [
            python,
            str(project_root / "tools" / "replay_suite.py"),
            "--output-dir",
            str(stage_output),
        ], stage_output
    raise ValueError(f"不支持的命令阶段：{stage}。")


def redact_output_tail(output: str, *, limit: int = 2000) -> str:
    redacted = output
    for _, pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted[-limit:]


def stable_result_hash(payload: Mapping[str, Any]) -> str:
    stable = json.loads(json.dumps(payload, ensure_ascii=False))
    stable.pop("result_hash", None)
    for iteration in stable.get("iterations", []):
        for stage in iteration.get("stages", []):
            stage.pop("duration_ms", None)
            stage.pop("output_tail", None)
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def enterprise_gate_to_markdown(result: Mapping[str, Any]) -> str:
    lines = [
        "# HIS Harness 企业核心离线门禁报告",
        "",
        f"- 状态：{result['status']}",
        f"- 技术闭环有效：{'是' if result['technical_valid'] else '否'}",
        "- 业务有效：否",
        "- 真实运行时已验证：否",
        "- 外部调用：否",
        "- 真实 Git 远端写入：否",
        f"- Git 测试环境：{'仅本地 fixture' if result.get('local_git_fixture_only') else '未声明'}",
        f"- 连续轮次：{result['iterations_passed']}/{result['iterations_requested']}",
        f"- 回放结果一致：{'是' if result['replay_deterministic'] else '否'}",
        f"- 结果哈希：`{result['result_hash']}`",
        "",
        "## 轮次",
        "",
    ]
    for iteration in result["iterations"]:
        lines.append(f"### 第 {iteration['iteration']} 轮：{iteration['status']}")
        lines.append("")
        for stage in iteration["stages"]:
            detail = f"，returncode={stage['returncode']}" if "returncode" in stage else ""
            lines.append(f"- `{stage['name']}`：{stage['status']}{detail}")
        lines.append("")
    lines.extend(["## 边界", ""])
    lines.extend(f"- {boundary}" for boundary in result["boundaries"])
    return "\n".join(lines) + "\n"
