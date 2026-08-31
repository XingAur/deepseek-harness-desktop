from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.runtime_policy import runtime_policy_snapshot


MODEL_WORKER_SMOKE_READINESS_SCHEMA_VERSION = "his-model-worker-smoke-readiness.v1"

_SAFE_LAST_SMOKE_FIELDS = (
    "id",
    "profile_key",
    "endpoint_host",
    "model",
    "status",
    "transport_status",
    "protocol_status",
    "marker_status",
    "completed_at",
)


def build_model_worker_smoke_readiness(
    *,
    last_smoke: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an inert Manager/API readiness contract for real-model worker smoke.

    This function intentionally does not read credentials, open provider policy files,
    call networks, or start the real-model DAG. It only exposes the contract that must
    be satisfied before a separately authorized single-node smoke can run.
    """

    policy = runtime_policy_snapshot()
    frozen = policy.real_model_runtime_frozen
    smoke_allowed = policy.real_model_smoke_allowed
    blockers = []
    if frozen:
        blockers.append({"code": "real_model_runtime_frozen", "message": policy.reason})
    if not smoke_allowed:
        blockers.append(
            {
                "code": "real_model_smoke_not_allowed",
                "message": "单节点真实模型 smoke 仍未授权；不会读取凭证或发起网络调用。",
            }
        )
    return {
        "schema_version": MODEL_WORKER_SMOKE_READINESS_SCHEMA_VERSION,
        "state": (
            "single_node_smoke_ready"
            if smoke_allowed
            else "frozen" if frozen else "smoke_ready"
        ),
        "runtime_policy": policy.to_dict(),
        "credentials_read": False,
        "network_called": False,
        "paid_network_calls_allowed": policy.paid_network_calls_allowed,
        "real_model_dag_enabled": False,
        "dag_state": "dag_still_frozen" if frozen else "dag_not_frozen",
        "single_node_smoke": {
            "status": "not_run",
            "allowed": smoke_allowed,
            "execution_action": "model.single_node.smoke",
            "boundary": "single-node-smoke-only",
            "required_authorization": [
                "allow_credentials=true",
                "allow_network=true",
                "authorization_id provided",
            ],
            "required_controls": [
                "fixed_smoke_prompt",
                "redacted_audit",
                "idempotency_key",
                "no_agent_team_dag",
            ],
            "last_smoke": _safe_last_smoke_summary(last_smoke),
        },
        "blockers": blockers,
        "next_actions": [
            "保持真实模型 DAG 冻结，只开放单节点 smoke 合同展示。",
            "真实 smoke 必须另行显式授权凭证读取、网络调用和本次 authorization_id。",
            "smoke 结果只能作为 provider 连通性证据，不能等同业务验收通过。",
        ],
    }


def _safe_last_smoke_summary(last_smoke: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not last_smoke:
        return None
    return {
        field: last_smoke[field]
        for field in _SAFE_LAST_SMOKE_FIELDS
        if field in last_smoke and last_smoke[field] not in (None, "")
    }
