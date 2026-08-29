"""Governed integration for Flux-OPD-Lite experience in local-agent runs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Mapping

from app.flux_lite_learning import (
    ExperienceCandidate,
    ReviewerOpinion,
    aggregate_opinions,
    select_learning_checks,
)
from app.flux_lite_repository import FluxLiteLearningRepository
from app.local_agent_contract import LocalAgentTask
from app.local_agent_repository import LocalAgentRunRepository
from app.repair_learning import (
    LearningRuleState,
    MatchedLearningRule,
    RetrospectiveSourceKind,
    RootCauseKind,
    build_current_task_rule,
    derive_task_learning_context,
    match_rules,
)


_AVAILABLE_CHECKS = ("verification_replay", "reviewer_focus", "path_coverage")


class FluxLiteExperienceService:
    """Turn independent structured opinions into inert, exact-scope checks."""

    def __init__(self, repository: LocalAgentRunRepository) -> None:
        if not isinstance(repository, LocalAgentRunRepository):
            raise TypeError("repository must be a LocalAgentRunRepository")
        self._run_repository = repository
        self._repository = FluxLiteLearningRepository(
            connection_factory=repository.open_learning_connection,
        )

    def record_reviewer_opinions(
        self,
        *,
        task: LocalAgentTask,
        run_id: int,
        attempt_id: int,
        opinions: Sequence[ReviewerOpinion],
    ) -> dict[str, object]:
        context = self._context(task, run_id=run_id, attempt_id=attempt_id)
        expected_scope = _scope_key(context.repository_kind, context.task_key)
        if any(opinion.scope_key != expected_scope for opinion in opinions):
            raise ValueError("flux_lite_scope_conflict")
        candidate = aggregate_opinions(
            opinions,
            high_risk=bool(context.high_risk_tags),
        )
        if candidate.scope_key != expected_scope:
            raise ValueError("flux_lite_scope_conflict")
        return self._repository.record_consensus(
            run_id=run_id,
            attempt_id=attempt_id,
            opinions=opinions,
            candidate=candidate,
        )

    def record_local_reviewer_opinion(
        self,
        *,
        task: LocalAgentTask,
        run_id: int,
        attempt_id: int,
        reviewer_id: str,
        verdict: str,
        review_hash: str,
    ) -> dict[str, object]:
        context = self._context(task, run_id=run_id, attempt_id=attempt_id)
        opinion = ReviewerOpinion(
            reviewer_id=reviewer_id,
            scope_key=_scope_key(context.repository_kind, context.task_key),
            root_cause="review_gap" if verdict == "changes_requested" else "verification_failure",
            focus_actions=("reviewer_focus",) if verdict == "changes_requested" else ("verification_replay",),
            verdict=verdict,
            evidence_refs=(f"sha256:{review_hash}",),
        )
        return self.record_reviewer_opinions(
            task=task,
            run_id=run_id,
            attempt_id=attempt_id,
            opinions=(opinion,),
        )

    def matched_checks_for_attempt(
        self,
        task: LocalAgentTask,
        *,
        run_id: int,
    ) -> tuple[MatchedLearningRule, ...]:
        context = self._context(task, run_id=run_id)
        records = self._repository.list_context_candidates(
            scope_key=_scope_key(context.repository_kind, context.task_key),
        )
        matched: list[MatchedLearningRule] = []
        for record in records:
            try:
                root_cause = RootCauseKind(str(record["root_cause"]))
                actions = select_learning_checks(
                    _candidate_from_record(record),
                    _AVAILABLE_CHECKS,
                )
                if not actions:
                    continue
                rule = build_current_task_rule(
                    context,
                    root_cause=root_cause,
                    actions=actions,
                    source_kind=RetrospectiveSourceKind.OFFLINE_IMPORT,
                    state=LearningRuleState.TRIAL,
                )
                matched.extend(match_rules(context, (rule,)))
            except (KeyError, TypeError, ValueError):
                # Invalid historical experience is ignored fail-closed.  It
                # remains inspectable in the append-only snapshot but cannot
                # reach either model prompt.
                continue
        return tuple(matched)

    def snapshot_for_attempt(self, *, run_id: int, attempt_id: int) -> dict[str, object]:
        return self._repository.snapshot_for_attempt(run_id=run_id, attempt_id=attempt_id)

    def _context(
        self,
        task: LocalAgentTask,
        *,
        run_id: int,
        attempt_id: int | None = None,
    ):
        binding = self._run_repository.read_learning_binding(
            task,
            run_id=run_id,
            attempt_id=attempt_id,
        )
        if int(binding["run_id"]) != run_id or (
            attempt_id is not None and int(binding["attempt_id"]) != attempt_id
        ):
            raise ValueError("flux_lite_input_invalid")
        return derive_task_learning_context(task, run_id=run_id)


def _scope_key(repository_kind: str, task_key: str) -> str:
    return f"{repository_kind}:{task_key}"


def _candidate_from_record(record: Mapping[str, object]) -> ExperienceCandidate:
    return ExperienceCandidate(
        candidate_id=str(record["candidate_id"]),
        scope_key=str(record["scope_key"]),
        root_cause=str(record["root_cause"]),
        focus_actions=tuple(str(item) for item in record["focus_actions"]),
        reviewer_count=int(record["reviewer_count"]),
        agreement_ratio=float(record["agreement_ratio"]),
        conflict_score=float(record["conflict_score"]),
        context_weight=float(record["context_weight"]),
        state=str(record["state"]),
        promotion_allowed=bool(record["promotion_allowed"]),
        high_risk=bool(record["high_risk"]),
    )
