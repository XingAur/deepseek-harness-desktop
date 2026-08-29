from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping


STAGE_ORDER = (
    "intake",
    "discovery",
    "contract",
    "local_change",
    "verification",
    "review",
    "learning",
)
STAGE_STATUSES = {"completed", "blocked", "failed", "skipped"}


@dataclass(frozen=True)
class DemandCaseStage:
    status: str
    evidence_refs: tuple[str, ...] = ()
    failure_code: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "evidence_refs": list(self.evidence_refs),
            "failure_code": self.failure_code,
        }


@dataclass(frozen=True)
class DemandCase:
    demand_text: str
    stages: Mapping[str, DemandCaseStage] = field(default_factory=dict)

    @classmethod
    def create(cls, demand_text: str) -> "DemandCase":
        normalized = str(demand_text or "").strip()
        if not normalized:
            raise ValueError("demand text is required")
        return cls(demand_text=normalized)

    @property
    def current_stage(self) -> str:
        completed = [stage for stage in STAGE_ORDER if stage in self.stages]
        return completed[-1] if completed else ""

    def with_stage_result(
        self,
        stage: str,
        status: str,
        evidence_refs: tuple[str, ...],
        failure_code: str,
    ) -> "DemandCase":
        updated = dict(self.stages)
        updated[stage] = DemandCaseStage(
            status=status,
            evidence_refs=evidence_refs,
            failure_code=failure_code,
        )
        return replace(self, stages=updated)

    def to_dict(self) -> dict:
        return {
            "schema_version": "demand-case.v1",
            "demand_text": self.demand_text,
            "current_stage": self.current_stage,
            "stages": {
                stage: item.to_dict()
                for stage, item in self.stages.items()
            },
        }


@dataclass(frozen=True)
class DemandCaseResult:
    case: DemandCase
    mutation_allowed: bool


def advance_demand_case(
    case: DemandCase,
    stage: str,
    status: str,
    *,
    evidence_refs: list[str] | tuple[str, ...] = (),
    failure_code: str = "",
) -> DemandCase:
    if stage not in STAGE_ORDER or status not in STAGE_STATUSES:
        raise ValueError("invalid DemandCase stage result")
    if stage in case.stages:
        raise ValueError("cannot advance DemandCase stage twice")
    stage_index = STAGE_ORDER.index(stage)
    if stage_index:
        previous = STAGE_ORDER[stage_index - 1]
        previous_result = case.stages.get(previous)
        if previous_result is None or previous_result.status != "completed":
            raise ValueError("cannot advance DemandCase past an unfinished stage")
    normalized_refs = tuple(
        str(item).strip() for item in evidence_refs if str(item).strip()
    )
    return case.with_stage_result(
        stage,
        status,
        normalized_refs,
        str(failure_code or "").strip(),
    )
