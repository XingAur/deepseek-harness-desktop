from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from app import database
from app.sensitive_text import (
    contains_sensitive_scalar_text,
    contains_sensitive_text,
    is_sensitive_mapping_key,
    validate_audit_alias,
)


CODE_EVIDENCE_REPOSITORY_SCHEMA_VERSION = "his-code-evidence-repository.v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_RELATIVE_PATH = re.compile(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*\Z")
_BUNDLE_STATUSES = frozenset(("collecting", "sealed", "reviewed", "invalid"))
_SET_STATUSES = frozenset(("collecting", "sealed", "invalid"))
_EVENT_STATUSES = frozenset(("pending", "running", "success", "failed", "blocked"))
_VERDICTS = frozenset(("approved", "changes_requested"))
_MAX_JSON_BYTES = 4_096


class CodeEvidenceRepository:
    """Append-only persistence for immutable code-evidence bundle facts."""

    def __init__(self) -> None:
        database.init_db()

    def create_bundle(
        self,
        *,
        bundle_key: str,
        conversation_key: str,
        task_key: str,
        repository_alias: str,
        repository_identity_sha256: str,
        head_sha: str,
        snapshot_sha256: str,
        required_capabilities: Sequence[str],
    ) -> dict[str, object]:
        safe_bundle_key = _alias(bundle_key)
        safe_conversation_key = _alias(conversation_key)
        safe_task_key = _alias(task_key)
        safe_repository_alias = _alias(repository_alias)
        safe_repository_identity = _sha256(repository_identity_sha256)
        safe_head = _git_sha(head_sha)
        safe_snapshot = _sha256(snapshot_sha256)
        capabilities = _capabilities(required_capabilities)
        capabilities_json = _canonical_json(list(capabilities))
        created_at = database.now_iso()
        try:
            with database.connect() as connection:
                connection.execute("begin immediate")
                cursor = connection.execute(
                    """
                    insert into code_evidence_bundles(
                        bundle_key, conversation_key, task_key, repository_alias,
                        repository_identity_sha256, head_sha, snapshot_sha256,
                        required_capabilities_json, status, seal_sha256,
                        created_at, sealed_at, reviewed_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, 'collecting', '', ?, '', '')
                    """,
                    (
                        safe_bundle_key,
                        safe_conversation_key,
                        safe_task_key,
                        safe_repository_alias,
                        safe_repository_identity,
                        safe_head,
                        safe_snapshot,
                        capabilities_json,
                        created_at,
                    ),
                )
                bundle_id = int(cursor.lastrowid)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("code_evidence_storage_invalid") from exc
        return self.get_bundle(bundle_id)

    def append_artifact(
        self,
        bundle_id: int,
        *,
        kind: str,
        relative_path: str,
        sha256: str,
        size_bytes: int,
        device: int,
        inode: int,
        mode: int,
        link_count: int,
    ) -> dict[str, object]:
        normalized_id = _positive_int(bundle_id)
        safe_kind = _alias(kind)
        safe_path = _relative_path(relative_path)
        safe_sha = _sha256(sha256)
        safe_size = _nonnegative_int(size_bytes)
        safe_device = _nonnegative_int(device)
        safe_inode = _positive_int(inode)
        safe_mode = _file_mode(mode)
        safe_link_count = _link_count(link_count)
        created_at = database.now_iso()
        try:
            with database.connect() as connection:
                connection.execute("begin immediate")
                _require_bundle_status(connection, normalized_id, "collecting")
                cursor = connection.execute(
                    """
                    insert into code_evidence_artifacts(
                        bundle_id, kind, relative_path, sha256, size_bytes,
                        device, inode, mode, link_count, created_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_id, safe_kind, safe_path, safe_sha, safe_size,
                        safe_device, safe_inode, safe_mode, safe_link_count, created_at,
                    ),
                )
                artifact_id = int(cursor.lastrowid)
        except KeyError:
            raise
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("code_evidence_storage_invalid") from exc
        return {
            "id": artifact_id,
            "bundle_id": normalized_id,
            "kind": safe_kind,
            "relative_path": safe_path,
            "sha256": safe_sha,
            "size_bytes": safe_size,
            "device": safe_device,
            "inode": safe_inode,
            "mode": safe_mode,
            "link_count": safe_link_count,
            "created_at": created_at,
        }

    def append_event(
        self,
        bundle_id: int,
        *,
        event_type: str,
        status: str,
        details: Mapping[str, object],
    ) -> dict[str, object]:
        normalized_id = _positive_int(bundle_id)
        safe_event = _alias(event_type)
        safe_status = _allowed_alias(status, _EVENT_STATUSES)
        details_json = _canonical_mapping(details)
        created_at = database.now_iso()
        try:
            with database.connect() as connection:
                connection.execute("begin immediate")
                _require_bundle_status(connection, normalized_id, "collecting")
                row = connection.execute(
                    "select coalesce(max(sequence_no), 0) from code_evidence_events where bundle_id = ?",
                    (normalized_id,),
                ).fetchone()
                sequence_no = int(row[0]) + 1
                cursor = connection.execute(
                    """
                    insert into code_evidence_events(
                        bundle_id, sequence_no, event_type, status, details_json, created_at
                    ) values (?, ?, ?, ?, ?, ?)
                    """,
                    (normalized_id, sequence_no, safe_event, safe_status, details_json, created_at),
                )
                event_id = int(cursor.lastrowid)
        except KeyError:
            raise
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("code_evidence_storage_invalid") from exc
        return {
            "id": event_id,
            "bundle_id": normalized_id,
            "sequence_no": sequence_no,
            "event_type": safe_event,
            "status": safe_status,
            "details_json": details_json,
            "details": json.loads(details_json),
            "created_at": created_at,
        }

    def seal_bundle(self, bundle_id: int, *, seal_sha256: str) -> dict[str, object]:
        normalized_id = _positive_int(bundle_id)
        safe_seal = _sha256(seal_sha256)
        sealed_at = database.now_iso()
        try:
            with database.connect() as connection:
                connection.execute("begin immediate")
                _require_bundle_status(connection, normalized_id, "collecting")
                artifacts = int(
                    connection.execute(
                        "select count(*) from code_evidence_artifacts where bundle_id = ?",
                        (normalized_id,),
                    ).fetchone()[0]
                )
                if artifacts < 1:
                    raise ValueError("code_evidence_state_invalid")
                changed = connection.execute(
                    """
                    update code_evidence_bundles
                    set status = 'sealed', seal_sha256 = ?, sealed_at = ?
                    where id = ? and status = 'collecting'
                    """,
                    (safe_seal, sealed_at, normalized_id),
                ).rowcount
                if changed != 1:
                    raise ValueError("code_evidence_state_invalid")
        except KeyError:
            raise
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("code_evidence_storage_invalid") from exc
        return self.get_bundle(normalized_id)

    def append_review(
        self,
        bundle_id: int,
        *,
        verdict: str,
        review_sha256: str,
        evidence_seal_sha256: str,
        findings: Sequence[Mapping[str, object]],
    ) -> dict[str, object]:
        normalized_id = _positive_int(bundle_id)
        safe_verdict = _allowed_alias(verdict, _VERDICTS)
        safe_review_sha = _sha256(review_sha256)
        safe_evidence_seal = _sha256(evidence_seal_sha256)
        findings_json = _canonical_findings(findings)
        created_at = database.now_iso()
        try:
            with database.connect() as connection:
                connection.execute("begin immediate")
                row = _require_bundle_status(connection, normalized_id, "sealed")
                if str(row["seal_sha256"]) != safe_evidence_seal:
                    raise ValueError("code_evidence_state_invalid")
                cursor = connection.execute(
                    """
                    insert into code_evidence_reviews(
                        bundle_id, verdict, review_sha256, evidence_seal_sha256,
                        findings_json, created_at
                    ) values (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_id,
                        safe_verdict,
                        safe_review_sha,
                        safe_evidence_seal,
                        findings_json,
                        created_at,
                    ),
                )
                review_id = int(cursor.lastrowid)
                updated = connection.execute(
                    """
                    update code_evidence_bundles
                    set status = 'reviewed', reviewed_at = ?
                    where id = ? and status = 'sealed'
                    """,
                    (created_at, normalized_id),
                ).rowcount
                if updated != 1:
                    raise ValueError("code_evidence_state_invalid")
        except KeyError:
            raise
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("code_evidence_storage_invalid") from exc
        return {
            "id": review_id,
            "bundle_id": normalized_id,
            "verdict": safe_verdict,
            "review_sha256": safe_review_sha,
            "evidence_seal_sha256": safe_evidence_seal,
            "findings": json.loads(findings_json),
            "created_at": created_at,
            "bundle_status": "reviewed",
        }

    def get_bundle(self, bundle_id: int) -> dict[str, object]:
        normalized_id = _positive_int(bundle_id)
        try:
            with database.connect() as connection:
                row = connection.execute(
                    "select * from code_evidence_bundles where id = ?", (normalized_id,)
                ).fetchone()
                if row is None:
                    raise KeyError("code_evidence_bundle_not_found")
                artifacts = connection.execute(
                    "select * from code_evidence_artifacts where bundle_id = ? order by id",
                    (normalized_id,),
                ).fetchall()
                events = connection.execute(
                    "select * from code_evidence_events where bundle_id = ? order by sequence_no",
                    (normalized_id,),
                ).fetchall()
                review = connection.execute(
                    "select * from code_evidence_reviews where bundle_id = ?",
                    (normalized_id,),
                ).fetchone()
            return _bundle_record(row, artifacts, events, review)
        except KeyError:
            raise
        except ValueError:
            raise ValueError("code_evidence_storage_invalid") from None
        except Exception as exc:
            raise ValueError("code_evidence_storage_invalid") from exc

    def create_evidence_set(
        self,
        *,
        set_key: str,
        conversation_key: str,
        required_repository_count: int,
    ) -> dict[str, object]:
        safe_set_key = _alias(set_key)
        safe_conversation = _alias(conversation_key)
        required_count = _positive_int(required_repository_count)
        created_at = database.now_iso()
        try:
            with database.connect() as connection:
                connection.execute("begin immediate")
                cursor = connection.execute(
                    """
                    insert into code_evidence_sets(
                        set_key, conversation_key, required_repository_count,
                        status, seal_sha256, created_at, sealed_at
                    ) values (?, ?, ?, 'collecting', '', ?, '')
                    """,
                    (safe_set_key, safe_conversation, required_count, created_at),
                )
                evidence_set_id = int(cursor.lastrowid)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("code_evidence_storage_invalid") from exc
        return self.get_evidence_set(evidence_set_id)

    def append_set_member(
        self,
        evidence_set_id: int,
        *,
        repository_alias: str,
        bundle_id: int,
        ordinal: int,
    ) -> dict[str, object]:
        safe_set_id = _positive_int(evidence_set_id)
        safe_bundle_id = _positive_int(bundle_id)
        safe_repository = _alias(repository_alias)
        safe_ordinal = _positive_int(ordinal)
        created_at = database.now_iso()
        try:
            with database.connect() as connection:
                connection.execute("begin immediate")
                _require_set_status(connection, safe_set_id, "collecting")
                bundle = connection.execute(
                    "select repository_alias from code_evidence_bundles where id = ?",
                    (safe_bundle_id,),
                ).fetchone()
                if bundle is None:
                    raise KeyError("code_evidence_bundle_not_found")
                if str(bundle["repository_alias"]) != safe_repository:
                    raise ValueError("code_evidence_relationship_invalid")
                cursor = connection.execute(
                    """
                    insert into code_evidence_set_members(
                        evidence_set_id, repository_alias, bundle_id, ordinal, created_at
                    ) values (?, ?, ?, ?, ?)
                    """,
                    (safe_set_id, safe_repository, safe_bundle_id, safe_ordinal, created_at),
                )
                member_id = int(cursor.lastrowid)
        except KeyError:
            raise
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("code_evidence_storage_invalid") from exc
        return {
            "id": member_id,
            "evidence_set_id": safe_set_id,
            "repository_alias": safe_repository,
            "bundle_id": safe_bundle_id,
            "ordinal": safe_ordinal,
            "created_at": created_at,
        }

    def seal_evidence_set(self, evidence_set_id: int, *, seal_sha256: str) -> dict[str, object]:
        safe_set_id = _positive_int(evidence_set_id)
        safe_seal = _sha256(seal_sha256)
        sealed_at = database.now_iso()
        try:
            with database.connect() as connection:
                connection.execute("begin immediate")
                row = _require_set_status(connection, safe_set_id, "collecting")
                count = int(
                    connection.execute(
                        "select count(*) from code_evidence_set_members where evidence_set_id = ?",
                        (safe_set_id,),
                    ).fetchone()[0]
                )
                if count != int(row["required_repository_count"]):
                    raise ValueError("code_evidence_state_invalid")
                updated = connection.execute(
                    """
                    update code_evidence_sets
                    set status = 'sealed', seal_sha256 = ?, sealed_at = ?
                    where id = ? and status = 'collecting'
                    """,
                    (safe_seal, sealed_at, safe_set_id),
                ).rowcount
                if updated != 1:
                    raise ValueError("code_evidence_state_invalid")
        except KeyError:
            raise
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("code_evidence_storage_invalid") from exc
        return self.get_evidence_set(safe_set_id)

    def get_evidence_set(self, evidence_set_id: int) -> dict[str, object]:
        safe_set_id = _positive_int(evidence_set_id)
        try:
            with database.connect() as connection:
                row = connection.execute(
                    "select * from code_evidence_sets where id = ?", (safe_set_id,)
                ).fetchone()
                if row is None:
                    raise KeyError("code_evidence_set_not_found")
                members = connection.execute(
                    "select * from code_evidence_set_members where evidence_set_id = ? order by ordinal",
                    (safe_set_id,),
                ).fetchall()
            return _set_record(row, members)
        except KeyError:
            raise
        except ValueError:
            raise ValueError("code_evidence_storage_invalid") from None
        except Exception as exc:
            raise ValueError("code_evidence_storage_invalid") from exc

    def list_recent_bundles(self, *, limit: int = 100) -> list[dict[str, object]]:
        safe_limit = _bounded_limit(limit)
        try:
            with database.connect() as connection:
                identifiers = [
                    int(row["id"])
                    for row in connection.execute(
                        "select id from code_evidence_bundles order by id desc limit ?",
                        (safe_limit,),
                    ).fetchall()
                ]
            return [self.get_bundle(bundle_id) for bundle_id in identifiers]
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("code_evidence_storage_invalid") from exc

    def list_recent_evidence_sets(self, *, limit: int = 100) -> list[dict[str, object]]:
        safe_limit = _bounded_limit(limit)
        try:
            with database.connect() as connection:
                identifiers = [
                    int(row["id"])
                    for row in connection.execute(
                        "select id from code_evidence_sets order by id desc limit ?",
                        (safe_limit,),
                    ).fetchall()
                ]
            return [self.get_evidence_set(set_id) for set_id in identifiers]
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("code_evidence_storage_invalid") from exc


