"""Durable, append-only storage for Flux-OPD-Lite structured experience.

Only hashes, identifiers, bounded enums and structured arrays are persisted.
The repository never accepts prompts, model responses, patches, credentials or
commands, and it does not grant any execution capability.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Sequence

from app import database
from app.flux_lite_learning import ExperienceCandidate, ReviewerOpinion, aggregate_opinions


class FluxLiteLearningRepository:
    """Persist reviewer opinions and derived candidates through an injected DB."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], sqlite3.Connection],
    ) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connection_factory = connection_factory
        database.init_db(connection_factory=connection_factory)

    def record_opinion(
        self,
        *,
        run_id: int,
        attempt_id: int,
        opinion: ReviewerOpinion,
    ) -> dict[str, object]:
        normalized_run_id = _positive_id(run_id)
        normalized_attempt_id = _positive_id(attempt_id)
        _require_opinion(opinion)
        opinion_key = _opinion_key(
            run_id=normalized_run_id,
            attempt_id=normalized_attempt_id,
            opinion=opinion,
        )
        with self._connect() as connection:
            connection.execute("begin immediate")
            _insert_opinion(
                connection,
                opinion_key=opinion_key,
                run_id=normalized_run_id,
                attempt_id=normalized_attempt_id,
                opinion=opinion,
            )
            row = connection.execute(
                "select * from flux_lite_reviewer_opinions where opinion_key = ?",
                (opinion_key,),
            ).fetchone()
            if row is None:
                raise ValueError("flux_lite_storage_invalid")
            return _opinion_record(row)

    def record_consensus(
        self,
        *,
        run_id: int,
        attempt_id: int,
        opinions: Sequence[ReviewerOpinion],
        candidate: ExperienceCandidate,
    ) -> dict[str, object]:
        normalized_run_id = _positive_id(run_id)
        normalized_attempt_id = _positive_id(attempt_id)
        if not isinstance(opinions, Sequence) or not opinions:
            raise ValueError("flux_lite_opinions_invalid")
        _require_candidate(candidate)
        try:
            expected = aggregate_opinions(opinions, high_risk=candidate.high_risk)
        except ValueError:
            raise
        if expected != candidate:
            raise ValueError("flux_lite_candidate_mismatch")

        opinion_keys = tuple(
            _opinion_key(
                run_id=normalized_run_id,
                attempt_id=normalized_attempt_id,
                opinion=opinion,
            )
            for opinion in opinions
        )
        with self._connect() as connection:
            connection.execute("begin immediate")
            for opinion, opinion_key in zip(opinions, opinion_keys):
                _require_opinion(opinion)
                _insert_opinion(
                    connection,
                    opinion_key=opinion_key,
                    run_id=normalized_run_id,
                    attempt_id=normalized_attempt_id,
                    opinion=opinion,
                )
            _insert_candidate(
                connection,
                run_id=normalized_run_id,
                attempt_id=normalized_attempt_id,
                candidate=candidate,
                opinion_keys=opinion_keys,
            )
            candidate_key = _candidate_key(
                run_id=normalized_run_id,
                attempt_id=normalized_attempt_id,
                candidate=candidate,
            )
            row = connection.execute(
                "select * from flux_lite_experience_candidates where candidate_key = ?",
                (candidate_key,),
            ).fetchone()
            if row is None:
                raise ValueError("flux_lite_storage_invalid")
            return _candidate_record(row)

    def list_context_candidates(
        self,
        *,
        scope_key: str,
        include_high_risk: bool = False,
    ) -> list[dict[str, object]]:
        normalized_scope = _identifier(scope_key, "scope_key")
        if not isinstance(include_high_risk, bool):
            raise ValueError("flux_lite_risk_invalid")
        with self._connect() as connection:
            rows = connection.execute(
                """
                select * from flux_lite_experience_candidates
                where scope_key = ?
                  and state in ('trial', 'stable')
                  and (? = 1 or (promotion_allowed = 1 and high_risk = 0))
                order by context_weight desc, id
                """,
                (normalized_scope, int(include_high_risk)),
            ).fetchall()
        records: list[dict[str, object]] = []
        seen_candidate_ids: set[str] = set()
        for row in rows:
            record = _candidate_record(row)
            candidate_id = str(record["candidate_id"])
            if candidate_id in seen_candidate_ids:
                continue
            seen_candidate_ids.add(candidate_id)
            records.append(record)
        return records

    def snapshot_for_attempt(self, *, run_id: int, attempt_id: int) -> dict[str, object]:
        normalized_run_id = _positive_id(run_id)
        normalized_attempt_id = _positive_id(attempt_id)
        with self._connect() as connection:
            opinions = connection.execute(
                """
                select * from flux_lite_reviewer_opinions
                where run_id = ? and attempt_id = ? order by id
                """,
                (normalized_run_id, normalized_attempt_id),
            ).fetchall()
            candidates = connection.execute(
                """
                select * from flux_lite_experience_candidates
                where run_id = ? and attempt_id = ? order by id
                """,
                (normalized_run_id, normalized_attempt_id),
            ).fetchall()
        return {
            "run_id": normalized_run_id,
            "attempt_id": normalized_attempt_id,
            "opinions": [_opinion_record(row) for row in opinions],
            "candidates": [_candidate_record(row) for row in candidates],
        }

    def _connect(self) -> "_OwnedConnection":
        return _OwnedConnection(self._connection_factory())


