from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from app import database
from app.node_runtime import sha256_json
from app.runtime_policy import assert_model_provider_smoke_allowed, assert_runtime_mode_allowed
from app.runtime_preflight import choose_private_runtime_root


MODEL_PROVIDER_RUNTIME_SCHEMA_VERSION = "1.1-controlled-model-provider-smoke-layers"
MODEL_PROVIDER_POLICY_SCHEMA_VERSION = "1.0-controlled-model-provider-profiles"
FIXED_SMOKE_RESPONSE = "SMOKE_OK"
MAX_TIMEOUT_SECONDS = 45
MAX_RESPONSE_BYTES = 64 * 1024
_CONTROLLED_SMOKE_PERMIT = object()


class ProviderSmokeTransportError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class ProviderSmokeTransport(Protocol):
    def request(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        api_key: str,
        timeout_seconds: int,
        _controlled_smoke_permit: object | None = None,
    ) -> dict[str, Any]: ...


class OpenAICompatibleSmokeTransport:
    """One-shot OpenAI-compatible HTTP transport with no retry path."""

    def request(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        api_key: str,
        timeout_seconds: int,
        _controlled_smoke_permit: object | None = None,
    ) -> dict[str, Any]:
        if _controlled_smoke_permit is _CONTROLLED_SMOKE_PERMIT:
            assert_model_provider_smoke_allowed()
        else:
            assert_runtime_mode_allowed("openai")
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        # Never inherit HTTPS_PROXY/ALL_PROXY and never follow a Location.  The
        # Manager resolver has already bound this request to one allowlisted
        # HTTPS authority; following even a same-host redirect would change the
        # reviewed endpoint path and can replay the bearer credential.
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _RejectSmokeRedirect(),
        )
        try:
            # ``build_opener`` retains urllib's default HTTPS handler, which
            # performs normal TLS certificate verification.
            with opener.open(request, timeout=timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                exc.close()
                raise ProviderSmokeTransportError(
                    "redirect_not_allowed",
                    "model smoke redirect rejected",
                ) from None
            detail = exc.read(4096).decode("utf-8", errors="replace")
            exc.close()
            raise ProviderSmokeTransportError(
                "http_error",
                f"HTTP {exc.code}: {detail}",
            ) from exc
        except urllib.error.URLError as exc:
            raise ProviderSmokeTransportError("network_error", str(exc.reason)) from exc
        except TimeoutError as exc:
            raise ProviderSmokeTransportError("timeout", "model smoke request timed out") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ProviderSmokeTransportError("response_too_large", "provider response exceeded 64 KiB")
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderSmokeTransportError("response_json_invalid", "provider response is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ProviderSmokeTransportError("response_shape_invalid", "provider response must be a JSON object")
        return parsed


class _RejectSmokeRedirect(urllib.request.HTTPRedirectHandler):
    """Fail closed before urllib can replay a bearer request to Location."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


@dataclass(frozen=True)
class ResolvedProviderProfile:
    profile_key: str
    provider_kind: str
    endpoint_url: str
    endpoint_host: str
    model: str
    api_key: str
    credential_key_names: dict[str, str]
    timeout_seconds: int
    max_output_tokens: int


class ControlledModelProviderRuntime:
    """Explicitly authorized, fixed-prompt, single-node provider smoke boundary."""

    def __init__(self, *, transport: ProviderSmokeTransport | None = None) -> None:
        try:
            database.init_db()
        except (OSError, sqlite3.OperationalError):
            database.DB_PATH = choose_private_runtime_root(prefix="his_harness_model_runtime_") / "harness.sqlite"
            database.init_db()
        self.transport = transport or OpenAICompatibleSmokeTransport()

    def run_smoke(
        self,
        *,
        profile_policy_path: Path,
        profile_key: str,
        credentials_path: Path,
        allow_credentials: bool,
        allow_network: bool,
        authorization_id: str,
        allow_frozen_test_transport: bool = False,
    ) -> dict[str, Any]:
        self._require_activation_gate(
            allow_credentials=allow_credentials,
            allow_network=allow_network,
            authorization_id=authorization_id,
        )
        assert_model_provider_smoke_allowed()
        policy = load_provider_policy(profile_policy_path)
        profile = resolve_provider_profile(
            policy=policy,
            profile_key=profile_key,
            credentials_path=credentials_path,
        )
        authorization_hash = "sha256:" + hashlib.sha256(
            authorization_id.strip().encode("utf-8")
        ).hexdigest()
        return self._run_resolved_smoke(
            profile=profile,
            authorization_hash=authorization_hash,
            safe_error_details=False,
        )

    def run_manager_smoke(
        self,
        *,
        profile: ResolvedProviderProfile,
        execution_key: str,
    ) -> dict[str, Any]:
        """Run the fixed smoke from a just-consumed Manager execution context.

        ``execution_key`` is a local plan identity, never the one-use authorization
        token.  It prevents a credential or authorization value from entering the
        model payload, smoke store, exception text, or returned evidence.
        """

        assert_model_provider_smoke_allowed()
        if not isinstance(execution_key, str) or not execution_key:
            raise ValueError("manager model smoke execution key is required")
        authorization_hash = "sha256:" + hashlib.sha256(
            ("manager-model-smoke:" + execution_key).encode("utf-8")
        ).hexdigest()
        return self._run_resolved_smoke(
            profile=profile,
            authorization_hash=authorization_hash,
            safe_error_details=True,
        )

    def _run_resolved_smoke(
        self,
        *,
        profile: ResolvedProviderProfile,
        authorization_hash: str,
        safe_error_details: bool,
    ) -> dict[str, Any]:
        payload = build_fixed_smoke_payload(profile)
        request_hash = sha256_json(
            {
                "schema_version": MODEL_PROVIDER_RUNTIME_SCHEMA_VERSION,
                "profile_key": profile.profile_key,
                "provider_kind": profile.provider_kind,
                "endpoint_host": profile.endpoint_host,
                "model": profile.model,
                "payload": payload,
            }
        )
        smoke_key = sha256_json(
            {
                "request_hash": request_hash,
                "authorization_hash": authorization_hash,
            }
        )
        existing = database.get_model_provider_smoke_by_key(smoke_key)
        if existing:
            return self._snapshot(int(existing["id"]), idempotent=True)

        started_at = now_iso()
        status = "failed_transport"
        transport_status = "not_run"
        protocol_status = "not_run"
        marker_status = "not_run"
        response_hash = ""
        usage: dict[str, int] = {}
        error_code = ""
        error_detail = ""
        response_verified = False
        events: list[tuple[str, str, dict[str, Any]]] = [
            (
                "authorized",
                "passed",
                {
                    "credentials_allowed": True,
                    "network_allowed": True,
                    "authorization_hash": authorization_hash,
                },
            ),
            (
                "credentials_resolved",
                "passed",
                {
                    "credential_key_names": profile.credential_key_names,
                    "endpoint_host": profile.endpoint_host,
                    "model": profile.model,
                },
            ),
        ]

        try:
            response = self.transport.request(
                url=profile.endpoint_url,
                payload=payload,
                api_key=profile.api_key,
                timeout_seconds=profile.timeout_seconds,
                _controlled_smoke_permit=_CONTROLLED_SMOKE_PERMIT,
            )
            transport_status = "passed"
            events.append(("network_completed", "passed", {"attempt_count": 1}))
        except ProviderSmokeTransportError as exc:
            transport_status = "failed"
            error_code = exc.code
            error_detail = _safe_error_detail(
                exc.detail,
                profile.api_key,
                strict=safe_error_details,
            )
            events.extend(
                (
                    ("network_completed", "failed", {"attempt_count": 1, "error_code": error_code}),
                    ("validated", "failed", {"response_verified": False, "error_code": error_code}),
                )
            )
        except Exception as exc:
            transport_status = "failed"
            error_code = "transport_unexpected_error"
            error_detail = _safe_error_detail(
                str(exc),
                profile.api_key,
                strict=safe_error_details,
            )
            events.extend(
                (
                    ("network_completed", "failed", {"attempt_count": 1, "error_code": error_code}),
                    ("validated", "failed", {"response_verified": False, "error_code": error_code}),
                )
            )
        else:
            try:
                content = extract_response_content(response)
                protocol_status = "passed"
                usage = extract_usage(response)
                response_hash = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
                response_verified = content.strip() == FIXED_SMOKE_RESPONSE
                if response_verified:
                    status = "passed"
                    marker_status = "passed"
                else:
                    status = "failed_protocol"
                    marker_status = "failed"
                    error_code = "smoke_response_mismatch"
                    error_detail = "provider did not return the exact fixed smoke marker"
            except ProviderSmokeTransportError as exc:
                status = "failed_protocol"
                protocol_status = "failed"
                error_code = exc.code
                error_detail = _safe_error_detail(
                    exc.detail,
                    profile.api_key,
                    strict=safe_error_details,
                )
            events.append(
                (
                    "validated",
                    "passed" if response_verified else "failed",
                    {
                        "response_verified": response_verified,
                        "response_hash": response_hash,
                        "usage": usage,
                        "error_code": error_code,
                        "transport_status": transport_status,
                        "protocol_status": protocol_status,
                        "marker_status": marker_status,
                    },
                )
            )

        completed_at = now_iso()
        smoke_id = database.add_model_provider_smoke(
            {
                "smoke_key": smoke_key,
                "profile_key": profile.profile_key,
                "provider_kind": profile.provider_kind,
                "endpoint_host": profile.endpoint_host,
                "model": profile.model,
                "status": status,
                "transport_status": transport_status,
                "protocol_status": protocol_status,
                "marker_status": marker_status,
                "authorization_hash": authorization_hash,
                "credential_key_names": profile.credential_key_names,
                "request_hash": request_hash,
                "response_hash": response_hash,
                "usage": usage,
                "timeout_seconds": profile.timeout_seconds,
                "error_code": error_code,
                "error_detail": error_detail,
                "started_at": started_at,
                "completed_at": completed_at,
            }
        )
        events.append(("persisted", "passed", {"status": status}))
        for sequence, (event_type, event_status, details) in enumerate(events, start=1):
            database.add_model_provider_smoke_event(
                {
                    "smoke_id": smoke_id,
                    "sequence": sequence,
                    "event_type": event_type,
                    "status": event_status,
                    "details": details,
                }
            )
        return self._snapshot(smoke_id, idempotent=False)

    def get_smoke(self, smoke_id: int) -> dict[str, Any]:
        return self._snapshot(smoke_id, idempotent=False)

    @staticmethod
    def _require_activation_gate(
        *,
        allow_credentials: bool,
        allow_network: bool,
        authorization_id: str,
    ) -> None:
        if not allow_credentials or not allow_network:
            raise PermissionError("真实模型 smoke 必须同时打开凭证读取与网络调用双开关")
        if not authorization_id.strip():
            raise PermissionError("真实模型 smoke 必须提供本次用户授权标识")

    @staticmethod
    def _snapshot(smoke_id: int, *, idempotent: bool) -> dict[str, Any]:
        smoke = database.get_model_provider_smoke(smoke_id)
        if not smoke:
            raise ValueError(f"model provider smoke not found: {smoke_id}")
        return {
            "schema_version": MODEL_PROVIDER_RUNTIME_SCHEMA_VERSION,
            "smoke": smoke,
            "events": database.list_model_provider_smoke_events(smoke_id),
            "connectivity_verified": (
                smoke["transport_status"] == "passed"
                and smoke["protocol_status"] == "passed"
            ),
            "response_verified": smoke["marker_status"] == "passed",
            "idempotent": idempotent,
            "single_node_only": True,
            "business_valid": False,
            "dag_enabled": False,
            "tool_execution_enabled": False,
            "retry_enabled": False,
            "boundaries": [
                "single-node-smoke-only",
                "固定提示且不保存模型响应原文。",
                "不接入动态 DAG、业务源码、PG、Git 或外部系统动作。",
            ],
        }


def load_provider_policy(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"provider profile policy not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("provider profile policy must be valid JSON") from exc
    if not isinstance(parsed, dict) or parsed.get("schema_version") != MODEL_PROVIDER_POLICY_SCHEMA_VERSION:
        raise ValueError("provider profile policy schema_version invalid")
    if not isinstance(parsed.get("profiles"), dict):
        raise ValueError("provider profile policy profiles must be an object")
    return parsed


def resolve_provider_profile(
    *,
    policy: dict[str, Any],
    profile_key: str,
    credentials_path: Path,
) -> ResolvedProviderProfile:
    raw_profile = policy["profiles"].get(profile_key)
    if not isinstance(raw_profile, dict):
        raise ValueError(f"provider profile not found: {profile_key}")
    if raw_profile.get("enabled") is not True or raw_profile.get("smoke_enabled") is not True:
        raise PermissionError(f"provider profile is not enabled for smoke: {profile_key}")
    provider_kind = str(raw_profile.get("provider_kind") or "").strip()
    if provider_kind != "openai_compatible":
        raise ValueError("v0.57 smoke only supports openai_compatible provider profiles")
    credentials = read_credentials(credentials_path)
    key_policy = raw_profile.get("credential_keys")
    if not isinstance(key_policy, dict):
        raise ValueError("provider profile credential_keys must be an object")
    api_key, api_key_name = resolve_credential(credentials, key_policy.get("api_key"), "api_key")
    base_url, base_url_name = resolve_credential(credentials, key_policy.get("base_url"), "base_url")
    model, model_name = resolve_credential(credentials, key_policy.get("model"), "model")
    endpoint_url, endpoint_host = validate_endpoint(base_url, raw_profile.get("allowed_endpoint_hosts"))
    timeout_seconds = bounded_positive_int(
        raw_profile.get("timeout_seconds", 20),
        field="timeout_seconds",
        maximum=MAX_TIMEOUT_SECONDS,
    )
    max_output_tokens = bounded_positive_int(
        raw_profile.get("max_output_tokens", 16),
        field="max_output_tokens",
        maximum=64,
    )
    return ResolvedProviderProfile(
        profile_key=profile_key,
        provider_kind=provider_kind,
        endpoint_url=endpoint_url,
        endpoint_host=endpoint_host,
        model=model,
        api_key=api_key,
        credential_key_names={
            "api_key": api_key_name,
            "base_url": base_url_name,
            "model": model_name,
        },
        timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens,
    )


def resolve_manager_provider_profile(
    *,
    profile_key: str,
    connection: dict[str, object],
    api_key: str,
) -> ResolvedProviderProfile:
    """Resolve a typed Manager Profile after execution authorization only.

    The Manager stores the secret separately, encrypted.  This resolver accepts
    only the decrypted API key supplied by the execution Context, and it never
    accepts a file path, environment key name, arbitrary endpoint, or arbitrary
    model from a request.
    """

    if not isinstance(profile_key, str) or not profile_key:
        raise ValueError("manager model profile key is invalid")
    if not isinstance(connection, dict):
        raise ValueError("manager model connection is invalid")
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("manager model api key is unavailable")
    provider_kind = str(connection.get("provider_kind") or "").strip()
    if provider_kind != "openai_compatible":
        raise ValueError("manager model provider kind is not allowed")
    base_url = str(connection.get("base_url") or "").strip()
    allowed_endpoint_host = str(connection.get("allowed_endpoint_host") or "").strip().lower()
    endpoint_url, endpoint_host = validate_endpoint(base_url, [allowed_endpoint_host])
    model = str(connection.get("model") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", model):
        raise ValueError("manager model alias is invalid")
    timeout_seconds = _manager_bounded_positive_int(
        connection.get("timeout_seconds"),
        field="timeout_seconds",
        maximum=MAX_TIMEOUT_SECONDS,
    )
    max_output_tokens = _manager_bounded_positive_int(
        connection.get("max_output_tokens"),
        field="max_output_tokens",
        maximum=64,
    )
    return ResolvedProviderProfile(
        profile_key=profile_key,
        provider_kind=provider_kind,
        endpoint_url=endpoint_url,
        endpoint_host=endpoint_host,
        model=model,
        api_key=api_key,
        credential_key_names={"api_key": "manager_encrypted"},
        timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens,
    )


def read_credentials(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"credentials file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("credentials file must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("credentials file must contain a JSON object")
    return parsed


def resolve_credential(
    credentials: dict[str, Any],
    aliases: Any,
    field: str,
) -> tuple[str, str]:
    if not isinstance(aliases, list) or not aliases or not all(isinstance(item, str) for item in aliases):
        raise ValueError(f"provider profile {field} credential aliases are invalid")
    for key in aliases:
        value = credentials.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), key
    raise ValueError(f"credentials missing configured model field: {field}")


def validate_endpoint(base_url: str, allowed_hosts: Any) -> tuple[str, str]:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("provider base_url must use HTTPS with a valid host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("provider base_url must not contain credentials, query or fragment")
    if not isinstance(allowed_hosts, list) or not allowed_hosts:
        raise ValueError("provider profile must declare allowed_endpoint_hosts")
    normalized_hosts = {str(item).strip().lower() for item in allowed_hosts if str(item).strip()}
    endpoint_host = parsed.hostname.lower()
    if endpoint_host not in normalized_hosts:
        raise PermissionError(f"provider endpoint host is not allowlisted: {endpoint_host}")
    normalized_base_url = base_url.rstrip("/")
    endpoint_url = (
        normalized_base_url
        if normalized_base_url.endswith("/chat/completions")
        else normalized_base_url + "/chat/completions"
    )
    return endpoint_url, endpoint_host


def bounded_positive_int(value: Any, *, field: str, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0 or value > maximum:
        raise ValueError(f"provider profile {field} must be between 1 and {maximum}")
    return value


def _manager_bounded_positive_int(value: object, *, field: str, maximum: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        normalized = value
    elif isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,8}", value.strip()):
        normalized = int(value.strip())
    else:
        raise ValueError(f"manager model {field} is invalid")
    return bounded_positive_int(normalized, field=field, maximum=maximum)


def build_fixed_smoke_payload(profile: ResolvedProviderProfile) -> dict[str, Any]:
    return {
        "model": profile.model,
        "messages": [
            {
                "role": "system",
                "content": "Return exactly the ASCII text SMOKE_OK. Do not add reasoning, explanation, punctuation, Markdown, or any other text.",
            },
            {"role": "user", "content": FIXED_SMOKE_RESPONSE},
        ],
        "temperature": 0,
        "max_tokens": profile.max_output_tokens,
        "stream": False,
    }


def extract_response_content(response: dict[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderSmokeTransportError(
            "response_shape_invalid",
            "provider response is missing choices[0].message.content",
        ) from exc
    if not isinstance(content, str):
        raise ProviderSmokeTransportError(
            "response_content_invalid",
            "provider response content must be text",
        )
    return content


def extract_usage(response: dict[str, Any]) -> dict[str, int]:
    raw = response.get("usage")
    if not isinstance(raw, dict):
        return {}
    mapping = {
        "input_tokens": "prompt_tokens",
        "output_tokens": "completion_tokens",
        "total_tokens": "total_tokens",
    }
    usage: dict[str, int] = {}
    for target, source in mapping.items():
        value = raw.get(source)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            usage[target] = value
    return usage


def redact_error_detail(detail: str, secrets: tuple[str, ...]) -> str:
    redacted = re.sub(
        r"(?i)(authorization\s*:\s*)?bearer\s+[^\s,;]+",
        "[REDACTED]",
        str(detail),
    )
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted[:500]


def _safe_error_detail(detail: str, api_key: str, *, strict: bool) -> str:
    """Do not retain upstream diagnostic bodies for Manager execution evidence."""

    if strict:
        return "manager model smoke failed; upstream detail withheld"
    return redact_error_detail(detail, (api_key,))


def write_model_provider_smoke_outputs(
    output_dir: Path,
    snapshot: dict[str, Any],
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    smoke_path = output_dir / "model_provider_smoke.json"
    events_path = output_dir / "model_provider_smoke_events.json"
    markdown_path = output_dir / "model_provider_smoke.md"
    smoke_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    events_path.write_text(
        json.dumps(snapshot.get("events") or [], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(model_provider_smoke_to_markdown(snapshot), encoding="utf-8")
    return smoke_path, events_path, markdown_path


def model_provider_smoke_to_markdown(snapshot: dict[str, Any]) -> str:
    smoke = snapshot.get("smoke") or {}
    return "\n".join(
        (
            "# Controlled Model Provider Smoke",
            "",
            f"- Smoke ID: {smoke.get('id')}",
            f"- Profile: {smoke.get('profile_key')}",
            f"- Provider: {smoke.get('provider_kind')}",
            f"- Endpoint host: {smoke.get('endpoint_host')}",
            f"- Model: {smoke.get('model')}",
            f"- Status: {smoke.get('status')}",
            f"- Transport status: {smoke.get('transport_status')}",
            f"- Protocol status: {smoke.get('protocol_status')}",
            f"- Marker status: {smoke.get('marker_status')}",
            f"- Connectivity verified: {snapshot.get('connectivity_verified')}",
            f"- Fixed marker verified: {snapshot.get('response_verified')}",
            f"- Retry enabled: {snapshot.get('retry_enabled')}",
            "- Boundary: single-node-smoke-only",
            "- Raw credentials/headers/model response: not persisted",
            "- Business valid: false",
        )
    )


def model_provider_smoke_exit_code(snapshot: dict[str, Any]) -> int:
    smoke = snapshot.get("smoke") or {}
    return 0 if all(
        smoke.get(field) == "passed"
        for field in ("transport_status", "protocol_status", "marker_status")
    ) else 2


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
