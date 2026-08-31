from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from app import database
from app.knowledge_index import publish_approved_knowledge_markdown
from app.sensitive_text import contains_sensitive_text, redact_sensitive_text


LEARNING_CANDIDATE_REPOSITORY_SCHEMA_VERSION = "his-learning-candidate-repository.v1"
LEARNING_CANDIDATE_TYPES = (
    "eval.sample",
    "contract_plugin.draft",
    "rule_pack.draft",
    "knowledge.candidate",
)
_REVIEW_DECISIONS = frozenset(("approve", "reject"))
_REVIEWER_ALIAS = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_SCOPE_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SAFE_TASK_KEY = re.compile(r"[^\\/\x00]{1,160}")
_SAFE_FAILURE_KIND = re.compile(r"[a-z][a-z0-9._-]{0,127}")


class LearningCandidateRepository:
    """Manager-backed, reviewer-only learning candidate state.

    The repository accepts only deliberately small failure metadata.  Original
    evidence locations and provider/model responses are converted to hashes
    before persistence, so the Manager database cannot become a raw run or
    response archive.
    """

    def __init__(self) -> None:
        database.init_db()

    def create_failed_run_candidates(
        self,
        sample: Mapping[str, object],
        *,
        source_action_audit_id: int,
    ) -> dict[str, object]:
        prepared = _prepare_failure_sample(sample)
        audit_id = _required_source_action_audit_id(source_action_audit_id)
        now = database.now_iso()
        state = "expired" if _is_expired(prepared["expires_at"], now) else "candidate"
        candidate_set_key = _candidate_set_key(audit_id)
        evidence_hash = _sha256_json(prepared["evidence_ref_hashes"])
        candidates: list[dict[str, object]] = []
        created_count = 0
        with database.connect() as connection:
            connection.execute("begin immediate")
            source = connection.execute(
                "select id, status from manager_provider_action_audits where id = ?", (audit_id,)
            ).fetchone()
            if source is None or str(source["status"]) != "failed":
                raise ValueError("learning_candidate_source_audit_invalid")
            for candidate_type in LEARNING_CANDIDATE_TYPES:
                candidate_key = f"{candidate_set_key}:{candidate_type}"
                safe_summary = {
                    "task_key": prepared["task_key"],
                    "failure_kind": prepared["failure_kind"],
                    # A plain-text response cannot be reliably classified as a
                    # human summary.  Persist only its digest so candidate
                    # storage can never become an indirect model/Provider
                    # response archive.
                    "summary_hash": _sha256_json(prepared["summary"]),
                    "scope": prepared["scope"],
                    "evidence_ref_hashes": prepared["evidence_ref_hashes"],
                    "source_run_hash": prepared["source_run_hash"],
                }
                cursor = connection.execute(
                    """
                    insert into manager_learning_candidates
                        (candidate_key, candidate_type, source_action_audit_id,
                         evidence_hash, safe_summary_json, state, reviewer_alias,
                         created_at, expires_at)
                    values (?, ?, ?, ?, ?, ?, '', ?, ?)
                    on conflict do nothing
                    """,
                    (
                        candidate_key,
                        candidate_type,
                        audit_id,
                        evidence_hash,
                        _safe_summary_json(safe_summary),
                        state,
                        now,
                        prepared["expires_at"],
                    ),
                )
                created_count += int(cursor.rowcount == 1)
                row = _select_candidate_for_source_audit(
                    connection, source_action_audit_id=audit_id, candidate_type=candidate_type
                )
                candidates.append(_candidate_record(row))
        return {
            "schema_version": LEARNING_CANDIDATE_REPOSITORY_SCHEMA_VERSION,
            "candidate_set_key": candidate_set_key,
            "candidate_count": len(candidates),
            "created_count": created_count,
            "auto_promote": False,
            "candidates": candidates,
        }

    def list_candidates(self, *, limit: int = 100) -> list[dict[str, object]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("learning_candidate_input_invalid")
        with database.connect() as connection:
            rows = connection.execute(
                """
                select * from manager_learning_candidates
                order by id desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [_candidate_record(row) for row in rows]

    def review_candidate(
        self,
        *,
        candidate_key: str,
        decision: str,
        reviewer_alias: str,
    ) -> dict[str, object]:
        key = _candidate_key(candidate_key)
        review_decision = _review_decision(decision)
        reviewer = _reviewer(reviewer_alias)
        now = database.now_iso()
        expired = False
        with database.connect() as connection:
            connection.execute("begin immediate")
            row = _select_candidate(connection, key)
            if _is_expired(str(row["expires_at"]), now):
                if str(row["state"]) != "promoted":
                    connection.execute(
                        """
                        update manager_learning_candidates
                        set state = 'expired', reviewer_alias = ?, reviewed_at = ?
                        where id = ?
                        """,
                        (reviewer, now, int(row["id"])),
                    )
                expired = True
                record = _candidate_record(_select_candidate(connection, key))
            else:
                current_state = str(row["state"])
                if current_state == "candidate":
                    next_state = "approved" if review_decision == "approve" else "rejected"
                    connection.execute(
                        """
                        update manager_learning_candidates
                        set state = ?, reviewer_alias = ?, reviewed_at = ?
                        where id = ? and state = 'candidate'
                        """,
                        (next_state, reviewer, now, int(row["id"])),
                    )
                    record = _candidate_record(_select_candidate(connection, key))
                elif (
                    (current_state == "approved" and review_decision == "approve")
                    or (current_state == "rejected" and review_decision == "reject")
                ) and str(row["reviewer_alias"]) == reviewer:
                    record = _candidate_record(row)
                else:
                    raise PermissionError("candidate_review_state_invalid")
        if expired:
            raise PermissionError("candidate_expired")
        return record

    def promote_knowledge_candidate(
        self,
        *,
        candidate_key: str,
        reviewer_alias: str,
        knowledge_home: str,
        knowledge_allowed_base: str,
    ) -> dict[str, object]:
        key = _candidate_key(candidate_key)
        reviewer = _reviewer(reviewer_alias)
        now = database.now_iso()
        with database.connect() as connection:
            row = _select_candidate(connection, key)
        if str(row["candidate_type"]) != "knowledge.candidate":
            raise PermissionError("knowledge_candidate_required")
        if _is_expired(str(row["expires_at"]), now):
            self._mark_expired(key, reviewer, now)
            raise PermissionError("candidate_expired")
        if str(row["state"]) not in {"approved", "promoted"}:
            raise PermissionError("candidate_not_approved")
        if str(row["reviewer_alias"]) != reviewer:
            raise PermissionError("promotion_reviewer_mismatch")
        safe_summary = _load_safe_summary(row)
        evidence_refs = safe_summary.get("evidence_ref_hashes")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            raise PermissionError("candidate_safe_evidence_required")
        title, body, content_hash = _knowledge_markdown_content(safe_summary)
        valid_until = str(row["expires_at"])
        publication = publish_approved_knowledge_markdown(
            knowledge_home,
            content_hash=content_hash,
            title=title,
            body=body,
            valid_until=valid_until,
            allowed_base=knowledge_allowed_base,
        )
        with database.connect() as connection:
            connection.execute("begin immediate")
            current = _select_candidate(connection, key)
            if str(current["state"]) == "approved":
                if str(current["reviewer_alias"]) != reviewer:
                    raise PermissionError("promotion_reviewer_mismatch")
                connection.execute(
                    """
                    update manager_learning_candidates
                    set state = 'promoted', promoted_at = ?
                    where id = ? and state = 'approved'
                    """,
                    (now, int(current["id"])),
                )
                current = _select_candidate(connection, key)
            elif str(current["state"]) != "promoted":
                raise PermissionError("candidate_not_approved")
        return {**_candidate_record(current), **publication}

    def _mark_expired(self, candidate_key: str, reviewer_alias: str, now: str) -> None:
        with database.connect() as connection:
            connection.execute("begin immediate")
            connection.execute(
                """
                update manager_learning_candidates
                set state = 'expired', reviewer_alias = ?, reviewed_at = ?
                where candidate_key = ? and state != 'promoted'
                """,
                (reviewer_alias, now, candidate_key),
            )


def _prepare_failure_sample(sample: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(sample, Mapping):
        raise ValueError("learning_candidate_input_invalid")
    run_id = _required_safe_text(sample.get("run_id"), "run_id", maximum=160)
    task_key = _required_safe_text(sample.get("task_key"), "task_key", maximum=160)
    if not _SAFE_TASK_KEY.fullmatch(task_key):
        raise ValueError("learning_candidate_input_invalid")
    failure_kind = _required_safe_text(sample.get("failure_kind"), "failure_kind", maximum=128)
    if not _SAFE_FAILURE_KIND.fullmatch(failure_kind):
        raise ValueError("learning_candidate_input_invalid")
    summary = _required_safe_text(sample.get("summary"), "summary", maximum=512)
    if _looks_like_raw_response(summary):
        raise ValueError("learning_candidate_input_invalid")
    references = sample.get("evidence_refs")
    if not isinstance(references, list) or not references:
        raise ValueError("learning_candidate_input_invalid")
    reference_hashes: list[str] = []
    for reference in references:
        safe_reference = _required_safe_text(reference, "evidence_ref", maximum=512)
        if _looks_like_raw_response(safe_reference):
            raise ValueError("learning_candidate_input_invalid")
        reference_hashes.append("sha256:" + hashlib.sha256(safe_reference.encode("utf-8")).hexdigest())
    scope = sample.get("scope", {})
    if not isinstance(scope, Mapping) or len(scope) > 16:
        raise ValueError("learning_candidate_input_invalid")
    safe_scope: dict[str, str] = {}
    for raw_key, raw_value in scope.items():
        if not isinstance(raw_key, str) or not _SCOPE_KEY.fullmatch(raw_key):
            raise ValueError("learning_candidate_input_invalid")
        value = _required_safe_text(raw_value, "scope", maximum=160)
        if "/" in value or "\\" in value:
            raise ValueError("learning_candidate_input_invalid")
        safe_scope[raw_key] = value
    expires_at = _optional_expiry(sample.get("expires_at", ""))
    return {
        "source_run_hash": "sha256:" + hashlib.sha256(run_id.encode("utf-8")).hexdigest(),
        "task_key": task_key,
        "failure_kind": failure_kind,
        "summary": summary,
        "scope": safe_scope,
        "evidence_ref_hashes": sorted(set(reference_hashes)),
        "expires_at": expires_at,
    }


def _required_safe_text(value: object, name: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("learning_candidate_input_invalid")
    normalized = value.strip()
    redacted = redact_sensitive_text(normalized)
    if (
        contains_sensitive_text(normalized)
        or redacted.startswith("[REDACTED_")
        or "\x00" in normalized
    ):
        raise ValueError("learning_candidate_input_invalid")
    return redacted


def _looks_like_raw_response(value: str) -> bool:
    stripped = value.lstrip()
    return stripped.startswith(("{", "[", "<")) or "\n{" in value or "\n[" in value


def _required_source_action_audit_id(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("learning_candidate_source_audit_invalid")
    return value


def _candidate_set_key(source_action_audit_id: int) -> str:
    material = {"source_action_audit_id": source_action_audit_id}
    return "learn-" + hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]


def _sha256_json(value: object) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _safe_summary_json(value: Mapping[str, object]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 4_096 or contains_sensitive_text(encoded):
        raise ValueError("learning_candidate_input_invalid")
    return encoded


def _select_candidate(connection: Any, candidate_key: str) -> Any:
    row = connection.execute(
        "select * from manager_learning_candidates where candidate_key = ?", (candidate_key,)
    ).fetchone()
    if row is None:
        raise KeyError("learning_candidate_not_found")
    return row


def _select_candidate_for_source_audit(
    connection: Any,
    *,
    source_action_audit_id: int,
    candidate_type: str,
) -> Any:
    row = connection.execute(
        """
        select * from manager_learning_candidates
        where source_action_audit_id = ? and candidate_type = ?
        """,
        (source_action_audit_id, candidate_type),
    ).fetchone()
    if row is None:
        raise KeyError("learning_candidate_not_found")
    return row


def _candidate_record(row: Any) -> dict[str, object]:
    safe_summary = _load_safe_summary(row)
    return {
        "id": int(row["id"]),
        "candidate_key": str(row["candidate_key"]),
        "candidate_type": str(row["candidate_type"]),
        "source_action_audit_id": int(row["source_action_audit_id"]),
        "state": str(row["state"]),
        "reviewer_alias": str(row["reviewer_alias"]),
        "created_at": str(row["created_at"]),
        "reviewed_at": str(row["reviewed_at"]),
        "promoted_at": str(row["promoted_at"]),
        "expires_at": str(row["expires_at"]),
        "safe_summary": safe_summary,
        "requires_reviewer": str(row["state"]) in {"candidate", "approved"},
        "executable": False,
        "auto_promote": False,
    }


def _load_safe_summary(row: Any) -> dict[str, object]:
    try:
        value = json.loads(str(row["safe_summary_json"]))
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("learning_candidate_storage_invalid") from None
    if not isinstance(value, dict) or contains_sensitive_text(json.dumps(value, ensure_ascii=False)):
        raise ValueError("learning_candidate_storage_invalid")
    return value


def _candidate_key(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("learn-") or len(value) > 256:
        raise ValueError("learning_candidate_input_invalid")
    if contains_sensitive_text(value):
        raise ValueError("learning_candidate_input_invalid")
    return value


def _review_decision(value: object) -> str:
    if not isinstance(value, str) or value not in _REVIEW_DECISIONS:
        raise ValueError("learning_candidate_input_invalid")
    return value


def _reviewer(value: object) -> str:
    if not isinstance(value, str) or not _REVIEWER_ALIAS.fullmatch(value):
        raise ValueError("reviewer_alias_invalid")
    return value


def _optional_expiry(value: object) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise ValueError("learning_candidate_input_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("learning_candidate_input_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("learning_candidate_input_invalid")
    return parsed.isoformat()


def _is_expired(expires_at: str, now: str) -> bool:
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(expires_at) <= datetime.fromisoformat(now)
    except ValueError:
        return True


def _knowledge_markdown_content(safe_summary: Mapping[str, object]) -> tuple[str, str, str]:
    task_key = safe_summary.get("task_key")
    failure_kind = safe_summary.get("failure_kind")
    summary_hash = safe_summary.get("summary_hash")
    scope = safe_summary.get("scope")
    evidence_refs = safe_summary.get("evidence_ref_hashes")
    if (
        not isinstance(task_key, str)
        or not isinstance(failure_kind, str)
        or not isinstance(summary_hash, str)
        or not isinstance(scope, Mapping)
        or not isinstance(evidence_refs, list)
        or not evidence_refs
    ):
        raise ValueError("learning_candidate_storage_invalid")
    scope_lines = [f"- {key}: {value}" for key, value in sorted(scope.items())]
    evidence_lines = [f"- {value}" for value in evidence_refs if isinstance(value, str)]
    if not evidence_lines:
        raise PermissionError("candidate_safe_evidence_required")
    title = f"{task_key} 受控运行失败知识候选"
    body = "\n".join(
        (
            "该结论由人工审核后从受控失败运行候选中推广；不包含原始响应、文件路径或凭证。",
            "",
            f"失败类别：{failure_kind}",
            f"受控失败摘要哈希：{summary_hash}",
            "",
            "适用范围：",
            *scope_lines,
            "",
            "可追溯安全证据引用（仅哈希）：",
            *evidence_lines,
        )
    )
    content_hash = _sha256_json(
        {"title": title, "body": body, "evidence_refs": evidence_refs}
    )
    return title, body, content_hash
