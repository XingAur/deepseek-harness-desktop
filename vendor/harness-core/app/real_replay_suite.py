from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from app.acceptance_contracts import execute_acceptance_contract
from app.change_ownership import build_change_ownership_matrix
from app.core_closure import build_requirement_contract
from app.requirement_calibration import build_requirement_calibration, find_high_risk_terms


REPLAY_MANIFEST_VERSION = "1.0-real-replay-manifest"
REPLAY_RESULT_VERSION = "1.0-real-replay-result"
REQUIRED_CATEGORY_COUNTS = {
    "frontend": 3,
    "backend": 2,
    "fullstack": 2,
    "ordering": 1,
    "high_risk": 2,
}
REQUIRED_CASE_LIST_FIELDS = (
    "source_refs",
    "allowed_paths",
    "expected_diff_features",
    "verify_commands",
    "manual_acceptance",
)


def load_replay_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).expanduser().resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_replay_manifest(payload)
    return payload


def validate_replay_manifest(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise ValueError("真实回放 manifest 根节点必须是 JSON 对象。")
    if payload.get("schema_version") != REPLAY_MANIFEST_VERSION:
        raise ValueError(f"schema_version 必须为 {REPLAY_MANIFEST_VERSION}。")
    if not str(payload.get("suite_id") or "").strip():
        raise ValueError("真实回放 manifest 缺少 suite_id。")
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != 10:
        raise ValueError("真实回放 manifest 必须固定包含 10 个场景。")
    category_counts = Counter(str(case.get("category") or "") for case in cases if isinstance(case, dict))
    if dict(category_counts) != REQUIRED_CATEGORY_COUNTS:
        raise ValueError(f"真实回放分类必须为 {REQUIRED_CATEGORY_COUNTS}，实际为 {dict(category_counts)}。")
    ids: set[str] = set()
    for case in cases:
        validate_replay_case(case, ids=ids)
    if len({str(case.get("entity_id") or "") for case in cases}) < 8:
        raise ValueError("10 个回放场景必须来自至少 8 个不同的真实需求编号。")


def validate_replay_case(case: Any, *, ids: set[str]) -> None:
    if not isinstance(case, dict):
        raise ValueError("真实回放场景必须是 JSON 对象。")
    case_id = str(case.get("id") or "").strip()
    if not case_id or case_id in ids:
        raise ValueError(f"真实回放场景 id 缺失或重复：{case_id or '-'}。")
    ids.add(case_id)
    for field in ("entity_id", "title", "requirement_text", "user_instruction", "comments_policy"):
        if not str(case.get(field) or "").strip():
            raise ValueError(f"{case_id} 缺少 {field}。")
    for field in REQUIRED_CASE_LIST_FIELDS:
        value = case.get(field)
        if not isinstance(value, list) or not value or not all(str(item).strip() for item in value):
            raise ValueError(f"{case_id} 的 {field} 必须是非空字符串数组。")
    if not isinstance(case.get("technical_evidence"), dict):
        raise ValueError(f"{case_id} 缺少 technical_evidence。")
    expected = case.get("expected")
    if not isinstance(expected, dict) or not isinstance(expected.get("ownership"), dict):
        raise ValueError(f"{case_id} 缺少 expected.ownership。")
    if set(expected["ownership"]) != {"frontend", "backend", "database", "configuration"}:
        raise ValueError(f"{case_id} 必须声明完整四层 expected.ownership。")
    negative = case.get("negative")
    if not isinstance(negative, dict) or not negative.get("kind") or negative.get("expected_status") != "blocked":
        raise ValueError(f"{case_id} 必须声明会阻断的可执行负例。")


def run_replay_suite(
    manifest: str | Path | dict[str, Any],
    *,
    manifest_base: str | Path | None = None,
) -> dict[str, Any]:
    if isinstance(manifest, (str, Path)):
        manifest_path = Path(manifest).expanduser().resolve()
        payload = load_replay_manifest(manifest_path)
        base = manifest_path.parent
    else:
        payload = copy.deepcopy(manifest)
        validate_replay_manifest(payload)
        base = Path(manifest_base or ".").expanduser().resolve()

    case_results = [run_replay_case(case, manifest_base=base) for case in payload["cases"]]
    passed = sum(case["status"] == "passed" for case in case_results)
    result: dict[str, Any] = {
        "schema_version": REPLAY_RESULT_VERSION,
        "suite_id": payload["suite_id"],
        "status": "passed" if passed == len(case_results) else "failed",
        "technical_valid": passed == len(case_results),
        "business_valid": False,
        "runtime_verified": False,
        "promotion_enabled": False,
        "external_calls": False,
        "business_repository_modified": False,
        "summary": {
            "total": len(case_results),
            "passed": passed,
            "failed": len(case_results) - passed,
            "unique_entities": len({case["entity_id"] for case in case_results}),
            "category_counts": dict(Counter(case["category"] for case in case_results)),
        },
        "cases": case_results,
        "boundaries": [
            "回放仅验证脱敏固定输入下的需求校准、变更归属和安全闸口。",
            "样本中的业务项目验证命令未执行，不代表业务运行时通过。",
            "未调用模型、网络、凭证、业务数据库、云效写入或 Git 远端。",
        ],
    }
    result["result_hash"] = stable_hash(result)
    return result


def run_replay_case(case: dict[str, Any], *, manifest_base: Path) -> dict[str, Any]:
    calibration = build_requirement_calibration(
        title=case["title"],
        demand_text=case["requirement_text"],
        user_instruction=case["user_instruction"],
    )
    decision = technical_decision_from_case(case)
    ownership = build_change_ownership_matrix(
        user_instruction=case["user_instruction"],
        requirement_text=case["requirement_text"],
        technical_decision=decision,
    )
    high_risk_detected = bool(
        find_high_risk_terms(
            title=case["title"],
            demand_text="\n".join([case["requirement_text"], case["user_instruction"]]),
        )
    )
    checks: list[dict[str, Any]] = []
    expected = case["expected"]
    add_check(checks, "ownership.status", expected["ownership_status"], ownership.status)
    for layer, expected_status in expected["ownership"].items():
        add_check(checks, f"ownership.{layer}", expected_status, ownership.row(layer).status)
    actual_parameters = [str(item.get("name") or "") for item in calibration.get("resolved_parameters") or []]
    for parameter_name in expected.get("parameter_names") or []:
        add_check(checks, f"parameter.{parameter_name}", True, parameter_name in actual_parameters)
    add_check(checks, "high_risk_detected", bool(expected.get("high_risk_blocked")), high_risk_detected)

    acceptance_status = "not_applicable"
    if case.get("acceptance_contract"):
        acceptance_path = (manifest_base / str(case["acceptance_contract"])).resolve()
        acceptance = execute_acceptance_contract(acceptance_path)
        acceptance_status = acceptance.status
        add_check(checks, "acceptance.status", expected.get("acceptance_status"), acceptance.status)

    negative = execute_negative_case(case, decision=decision, manifest_base=manifest_base)
    add_check(checks, "negative.status", case["negative"]["expected_status"], negative["actual_status"])
    status = "passed" if all(check["status"] == "passed" for check in checks) else "failed"
    return {
        "id": case["id"],
        "entity_id": case["entity_id"],
        "category": case["category"],
        "status": status,
        "negative_status": "passed" if negative["actual_status"] == case["negative"]["expected_status"] else "failed",
        "calibration_status": calibration.get("status"),
        "ownership_status": ownership.status,
        "acceptance_status": acceptance_status,
        "high_risk_detected": high_risk_detected,
        "checks": checks,
        "negative": negative,
        "source_refs": list(case["source_refs"]),
        "allowed_paths": list(case["allowed_paths"]),
        "expected_diff_features": list(case["expected_diff_features"]),
        "verify_commands_recorded_not_executed": list(case["verify_commands"]),
        "manual_acceptance": list(case["manual_acceptance"]),
    }


def technical_decision_from_case(case: dict[str, Any]) -> dict[str, Any]:
    evidence = case["technical_evidence"]
    frontend_paths = [str(path) for path in evidence.get("frontend_paths") or []]
    backend_paths = [str(path) for path in evidence.get("backend_paths") or []]
    selected_projects: list[dict[str, Any]] = []
    if frontend_paths:
        selected_projects.append({"path": "/replay/frontend", "name": "desensitized-frontend", "role": "frontend", "exists": True})
    if backend_paths:
        selected_projects.append({"path": "/replay/backend", "name": "desensitized-backend", "role": "backend", "exists": True})
    client_status = str(evidence.get("client_status") or "not_required")
    server_status = str(evidence.get("server_status") or "not_required")
    contract_required = bool(evidence.get("contract_required"))
    contract_verified = not contract_required or (
        client_status in {"verified", "not_required"} and server_status == "verified"
    )
    return {
        "selected_projects": selected_projects,
        "field_provenance": {
            "target_ui_found": bool(frontend_paths),
            "evidence": [
                {"project": "desensitized-frontend", "path": path, "reason": "固定回放前端源码证据"}
                for path in frontend_paths
            ],
        },
        "contract_verification": {
            "required": contract_required,
            "status": "verified" if contract_verified else "blocked",
            "layers": {
                "client_request": {
                    "status": client_status,
                    "evidence_paths": frontend_paths if client_status == "verified" else [],
                },
                "server_contract": {
                    "status": server_status,
                    "evidence_paths": backend_paths if server_status == "verified" else [],
                },
            },
        },
        "implementation_decision": {"can_patch": True, "blockers": []},
        "recommended_allowed_paths": list(case["allowed_paths"]),
        "recommended_verify_commands": list(case["verify_commands"]),
    }


def execute_negative_case(
    case: dict[str, Any],
    *,
    decision: dict[str, Any],
    manifest_base: Path,
) -> dict[str, Any]:
    kind = str(case["negative"]["kind"])
    if kind == "remove_frontend_evidence":
        negative_decision = copy.deepcopy(decision)
        negative_decision["selected_projects"] = [
            item for item in negative_decision["selected_projects"] if item.get("role") != "frontend"
        ]
        negative_decision["field_provenance"] = {"target_ui_found": False, "evidence": []}
        matrix = build_change_ownership_matrix(
            user_instruction=case["user_instruction"],
            requirement_text=case["requirement_text"],
            technical_decision=negative_decision,
        )
        return {"kind": kind, "actual_status": matrix.status, "blockers": list(matrix.blockers)}

    if kind in {"remove_server_evidence", "remove_server_evidence_and_confirmation"}:
        negative_decision = copy.deepcopy(decision)
        negative_decision["selected_projects"] = [
            item for item in negative_decision["selected_projects"] if item.get("role") != "backend"
        ]
        contract = negative_decision["contract_verification"]
        contract["status"] = "blocked"
        contract["layers"]["server_contract"] = {"status": "missing", "evidence_paths": []}
        instruction = case["user_instruction"]
        if kind == "remove_server_evidence_and_confirmation":
            instruction = "前端按需求调整；需求正文中的服务端完成描述只作为未核验线索。"
        matrix = build_change_ownership_matrix(
            user_instruction=instruction,
            requirement_text=case["requirement_text"],
            technical_decision=negative_decision,
        )
        return {"kind": kind, "actual_status": matrix.status, "blockers": list(matrix.blockers)}

    if kind == "damage_ordering_contract":
        source_path = (manifest_base / str(case["acceptance_contract"])).resolve()
        damaged = json.loads(source_path.read_text(encoding="utf-8"))
        damaged["required_checks"] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "damaged-ordering.json"
            path.write_text(json.dumps(damaged, ensure_ascii=False, indent=2), encoding="utf-8")
            result = execute_acceptance_contract(path)
        return {"kind": kind, "actual_status": result.status, "blockers": list(result.blockers)}

    if kind == "attempt_high_risk_auto_patch":
        ownership = build_change_ownership_matrix(
            user_instruction=case["user_instruction"],
            requirement_text=case["requirement_text"],
            technical_decision=decision,
        )
        contract = build_requirement_contract(
            title=case["title"],
            demand_text=case["requirement_text"],
            requirement_calibration={
                "status": "ready_for_development",
                "decision": {"can_enter_development": True},
                "source_priority": [],
                "resolved_parameters": [],
                "warnings": [],
            },
            technical_decision=decision,
            acceptance_matrix={"items": [{"kind": "automatic", "statement": "伪造自动验收"}]},
            apply_to_project=True,
            change_ownership_matrix=ownership.to_dict(),
        )
        high_risk_blocker = any("高风险" in blocker for blocker in contract.blockers)
        return {
            "kind": kind,
            "actual_status": "blocked" if contract.status == "blocked" and high_risk_blocker else contract.status,
            "blockers": list(contract.blockers),
        }
    raise ValueError(f"不支持的真实回放负例：{kind}")


def add_check(checks: list[dict[str, Any]], name: str, expected: Any, actual: Any) -> None:
    checks.append(
        {
            "name": name,
            "status": "passed" if expected == actual else "failed",
            "expected": expected,
            "actual": actual,
        }
    )


def stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def replay_result_to_markdown(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        "# HIS Harness 脱敏真实需求回放报告",
        "",
        f"- 状态：{result['status']}",
        f"- 技术回放有效：{'是' if result['technical_valid'] else '否'}",
        "- 业务有效：否",
        "- 运行时已验证：否",
        "- 外部调用：否",
        f"- 场景：{summary['passed']}/{summary['total']} 通过，来自 {summary['unique_entities']} 个真实需求编号",
        f"- 结果哈希：`{result['result_hash']}`",
        "",
        "| 场景 | 分类 | 结果 | 负例 | 高风险 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for case in result["cases"]:
        lines.append(
            f"| {case['id']} | {case['category']} | {case['status']} | "
            f"{case['negative_status']} | {'是' if case['high_risk_detected'] else '否'} |"
        )
    lines.extend(["", "## 边界", ""])
    lines.extend(f"- {boundary}" for boundary in result["boundaries"])
    return "\n".join(lines)
