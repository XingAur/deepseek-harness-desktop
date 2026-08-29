"""Deterministic Harness decision and repeat-prevention contracts.

The model never owns these decisions.  Harness builds the contract from the
validated task, durable learning checks, and the previous attempt outcome;
the worker only receives the resulting execute-only instructions.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence

from app.local_agent_contract import LocalAgentTask
from app.repair_learning import LearningRule, MatchedLearningRule, RootCauseKind


LEARNING_GUARD_SCHEMA_VERSION = "his-harness-learning-guard.v1"
REPLAN_SCHEMA_VERSION = "his-harness-replan.v1"

_SAFE_CODE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")

_ROOT_CAUSE_CHECKS: dict[RootCauseKind, tuple[str, ...]] = {
    RootCauseKind.VERIFICATION_FAILURE: (
        "reinspect_requirement_and_call_chain",
        "replay_failed_verification_with_clean_workspace",
        "verify_side_effect_boundary",
        "replan_before_model_execution",
    ),
    RootCauseKind.REVIEW_GAP: (
        "reinspect_requirement_and_call_chain",
        "replay_review_findings_against_changed_paths",
        "run_independent_review_before_confirmation",
        "replan_before_model_execution",
    ),
    RootCauseKind.PATH_COVERAGE_GAP: (
        "reinspect_requirement_and_call_chain",
        "trace_adjacent_callers_and_callees",
        "reconcile_allowed_paths_with_evidence",
        "replan_before_model_execution",
    ),
    RootCauseKind.CONTRACT_MISMATCH: (
        "reinspect_requirement_and_call_chain",
        "reconcile_contract_with_project_evidence",
        "rebuild_execution_scope",
        "replan_before_model_execution",
    ),
    RootCauseKind.IMPLEMENTATION_DEFECT: (
        "reinspect_requirement_and_call_chain",
        "recheck_root_cause_evidence",
        "run_targeted_regression_before_confirmation",
        "replan_before_model_execution",
    ),
}


def required_checks_for_root_cause(root_cause: RootCauseKind | str) -> tuple[str, ...]:
    """Return fixed checks for a normalized learning root cause."""

    try:
        normalized = RootCauseKind(root_cause)
    except (TypeError, ValueError):
        raise ValueError("harness_learning_guard_invalid") from None
    return _ROOT_CAUSE_CHECKS[normalized]


def build_learning_guard_payload(
    *,
    run_id: int,
    attempt_id: int,
    checks: Sequence[MatchedLearningRule],
) -> dict[str, object]:
    """Build the persisted, non-executable no-repeat guard for one attempt."""

    _positive_id(run_id)
    _positive_id(attempt_id)
    if not isinstance(checks, (tuple, list)):
        raise ValueError("harness_learning_guard_invalid")

    guards: list[dict[str, object]] = []
    all_checks = {"reinspect_requirement_and_call_chain", "rebuild_execution_scope"}
    for matched in checks:
        if type(matched) is not MatchedLearningRule or matched.outcome.value != "matched":
            raise ValueError("harness_learning_guard_invalid")
        rule = matched.rule
        if type(rule) is not LearningRule:
            raise ValueError("harness_learning_guard_invalid")
        root_cause = RootCauseKind(rule.root_cause.value)
        required = set(required_checks_for_root_cause(root_cause))
        all_checks.update(required)
        actions = tuple(sorted(set(rule.actions)))
        guards.append(
            {
                "rule_key": _digest(rule.key),
                "root_cause": root_cause.value,
                "actions": list(actions),
                "required_checks": sorted(required),
                "no_repeat": True,
            }
        )

    guards.sort(key=lambda item: str(item["rule_key"]))
    return {
        "schema_version": LEARNING_GUARD_SCHEMA_VERSION,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "must_replan": bool(guards),
        "must_reinspect": True,
        "forbid_replaying_previous_decision": True,
        "required_checks": sorted(all_checks),
        "guards": guards,
    }


def build_replan_decision(
    task: LocalAgentTask,
    *,
    run_id: int,
    attempt_id: int,
    previous_plan_version: int,
    failure_code: str,
    learning_guard: Mapping[str, object],
) -> dict[str, object]:
    """Create the next authoritative Harness plan version.

    A retry is therefore never a replay of the old model prompt: the previous
    decision is explicitly superseded and the new decision always requires a
    fresh requirement/call-chain inspection before execution.
    """

    if not isinstance(task, LocalAgentTask):
        raise ValueError("harness_learning_guard_invalid")
    _positive_id(run_id)
    _positive_id(attempt_id)
    if (
        not isinstance(previous_plan_version, int)
        or isinstance(previous_plan_version, bool)
        or previous_plan_version < 0
        or not isinstance(failure_code, str)
        or _SAFE_CODE.fullmatch(failure_code) is None
        or not isinstance(learning_guard, Mapping)
    ):
        raise ValueError("harness_learning_guard_invalid")
    _validate_learning_guard(learning_guard, run_id=run_id, attempt_id=attempt_id)

    payload: dict[str, object] = {
        "schema_version": REPLAN_SCHEMA_VERSION,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "plan_version": previous_plan_version + 1,
        "supersedes_plan_version": previous_plan_version or None,
        "decision_kind": "initial_plan" if previous_plan_version == 0 else "replan",
        "failure_code": failure_code,
        "task_key": task.task_key,
        "contract_hash": task.contract_hash,
        "allowed_paths": list(task.allowed_paths),
        "verification_commands": [list(command) for command in task.verification_commands],
        "acceptance_criteria_sha256": [
            hashlib.sha256(item.encode("utf-8")).hexdigest()
            for item in task.acceptance_criteria
        ],
        "learning_guard": dict(learning_guard),
        "execute_only": True,
        "must_reinspect": True,
        "forbid_model_replanning": True,
    }
    payload["decision_sha256"] = _payload_digest(payload)
    return payload


def validate_replan_decision(
    task: LocalAgentTask,
    payload: Mapping[str, object],
    *,
    run_id: int,
    attempt_id: int,
) -> dict[str, object]:
    """Fail closed if a worker prompt carries a stale or forged decision."""

    if not isinstance(task, LocalAgentTask) or not isinstance(payload, Mapping):
        raise ValueError("harness_learning_guard_invalid")
    _positive_id(run_id)
    _positive_id(attempt_id)
    expected_keys = {
        "schema_version", "run_id", "attempt_id", "plan_version",
        "supersedes_plan_version", "decision_kind", "failure_code", "task_key",
        "contract_hash", "allowed_paths", "verification_commands",
        "acceptance_criteria_sha256", "learning_guard", "execute_only",
        "must_reinspect", "forbid_model_replanning", "decision_sha256",
    }
    if set(payload) != expected_keys:
        raise ValueError("harness_learning_guard_invalid")
    if (
        payload["schema_version"] != REPLAN_SCHEMA_VERSION
        or payload["run_id"] != run_id
        or payload["attempt_id"] != attempt_id
        or payload["task_key"] != task.task_key
        or payload["contract_hash"] != task.contract_hash
        or payload["allowed_paths"] != list(task.allowed_paths)
        or payload["verification_commands"] != [list(command) for command in task.verification_commands]
        or payload["execute_only"] is not True
        or payload["must_reinspect"] is not True
        or payload["forbid_model_replanning"] is not True
        or not isinstance(payload["decision_sha256"], str)
        or _digest(payload["decision_sha256"]) != _payload_digest(
            {key: value for key, value in payload.items() if key != "decision_sha256"}
        )
    ):
        raise ValueError("harness_learning_guard_invalid")
    _validate_learning_guard(payload["learning_guard"], run_id=run_id, attempt_id=attempt_id)  # type: ignore[arg-type]
    return dict(payload)


def _validate_learning_guard(
    payload: Mapping[str, object], *, run_id: int, attempt_id: int,
) -> None:
    if (
        payload.get("schema_version") != LEARNING_GUARD_SCHEMA_VERSION
        or payload.get("run_id") != run_id
        or payload.get("attempt_id") != attempt_id
        or payload.get("must_reinspect") is not True
        or payload.get("forbid_replaying_previous_decision") is not True
        or not isinstance(payload.get("required_checks"), list)
        or not isinstance(payload.get("guards"), list)
    ):
        raise ValueError("harness_learning_guard_invalid")


def _payload_digest(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _positive_id(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("harness_learning_guard_invalid")
    return value


def _digest(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError("harness_learning_guard_invalid")
    return value
