"""Safe, local-only candidate review and promotion service for HIS knowledge."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))
from knowledge_store import KnowledgeStore  # noqa: E402
from knowledge_capability import knowledge_home, result as capability_result, run_main, validate_request  # noqa: E402

_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID = re.compile(r"(?<!\d)(\d{17}[\dXx])(?![\dXx])")
_BEARER = re.compile(r"\bbearer\s+[A-Za-z0-9._-]{8,}\b", re.I)
_URI = re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s:/]+:[^\s@]+@", re.I)
_TOKEN_ASSIGNMENT = re.compile(r"\b(?:token|pat|api[_-]?key|access[_-]?key)\s*[:=]\s*\S+", re.I)
_AUTHORIZATION_HEADER = re.compile(r"\bauthorization\s*:\s*(?:basic|bearer|digest|token)\b", re.I)
_SCOPE_FIELDS = ("hospital_scope", "region_scope", "module_scope", "repo_scope", "branch_scope")
_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECKS = "10X98765432"
_CREDENTIAL_KEY_TOKENS = {"token", "pat", "password", "passwd", "pwd", "secret", "authorization", "auth", "dsn"}
_CREDENTIAL_KEY_COMPOUNDS = (("access", "key"), ("access", "key", "id"), ("connection", "string"), ("session", "id"), ("session", "token"), ("cookie", "id"), ("session", "cookie"), ("cookie", "value"))
_MAX_AUDIT_TEXT_LENGTH = 256


@dataclass(frozen=True)
class CandidateResult:
    candidate_id: Optional[int]
    status: str
    content_hash: str
    categories: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromotionResult:
    candidate_id: int
    item_id: int
    status: str
    content_hash: str


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(dict(payload)).encode("utf-8")).hexdigest()


def _contains_valid_chinese_id(value: str) -> bool:
    for match in _ID.finditer(value):
        identity = match.group(1).upper()
        try:
            date(int(identity[6:10]), int(identity[10:12]), int(identity[12:14]))
        except ValueError:
            continue
        checksum = sum(int(digit) * weight for digit, weight in zip(identity[:17], _ID_WEIGHTS)) % 11
        if identity[-1] == _ID_CHECKS[checksum]:
            return True
    return False


def _credential_key(key: object) -> bool:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(key)).lower()
    tokens = tuple(re.findall(r"[a-z0-9]+", text))
    if "status" in tokens:
        return False
    if bool(set(tokens) & _CREDENTIAL_KEY_TOKENS) or tokens in {("session",), ("cookie",)}:
        return True
    return any(
        tokens[index:index + len(alias)] == alias
        for alias in _CREDENTIAL_KEY_COMPOUNDS
        for index in range(len(tokens) - len(alias) + 1)
    )


def _has_nonempty_value(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(_has_nonempty_value(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_nonempty_value(child) for child in value)
    return value is not None and value != ""


def _signals(value: object, path: str = "$") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if _credential_key(key) and _has_nonempty_value(child):
                found.append(("credential", child_path))
            found.extend(_signals(child, child_path))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            found.extend(_signals(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        if _BEARER.search(value) or _URI.search(value) or _TOKEN_ASSIGNMENT.search(value) or _AUTHORIZATION_HEADER.search(value):
            found.append(("credential", path))
        if _PHONE.search(value) or _contains_valid_chinese_id(value):
            found.append(("privacy", path))
    return found


def _persisted_texts(value: object) -> list[str]:
    if isinstance(value, Mapping):
        return [text for child in value.values() for text in _persisted_texts(child)]
    if isinstance(value, (list, tuple)):
        return [text for child in value for text in _persisted_texts(child)]
    return [value] if isinstance(value, str) and value.strip() else []


def _validate_audit_text(reviewer: object, reason: object, candidate: Optional[Mapping[str, object]] = None) -> tuple[str, str]:
    if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(reason, str) or not reason.strip():
        raise ValueError("audit requires reviewer and reason")
    reviewer, reason = reviewer.strip(), reason.strip()
    if len(reviewer) > _MAX_AUDIT_TEXT_LENGTH or len(reason) > _MAX_AUDIT_TEXT_LENGTH:
        raise ValueError("audit text is not permitted")
    if _signals({"reviewer": reviewer, "reason": reason}, "$.audit"):
        raise ValueError("audit text is not permitted")
    if candidate is not None:
        candidate_values = (candidate.get("payload", candidate), candidate.get("provenance", {}))
        normalized_audits = tuple(" ".join(text.split()).casefold() for text in (reviewer, reason))
        for candidate_text in _persisted_texts(candidate_values):
            normalized_candidate = " ".join(candidate_text.split()).casefold()
            if any(
                normalized_candidate == audit
                or (len(normalized_candidate) >= 16 and normalized_candidate in audit)
                for audit in normalized_audits
            ):
                raise ValueError("audit text is not permitted")
    return reviewer, reason


class KnowledgeMaintainer:
    """Candidate-only maintenance facade; it never promotes without explicit review and evidence gates."""

    def __init__(self, store: KnowledgeStore) -> None:
        self.store = store

    def create_candidate(self, *, payload: Mapping[str, Any], provenance: Mapping[str, Any], allow_personal_memory: bool = False) -> CandidateResult:
        if not isinstance(payload, Mapping) or not isinstance(provenance, Mapping):
            raise ValueError("payload and provenance must be mappings")
        payload_dict, provenance_dict = dict(payload), dict(provenance)
        digest = _hash(payload_dict)
        personal = payload_dict.get("kind") == "personal_memory" or payload_dict.get("authority") == "personal_preference"
        signals = _signals(payload_dict, "$.payload") + _signals(provenance_dict, "$.provenance")
        if personal and not allow_personal_memory:
            return CandidateResult(None, "blocked", digest, ("personal_memory",), ("$.payload",))
        if signals:
            categories = tuple(sorted({category for category, _ in signals}))
            paths = tuple(sorted({path for _, path in signals}))
            return CandidateResult(None, "blocked", digest, categories, paths)
        existing = self.store.find_candidate_by_payload(payload_dict)
        if existing is not None:
            return CandidateResult(existing["id"], existing["review_status"], digest)
        candidate = self.store.create_candidate(proposed_key=str(payload_dict.get("stable_key", "candidate")), payload=payload_dict, provenance=provenance_dict)
        return CandidateResult(candidate["id"], "pending", digest)

    def review_candidate(self, candidate_id: int, *, status: str, reviewer: str, reason: str) -> CandidateResult:
        if status not in {"approved", "rejected"}:
            raise ValueError("review requires pending status, reviewer, and reason")
        reviewer, reason = _validate_audit_text(reviewer, reason)
        candidate = self.store.get_candidate(candidate_id)
        if candidate is None or candidate["review_status"] != "pending":
            raise ValueError("only pending candidates may be reviewed")
        reviewer, reason = _validate_audit_text(reviewer, reason, candidate)
        reviewed = self.store.review_candidate(candidate_id, status=status, reason=_canonical({"reviewer": reviewer, "reason": reason}))
        return CandidateResult(candidate_id, reviewed["review_status"], _hash(reviewed["payload"]))

    def promote_candidate(self, candidate_id: int, *, reviewer: str, review_reason: str) -> PromotionResult:
        reviewer, review_reason = _validate_audit_text(reviewer, review_reason)
        candidate = self.store.get_candidate(candidate_id)
        if candidate is None or candidate["review_status"] != "approved":
            raise ValueError("only approved candidates can promote")
        reviewer, review_reason = _validate_audit_text(reviewer, review_reason, candidate)
        payload, provenance = candidate["payload"], candidate["provenance"]
        signals = _signals(payload, "$.payload") + _signals(provenance, "$.provenance")
        if signals:
            raise ValueError("stored candidate contains sensitive content")
        sources = payload.get("source_refs")
        if not isinstance(sources, list) or not any(source for source in sources):
            raise ValueError("promotion requires a source reference")
        if not any(isinstance(payload.get(field), str) and payload[field].strip() for field in _SCOPE_FIELDS):
            raise ValueError("promotion requires an explicit scope")
        if payload.get("kind") == "business_rule" and not all(isinstance(source, Mapping) and source.get("claim_level") in {"code", "runtime", "production"} for source in sources):
            raise ValueError("business-rule promotion requires evidence claim level")
        item = self.store.promote_candidate_with_audit(candidate_id, reviewer=reviewer, reason=review_reason)
        return PromotionResult(candidate_id, item["id"], "promoted", _hash(payload))


def create_candidate(
    *, payload: Mapping[str, Any], provenance: Mapping[str, Any], allow_personal_memory: bool = False,
    store: Optional[KnowledgeStore] = None,
) -> CandidateResult:
    """Thin, lazy public API for candidate creation without import-time I/O."""
    return KnowledgeMaintainer(store or KnowledgeStore()).create_candidate(
        payload=payload, provenance=provenance, allow_personal_memory=allow_personal_memory,
    )


def promote_candidate(
    candidate_id: int, *, reviewer: str, review_reason: str, store: Optional[KnowledgeStore] = None,
) -> PromotionResult:
    """Thin, lazy public API for promotion after the explicit review operation."""
    return KnowledgeMaintainer(store or KnowledgeStore()).promote_candidate(
        candidate_id, reviewer=reviewer, review_reason=review_reason,
    )


_CREATE_INPUT = frozenset(("payload", "provenance", "allow_personal_memory"))
_REVIEW_INPUT = frozenset(("candidate_id", "status", "reviewer", "reason"))
_PROMOTE_INPUT = frozenset(("candidate_id", "reviewer", "review_reason"))


def _positive_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _validate_create(value: Mapping[str, object]) -> None:
    if set(value) != _CREATE_INPUT or not isinstance(value.get("payload"), dict) or not isinstance(value.get("provenance"), dict) or not isinstance(value.get("allow_personal_memory"), bool):
        raise ValueError("invalid capability request")


def _validate_review(value: Mapping[str, object]) -> None:
    if set(value) != _REVIEW_INPUT or not _positive_id(value.get("candidate_id")) or value.get("status") not in {"approved", "rejected"} or not isinstance(value.get("reviewer"), str) or not value["reviewer"].strip() or not isinstance(value.get("reason"), str) or not value["reason"].strip():
        raise ValueError("invalid capability request")


def _validate_promote(value: Mapping[str, object]) -> None:
    if set(value) != _PROMOTE_INPUT or not _positive_id(value.get("candidate_id")) or not isinstance(value.get("reviewer"), str) or not value["reviewer"].strip() or not isinstance(value.get("review_reason"), str) or not value["review_reason"].strip():
        raise ValueError("invalid capability request")


def _audit_path(store: KnowledgeStore) -> str:
    # The runtime treats injected environment values as sensitive.  Keep the
    # local SQLite identity useful without reflecting the injected home path.
    return "$HIS_KNOWLEDGE_HOME/knowledge.sqlite"


def _blocked(request: Mapping[str, object], level: str, digest: str) -> dict[str, object]:
    return capability_result(
        request, status="blocked", summary="KNOWLEDGE_CANDIDATE_BLOCKED", mutation_level=level, changed=False,
        data={"content_hash": digest}, blockers=["candidate_not_permitted"],
        audit={"credential_class": "none", "external_write_attempted": False},
    )


def execute_request(request: object) -> dict[str, object]:
    """Dispatch the three L2 local maintenance capabilities with redacted outputs."""
    if not isinstance(request, dict):
        raise ValueError("invalid capability request")
    name = request.get("capability")
    store = KnowledgeStore(home=knowledge_home())
    maintainer = KnowledgeMaintainer(store)
    if name == "knowledge.candidate.create":
        checked = validate_request(request, capability=name, mode="apply", mutation_level="L2", scope=("knowledge:candidate:create",), input_fields=_CREATE_INPUT, validator=_validate_create)
        values = checked["input"]
        existing = store.find_candidate_by_payload(values["payload"]) if store.database_path.is_file() else None
        created = maintainer.create_candidate(payload=values["payload"], provenance=values["provenance"], allow_personal_memory=values["allow_personal_memory"])
        if created.status == "blocked":
            return _blocked(checked, "L2", created.content_hash)
        return capability_result(
            checked, status="success", summary="KNOWLEDGE_CANDIDATE_CREATED", mutation_level="L2", changed=existing is None,
            data={"candidate_id": created.candidate_id, "status": created.status, "content_hash": created.content_hash, "local_sqlite_path": _audit_path(store)},
            audit={"credential_class": "none", "external_write_attempted": False, "candidate_id": created.candidate_id, "status": created.status, "content_hash": created.content_hash, "local_sqlite_path": _audit_path(store)},
        )
    if name == "knowledge.candidate.review":
        checked = validate_request(request, capability=name, mode="apply", mutation_level="L2", scope=("knowledge:candidate:review",), input_fields=_REVIEW_INPUT, validator=_validate_review)
        values = checked["input"]
        reviewed = maintainer.review_candidate(int(values["candidate_id"]), status=str(values["status"]), reviewer=str(values["reviewer"]), reason=str(values["reason"]))
        return capability_result(
            checked, status="success", summary="KNOWLEDGE_CANDIDATE_REVIEWED", mutation_level="L2", changed=True,
            data={"candidate_id": reviewed.candidate_id, "status": reviewed.status, "content_hash": reviewed.content_hash, "local_sqlite_path": _audit_path(store)},
            audit={"credential_class": "none", "external_write_attempted": False, "candidate_id": reviewed.candidate_id, "status": reviewed.status, "local_sqlite_path": _audit_path(store)},
        )
    if name == "knowledge.item.promote":
        checked = validate_request(request, capability=name, mode="apply", mutation_level="L2", scope=("knowledge:item:promote",), input_fields=_PROMOTE_INPUT, validator=_validate_promote)
        values = checked["input"]
        candidate = store.get_candidate(int(values["candidate_id"]))
        before = None if candidate is None else store.get_item(str(candidate["payload"].get("stable_key", "")))
        before_audit = "" if candidate is None else str(candidate.get("review_reason", ""))
        promoted = maintainer.promote_candidate(int(values["candidate_id"]), reviewer=str(values["reviewer"]), review_reason=str(values["review_reason"]))
        after = store.get_item(str(candidate["payload"].get("stable_key", "")))
        after_candidate = store.get_candidate(int(values["candidate_id"]))
        changed = (
            before is None
            or before.get("content_hash") != after.get("content_hash")
            or (after_candidate is not None and before_audit != str(after_candidate.get("review_reason", "")))
        )
        return capability_result(
            checked, status="success", summary="KNOWLEDGE_ITEM_PROMOTED", mutation_level="L2", changed=changed,
            data={"candidate_id": promoted.candidate_id, "item_id": promoted.item_id, "status": promoted.status, "content_hash": promoted.content_hash, "local_sqlite_path": _audit_path(store)},
            audit={"credential_class": "none", "external_write_attempted": False, "candidate_id": promoted.candidate_id, "item_id": promoted.item_id, "content_hash": promoted.content_hash, "local_sqlite_path": _audit_path(store)},
        )
    raise ValueError("invalid capability request")


def main(argv: list[str] | None = None) -> int:
    return run_main(argv, execute_request)


if __name__ == "__main__":
    raise SystemExit(main())
