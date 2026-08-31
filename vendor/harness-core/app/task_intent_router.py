from __future__ import annotations

from dataclasses import dataclass
import re


_WORK_ITEM_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,31}-\d+$")
_CONVERSATION_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_STRONG_TASK_PATTERN = re.compile(
    r"需求|缺陷|(?<![A-Za-z])bug(?![A-Za-z])|工作项|工单|业务规则|影响分析|"
    r"定位|排查|修改|改动|实现|修复|审核|验证|验收",
    re.IGNORECASE,
)
_GENERAL_QUESTION_PATTERN = re.compile(
    r"什么是|是什么|原理|区别|用途|介绍|解释|"
    r"\bwhat\s+is\b|"
    r"\bexplain\b|\bdifference\s+between\b",
    re.IGNORECASE,
)
_CLEAR_DEFINITION_PATTERN = re.compile(r"(?:什么是.+|.+是什么)$", re.IGNORECASE)
_DEFINITION_DIAGNOSTIC_CONTEXT_PATTERN = re.compile(
    r"(?:这个|该|当前)|"
    r"是什么(?:原因|问题|情况)$|"
    r"(?:发生|出现|提示).{0,6}(?:错误|异常|故障|报错)|"
    r"返回.{0,8}(?:状态码|[1-5]\d{2}|错误|异常|报错)|"
    r"(?:页面|网页).{0,6}打不开",
    re.IGNORECASE,
)
_TROUBLESHOOTING_TASK_PATTERN = re.compile(
    r"(?:"
    r"(?:页面|网页).{0,6}打不开|"
    r"请求.{0,6}失败|"
    r"(?:发生|出现|提示).{0,6}(?:错误|异常|故障|报错)|"
    r"系统.{0,6}故障|"
    r"返回.{0,6}状态码\s*[1-5]\d{2}|"
    r"(?:接口|http).{0,8}(?:返回\s*)?[1-5]\d{2}|"
    r"(?:这个|该|当前).{0,6}(?:错误|异常|故障|报错)|"
    r"(?:错误|异常|故障|报错).{0,12}(?:什么(?:原因|问题|情况)|为什么|怎么)|"
    r"状态码\s*[1-5]\d{2}.{0,12}(?:什么(?:原因|问题|情况)|为什么|怎么)"
    r")",
    re.IGNORECASE,
)
_TASK_PHASES = frozenset({"requirement_intake"})
TASK_INTENT_REASON_CODES = frozenset(
    {
        "explicit_override",
        "sticky_task_session",
        "structured_work_item",
        "structured_task_context",
        "strong_task_text",
        "general_knowledge_question",
        "troubleshooting_task_text",
        "conservative_task_fallback",
    }
)
TASK_INTENT_CORRECTION_REASON_CODES = ("explicit_override",)


@dataclass(frozen=True)
class IntentContext:
    """Structured, non-secret context used by the pure intent classifier."""

    conversation_key: str = ""
    work_item_id: str | None = None
    local_requirement_id: str | None = None
    current_phase: str | None = None
    change_target: str | None = None
    yunxiao_status: str | None = None
    yunxiao_lookup_failed: bool = False
    provider_available: bool | None = None


@dataclass(frozen=True)
class IntentDecision:
    mode: str
    reason_codes: tuple[str, ...]
    confidence: str
    sticky: bool
    conversation_key: str | None
    linked_work_item: str | None
    yunxiao_status: str
    current_phase: str
    next_route: str


