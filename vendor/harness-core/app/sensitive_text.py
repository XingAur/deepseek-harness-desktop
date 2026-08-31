from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from typing import Any
from urllib.parse import unquote


URL_DECODE_ROUNDS = 3
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?:(?:RSA|EC|OPENSSH|ENCRYPTED) )?PRIVATE KEY-----.*?"
    r"-----END (?:(?:RSA|EC|OPENSSH|ENCRYPTED) )?PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_PRIVATE_KEY_REMAINDER = re.compile(
    r"-----BEGIN (?:(?:RSA|EC|OPENSSH|ENCRYPTED) )?PRIVATE KEY-----.*",
    re.IGNORECASE | re.DOTALL,
)
_SENSITIVE_KEY = (
    r"(?:pat|token|access[_-]?token|refresh[_-]?token|api[_-]?key|secret|password|"
    r"credential|client[_-]?secret|personal[_-]?access[_-]?token|"
    r"aliyun[_-]?devops[_-]?pat|[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*[_-]pat)"
)
_SENSITIVE_KEY_NAME = re.compile(rf"^{_SENSITIVE_KEY}$", re.IGNORECASE)
_NAMED_SECRET_VALUE = re.compile(
    rf"(?P<key>[\"']?{_SENSITIVE_KEY}[\"']?)\s*[:=]\s*"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)",
    re.IGNORECASE,
)
_AUTHORIZATION_VALUE = re.compile(
    r"(?P<key>[\"']?authorization[\"']?)\s*[:=]\s*"
    r"(?P<value>\"(?:bearer|basic)?\s*[^\"]*\"|"
    r"'(?:bearer|basic)?\s*[^']*'|(?:(?:bearer|basic)\s+)?[^\s,;}\]]+)",
    re.IGNORECASE,
)
_STANDALONE_API_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b", re.IGNORECASE)
_CREDENTIAL_URI = re.compile(
    r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@",
    re.IGNORECASE,
)
_MOBILE_NUMBER = re.compile(r"(?<!\d)(?:\+86)?1[3-9](?:[\s.-]?\d){9}(?!\d)")
_IDENTITY_CARD = re.compile(
    r"(?<!\d)\d{6}[\s-]?\d{8}[\s-]?\d{3}[\s-]?[\dXx](?![\dXx])"
)
_PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
_PERCENT_UNICODE_ESCAPE = re.compile(r"%u[0-9A-Fa-f]{4}", re.IGNORECASE)
_HTML_ENTITY = re.compile(
    r"&(?:#[xX][0-9A-Fa-f]+|#[0-9]+|[A-Za-z][A-Za-z0-9]+);"
)
_JSON_UNICODE_ESCAPE = re.compile(r"\\u([0-9A-Fa-f]{4})")
_SENSITIVE_KEY_MARKER = re.compile(_SENSITIVE_KEY, re.IGNORECASE)
_JSON_CONTAINER_START = frozenset(("{", "["))
_JSON_CONTAINER_END = {"}": "{", "]": "["}
_JSON_SCAN_MAX_CHARS = 32_768
_JSON_SCAN_MAX_UTF8_BYTES = 65_536
_JSON_SCAN_MAX_NESTING = 64
_JSON_SCAN_NODE_LIMIT = 10_000
_PUBLIC_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_GITLAB_AUDIT_IDENTITY = re.compile(r"gitlab\.[a-z][a-z0-9_-]{0,63}\.[a-z0-9._-]{1,63}\.[a-z0-9._-]{1,63}(?:\.mr[1-9][0-9]{0,9})?")
_GITLAB_LENGTH_IDENTITY = re.compile(r"gl-h[1-9][0-9]*-[a-z0-9_-]+-g[1-9][0-9]*-[a-z0-9._-]+-p[1-9][0-9]*-[a-z0-9._-]+(?:-m[1-9][0-9]*)?")
_GITHUB_LENGTH_IDENTITY = re.compile(r"gh-o[1-9][0-9]*-[a-z0-9-]+-r[1-9][0-9]*-[a-z0-9._-]+(?:-[piw][1-9][0-9]*)?")
_STRUCTURED_SENSITIVE_KEY = re.compile(
    r"(?:authorization|access(?:token)?|refreshtoken|token|pat|apikey|secret|"
    r"password|passwd|credential|privatekey)",
    re.IGNORECASE,
)
_AUTHORIZATION_SCALAR = re.compile(r"^(?:bearer|basic)\s+\S+", re.IGNORECASE)
_CONNECTION_STRING_SCALAR = re.compile(
    r"^(?:jdbc:)?(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|"
    r"oracle|sqlserver|mssql)://",
    re.IGNORECASE,
)
_OPAQUE_SECRET_SCALAR = re.compile(r"[A-Za-z0-9._~+/=-]{24,}")
_SAFE_MAPPING_MAX_DEPTH = 8
_SAFE_MAPPING_MAX_ITEMS = 64
_SAFE_MAPPING_MAX_KEY_CHARS = 128
_SAFE_MAPPING_MAX_TEXT_CHARS = 512
_SAFE_MAPPING_MAX_JSON_BYTES = 4_096


