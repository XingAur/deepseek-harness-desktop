"""Persisted, route-before-provider task intent service."""
from __future__ import annotations

from dataclasses import dataclass
import re

from app.task_intent_repository import TaskIntentRepository
from app.task_intent_router import (
    IntentContext,
    IntentDecision,
    classify_task_intent,
)


__all__ = (
    "TaskIntentRoutingResult",
    "TaskIntentService",
    "require_knowledge_route",
    "require_requirement_workflow_route",
)


_EXPLICIT_MUTATION_REQUEST = re.compile(
    r"(?:^|[，,。；;！!\n])\s*(?:请\s*(?:直接\s*)?|帮我\s*(?:把|将)?\s*|"
    r"开始\s*|直接\s*|现在\s*|继续\s*)(?:修改|改动|修复|实现|落地|应用)|"
    r"\bplease\s+(?:change|fix|implement|modify|apply)\b",
    re.IGNORECASE,
)
_DIRECT_MUTATION_REQUEST = re.compile(
    r"^\s*(?:修改|改动|修复|实现|落地|应用)(?:一下|这个|该|本|\s|[A-Za-z])",
    re.IGNORECASE,
)
_INQUIRY_CUE = re.compile(
    r"哪些|为什么|是否|怎么|如何|是什么|什么(?:原因|问题|情况)|[？?]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TaskIntentRoutingResult:
    """One persisted routing decision; it is not an action authorization."""

    decision: IntentDecision
    event_id: int
    mutation_requested: bool


class TaskIntentService:
    """Read sticky state, classify without providers, then persist the result."""

    def __init__(self, repository: TaskIntentRepository | None = None) -> None:
        self._repository = repository if repository is not None else TaskIntentRepository()

    def route(
        self,
        message: str,
        context: IntentContext,
        *,
        explicit_override: str | None = None,
    ) -> TaskIntentRoutingResult:
        if not isinstance(context, IntentContext):
            raise ValueError("task_intent_input_invalid")

        conversation_key = context.conversation_key
        previous = self._repository.get_session(conversation_key)
        previous_mode = previous.get("mode") if previous is not None else None
        decision = classify_task_intent(
            message,
            context,
            previous_mode=previous_mode,
            explicit_override=explicit_override,
        )
        mutation_requested = decision.mode == "task" and _mutation_requested(message)
        session = self._repository.record_decision(
            conversation_key=conversation_key,
            message=message,
            decision=decision,
            explicit_override=explicit_override,
            mutation_requested=mutation_requested,
        )
        event_id = session.get("last_event_id")
        if (
            not isinstance(event_id, int)
            or isinstance(event_id, bool)
            or event_id < 1
        ):
            raise ValueError("task_intent_storage_invalid")
        return TaskIntentRoutingResult(
            decision=decision,
            event_id=event_id,
            mutation_requested=mutation_requested,
        )


def require_knowledge_route(result: object) -> TaskIntentRoutingResult:
    validated = _validated_routing_result(
        result,
        error="knowledge_route_requires_question_intent",
    )
    if validated.decision.mode != "question":
        raise ValueError("knowledge_route_requires_question_intent")
    return validated


def require_requirement_workflow_route(
    result: object,
) -> TaskIntentRoutingResult:
    validated = _validated_routing_result(
        result,
        error="task_capability_route_requires_requirement_workflow",
    )
    if validated.decision.mode != "task":
        raise ValueError("task_capability_route_requires_requirement_workflow")
    return validated


def _validated_routing_result(
    result: object,
    *,
    error: str,
) -> TaskIntentRoutingResult:
    try:
        if not isinstance(result, TaskIntentRoutingResult):
            raise ValueError
        if not isinstance(result.decision, IntentDecision):
            raise ValueError
        if (
            not isinstance(result.event_id, int)
            or isinstance(result.event_id, bool)
            or result.event_id < 1
            or not isinstance(result.mutation_requested, bool)
        ):
            raise ValueError
        decision = result.decision
        if decision.mode == "question":
            if (
                decision.next_route != "knowledge"
                or decision.current_phase != "knowledge_retrieval"
                or decision.yunxiao_status != "not_applicable"
                or result.mutation_requested
            ):
                raise ValueError
        elif decision.mode == "task":
            if (
                decision.next_route != "requirement_workflow"
                or decision.current_phase != "requirement_intake"
                or decision.yunxiao_status
                not in {"linked", "unlinked", "lookup_failed"}
            ):
                raise ValueError
        else:
            raise ValueError
        TaskIntentRepository(initialize=False).verify_event(
            event_id=result.event_id,
            decision=decision,
            mutation_requested=result.mutation_requested,
        )
    except Exception:
        raise ValueError(error) from None
    return result


def _mutation_requested(message: object) -> bool:
    if not isinstance(message, str):
        return False
    if _EXPLICIT_MUTATION_REQUEST.search(message) is not None:
        return True
    if _INQUIRY_CUE.search(message) is not None:
        return False
    return _DIRECT_MUTATION_REQUEST.search(message) is not None
