from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from types import MappingProxyType

from app.change_context_contracts import LAYER_TYPES, ChangeContextPack, ChangeContextProjection
from app.change_context_gate import ChangeContextGate, ChangeContextGateRepository
from app.change_context_projection import ChangeContextProjectionError, ChangeContextProjectionService


_PACK_ID = re.compile(r"ccp:sha256:[0-9a-f]{64}\Z")
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class ChangeContextExecutionBinding:
    pack_id: str
    projection_hash: str
    layer_hashes: Mapping[str, str]

    def __post_init__(self) -> None:
        if not _PACK_ID.fullmatch(self.pack_id) or not _HASH.fullmatch(self.projection_hash):
            raise ValueError("change_context_execution_binding_identity_invalid")
        hashes = dict(self.layer_hashes)
        if set(hashes) != set(LAYER_TYPES) or any(not _HASH.fullmatch(value) for value in hashes.values()):
            raise ValueError("change_context_execution_binding_layers_invalid")
        object.__setattr__(self, "layer_hashes", MappingProxyType(dict(sorted(hashes.items()))))

    @classmethod
    def from_value(cls, value: object) -> "ChangeContextExecutionBinding":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping) or set(value) != {"pack_id", "projection_hash", "layer_hashes"}:
            raise ValueError("change_context_execution_binding_invalid")
        layer_hashes = value.get("layer_hashes")
        if not isinstance(layer_hashes, Mapping):
            raise ValueError("change_context_execution_binding_layers_invalid")
        return cls(
            pack_id=str(value.get("pack_id") or ""),
            projection_hash=str(value.get("projection_hash") or ""),
            layer_hashes={str(key): str(item) for key, item in layer_hashes.items()},
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "pack_id": self.pack_id,
            "projection_hash": self.projection_hash,
            "layer_hashes": dict(self.layer_hashes),
        }


@dataclass(frozen=True)
class ChangeContextExecutionValidation:
    status: str
    code: str
    message: str
    pack: ChangeContextPack | None = None

    def to_dict(self) -> dict[str, str]:
        return {"status": self.status, "code": self.code, "message": self.message}


class ChangeContextExecutionVerifier:
    """Reopen immutable context immediately before any worker may execute."""

    def __init__(
        self,
        *,
        repository: ChangeContextGateRepository,
        gate: ChangeContextGate,
    ) -> None:
        self.repository = repository
        self.gate = gate

    def validate(
        self,
        binding_value: object,
        *,
        role: str = "implementation",
    ) -> ChangeContextExecutionValidation:
        try:
            binding = ChangeContextExecutionBinding.from_value(binding_value)
        except (TypeError, ValueError):
            return _blocked("BLOCKED_CONTEXT_BINDING_INVALID", "ChangeContext 执行绑定结构或哈希格式无效。")
        try:
            pack = self.repository.get_pack(binding.pack_id)
        except (KeyError, ValueError):
            return _blocked("BLOCKED_CONTEXT_SOURCE_UNAVAILABLE", "无法按绑定重新打开 ChangeContextPack。")
        gate_result = self.gate.evaluate(pack, self.repository)
        if gate_result.status != "ready":
            return _blocked(gate_result.code, "ChangeContextPack 已失效或未通过当前门禁。", pack)
        expected_layer_hashes = {
            layer.layer_type: layer.content_hash
            for layer in pack.layers
        }
        if dict(binding.layer_hashes) != expected_layer_hashes:
            return _blocked("BLOCKED_CONTEXT_HASH_MISMATCH", "执行绑定的四层哈希与持久化 Pack 不一致。", pack)
        try:
            payloads = {
                layer.layer_type: self.repository.get_layer(layer.layer_id)[1]
                for layer in pack.layers
            }
            projection = ChangeContextProjectionService().render(
                pack=pack,
                layer_payloads=payloads,
                role=role,
            )
        except (KeyError, ValueError, ChangeContextProjectionError):
            return _blocked("BLOCKED_CONTEXT_HASH_MISMATCH", f"无法从持久化 Pack 复算 {role} 投影。", pack)
        if projection.projection_hash != binding.projection_hash:
            return _blocked("BLOCKED_CONTEXT_HASH_MISMATCH", f"{role} 投影哈希与执行绑定不一致。", pack)
        return ChangeContextExecutionValidation(
            "ready",
            "CHANGE_CONTEXT_EXECUTION_READY",
            "ChangeContext 执行绑定已重开并通过哈希、时效和角色投影校验。",
            pack,
        )


def validate_worker_binding(
    verifier: ChangeContextExecutionVerifier | None,
    binding_value: object,
) -> ChangeContextExecutionValidation:
    if verifier is None:
        return _blocked("BLOCKED_CONTEXT_VERIFIER_MISSING", "执行器未注入 ChangeContext 重开校验器。")
    if binding_value is None:
        return _blocked("BLOCKED_CONTEXT_BINDING_MISSING", "执行器缺少 ChangeContextPack/投影/四层哈希绑定。")
    return verifier.validate(binding_value)


def validate_worker_context(
    verifier: ChangeContextExecutionVerifier | None,
    binding_value: object,
    projection_value: object,
    *,
    expected_role: str = "implementation",
) -> ChangeContextExecutionValidation:
    if projection_value is None:
        binding_validation = validate_worker_binding(verifier, binding_value)
        if binding_validation.status != "ready":
            return binding_validation
        return _blocked("BLOCKED_CONTEXT_PROJECTION_MISSING", f"执行器缺少 {expected_role} 角色投影。", binding_validation.pack)
    try:
        projection = (
            projection_value
            if isinstance(projection_value, ChangeContextProjection)
            else ChangeContextProjection.from_dict(projection_value)
            if isinstance(projection_value, Mapping)
            else None
        )
        binding = ChangeContextExecutionBinding.from_value(binding_value)
    except (TypeError, ValueError):
        return _blocked("BLOCKED_CONTEXT_PROJECTION_INVALID", "执行器角色投影结构或哈希无效。")
    validation = (
        verifier.validate(binding, role=expected_role)
        if verifier is not None
        else _blocked("BLOCKED_CONTEXT_VERIFIER_MISSING", "执行器未注入 ChangeContext 重开校验器。")
    )
    if validation.status != "ready":
        return validation
    if projection is None or projection.role != expected_role:
        return _blocked("BLOCKED_CONTEXT_PROJECTION_INVALID", f"执行器仅接受 {expected_role} 角色投影。", validation.pack)
    if projection.pack_id != binding.pack_id or projection.projection_hash != binding.projection_hash:
        return _blocked("BLOCKED_CONTEXT_HASH_MISMATCH", "角色投影与 ChangeContext 执行绑定不一致。", validation.pack)
    return validation


def _blocked(
    code: str,
    message: str,
    pack: ChangeContextPack | None = None,
) -> ChangeContextExecutionValidation:
    return ChangeContextExecutionValidation("blocked", code, message, pack)
