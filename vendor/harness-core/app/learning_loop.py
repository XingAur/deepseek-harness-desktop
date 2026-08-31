from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


LEARNING_CANDIDATES_SCHEMA_VERSION = "his-learning-candidates.v1"
_CANDIDATE_KINDS = (
    "eval.sample",
    "contract_plugin.draft",
    "rule_pack.draft",
    "knowledge.candidate",
)
_SECRET_PATTERNS = (
    re.compile(r"Authorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/=-]{24,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def derive_learning_candidates(sample: Mapping[str, Any]) -> dict[str, Any]:
    """Derive inert learning candidates from one failed run sample.

    This function is deliberately metadata-only: it does not write a database,
    does not call knowledge promote, and does not execute any suggested action.
    """
    values = _validate_sample(sample)
    candidate_key = _candidate_key(values)
    candidates = [
        {
            "kind": kind,
            "status": "candidate",
            "candidate_id": f"{candidate_key}:{index}:{kind}",
            "source_run_id": values["run_id"],
            "task_key": values["task_key"],
            "failure_kind": values["failure_kind"],
            "summary": values["summary"],
            "scope": dict(values["scope"]),
            "evidence_refs": list(values["evidence_refs"]),
            "promotion_allowed": False,
            "requires_review": True,
        }
        for index, kind in enumerate(_CANDIDATE_KINDS, start=1)
    ]
    return {
        "schema_version": LEARNING_CANDIDATES_SCHEMA_VERSION,
        "changed": False,
        "auto_promote": False,
        "persistence": "not_written",
        "candidate_count": len(candidates),
        "candidates": candidates,
        "next_actions": [
            "人工审核候选样本是否可复用。",
            "需要持久化时显式调用对应 candidate create/review/promote 流程。",
        ],
    }


def persist_learning_candidates(
    sample: Mapping[str, Any],
    candidate_home: str | Path,
) -> dict[str, Any]:
    """Export failed-sample candidates for offline compatibility only.

    Validation happens before directory creation so secret-shaped evidence never
    creates a knowledge/candidate home as a side effect. Persisting a candidate
    set does not promote knowledge, evals, contract plugins, or rule packs.  A
    Manager-controlled run must use :func:`persist_manager_learning_candidates`
    instead; this JSON export is not the operational source of truth.
    """

    result = derive_learning_candidates(sample)
    first_candidate_id = str(result["candidates"][0]["candidate_id"])
    candidate_set_id = first_candidate_id.split(":", 1)[0]
    payload = {
        **result,
        "changed": True,
        "persistence": "written",
        "review_state": "review_required",
        "candidate_set_id": candidate_set_id,
    }
    root = Path(candidate_home).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    candidate_set_path = root / f"{_safe_filename(candidate_set_id)}.json"
    temporary_path = candidate_set_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(candidate_set_path)
    return {**payload, "candidate_set_path": str(candidate_set_path)}


def export_learning_candidates_offline(
    sample: Mapping[str, Any],
    candidate_home: str | Path,
) -> dict[str, Any]:
    """Named compatibility entrypoint for the legacy offline JSON export."""

    return persist_learning_candidates(sample, candidate_home)


def persist_manager_learning_candidates(
    sample: Mapping[str, Any],
    *,
    repository: Any,
    source_action_audit_id: int | None = None,
) -> dict[str, Any]:
    """Persist one failed controlled run in the Manager candidate repository.

    The repository is deliberately injected so this module cannot choose a
    local candidate directory or silently fall back to JSON during Manager
    operation.  It stores only audit-safe metadata and hashed evidence refs.
    """

    creator = getattr(repository, "create_failed_run_candidates", None)
    if not callable(creator):
        raise TypeError("learning_candidate_repository_required")
    stored = creator(sample, source_action_audit_id=source_action_audit_id)
    if not isinstance(stored, Mapping):
        raise ValueError("learning_candidate_repository_invalid")
    result = dict(stored)
    result.update(
        {
            "persistence": "manager_database",
            "changed": int(result.get("created_count") or 0) > 0,
            "auto_promote": False,
            "offline_export": False,
        }
    )
    result.pop("candidate_set_path", None)
    return result


def _validate_sample(sample: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(sample, Mapping):
        raise ValueError("learning sample must be a mapping")
    run_id = _text(sample.get("run_id"), "run_id")
    failure_kind = _text(sample.get("failure_kind"), "failure_kind")
    summary = _text(sample.get("summary"), "summary")
    task_key = str(sample.get("task_key") or "").strip()
    evidence_refs = sample.get("evidence_refs", ())
    if not isinstance(evidence_refs, list) or not all(isinstance(item, str) and item.strip() for item in evidence_refs):
        raise ValueError("evidence_refs must be non-empty strings")
    if any(_contains_secret_shape(item) for item in evidence_refs):
        raise ValueError("sensitive evidence is not accepted")
    scope = sample.get("scope", {})
    if not isinstance(scope, Mapping) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in scope.items()):
        raise ValueError("scope must be a string mapping")
    return {
        "run_id": run_id,
        "task_key": task_key,
        "failure_kind": failure_kind,
        "summary": summary,
        "evidence_refs": [item.strip() for item in evidence_refs],
        "scope": {key.strip(): value.strip() for key, value in scope.items() if key.strip() and value.strip()},
    }


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _contains_secret_shape(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_PATTERNS)


def _candidate_key(values: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        "|".join(
            (
                str(values["run_id"]),
                str(values["task_key"]),
                str(values["failure_kind"]),
                str(values["summary"]),
            )
        ).encode("utf-8")
    ).hexdigest()[:16]
    return "learn-" + digest


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-") or "candidate"
