from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import replace
from typing import Literal

from app.model_provider_runtime import (
    ControlledModelProviderRuntime,
    OpenAICompatibleSmokeTransport,
    ProviderSmokeTransport,
    resolve_manager_provider_profile,
)
from app.provider_execution import ProviderExecutionContext, ProviderExecutionRequest


MODEL_SMOKE_ACTION = "model.single_node.smoke"
_MODEL_TARGET = re.compile(r"model\.([a-z][a-z0-9_-]{0,63})")


class ManagerModelSmokeProviderAdapter:
    """Manager-only one-shot smoke adapter; it never accepts prompt-like input."""

    def __init__(self, *, transport: ProviderSmokeTransport | None = None) -> None:
        selected_transport = transport or OpenAICompatibleSmokeTransport()
        # Tests may inject a fake transport.  Explicitly supplying the actual
        # controlled HTTPS transport must retain live-dispatch audit semantics.
        self._simulated_transport = not isinstance(
            selected_transport, OpenAICompatibleSmokeTransport
        )
        self._runtime = ControlledModelProviderRuntime(transport=selected_transport)

    @staticmethod
    def normalize_target_alias(target_alias: object) -> str:
        if not isinstance(target_alias, str):
            raise ValueError("model_smoke_target_invalid")
        normalized = target_alias.strip().lower()
        if _MODEL_TARGET.fullmatch(normalized) is None:
            raise ValueError("model_smoke_target_invalid")
        return normalized

    @classmethod
    def normalize_request_target(cls, parameters: Mapping[str, object]) -> str:
        if not isinstance(parameters, Mapping):
            raise ValueError("model_smoke_parameters_invalid")
        alias = parameters.get("model_profile_alias")
        if not isinstance(alias, str):
            raise ValueError("model_smoke_parameters_invalid")
        return cls.normalize_target_alias(f"model.{alias}")

    def validate_profile_binding(self, *, profile_id: int, target_alias: str) -> str:
        if not isinstance(profile_id, int) or profile_id < 1:
            raise ValueError("model_smoke_profile_invalid")
        return self.normalize_target_alias(target_alias)

    def execute(
        self,
        request: ProviderExecutionRequest,
        context: ProviderExecutionContext,
    ) -> Mapping[str, object]:
        if request.action != MODEL_SMOKE_ACTION:
            raise ValueError("model_smoke_action_not_allowed")
        target_alias = validate_model_smoke_parameters(
            target_alias=self.normalize_request_target(request.parameters),
            parameters=request.parameters,
        )
        expected_target = self.normalize_target_alias(f"model.{context.profile_key}")
        if target_alias != expected_target:
            raise ValueError("model_smoke_profile_target_mismatch")
        # Validate every non-secret field before resolving the encrypted key.
        connection = context.profile_connection
        validated_profile = resolve_manager_provider_profile(
            profile_key=context.profile_key,
            connection=connection,
            api_key="manager-validation-only",
        )
        profile = replace(validated_profile, api_key=context.credential("api_key"))
        context.record_network_dispatch(
            target_alias,
            simulated=self._simulated_transport,
        )
        started = time.monotonic()
        snapshot = self._runtime.run_manager_smoke(
            profile=profile,
            execution_key=f"plan-{request.plan_id}",
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        smoke = snapshot.get("smoke")
        if not isinstance(smoke, Mapping):
            raise ValueError("model_smoke_evidence_invalid")
        marker_status = str(smoke.get("marker_status") or "not_run")
        if smoke.get("status") != "passed" or marker_status != "passed":
            # A dispatched request that did not prove the fixed marker is an
            # execution failure, not a successful Provider action with a
            # failure-shaped summary.  The common execution boundary retains
            # the dispatch fact but emits no raw upstream detail.
            raise ValueError("model_smoke_not_verified")
        return {
            "profile_alias": profile.profile_key,
            "endpoint_host": profile.endpoint_host,
            "model_alias": profile.model,
            "smoke_status": str(smoke.get("status") or "failed"),
            "result_marker": "SMOKE_OK" if marker_status == "passed" else "not_verified",
            "request_hash": str(smoke.get("request_hash") or ""),
            "response_hash": str(smoke.get("response_hash") or ""),
            "usage": dict(smoke.get("usage") or {}),
            "duration_ms": elapsed_ms,
        }

    def verify(
        self,
        verifier_action: str,
        original_write_action: str,
        request: ProviderExecutionRequest,
        target_alias: str,
        context: ProviderExecutionContext,
    ) -> Literal["unknown"]:
        return "unknown"


def validate_model_smoke_parameters(
    *,
    target_alias: object,
    parameters: Mapping[str, object],
) -> str:
    """Accept only the immutable profile alias that was reviewed in the plan."""

    if not isinstance(parameters, Mapping) or set(parameters) != {"model_profile_alias"}:
        raise ValueError("model_smoke_parameters_invalid")
    alias = parameters.get("model_profile_alias")
    if not isinstance(alias, str):
        raise ValueError("model_smoke_parameters_invalid")
    expected = ManagerModelSmokeProviderAdapter.normalize_target_alias(f"model.{alias}")
    actual = ManagerModelSmokeProviderAdapter.normalize_target_alias(target_alias)
    if actual != expected:
        raise ValueError("model_smoke_parameters_invalid")
    return actual
