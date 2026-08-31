from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app import database
from app.manager_credential_crypto import (
    CIPHER_VERSION,
    AesGcmCredentialCipher,
    credential_aad,
)
from app.provider_profiles import _reject_sensitive_values
from app.sensitive_text import redact_sensitive_mapping, validate_audit_alias


DEFAULT_LOCAL_SCOPE = ("local", "default")


class CredentialStatus(str, Enum):
    CONFIGURED = "configured"


class CredentialResolutionUnavailable(RuntimeError):
    """Raised without credential details when execution-time resolution is unavailable."""


@dataclass(frozen=True)
class ProviderProfileRecord:
    id: int
    scope_id: int
    scope_type: str
    scope_key: str
    provider: str
    profile_key: str
    display_name: str
    enabled: bool
    connection: dict[str, object]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ProviderImportResult:
    status: str
    imported_count: int


@dataclass(frozen=True)
class _PreparedImportProfile:
    provider: str
    profile_key: str
    display_name: str
    enabled: bool
    connection_json: str


class ManagerProviderRepository:
    """Persist Manager provider configuration without exposing credential values."""

    def __init__(self, cipher: AesGcmCredentialCipher | None = None) -> None:
        database.init_db()
        self._cipher = cipher

    def upsert_profile(
        self,
        *,
        scope_type: str,
        scope_key: str,
        provider: str,
        profile_key: str,
        display_name: str,
        enabled: bool,
        connection: Mapping[str, object],
    ) -> ProviderProfileRecord:
        values = {
            "scope_type": _required_text(scope_type, "scope_type"),
            "scope_key": _required_text(scope_key, "scope_key"),
            "provider": _required_text(provider, "provider"),
            "profile_key": _required_text(profile_key, "profile_key"),
            "display_name": _text(display_name, "display_name"),
        }
        _validate_public_values(values)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        connection_json = _validated_connection_json(values["provider"], connection)
        timestamp = database.now_iso()
        with database.connect() as db:
            db.execute(
                """
                insert into manager_provider_scopes
                    (scope_type, scope_key, display_name, created_at)
                values (?, ?, ?, ?)
                on conflict(scope_type, scope_key) do nothing
                """,
                (values["scope_type"], values["scope_key"], values["scope_key"], timestamp),
            )
            scope = db.execute(
                "select id from manager_provider_scopes where scope_type = ? and scope_key = ?",
                (values["scope_type"], values["scope_key"]),
            ).fetchone()
            if scope is None:  # pragma: no cover - protected by the unique upsert above
                raise RuntimeError("manager provider scope was not created")
            db.execute(
                """
                insert into manager_provider_profiles
                    (scope_id, provider, profile_key, display_name, enabled,
                     connection_json, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(scope_id, provider, profile_key) do update set
                    display_name = excluded.display_name,
                    enabled = excluded.enabled,
                    connection_json = excluded.connection_json,
                    updated_at = excluded.updated_at
                """,
                (
                    int(scope["id"]),
                    values["provider"],
                    values["profile_key"],
                    values["display_name"],
                    int(enabled),
                    connection_json,
                    timestamp,
                    timestamp,
                ),
            )
            row = _select_profile(
                db,
                scope_type=values["scope_type"],
                scope_key=values["scope_key"],
                provider=values["provider"],
                profile_key=values["profile_key"],
            )
        return _profile_record(row)

    def list_profiles(
        self,
        *,
        scope_type: str = DEFAULT_LOCAL_SCOPE[0],
        scope_key: str = DEFAULT_LOCAL_SCOPE[1],
    ) -> list[ProviderProfileRecord]:
        with database.connect() as db:
            rows = db.execute(
                """
                select p.*, s.scope_type, s.scope_key
                from manager_provider_profiles p
                join manager_provider_scopes s on s.id = p.scope_id
                where s.scope_type = ? and s.scope_key = ?
                order by p.provider, p.profile_key
                """,
                (_required_text(scope_type, "scope_type"), _required_text(scope_key, "scope_key")),
            ).fetchall()
        return [_profile_record(row) for row in rows]

    def import_profiles_once(
        self,
        *,
        source_sha256: str,
        profiles: Sequence[Mapping[str, object]],
    ) -> ProviderImportResult:
        """Atomically import prepared legacy Profiles into the empty local scope."""

        digest = _validated_source_sha256(source_sha256)
        prepared = _prepare_import_profiles(profiles)
        timestamp = database.now_iso()
        with database.connect() as db:
            db.execute("begin immediate")
            imported = db.execute(
                "select imported_count from manager_provider_imports where source_sha256 = ?",
                (digest,),
            ).fetchone()
            if imported is not None:
                return ProviderImportResult(
                    status="already_imported",
                    imported_count=int(imported["imported_count"]),
                )

            scope = db.execute(
                "select id from manager_provider_scopes where scope_type = ? and scope_key = ?",
                DEFAULT_LOCAL_SCOPE,
            ).fetchone()
            if scope is not None:
                profile_count = int(
                    db.execute(
                        "select count(*) from manager_provider_profiles where scope_id = ?",
                        (int(scope["id"]),),
                    ).fetchone()[0]
                )
                if profile_count:
                    return ProviderImportResult(status="profiles_exist", imported_count=0)
                scope_id = int(scope["id"])
            else:
                cursor = db.execute(
                    """
                    insert into manager_provider_scopes
                        (scope_type, scope_key, display_name, created_at)
                    values (?, ?, ?, ?)
                    """,
                    (
                        DEFAULT_LOCAL_SCOPE[0],
                        DEFAULT_LOCAL_SCOPE[1],
                        DEFAULT_LOCAL_SCOPE[1],
                        timestamp,
                    ),
                )
                scope_id = int(cursor.lastrowid)

            for profile in prepared:
                db.execute(
                    """
                    insert into manager_provider_profiles
                        (scope_id, provider, profile_key, display_name, enabled,
                         connection_json, created_at, updated_at)
                    values (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scope_id,
                        profile.provider,
                        profile.profile_key,
                        profile.display_name,
                        int(profile.enabled),
                        profile.connection_json,
                        timestamp,
                        timestamp,
                    ),
                )
            db.execute(
                """
                insert into manager_provider_imports
                    (source_sha256, imported_count, status, created_at)
                values (?, ?, ?, ?)
                """,
                (digest, len(prepared), "imported", timestamp),
            )
        return ProviderImportResult(status="imported", imported_count=len(prepared))

    def upsert_credential(self, *, profile_id: int, field: str, plaintext: str) -> CredentialStatus:
        if not isinstance(plaintext, str) or not plaintext:
            raise ValueError("plaintext must be a non-empty string")
        profile = self._profile_by_id(profile_id)
        credential_field = _validated_credential_field(profile.provider, field)
        aad = _profile_credential_aad(profile, credential_field)
        encrypted = self._credential_cipher().encrypt(plaintext, aad=aad)
        timestamp = database.now_iso()
        with database.connect() as db:
            db.execute(
                """
                insert into manager_provider_credentials
                    (profile_id, credential_field, cipher_version, ciphertext, created_at, updated_at)
                values (?, ?, ?, ?, ?, ?)
                on conflict(profile_id, credential_field) do update set
                    cipher_version = excluded.cipher_version,
                    ciphertext = excluded.ciphertext,
                    updated_at = excluded.updated_at
                """,
                (profile.id, credential_field, CIPHER_VERSION, encrypted, timestamp, timestamp),
            )
        return CredentialStatus.CONFIGURED

    def credential_statuses(self, *, profile_id: int) -> dict[str, str]:
        profile = self._profile_by_id(profile_id)
        with database.connect() as db:
            rows = db.execute(
                """
                select credential_field
                from manager_provider_credentials
                where profile_id = ?
                order by credential_field
                """,
                (profile_id,),
            ).fetchall()
        return {
            _validated_credential_field(
                profile.provider,
                row["credential_field"],
            ): CredentialStatus.CONFIGURED.value
            for row in rows
        }

    def profile_status(self, profile_id: int) -> dict[str, object]:
        profile = self._profile_by_id(profile_id)
        return {
            "id": profile.id,
            "scope_type": profile.scope_type,
            "scope_key": profile.scope_key,
            "provider": profile.provider,
            "profile_key": profile.profile_key,
            "display_name": profile.display_name,
            "enabled": profile.enabled,
            "connection": dict(profile.connection),
            "credentials": self.credential_statuses(profile_id=profile.id),
        }

    def resolve_credential_for_authorized_executor(self, *, profile_id: int, field: str) -> str:
        """Keep the repository's public surface unable to reveal plaintext.

        ``ProviderExecutionService`` binds the private resolver to a freshly
        created execution context only after consuming an authorization.  This
        deliberately remains fail-closed for Manager listings, preflight and
        any future HTTP handler that tries to call the repository directly.
        """

        raise CredentialResolutionUnavailable("credential_resolution_unavailable")

    def create_action_plan(
        self,
        *,
        profile_id: int,
        action_type: str,
        target_alias: str,
        parameter_hash: str,
        reviewed_parameter_summary: Mapping[str, object] | None = None,
        requested_by: str,
        created_at: str,
    ) -> dict[str, object]:
        profile = self._profile_by_id(profile_id)
        if not profile.enabled:
            raise PermissionError("provider_profile_disabled")
        values = {
            "action_type": _required_text(action_type, "action_type"),
            "target_alias": _required_text(target_alias, "target_alias"),
            "requested_by": _required_text(requested_by, "requested_by"),
        }
        _validate_public_values(values)
        _validate_database_plan_target(
            provider=profile.provider,
            profile_key=profile.profile_key,
            action_type=values["action_type"],
            target_alias=values["target_alias"],
        )
        digest = _validated_sha256(parameter_hash, "parameter_hash")
        reviewed_summary_json = _safe_mapping_json(
            redact_sensitive_mapping(reviewed_parameter_summary or {}),
            "reviewed_parameter_summary",
        )
        timestamp = _validated_timestamp(created_at, "created_at")
        with database.connect() as db:
            cursor = db.execute(
                """
                insert into manager_provider_action_plans(
                    profile_id, scope_type, scope_key, provider, profile_key,
                    action_type, target_alias, parameter_hash,
                    reviewed_parameter_summary_json, requested_by, state, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?)
                """,
                (
                    profile.id,
                    profile.scope_type,
                    profile.scope_key,
                    profile.provider,
                    profile.profile_key,
                    values["action_type"],
                    values["target_alias"],
                    digest,
                    reviewed_summary_json,
                    values["requested_by"],
                    timestamp,
                ),
            )
            plan_id = int(cursor.lastrowid)
            row = _select_action_plan(db, plan_id)
        return _action_plan_record(row)

    def get_action_plan(self, plan_id: int) -> dict[str, object]:
        with database.connect() as db:
            row = _select_action_plan(db, _positive_int(plan_id, "plan_id"))
        return _action_plan_record(row)

    def list_action_plans(self, *, limit: int = 100) -> list[dict[str, object]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with database.connect() as db:
            rows = db.execute(
                """
                select * from manager_provider_action_plans
                order by id desc limit ?
                """,
                (limit,),
            ).fetchall()
        return [_action_plan_record(row) for row in rows]

    def confirm_action_plan(
        self,
        *,
        plan_id: int,
        actor: str,
        authorization_hash: str,
        confirmed_at: str,
        expires_at: str,
    ) -> dict[str, object]:
        normalized_plan_id = _positive_int(plan_id, "plan_id")
        confirmed_by = _required_text(actor, "actor")
        _validate_public_values({"actor": confirmed_by})
        digest = _validated_sha256(authorization_hash, "authorization_hash")
        confirmed_timestamp = _validated_timestamp(confirmed_at, "confirmed_at")
        expiry_timestamp = _validated_timestamp(expires_at, "expires_at")
        if _parse_timestamp(expiry_timestamp) <= _parse_timestamp(confirmed_timestamp):
            raise ValueError("expires_at must be after confirmed_at")

        with database.connect() as db:
            db.execute("begin immediate")
            row = _select_action_plan(db, normalized_plan_id)
            if str(row["state"]) != "planned":
                raise PermissionError("action_plan_not_planned")
            _validate_current_action_plan_profile(db, row)
            updated = db.execute(
                """
                update manager_provider_action_plans
                set state = 'confirmed', confirmed_by = ?, authorization_hash = ?,
                    confirmed_at = ?, authorization_expires_at = ?
                where id = ? and state = 'planned'
                """,
                (
                    confirmed_by,
                    digest,
                    confirmed_timestamp,
                    expiry_timestamp,
                    normalized_plan_id,
                ),
            )
            if updated.rowcount != 1:  # pragma: no cover - protected by begin immediate
                raise PermissionError("action_plan_not_planned")
            confirmed = _select_action_plan(db, normalized_plan_id)
        return _action_plan_record(confirmed)

    def consume_action_plan(
        self,
        *,
        plan_id: int,
        actor: str,
        authorization_hash: str,
        parameter_hash: str,
        attempted_at: str,
    ) -> dict[str, object]:
        """Atomically consume a matching authorization and record the attempt.

        A caller may invoke an adapter only after this transaction returns
        ``allowed=True``. The stored state is therefore consumed before any
        external effect can begin.
        """

        normalized_plan_id = _positive_int(plan_id, "plan_id")
        attempted_by = _required_text(actor, "actor")
        _validate_public_values({"actor": attempted_by})
        supplied_authorization_hash = (
            _validated_sha256(authorization_hash, "authorization_hash")
            if authorization_hash
            else ""
        )
        supplied_parameter_hash = _validated_sha256(parameter_hash, "parameter_hash")
        attempted_timestamp = _validated_timestamp(attempted_at, "attempted_at")

        with database.connect() as db:
            db.execute("begin immediate")
            row = _select_action_plan(db, normalized_plan_id)
            current_state = str(row["state"])
            next_state = current_state
            allowed = False
            reason = "authorization_required"

            if current_state == "consumed":
                reason = "authorization_reused"
            elif current_state == "expired":
                reason = "authorization_expired"
            elif current_state == "rejected":
                reason = "action_plan_rejected"
            elif current_state == "planned":
                reason = "authorization_required"
            elif _parse_timestamp(str(row["authorization_expires_at"])) <= _parse_timestamp(
                attempted_timestamp
            ):
                next_state = "expired"
                reason = "authorization_expired"
            elif not supplied_authorization_hash:
                next_state = "rejected"
                reason = "authorization_required"
            elif supplied_authorization_hash != str(row["authorization_hash"]):
                next_state = "rejected"
                reason = "authorization_hash_mismatch"
            elif attempted_by != str(row["confirmed_by"]):
                next_state = "rejected"
                reason = "actor_mismatch"
            elif supplied_parameter_hash != str(row["parameter_hash"]):
                next_state = "rejected"
                reason = "parameter_hash_mismatch"
            else:
                next_state = "consumed"
                allowed = True
                reason = "authorized"

            if next_state != current_state:
                rejected_at = attempted_timestamp if next_state in {"expired", "rejected"} else ""
                consumed_at = attempted_timestamp if next_state == "consumed" else ""
                updated = db.execute(
                    """
                    update manager_provider_action_plans
                    set state = ?, rejection_reason = ?, rejected_at = ?, consumed_at = ?
                    where id = ? and state = ?
                    """,
                    (
                        next_state,
                        "" if allowed else reason,
                        rejected_at,
                        consumed_at,
                        normalized_plan_id,
                        current_state,
                    ),
                )
                if updated.rowcount != 1:  # pragma: no cover - protected by begin immediate
                    raise RuntimeError("action plan state changed during consume")

            audit_id = _insert_action_audit(
                db,
                action_plan_id=normalized_plan_id,
                profile_id=int(row["profile_id"]),
                action_type=str(row["action_type"]),
                target_alias=str(row["target_alias"]),
                parameter_hash=supplied_parameter_hash,
                authorization_hash=supplied_authorization_hash,
                status="consumed" if allowed else "rejected",
                result_summary={"reason": reason},
                created_at=attempted_timestamp,
            )
            final_row = _select_action_plan(db, normalized_plan_id)

        return {
            "allowed": allowed,
            "status": str(final_row["state"]),
            "reason": reason,
            "plan_id": normalized_plan_id,
            "audit_id": audit_id,
        }

    def consume_read_action_plan(
        self,
        *,
        plan_id: int,
        actor: str,
        parameter_hash: str,
        attempted_at: str,
    ) -> dict[str, object]:
        """Atomically consume an approval-free, still-governed read plan."""

        normalized_plan_id = _positive_int(plan_id, "plan_id")
        attempted_by = _required_text(actor, "actor")
        _validate_public_values({"actor": attempted_by})
        supplied_parameter_hash = _validated_sha256(parameter_hash, "parameter_hash")
        attempted_timestamp = _validated_timestamp(attempted_at, "attempted_at")

        with database.connect() as db:
            db.execute("begin immediate")
            row = _select_action_plan(db, normalized_plan_id)
            current_state = str(row["state"])
            next_state = current_state
            allowed = False

            if current_state == "consumed":
                reason = "execution_grant_reused"
            elif current_state == "expired":
                reason = "action_plan_expired"
            elif current_state == "rejected":
                reason = "action_plan_rejected"
            elif current_state not in {"planned", "confirmed"}:
                reason = "action_plan_not_executable"
            else:
                _validate_current_action_plan_profile(db, row)
                if attempted_by != str(row["requested_by"]):
                    next_state = "rejected"
                    reason = "actor_mismatch"
                elif supplied_parameter_hash != str(row["parameter_hash"]):
                    next_state = "rejected"
                    reason = "parameter_hash_mismatch"
                else:
                    next_state = "consumed"
                    allowed = True
                    reason = "credential_or_endpoint_authority"

            if next_state != current_state:
                rejected_at = attempted_timestamp if next_state == "rejected" else ""
                consumed_at = attempted_timestamp if next_state == "consumed" else ""
                updated = db.execute(
                    """
                    update manager_provider_action_plans
                    set state = ?, rejection_reason = ?, rejected_at = ?, consumed_at = ?
                    where id = ? and state = ?
                    """,
                    (
                        next_state,
                        "" if allowed else reason,
                        rejected_at,
                        consumed_at,
                        normalized_plan_id,
                        current_state,
                    ),
                )
                if updated.rowcount != 1:  # pragma: no cover - protected by begin immediate
                    raise RuntimeError("action plan state changed during read consume")

            audit_id = _insert_action_audit(
                db,
                action_plan_id=normalized_plan_id,
                profile_id=int(row["profile_id"]),
                action_type=str(row["action_type"]),
                target_alias=str(row["target_alias"]),
                parameter_hash=supplied_parameter_hash,
                authorization_hash="",
                status="consumed" if allowed else "rejected",
                result_summary={"reason": reason},
                created_at=attempted_timestamp,
            )
            final_row = _select_action_plan(db, normalized_plan_id)

        return {
            "allowed": allowed,
            "status": str(final_row["state"]),
            "reason": reason,
            "plan_id": normalized_plan_id,
            "audit_id": audit_id,
        }

    def record_action_plan_rejection(
        self,
        *,
        plan_id: int,
        parameter_hash: str,
        reason: str,
        attempted_at: str,
    ) -> dict[str, object]:
        """Record a fail-closed attempt that did not carry a trusted token object."""

        normalized_plan_id = _positive_int(plan_id, "plan_id")
        supplied_parameter_hash = _validated_sha256(parameter_hash, "parameter_hash")
        safe_reason = _required_text(reason, "reason")
        _validate_public_values({"reason": safe_reason})
        timestamp = _validated_timestamp(attempted_at, "attempted_at")
        with database.connect() as db:
            db.execute("begin immediate")
            row = _select_action_plan(db, normalized_plan_id)
            audit_id = _insert_action_audit(
                db,
                action_plan_id=normalized_plan_id,
                profile_id=int(row["profile_id"]),
                action_type=str(row["action_type"]),
                target_alias=str(row["target_alias"]),
                parameter_hash=supplied_parameter_hash,
                authorization_hash="",
                status="rejected",
                result_summary={"reason": safe_reason},
                created_at=timestamp,
            )
        return {
            "allowed": False,
            "status": str(row["state"]),
            "reason": safe_reason,
            "plan_id": normalized_plan_id,
            "audit_id": audit_id,
        }

    def record_action(
        self,
        *,
        profile_id: int | None,
        action_type: str,
        status: str,
        details: Mapping[str, object],
        authorization_id: str = "",
        target_alias: str = "",
        parameter_hash: str = "",
    ) -> int:
        if profile_id is not None:
            self._profile_by_id(profile_id)
        if not isinstance(details, Mapping):
            raise ValueError("details must be a mapping")
        _reject_sensitive_values(details)
        authorization_hash = (
            "sha256:" + hashlib.sha256(authorization_id.encode("utf-8")).hexdigest()
            if authorization_id
            else ""
        )
        safe_target_alias = _text(target_alias, "target_alias")
        safe_parameter_hash = (
            _validated_sha256(parameter_hash, "parameter_hash") if parameter_hash else ""
        )
        with database.connect() as db:
            return _insert_action_audit(
                db,
                action_plan_id=None,
                profile_id=profile_id,
                action_type=_text(action_type, "action_type"),
                target_alias=safe_target_alias,
                parameter_hash=safe_parameter_hash,
                authorization_hash=authorization_hash,
                status=_text(status, "status"),
                result_summary=details,
                created_at=database.now_iso(),
            )

    def list_action_audits(
        self,
        *,
        action_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")
        parameters: list[object] = []
        where = ""
        if action_type is not None:
            where = "where a.action_type = ?"
            parameters.append(_required_text(action_type, "action_type"))
        parameters.append(limit)
        with database.connect() as db:
            rows = db.execute(
                f"""
                select a.id, a.action_type, a.target_alias, a.parameter_hash,
                       a.status, a.result_summary_json, a.created_at,
                       p.provider, p.profile_key
                from manager_provider_action_audits a
                left join manager_provider_profiles p on p.id = a.profile_id
                {where}
                order by a.id desc
                limit ?
                """,
                parameters,
            ).fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            details = json.loads(str(row["result_summary_json"]))
            if not isinstance(details, Mapping):
                raise ValueError("stored manager action details must be a mapping")
            _reject_sensitive_values(details)
            result.append(
                {
                    "id": int(row["id"]),
                    "action_type": str(row["action_type"]),
                    "target_alias": str(row["target_alias"]),
                    "parameter_hash": str(row["parameter_hash"]),
                    "status": str(row["status"]),
                    "provider": str(row["provider"] or details.get("provider") or ""),
                    "profile_key": str(row["profile_key"] or details.get("profile_key") or ""),
                    "details": dict(details),
                    "created_at": str(row["created_at"]),
                }
            )
        return result

    def record_knowledge_consultation(
        self,
        *,
        query_redacted: str,
        query_hash: str,
        retrieval_status: str,
        citations: Sequence[str],
        model_used: bool,
        scope_type: str = DEFAULT_LOCAL_SCOPE[0],
        scope_key: str = DEFAULT_LOCAL_SCOPE[1],
    ) -> None:
        redacted = _required_text(query_redacted, "query_redacted")
        digest = _validated_prefixed_sha256(query_hash, "query_hash")
        status = _required_text(retrieval_status, "retrieval_status")
        if not isinstance(model_used, bool):
            raise ValueError("model_used must be a boolean")
        if not isinstance(citations, Sequence) or isinstance(citations, (str, bytes)):
            raise ValueError("citations must be a sequence")
        safe_citations = [_required_text(item, "citation") for item in citations]
        _reject_sensitive_values({"query_redacted": redacted, "citations": safe_citations})
        timestamp = database.now_iso()
        with database.connect() as db:
            db.execute(
                """
                insert into manager_provider_scopes
                    (scope_type, scope_key, display_name, created_at)
                values (?, ?, ?, ?)
                on conflict(scope_type, scope_key) do nothing
                """,
                (
                    _required_text(scope_type, "scope_type"),
                    _required_text(scope_key, "scope_key"),
                    _required_text(scope_key, "scope_key"),
                    timestamp,
                ),
            )
            scope = db.execute(
                "select id from manager_provider_scopes where scope_type = ? and scope_key = ?",
                (scope_type, scope_key),
            ).fetchone()
            if scope is None:  # pragma: no cover - protected by upsert
                raise RuntimeError("manager provider scope was not created")
            db.execute(
                """
                insert into manager_knowledge_consultations
                    (scope_id, query_redacted, query_hash, retrieval_status,
                     citations_json, model_used, created_at)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(scope["id"]),
                    redacted,
                    digest,
                    status,
                    json.dumps(safe_citations, ensure_ascii=False, separators=(",", ":")),
                    int(model_used),
                    timestamp,
                ),
            )

    def count_knowledge_consultations(
        self,
        *,
        scope_type: str = DEFAULT_LOCAL_SCOPE[0],
        scope_key: str = DEFAULT_LOCAL_SCOPE[1],
    ) -> int:
        with database.connect() as db:
            row = db.execute(
                """
                select count(*)
                from manager_knowledge_consultations c
                join manager_provider_scopes s on s.id = c.scope_id
                where s.scope_type = ? and s.scope_key = ?
                """,
                (scope_type, scope_key),
            ).fetchone()
        return int(row[0])

    def list_knowledge_consultations(
        self,
        *,
        scope_type: str = DEFAULT_LOCAL_SCOPE[0],
        scope_key: str = DEFAULT_LOCAL_SCOPE[1],
        limit: int = 50,
    ) -> list[dict[str, object]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")
        with database.connect() as db:
            rows = db.execute(
                """
                select c.id, c.query_redacted, c.retrieval_status,
                       c.citations_json, c.model_used, c.created_at
                from manager_knowledge_consultations c
                join manager_provider_scopes s on s.id = c.scope_id
                where s.scope_type = ? and s.scope_key = ?
                order by c.id desc
                limit ?
                """,
                (scope_type, scope_key, limit),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "query_redacted": str(row["query_redacted"]),
                "retrieval_status": str(row["retrieval_status"]),
                "citations": json.loads(str(row["citations_json"])),
                "model_used": bool(row["model_used"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def _profile_by_id(self, profile_id: int) -> ProviderProfileRecord:
        with database.connect() as db:
            row = db.execute(
                """
                select p.*, s.scope_type, s.scope_key
                from manager_provider_profiles p
                join manager_provider_scopes s on s.id = p.scope_id
                where p.id = ?
                """,
                (profile_id,),
            ).fetchone()
        if row is None:
            raise KeyError("manager provider profile not found")
        return _profile_record(row)

    def _credential_cipher(self) -> AesGcmCredentialCipher:
        if self._cipher is None:
            self._cipher = AesGcmCredentialCipher.from_environment()
        return self._cipher


def _select_profile(
    db: Any,
    *,
    scope_type: str,
    scope_key: str,
    provider: str,
    profile_key: str,
) -> Any:
    row = db.execute(
        """
        select p.*, s.scope_type, s.scope_key
        from manager_provider_profiles p
        join manager_provider_scopes s on s.id = p.scope_id
        where s.scope_type = ? and s.scope_key = ? and p.provider = ? and p.profile_key = ?
        """,
        (scope_type, scope_key, provider, profile_key),
    ).fetchone()
    if row is None:  # pragma: no cover - protected by the upsert immediately before this query
        raise RuntimeError("manager provider profile was not created")
    return row


def _prepare_import_profiles(
    profiles: Sequence[Mapping[str, object]],
) -> tuple[_PreparedImportProfile, ...]:
    if not isinstance(profiles, Sequence) or isinstance(profiles, (str, bytes)):
        raise ValueError("profiles must be a sequence")
    prepared: list[_PreparedImportProfile] = []
    expected_fields = {
        "provider",
        "profile_key",
        "display_name",
        "enabled",
        "connection",
    }
    for profile in profiles:
        if not isinstance(profile, Mapping) or set(profile) != expected_fields:
            raise ValueError("import profile fields are invalid")
        enabled = profile["enabled"]
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        public_values = {
            "provider": _required_text(profile["provider"], "provider"),
            "profile_key": _required_text(profile["profile_key"], "profile_key"),
            "display_name": _text(profile["display_name"], "display_name"),
        }
        _validate_public_values(public_values)
        prepared.append(
            _PreparedImportProfile(
                provider=public_values["provider"],
                profile_key=public_values["profile_key"],
                display_name=public_values["display_name"],
                enabled=enabled,
                connection_json=_validated_connection_json(
                    public_values["provider"],
                    profile["connection"],
                ),
            )
        )
    return tuple(prepared)


def _validated_source_sha256(value: object) -> str:
    digest = _required_text(value, "source_sha256").lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("source_sha256 must be a 64-character hexadecimal digest")
    return digest


def _validated_prefixed_sha256(value: object, name: str) -> str:
    digest = _required_text(value, name).lower()
    if not digest.startswith("sha256:"):
        raise ValueError(f"{name} must use sha256 prefix")
    hexadecimal = digest.removeprefix("sha256:")
    if len(hexadecimal) != 64 or any(character not in "0123456789abcdef" for character in hexadecimal):
        raise ValueError(f"{name} must contain a 64-character hexadecimal digest")
    return digest


def _profile_record(row: Any) -> ProviderProfileRecord:
    connection = json.loads(str(row["connection_json"]))
    if not isinstance(connection, dict):
        raise ValueError("stored provider connection must be a mapping")
    public_values = {
        "scope_type": str(row["scope_type"]),
        "scope_key": str(row["scope_key"]),
        "provider": str(row["provider"]),
        "profile_key": str(row["profile_key"]),
        "display_name": str(row["display_name"]),
    }
    _validate_public_values(public_values)
    _safe_connection_json(connection)
    validated_connection = _validate_provider_connection(
        public_values["provider"],
        connection,
    )
    return ProviderProfileRecord(
        id=int(row["id"]),
        scope_id=int(row["scope_id"]),
        scope_type=public_values["scope_type"],
        scope_key=public_values["scope_key"],
        provider=public_values["provider"],
        profile_key=public_values["profile_key"],
        display_name=public_values["display_name"],
        enabled=bool(row["enabled"]),
        connection=validated_connection,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _profile_credential_aad(profile: ProviderProfileRecord, field: str) -> bytes:
    return credential_aad(
        scope_type=profile.scope_type,
        scope_key=profile.scope_key,
        provider=profile.provider,
        profile_key=profile.profile_key,
        field=field,
    )


def _validate_database_plan_target(
    *, provider: object, profile_key: object, action_type: object, target_alias: object
) -> None:
    if provider != "database":
        return
    from app.providers.database_readonly import (
        DATABASE_PROFILE_BOUND_ACTIONS,
        canonical_database_target,
    )

    if action_type in DATABASE_PROFILE_BOUND_ACTIONS and target_alias != canonical_database_target(profile_key):
        raise ValueError("database_target_invalid")


def _validate_current_action_plan_profile(db: Any, action_plan: Any) -> None:
    profile = db.execute(
        "select provider, profile_key, enabled from manager_provider_profiles where id = ?",
        (int(action_plan["profile_id"]),),
    ).fetchone()
    if profile is None:
        raise PermissionError("provider_profile_not_found")
    if not bool(profile["enabled"]):
        raise PermissionError("provider_profile_disabled")
    _validate_database_plan_target(
        provider=profile["provider"],
        profile_key=profile["profile_key"],
        action_type=action_plan["action_type"],
        target_alias=action_plan["target_alias"],
    )


def _select_action_plan(db: Any, plan_id: int) -> Any:
    row = db.execute(
        """
        select id, profile_id, scope_type, scope_key, provider, profile_key,
               action_type, target_alias, parameter_hash, requested_by,
               reviewed_parameter_summary_json,
               confirmed_by, authorization_hash, state, rejection_reason,
               created_at, confirmed_at, authorization_expires_at,
               consumed_at, rejected_at
        from manager_provider_action_plans
        where id = ?
        """,
        (plan_id,),
    ).fetchone()
    if row is None:
        raise KeyError("manager provider action plan not found")
    return row


def _action_plan_record(row: Any) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "profile_id": int(row["profile_id"]),
        "scope_type": str(row["scope_type"]),
        "scope_key": str(row["scope_key"]),
        "provider": str(row["provider"]),
        "profile_key": str(row["profile_key"]),
        "action_type": str(row["action_type"]),
        "target_alias": str(row["target_alias"]),
        "parameter_hash": str(row["parameter_hash"]),
        "reviewed_parameter_summary": _stored_safe_mapping(
            row["reviewed_parameter_summary_json"],
            "reviewed_parameter_summary",
        ),
        "requested_by": str(row["requested_by"]),
        "confirmed_by": str(row["confirmed_by"]),
        "state": str(row["state"]),
        "rejection_reason": str(row["rejection_reason"]),
        "created_at": str(row["created_at"]),
        "confirmed_at": str(row["confirmed_at"]),
        "authorization_expires_at": str(row["authorization_expires_at"]),
        "consumed_at": str(row["consumed_at"]),
        "rejected_at": str(row["rejected_at"]),
    }


def _stored_safe_mapping(value: object, name: str) -> dict[str, object]:
    try:
        payload = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ValueError(f"stored {name} is invalid") from None
    if not isinstance(payload, dict):
        raise ValueError(f"stored {name} is invalid")
    _safe_mapping_json(payload, name)
    return payload


def _insert_action_audit(
    db: Any,
    *,
    action_plan_id: int | None,
    profile_id: int | None,
    action_type: str,
    target_alias: str,
    parameter_hash: str,
    authorization_hash: str,
    status: str,
    result_summary: Mapping[str, object],
    created_at: str,
) -> int:
    safe_action_type = validate_audit_alias(action_type)
    safe_status = validate_audit_alias(status)
    safe_target_alias = validate_audit_alias(target_alias, allow_empty=True)
    safe_parameter_hash = _validate_action_audit_hash(parameter_hash)
    safe_authorization_hash = _validate_action_audit_hash(authorization_hash)
    summary_json = json.dumps(
        redact_sensitive_mapping(result_summary),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    cursor = db.execute(
        """
        insert into manager_provider_action_audits(
            action_plan_id, profile_id, action_type, target_alias,
            parameter_hash, authorization_hash, authorization_id_hash,
            status, result_summary_json, details_json, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            action_plan_id,
            profile_id,
            safe_action_type,
            safe_target_alias,
            safe_parameter_hash,
            safe_authorization_hash,
            safe_authorization_hash,
            safe_status,
            summary_json,
            summary_json,
            created_at,
        ),
    )
    return int(cursor.lastrowid)


def _validate_action_audit_hash(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("provider_audit_input_invalid")
    if value == "":
        return value
    hexadecimal = value[7:]
    if (
        len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in hexadecimal)
    ):
        raise ValueError("provider_audit_input_invalid")
    return value


def _safe_mapping_json(value: Mapping[str, object], name: str) -> str:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    _reject_sensitive_values(value)
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _safe_connection_json(connection: Mapping[str, object]) -> str:
    if _contains_connection_credential_field(connection):
        raise ValueError("sensitive field is not accepted")
    return _safe_mapping_json(connection, "connection")


def _validated_connection_json(provider: str, connection: Mapping[str, object]) -> str:
    _safe_connection_json(connection)
    validated = _validate_provider_connection(provider, connection)
    return _safe_connection_json(validated)


def _validate_provider_connection(
    provider: str,
    connection: Mapping[str, object],
) -> dict[str, str]:
    # Lazy import keeps provider_profiles -> repository compatibility imports acyclic.
    from app.provider_field_schema import validate_provider_connection

    return validate_provider_connection(provider, connection)


def _validated_credential_field(provider: str, field: object) -> str:
    # Lazy import keeps schema ownership in one place without a module import cycle.
    from app.provider_field_schema import PROVIDER_CREDENTIAL_FIELDS

    allowed_fields = PROVIDER_CREDENTIAL_FIELDS.get(provider)
    if allowed_fields is None:
        raise ValueError("manager_provider_repository:unsupported_provider")
    try:
        credential_field = _required_text(field, "field")
    except ValueError:
        raise ValueError("manager_provider_repository:undeclared_credential_field") from None
    if credential_field not in allowed_fields:
        raise ValueError("manager_provider_repository:undeclared_credential_field")
    return credential_field


def _validate_public_values(values: Mapping[str, object]) -> None:
    try:
        _reject_sensitive_values(values)
    except ValueError:
        raise ValueError("manager_provider_repository:sensitive_public_field") from None


def _validated_sha256(value: object, name: str) -> str:
    digest = _required_text(value, name)
    if len(digest) != 71 or not digest.startswith("sha256:"):
        raise ValueError(f"{name} must be a sha256 digest")
    try:
        int(digest[7:], 16)
    except ValueError:
        raise ValueError(f"{name} must be a sha256 digest") from None
    return digest.lower()


def _validated_timestamp(value: object, name: str) -> str:
    timestamp = _parse_timestamp(_required_text(value, name))
    return timestamp.astimezone(timezone.utc).isoformat()


def _parse_timestamp(value: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("timestamp must be valid ISO-8601") from None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return timestamp


def _positive_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _contains_connection_credential_field(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if not normalized.endswith("_ref") and (
                normalized in {"api_key", "apikey", "credential", "credentials"}
                or normalized.endswith("_api_key")
            ):
                return True
            if _contains_connection_credential_field(item):
                return True
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_connection_credential_field(item) for item in value)
    return False


def _required_text(value: object, name: str) -> str:
    text = _text(value, name).strip()
    if not text:
        raise ValueError(f"{name} must be a non-empty string")
    return text


def _text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value