def _bundle_record(row: Any, artifacts: Sequence[Any], events: Sequence[Any], review: Any | None) -> dict[str, object]:
    status = _allowed_alias(str(row["status"]), _BUNDLE_STATUSES, storage=True)
    seal = str(row["seal_sha256"])
    sealed_at = _stored_text(row["sealed_at"], allow_empty=True)
    reviewed_at = _stored_text(row["reviewed_at"], allow_empty=True)
    if status == "collecting" and (seal or sealed_at or reviewed_at):
        raise ValueError
    if status == "sealed" and (not _is_sha256(seal) or not sealed_at or reviewed_at):
        raise ValueError
    if status == "reviewed" and (not _is_sha256(seal) or not sealed_at or not reviewed_at or review is None):
        raise ValueError
    if status == "invalid" and review is not None:
        raise ValueError
    capabilities = _load_json(row["required_capabilities_json"])
    if not isinstance(capabilities, list) or tuple(capabilities) != _capabilities(capabilities):
        raise ValueError
    artifact_values = [_artifact_record(item, int(row["id"])) for item in artifacts]
    event_values = [_event_record(item, int(row["id"]), index + 1) for index, item in enumerate(events)]
    review_value = _review_record(review, int(row["id"]), seal) if review is not None else None
    return {
        "id": int(row["id"]),
        "bundle_key": _alias(str(row["bundle_key"])),
        "conversation_key": _alias(str(row["conversation_key"])),
        "task_key": _alias(str(row["task_key"])),
        "repository_alias": _alias(str(row["repository_alias"])),
        "repository_identity_sha256": _sha256(str(row["repository_identity_sha256"])),
        "head_sha": _git_sha(str(row["head_sha"])),
        "snapshot_sha256": _sha256(str(row["snapshot_sha256"])),
        "required_capabilities": list(capabilities),
        "status": status,
        "seal_sha256": seal,
        "created_at": _stored_text(row["created_at"]),
        "sealed_at": sealed_at,
        "reviewed_at": reviewed_at,
        "artifacts": artifact_values,
        "events": event_values,
        "review": review_value,
    }


