"""Deterministic, evidence-only customer-service answers for HIS knowledge."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Optional


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))
from knowledge_retrieve import KnowledgeMatch, KnowledgeQuery, KnowledgeRetriever  # noqa: E402
from knowledge_store import KnowledgeStore  # noqa: E402
from knowledge_capability import knowledge_home, result as capability_result, run_main, validate_request  # noqa: E402


ANSWER_STATUSES = {
    "answered",
    "needs_live_evidence",
    "needs_clarification",
    "conflicted",
    "unsupported",
}
_SCOPE_NAMES = ("hospital", "region", "module", "repo", "branch")
_CHANGE_PHRASES = ("帮我改", "修改代码", "改一下代码", "修复代码", "实现功能", "改代码")
_REQUEST_PREFIXES = ("帮我", "请", "需要", "给我")
_REQUEST_ACTIONS = ("实现", "重构", "修改", "修复", "改")
_LATEST_TERMS = ("最新", "当前", "现在")
_LIVE_DATABASE_TERMS = ("生产", "线上", "运行时", "runtime")


@dataclass(frozen=True)
class KnowledgeAnswer:
    status: str
    answer: str
    evidence: tuple[dict[str, Any], ...]
    applicability: tuple[str, ...]
    freshness: str
    confidence_basis: tuple[str, ...]
    missing_information: tuple[str, ...]
    suggested_capabilities: tuple[str, ...]


def _copy_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _copy_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_copy_value(item) for item in value]
    if isinstance(value, list):
        return [_copy_value(item) for item in value]
    return value


def _is_change_request(text: str) -> bool:
    if any(prefix + action in text for prefix in _REQUEST_PREFIXES for action in _REQUEST_ACTIONS):
        return True
    if text.startswith("把") and any(action in text for action in ("改", "重构")):
        return True
    return any(phrase in text for phrase in _CHANGE_PHRASES)


class KnowledgeAnswerer:
    """Route customer questions without invoking external capabilities or writes."""

    def __init__(
        self,
        store: KnowledgeStore,
        *,
        utc_date: Optional[Callable[[], date]] = None,
        retriever: Optional[KnowledgeRetriever] = None,
        query_type: Callable[..., KnowledgeQuery] = KnowledgeQuery,
    ) -> None:
        if not isinstance(store, KnowledgeStore):
            raise ValueError("store must be a KnowledgeStore")
        self.retriever = retriever or KnowledgeRetriever(store, utc_date=utc_date)
        self._query_type = query_type

    def answer(
        self,
        text: str,
        *,
        hospital: str = "",
        region: str = "",
        module: str = "",
        repo: str = "",
        branch: str = "",
    ) -> KnowledgeAnswer:
        text, scopes = self._validate(text, hospital, region, module, repo, branch)
        if _is_change_request(text):
            return self._result(
                "unsupported", "这是改码请求，请进入 Harness 任务模式处理。", suggested=("harness.task",), freshness="not_applicable",
            )
        if "云效" in text and any(term in text for term in _LATEST_TERMS):
            return self._result(
                "needs_live_evidence", "云效最新内容需要实时读取，现有知识不能替代当前工单。",
                suggested=("workitem.read",), freshness="live_required",
            )
        if any(term in text for term in _LIVE_DATABASE_TERMS) and ("数据库" in text or "生产库" in text):
            return self._result(
                "needs_live_evidence", "生产数据库事实需要实时检查，现有知识不能证明当前值。",
                suggested=("database.inspect",), freshness="live_required",
            )
        retrieval = self.retriever.retrieve(self._query_type(text=text, **scopes))
        missing = self._missing_scopes(retrieval.items, scopes)
        if missing:
            return self._result(
                "needs_clarification", "需要补充适用范围后才能选择正确规则。",
                missing=missing, freshness="scope_required",
            )
        if retrieval.evidence_status == "conflict":
            return self._result("conflicted", "存在同级高权威知识冲突，不能选择其中一条作为结论。", freshness="conflict")
        if retrieval.can_answer and retrieval.items:
            return self._answered(retrieval.items[0])
        if retrieval.evidence_status in {"stale", "not_current"}:
            return self._result(
                "needs_live_evidence", "现有正式知识无法确认当前事实，需要实时证据。",
                suggested=("workitem.read",), freshness=retrieval.evidence_status,
            )
        return self._result("unsupported", "没有可支持该问题的正式知识，也没有可安全推荐的实时证据路径。", freshness="absent")

    @staticmethod
    def _validate(text: object, *scope_values: object) -> tuple[str, dict[str, str]]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("answer text must be a non-empty string")
        if not all(isinstance(value, str) for value in scope_values):
            raise ValueError("answer scope fields must be strings")
        return text.strip(), {name: str(value).strip() for name, value in zip(_SCOPE_NAMES, scope_values)}

    @staticmethod
    def _missing_scopes(items: tuple[KnowledgeMatch, ...], scopes: Mapping[str, str]) -> tuple[str, ...]:
        missing = []
        for name in _SCOPE_NAMES:
            if scopes[name]:
                continue
            values = {getattr(item.scopes, name) for item in items if item.can_support_direct_answer and getattr(item.scopes, name)}
            if len(values) > 1:
                missing.append(name)
        return tuple(missing)

    @staticmethod
    def _result(
        status: str,
        answer: str,
        *,
        suggested: tuple[str, ...] = (),
        missing: tuple[str, ...] = (),
        freshness: str,
    ) -> KnowledgeAnswer:
        return KnowledgeAnswer(status, answer, (), (), freshness, (), missing, suggested)

    @staticmethod
    def _answered(item: KnowledgeMatch) -> KnowledgeAnswer:
        applicability = tuple(
            name + "=" + getattr(item.scopes, name)
            for name in _SCOPE_NAMES if getattr(item.scopes, name)
        ) or ("global",)
        evidence = ({
            "stable_key": item.stable_key,
            "title": item.title,
            "authority": item.authority,
            "version_label": item.version_label,
            "source_refs": _copy_value(item.source_refs),
            "excerpt": item.body[:160],
        },)
        confidence = (
            "authority:" + item.authority,
            "freshness:" + item.temporal_state,
            "scope:" + ",".join(applicability),
        )
        return KnowledgeAnswer(
            "answered", "结论：" + item.title + "\n依据：" + item.body[:160], evidence, applicability,
            item.temporal_state, confidence, (), (),
        )


def answer(
    text: str,
    *,
    store: Optional[KnowledgeStore] = None,
    hospital: str = "",
    region: str = "",
    module: str = "",
    repo: str = "",
    branch: str = "",
    utc_date: Optional[Callable[[], date]] = None,
    retriever: Optional[KnowledgeRetriever] = None,
    query_type: Callable[..., KnowledgeQuery] = KnowledgeQuery,
) -> KnowledgeAnswer:
    """Return deterministic answer data; suggestions are never executed here."""
    return KnowledgeAnswerer(
        store if store is not None else KnowledgeStore(), utc_date=utc_date, retriever=retriever, query_type=query_type,
    ).answer(
        text, hospital=hospital, region=region, module=module, repo=repo, branch=branch,
    )


_CAPABILITY_INPUT = frozenset(("text", "hospital", "region", "module", "repo", "branch"))


def _validate_capability_input(value: Mapping[str, object]) -> None:
    if not isinstance(value.get("text"), str) or not value["text"].strip():
        raise ValueError("invalid capability request")
    if any(key != "text" and not isinstance(item, str) for key, item in value.items()):
        raise ValueError("invalid capability request")


def execute_request(request: object) -> dict[str, object]:
    """Run L0 answer routing; capability suggestions stay inert result data."""
    checked = validate_request(
        request, capability="knowledge.answer", mode="preview", mutation_level="L0", scope=(),
        input_fields=_CAPABILITY_INPUT, validator=_validate_capability_input,
    )
    values = checked["input"]
    answered = answer(
        str(values["text"]), store=KnowledgeStore(home=knowledge_home()),
        hospital=str(values.get("hospital", "")), region=str(values.get("region", "")),
        module=str(values.get("module", "")), repo=str(values.get("repo", "")), branch=str(values.get("branch", "")),
    )
    return capability_result(
        checked, status="success", summary="KNOWLEDGE_ANSWER_" + answered.status.upper(), mutation_level="L0", changed=False,
        data={
            "answer": answered.answer, "applicability": list(answered.applicability), "freshness": answered.freshness,
            "confidence_basis": list(answered.confidence_basis), "missing_information": list(answered.missing_information),
            "suggested_capabilities": list(answered.suggested_capabilities), "answer_status": answered.status,
        }, evidence=[dict(item) for item in answered.evidence], audit={
            "credential_class": "none", "external_write_attempted": False, "suggestions_executed": False,
        },
    )


def main(argv: list[str] | None = None) -> int:
    return run_main(argv, execute_request)


if __name__ == "__main__":
    raise SystemExit(main())
