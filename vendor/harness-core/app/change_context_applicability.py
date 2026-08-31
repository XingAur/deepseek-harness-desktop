from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Mapping, Sequence

from app.task_context import TaskIntentContext


ALWAYS_REQUIRED = "CTX-BASE-001"
DATA_BACKEND_PERSISTENCE = "CTX-DATA-001"
DATA_API_PERSISTED_FIELD = "CTX-DATA-002"
DATA_MODEL_MAPPING = "CTX-DATA-003"
DATA_SQL_OR_SCHEMA = "CTX-DATA-004"
DATA_DATABASE_CONFIGURATION = "CTX-DATA-005"
DATA_FRONTEND_SAVE_PATH = "CTX-DATA-006"
DATA_MULTI_REPOSITORY = "CTX-DATA-007"
DATA_ALWAYS_TARGET = "CTX-DATA-008"
DATA_CONSERVATIVE_UNKNOWN = "CTX-DATA-009"
DATA_NOT_APPLICABLE_DOC = "CTX-DATA-NA-001"
DATA_NOT_APPLICABLE_COPY = "CTX-DATA-NA-002"
DATA_NOT_APPLICABLE_STYLE = "CTX-DATA-NA-003"

_LAYER_ORDER = ("project_graph", "change_scope", "code_graph", "data_graph")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_EVIDENCE_REF = re.compile(r"evidence://[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,511}\Z")
_SAFE_KINDS = {
    "documentation": DATA_NOT_APPLICABLE_DOC,
    "copy_only": DATA_NOT_APPLICABLE_COPY,
    "style_only": DATA_NOT_APPLICABLE_STYLE,
}
_REQUIRED_KINDS = {
    "frontend_api_field": DATA_API_PERSISTED_FIELD,
    "frontend_save_path": DATA_FRONTEND_SAVE_PATH,
    "controller": DATA_API_PERSISTED_FIELD,
    "service": DATA_BACKEND_PERSISTENCE,
    "repository": DATA_ALWAYS_TARGET,
    "dao": DATA_ALWAYS_TARGET,
    "mapper": DATA_ALWAYS_TARGET,
    "entity": DATA_ALWAYS_TARGET,
    "dto": DATA_MODEL_MAPPING,
    "sql": DATA_SQL_OR_SCHEMA,
    "migration": DATA_SQL_OR_SCHEMA,
    "datasource": DATA_DATABASE_CONFIGURATION,
    "orm_mapping": DATA_MODEL_MAPPING,
    "schema_configuration": DATA_DATABASE_CONFIGURATION,
}
_RELATIONSHIP_RULES = {
    "api_field": DATA_API_PERSISTED_FIELD,
    "backend_contract": DATA_API_PERSISTED_FIELD,
    "persisted_field": DATA_API_PERSISTED_FIELD,
    "state_transition": DATA_API_PERSISTED_FIELD,
    "persistence_call": DATA_BACKEND_PERSISTENCE,
    "data_load": DATA_BACKEND_PERSISTENCE,
    "orm_mapping": DATA_MODEL_MAPPING,
    "sql": DATA_SQL_OR_SCHEMA,
    "schema": DATA_SQL_OR_SCHEMA,
    "database_configuration": DATA_DATABASE_CONFIGURATION,
    "configuration_key": DATA_DATABASE_CONFIGURATION,
    "frontend_save": DATA_FRONTEND_SAVE_PATH,
}
_HIGH_RISK_WORDS = ("医保", "收费", "退费", "结算", "清算", "对账", "金额")


@dataclass(frozen=True)
class CandidateTarget:
    repository_alias: str
    relative_path: str
    target_kind: str
    evidence_refs: tuple[str, ...]
    relationships: tuple[str, ...]


@dataclass(frozen=True)
class LayerApplicabilityDecision:
    layer_type: str
    requirement: str
    rule_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ApplicabilityAssessment:
    status: str
    decisions: tuple[LayerApplicabilityDecision, ...]
    blockers: tuple[str, ...]
    risk_tags: tuple[str, ...]
    task_context_hash: str

    def decision(self, layer_type: str) -> LayerApplicabilityDecision:
        for item in self.decisions:
            if item.layer_type == layer_type:
                return item
        raise KeyError(layer_type)