def normalize_sensitive_text(value: str, *, decode_rounds: int = URL_DECODE_ROUNDS) -> str:
    if not isinstance(value, str):
        raise TypeError("sensitive text must be a string")
    if not isinstance(decode_rounds, int) or isinstance(decode_rounds, bool):
        raise TypeError("decode_rounds must be an integer")
    if decode_rounds < 0 or decode_rounds > URL_DECODE_ROUNDS:
        raise ValueError("decode_rounds is outside the bounded range")
    current = unicodedata.normalize("NFKC", value)
    for _ in range(decode_rounds):
        decoded = unicodedata.normalize("NFKC", unquote(current))
        if decoded == current:
            break
        current = decoded
    return current


def redact_sensitive_text(value: str) -> str:
    redacted = normalize_sensitive_text(value)
    if _has_residual_encoding(redacted):
        return "[REDACTED_ENCODED_TEXT]"
    if _contains_sensitive_json(redacted):
        return "[REDACTED_SENSITIVE_JSON]"
    redacted = _PRIVATE_KEY_BLOCK.sub("[REDACTED_PRIVATE_KEY]", redacted)
    redacted = _PRIVATE_KEY_REMAINDER.sub("[REDACTED_PRIVATE_KEY]", redacted)
    redacted = _AUTHORIZATION_VALUE.sub(
        lambda match: f"{match.group('key')}=[REDACTED_AUTHORIZATION]",
        redacted,
    )
    redacted = _NAMED_SECRET_VALUE.sub(
        lambda match: f"{match.group('key')}=[REDACTED_SECRET]",
        redacted,
    )
    redacted = _STANDALONE_API_KEY.sub("[REDACTED_API_KEY]", redacted)
    redacted = _CREDENTIAL_URI.sub(
        lambda match: f"{match.group('scheme')}[REDACTED_CREDENTIAL]@",
        redacted,
    )
    redacted = _IDENTITY_CARD.sub("[REDACTED_IDENTITY_CARD]", redacted)
    return _MOBILE_NUMBER.sub("[REDACTED_MOBILE]", redacted)


def contains_sensitive_text(value: str) -> bool:
    normalized = normalize_sensitive_text(value)
    return redact_sensitive_text(normalized) != normalized


def is_sensitive_mapping_key(value: object) -> bool:
    """Recognize secret-bearing structured keys across common naming styles."""

    if not isinstance(value, str):
        return True
    normalized = unicodedata.normalize("NFKC", value)
    compact = re.sub(r"[^a-z0-9]", "", normalized.lower())
    return bool(_STRUCTURED_SENSITIVE_KEY.search(compact))


