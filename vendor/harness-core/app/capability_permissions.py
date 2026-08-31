from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.capability_contracts import CapabilityRequest, CapabilityResult, MutationLevel


@dataclass(frozen=True)
class PermissionDecision:
    status: str
    allowed: bool
    required_level: MutationLevel
    blockers: tuple[str, ...]


def evaluate_capability_permission(
    *,
    request: CapabilityRequest,
    declared_level: MutationLevel,
    declared_scopes: Sequence[str],
    external_writes_default: bool = False,
) -> PermissionDecision:
    blockers: list[str] = []

    if request.mutation_level != declared_level:
        blockers.append("请求权限等级与 capability 声明不一致。")

    if request.mode == "apply":
        required_scopes = set(declared_scopes)
        if declared_level >= MutationLevel.L4:
            required_scopes.add(f"capability:{request.capability}")

        if declared_level >= MutationLevel.L2 and set(request.authorization.scope) != required_scopes:
            blockers.append("授权范围与 capability 声明不一致。")
        if declared_level >= MutationLevel.L3 and not request.authorization.explicit:
            blockers.append("该操作需要明确授权。")
        if declared_level >= MutationLevel.L4 and not external_writes_default:
            blockers.append("外部写能力默认关闭。")

    return _decision(declared_level, blockers)


def evaluate_capability_result_permission(
    *, request: CapabilityRequest, result: CapabilityResult
) -> PermissionDecision:
    blockers: list[str] = []

    if request.mode == "preview" and result.changed:
        blockers.append("预览请求不能返回 changed=true。")

    return _decision(request.mutation_level, blockers)


def _decision(required_level: MutationLevel, blockers: list[str]) -> PermissionDecision:
    allowed = not blockers
    return PermissionDecision(
        status="allowed" if allowed else "blocked",
        allowed=allowed,
        required_level=required_level,
        blockers=tuple(blockers),
    )