def _artifact_record(row: Any, bundle_id: int) -> dict[str, object]:
    if int(row["bundle_id"]) != bundle_id:
        raise ValueError
    return {
        "id": _positive_int(int(row["id"])),
        "bundle_id": bundle_id,
        "kind": _alias(str(row["kind"])),
        "relative_path": _relative_path(str(row["relative_path"])),
        "sha256": _sha256(str(row["sha256"])),
        "size_bytes": _nonnegative_int(int(row["size_bytes"])),
        "device": _nonnegative_int(int(row["device"])),
        "inode": _positive_int(int(row["inode"])),
        "mode": _file_mode(int(row["mode"])),
        "link_count": _link_count(int(row["link_count"])),
        "created_at": _stored_text(row["created_at"]),
    }


def _event_record(row: Any, bundle_id: int, sequence_no: int) -> dict[str, object]:
    if int(row["bundle_id"]) != bundle_id or int(row["sequence_no"]) != sequence_no:
        raise ValueError
    details_json = str(row["details_json"])
    details = _load_json(details_json)
    if not isinstance(details, dict) or _canonical_mapping(details) != details_json:
        raise ValueError
    return {
        "id": _positive_int(int(row["id"])),
        "bundle_id": bundle_id,
        "sequence_no": sequence_no,
        "event_type": _alias(str(row["event_type"])),
        "status": _allowed_alias(str(row["status"]), _EVENT_STATUSES, storage=True),
        "details_json": details_json,
        "details": details,
        "created_at": _stored_text(row["created_at"]),
    }


