from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.capability_contracts import MutationLevel
from app.capability_registry import CapabilityDescriptor, CapabilityRegistry
from app.capability_runtime import CapabilityRuntime
from app.capability_service import CapabilityService
from app.harness import CapabilityWorkflowOrchestrator, CapabilityWorkflowResult
from app.knowledge_index import query_knowledge_index
from app.manager_provider_repository import ManagerProviderRepository
from app.plugin_inventory import verify_plugin_inventory
from app.sensitive_text import contains_sensitive_text, redact_sensitive_text
from app.task_intent_service import (
    TaskIntentRoutingResult,
    require_knowledge_route,
)
from tools.capability_check import load_runtime_config


KNOWLEDGE_CONSULTATION_SCHEMA_VERSION = "his-knowledge-consultation.v1"
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CAPABILITY_CONFIG = _PROJECT_ROOT / "config" / "capabilities.json"
_ANSWER_CAPABILITY = "knowledge.answer"
_ANSWER_PROVIDER = "his-knowledge"
_ANSWER_SCOPES = ("knowledge:answer",)
_MANAGER_MODULE_SCOPE = "Harness"
_ANSWER_STATUSES = frozenset({
    "answered", "needs_live_evidence", "needs_clarification", "conflicted", "unsupported",
})
_PUBLIC_FIELDS = (
    "answer", "answer_status", "applicability", "freshness", "confidence_basis",
    "missing_information", "suggested_capabilities", "evidence",
)


def build_manager_knowledge_capability_service(
    *,
    config_path: str | Path = _DEFAULT_CAPABILITY_CONFIG,
) -> CapabilityService:
    """Build the pinned, L0-only runtime used by Manager question mode.

    The config and inventory are the only authority for plugin roots and the
    knowledge home.  This deliberately does not accept request-time paths or
    inherit arbitrary process environment values.
    """

    resolved_config = Path(config_path).resolve(strict=True)
    config = load_runtime_config(str(resolved_config))
    if config.routing_mode != "enforce" or config.external_writes_default:
        raise ValueError("manager knowledge runtime must be enforce and read-only")
    full_registry = CapabilityRegistry.from_plugin_roots(config.plugin_roots)
    verify_plugin_inventory(
        resolved_config.parent / "plugin_inventory.json",
        list(config.plugin_roots),
        registry=full_registry,
    )
    descriptor = full_registry.resolve(_ANSWER_CAPABILITY, _ANSWER_PROVIDER)
    _require_answer_descriptor(descriptor)
    restricted_registry = CapabilityRegistry((descriptor,))
    runtime = CapabilityRuntime(
        restricted_registry,
        external_writes_default=False,
        default_timeout_seconds=config.default_timeout_seconds,
        environment_allowlist=("HIS_KNOWLEDGE_HOME",),
    )
    return CapabilityService(
        runtime,
        routing_mode="enforce",
        capability_environments={
            (_ANSWER_CAPABILITY, _ANSWER_PROVIDER): {
                "HIS_KNOWLEDGE_HOME": config.knowledge_home,
            }
        },
    )


def consult_knowledge(
    query: str,
    *,
    repository: ManagerProviderRepository,
    routing_result: TaskIntentRoutingResult,
    capability_service: CapabilityService | None = None,
    legacy_retrieval: Callable[[str | Path, str], Mapping[str, object]] | None = None,
    knowledge_home: str | Path | None = None,
) -> dict[str, object]:
    """Answer a verified question through formal ``knowledge.answer`` only.

    ``legacy_retrieval`` is an explicit test/legacy seam.  It is never chosen
    by the production default, so an ambient ``HIS_KNOWLEDGE_HOME`` cannot
    silently reinstate the retired Obsidian index route.
    """

    require_knowledge_route(routing_result)
    original_query = _required_query(query)
    if contains_sensitive_text(original_query):
        return _record_sensitive_query_rejection(
            original_query,
            repository=repository,
        )
    if legacy_retrieval is not None:
        if knowledge_home is None:
            raise ValueError("legacy knowledge retrieval requires knowledge_home")
        return _consult_legacy(
            original_query,
            knowledge_home=knowledge_home,
            repository=repository,
            retrieval=legacy_retrieval,
        )

    service = capability_service or build_manager_knowledge_capability_service()
    workflow = CapabilityWorkflowOrchestrator(
        _QuestionResultValidatingService(service)
    ).run_question(text=original_query)
    return _record_formal_workflow(
        original_query,
        repository=repository,
        workflow=workflow,
    )


def redact_consultation_query(query: str) -> str:
    return redact_sensitive_text(query)


