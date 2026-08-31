from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Protocol

from app.change_context_contracts import (
    ChangeContextPack,
    ChangeContextProjection,
    PROJECTION_ROLES,
    canonical_json_bytes,
    content_hash,
)


ROLES = PROJECTION_ROLES
TIER0_MAX_BYTES = 2_048
TIER1_MAX_BYTES = 12_288


class ChangeContextProjectionError(ValueError):
    pass


class ProjectionMetricRepository(Protocol):
    def record_projection_metric(self, **kwargs: object) -> None: ...


def canonical_projection_bytes(value: Mapping[str, object]) -> bytes:
    return canonical_json_bytes(value)


def enforce_projection_budget(value: Mapping[str, object], *, maximum: int) -> None:
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
        raise ValueError("change_context_projection_budget_invalid")
    if len(canonical_projection_bytes(value)) > maximum:
        raise ChangeContextProjectionError("BLOCKED_CONTEXT_PROJECTION_BUDGET")


class ChangeContextProjectionService:
    """Render explicit role projections; broad evidence never enters prompts by default."""

    def __init__(self, *, repository: ProjectionMetricRepository | None = None) -> None:
        self.repository = repository

    def render(
        self,
        *,
        pack: ChangeContextPack,
        layer_payloads: Mapping[str, Mapping[str, object]],
        role: str,
        opened_evidence_refs: Sequence[str] = (),
        reused_layer_count: int = 0,
        recollected_layer_count: int = 0,
        reported_model_tokens: int = 0,
    ) -> ChangeContextProjection:
        if not isinstance(pack, ChangeContextPack) or role not in ROLES:
            raise ChangeContextProjectionError("BLOCKED_CONTEXT_VERSION_MISMATCH")
        if pack.status != "ready" or pack.gate.status != "ready" or pack.gate.code != "CHANGE_CONTEXT_READY":
            raise ChangeContextProjectionError("BLOCKED_CONTEXT_INCOMPLETE")
        payloads = self._verified_payloads(pack, layer_payloads)
        tier0 = self._tier0(pack)
        tier1 = self._tier1(pack, payloads, role)
        enforce_projection_budget(tier0, maximum=TIER0_MAX_BYTES)
        enforce_projection_budget(tier1, maximum=TIER1_MAX_BYTES)
        projection = ChangeContextProjection.create(
            pack_id=pack.pack_id,
            role=role,
            tier0=tier0,
            tier1=tier1,
            opened_evidence_refs=tuple(opened_evidence_refs),
        )
        if self.repository is not None:
            raw_bytes = len(canonical_json_bytes(pack.to_dict())) + sum(
                len(canonical_json_bytes(payload)) for payload in payloads.values()
            )
            projected_bytes = len(canonical_json_bytes(projection.to_dict()))
            self.repository.record_projection_metric(
                pack_id=pack.pack_id,
                role=role,
                projection_hash=projection.projection_hash,
                raw_bytes=raw_bytes,
                projected_bytes=projected_bytes,
                reused_layer_count=_metric_number(reused_layer_count),
                recollected_layer_count=_metric_number(recollected_layer_count),
                evidence_refs_opened=len(tuple(opened_evidence_refs)),
                reported_model_tokens=_metric_number(reported_model_tokens),
            )
        return projection

    @staticmethod
    def _verified_payloads(
        pack: ChangeContextPack,
        layer_payloads: Mapping[str, Mapping[str, object]],
    ) -> dict[str, Mapping[str, object]]:
        if set(layer_payloads) != {item.layer_type for item in pack.layers}:
            raise ChangeContextProjectionError("BLOCKED_CONTEXT_INCOMPLETE")
        verified: dict[str, Mapping[str, object]] = {}
        for layer in pack.layers:
            payload = layer_payloads.get(layer.layer_type)
            if not isinstance(payload, Mapping) or content_hash(payload) != layer.content_hash:
                raise ChangeContextProjectionError("BLOCKED_CONTEXT_HASH_MISMATCH")
            verified[layer.layer_type] = payload
        return verified

    @staticmethod
    def _tier0(pack: ChangeContextPack) -> dict[str, object]:
        return {
            "pack_id": pack.pack_id,
            "gate_status": pack.gate.status,
            "gate_code": pack.gate.code,
            "required_layers": list(pack.required_layers),
            "missing": list(pack.gate.missing),
            "conflicts": list(pack.gate.conflicts),
            "version_delta": [f"version={pack.pack_version}", f"supersedes={pack.supersedes_pack_id or '-'}"],
            "risk": [],
            "gate": "ready",
        }

    def _tier1(
        self,
        pack: ChangeContextPack,
        payloads: Mapping[str, Mapping[str, object]],
        role: str,
    ) -> dict[str, object]:
        project = payloads["project_graph"]
        scope = payloads["change_scope"]
        code = payloads["code_graph"]
        data = payloads["data_graph"]
        evidence_refs = list(dict.fromkeys(ref for layer in pack.layers for ref in layer.evidence_refs))
        values: dict[str, object] = {
            "project_relationships": _project_relationships(project),
            "requirement_scope": _requirement_scope(scope),
            "entry_points": _string_list(code.get("target_paths")),
            "allowed_paths": _string_list(code.get("target_paths")),
            "call_chain": _call_chain(code),
            "data_contracts": _data_contracts(data),
            "boundaries": _boundaries(project, scope),
            "tests": _string_list(code.get("tests")),
            "change_contract": _change_contract(scope, code),
            "diff_evidence_refs": [],
            "verification_evidence_refs": [],
            "knowledge_summary": _knowledge_summary(project, scope, code, data),
            "evidence_refs": evidence_refs,
        }
        fields = {
            "manager": (
                "project_relationships", "requirement_scope", "boundaries", "tests", "evidence_refs",
            ),
            "analysis": (
                "project_relationships", "requirement_scope", "entry_points", "allowed_paths",
                "call_chain", "data_contracts", "boundaries", "tests", "evidence_refs",
            ),
            "implementation": (
                "allowed_paths", "call_chain", "data_contracts", "tests", "change_contract", "evidence_refs",
            ),
            "review": (
                "allowed_paths", "call_chain", "data_contracts", "tests", "diff_evidence_refs",
                "verification_evidence_refs", "evidence_refs",
            ),
            "knowledge_answer": ("knowledge_summary", "boundaries", "evidence_refs"),
        }[role]
        return {name: values[name] for name in fields}


