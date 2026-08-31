from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence


CHANGE_CONTEXT_LAYER_SCHEMA_VERSION = "change-context-layer.v1"
CHANGE_CONTEXT_PACK_SCHEMA_VERSION = "change-context-pack.v1"
CHANGE_CONTEXT_PROJECTION_SCHEMA_VERSION = "change-context-projection.v1"
LAYER_TYPES = ("project_graph", "change_scope", "code_graph", "data_graph")
LAYER_STATUSES = frozenset({"complete", "incomplete", "not_applicable", "stale"})
PACK_STATUSES = frozenset({"collecting", "ready", "blocked", "stale", "superseded"})
GATE_CODES = frozenset(
    {
        "CHANGE_CONTEXT_READY",
        "BLOCKED_CONTEXT_INCOMPLETE",
        "BLOCKED_CONTEXT_STALE",
        "BLOCKED_CONTEXT_CONFLICT",
        "BLOCKED_CONTEXT_SOURCE_UNAVAILABLE",
        "BLOCKED_CONTEXT_HASH_MISMATCH",
        "BLOCKED_CONTEXT_PROJECTION_BUDGET",
        "BLOCKED_CONTEXT_VERSION_MISMATCH",
    }
)
PROJECTION_ROLES = frozenset({"manager", "analysis", "implementation", "review", "knowledge_answer"})
MCP_EVIDENCE_RECEIPT_SCHEMA_VERSION = "mcp-evidence-receipt.v1"

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_LAYER_ID = re.compile(r"ccl:sha256:[0-9a-f]{64}\Z")
_PACK_ID = re.compile(r"ccp:sha256:[0-9a-f]{64}\Z")
_REFERENCE = re.compile(
    r"(?:(?:evidence|artifact)://[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,511}|"
    r"mcp-evidence:[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,511})\Z"
)
_RULE_ID = re.compile(r"[A-Z][A-Z0-9-]{2,63}\Z")
_VOLATILE_KEYS = frozenset(
    {"created_at", "updated_at", "collected_at", "collection_time", "rendered_at", "audit_timestamp"}
)
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "dsn",
        "database_url",
        "private_key",
        "secret",
        "raw_payload",
        "raw_envelope",
        "business_rows",
        "raw_rows",
    }
)
_SENSITIVE_VALUE = re.compile(
    r"(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bBearer\s+[A-Za-z0-9._~+/=-]+|jdbc:[a-z]+:|postgres(?:ql)?://[^\s]+)",
    re.IGNORECASE,
)


def canonical_json_bytes(value: object) -> bytes:
    normalized = _normalize_semantic(value)
    try:
        return json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("change_context_payload_invalid") from exc


