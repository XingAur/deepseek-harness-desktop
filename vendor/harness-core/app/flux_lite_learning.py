"""Pure, non-executable experience aggregation for Flux-OPD-Lite.

This module intentionally operates on structured reviewer opinions only.  It
does not call a model, read prompts, modify a worktree, persist data, or grant
any execution capability.  The resulting weight controls only bounded
Worker/Reviewer check injection in a later integration layer.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Iterable, Sequence


_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}", re.IGNORECASE)
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_VERDICTS = frozenset({"approved", "changes_requested", "blocked"})
_MIN_CONTEXT_WEIGHT = 0.2
_MAX_CONTEXT_WEIGHT = 1.0


@dataclass(frozen=True)
class ReviewerOpinion:
    """One independently produced, structured reviewer opinion."""

    reviewer_id: str
    scope_key: str
    root_cause: str
    focus_actions: tuple[str, ...]
    verdict: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _identifier(self.reviewer_id, "reviewer_id")
        _identifier(self.scope_key, "scope_key")
        _identifier(self.root_cause, "root_cause")
        if not isinstance(self.focus_actions, tuple) or not self.focus_actions:
            raise ValueError("flux_lite_focus_actions_invalid")
        for action in self.focus_actions:
            _identifier(action, "focus_action")
        if self.verdict not in _VERDICTS:
            raise ValueError("flux_lite_verdict_invalid")
        if not isinstance(self.evidence_refs, tuple) or not self.evidence_refs:
            raise ValueError("flux_lite_evidence_invalid")
        for reference in self.evidence_refs:
            if not isinstance(reference, str) or _SHA256.fullmatch(reference) is None:
                raise ValueError("flux_lite_evidence_invalid")

    @property
    def consensus_key(self) -> tuple[str, str, tuple[str, ...], str]:
        return self.scope_key, self.root_cause, self.focus_actions, self.verdict


@dataclass(frozen=True)
class ExperienceCandidate:
    """An aggregated candidate that is safe to pass to a later gate."""

    candidate_id: str
    scope_key: str
    root_cause: str
    focus_actions: tuple[str, ...]
    reviewer_count: int
    agreement_ratio: float
    conflict_score: float
    context_weight: float
    state: str
    promotion_allowed: bool
    high_risk: bool


def aggregate_opinions(
    opinions: Sequence[ReviewerOpinion],
    *,
    high_risk: bool = False,
    conflict_threshold: float = 0.0,
    minimum_weight: float = _MIN_CONTEXT_WEIGHT,
    maximum_weight: float = _MAX_CONTEXT_WEIGHT,
) -> ExperienceCandidate:
    """Aggregate same-scope opinions and derive a bounded context weight.

    The conflict score is ``1 - dominant_cluster_ratio``.  This is an
    orchestration-level analogue of the paper's disagreement signal; it is not
    a token probability or a KL gradient.
    """

    if not isinstance(opinions, Sequence) or not opinions:
        raise ValueError("flux_lite_opinions_invalid")
    if any(not isinstance(item, ReviewerOpinion) for item in opinions):
        raise ValueError("flux_lite_opinions_invalid")
    if len({item.reviewer_id for item in opinions}) != len(opinions):
        raise ValueError("flux_lite_duplicate_reviewer")
    if not isinstance(high_risk, bool):
        raise ValueError("flux_lite_risk_invalid")
    _weight(minimum_weight, "minimum_weight")
    _weight(maximum_weight, "maximum_weight")
    if minimum_weight > maximum_weight:
        raise ValueError("flux_lite_weight_bounds_invalid")
    if high_risk and minimum_weight > 0.25:
        raise ValueError("flux_lite_weight_bounds_invalid")
    if not isinstance(conflict_threshold, (int, float)) or not 0 <= conflict_threshold < 1:
        raise ValueError("flux_lite_conflict_threshold_invalid")

    scope_keys = {item.scope_key for item in opinions}
    if len(scope_keys) != 1:
        raise ValueError("flux_lite_scope_conflict")

    counts = Counter(item.consensus_key for item in opinions)
    dominant_key, dominant_count = sorted(
        counts.items(), key=lambda item: (-item[1], item[0])
    )[0]
    reviewer_count = len(opinions)
    agreement_ratio = dominant_count / reviewer_count
    conflict_score = 1.0 - agreement_ratio
    if conflict_score <= conflict_threshold:
        context_weight = maximum_weight
    else:
        remaining = (1.0 - conflict_score) / (1.0 - conflict_threshold)
        context_weight = minimum_weight + (maximum_weight - minimum_weight) * remaining
    if high_risk:
        context_weight = min(context_weight, 0.25)
    context_weight = max(minimum_weight, min(maximum_weight, context_weight))

    scope_key, root_cause, focus_actions, _verdict = dominant_key
    unanimous = conflict_score == 0.0
    promotion_allowed = reviewer_count >= 2 and unanimous and not high_risk
    state = "trial" if reviewer_count >= 2 and unanimous else "candidate"
    candidate_payload = {
        "scope_key": scope_key,
        "root_cause": root_cause,
        "focus_actions": list(focus_actions),
        "reviewer_count": reviewer_count,
        "agreement_ratio": agreement_ratio,
        "conflict_score": conflict_score,
        "context_weight": context_weight,
        "high_risk": high_risk,
    }
    candidate_id = "flux-learn-" + hashlib.sha256(
        json.dumps(candidate_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    return ExperienceCandidate(
        candidate_id=candidate_id,
        scope_key=scope_key,
        root_cause=root_cause,
        focus_actions=tuple(focus_actions),
        reviewer_count=reviewer_count,
        agreement_ratio=agreement_ratio,
        conflict_score=conflict_score,
        context_weight=context_weight,
        state=state,
        promotion_allowed=promotion_allowed,
        high_risk=high_risk,
    )


def select_learning_checks(
    candidate: ExperienceCandidate,
    available_checks: Iterable[str],
) -> tuple[str, ...]:
    """Return only consensus actions that already exist in the task contract."""

    if not isinstance(candidate, ExperienceCandidate):
        raise ValueError("flux_lite_candidate_invalid")
    available = tuple(available_checks)
    if any(not isinstance(item, str) or not item for item in available):
        raise ValueError("flux_lite_available_checks_invalid")
    allowed = set(candidate.focus_actions)
    return tuple(item for item in available if item in allowed)


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"flux_lite_{name}_invalid")
    return value


def _weight(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
        raise ValueError(f"flux_lite_{name}_invalid")
    return float(value)