def _review_record(row: Any, bundle_id: int, seal_sha256: str) -> dict[str, object]:
    if int(row["bundle_id"]) != bundle_id or str(row["evidence_seal_sha256"]) != seal_sha256:
        raise ValueError
    findings = _load_json(row["findings_json"])
    if not isinstance(findings, list) or _canonical_findings(findings) != str(row["findings_json"]):
        raise ValueError
    return {
        "id": _positive_int(int(row["id"])),
        "bundle_id": bundle_id,
        "verdict": _allowed_alias(str(row["verdict"]), _VERDICTS, storage=True),
        "review_sha256": _sha256(str(row["review_sha256"])),
        "evidence_seal_sha256": _sha256(str(row["evidence_seal_sha256"])),
        "findings": findings,
        "created_at": _stored_text(row["created_at"]),
    }


def _set_record(row: Any, members: Sequence[Any]) -> dict[str, object]:
    status = _allowed_alias(str(row["status"]), _SET_STATUSES, storage=True)
    seal = str(row["seal_sha256"])
    sealed_at = _stored_text(row["sealed_at"], allow_empty=True)
    required = _positive_int(int(row["required_repository_count"]))
    if status == "collecting" and (seal or sealed_at):
        raise ValueError
    if status == "sealed" and (not _is_sha256(seal) or not sealed_at or len(members) != required):
        raise ValueError
    result_members: list[dict[str, object]] = []
    for expected, member in enumerate(members, 1):
        if int(member["evidence_set_id"]) != int(row["id"]) or int(member["ordinal"]) != expected:
            raise ValueError
        result_members.append({
            "id": _positive_int(int(member["id"])),
            "evidence_set_id": int(row["id"]),
            "repository_alias": _alias(str(member["repository_alias"])),
            "bundle_id": _positive_int(int(member["bundle_id"])),
            "ordinal": expected,
            "created_at": _stored_text(member["created_at"]),
        })
    return {
        "id": _positive_int(int(row["id"])),
        "set_key": _alias(str(row["set_key"])),
        "conversation_key": _alias(str(row["conversation_key"])),
        "required_repository_count": required,
        "status": status,
        "seal_sha256": seal,
        "created_at": _stored_text(row["created_at"]),
        "sealed_at": sealed_at,
        "members": result_members,
    }