class ContextApplicabilityGate:
    """Pure deterministic policy; it performs no I/O and grants no mutation authority."""

    def assess(
        self,
        *,
        task_context: TaskIntentContext,
        candidate_targets: Sequence[CandidateTarget],
        change_ownership: Mapping[str, object] | None = None,
        project_routing: Mapping[str, object] | None = None,
        model_hint: Mapping[str, object] | None = None,
    ) -> ApplicabilityAssessment:
        del model_hint
        if not isinstance(task_context, TaskIntentContext):
            raise ValueError("change_context_task_intent_invalid")
        targets = tuple(candidate_targets)
        for target in targets:
            _validate_target(target)

        blockers = [f"task_context_missing:{name}" for name in task_context.missing_fields]
        if project_routing is not None and project_routing.get("status") not in {None, "ready", "complete"}:
            blockers.append("project_routing_incomplete")

        evidence_refs = _unique(ref for target in targets for ref in target.evidence_refs)
        base_decisions = [
            LayerApplicabilityDecision(
                layer_type=layer_type,
                requirement="required",
                rule_ids=(ALWAYS_REQUIRED,),
                evidence_refs=evidence_refs,
                reasons=("mutation_capable_run_requires_context",),
            )
            for layer_type in _LAYER_ORDER[:3]
        ]
        data_decision = self._data_decision(targets, change_ownership, evidence_refs)
        risk_tags = ("his_high_risk",) if any(word in task_context.goal for word in _HIGH_RISK_WORDS) else ()
        return ApplicabilityAssessment(
            status="blocked" if blockers else "ready",
            decisions=tuple((*base_decisions, data_decision)),
            blockers=tuple(blockers),
            risk_tags=risk_tags,
            task_context_hash=task_context.content_hash,
        )

    def _data_decision(
        self,
        targets: tuple[CandidateTarget, ...],
        change_ownership: Mapping[str, object] | None,
        evidence_refs: tuple[str, ...],
    ) -> LayerApplicabilityDecision:
        required_rules: list[str] = []
        safe_rules: list[str] = []
        reasons: list[str] = []
        for target in targets:
            relationship_rules = [_RELATIONSHIP_RULES.get(item, DATA_CONSERVATIVE_UNKNOWN) for item in target.relationships]
            if relationship_rules:
                required_rules.extend(relationship_rules)
                reasons.append(f"relationship:{target.repository_alias}:{target.relative_path}")
                continue
            if target.target_kind in _REQUIRED_KINDS:
                required_rules.append(_REQUIRED_KINDS[target.target_kind])
                reasons.append(f"target:{target.repository_alias}:{target.relative_path}")
            elif target.target_kind in _SAFE_KINDS:
                safe_rules.append(_SAFE_KINDS[target.target_kind])
            else:
                required_rules.append(DATA_CONSERVATIVE_UNKNOWN)
                reasons.append(f"unclassified:{target.repository_alias}:{target.relative_path}")

        if not targets:
            required_rules.append(DATA_CONSERVATIVE_UNKNOWN)
            reasons.append("candidate_targets_missing")
        if _ownership_requires_data(change_ownership):
            required_rules.append(DATA_BACKEND_PERSISTENCE)
            reasons.append("change_ownership_requires_database_context")
        repositories = {target.repository_alias for target in targets}
        if len(repositories) > 1 and required_rules:
            required_rules.append(DATA_MULTI_REPOSITORY)
        if required_rules:
            return LayerApplicabilityDecision(
                "data_graph",
                "required",
                _unique(required_rules),
                evidence_refs,
                _unique(reasons),
            )
        return LayerApplicabilityDecision(
            "data_graph",
            "not_applicable",
            _unique(safe_rules),
            evidence_refs,
            ("all_targets_proven_non_data",),
        )


def _validate_target(target: object) -> None:
    if not isinstance(target, CandidateTarget):
        raise ValueError("change_context_candidate_invalid")
    if not _NAME.fullmatch(target.repository_alias) or not _NAME.fullmatch(target.target_kind):
        raise ValueError("change_context_candidate_identity_invalid")
    if not _safe_relative_path(target.relative_path):
        raise ValueError("change_context_candidate_path_invalid")
    if not target.evidence_refs or len(set(target.evidence_refs)) != len(target.evidence_refs):
        raise ValueError("change_context_candidate_evidence_invalid")
    if any(not _EVIDENCE_REF.fullmatch(ref) or ".." in ref for ref in target.evidence_refs):
        raise ValueError("change_context_candidate_evidence_invalid")
    if len(set(target.relationships)) != len(target.relationships) or any(not _NAME.fullmatch(item) for item in target.relationships):
        raise ValueError("change_context_candidate_relationship_invalid")


def _safe_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value or value != value.strip() or value.startswith(("/", "~", "./")) or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and "." not in path.parts and path.as_posix() == value


def _ownership_requires_data(value: Mapping[str, object] | None) -> bool:
    if value is None:
        return False
    rows = value.get("rows")
    if not isinstance(rows, (list, tuple)):
        return False
    for row in rows:
        if isinstance(row, Mapping) and row.get("layer") in {"backend", "database"} and row.get("status") in {"required", "unresolved"}:
            return True
    return False


def _unique(values: Sequence[str] | object) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:  # type: ignore[union-attr]
        if value not in result:
            result.append(value)
    return tuple(result)
