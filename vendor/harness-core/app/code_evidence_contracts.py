from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from app.sensitive_text import validate_audit_alias


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = frozenset(
    (
        "required_capabilities",
        "successful_capabilities",
        "required_artifact_kinds",
        "artifact_kinds",
        "bundle_sealed",
        "snapshot_consistent",
        "search_complete",
        "diff_complete",
        "sensitive_blocked",
        "limit_exceeded",
        "verification_bundle_sha256",
        "review_bundle_sha256",
        "bundle_sha256",
        "verification_status",
        "review_verdict",
    )
)


@dataclass(frozen=True)
class EvidenceCompletenessResult:
    status: str
    blockers: tuple[str, ...]


def evaluate_evidence_completeness(facts: Mapping[str, object]) -> EvidenceCompletenessResult:
    if not isinstance(facts, Mapping) or set(facts) != _FIELDS:
        raise ValueError("code_evidence_completeness_invalid")
    required_capabilities = _aliases(facts["required_capabilities"])
    successful_capabilities = _aliases(facts["successful_capabilities"], allow_empty=True)
    required_artifacts = _aliases(facts["required_artifact_kinds"])
    artifacts = _aliases(facts["artifact_kinds"], allow_empty=True)
    booleans = {
        field: _boolean(facts[field])
        for field in (
            "bundle_sealed",
            "snapshot_consistent",
            "search_complete",
            "diff_complete",
            "sensitive_blocked",
            "limit_exceeded",
        )
    }
    bundle_sha = _sha(facts["bundle_sha256"])
    verification_sha = _sha(facts["verification_bundle_sha256"])
    review_sha = _sha(facts["review_bundle_sha256"])
    verification_status = _choice(facts["verification_status"], ("passed", "failed", "not_run"))
    review_verdict = _choice(facts["review_verdict"], ("approved", "changes_requested", "not_run"))

    blockers: list[str] = []
    if not set(required_capabilities).issubset(successful_capabilities):
        blockers.append("code_evidence_capability_incomplete")
    if not set(required_artifacts).issubset(artifacts):
        blockers.append("code_evidence_artifact_incomplete")
    if not booleans["bundle_sealed"]:
        blockers.append("code_evidence_bundle_unsealed")
    if not booleans["snapshot_consistent"]:
        blockers.append("code_evidence_changed")
    if not booleans["search_complete"]:
        blockers.append("code_evidence_search_incomplete")
    if not booleans["diff_complete"]:
        blockers.append("code_evidence_diff_incomplete")
    if booleans["sensitive_blocked"]:
        blockers.append("code_evidence_sensitive")
    if booleans["limit_exceeded"]:
        blockers.append("code_evidence_limit_exceeded")
    if verification_status != "passed":
        blockers.append("code_evidence_verification_failed")
    if review_verdict != "approved":
        blockers.append("code_evidence_review_not_approved")
    if verification_sha != bundle_sha or review_sha != bundle_sha:
        blockers.append("code_evidence_binding_invalid")
    unique = tuple(dict.fromkeys(blockers))
    return EvidenceCompletenessResult(
        status="complete" if not unique else "blocked",
        blockers=unique,
    )


def _aliases(value: object, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("code_evidence_completeness_invalid")
    try:
        result = tuple(validate_audit_alias(item) for item in value)
    except (TypeError, ValueError):
        raise ValueError("code_evidence_completeness_invalid") from None
    if (
        (not allow_empty and not result)
        or len(result) > 32
        or len(result) != len(set(result))
    ):
        raise ValueError("code_evidence_completeness_invalid")
    return result


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise ValueError("code_evidence_completeness_invalid")
    return value


def _sha(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError("code_evidence_completeness_invalid")
    return value


def _choice(value: object, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError("code_evidence_completeness_invalid")
    return value
