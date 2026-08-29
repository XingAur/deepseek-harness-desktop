from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.sensitive_text import contains_sensitive_text


TASK_CONTEXT_SCHEMA_VERSION = "his-task-intent-context.v1"
REQUIRED_CONTEXT_FIELDS = ("background", "goal", "scenarios", "desired_outcome")
_OPTIONAL_CONTEXT_FIELDS = ("constraints", "acceptance_criteria", "source_refs")
_MAX_TEXT_CHARS = 4_096
_MAX_ITEMS = 16


class TaskIntentContextError(ValueError):
    """任务意图上下文不符合 Harness 的安全和结构约束。"""


@dataclass(frozen=True)
class TaskIntentContext:
    """把任务的背景、目标、场景和期望结果固定为可审计输入。"""

    background: str = ""
    goal: str = ""
    scenarios: tuple[str, ...] = ()
    desired_outcome: str = ""
    constraints: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("background", "goal", "desired_outcome"):
            _validate_text(getattr(self, field_name), field_name, allow_empty=True)
        for field_name in ("scenarios", "constraints", "acceptance_criteria", "source_refs"):
            _validate_items(getattr(self, field_name), field_name)

    @classmethod
    def empty(cls) -> "TaskIntentContext":
        return cls()

    @classmethod
    def from_dict(cls, payload: Any) -> "TaskIntentContext":
        if payload is None:
            return cls.empty()
        if not isinstance(payload, dict):
            raise TaskIntentContextError("task_context 必须是对象。")
        allowed = set(REQUIRED_CONTEXT_FIELDS) | set(_OPTIONAL_CONTEXT_FIELDS) | {
            "schema_version", "status", "missing_fields", "content_hash"
        }
        unexpected = set(payload) - allowed
        if unexpected:
            raise TaskIntentContextError(
                f"task_context 存在未知字段：{', '.join(sorted(unexpected))}。"
            )
        version = payload.get("schema_version", TASK_CONTEXT_SCHEMA_VERSION)
        if version != TASK_CONTEXT_SCHEMA_VERSION:
            raise TaskIntentContextError(
                f"task_context schema_version 必须为 {TASK_CONTEXT_SCHEMA_VERSION}。"
            )
        return cls(
            background=str(payload.get("background") or ""),
            goal=str(payload.get("goal") or ""),
            scenarios=_items_from_payload(payload.get("scenarios"), "scenarios"),
            desired_outcome=str(payload.get("desired_outcome") or ""),
            constraints=_items_from_payload(payload.get("constraints"), "constraints"),
            acceptance_criteria=_items_from_payload(
                payload.get("acceptance_criteria"), "acceptance_criteria"
            ),
            source_refs=_items_from_payload(payload.get("source_refs"), "source_refs"),
        )

    @property
    def missing_fields(self) -> tuple[str, ...]:
        missing: list[str] = []
        for field_name in REQUIRED_CONTEXT_FIELDS:
            value = getattr(self, field_name)
            if isinstance(value, tuple):
                if not value:
                    missing.append(field_name)
            elif not value.strip():
                missing.append(field_name)
        return tuple(missing)

    @property
    def is_complete(self) -> bool:
        return not self.missing_fields

    @property
    def content_hash(self) -> str:
        encoded = json.dumps(
            self._content_mapping(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TASK_CONTEXT_SCHEMA_VERSION,
            **self._content_mapping(),
            "status": "complete" if self.is_complete else "incomplete",
            "missing_fields": list(self.missing_fields),
            "content_hash": self.content_hash,
        }

    def _content_mapping(self) -> dict[str, Any]:
        return {
            "background": self.background,
            "goal": self.goal,
            "scenarios": list(self.scenarios),
            "desired_outcome": self.desired_outcome,
            "constraints": list(self.constraints),
            "acceptance_criteria": list(self.acceptance_criteria),
            "source_refs": list(self.source_refs),
        }


def _validate_text(value: Any, field_name: str, *, allow_empty: bool) -> None:
    if not isinstance(value, str):
        raise TaskIntentContextError(f"task_context.{field_name} 必须是字符串。")
    if len(value) > _MAX_TEXT_CHARS:
        raise TaskIntentContextError(f"task_context.{field_name} 超过长度限制。")
    if not allow_empty and not value.strip():
        raise TaskIntentContextError(f"task_context.{field_name} 不能为空。")
    if value and contains_sensitive_text(value):
        raise TaskIntentContextError(f"task_context.{field_name} 包含 sensitive 内容。")


def _validate_items(value: Any, field_name: str) -> None:
    if not isinstance(value, tuple):
        raise TaskIntentContextError(f"task_context.{field_name} 必须是字符串元组。")
    if len(value) > _MAX_ITEMS:
        raise TaskIntentContextError(f"task_context.{field_name} 项数超过限制。")
    seen: set[str] = set()
    for item in value:
        _validate_text(item, f"{field_name}[]", allow_empty=False)
        if item in seen:
            raise TaskIntentContextError(f"task_context.{field_name} 不得重复。")
        seen.add(item)


def _items_from_payload(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise TaskIntentContextError(f"task_context.{field_name} 必须是数组。")
    return tuple(value)