def classify_task_intent(
    message: str,
    context: IntentContext,
    previous_mode: str | None = None,
    explicit_override: str | None = None,
) -> IntentDecision:
    """Classify an input without consulting storage, providers, or the network."""

    _validate_public_inputs(message, context)
    override = _validated_mode(explicit_override, field_name="explicit_override")
    previous = _validated_mode(previous_mode, field_name="previous_mode")
    if override is not None:
        return _decision(
            override,
            reason_codes=("explicit_override",),
            confidence="high",
            sticky=False,
            context=context,
        )

    if previous == "task":
        return _decision(
            "task",
            reason_codes=("sticky_task_session",),
            confidence="high",
            sticky=True,
            context=context,
        )

    if _has_structured_task_context(context):
        return _decision(
            "task",
            reason_codes=("structured_work_item",)
            if _validated_work_item(context.work_item_id) is not None
            else ("structured_task_context",),
            confidence="high",
            sticky=False,
            context=context,
        )

    text = message.strip()
    if _STRONG_TASK_PATTERN.search(text):
        return _decision(
            "task",
            reason_codes=("strong_task_text",),
            confidence="high",
            sticky=False,
            context=context,
        )

    if _is_clear_definition_question(text):
        return _decision(
            "question",
            reason_codes=("general_knowledge_question",),
            confidence="high",
            sticky=False,
            context=context,
        )

    if _TROUBLESHOOTING_TASK_PATTERN.search(text):
        return _decision(
            "task",
            reason_codes=("troubleshooting_task_text",),
            confidence="high",
            sticky=False,
            context=context,
        )

    if _GENERAL_QUESTION_PATTERN.search(text):
        return _decision(
            "question",
            reason_codes=("general_knowledge_question",),
            confidence="high",
            sticky=False,
            context=context,
        )

    return _decision(
        "task",
        reason_codes=("conservative_task_fallback",),
        confidence="conservative",
        sticky=False,
        context=context,
    )


def _is_clear_definition_question(text: str) -> bool:
    sentence = text.strip().rstrip("？?。！!").strip()
    if _DEFINITION_DIAGNOSTIC_CONTEXT_PATTERN.search(sentence):
        return False
    return _CLEAR_DEFINITION_PATTERN.fullmatch(sentence) is not None


def _decision(
    mode: str,
    *,
    reason_codes: tuple[str, ...],
    confidence: str,
    sticky: bool,
    context: IntentContext,
) -> IntentDecision:
    conversation_key = _validated_conversation_key(context.conversation_key)
    linked_work_item = _validated_work_item(context.work_item_id)
    return IntentDecision(
        mode=mode,
        reason_codes=reason_codes,
        confidence=confidence,
        sticky=sticky,
        conversation_key=conversation_key,
        linked_work_item=linked_work_item,
        yunxiao_status=_yunxiao_status(mode, context, linked_work_item),
        current_phase="knowledge_retrieval" if mode == "question" else "requirement_intake",
        next_route="knowledge" if mode == "question" else "requirement_workflow",
    )


def _has_structured_task_context(context: IntentContext) -> bool:
    return any(
        (
            _validated_work_item(context.work_item_id),
            _non_empty(context.local_requirement_id),
            _validated_task_phase(context.current_phase),
            _non_empty(context.change_target),
        )
    )


def _yunxiao_status(
    mode: str, context: IntentContext, linked_work_item: str | None
) -> str:
    if mode == "question":
        return "not_applicable"
    if context.yunxiao_lookup_failed or context.yunxiao_status == "lookup_failed":
        return "lookup_failed"
    if linked_work_item is not None:
        return "linked"
    return "unlinked"


def _validated_work_item(value: str | None) -> str | None:
    text = _non_empty(value)
    if text is not None and _WORK_ITEM_PATTERN.fullmatch(text):
        return text
    return None


def _validated_conversation_key(value: str | None) -> str | None:
    text = _non_empty(value)
    if text is not None and _CONVERSATION_KEY_PATTERN.fullmatch(text):
        return text
    return None


def _validated_task_phase(value: str | None) -> str | None:
    text = _non_empty(value)
    if text in _TASK_PHASES:
        return text
    return None


def _validate_public_inputs(message: str, context: IntentContext) -> None:
    if not isinstance(message, str):
        raise ValueError("message must be a string")
    if not isinstance(context, IntentContext):
        raise ValueError("context must be an IntentContext")


def _validated_mode(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value in {"question", "task"}:
        return value
    raise ValueError(f"{field_name} must be None, 'question', or 'task'")


def _non_empty(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None
