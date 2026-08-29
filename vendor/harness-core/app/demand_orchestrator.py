from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
from typing import Any

from app.demand_case import DemandCase, DemandCaseResult, advance_demand_case
from app.demand_discovery import DiscoveryResult


def build_governed_demand_case(
    *,
    demand_text: str,
    intake_record: Mapping[str, Any],
    discovery: DiscoveryResult,
    technical_decision: Mapping[str, Any],
    governance: object,
) -> DemandCaseResult:
    """Build one immutable case from intake through the mutation gate without I/O."""
    case = DemandCase.create(demand_text)
    source = _safe_source_ref(intake_record.get("source"))
    readonly_allowed = intake_record.get("readonly_discovery_allowed") is True
    intake_mutation_allowed = intake_record.get("mutation_allowed") is True
    intake_status = str(intake_record.get("intake_status") or "")
    if not readonly_allowed or intake_status not in {
        "accepted",
        "accepted_for_readonly_discovery",
    }:
        case = advance_demand_case(
            case,
            "intake",
            "blocked",
            evidence_refs=[source],
            failure_code="intake_not_authorized_for_readonly_discovery",
        )
        return DemandCaseResult(case=case, mutation_allowed=False)

    case = advance_demand_case(
        case,
        "intake",
        "completed",
        evidence_refs=[source, f"intake_status:{intake_status}"],
    )
    discovery_refs = [
        f"{node.project}:{node.path}"
        for node in discovery.graph.nodes[:30]
    ]
    case = advance_demand_case(
        case,
        "discovery",
        "completed",
        evidence_refs=discovery_refs or ["discovery:completed_without_source_match"],
    )

    governance_status = _governance_value(governance, "status")
    governance_can_modify = _governance_value(governance, "can_modify") is True
    technical_can_patch = technical_decision.get("can_patch") is True
    contract_refs = [
        f"governance:{governance_status or 'missing'}",
        f"technical_can_patch:{str(technical_can_patch).lower()}",
        f"intake_mutation_allowed:{str(intake_mutation_allowed).lower()}",
    ]
    if intake_mutation_allowed and governance_can_modify and technical_can_patch:
        case = advance_demand_case(
            case,
            "contract",
            "completed",
            evidence_refs=contract_refs,
        )
        return DemandCaseResult(case=case, mutation_allowed=True)

    failure_code = governance_status or (
        "technical_decision_blocks_patch" if not technical_can_patch else "intake_not_authorized_for_mutation"
    )
    case = advance_demand_case(
        case,
        "contract",
        "blocked",
        evidence_refs=contract_refs,
        failure_code=failure_code,
    )
    return DemandCaseResult(case=case, mutation_allowed=False)


def build_governed_demand_case_from_intake_file(
    *,
    demand_text: str,
    intake_path: str | Path,
    discovery: DiscoveryResult,
    technical_decision: Mapping[str, Any],
    governance: object,
) -> DemandCaseResult:
    path = Path(intake_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("intake_record_unavailable") from exc
    if not isinstance(payload, dict):
        raise ValueError("intake_record_invalid")
    return build_governed_demand_case(
        demand_text=demand_text,
        intake_record=payload,
        discovery=discovery,
        technical_decision=technical_decision,
        governance=governance,
    )


def write_demand_case_snapshot(
    *,
    run_dir: str | Path,
    result: DemandCaseResult,
) -> Path:
    root = Path(run_dir)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("demand_case_run_dir_invalid")
    path = root / "demand_case.json"
    payload = {
        "schema_version": "demand-case-snapshot.v1",
        "mutation_allowed": result.mutation_allowed,
        "case": result.case.to_dict(),
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path


def _governance_value(governance: object, name: str) -> Any:
    if isinstance(governance, Mapping):
        return governance.get(name)
    return getattr(governance, name, None)


def _safe_source_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "intake:unknown_source"
    if "?" in text or "@" in text:
        return "intake:work_item"
    return f"intake:{text}"
