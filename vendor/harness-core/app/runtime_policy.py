from __future__ import annotations

import hashlib
import re
import weakref
from dataclasses import dataclass

from app.sensitive_text import contains_sensitive_text


REAL_MODEL_RUNTIME_FROZEN = True
REAL_MODEL_SMOKE_ALLOWED = True
REAL_MODEL_MODES = frozenset({"openai", "real", "anthropic", "claude", "zhipu"})
REAL_MODEL_RUNTIME_FREEZE_REASON = (
    "Harness 企业级核心闭环尚未完成验收；真实模型调用与真实模型 DAG 暂停。"
)


class RealModelRuntimeFrozenError(RuntimeError):
    code = "real_model_runtime_frozen"

    def __init__(self, mode: str) -> None:
        self.mode = normalize_runtime_mode(mode)
        super().__init__(
            f"{self.code}: mode={self.mode}; {REAL_MODEL_RUNTIME_FREEZE_REASON}"
        )


class RealModelSmokeNotAllowedError(RuntimeError):
    code = "real_model_smoke_not_allowed"

    def __init__(self) -> None:
        super().__init__(
            f"{self.code}: 真实模型单节点 smoke 未开启；"
            "常规模型运行与真实模型 DAG 仍保持冻结。"
        )


class LocalAgentRunNotAllowedError(RuntimeError):
    code = "local_agent_run_not_allowed"

    def __init__(self) -> None:
        super().__init__(
            f"{self.code}: local real-agent execution requires an explicit preflight label."
        )


@dataclass(frozen=True)
class RuntimePolicySnapshot:
    real_model_runtime_frozen: bool
    real_model_smoke_allowed: bool
    real_model_modes: tuple[str, ...]
    reason: str
    paid_network_calls_allowed: bool

    def to_dict(self) -> dict:
        return {
            "real_model_runtime_frozen": self.real_model_runtime_frozen,
            "real_model_smoke_allowed": self.real_model_smoke_allowed,
            "real_model_modes": list(self.real_model_modes),
            "reason": self.reason,
            "paid_network_calls_allowed": self.paid_network_calls_allowed,
        }


def normalize_runtime_mode(mode: str | None) -> str:
    return (mode or "").strip().lower()


def assert_runtime_mode_allowed(
    mode: str | None,
    *,
    allow_frozen_test_transport: bool = False,
) -> None:
    normalized = normalize_runtime_mode(mode)
    if (
        REAL_MODEL_RUNTIME_FROZEN
        and normalized in REAL_MODEL_MODES
        and not allow_frozen_test_transport
    ):
        raise RealModelRuntimeFrozenError(normalized)


def assert_model_provider_smoke_allowed() -> None:
    """Allow only the separately governed fixed single-node smoke path."""

    if not REAL_MODEL_SMOKE_ALLOWED:
        raise RealModelSmokeNotAllowedError()


def _create_local_agent_activation_api():
    registry: weakref.WeakKeyDictionary[object, str] = weakref.WeakKeyDictionary()

    class LocalAgentActivationPreflight:
        """Opaque, process-local evidence awaiting transactional consumption."""

        __slots__ = ("__weakref__",)

        def __new__(cls):
            raise LocalAgentRunNotAllowedError()

        def __repr__(self) -> str:
            return "LocalAgentActivationPreflight(<opaque>)"

        def __copy__(self):
            return object.__new__(type(self))

        def __deepcopy__(self, memo):
            del memo
            return object.__new__(type(self))

        def __reduce_ex__(self, protocol):
            del protocol
            raise LocalAgentRunNotAllowedError()

    def assert_local_agent_run_allowed(
        *, allow_real_agent: bool, authorization_id: str
    ) -> LocalAgentActivationPreflight:
        """Mint process-local preflight evidence without consuming authorization."""

        if allow_real_agent is not True:
            raise LocalAgentRunNotAllowedError()
        normalized = validate_one_time_authorization_text(authorization_id)
        preflight = object.__new__(LocalAgentActivationPreflight)
        registry[preflight] = (
            "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        )
        return preflight

    def verify_local_agent_activation_preflight(preflight: object) -> str:
        """Return the hash only for an exact capability minted in this process."""

        if type(preflight) is not LocalAgentActivationPreflight:
            raise LocalAgentRunNotAllowedError()
        try:
            authorization_hash = registry[preflight]
        except (KeyError, TypeError):
            raise LocalAgentRunNotAllowedError() from None
        return authorization_hash

    return (
        LocalAgentActivationPreflight,
        assert_local_agent_run_allowed,
        verify_local_agent_activation_preflight,
    )


(
    LocalAgentActivationPreflight,
    assert_local_agent_run_allowed,
    verify_local_agent_activation_preflight,
) = _create_local_agent_activation_api()
del _create_local_agent_activation_api


def validate_one_time_authorization_text(authorization_id: str) -> str:
    """Accept a bounded preflight label and never treat it as a credential."""

    if not isinstance(authorization_id, str):
        raise ValueError("local_agent_authorization_invalid")
    normalized = authorization_id.strip()
    if (
        authorization_id != normalized
        or not normalized
        or len(normalized) > 256
        or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,255}", normalized, re.IGNORECASE)
        is None
        or contains_sensitive_text(normalized)
        or re.search(r"\b(?:basic|bearer)\s+\S+", normalized, re.IGNORECASE)
    ):
        raise ValueError("local_agent_authorization_invalid")
    return normalized


def runtime_policy_snapshot() -> RuntimePolicySnapshot:
    return RuntimePolicySnapshot(
        real_model_runtime_frozen=REAL_MODEL_RUNTIME_FROZEN,
        real_model_smoke_allowed=REAL_MODEL_SMOKE_ALLOWED,
        real_model_modes=tuple(sorted(REAL_MODEL_MODES)),
        reason=REAL_MODEL_RUNTIME_FREEZE_REASON,
        paid_network_calls_allowed=not REAL_MODEL_RUNTIME_FROZEN,
    )