def content_hash(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def layer_id(value: object) -> str:
    return "ccl:" + content_hash(value)


def pack_id(value: object) -> str:
    return "ccp:" + content_hash(value)


@dataclass(frozen=True)
class EvidenceReference:
    ref: str
    kind: str
    source: str
    content_hash: str

    def __post_init__(self) -> None:
        _require_reference(self.ref)
        _require_name(self.kind, "change_context_evidence_kind_invalid")
        _require_name(self.source, "change_context_evidence_source_invalid")
        _require_hash(self.content_hash)

    def to_dict(self) -> dict[str, str]:
        return {
            "ref": self.ref,
            "kind": self.kind,
            "source": self.source,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "EvidenceReference":
        data = _exact_mapping(value, {"ref", "kind", "source", "content_hash"})
        return cls(
            ref=_string(data["ref"]),
            kind=_string(data["kind"]),
            source=_string(data["source"]),
            content_hash=_string(data["content_hash"]),
        )


@dataclass(frozen=True)
class McpEvidenceReceipt:
    schema_version: str
    execution_kind: str
    capability: str
    provider: str
    request_id: str
    source_identity: str
    source_version: str
    payload_hash: str
    evidence_refs: tuple[str, ...]
    freshness_status: str
    freshness_expires_at: str
    collected_at: str

    def __post_init__(self) -> None:
        if self.schema_version != MCP_EVIDENCE_RECEIPT_SCHEMA_VERSION:
            raise ValueError("mcp_evidence_receipt_version_invalid")
        if self.execution_kind != "mcp":
            raise ValueError("mcp_evidence_receipt_execution_kind_invalid")
        _require_name(self.capability, "mcp_evidence_receipt_capability_invalid")
        _require_name(self.provider, "mcp_evidence_receipt_provider_invalid")
        _require_name(self.request_id, "mcp_evidence_receipt_request_id_invalid")
        _require_text(self.source_identity, "mcp_evidence_receipt_source_invalid")
        _require_text(self.source_version, "mcp_evidence_receipt_source_version_invalid")
        _require_hash(self.payload_hash)
        _validate_references(self.evidence_refs)
        if self.freshness_status not in {"fresh", "stale", "unknown", "not_applicable"}:
            raise ValueError("mcp_evidence_receipt_freshness_invalid")
        _require_timestamp(self.freshness_expires_at, "mcp_evidence_receipt_expiry_invalid", allow_empty=True)
        _require_timestamp(self.collected_at, "mcp_evidence_receipt_collected_at_invalid")
        canonical_json_bytes(self.identity_payload())

    @property
    def is_current(self) -> bool:
        return self.freshness_status == "fresh"

    def identity_payload(self) -> dict[str, object]:
        """Semantic receipt identity; collection time is audit-only metadata."""
        return {
            "schema_version": self.schema_version,
            "execution_kind": self.execution_kind,
            "capability": self.capability,
            "provider": self.provider,
            "request_id": self.request_id,
            "source_identity": self.source_identity,
            "source_version": self.source_version,
            "payload_hash": self.payload_hash,
            "evidence_refs": list(self.evidence_refs),
            "freshness_status": self.freshness_status,
            "freshness_expires_at": self.freshness_expires_at,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.identity_payload(), "collected_at": self.collected_at}

    @classmethod
    def from_capability_result(cls, result: object) -> "McpEvidenceReceipt":
        from app.capability_contracts import CapabilityResult

        if not isinstance(result, CapabilityResult):
            raise ValueError("mcp_evidence_receipt_result_invalid")
        if result.status != "success" or result.changed:
            raise ValueError("mcp_evidence_receipt_result_not_readonly_success")
        audit = result.audit
        if audit.get("execution_kind") != "mcp":
            raise ValueError("mcp_evidence_receipt_execution_kind_invalid")
        evidence_refs = tuple(
            str(item.get("ref") or "")
            for item in result.evidence
            if isinstance(item, Mapping) and str(item.get("ref") or "")
        )
        return cls(
            schema_version=MCP_EVIDENCE_RECEIPT_SCHEMA_VERSION,
            execution_kind="mcp",
            capability=result.capability,
            provider=result.provider,
            request_id=result.request_id,
            source_identity=str(audit.get("source_identity") or ""),
            source_version=str(audit.get("source_version") or ""),
            payload_hash=content_hash(result.data),
            evidence_refs=evidence_refs,
            freshness_status=str(audit.get("freshness_status") or "unknown"),
            freshness_expires_at=str(audit.get("freshness_expires_at") or ""),
            collected_at=str(audit.get("collected_at") or ""),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "McpEvidenceReceipt":
        data = _exact_mapping(
            value,
            {
                "schema_version", "execution_kind", "capability", "provider", "request_id",
                "source_identity", "source_version", "payload_hash", "evidence_refs",
                "freshness_status", "freshness_expires_at", "collected_at",
            },
        )
        return cls(
            schema_version=_string(data["schema_version"]),
            execution_kind=_string(data["execution_kind"]),
            capability=_string(data["capability"]),
            provider=_string(data["provider"]),
            request_id=_string(data["request_id"]),
            source_identity=_string(data["source_identity"]),
            source_version=_string(data["source_version"]),
            payload_hash=_string(data["payload_hash"]),
            evidence_refs=_string_tuple(data["evidence_refs"]),
            freshness_status=_string(data["freshness_status"]),
            freshness_expires_at=_string(data["freshness_expires_at"]),
            collected_at=_string(data["collected_at"]),
        )


@dataclass(frozen=True)
class ChangeContextLayer:
    schema_version: str
    layer_type: str
    layer_id: str
    status: str
    content_hash: str
    source_fingerprint: str
    artifact_ref: str
    evidence_refs: tuple[str, ...]
    policy_rule_ids: tuple[str, ...]
    blockers: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != CHANGE_CONTEXT_LAYER_SCHEMA_VERSION:
            raise ValueError("change_context_layer_version_invalid")
        if self.layer_type not in LAYER_TYPES:
            raise ValueError("change_context_layer_type_invalid")
        if self.status not in LAYER_STATUSES:
            raise ValueError("change_context_layer_status_invalid")
        _require_hash(self.content_hash)
        _require_hash(self.source_fingerprint)
        _require_reference(self.artifact_ref)
        _validate_references(self.evidence_refs)
        _validate_rule_ids(self.policy_rule_ids)
        _validate_text_tuple(self.blockers, "change_context_layer_blockers_invalid", allow_empty=True)
        if self.status in {"complete", "not_applicable"} and self.blockers:
            raise ValueError("change_context_layer_complete_with_blockers")
        if self.status in {"incomplete", "stale"} and not self.blockers:
            raise ValueError("change_context_layer_missing_blocker")
        if self.status == "not_applicable" and self.layer_type != "data_graph":
            raise ValueError("change_context_layer_not_applicable_invalid")
        if not _LAYER_ID.fullmatch(self.layer_id):
            raise ValueError("change_context_layer_id_invalid")
        if self.layer_id != layer_id(self.identity_payload()):
            raise ValueError("change_context_layer_id_mismatch")

    @classmethod
    def create(
        cls,
        *,
        layer_type: str,
        status: str,
        payload: Mapping[str, object],
        source_fingerprint: str,
        artifact_ref: str,
        evidence_refs: Sequence[str],
        policy_rule_ids: Sequence[str],
        blockers: Sequence[str],
    ) -> "ChangeContextLayer":
        payload_hash = content_hash(payload)
        identity = {
            "schema_version": CHANGE_CONTEXT_LAYER_SCHEMA_VERSION,
            "layer_type": layer_type,
            "status": status,
            "content_hash": payload_hash,
            "source_fingerprint": source_fingerprint,
            "artifact_ref": artifact_ref,
            "evidence_refs": list(evidence_refs),
            "policy_rule_ids": list(policy_rule_ids),
            "blockers": list(blockers),
        }
        return cls(
            schema_version=CHANGE_CONTEXT_LAYER_SCHEMA_VERSION,
            layer_type=layer_type,
            layer_id=layer_id(identity),
            status=status,
            content_hash=payload_hash,
            source_fingerprint=source_fingerprint,
            artifact_ref=artifact_ref,
            evidence_refs=tuple(evidence_refs),
            policy_rule_ids=tuple(policy_rule_ids),
            blockers=tuple(blockers),
        )

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "layer_type": self.layer_type,
            "status": self.status,
            "content_hash": self.content_hash,
            "source_fingerprint": self.source_fingerprint,
            "artifact_ref": self.artifact_ref,
            "evidence_refs": list(self.evidence_refs),
            "policy_rule_ids": list(self.policy_rule_ids),
            "blockers": list(self.blockers),
        }

    def to_dict(self) -> dict[str, object]:
        return {"layer_id": self.layer_id, **self.identity_payload()}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ChangeContextLayer":
        fields = {
            "schema_version", "layer_type", "layer_id", "status", "content_hash",
            "source_fingerprint", "artifact_ref", "evidence_refs", "policy_rule_ids", "blockers",
        }
        data = _exact_mapping(value, fields)
        return cls(
            schema_version=_string(data["schema_version"]),
            layer_type=_string(data["layer_type"]),
            layer_id=_string(data["layer_id"]),
            status=_string(data["status"]),
            content_hash=_string(data["content_hash"]),
            source_fingerprint=_string(data["source_fingerprint"]),
            artifact_ref=_string(data["artifact_ref"]),
            evidence_refs=_string_tuple(data["evidence_refs"]),
            policy_rule_ids=_string_tuple(data["policy_rule_ids"]),
            blockers=_string_tuple(data["blockers"]),
        )


@dataclass(frozen=True)
class TaskBinding:
    provider: str
    ticket_id: str
    requirement_revision: str
    request_hash: str

    def __post_init__(self) -> None:
        _require_name(self.provider, "change_context_task_provider_invalid")
        _require_name(self.ticket_id, "change_context_task_ticket_invalid")
        _require_text(self.requirement_revision, "change_context_task_revision_invalid")
        _require_hash(self.request_hash)

    def to_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "ticket_id": self.ticket_id,
            "requirement_revision": self.requirement_revision,
            "request_hash": self.request_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TaskBinding":
        data = _exact_mapping(value, {"provider", "ticket_id", "requirement_revision", "request_hash"})
        return cls(*(_string(data[name]) for name in ("provider", "ticket_id", "requirement_revision", "request_hash")))


@dataclass(frozen=True)
class ChangeContextGateResult:
    status: str
    code: str
    missing: tuple[str, ...]
    conflicts: tuple[str, ...]
    blockers: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in {"ready", "blocked"} or self.code not in GATE_CODES:
            raise ValueError("change_context_gate_invalid")
        _validate_text_tuple(self.missing, "change_context_gate_missing_invalid", allow_empty=True)
        _validate_text_tuple(self.conflicts, "change_context_gate_conflicts_invalid", allow_empty=True)
        _validate_text_tuple(self.blockers, "change_context_gate_blockers_invalid", allow_empty=True)
        if self.status == "ready":
            if self.code != "CHANGE_CONTEXT_READY" or any((self.missing, self.conflicts, self.blockers)):
                raise ValueError("change_context_gate_ready_invalid")
        elif self.code == "CHANGE_CONTEXT_READY" or not any((self.missing, self.conflicts, self.blockers)):
            raise ValueError("change_context_gate_blocked_invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "code": self.code,
            "missing": list(self.missing),
            "conflicts": list(self.conflicts),
            "blockers": list(self.blockers),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ChangeContextGateResult":
        data = _exact_mapping(value, {"status", "code", "missing", "conflicts", "blockers"})
        return cls(
            _string(data["status"]),
            _string(data["code"]),
            _string_tuple(data["missing"]),
            _string_tuple(data["conflicts"]),
            _string_tuple(data["blockers"]),
        )


@dataclass(frozen=True)
class ChangeContextPack:
    schema_version: str
    pack_id: str
    pack_version: int
    status: str
    task_binding: TaskBinding
    required_layers: tuple[str, ...]
    layers: tuple[ChangeContextLayer, ...]
    gate: ChangeContextGateResult
    supersedes_pack_id: str

    def __post_init__(self) -> None:
        if self.schema_version != CHANGE_CONTEXT_PACK_SCHEMA_VERSION:
            raise ValueError("change_context_pack_version_invalid")
        if isinstance(self.pack_version, bool) or not isinstance(self.pack_version, int) or self.pack_version < 1:
            raise ValueError("change_context_pack_number_invalid")
        if self.status not in PACK_STATUSES:
            raise ValueError("change_context_pack_status_invalid")
        if not isinstance(self.task_binding, TaskBinding) or not isinstance(self.gate, ChangeContextGateResult):
            raise ValueError("change_context_pack_nested_contract_invalid")
        if tuple(dict.fromkeys(self.required_layers)) != self.required_layers:
            raise ValueError("change_context_pack_required_layers_invalid")
        if any(item not in LAYER_TYPES for item in self.required_layers):
            raise ValueError("change_context_pack_required_layers_invalid")
        if not all(item in self.required_layers for item in LAYER_TYPES[:3]):
            raise ValueError("change_context_pack_base_layer_missing")
        if not isinstance(self.layers, tuple) or len(self.layers) != len(LAYER_TYPES):
            raise ValueError("change_context_pack_layers_invalid")
        by_type = {item.layer_type: item for item in self.layers if isinstance(item, ChangeContextLayer)}
        if len(by_type) != len(LAYER_TYPES) or set(by_type) != set(LAYER_TYPES):
            raise ValueError("change_context_pack_layers_invalid")
        if "data_graph" not in self.required_layers and by_type["data_graph"].status != "not_applicable":
            raise ValueError("change_context_pack_data_layer_invalid")
        if self.status == "ready":
            if self.gate.status != "ready" or any(by_type[name].status != "complete" for name in self.required_layers):
                raise ValueError("change_context_pack_ready_invalid")
        elif self.status == "blocked" and self.gate.status != "blocked":
            raise ValueError("change_context_pack_blocked_invalid")
        if self.pack_version == 1 and self.supersedes_pack_id:
            raise ValueError("change_context_pack_supersession_invalid")
        if self.pack_version > 1 and not _PACK_ID.fullmatch(self.supersedes_pack_id):
            raise ValueError("change_context_pack_supersession_invalid")
        if not _PACK_ID.fullmatch(self.pack_id):
            raise ValueError("change_context_pack_id_invalid")
        if self.pack_id != pack_id(self.identity_payload()):
            raise ValueError("change_context_pack_id_mismatch")

    @classmethod
    def create(
        cls,
        *,
        pack_version: int,
        status: str,
        task_binding: TaskBinding,
        required_layers: Sequence[str],
        layers: Sequence[ChangeContextLayer],
        gate: ChangeContextGateResult,
        supersedes_pack_id: str = "",
    ) -> "ChangeContextPack":
        identity = {
            "schema_version": CHANGE_CONTEXT_PACK_SCHEMA_VERSION,
            "pack_version": pack_version,
            "status": status,
            "task_binding": task_binding.to_dict(),
            "required_layers": list(required_layers),
            "layers": [item.to_dict() for item in layers],
            "gate": gate.to_dict(),
            "supersedes_pack_id": supersedes_pack_id,
        }
        return cls(
            CHANGE_CONTEXT_PACK_SCHEMA_VERSION,
            pack_id(identity),
            pack_version,
            status,
            task_binding,
            tuple(required_layers),
            tuple(layers),
            gate,
            supersedes_pack_id,
        )

    @classmethod
    def create_from_dict(cls, value: Mapping[str, object]) -> "ChangeContextPack":
        data = _exact_mapping(
            value,
            {"schema_version", "pack_id", "pack_version", "status", "task_binding", "required_layers", "layers", "gate", "supersedes_pack_id"},
        )
        if _string(data["schema_version"]) != CHANGE_CONTEXT_PACK_SCHEMA_VERSION:
            raise ValueError("change_context_pack_version_invalid")
        return cls.create(
            pack_version=_integer(data["pack_version"]),
            status=_string(data["status"]),
            task_binding=TaskBinding.from_dict(_mapping(data["task_binding"])),
            required_layers=_string_tuple(data["required_layers"]),
            layers=tuple(ChangeContextLayer.from_dict(item) for item in _mapping_sequence(data["layers"])),
            gate=ChangeContextGateResult.from_dict(_mapping(data["gate"])),
            supersedes_pack_id=_string(data["supersedes_pack_id"]),
        )

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "pack_version": self.pack_version,
            "status": self.status,
            "task_binding": self.task_binding.to_dict(),
            "required_layers": list(self.required_layers),
            "layers": [item.to_dict() for item in self.layers],
            "gate": self.gate.to_dict(),
            "supersedes_pack_id": self.supersedes_pack_id,
        }

    def to_dict(self) -> dict[str, object]:
        return {"pack_id": self.pack_id, **self.identity_payload()}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ChangeContextPack":
        data = _exact_mapping(
            value,
            {"schema_version", "pack_id", "pack_version", "status", "task_binding", "required_layers", "layers", "gate", "supersedes_pack_id"},
        )
        return cls(
            schema_version=_string(data["schema_version"]),
            pack_id=_string(data["pack_id"]),
            pack_version=_integer(data["pack_version"]),
            status=_string(data["status"]),
            task_binding=TaskBinding.from_dict(_mapping(data["task_binding"])),
            required_layers=_string_tuple(data["required_layers"]),
            layers=tuple(ChangeContextLayer.from_dict(item) for item in _mapping_sequence(data["layers"])),
            gate=ChangeContextGateResult.from_dict(_mapping(data["gate"])),
            supersedes_pack_id=_string(data["supersedes_pack_id"]),
        )


@dataclass(frozen=True)
class ChangeContextProjection:
    schema_version: str
    pack_id: str
    role: str
    tier0: Mapping[str, Any]
    tier1: Mapping[str, Any]
    opened_evidence_refs: tuple[str, ...]
    projection_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != CHANGE_CONTEXT_PROJECTION_SCHEMA_VERSION:
            raise ValueError("change_context_projection_version_invalid")
        if not _PACK_ID.fullmatch(self.pack_id) or self.role not in PROJECTION_ROLES:
            raise ValueError("change_context_projection_identity_invalid")
        tier0 = _freeze_json(_json_mapping(self.tier0))
        tier1 = _freeze_json(_json_mapping(self.tier1))
        object.__setattr__(self, "tier0", tier0)
        object.__setattr__(self, "tier1", tier1)
        _validate_references(self.opened_evidence_refs)
        _require_hash(self.projection_hash)
        if self.projection_hash != content_hash(self.identity_payload()):
            raise ValueError("change_context_projection_hash_mismatch")

    @classmethod
    def create(
        cls,
        *,
        pack_id: str,
        role: str,
        tier0: Mapping[str, object],
        tier1: Mapping[str, object],
        opened_evidence_refs: Sequence[str],
    ) -> "ChangeContextProjection":
        identity = {
            "schema_version": CHANGE_CONTEXT_PROJECTION_SCHEMA_VERSION,
            "pack_id": pack_id,
            "role": role,
            "tier0": _json_mapping(tier0),
            "tier1": _json_mapping(tier1),
            "opened_evidence_refs": list(opened_evidence_refs),
        }
        return cls(
            CHANGE_CONTEXT_PROJECTION_SCHEMA_VERSION,
            pack_id,
            role,
            identity["tier0"],
            identity["tier1"],
            tuple(opened_evidence_refs),
            content_hash(identity),
        )

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "pack_id": self.pack_id,
            "role": self.role,
            "tier0": _thaw_json(self.tier0),
            "tier1": _thaw_json(self.tier1),
            "opened_evidence_refs": list(self.opened_evidence_refs),
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.identity_payload(), "projection_hash": self.projection_hash}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ChangeContextProjection":
        data = _exact_mapping(
            value,
            {"schema_version", "pack_id", "role", "tier0", "tier1", "opened_evidence_refs", "projection_hash"},
        )
        return cls(
            _string(data["schema_version"]),
            _string(data["pack_id"]),
            _string(data["role"]),
            _json_mapping(_mapping(data["tier0"])),
            _json_mapping(_mapping(data["tier1"])),
            _string_tuple(data["opened_evidence_refs"]),
            _string(data["projection_hash"]),
        )


def _normalize_semantic(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and _SENSITIVE_VALUE.search(value):
            raise ValueError("change_context_sensitive_value")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("change_context_payload_invalid")
        return value
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or any(ord(char) < 32 for char in key):
                raise ValueError("change_context_key_invalid")
            lowered = key.casefold()
            if lowered in _SENSITIVE_KEYS:
                raise ValueError("change_context_sensitive_key")
            if lowered in _VOLATILE_KEYS:
                continue
            result[key] = _normalize_semantic(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_normalize_semantic(item) for item in value]
    raise ValueError("change_context_payload_invalid")


class _FrozenDict(dict[str, Any]):
    def _immutable(self, *args: object, **kwargs: object) -> None:
        raise TypeError("change_context_mapping_is_immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        frozen = _FrozenDict()
        dict.update(frozen, {str(key): _freeze_json(item) for key, item in value.items()})
        return frozen
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(item) for item in value]
    return value


def _json_mapping(value: Mapping[str, object]) -> dict[str, Any]:
    normalized = _normalize_semantic(value)
    if not isinstance(normalized, dict):
        raise ValueError("change_context_mapping_invalid")
    return json.loads(json.dumps(normalized, ensure_ascii=False, sort_keys=True, allow_nan=False))


def _exact_mapping(value: object, fields: set[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields or any(not isinstance(key, str) for key in value):
        raise ValueError("change_context_fields_invalid")
    return value


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("change_context_mapping_invalid")
    return value


def _mapping_sequence(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("change_context_sequence_invalid")
    return tuple(_mapping(item) for item in value)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("change_context_string_invalid")
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("change_context_integer_invalid")
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise ValueError("change_context_sequence_invalid")
    return tuple(value)


def _require_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or any(ord(char) < 32 for char in value):
        raise ValueError(code)
    if _SENSITIVE_VALUE.search(value):
        raise ValueError("change_context_sensitive_value")
    return value


def _require_name(value: object, code: str) -> str:
    text = _require_text(value, code)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", text):
        raise ValueError(code)
    return text


def _require_hash(value: object) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError("change_context_hash_invalid")
    return value


def _require_timestamp(value: object, code: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ValueError(code)
    if not value and allow_empty:
        return value
    if not value.endswith("Z"):
        raise ValueError(code)
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(code) from exc
    return value


def _require_reference(value: object) -> str:
    if not isinstance(value, str) or not _REFERENCE.fullmatch(value) or ".." in value:
        raise ValueError("change_context_reference_invalid")
    return value


def _validate_references(values: object) -> None:
    if not isinstance(values, tuple) or len(set(values)) != len(values):
        raise ValueError("change_context_references_invalid")
    for value in values:
        _require_reference(value)


def _validate_rule_ids(values: object) -> None:
    if not isinstance(values, tuple) or not values or len(set(values)) != len(values):
        raise ValueError("change_context_policy_rules_invalid")
    if any(not isinstance(value, str) or not _RULE_ID.fullmatch(value) for value in values):
        raise ValueError("change_context_policy_rules_invalid")


def _validate_text_tuple(values: object, code: str, *, allow_empty: bool) -> None:
    if not isinstance(values, tuple) or (not allow_empty and not values) or len(set(values)) != len(values):
        raise ValueError(code)
    for value in values:
        _require_text(value, code)