def _require_bundle_status(connection: Any, bundle_id: int, status: str) -> Any:
    row = connection.execute(
        "select id, status, seal_sha256 from code_evidence_bundles where id = ?", (bundle_id,)
    ).fetchone()
    if row is None:
        raise KeyError("code_evidence_bundle_not_found")
    if str(row["status"]) != status:
        raise ValueError("code_evidence_state_invalid")
    return row


def _bounded_limit(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100:
        raise ValueError("code_evidence_input_invalid")
    return value


def _require_set_status(connection: Any, evidence_set_id: int, status: str) -> Any:
    row = connection.execute(
        "select * from code_evidence_sets where id = ?", (evidence_set_id,)
    ).fetchone()
    if row is None:
        raise KeyError("code_evidence_set_not_found")
    if str(row["status"]) != status:
        raise ValueError("code_evidence_state_invalid")
    return row


def _alias(value: object) -> str:
    try:
        return validate_audit_alias(value)
    except (TypeError, ValueError):
        raise ValueError("code_evidence_input_invalid") from None


def _allowed_alias(value: object, allowed: frozenset[str], *, storage: bool = False) -> str:
    try:
        if not isinstance(value, str) or value not in allowed:
            raise ValueError
        return value
    except (TypeError, ValueError):
        raise ValueError("code_evidence_storage_invalid" if storage else "code_evidence_input_invalid") from None


def _sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError("code_evidence_input_invalid")
    return value


def _git_sha(value: object) -> str:
    if not isinstance(value, str) or _GIT_SHA.fullmatch(value) is None:
        raise ValueError("code_evidence_input_invalid")
    return value


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _relative_path(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8")) > 512
        or _RELATIVE_PATH.fullmatch(value) is None
        or "." in value.split("/")
        or ".." in value.split("/")
        or ".git" in value.split("/")
        or contains_sensitive_scalar_text(value)
    ):
        raise ValueError("code_evidence_input_invalid")
    return value


def _capabilities(value: Sequence[object]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError("code_evidence_input_invalid")
    result = tuple(_alias(item) for item in value)
    if not result or len(result) > 16 or len(result) != len(set(result)) or result != tuple(sorted(result)):
        raise ValueError("code_evidence_input_invalid")
    return result


def _positive_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("code_evidence_input_invalid")
    return value


def _nonnegative_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("code_evidence_input_invalid")
    return value


def _file_mode(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value != 0o600:
        raise ValueError("code_evidence_input_invalid")
    return value


def _link_count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value != 1:
        raise ValueError("code_evidence_input_invalid")
    return value


def _canonical_mapping(value: Mapping[str, object]) -> str:
    if not isinstance(value, Mapping) or len(value) > 32:
        raise ValueError("code_evidence_input_invalid")
    for key, item in value.items():
        if not isinstance(key, str) or is_sensitive_mapping_key(key) or contains_sensitive_text(key):
            raise ValueError("code_evidence_input_invalid")
        if not _safe_json_value(item, depth=0):
            raise ValueError("code_evidence_input_invalid")
    return _canonical_json(dict(value))


def _canonical_findings(value: Sequence[Mapping[str, object]]) -> str:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence) or len(value) > 32:
        raise ValueError("code_evidence_input_invalid")
    result: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("code_evidence_input_invalid")
        encoded = _canonical_mapping(item)
        parsed = json.loads(encoded)
        if not isinstance(parsed, dict):
            raise ValueError("code_evidence_input_invalid")
        result.append(parsed)
    return _canonical_json(result)


def _safe_json_value(value: object, *, depth: int) -> bool:
    if depth > 4:
        return False
    if value is None or type(value) in {bool, int}:
        return True
    if isinstance(value, str):
        return len(value) <= 512 and not contains_sensitive_scalar_text(value) and not contains_sensitive_text(value)
    if isinstance(value, list):
        return len(value) <= 32 and all(_safe_json_value(item, depth=depth + 1) for item in value)
    if isinstance(value, dict):
        return len(value) <= 32 and all(
            isinstance(key, str)
            and not is_sensitive_mapping_key(key)
            and not contains_sensitive_text(key)
            and _safe_json_value(item, depth=depth + 1)
            for key, item in value.items()
        )
    return False


def _canonical_json(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError):
        raise ValueError("code_evidence_input_invalid") from None
    if len(encoded.encode("utf-8")) > _MAX_JSON_BYTES or contains_sensitive_text(encoded):
        raise ValueError("code_evidence_input_invalid")
    return encoded


def _load_json(value: object) -> object:
    if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_JSON_BYTES or contains_sensitive_text(value):
        raise ValueError
    try:
        return json.loads(value, object_pairs_hook=_reject_duplicate_pairs, parse_constant=_reject_nonfinite)
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError):
        raise ValueError from None


def _reject_duplicate_pairs(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> None:
    raise ValueError


def _stored_text(value: object, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 128 or (not allow_empty and not value) or contains_sensitive_text(value):
        raise ValueError
    return value
