"""Bounded, structured repair retrospectives for the local-agent loop.

This service deliberately accepts only local, already-structured lifecycle
facts.  It does not invoke a model, read a prompt or patch, execute a command,
or write to any remote system.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from collections.abc import Mapping, Sequence

from app.local_agent_contract import LocalAgentTask
from app.local_agent_repository import LocalAgentRunRepository
from app.flux_lite_learning import ReviewerOpinion
from app.flux_lite_service import FluxLiteExperienceService
from app.repair_learning import (
    LearningRule,
    LearningRuleState,
    MatchedLearningRule,
    PromotionEvidence,
    RetrospectiveSourceKind,
    RootCauseKind,
    RuleObservationOutcome,
    TaskLearningContext,
    build_current_task_rule,
    derive_task_learning_context,
    match_rules,
    rule_key,
    validate_rule_payload,
)
from app.repair_learning_repository import RepairLearningRepository
from app.sensitive_text import (
    contains_sensitive_text,
    normalize_sensitive_text,
    redact_sensitive_text,
)


RETROSPECTIVE_ARTIFACT_SCHEMA_VERSION = "his-repair-retrospective.v1"
_MAX_SUMMARY_CHARS = 320
_UNTRUSTED_CONTENT = re.compile(
    r"(?:untrusted_task_data_json|diff\s+--git|\bprompt\b)",
    re.IGNORECASE,
)
_PATCH_CONTENT = re.compile(
    r"(?:"
    r"^[^\S\r\n]*---\s+[^\n]+\n[^\S\r\n]*\+\+\+\s+[^\n]+(?:\n|$)"
    r"|^[^\S\r\n]*@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@"
    r"|^[^\S\r\n]*\*\*\*\s+[^\n]+\n[^\S\r\n]*---\s+[^\n]+(?:\n|$)"
    r"|^[^\S\r\n]*Index:\s+[^\n]+(?:\n|$)"
    r"|^[^\S\r\n]*GIT binary patch(?:\n|$)"
    r"|^[^\S\r\n]*Binary files\s+.+\s+differ(?:\n|$)"
    r")",
    re.IGNORECASE | re.MULTILINE,
)
# These public token prefixes are deliberately checked here as well as by the
# generic sensitive-text boundary.  A repair retrospective is an audit
# artifact, so accepting an otherwise standalone token would be a leak even
# when it is not attached to a ``token=``-style key.
_STANDALONE_SECRET = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"gh[opurs]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|glpat-[A-Za-z0-9_-]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{20,}"
    r"|xapp-[A-Za-z0-9-]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|ASIA[0-9A-Z]{16}"
    r"|sk-[A-Za-z0-9_-]{8,}"
    r")(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_SOURCE_CODES = {
    RetrospectiveSourceKind.RUN_OBSERVATION: 1,
    RetrospectiveSourceKind.REVIEW_OBSERVATION: 2,
    RetrospectiveSourceKind.OFFLINE_IMPORT: 3,
}
_ROOT_CAUSE_CODES = {
    RootCauseKind.VERIFICATION_FAILURE: 1,
    RootCauseKind.REVIEW_GAP: 2,
    RootCauseKind.PATH_COVERAGE_GAP: 3,
    RootCauseKind.CONTRACT_MISMATCH: 4,
    RootCauseKind.IMPLEMENTATION_DEFECT: 5,
}


@dataclass(frozen=True)
class RepairLearningArtifact:
    """A structured artifact payload that the runner may persist later."""

    kind: str
    leaf: str
    payload: dict[str, object]
    content: bytes
    sha256: str


@dataclass(frozen=True)
class RepairLearningRecord:
    retrospective: dict[str, object]
    rule: LearningRule
    artifact: RepairLearningArtifact


class RepairLearningService:
    """Create and observe bounded rules through the run repository's DB only."""

    def __init__(self, repository: LocalAgentRunRepository) -> None:
        if not isinstance(repository, LocalAgentRunRepository):
            raise TypeError("repository must be a LocalAgentRunRepository")
        self._run_repository = repository
        self._learning_repository = RepairLearningRepository(
            connection_factory=repository.open_learning_connection,
        )
        self._flux_lite_service = FluxLiteExperienceService(repository)

    def record_reviewer_opinions(
        self,
        *,
        task: LocalAgentTask,
        run_id: int,
        attempt_id: int,
        opinions: Sequence[ReviewerOpinion],
    ) -> dict[str, object]:
        """Persist a bounded independent-review consensus for this attempt."""

        return self._flux_lite_service.record_reviewer_opinions(
            task=task,
            run_id=run_id,
            attempt_id=attempt_id,
            opinions=opinions,
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
        """Record one local reviewer fact without making it prompt-eligible."""

        return self._flux_lite_service.record_local_reviewer_opinion(
            task=task,
            run_id=run_id,
            attempt_id=attempt_id,
            reviewer_id=reviewer_id,
            verdict=verdict,
            review_hash=review_hash,
        )

    def matched_checks_for_attempt(
        self,
        task: LocalAgentTask,
        *,
        run_id: int,
    ) -> tuple[MatchedLearningRule, ...]:
        binding = self._run_repository.read_learning_binding(task, run_id=_positive_id(run_id))
        context = derive_task_learning_context(task, run_id=int(binding["run_id"]))
        legacy = tuple(item[1] for item in self._matched_records(context, int(binding["run_id"])))
        human_guards = self._matched_human_correction_guards(context)
        flux_lite = self._flux_lite_service.matched_checks_for_attempt(
            task,
            run_id=int(binding["run_id"]),
        )
        return _dedupe_matched_checks(legacy + human_guards + flux_lite)

    def record_verification_failure(
        self,
        *,
        task: LocalAgentTask,
        run_id: int,
        attempt_id: int,
        summary: str,
    ) -> RepairLearningRecord:
        return self._record_retrospective(
            task=task,
            run_id=run_id,
            attempt_id=attempt_id,
            source_kind=RetrospectiveSourceKind.RUN_OBSERVATION,
            root_cause=RootCauseKind.VERIFICATION_FAILURE,
            summary=summary,
            actions=("verification_replay",),
            allowed_run_statuses=frozenset({"verifying", "failed_verification"}),
        )

    def record_reviewer_changes_requested(
        self,
        *,
        task: LocalAgentTask,
        run_id: int,
        attempt_id: int,
        summary: str,
    ) -> RepairLearningRecord:
        return self._record_retrospective(
            task=task,
            run_id=run_id,
            attempt_id=attempt_id,
            source_kind=RetrospectiveSourceKind.REVIEW_OBSERVATION,
            root_cause=RootCauseKind.REVIEW_GAP,
            summary=summary,
            actions=("reviewer_focus",),
            allowed_run_statuses=frozenset({"reviewing", "changes_requested"}),
        )

    def record_human_correction(
        self,
        *,
        task: LocalAgentTask,
        run_id: int,
        attempt_id: int,
        root_cause_kind: RootCauseKind | str,
        summary: str,
    ) -> RepairLearningRecord:
        try:
            root_cause = RootCauseKind(root_cause_kind)
        except (TypeError, ValueError):
            raise ValueError("repair_learning_input_invalid") from None
        return self._record_retrospective(
            task=task,
            run_id=run_id,
            attempt_id=attempt_id,
            source_kind=RetrospectiveSourceKind.OFFLINE_IMPORT,
            root_cause=root_cause,
            summary=summary,
            actions=("replan_before_execute", "verification_replay", "reviewer_focus"),
            allowed_run_statuses=frozenset({"failed_verification", "changes_requested"}),
        )

    def record_awaiting_human_correction(
        self,
        *,
        task: LocalAgentTask,
        run_id: int,
        attempt_id: int,
        root_cause_kind: RootCauseKind | str,
        summary: str,
    ) -> RepairLearningRecord:
        """Record a correction and revoke the issued local-apply token together."""

        try:
            root_cause = RootCauseKind(root_cause_kind)
            normalized_run_id, normalized_attempt_id = _positive_id(run_id), _positive_id(attempt_id)
            safe_summary = _safe_summary(summary)
            source_key = _source_key(
                run_id=normalized_run_id,
                attempt_id=normalized_attempt_id,
                source_kind=RetrospectiveSourceKind.OFFLINE_IMPORT,
                root_cause=root_cause,
            )
        except (TypeError, ValueError):
            raise ValueError("repair_learning_input_invalid") from None
        try:
            stored = self._run_repository.record_awaiting_human_correction(
                task,
                source_key=source_key,
                run_id=normalized_run_id,
                attempt_id=normalized_attempt_id,
                root_cause_kind=root_cause.value,
                safe_summary=safe_summary,
            )
        except ValueError as error:
            _raise_learning_storage_error(error)
        try:
            retrospective = stored["retrospective"]
            rule = _learning_rule_from_payload(stored["rule"])
            if not isinstance(retrospective, Mapping):
                raise ValueError
            artifact = _artifact_for(retrospective=retrospective, rule=rule)
        except (KeyError, TypeError, ValueError):
            raise ValueError("repair_learning_input_invalid") from None
        return RepairLearningRecord(
            retrospective=dict(retrospective),
            rule=rule,
            artifact=artifact,
        )

    def record_successful_observation(
        self,
        *,
        task: LocalAgentTask,
        run_id: int,
        attempt_id: int,
        workspace_fingerprint: str | None = None,
    ) -> tuple[LearningRule, ...]:
        binding = self._learning_binding(
            task=task,
            run_id=run_id,
            attempt_id=attempt_id,
            allowed_run_statuses=frozenset({"awaiting_human_confirmation"}),
        )
        normalized_run_id, normalized_attempt_id = int(binding["run_id"]), int(binding["attempt_id"])
        context = derive_task_learning_context(task, run_id=normalized_run_id)
        workspace = self._durable_workspace_fingerprint(binding, workspace_fingerprint)
        advanced: list[LearningRule] = []
        for record, _matched in self._matched_records(context, normalized_run_id):
            self._run_repository.record_learning_observation(
                task,
                rule_id=int(record["id"]),
                run_id=normalized_run_id,
                attempt_id=normalized_attempt_id,
                task_key=context.task_key,
                workspace_fingerprint=workspace,
                outcome=RuleObservationOutcome.MATCHED,
                evidence={"event": "verification_and_review_passed"},
                allowed_run_statuses=frozenset({"awaiting_human_confirmation"}),
            )
            current = self._record_for_rule(
                run_id=normalized_run_id,
                rule_id=int(record["id"]),
            )
            state = str(current["state"])
            if state == LearningRuleState.ACTIVE_CURRENT_TASK.value:
                current = self._learning_repository.advance_rule_state(
                    rule_id=int(current["id"]),
                    expected_state_version=int(current["state_version"]),
                    new_state=LearningRuleState.TRIAL,
                )
            elif (
                state == LearningRuleState.TRIAL.value
                and not _is_high_risk_rule(current)
                and int(current["verified_task_count"]) >= 3
                and int(current["distinct_workspace_count"]) >= 2
                and int(current["counterexample_count"]) == 0
            ):
                current = self._learning_repository.advance_rule_state(
                    rule_id=int(current["id"]),
                    expected_state_version=int(current["state_version"]),
                    new_state=LearningRuleState.STABLE,
                )
            advanced.append(_learning_rule_from_payload(current["rule"]))
        return tuple(advanced)

    def record_approved_review_success_observation(
        self,
        *,
        task: LocalAgentTask,
        run_id: int,
        attempt_id: int,
        review_finalization_capability: object,
    ) -> object | None:
        """Stage fixed success evidence for atomic final-review persistence.

        The repository owns the returned opaque staging token.  It writes no
        observation until the exact associated ``finalize_review`` transaction
        succeeds, so review-finalization failures cannot leave evidence behind.
        """

        binding = self._learning_binding(
            task=task,
            run_id=run_id,
            attempt_id=attempt_id,
            allowed_run_statuses=frozenset({"reviewing"}),
        )
        normalized_run_id = int(binding["run_id"])
        del normalized_run_id
        return self._run_repository.stage_approved_review_learning_observation(
            review_finalization_capability,
            task=task,
        )

    def record_counterexample(
        self,
        *,
        task: LocalAgentTask,
        run_id: int,
        attempt_id: int,
        workspace_fingerprint: str | None = None,
        summary: str,
    ) -> tuple[LearningRule, ...]:
        binding = self._learning_binding(
            task=task,
            run_id=run_id,
            attempt_id=attempt_id,
            allowed_run_statuses=frozenset({"changes_requested", "awaiting_human_confirmation"}),
        )
        normalized_run_id, normalized_attempt_id = int(binding["run_id"]), int(binding["attempt_id"])
        context = derive_task_learning_context(task, run_id=normalized_run_id)
        workspace = self._durable_workspace_fingerprint(binding, workspace_fingerprint)
        safe_summary = _safe_summary(summary)
        suspended: list[LearningRule] = []
        for record, _matched in self._matched_records(context, normalized_run_id):
            self._run_repository.record_learning_observation(
                task,
                rule_id=int(record["id"]),
                run_id=normalized_run_id,
                attempt_id=normalized_attempt_id,
                task_key=context.task_key,
                workspace_fingerprint=workspace,
                outcome=RuleObservationOutcome.NOT_MATCHED,
                evidence={"event": "counterexample", **safe_summary},
                allowed_run_statuses=frozenset({"changes_requested", "awaiting_human_confirmation"}),
            )
            current = self._record_for_rule(
                run_id=normalized_run_id,
                rule_id=int(record["id"]),
            )
            if str(current["state"]) != LearningRuleState.SUSPENDED.value:
                raise ValueError("repair_learning_state_invalid")
            suspended.append(_learning_rule_from_payload(current["rule"]))
        return tuple(suspended)

    def snapshot_for_run(self, run_id: int) -> dict[str, object]:
        return self._learning_repository.snapshot_for_run(run_id=_positive_id(run_id))

    def _record_retrospective(
        self,
        *,
        task: LocalAgentTask,
        run_id: int,
        attempt_id: int,
        source_kind: RetrospectiveSourceKind,
        root_cause: RootCauseKind,
        summary: str,
        actions: tuple[str, ...],
        allowed_run_statuses: frozenset[str],
    ) -> RepairLearningRecord:
        normalized_run_id, normalized_attempt_id = _positive_id(run_id), _positive_id(attempt_id)
        context = _context_for_source(
            derive_task_learning_context(task, run_id=normalized_run_id),
            source_kind=source_kind,
            root_cause=root_cause,
        )
        safe_summary = _safe_summary(summary)
        source_key = _source_key(
            run_id=normalized_run_id,
            attempt_id=normalized_attempt_id,
            source_kind=source_kind,
            root_cause=root_cause,
        )
        rule = build_current_task_rule(
            context,
            root_cause=root_cause,
            actions=actions,
            source_kind=source_kind,
        )
        try:
            stored = self._run_repository.record_learning_retrospective_with_rule(
                task,
                source_key=source_key,
                run_id=normalized_run_id,
                attempt_id=normalized_attempt_id,
                source_kind=source_kind.value,
                root_cause_kind=root_cause.value,
                safe_summary=safe_summary,
                task_context=_context_payload(context),
                rule_payload=rule.to_payload(),
                allowed_run_statuses=allowed_run_statuses,
            )
        except ValueError as error:
            _raise_learning_storage_error(error)
        try:
            retrospective = stored["retrospective"]
            if not isinstance(retrospective, Mapping):
                raise ValueError
            stored_rule = _learning_rule_from_payload(stored["rule"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("repair_learning_storage_invalid") from None
        artifact = _artifact_for(
            retrospective=retrospective,
            rule=stored_rule,
        )
        return RepairLearningRecord(
            retrospective=retrospective,
            rule=stored_rule,
            artifact=artifact,
        )

    def _learning_binding(
        self,
        *,
        task: LocalAgentTask,
        run_id: int,
        attempt_id: int,
        allowed_run_statuses: frozenset[str],
    ) -> dict[str, object]:
        binding = self._run_repository.read_learning_binding(
            task,
            run_id=_positive_id(run_id),
            attempt_id=_positive_id(attempt_id),
        )
        if (
            binding.get("run_status") not in allowed_run_statuses
            or binding.get("attempt_status") != "completed"
        ):
            raise ValueError("repair_learning_input_invalid")
        return binding

    @staticmethod
    def _durable_workspace_fingerprint(
        binding: Mapping[str, object],
        supplied: str | None,
    ) -> str:
        workspace = _safe_workspace_fingerprint(binding.get("workspace_fingerprint"))
        if supplied is not None and _safe_workspace_fingerprint(supplied) != workspace:
            raise ValueError("repair_learning_input_invalid")
        return workspace

    def _matched_records(
        self,
        context: TaskLearningContext,
        run_id: int,
    ) -> tuple[tuple[dict[str, object], MatchedLearningRule], ...]:
        records = self._learning_repository.list_matchable_rules(run_id=run_id)
        matched: list[tuple[dict[str, object], MatchedLearningRule]] = []
        for record in records:
            candidate = _learning_rule_from_payload(record.get("rule"))
            result = match_rules(context, (candidate,))
            if result:
                matched.append((record, result[0]))
        return tuple(matched)

    def _matched_human_correction_guards(
        self,
        context: TaskLearningContext,
    ) -> tuple[MatchedLearningRule, ...]:
        """Turn explicit human corrections into immediate cross-run guards.

        Promoted rules intentionally need repeated success evidence.  A human
        correction is different: it is an authoritative no-repeat signal and
        must be applied on the next compatible task, before a worker starts.
        The persisted retrospective contains only normalized contract facts;
        no raw correction text is loaded into the model prompt.
        """

        matched: list[MatchedLearningRule] = []
        for retrospective in self._learning_repository.list_human_correction_retrospectives():
            try:
                task_context = retrospective["task_context"]
                if not isinstance(task_context, Mapping):
                    raise ValueError
                # Pre-fix rows may have been encoded by the generic redactor,
                # which intentionally hides fields such as path prefixes.
                # They remain auditable, but cannot safely become a new
                # cross-run guard because the matching scope is incomplete.
                if set(task_context) != {
                    "run_id", "task_key", "repository_kind", "allowed_path_prefixes",
                    "verification_command_fingerprints", "high_risk_tags", "failure_sources",
                }:
                    continue
                source_context = TaskLearningContext(
                    run_id=_positive_id(task_context["run_id"]),
                    task_key=_safe_text(task_context["task_key"], maximum=128),
                    repository_kind=_safe_text(task_context["repository_kind"], maximum=32),
                    allowed_path_prefixes=_text_tuple(task_context["allowed_path_prefixes"]),
                    verification_command_fingerprints=_text_tuple(
                        task_context["verification_command_fingerprints"],
                    ),
                    high_risk_tags=_text_tuple(task_context["high_risk_tags"]),
                    failure_sources=_text_tuple(task_context["failure_sources"]),
                )
                root_cause = RootCauseKind(retrospective["root_cause_kind"])
                rule = build_current_task_rule(
                    source_context,
                    root_cause=root_cause,
                    actions=("replan_before_execute", "reviewer_focus", "verification_replay"),
                    source_kind=RetrospectiveSourceKind.OFFLINE_IMPORT,
                    state=LearningRuleState.TRIAL,
                )
                matched.extend(match_rules(context, (rule,)))
            except (KeyError, TypeError, ValueError):
                raise ValueError("repair_learning_storage_invalid") from None
        return tuple(matched)

    def _record_for_rule(self, *, run_id: int, rule_id: int) -> dict[str, object]:
        snapshot = self._learning_repository.snapshot_for_run(run_id=run_id)
        for record in snapshot["rules"]:
            if isinstance(record, Mapping) and record.get("id") == rule_id:
                return dict(record)
        raise ValueError("repair_learning_storage_invalid")


def _context_for_source(
    context: TaskLearningContext,
    *,
    source_kind: RetrospectiveSourceKind,
    root_cause: RootCauseKind,
) -> TaskLearningContext:
    # Provenance belongs to the retrospective row and its source key.  It is
    # deliberately not a matching constraint: a verification-failure rule
    # must remain available for this run's retry before the later successful
    # observation is recorded.
    del source_kind, root_cause
    return TaskLearningContext(
        run_id=context.run_id,
        task_key=context.task_key,
        repository_kind=context.repository_kind,
        allowed_path_prefixes=context.allowed_path_prefixes,
        verification_command_fingerprints=context.verification_command_fingerprints,
        high_risk_tags=context.high_risk_tags,
        failure_sources=(),
    )


def _dedupe_matched_checks(
    checks: Sequence[MatchedLearningRule],
) -> tuple[MatchedLearningRule, ...]:
    seen: set[str] = set()
    result: list[MatchedLearningRule] = []
    for item in checks:
        if item.key in seen:
            continue
        seen.add(item.key)
        result.append(item)
    return tuple(result)


def _context_payload(context: TaskLearningContext) -> dict[str, object]:
    return {
        "run_id": context.run_id,
        "task_key": context.task_key,
        "repository_kind": context.repository_kind,
        "allowed_path_prefixes": list(context.allowed_path_prefixes),
        "verification_command_fingerprints": list(context.verification_command_fingerprints),
        "high_risk_tags": list(context.high_risk_tags),
        "failure_sources": list(context.failure_sources),
    }


def _learning_rule_from_payload(value: object) -> LearningRule:
    if not isinstance(value, Mapping):
        raise ValueError("repair_learning_storage_invalid")
    try:
        payload = validate_rule_payload(value)
        match = payload["match"]
        if not isinstance(match, Mapping):
            raise ValueError
        context = TaskLearningContext(
            run_id=_positive_id(match["run_id"]),
            task_key=_safe_text(match["task_key"], maximum=128),
            repository_kind=_safe_text(match["repository_kind"], maximum=32),
            allowed_path_prefixes=_text_tuple(match["allowed_path_prefixes"]),
            verification_command_fingerprints=_text_tuple(match["verification_command_fingerprints"]),
            high_risk_tags=_text_tuple(match["high_risk_tags"]),
            failure_sources=_text_tuple(match["failure_sources"]),
        )
        evidence_payload = payload["promotion_evidence"]
        evidence = None
        if evidence_payload is not None:
            if not isinstance(evidence_payload, Mapping):
                raise ValueError
            evidence = PromotionEvidence(
                task_keys=_text_tuple(evidence_payload["task_keys"]),
                workspace_fingerprints=_text_tuple(evidence_payload["workspace_fingerprints"]),
                counterexample_count=_nonnegative_int(evidence_payload["counterexample_count"]),
            )
        return LearningRule(
            key=rule_key(payload),
            state=LearningRuleState(str(payload["state"])),
            source_kind=RetrospectiveSourceKind(str(payload["source_kind"])),
            root_cause=RootCauseKind(str(payload["root_cause"])),
            actions=tuple(payload["actions"]),
            context=context,
            promotion_evidence=evidence,
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("repair_learning_storage_invalid") from None


def _artifact_for(
    *,
    retrospective: Mapping[str, object],
    rule: LearningRule,
) -> RepairLearningArtifact:
    payload = {
        "schema_version": RETROSPECTIVE_ARTIFACT_SCHEMA_VERSION,
        "source_key": retrospective["source_key"],
        "run_id": retrospective["run_id"],
        "attempt_id": retrospective["attempt_id"],
        "source_kind": retrospective["source_kind"],
        "root_cause_kind": retrospective["root_cause_kind"],
        "safe_summary": retrospective["safe_summary"],
        "task_context": retrospective["task_context"],
        "rule": rule.to_payload(),
        "rule_key": rule.key,
    }
    content = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return RepairLearningArtifact(
        kind="repair_retrospective",
        # Source keys are deterministic for one run/attempt/source/root-cause
        # tuple.  Keeping them in the leaf prevents a later human correction
        # from replacing the verification retrospective for that attempt.
        leaf=f"repair-retrospective-{retrospective['source_key']}.json",
        payload=payload,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _source_key(
    *,
    run_id: int,
    attempt_id: int,
    source_kind: RetrospectiveSourceKind,
    root_cause: RootCauseKind,
) -> str:
    # Run and attempt IDs are immutable local database identities.  The two
    # enum codes make one recorded event unique without storing free text or a
    # long opaque digest that the audit sanitizer would rightly reject.
    return (
        f"retro-r{run_id}-a{attempt_id}-s{_SOURCE_CODES[source_kind]}"
        f"-c{_ROOT_CAUSE_CODES[root_cause]}"
    )


def _safe_summary(value: object) -> dict[str, object]:
    if not isinstance(value, str) or len(value) > 12_000:
        raise ValueError("repair_learning_input_invalid")
    line_preserving = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized = " ".join(value.split())
    if not normalized:
        return {"summary_status": "empty"}
    secret_scan = normalize_sensitive_text(line_preserving)
    if (
        len(normalized) > _MAX_SUMMARY_CHARS
        or _UNTRUSTED_CONTENT.search(line_preserving)
        or _PATCH_CONTENT.search(line_preserving)
        or _STANDALONE_SECRET.search(line_preserving)
        or _STANDALONE_SECRET.search(secret_scan)
        or contains_sensitive_text(normalized)
        or redact_sensitive_text(normalized) != normalized
    ):
        return {
            "summary_status": "redacted",
            "summary_sha256": "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest(),
        }
    return {"summary_status": "safe", "summary": normalized}


def _is_high_risk_rule(record: Mapping[str, object]) -> bool:
    payload = record.get("rule")
    if not isinstance(payload, Mapping) or not isinstance(payload.get("match"), Mapping):
        raise ValueError("repair_learning_storage_invalid")
    tags = payload["match"].get("high_risk_tags")
    return isinstance(tags, list) and bool(tags)


def _safe_workspace_fingerprint(value: object) -> str:
    return _safe_text(value, maximum=128)


def _text_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("repair_learning_storage_invalid")
    return tuple(value)


def _safe_text(value: object, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or contains_sensitive_text(value)
    ):
        raise ValueError("repair_learning_storage_invalid")
    return value


def _raise_learning_storage_error(error: ValueError) -> None:
    """Translate only the repository's opaque storage boundary error."""

    if str(error) == "local_agent_storage_invalid":
        raise ValueError("repair_learning_storage_invalid") from None
    raise error


def _positive_id(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("repair_learning_input_invalid")
    return value


def _nonnegative_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("repair_learning_storage_invalid")
    return value
