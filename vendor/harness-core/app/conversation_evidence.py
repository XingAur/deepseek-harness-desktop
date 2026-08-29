"""First-class, local-only evidence from a user conversation.

Hosts must export a deliberately selected conversation package.  This module
does not scrape another application's history or interpret an image by OCR;
an image becomes requirement evidence only when the exporting host/user also
records the observation it supports.  That keeps a later model from treating
an opaque attachment as permission to guess.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.llm_client import redact_secrets


CONVERSATION_EVIDENCE_VERSION = "conversation-evidence.v1"
_ROLES = frozenset({"user", "assistant", "system", "tool"})
_FACT_KINDS = frozenset({"user_confirmed", "screenshot_observed", "user_correction"})


def load_conversation_evidence_file(path: str | Path | None) -> dict | None:
    if not path:
        return None
    target = Path(path).expanduser().resolve()
    raw = target.read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"对话证据文件必须是 JSON：{target}") from error
    if not isinstance(payload, Mapping):
        raise ValueError("对话证据文件根节点必须是对象")
    return normalize_conversation_evidence(payload, base_dir=target.parent)


def normalize_conversation_evidence(payload: Mapping[str, Any], *, base_dir: Path | None = None) -> dict:
    messages = _messages(payload.get("messages"))
    message_ids = {item["id"] for item in messages}
    facts = _facts(payload.get("confirmed_facts"), message_ids=message_ids)
    required_call_chain = _text_list(payload.get("required_call_chain"))
    media = _media(payload.get("media") or payload.get("attachments"), base_dir=base_dir)
    if not messages and not facts:
        raise ValueError("对话证据至少需要 messages 或 confirmed_facts")
    if any(item["kind"] in {"screenshot_observed", "user_correction"} for item in facts) and not messages:
        raise ValueError("截图观察或用户纠正必须能回链到对话消息")
    return {
        "version": CONVERSATION_EVIDENCE_VERSION,
        "readonly": True,
        "host": _text(payload.get("host") or payload.get("source") or "manual"),
        "conversation_id": _hash_identifier(payload.get("conversation_id")),
        "messages": messages,
        "media": media,
        "confirmed_facts": facts,
        "required_call_chain": required_call_chain,
        "boundaries": [
            "只接收当前任务被明确导出的对话，不抓取任意聊天历史。",
            "图片文件本身不是语义结论；需要用户或宿主记录观察/纠正文本。",
            "用户已确认事实约束需求理解；源码与运行时证据仍分别验证。",
        ],
    }


def conversation_evidence_to_markdown(evidence: Mapping[str, Any]) -> str:
    lines = ["## 对话与用户确认事实", "", f"- 来源宿主：{_text(evidence.get('host')) or '-'}"]
    facts = evidence.get("confirmed_facts") or []
    if facts:
        lines.extend(["", "### 不得违背的用户确认", ""])
        for fact in facts:
            if not isinstance(fact, Mapping):
                continue
            terms = "、".join(_text_list(fact.get("required_code_terms"))) or "-"
            lines.append(f"- [{_text(fact.get('kind')) or 'user_confirmed'}] {_text(fact.get('statement'))}（需源码核验：{terms}）")
    chain = _text_list(evidence.get("required_call_chain"))
    if chain:
        lines.extend(["", "### 已知业务链路", "", " -> ".join(chain)])
    media = evidence.get("media") or []
    if media:
        lines.extend(["", "### 已绑定媒体", ""])
        for item in media:
            if isinstance(item, Mapping):
                lines.append(f"- {_text(item.get('name')) or '-'}：{_text(item.get('path')) or '-'}")
    return redact_secrets("\n".join(lines))


def conversation_fact_terms(evidence: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not isinstance(evidence, Mapping):
        return ()
    terms: list[str] = []
    for fact in evidence.get("confirmed_facts") or []:
        if isinstance(fact, Mapping) and fact.get("must_not_contradict") is True:
            terms.extend(_text_list(fact.get("required_code_terms")))
    return tuple(dict.fromkeys(terms))


def conversation_code_locator_text(evidence: Mapping[str, Any] | None) -> str:
    """Return only user-confirmed code locators for read-only source discovery.

    This deliberately excludes assistant messages, host metadata and broad
    conversation prose.  It lets the technical scanner search an identifier
    such as ``menZhenTfYjs`` without letting an earlier assistant conjecture
    choose a repository or a business branch.
    """
    if not isinstance(evidence, Mapping):
        return ""
    parts: list[str] = []
    for fact in evidence.get("confirmed_facts") or []:
        if not isinstance(fact, Mapping) or fact.get("must_not_contradict") is not True:
            continue
        parts.extend(_text_list(fact.get("required_code_terms")))
        parts.append(_text(fact.get("statement")))
    parts.extend(_text_list(evidence.get("required_call_chain")))
    return "\n".join(dict.fromkeys(part for part in parts if part))


def _messages(value: object) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value[:100]):
        if not isinstance(raw, Mapping):
            continue
        message_id = _text(raw.get("id") or f"message-{index + 1}")
        role = _text(raw.get("role")).lower()
        content = redact_secrets(_text(raw.get("content") or raw.get("text")))
        if not message_id or message_id in seen or role not in _ROLES or not content:
            continue
        seen.add(message_id)
        result.append({"id": message_id, "role": role, "content": content})
    return result


def _facts(value: object, *, message_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value[:50]):
        if not isinstance(raw, Mapping):
            continue
        fact_id = _text(raw.get("id") or f"fact-{index + 1}")
        kind = _text(raw.get("kind") or "user_confirmed")
        statement = redact_secrets(_text(raw.get("statement")))
        refs = tuple(item for item in _text_list(raw.get("source_message_ids")) if item in message_ids)
        if not fact_id or fact_id in seen or kind not in _FACT_KINDS or not statement:
            continue
        if kind in {"screenshot_observed", "user_correction"} and not refs:
            raise ValueError(f"对话事实 {fact_id} 缺少有效 source_message_ids")
        seen.add(fact_id)
        result.append({
            "id": fact_id,
            "kind": kind,
            "statement": statement,
            "source_message_ids": list(refs),
            "required_code_terms": list(_text_list(raw.get("required_code_terms"))),
            "must_not_contradict": raw.get("must_not_contradict") is True,
        })
    return result


def _media(value: object, *, base_dir: Path | None) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[dict[str, str]] = []
    for raw in value[:30]:
        if not isinstance(raw, Mapping):
            continue
        path = _text(raw.get("path"))
        candidate = Path(path).expanduser()
        if path and not candidate.is_absolute() and base_dir is not None:
            candidate = base_dir / candidate
        resolved = str(candidate.resolve()) if path and candidate.exists() else path
        result.append({"name": _text(raw.get("name")) or Path(path).name, "path": resolved, "sha256": _text(raw.get("sha256"))})
    return result


def _hash_identifier(value: object) -> str:
    text = _text(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


def _text(value: object) -> str:
    return str(value or "").strip()


def _text_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(dict.fromkeys(_text(item) for item in value if _text(item)))
