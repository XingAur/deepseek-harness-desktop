from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.capability_contracts import (
    CapabilityAuthorization,
    CapabilityRequest,
    MutationLevel,
)
from app.harness_config import DEFAULT_CREDENTIALS_FILE
from app.pg_evidence import (
    PgEvidenceRequest,
    build_database_capability_service,
)
from app.runtime_bootstrap import reexec_in_project_venv


READ_SCOPES = ("database:metadata:read", "database:rows:read")


def default_profile_policy_path() -> Path:
    """Prefer the checked-in local-development policy when it is available."""

    local_policy = PROJECT_ROOT / "config" / "pg_evidence_profiles.local.json"
    if local_policy.is_file():
        return local_policy
    return PROJECT_ROOT / "config" / "pg_evidence_profiles.example.json"


def credential_aliases_from_profile_policy(policy_path: Path) -> dict[str, str]:
    """Read explicit nonsecret credential-profile aliases from the policy."""

    try:
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    profiles = payload.get("profiles") if isinstance(payload, Mapping) else None
    if not isinstance(profiles, Mapping):
        return {}
    aliases: dict[str, str] = {}
    for target_raw, item in profiles.items():
        source_raw = item.get("credential_profile") if isinstance(item, Mapping) else ""
        target = str(target_raw).strip().lower()
        source = str(source_raw or "").strip().lower()
        if (
            re.fullmatch(r"[a-z0-9_]+", target)
            and re.fullmatch(r"[a-z0-9_]+", source)
            and target != source
        ):
            aliases[target] = source
    return aliases


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plan or execute an explicit, readonly PostgreSQL evidence query."
    )
    parser.add_argument("--request-file", required=True, help="PG evidence request JSON file")
    parser.add_argument(
        "--profile-policy",
        default=str(default_profile_policy_path()),
        help="PG profile policy JSON file",
    )
    parser.add_argument(
        "--credentials-file",
        default="",
        help="credentials JSON; defaults to HARNESS_CREDENTIALS_FILE or the local Harness credential path",
    )
    parser.add_argument(
        "--mode",
        choices=["plan", "execute"],
        default="plan",
        help="plan never creates a driver or DB connection; execute is explicit readonly access",
    )
    parser.add_argument(
        "--project-root",
        required=True,
        help="local source root used for readonly candidate inference",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="directory for redacted PG evidence artifacts",
    )
    args = parser.parse_args()

    request = load_request_file(Path(args.request_file))
    credentials_path = Path(
        args.credentials_file
        or os.environ.get("HARNESS_CREDENTIALS_FILE")
        or DEFAULT_CREDENTIALS_FILE
    ).expanduser()
    policy_path = Path(args.profile_policy).expanduser().resolve()
    service = build_database_capability_service(
        credentials_path,
        credential_aliases=credential_aliases_from_profile_policy(policy_path),
    )
    preview_request = build_capability_request(
        request=request,
        profile_policy=policy_path,
        project_root=Path(args.project_root).expanduser().resolve(),
        mode="plan",
    )
    preview_result = service.route(preview_request).result
    capability_result = preview_result
    if args.mode == "execute":
        if database_preview_is_readonly_ready(preview_result):
            execute_request = build_capability_request(
                request=request,
                profile_policy=policy_path,
                project_root=Path(args.project_root).expanduser().resolve(),
                mode="execute",
            )
            capability_result = service.route(execute_request).result
        else:
            capability_result = blocked_execute_result(preview_request)
    files = write_capability_outputs(
        Path(args.output_dir),
        capability_result,
    )
    data = capability_result.get("data")
    data = data if isinstance(data, Mapping) else {}
    pg_status = str(
        data.get("pg_status") or capability_result.get("status") or "failed"
    )

    print(f"PG evidence status: {pg_status}")
    print(f"Plan: {files['pg_evidence_plan.json']}")
    print(f"Result: {files['pg_evidence_result.json']}")
    print(f"Audit: {files['pg_evidence_audit.json']}")
    if args.mode == "execute" and pg_status != "passed":
        raise SystemExit(1)


def database_preview_is_readonly_ready(result: Mapping[str, object]) -> bool:
    data = result.get("data")
    plan = data.get("plan") if isinstance(data, Mapping) else None
    guard = plan.get("guard") if isinstance(plan, Mapping) else None
    if result.get("status") != "success" or not isinstance(plan, Mapping):
        return False
    # A metadata_required plan is still safe to execute: the provider performs
    # one bounded, readonly metadata lookup before the SELECT.  Blocking this
    # state at the CLI made the real database path impossible whenever the
    # schema was not encoded in the request SQL.
    if plan.get("status") not in {"ready", "metadata_required"}:
        return False
    if not isinstance(plan.get("selected_profile"), str) or not plan.get("selected_profile"):
        return False
    if plan.get("status") == "metadata_required" and int(plan.get("metadata_queries_remaining") or 0) <= 0:
        return False
    if not isinstance(guard, Mapping):
        return False
    # The provider contract represents the SQL guard as ``status: pass``;
    # accept the older expanded shape too, but never accept an unguarded plan.
    return bool(
        guard.get("status") == "pass"
        or (
            guard.get("allowed") is True
            and guard.get("statement_type") == "SELECT"
        )
    )