def _record_sensitive_query_rejection(
    query: str,
    *,
    repository: ManagerProviderRepository,
) -> dict[str, object]:
    """Keep sensitive query material out of all retrieval providers."""

    result = _closed_result("knowledge_insufficient")
    repository.record_knowledge_consultation(
        query_redacted=redact_consultation_query(query),
        query_hash="sha256:" + hashlib.sha256(query.encode("utf-8")).hexdigest(),
        retrieval_status=str(result["retrieval_status"]),
        citations=(),
        model_used=False,
    )
    return result


class _QuestionResultValidatingService:
    """Preserve the runtime boundary while retaining C2 result validation."""

    def __init__(self, service: object) -> None:
        self._service = service

    def route(self, request: object, **kwargs: object) -> object:
        scoped_request = replace(
            request,
            input={
                **request.input,
                "module": _MANAGER_MODULE_SCOPE,
            },
        )
        route = self._service.route(scoped_request, **kwargs)
        result = getattr(route, "result", None)
        if isinstance(result, Mapping) and _matches_question_result(scoped_request, result):
            return route
        if not isinstance(result, Mapping):
            return route
        return SimpleNamespace(
            result={
                "status": "success",
                "request_id": getattr(scoped_request, "request_id", ""),
                "capability": _ANSWER_CAPABILITY,
                "provider": _ANSWER_PROVIDER,
                "mutation_level": MutationLevel.L0.name,
                "changed": False,
                "data": {
                    "answer_status": "unsupported",
                    "provider_output_unsafe": True,
                },
                "evidence": [],
            }
        )


def _matches_question_result(request: object, result: Mapping[str, object]) -> bool:
    return (
        result.get("request_id") == getattr(request, "request_id", None)
        and result.get("capability") == _ANSWER_CAPABILITY
        and result.get("provider") == _ANSWER_PROVIDER
        and result.get("mutation_level") == MutationLevel.L0.name
        and result.get("changed") is False
    )


def _require_answer_descriptor(descriptor: CapabilityDescriptor) -> None:
    if (
        descriptor.plugin != "his-knowledge"
        or descriptor.name != _ANSWER_CAPABILITY
        or descriptor.provider != _ANSWER_PROVIDER
        or descriptor.contract_version != "knowledge-answer.v1"
        or descriptor.mutation_level is not MutationLevel.L0
        or descriptor.credential_class != "none"
        or not descriptor.enabled
        or descriptor.scopes != _ANSWER_SCOPES
    ):
        raise ValueError("manager knowledge capability contract is not pinned")


def _record_formal_workflow(
    query: str,
    *,
    repository: ManagerProviderRepository,
    workflow: CapabilityWorkflowResult,
) -> dict[str, object]:
    projected = _project_formal_workflow(workflow)
    citations = [
        str(item["stable_key"])
        for item in projected.get("evidence", [])
        if isinstance(item, Mapping) and isinstance(item.get("stable_key"), str)
    ]
    repository.record_knowledge_consultation(
        query_redacted=redact_consultation_query(query),
        query_hash="sha256:" + hashlib.sha256(query.encode("utf-8")).hexdigest(),
        retrieval_status=str(projected["retrieval_status"]),
        citations=citations,
        model_used=False,
    )
    return projected


def _project_formal_workflow(workflow: object) -> dict[str, object]:
    if not isinstance(workflow, CapabilityWorkflowResult) or workflow.events != (_ANSWER_CAPABILITY,):
        return _closed_result("knowledge_provider_unavailable")
    data = workflow.data
    if not isinstance(data, Mapping):
        return _closed_result("knowledge_provider_output_unsafe")
    if data.get("provider_output_unsafe") is True:
        return _closed_result("knowledge_provider_output_unsafe")
    answer_status = data.get("answer_status")
    if not isinstance(answer_status, str) or answer_status not in _ANSWER_STATUSES:
        return _closed_result("knowledge_provider_output_unsafe")
    if workflow.status != answer_status or data.get("provider_unavailable") is True:
        return _closed_result("knowledge_provider_unavailable")
    public = _public_data(data)
    if public is None:
        return _closed_result("knowledge_provider_output_unsafe")
    if answer_status == "answered":
        evidence = public["evidence"]
        if not public["answer"] or not evidence or not public["applicability"] or not public["freshness"] or not public["confidence_basis"]:
            return _closed_result("knowledge_provider_output_unsafe")
        return {
            "schema_version": KNOWLEDGE_CONSULTATION_SCHEMA_VERSION,
            "answerable": True,
            "model_used": False,
            "model_escalation_required": False,
            "retrieval_status": "knowledge_hit",
            "message": "已从本地已批准知识中检索到可引用来源。",
            **public,
            "citations": [item["stable_key"] for item in evidence],
            "results": [],
        }
    return _closed_result("knowledge_insufficient", answer_status=answer_status, public=public)


