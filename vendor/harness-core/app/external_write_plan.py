from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence


EXTERNAL_WRITE_PLAN_SCHEMA_VERSION = "his-external-write-dry-run-plan.v1"
SUPPORTED_EXTERNAL_WRITE_CAPABILITIES = frozenset(
    ("workitem.write", "git.push", "gitlab.write", "github.write", "database.change")
)
_SECRET_PATTERNS = (
    re.compile(r"Authorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/=-]{24,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def build_external_write_dry_run_plan(actions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build an inert preview; it never authorizes enabled or disabled writes."""
    normalized = [_normalize_action(item) for item in actions]
    planned_actions = [
        {
            "capability": item["capability"],
            "target": item["target"],
            "operation": item["operation"],
            "status": "blocked_by_policy",
            "execution_allowed": False,
            "confirmation_required": True,
            "idempotency_key": _idempotency_key(item),
            "required_before_execution": [
                "test_object_acceptance",
                "explicit_user_confirmation",
                "capability_manifest_enabled",
            ],
        }
        for item in normalized
    ]
    return {
        "schema_version": EXTERNAL_WRITE_PLAN_SCHEMA_VERSION,
        "mode": "dry_run",
        "changed": False,
        "external_write_attempted": False,
        "execution_allowed": False,
        "confirmation_required": True,
        "actions": planned_actions,
        "next_actions": [
            "先在测试对象上验证事务计划。",
            "真实外部写入必须匹配能力白名单、不可变目标和当前任务的一次性确认。",
        ],
    }


def _normalize_action(action: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(action, Mapping):
        raise ValueError("external write action must be a mapping")
    capability = _required_text(action.get("capability"), "capability")
    if capability not in SUPPORTED_EXTERNAL_WRITE_CAPABILITIES:
        raise ValueError("unsupported external write capability")
    target = _required_text(action.get("target"), "target")
    operation = _required_text(action.get("operation"), "operation")
    payload_preview = str(action.get("payload_preview") or "")
    if _contains_sensitive_value(json.dumps(action, ensure_ascii=False, sort_keys=True)):
        raise ValueError("sensitive value is not accepted")
    return {
        "capability": capability,
        "target": target,
        "operation": operation,
        "payload_preview": payload_preview,
    }


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _contains_sensitive_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_PATTERNS)


def _idempotency_key(action: Mapping[str, str]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {
                "capability": action["capability"],
                "target": action["target"],
                "operation": action["operation"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]
    return "dryrun:" + digest