def _insert_opinion(
    connection: sqlite3.Connection,
    *,
    opinion_key: str,
    run_id: int,
    attempt_id: int,
    opinion: ReviewerOpinion,
) -> None:
    try:
        connection.execute(
            """
            insert into flux_lite_reviewer_opinions(
                opinion_key, run_id, attempt_id, reviewer_id, scope_key,
                root_cause, focus_actions_json, verdict, evidence_refs_json, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(opinion_key) do nothing
            """,
            (
                opinion_key,
                run_id,
                attempt_id,
                opinion.reviewer_id,
                opinion.scope_key,
                opinion.root_cause,
                _json_array(opinion.focus_actions),
                opinion.verdict,
                _json_array(opinion.evidence_refs),
                database.now_iso(),
            ),
        )
    except sqlite3.IntegrityError:
        raise ValueError("flux_lite_replay_conflict") from None
    row = connection.execute(
        """
        select reviewer_id, scope_key, root_cause, focus_actions_json,
               verdict, evidence_refs_json
        from flux_lite_reviewer_opinions where opinion_key = ?
        """,
        (opinion_key,),
    ).fetchone()
    expected = (
        opinion.reviewer_id,
        opinion.scope_key,
        opinion.root_cause,
        _json_array(opinion.focus_actions),
        opinion.verdict,
        _json_array(opinion.evidence_refs),
    )
    if row is None or tuple(row) != expected:
        raise ValueError("flux_lite_replay_conflict")


def _insert_candidate(
    connection: sqlite3.Connection,
    *,
    run_id: int,
    attempt_id: int,
    candidate: ExperienceCandidate,
    opinion_keys: Sequence[str],
) -> None:
    opinion_keys_json = _json_array(opinion_keys)
    candidate_key = _candidate_key(
        run_id=run_id,
        attempt_id=attempt_id,
        candidate=candidate,
    )
    try:
        connection.execute(
            """
            insert into flux_lite_experience_candidates(
                candidate_key, candidate_id, run_id, attempt_id, scope_key, root_cause,
                focus_actions_json, reviewer_count, agreement_ratio, conflict_score,
                context_weight, state, promotion_allowed, high_risk,
                opinion_keys_json, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(candidate_key) do nothing
            """,
            (
                candidate_key,
                candidate.candidate_id,
                run_id,
                attempt_id,
                candidate.scope_key,
                candidate.root_cause,
                _json_array(candidate.focus_actions),
                candidate.reviewer_count,
                candidate.agreement_ratio,
                candidate.conflict_score,
                candidate.context_weight,
                candidate.state,
                int(candidate.promotion_allowed),
                int(candidate.high_risk),
                opinion_keys_json,
                database.now_iso(),
            ),
        )
    except sqlite3.IntegrityError:
        raise ValueError("flux_lite_replay_conflict") from None
    row = connection.execute(
        "select * from flux_lite_experience_candidates where candidate_key = ?",
        (candidate_key,),
    ).fetchone()
    expected = _candidate_record_from_value(
        candidate,
        run_id=run_id,
        attempt_id=attempt_id,
        opinion_keys=opinion_keys,
        candidate_key=candidate_key,
    )
    actual = None if row is None else _candidate_record(row)
    if actual is None or any(actual.get(key) != value for key, value in expected.items()):
        raise ValueError("flux_lite_replay_conflict")