def redact_sensitive_mapping(value: Mapping[str, object]) -> dict[str, object]:
    """Return a recursively redacted and size-bounded audit-safe mapping."""

    if not isinstance(value, Mapping):
        raise ValueError("sensitive_text:mapping_required")
    try:
        redacted = _redact_structured_value(
            value,
            depth=0,
            budget=[_SAFE_MAPPING_MAX_ITEMS],
        )
        if not isinstance(redacted, dict):  # pragma: no cover - root checked above
            raise ValueError("sensitive_text:mapping_required")
        encoded = json.dumps(
            redacted,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (MemoryError, RecursionError, UnicodeError, ValueError, TypeError):
        return {"status": "summary_unavailable"}
    if len(encoded) <= _SAFE_MAPPING_MAX_JSON_BYTES:
        return redacted
    return {
        "status": "summary_truncated",
        "summary_hash": "sha256:" + hashlib.sha256(encoded).hexdigest(),
    }


def _redact_structured_value(
    value: object,
    *,
    depth: int,
    budget: list[int],
) -> object:
    if depth > _SAFE_MAPPING_MAX_DEPTH:
        return "[REDACTED_DEPTH_LIMIT]"
    if budget[0] <= 0:
        return "[REDACTED_ITEM_LIMIT]"
    budget[0] -= 1
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for index, (key, item) in enumerate(value.items()):
            if budget[0] <= 0:
                result["[REDACTED_ITEM_LIMIT]"] = "[REDACTED_ITEM_LIMIT]"
                break
            if is_sensitive_mapping_key(key) or contains_sensitive_text(str(key)):
                budget[0] -= 1
                result[f"[REDACTED_SENSITIVE_KEY_{index}]"] = (
                    "[REDACTED_SENSITIVE_FIELD]"
                )
                continue
            safe_key = _bounded_structured_key(key)
            result[safe_key] = _redact_structured_value(
                item,
                depth=depth + 1,
                budget=budget,
            )
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result_list: list[object] = []
        for item in value:
            if budget[0] <= 0:
                result_list.append("[REDACTED_ITEM_LIMIT]")
                break
            result_list.append(
                _redact_structured_value(item, depth=depth + 1, budget=budget)
            )
        return result_list
    if isinstance(value, str):
        return _redact_structured_text(value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "[REDACTED_NON_FINITE_NUMBER]"
    return "[REDACTED_UNSUPPORTED_VALUE]"


def _bounded_structured_key(value: object) -> str:
    if not isinstance(value, str):
        return "[REDACTED_NON_STRING_KEY]"
    normalized = unicodedata.normalize("NFKC", value)
    safe = normalized.encode("utf-8", "replace").decode("utf-8")
    if len(safe) <= _SAFE_MAPPING_MAX_KEY_CHARS:
        return safe
    digest = hashlib.sha256(safe.encode("utf-8")).hexdigest()[:16]
    return f"[REDACTED_LONG_KEY_sha256:{digest}]"


def _redact_structured_text(value: str) -> str:
    normalized = normalize_sensitive_text(value)
    safe = normalized.encode("utf-8", "replace").decode("utf-8")
    if len(safe) > _SAFE_MAPPING_MAX_TEXT_CHARS:
        digest = hashlib.sha256(safe.encode("utf-8")).hexdigest()
        return f"[REDACTED_TEXT_TRUNCATED_sha256:{digest}]"
    if _AUTHORIZATION_SCALAR.search(safe):
        return "[REDACTED_AUTHORIZATION]"
    if _CONNECTION_STRING_SCALAR.search(safe):
        return "[REDACTED_CONNECTION_STRING]"
    if (
        _OPAQUE_SECRET_SCALAR.fullmatch(safe)
        and any(character.isalpha() for character in safe)
        and any(character.isdigit() for character in safe)
    ):
        return "[REDACTED_OPAQUE_VALUE]"
    return redact_sensitive_text(safe)


def validate_public_identifier(
    value: object,
    *,
    allowed_values: Collection[str] | None = None,
) -> str:
    """Return a safe audit identifier or fail without echoing the input."""

    if not isinstance(value, str):
        raise ValueError("provider_audit_input_invalid")
    normalized = value.strip()
    if (
        not normalized
        or contains_sensitive_text(normalized)
        or _PUBLIC_IDENTIFIER.fullmatch(normalized) is None
        or (allowed_values is not None and normalized not in allowed_values)
    ):
        raise ValueError("provider_audit_input_invalid")
    return normalized


def validate_audit_alias(value: object, *, allow_empty: bool = False) -> str:
    """Validate an audit metadata alias without normalizing unsafe input."""

    if not isinstance(value, str):
        raise ValueError("provider_audit_input_invalid")
    normalized = value.strip()
    if allow_empty and value == "":
        return ""
    if (
        not normalized
        or value != normalized
        or (
            _PUBLIC_IDENTIFIER.fullmatch(normalized) is None
            and _GITLAB_AUDIT_IDENTITY.fullmatch(normalized) is None
            and _GITLAB_LENGTH_IDENTITY.fullmatch(normalized) is None
            and _GITHUB_LENGTH_IDENTITY.fullmatch(normalized) is None
        )
        or (
            _GITLAB_AUDIT_IDENTITY.fullmatch(normalized) is None
            and _GITLAB_LENGTH_IDENTITY.fullmatch(normalized) is None
            and _GITHUB_LENGTH_IDENTITY.fullmatch(normalized) is None
            and _redact_structured_text(normalized) != normalized
        )
    ):
        raise ValueError("provider_audit_input_invalid")
    return normalized


def contains_sensitive_scalar_text(value: str) -> bool:
    """Recognize secret-shaped scalar text, including opaque token values."""

    if not isinstance(value, str):
        raise TypeError("sensitive text must be a string")
    normalized = normalize_sensitive_text(value)
    return _redact_structured_text(normalized) != normalized


def _has_residual_encoding(value: str) -> bool:
    return bool(
        _PERCENT_ESCAPE.search(value)
        or _PERCENT_UNICODE_ESCAPE.search(value)
        or _HTML_ENTITY.search(value)
    )


def _contains_sensitive_json(value: str) -> bool:
    for stripped in _iter_json_candidates(value):
        unicode_escape_present = bool(_JSON_UNICODE_ESCAPE.search(stripped))
        shadow = _shadow_normalize_json_unicode(stripped)
        if shadow != stripped and _contains_plain_sensitive_text(shadow):
            return True
        if _json_candidate_exceeds_limits(stripped):
            return True
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            if unicode_escape_present or _SENSITIVE_KEY_MARKER.search(shadow):
                return True
            continue
        except (MemoryError, RecursionError):
            return True
        if _json_value_contains_sensitive(parsed):
            return True
    return False


def _iter_json_candidates(value: str):
    seen: set[str] = set()
    start: int | None = None
    stack: list[str] = []
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if start is None:
            if character in _JSON_CONTAINER_START:
                start = index
                stack = [character]
            continue
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"\"", "'"}:
            quote = character
        elif character in _JSON_CONTAINER_START:
            stack.append(character)
        elif character in _JSON_CONTAINER_END:
            if not stack or stack[-1] != _JSON_CONTAINER_END[character]:
                candidate = value[start : index + 1].strip()
                if candidate and candidate not in seen:
                    seen.add(candidate)
                    yield candidate
                start = None
                stack = []
                quote = None
                escaped = False
            else:
                stack.pop()
                if not stack:
                    candidate = value[start : index + 1].strip()
                    if candidate and candidate not in seen:
                        seen.add(candidate)
                        yield candidate
                    start = None
    if start is not None:
        candidate = value[start:].strip()
        if candidate and candidate not in seen:
            yield candidate


def _shadow_normalize_json_unicode(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        codepoint = int(match.group(1), 16)
        if codepoint <= 0x7F:
            return chr(codepoint)
        return match.group(0)

    return _JSON_UNICODE_ESCAPE.sub(replace, value)


def _json_candidate_exceeds_limits(value: str) -> bool:
    if len(value) > _JSON_SCAN_MAX_CHARS:
        return True
    try:
        if len(value.encode("utf-8")) > _JSON_SCAN_MAX_UTF8_BYTES:
            return True
    except UnicodeEncodeError:
        return True
    return _json_nesting_exceeds_limit(value)


def _json_nesting_exceeds_limit(value: str) -> bool:
    depth = 0
    quote: str | None = None
    escaped = False
    for character in value:
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"\"", "'"}:
            quote = character
        elif character in _JSON_CONTAINER_START:
            depth += 1
            if depth > _JSON_SCAN_MAX_NESTING:
                return True
        elif character in _JSON_CONTAINER_END and depth:
            depth -= 1
    return False


def _json_value_contains_sensitive(root: Any) -> bool:
    pending = [root]
    visited = 0
    while pending:
        visited += 1
        if visited > _JSON_SCAN_NODE_LIMIT:
            return True
        value = pending.pop()
        if isinstance(value, dict):
            for key, item in value.items():
                normalized_key = unicodedata.normalize("NFKC", str(key)).strip()
                if normalized_key.lower() == "authorization":
                    return True
                if _SENSITIVE_KEY_NAME.fullmatch(normalized_key):
                    return True
                pending.append(item)
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str):
            normalized = normalize_sensitive_text(value)
            if _has_residual_encoding(normalized) or _contains_plain_sensitive_text(normalized):
                return True
    return False


def _contains_plain_sensitive_text(value: str) -> bool:
    return any(
        pattern.search(value)
        for pattern in (
            _PRIVATE_KEY_BLOCK,
            _PRIVATE_KEY_REMAINDER,
            _AUTHORIZATION_VALUE,
            _NAMED_SECRET_VALUE,
            _STANDALONE_API_KEY,
            _CREDENTIAL_URI,
            _IDENTITY_CARD,
            _MOBILE_NUMBER,
        )
    )
