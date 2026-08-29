from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Mapping


REQUEST_SCHEMA_VERSION = "his-capability-request.v1"
RESULT_SCHEMA_VERSION = "his-capability-result.v1"
MODES = frozenset({"preview", "apply"})
RESULT_STATUSES = frozenset(
    {"success", "blocked", "failed", "partial", "unsupported"}
)


class CapabilityContractError(ValueError):
    pass


class MutationLevel(IntEnum):
    L0 = 0
    L1 = 1
    L2 = 2
    L3 = 3
    L4 = 4
    L5 = 5


@dataclass(frozen=True)
class CapabilityAuthorization:
    explicit: bool
    scope: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: Any) -> "CapabilityAuthorization":
        data = _object_with_exact_fields(payload, {"explicit", "scope"}, "authorization")
        explicit = data["explicit"]
        if not isinstance(explicit, bool):
            raise CapabilityContractError("authorization.explicit 必须是布尔值。")
        scope = _string_tuple(data["scope"], "authorization.scope")
        return cls(explicit=explicit, scope=scope)

    def to_dict(self) -> dict[str, Any]:
        return {"explicit": self.explicit, "scope": list(self.scope)}


@dataclass(frozen=True)
class CapabilityRequest:
    request_id: str
    capability: str
    provider: str
    mode: str
    mutation_level: MutationLevel
    authorization: CapabilityAuthorization
    input: Mapping[str, Any]
    context: Mapping[str, Any]

    @classmethod
    def from_dict(cls, payload: Any) -> "CapabilityRequest":
        data = _object_with_exact_fields(
            payload,
            {
                "schema_version", "request_id", "capability", "provider", "mode",
                "mutation_level", "authorization", "input", "context",
            },
            "请求",
        )
        _require_schema_version(data["schema_version"], REQUEST_SCHEMA_VERSION)
        mode = _required_text(data["mode"], "mode")
        if mode not in MODES:
            raise CapabilityContractError("mode 只能为 preview 或 apply。")
        return cls(
            request_id=_required_text(data["request_id"], "request_id"),
            capability=_required_text(data["capability"], "capability"),
            provider=_required_text(data["provider"], "provider"),
            mode=mode,
            mutation_level=_mutation_level(data["mutation_level"]),
            authorization=CapabilityAuthorization.from_dict(data["authorization"]),
            input=_mapping(data["input"], "input"),
            context=_mapping(data["context"], "context"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REQUEST_SCHEMA_VERSION,
            "request_id": self.request_id,
            "capability": self.capability,
            "provider": self.provider,
            "mode": self.mode,
            "mutation_level": self.mutation_level.name,
            "authorization": self.authorization.to_dict(),
            "input": dict(self.input),
            "context": dict(self.context),
        }


@dataclass(frozen=True)
class CapabilityResult:
    request_id: str
    capability: str
    provider: str
    status: str
    mutation_level: MutationLevel
    changed: bool
    summary: str
    data: Mapping[str, Any]
    evidence: tuple[Mapping[str, Any], ...]
    warnings: tuple[str, ...]
    blockers: tuple[str, ...]
    audit: Mapping[str, Any]

    @classmethod
    def from_dict(
        cls, payload: Any, *, request: CapabilityRequest | None = None
    ) -> "CapabilityResult":
        data = _object_with_exact_fields(
            payload,
            {
                "schema_version", "request_id", "capability", "provider", "status",
                "mutation_level", "changed", "summary", "data", "evidence", "warnings",
                "blockers", "audit",
            },
            "结果",
        )
        _require_schema_version(data["schema_version"], RESULT_SCHEMA_VERSION)
        request_id = _required_text(data["request_id"], "request_id")
        if request is not None and request_id != request.request_id:
            raise CapabilityContractError("结果 request_id 必须与请求一致。")
        status = _required_text(data["status"], "status")
        if status not in RESULT_STATUSES:
            raise CapabilityContractError(
                "status 只能为 success、blocked、failed、partial 或 unsupported。"
            )
        changed = data["changed"]
        if not isinstance(changed, bool):
            raise CapabilityContractError("changed 必须是布尔值。")
        audit = _mapping(data["audit"], "audit")
        if changed and not audit:
            raise CapabilityContractError("changed=true 时 audit 不能为空。")
        return cls(
            request_id=request_id,
            capability=_required_text(data["capability"], "capability"),
            provider=_required_text(data["provider"], "provider"),
            status=status,
            mutation_level=_mutation_level(data["mutation_level"]),
            changed=changed,
            summary=_text(data["summary"], "summary"),
            data=_mapping(data["data"], "data"),
            evidence=_mapping_tuple(data["evidence"], "evidence"),
            warnings=_string_tuple(data["warnings"], "warnings"),
            blockers=_string_tuple(data["blockers"], "blockers"),
            audit=audit,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RESULT_SCHEMA_VERSION,
            "request_id": self.request_id,
            "capability": self.capability,
            "provider": self.provider,
            "status": self.status,
            "mutation_level": self.mutation_level.name,
            "changed": self.changed,
            "summary": self.summary,
            "data": dict(self.data),
            "evidence": [dict(item) for item in self.evidence],
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
            "audit": dict(self.audit),
        }


def _object_with_exact_fields(payload: Any, fields: set[str], label: str) -> Mapping[str, Any]:
    data = _mapping(payload, label)
    missing = fields - data.keys()
    unexpected = data.keys() - fields
    if missing:
        raise CapabilityContractError(f"{label} 缺少字段：{', '.join(sorted(missing))}。")
    if unexpected:
        raise CapabilityContractError(f"{label} 存在未知字段：{', '.join(sorted(unexpected))}。")
    return data


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise CapabilityContractError(f"{label} 必须是对象。")
    return value


def _require_schema_version(value: Any, expected: str) -> None:
    if value != expected:
        raise CapabilityContractError(f"schema_version 必须为 {expected}。")


def _required_text(value: Any, label: str) -> str:
    text = _text(value, label)
    if not text:
        raise CapabilityContractError(f"{label} 不能为空。")
    return text


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise CapabilityContractError(f"{label} 必须是字符串。")
    return value


def _mutation_level(value: Any) -> MutationLevel:
    if not isinstance(value, str):
        raise CapabilityContractError("mutation_level 必须是字符串。")
    try:
        return MutationLevel[value]
    except KeyError as exc:
        raise CapabilityContractError("mutation_level 必须为 L0 至 L5。") from exc


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CapabilityContractError(f"{label} 必须是数组。")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_required_text(item, f"{label}[{index}]"))
    return tuple(result)


def _mapping_tuple(value: Any, label: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        raise CapabilityContractError(f"{label} 必须是数组。")
    return tuple(_mapping(item, f"{label}[{index}]") for index, item in enumerate(value))