def blocked_execute_result(request: CapabilityRequest) -> dict[str, object]:
    blocker = "database_readonly_preview_not_ready"
    return {
        "request_id": request.request_id,
        "capability": request.capability,
        "provider": request.provider,
        "status": "blocked",
        "mutation_level": request.mutation_level.name,
        "changed": False,
        "summary": "DATABASE_INSPECT_BLOCKED",
        "data": {
            "pg_status": "blocked",
            "plan_status": "blocked",
        },
        "evidence": [],
        "warnings": [],
        "blockers": [blocker],
        "audit": {
            "database_connection_attempted": False,
            "database_execution_attempted": False,
        },
    }


def build_capability_request(
    *,
    request: PgEvidenceRequest,
    profile_policy: Path,
    project_root: Path,
    mode: str,
) -> CapabilityRequest:
    execute = mode == "execute"
    return CapabilityRequest(
        request_id=f"pg-evidence-cli-{uuid.uuid4().hex}",
        capability="database.inspect",
        provider="postgresql",
        mode="apply" if execute else "preview",
        mutation_level=MutationLevel.L1,
        authorization=CapabilityAuthorization(
            explicit=execute,
            scope=READ_SCOPES if execute else (),
        ),
        input={
            "subject": request.subject,
            "keywords": list(request.keywords),
            "sql": request.sql,
            "parameters": dict(request.parameters),
            "project_root": str(project_root),
            "profile_policy": str(profile_policy),
            "mode": mode,
        },
        context={},
    )


def write_capability_outputs(
    output_dir: Path,
    capability_result: Mapping[str, Any],
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    data = capability_result.get("data")
    data = data if isinstance(data, dict) else {}
    blockers = capability_result.get("blockers")
    blockers = blockers if isinstance(blockers, list) else []
    plan = data.get("plan")
    plan = (
        plan
        if isinstance(plan, dict)
        else {
            "status": data.get("plan_status") or "blocked",
            "blockers": blockers,
        }
    )
    result = data.get("result")
    result = (
        result
        if isinstance(result, dict)
        else {
            "status": data.get("pg_status") or capability_result.get("status") or "failed",
            "row_count": 0,
            "masked_columns": [],
            "blockers": blockers,
        }
    )
    combined_audit = capability_result.get("audit")
    combined_audit = combined_audit if isinstance(combined_audit, dict) else {}
    provider_audit = combined_audit.get("provider")
    provider_audit = provider_audit if isinstance(provider_audit, dict) else combined_audit
    runtime_audit = combined_audit.get("runtime")
    runtime_audit = runtime_audit if isinstance(runtime_audit, dict) else {"environment_keys": []}
    audit = {
        "schema_version": "pg-evidence-capability-audit.v1",
        "capability_runtime": True,
        "capability_status": capability_result.get("status"),
        "provider": provider_audit,
        "runtime": runtime_audit,
    }
    payloads = {
        "pg_evidence_plan.json": plan,
        "pg_evidence_result.json": result,
        "pg_evidence_audit.json": audit,
        "pg_evidence_plan.md": _plan_markdown(plan),
        "pg_evidence_result.md": _result_markdown(result),
    }
    files: dict[str, str] = {}
    for name, payload in payloads.items():
        path = output_dir / name
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        files[name] = str(path)
    return files


def _plan_markdown(plan: Mapping[str, Any]) -> str:
    blockers = plan.get("blockers")
    blocker_lines = (
        "\n".join(f"- {item}" for item in blockers)
        if isinstance(blockers, list) and blockers
        else "- 无"
    )
    return (
        "# PostgreSQL 数据证据计划\n\n"
        f"- 状态：`{plan.get('status') or '-'}`\n"
        f"- Profile：`{plan.get('selected_profile') or '-'}`\n"
        f"- 表：`{plan.get('selected_table') or '-'}`\n"
        f"- 查询模板：`{plan.get('query_template_id') or '-'}`\n\n"
        "## 阻断项\n\n"
        f"{blocker_lines}\n"
    )


def _result_markdown(result: Mapping[str, Any]) -> str:
    masked_columns = result.get("masked_columns")
    masked = ", ".join(str(item) for item in masked_columns) if isinstance(masked_columns, list) else ""
    blockers = result.get("blockers")
    blocker_lines = (
        "\n".join(f"- {item}" for item in blockers)
        if isinstance(blockers, list) and blockers
        else "- 无"
    )
    return (
        "# PostgreSQL 数据证据结果\n\n"
        f"- 状态：`{result.get('status') or '-'}`\n"
        f"- Profile：`{result.get('profile') or '-'}`\n"
        f"- 表：`{result.get('table') or '-'}`\n"
        f"- 行数：{result.get('row_count') or 0}\n"
        f"- 脱敏列：{masked or '-'}\n\n"
        "## 阻断项\n\n"
        f"{blocker_lines}\n"
    )


def load_request_file(path: Path) -> PgEvidenceRequest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"无法读取 PG 证据请求文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"PG 证据请求文件不是合法 JSON：{path}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("PG 证据请求根节点必须是 JSON 对象。")
    parameters = payload.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise SystemExit("parameters 必须是 JSON 对象。")
    keywords = payload.get("keywords") or []
    if not isinstance(keywords, list):
        raise SystemExit("keywords 必须是 JSON 数组。")
    return PgEvidenceRequest(
        subject=str(payload.get("subject") or "").strip(),
        keywords=tuple(str(item).strip() for item in keywords if str(item).strip()),
        sql=str(payload.get("sql") or ""),
        parameters=parameters,
    )


if __name__ == "__main__":
    reexec_in_project_venv(PROJECT_ROOT)
    main()