def _public_data(data: Mapping[str, Any]) -> dict[str, object] | None:
    if set(data) - set(_PUBLIC_FIELDS):
        # A result can contain no hidden side channel; Manager does not expose
        # raw provider fields even if a future plugin adds one.
        return None
    answer = _safe_text(data.get("answer"), required=True)
    freshness = _safe_text(data.get("freshness"), required=True)
    if answer is None or freshness is None:
        return None
    values: dict[str, object] = {"answer": answer, "answer_status": data.get("answer_status"), "freshness": freshness}
    for field in ("applicability", "confidence_basis", "missing_information", "suggested_capabilities"):
        items = _safe_text_list(data.get(field, []))
        if items is None:
            return None
        values[field] = items
    evidence = _safe_evidence(data.get("evidence"))
    if evidence is None:
        return None
    values["evidence"] = evidence
    return values


def _safe_text(value: object, *, required: bool) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) > 4000 or contains_sensitive_text(text):
        return None
    if required and not text:
        return None
    return text


def _safe_text_list(value: object) -> list[str] | None:
    if not isinstance(value, (list, tuple)) or len(value) > 100:
        return None
    public: list[str] = []
    for item in value:
        text = _safe_text(item, required=True)
        if text is None:
            return None
        public.append(text)
    return public


def _safe_evidence(value: object) -> list[dict[str, object]] | None:
    if not isinstance(value, (list, tuple)) or len(value) > 20:
        return None
    public: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) - {"stable_key", "title", "authority", "version_label", "source_refs", "excerpt"}:
            return None
        copied: dict[str, object] = {}
        for field in ("stable_key", "title", "authority", "version_label", "excerpt"):
            text = _safe_text(item.get(field), required=True)
            if text is None:
                return None
            copied[field] = text
        refs = item.get("source_refs")
        if not isinstance(refs, list) or not refs or len(refs) > 20:
            return None
        safe_refs: list[dict[str, str]] = []
        for ref in refs:
            if not isinstance(ref, Mapping) or not ref or set(ref) - {"claim_level", "ref", "kind", "path", "url", "version", "commit"}:
                return None
            safe_ref: dict[str, str] = {}
            for key, raw in ref.items():
                text = _safe_text(raw, required=True)
                if text is None:
                    return None
                safe_ref[str(key)] = text
            safe_refs.append(safe_ref)
        copied["source_refs"] = safe_refs
        public.append(copied)
    return public


def _closed_result(
    retrieval_status: str,
    *,
    answer_status: str = "unsupported",
    public: Mapping[str, object] | None = None,
) -> dict[str, object]:
    result = {
        "schema_version": KNOWLEDGE_CONSULTATION_SCHEMA_VERSION,
        "answerable": False,
        "model_used": False,
        "model_escalation_required": False,
        "retrieval_status": retrieval_status,
        "message": "本地已批准知识不足或提供方结果不安全，当前不能直接回答，也不会自动调用模型。",
        "candidate_suggestion": "建议整理证据并创建 knowledge candidate，人工审核后再晋升。",
        "candidate_recommendation": {"candidate_type": "knowledge.candidate", "state": "candidate", "requires_reviewer": True, "auto_promote": False},
        "answer_status": answer_status,
        "results": [],
        "citations": [],
        "evidence": [],
    }
    if public is not None:
        # Status metadata may guide a later reviewed follow-up, but a
        # non-answer must never leak provider prose or evidence as an answer.
        for field in (
            "applicability",
            "freshness",
            "confidence_basis",
            "missing_information",
            "suggested_capabilities",
        ):
            result[field] = public[field]
        result["evidence"] = []
    return result


def _consult_legacy(
    query: str,
    *,
    knowledge_home: str | Path,
    repository: ManagerProviderRepository,
    retrieval: Callable[[str | Path, str], Mapping[str, object]],
) -> dict[str, object]:
    response = retrieval(knowledge_home, query)
    answerable = bool(response.get("answerable"))
    citations = list(response.get("citations") or []) if answerable else []
    retrieval_status = "knowledge_hit" if answerable else "knowledge_insufficient"
    repository.record_knowledge_consultation(
        query_redacted=redact_consultation_query(query),
        query_hash="sha256:" + hashlib.sha256(query.encode("utf-8")).hexdigest(),
        retrieval_status=retrieval_status,
        citations=citations,
        model_used=False,
    )
    if answerable:
        return {"schema_version": KNOWLEDGE_CONSULTATION_SCHEMA_VERSION, "answerable": True, "model_used": False, "model_escalation_required": False, "retrieval_status": retrieval_status, "message": "已从本地已批准知识中检索到可引用来源。", "results": list(response.get("results") or []), "citations": citations}
    return _closed_result("knowledge_insufficient")


def _required_query(query: object) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("knowledge query must be a non-empty string")
    return query.strip()