def _metric_number(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("change_context_projection_metric_invalid")
    return value


def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return list(dict.fromkeys(str(item)[:1024] for item in value if isinstance(item, str) and item))


def _project_relationships(payload: Mapping[str, object]) -> list[str]:
    result: list[str] = []
    for item in _mapping_list(payload.get("relationships")):
        result.append(
            f"{item.get('source', '-')}>{item.get('target', '-')}:{item.get('kind', '-')}:{item.get('endpoint', '-')}"[:1024]
        )
    if not result:
        for item in _mapping_list(payload.get("projects")):
            result.append(f"{item.get('name', '-')}:{item.get('role', 'unknown')}"[:512])
    return result


def _requirement_scope(payload: Mapping[str, object]) -> list[str]:
    result = [
        f"provider={payload.get('provider', '-')}",
        f"ticket={payload.get('ticket_id', '-')}",
        f"revision={payload.get('requirement_revision', '-')}",
    ]
    calibrated = payload.get("calibrated_scope")
    if isinstance(calibrated, Mapping):
        for key in sorted(calibrated):
            result.append(f"{key}={_compact_value(calibrated[key])}"[:2048])
    correction = str(payload.get("current_user_correction") or "")
    if correction:
        result.append("correction=" + correction[:2048])
    return result


def _call_chain(payload: Mapping[str, object]) -> list[str]:
    return [
        f"{item.get('source_path', '-')}>{item.get('target_path', '-')}:{item.get('kind', '-')}:{item.get('identifier', '-')}"[:2048]
        for item in _mapping_list(payload.get("call_edges"))
    ]


def _data_contracts(payload: Mapping[str, object]) -> list[str]:
    result: list[str] = []
    schema = str(payload.get("schema") or "")
    for table in _mapping_list(payload.get("tables")):
        table_name = str(table.get("name") or "")
        columns = _mapping_list(table.get("columns"))
        column_text = ",".join(
            f"{item.get('name', '-')}:{item.get('type', '-')}:{'null' if item.get('nullable') else 'required'}"
            for item in columns
        )
        result.append(f"{schema}.{table_name}[{column_text}]"[:4096])
    if not result and payload.get("status") == "not_applicable":
        result.append("not_applicable")
    return result


def _boundaries(project: Mapping[str, object], scope: Mapping[str, object]) -> list[str]:
    result = [f"explicit_project_scope={bool(project.get('explicit_scope'))}"]
    calibrated = scope.get("calibrated_scope")
    if isinstance(calibrated, Mapping) and "do_not" in calibrated:
        result.append("do_not=" + _compact_value(calibrated["do_not"])[:2048])
    return result


def _change_contract(scope: Mapping[str, object], code: Mapping[str, object]) -> list[str]:
    return [
        "allowed_paths=" + ",".join(_string_list(code.get("target_paths"))),
        "tests=" + ",".join(_string_list(code.get("tests"))),
        "correction=" + str(scope.get("current_user_correction") or "")[:2048],
    ]


def _knowledge_summary(
    project: Mapping[str, object],
    scope: Mapping[str, object],
    code: Mapping[str, object],
    data: Mapping[str, object],
) -> list[str]:
    return [
        "projects=" + ",".join(str(item.get("name") or "") for item in _mapping_list(project.get("projects"))),
        "scope=" + ";".join(_requirement_scope(scope)),
        "paths=" + ",".join(_string_list(code.get("target_paths"))),
        "data=" + ";".join(_data_contracts(data)),
    ]


def _mapping_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _compact_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
