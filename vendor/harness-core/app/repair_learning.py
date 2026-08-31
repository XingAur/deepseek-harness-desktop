"""Inert, deterministic repair-learning rules.

This module stores only bounded metadata.  It never persists a rule, executes
a command, or imports database, provider, or model integrations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Mapping, Sequence

from app.local_agent_contract import LocalAgentTask


RULE_SCHEMA_VERSION = "his-repair-learning-rule.v1"

_REPOSITORY_KINDS = frozenset(("python", "node", "gradle", "unknown"))
_ACTIONS = frozenset(
    (
        "verification_replay",
        "reviewer_focus",
        "path_coverage",
        # A human correction is a durable no-repeat signal.  It never grants
        # execution authority; it only forces Harness to re-inspect and issue
        # a new decision before the next worker attempt.
        "replan_before_execute",
    )
)
_HIGH_RISK_WORDS = {
    "billing": ("收费", "金额", "fee", "billing"),
    "refund": ("退费", "refund"),
    "settlement": ("结算", "settlement"),
    "insurance": ("医保", "yibao", "insurance"),
    "reconciliation": ("对账", "reconciliation"),
}
_SAFE_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}", re.IGNORECASE)
_SAFE_PATH = re.compile(r"[A-Za-z0-9._/-]{1,256}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SHELL_CONTROL = re.compile(r"[;&|`$<>\\\n\r]")
_SENSITIVE_FIELD = re.compile(
    r"(?:token|secret|password|credential|authorization|api[_-]?key|pat)", re.I
)


class RetrospectiveSourceKind(StrEnum):
    """Event provenance, deliberately separate from lifecycle state."""

    RUN_OBSERVATION = "run_observation"
    REVIEW_OBSERVATION = "review_observation"
    OFFLINE_IMPORT = "offline_import"


class RootCauseKind(StrEnum):
    VERIFICATION_FAILURE = "verification_failure"
    REVIEW_GAP = "review_gap"
    PATH_COVERAGE_GAP = "path_coverage_gap"
    CONTRACT_MISMATCH = "contract_mismatch"
    IMPLEMENTATION_DEFECT = "implementation_defect"


class LearningRuleState(StrEnum):
    DRAFT = "draft"
    ACTIVE_CURRENT_TASK = "active_current_task"
    TRIAL = "trial"
    STABLE = "stable"
    SUSPENDED = "suspended"
    RETIRED = "retired"


class RuleObservationOutcome(StrEnum):
    MATCHED = "matched"
    NOT_MATCHED = "not_matched"


@dataclass(frozen=True)
class PromotionEvidence:
    """Bounded, non-free-text evidence needed to promote a rule to stable."""

    task_keys: tuple[str, ...]
    workspace_fingerprints: tuple[str, ...]
    counterexample_count: int


@dataclass(frozen=True)
class TaskLearningContext:
    """Only comparable, contract-derived conditions that are safe to persist."""

    run_id: int
    task_key: str
    repository_kind: str
    allowed_path_prefixes: tuple[str, ...]
    verification_command_fingerprints: tuple[str, ...]
    high_risk_tags: tuple[str, ...]
    failure_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class LearningRule:
    key: str
    state: LearningRuleState
    source_kind: RetrospectiveSourceKind
    root_cause: RootCauseKind
    actions: tuple[str, ...]
    context: TaskLearningContext
    promotion_evidence: PromotionEvidence | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": RULE_SCHEMA_VERSION,
            "state": self.state.value,
            "source_kind": self.source_kind.value,
            "root_cause": self.root_cause.value,
            "actions": list(self.actions),
            "match": _context_match_payload(self.context),
            "promotion_evidence": _evidence_payload(self.promotion_evidence),
        }


@dataclass(frozen=True)
class MatchedLearningRule:
    rule: LearningRule
    outcome: RuleObservationOutcome = RuleObservationOutcome.MATCHED

    @property
    def key(self) -> str:
        return self.rule.key


def derive_task_learning_context(task: LocalAgentTask, *, run_id: int) -> TaskLearningContext:
    """Derive a non-executable matching context from a validated task contract."""

    if not isinstance(task, LocalAgentTask) or not _positive_int(run_id):
        _invalid()
    prefixes = _string_tuple(task.allowed_paths, _SAFE_PATH)
    commands = tuple(sorted({_command_fingerprint(command) for command in task.verification_commands}))
    request = task.request.casefold()
    return TaskLearningContext(
        run_id=run_id,
        task_key=_identifier(task.task_key),
        repository_kind=_repository_kind(prefixes, task.verification_commands),
        allowed_path_prefixes=prefixes,
        verification_command_fingerprints=commands,
        high_risk_tags=tuple(sorted(
            tag for tag, markers in _HIGH_RISK_WORDS.items() if any(marker in request for marker in markers)
        )),
    )


def build_current_task_rule(
    context: TaskLearningContext,
    *,
    root_cause: RootCauseKind | str = RootCauseKind.VERIFICATION_FAILURE,
    actions: Sequence[str] = ("verification_replay",),
    source_kind: RetrospectiveSourceKind | str = RetrospectiveSourceKind.RUN_OBSERVATION,
    state: LearningRuleState | str = LearningRuleState.ACTIVE_CURRENT_TASK,
    promotion_evidence: PromotionEvidence | None = None,
) -> LearningRule:
    """Build one bounded check-only rule without promoting it implicitly."""

    payload = {
        "schema_version": RULE_SCHEMA_VERSION,
        "state": _enum_value(state, LearningRuleState),
        "source_kind": _enum_value(source_kind, RetrospectiveSourceKind),
        "root_cause": _enum_value(root_cause, RootCauseKind),
        "actions": list(actions),
        "match": _context_match_payload(_validate_context(context)),
        "promotion_evidence": _evidence_payload(promotion_evidence),
    }
    normalized = validate_rule_payload(payload)
    match = normalized["match"]
    assert isinstance(match, Mapping)
    return LearningRule(
        key=rule_key(normalized),
        state=LearningRuleState(str(normalized["state"])),
        source_kind=RetrospectiveSourceKind(str(normalized["source_kind"])),
        root_cause=RootCauseKind(str(normalized["root_cause"])),
        actions=tuple(normalized["actions"]),  # type: ignore[arg-type]
        context=_context_from_match(match),
        promotion_evidence=_evidence_from_payload(normalized["promotion_evidence"]),
    )


def match_rules(context: TaskLearningContext, rules: Sequence[LearningRule]) -> tuple[MatchedLearningRule, ...]:
    """Return only applicable, contract-compatible inert rules in input order."""

    current = _validate_context(context)
    matched: list[MatchedLearningRule] = []
    for rule in rules:
        canonical = _canonical_candidate(rule)
        if canonical is None:
            continue
        if canonical.state not in {
            LearningRuleState.ACTIVE_CURRENT_TASK, LearningRuleState.TRIAL, LearningRuleState.STABLE
        }:
            continue
        candidate = canonical.context
        exact_scope = (
            candidate.allowed_path_prefixes == current.allowed_path_prefixes
            and candidate.verification_command_fingerprints == current.verification_command_fingerprints
            and candidate.high_risk_tags == current.high_risk_tags
            and candidate.failure_sources == current.failure_sources
        )
        if canonical.state is LearningRuleState.ACTIVE_CURRENT_TASK:
            compatible = candidate.run_id == current.run_id and candidate.task_key == current.task_key and exact_scope
        else:
            compatible = current.repository_kind != "unknown" and candidate.repository_kind == current.repository_kind and exact_scope
        if compatible:
            matched.append(MatchedLearningRule(rule=canonical))
    return tuple(matched)


def validate_rule_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Validate and canonicalise one persistence-safe, non-executable rule."""

    expected = {"schema_version", "state", "source_kind", "root_cause", "actions", "match", "promotion_evidence"}
    if not isinstance(payload, Mapping) or set(payload) != expected or payload["schema_version"] != RULE_SCHEMA_VERSION:
        _invalid()
    state = _enum_value(payload["state"], LearningRuleState)
    source_kind = _enum_value(payload["source_kind"], RetrospectiveSourceKind)
    root_cause = _enum_value(payload["root_cause"], RootCauseKind)
    actions = _validate_actions(payload["actions"])
    if not isinstance(payload["match"], Mapping):
        _invalid()
    context = _context_from_match(payload["match"])
    evidence = _evidence_from_payload(payload["promotion_evidence"])
    if state == LearningRuleState.STABLE:
        if evidence is None or len(evidence.task_keys) < 3 or len(evidence.workspace_fingerprints) < 2 or evidence.counterexample_count != 0:
            _invalid()
    return {
        "schema_version": RULE_SCHEMA_VERSION,
        "state": state,
        "source_kind": source_kind,
        "root_cause": root_cause,
        "actions": list(actions),
        "match": _context_match_payload(context),
        "promotion_evidence": _evidence_payload(evidence),
    }