def _opinion_record(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "opinion_key": str(row["opinion_key"]),
        "run_id": int(row["run_id"]),
        "attempt_id": int(row["attempt_id"]),
        "reviewer_id": str(row["reviewer_id"]),
        "scope_key": str(row["scope_key"]),
        "root_cause": str(row["root_cause"]),
        "focus_actions": _load_array(row["focus_actions_json"]),
        "verdict": str(row["verdict"]),
        "evidence_refs": _load_array(row["evidence_refs_json"]),
        "created_at": str(row["created_at"]),
    }


def _candidate_record(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "candidate_key": str(row["candidate_key"]),
        "candidate_id": str(row["candidate_id"]),
        "run_id": int(row["run_id"]),
        "attempt_id": int(row["attempt_id"]),
        "scope_key": str(row["scope_key"]),
        "root_cause": str(row["root_cause"]),
        "focus_actions": _load_array(row["focus_actions_json"]),
        "reviewer_count": int(row["reviewer_count"]),
        "agreement_ratio": float(row["agreement_ratio"]),
        "conflict_score": float(row["conflict_score"]),
        "context_weight": float(row["context_weight"]),
        "state": str(row["state"]),
        "promotion_allowed": bool(row["promotion_allowed"]),
        "high_risk": bool(row["high_risk"]),
        "opinion_keys": _load_array(row["opinion_keys_json"]),
        "created_at": str(row["created_at"]),
    }


def _candidate_record_from_value(
    candidate: ExperienceCandidate,
    *,
    run_id: int,
    attempt_id: int,
    opinion_keys: Sequence[str],
    candidate_key: str,
) -> dict[str, object]:
    return {
        "candidate_key": candidate_key,
        "candidate_id": candidate.candidate_id,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "scope_key": candidate.scope_key,
        "root_cause": candidate.root_cause,
        "focus_actions": list(candidate.focus_actions),
        "reviewer_count": candidate.reviewer_count,
        "agreement_ratio": candidate.agreement_ratio,
        "conflict_score": candidate.conflict_score,
        "context_weight": candidate.context_weight,
        "state": candidate.state,
        "promotion_allowed": candidate.promotion_allowed,
        "high_risk": candidate.high_risk,
        "opinion_keys": list(opinion_keys),
    }


def _opinion_key(*, run_id: int, attempt_id: int, opinion: ReviewerOpinion) -> str:
    payload = {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "reviewer_id": opinion.reviewer_id,
        "scope_key": opinion.scope_key,
        "root_cause": opinion.root_cause,
        "focus_actions": list(opinion.focus_actions),
        "verdict": opinion.verdict,
        "evidence_refs": list(opinion.evidence_refs),
    }
    return "flux-opinion-" + hashlib.sha256(_canonical_json(payload)).hexdigest()


def _candidate_key(*, run_id: int, attempt_id: int, candidate: ExperienceCandidate) -> str:
    payload = {
        "run_id": run_id,
        "attempt_id": attempt_id,
        "candidate_id": candidate.candidate_id,
    }
    return "flux-occurrence-" + hashlib.sha256(_canonical_json(payload)).hexdigest()


def _require_opinion(value: object) -> None:
    if not isinstance(value, ReviewerOpinion):
        raise ValueError("flux_lite_opinion_invalid")


def _require_candidate(value: object) -> None:
    if not isinstance(value, ExperienceCandidate):
        raise ValueError("flux_lite_candidate_invalid")
    if value.state not in {"candidate", "trial", "stable", "suspended", "retired"}:
        raise ValueError("flux_lite_candidate_invalid")


def _positive_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("flux_lite_identifier_invalid")
    return value


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError(f"flux_lite_{name}_invalid")
    return value


def _json_array(values: Sequence[str]) -> str:
    if not isinstance(values, Sequence) or any(not isinstance(value, str) for value in values):
        raise ValueError("flux_lite_storage_invalid")
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def _load_array(value: object) -> list[str]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("flux_lite_storage_invalid") from None
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise ValueError("flux_lite_storage_invalid")
    return parsed


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class _OwnedConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection_factory must return sqlite3.Connection")
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        self._connection.__enter__()
        return self._connection

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return bool(self._connection.__exit__(exc_type, exc_value, traceback))
        finally:
            self._connection.close()