def canonical_rule_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(validate_rule_payload(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def rule_key(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_rule_bytes(payload)).hexdigest()


def _context_match_payload(context: TaskLearningContext) -> dict[str, object]:
    return {
        "run_id": context.run_id,
        "task_key": context.task_key,
        "repository_kind": context.repository_kind,
        "allowed_path_prefixes": list(context.allowed_path_prefixes),
        "verification_command_fingerprints": list(context.verification_command_fingerprints),
        "high_risk_tags": list(context.high_risk_tags),
        "failure_sources": list(context.failure_sources),
    }


def _context_from_match(match: Mapping[str, object]) -> TaskLearningContext:
    expected = {"run_id", "task_key", "repository_kind", "allowed_path_prefixes", "verification_command_fingerprints", "high_risk_tags", "failure_sources"}
    if set(match) != expected or not _positive_int(match["run_id"]) or match["repository_kind"] not in _REPOSITORY_KINDS:
        _invalid()
    return TaskLearningContext(
        run_id=int(match["run_id"]), task_key=_identifier(match["task_key"]), repository_kind=str(match["repository_kind"]),
        allowed_path_prefixes=_string_tuple(match["allowed_path_prefixes"], _SAFE_PATH),
        verification_command_fingerprints=_string_tuple(match["verification_command_fingerprints"], _SHA256),
        high_risk_tags=_string_tuple(match["high_risk_tags"], _SAFE_IDENTIFIER, allow_empty=True),
        failure_sources=_string_tuple(match["failure_sources"], _SAFE_IDENTIFIER, allow_empty=True),
    )


def _evidence_payload(evidence: PromotionEvidence | None) -> dict[str, object] | None:
    if evidence is None:
        return None
    if not isinstance(evidence, PromotionEvidence):
        _invalid()
    return {
        "task_keys": list(evidence.task_keys),
        "workspace_fingerprints": list(evidence.workspace_fingerprints),
        "counterexample_count": evidence.counterexample_count,
    }


def _evidence_from_payload(payload: object) -> PromotionEvidence | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping) or set(payload) != {"task_keys", "workspace_fingerprints", "counterexample_count"}:
        _invalid()
    count = payload["counterexample_count"]
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        _invalid()
    return PromotionEvidence(
        task_keys=_string_tuple(payload["task_keys"], _SAFE_IDENTIFIER),
        workspace_fingerprints=_string_tuple(payload["workspace_fingerprints"], _SAFE_IDENTIFIER),
        counterexample_count=count,
    )


def _validate_context(context: TaskLearningContext) -> TaskLearningContext:
    if not isinstance(context, TaskLearningContext):
        _invalid()
    return _context_from_match(_context_match_payload(context))


def _string_tuple(value: object, expression: re.Pattern[str], *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or (not value and not allow_empty):
        _invalid()
    values: list[str] = []
    for item in value:
        if not isinstance(item, str) or len(item) > 256 or not expression.fullmatch(item) or _SHELL_CONTROL.search(item):
            _invalid()
        if expression is _SAFE_PATH:
            path = PurePosixPath(item)
            if item.startswith("/") or ".." in path.parts or item in {".", ""}:
                _invalid()
        values.append(item)
    return tuple(sorted(set(values)))


def _validate_actions(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value or any(not isinstance(item, str) or item not in _ACTIONS for item in value):
        _invalid()
    return tuple(sorted(set(value)))


def _repository_kind(paths: Sequence[str], commands: Sequence[Sequence[str]]) -> str:
    path_names = {PurePosixPath(path).name.casefold() for path in paths}
    suffixes = {PurePosixPath(path).suffix.casefold() for path in paths}
    command_names = {
        PurePosixPath(command[0]).name.casefold()
        for command in commands
        if command and isinstance(command[0], str)
    }
    if command_names & {"gradle", "gradlew", "gradlew.bat"} or path_names & {"build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"} or ".java" in suffixes:
        return "gradle"
    if command_names & {"node", "npm", "npx", "yarn", "pnpm"} or suffixes & {".js", ".jsx", ".ts", ".tsx", ".vue"}:
        return "node"
    if any(name in {"python", "python3"} or re.fullmatch(r"python3\.[0-9]+", name) for name in command_names) or suffixes & {".py", ".pyi"}:
        return "python"
    return "unknown"


def _command_fingerprint(command: Sequence[str]) -> str:
    if not isinstance(command, tuple) or not command or any(not isinstance(item, str) for item in command):
        _invalid()
    return hashlib.sha256(json.dumps(list(command), ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _canonical_candidate(rule: object) -> LearningRule | None:
    """Fail closed for directly constructed or tampered public dataclasses."""

    if not _has_exact_candidate_types(rule):
        return None
    try:
        payload = {
            "schema_version": RULE_SCHEMA_VERSION,
            "state": rule.state.value,
            "source_kind": rule.source_kind.value,
            "root_cause": rule.root_cause.value,
            "actions": list(rule.actions),
            "match": _context_match_payload(rule.context),
            "promotion_evidence": _evidence_payload(rule.promotion_evidence),
        }
        normalized = validate_rule_payload(payload)
        if rule.key != rule_key(normalized):
            return None
        match = normalized["match"]
        assert isinstance(match, Mapping)
        return LearningRule(
            key=rule_key(normalized),
            state=LearningRuleState(str(normalized["state"])),
            source_kind=RetrospectiveSourceKind(str(normalized["source_kind"])),
            root_cause=RootCauseKind(str(normalized["root_cause"])),
            actions=tuple(normalized["actions"]),  # type: ignore[arg-type]
            context=_context_from_match(match),
            promotion_evidence=_evidence_from_payload(normalized["promotion_evidence"]),
        )
    except (TypeError, ValueError):
        return None


def _has_exact_candidate_types(rule: object) -> bool:
    """Reject untrusted subclasses before performing any semantic operation."""

    if type(rule) is not LearningRule:
        return False
    if (
        type(rule.key) is not str
        or type(rule.state) is not LearningRuleState
        or type(rule.source_kind) is not RetrospectiveSourceKind
        or type(rule.root_cause) is not RootCauseKind
        or type(rule.actions) is not tuple
        or any(type(action) is not str for action in rule.actions)
        or not _has_exact_context_types(rule.context)
    ):
        return False
    return rule.promotion_evidence is None or _has_exact_evidence_types(rule.promotion_evidence)


def _has_exact_context_types(context: object) -> bool:
    if type(context) is not TaskLearningContext:
        return False
    if type(context.run_id) is not int or type(context.task_key) is not str or type(context.repository_kind) is not str:
        return False
    return all(
        type(values) is tuple and all(type(item) is str for item in values)
        for values in (
            context.allowed_path_prefixes,
            context.verification_command_fingerprints,
            context.high_risk_tags,
            context.failure_sources,
        )
    )


def _has_exact_evidence_types(evidence: object) -> bool:
    if type(evidence) is not PromotionEvidence:
        return False
    return (
        type(evidence.task_keys) is tuple
        and all(type(item) is str for item in evidence.task_keys)
        and type(evidence.workspace_fingerprints) is tuple
        and all(type(item) is str for item in evidence.workspace_fingerprints)
        and type(evidence.counterexample_count) is int
    )


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value) or _SENSITIVE_FIELD.search(value):
        _invalid()
    return value


def _enum_value(value: object, enum_type: type[StrEnum]) -> str:
    if not isinstance(value, str):
        _invalid()
    try:
        return enum_type(value).value
    except ValueError:
        _invalid()
    raise AssertionError("unreachable")


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _invalid() -> None:
    raise ValueError("repair_learning_invalid")
