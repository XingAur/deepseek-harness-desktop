from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import database
from app.dynamic_plan_registry import MAX_CONTRACT_CONTENT_BYTES, find_credential_field
from app.node_runtime import (
    ControlledNodeRuntime,
    canonical_json,
    sha256_json,
    validate_fixture_boundary,
)
from app.mock_agent_runtime import bounded_token_estimate, validate_mock_fixture_root


MODEL_INVOCATION_SCHEMA_VERSION = "1.0-provider-neutral-offline-model-runtime"
MODEL_REQUEST_SCHEMA_VERSION = "1.0-provider-neutral-model-request"
MODEL_RESPONSE_SCHEMA_VERSION = "1.0-provider-neutral-model-response"
MODEL_CASSETTE_SCHEMA_VERSION = "1.0-offline-model-cassette"
MODEL_OUTPUT_SCHEMA_VERSION = "1.0-structured-contract-output"
OFFLINE_MODEL_MODES = {"mock", "replay"}


class OfflineModelInvocationRuntime:
    """Fixture-only model boundary with no credential or network code path."""

    def __init__(self) -> None:
        database.init_db()
        self.node_runtime = ControlledNodeRuntime()

    def build_request(self, schedule_id: int, node_id: str) -> dict[str, Any]:
        context = self.node_runtime.prepare_context(schedule_id, node_id, requested_tools=())
        envelope = context["envelope"]
        node = envelope["node"]
        request = {
            "schema_version": MODEL_REQUEST_SCHEMA_VERSION,
            "invocation_kind": "structured_contract_output",
            "fixture_only": True,
            "network_allowed": False,
            "credentials_allowed": False,
            "context": {
                "context_id": context["id"],
                "context_hash": context["envelope_hash"],
                "checkpoint_hash": context["checkpoint_hash"],
                "schedule_id": context["schedule_id"],
                "plan_id": context["plan_id"],
                "node_id": context["node_id"],
                "role_id": context["role_id"],
            },
            "messages": [
                {
                    "role": "system",
                    "content": "Return only the declared structured fixture contract. Do not execute tools or business actions.",
                },
                {
                    "role": "user",
                    "content": {
                        "title": node["title"],
                        "node_kind": node["node_kind"],
                        "allowed_paths": node["allowed_paths"],
                        "completion_criteria": node["completion_criteria"],
                        "upstream_artifacts": envelope["upstream_artifacts"],
                    },
                },
            ],
            "output_contract": {
                "name": node["output_contract"],
                "schema_version": MODEL_OUTPUT_SCHEMA_VERSION,
                "producer_role": context["role_id"],
                "required_fields": [
                    "schema_version",
                    "contract_name",
                    "producer_role",
                    "summary",
                    "content",
                    "evidence_refs",
                    "fixture_only",
                    "business_valid",
                ],
            },
            "limits": {
                "input_budget_tokens": int(envelope["role_policy"]["input_budget_tokens"]),
                "output_budget_tokens": int(envelope["role_policy"]["output_budget_tokens"]),
                "timeout_seconds": int(envelope["role_policy"]["timeout_seconds"]),
            },
        }
        credential_path = find_credential_field(request)
        if credential_path:
            raise ValueError(f"模型请求包含禁止的凭证字段：{credential_path}")
        return {
            "context": context,
            "request": request,
            "request_hash": sha256_json(request),
        }

    def invoke(
        self,
        schedule_id: int,
        node_id: str,
        *,
        fixture_root: Path,
        mode: str = "mock",
        cassette_file: Path | None = None,
        record_cassette: bool = False,
    ) -> dict[str, Any]:
        selected_mode = str(mode).strip().lower()
        if selected_mode not in OFFLINE_MODEL_MODES:
            raise ValueError("v0.55 离线模型调用只允许 mock/replay，真实模型与凭证读取未开放")
        root = validate_mock_fixture_root(fixture_root)
        prepared = self.build_request(schedule_id, node_id)
        context = prepared["context"]
        request = prepared["request"]
        request_hash = prepared["request_hash"]
        cassette_relpath = ""
        cassette_digest = ""
        cassette: dict[str, Any] = {}
        boundary_error = ""

        if selected_mode == "replay":
            if cassette_file is None:
                raise ValueError("replay 模式必须提供 cassette_file")
            boundary = validate_fixture_boundary(root, cassette_file)
            boundary_error = str(boundary.get("error_code") or "")
            if not boundary_error:
                cassette_relpath = str(boundary["relative_path"])
                cassette_bytes = boundary["fixture_file"].read_bytes()
                cassette_digest = "sha256:" + hashlib.sha256(cassette_bytes).hexdigest()
                try:
                    parsed = json.loads(cassette_bytes.decode("utf-8"))
                    cassette = parsed if isinstance(parsed, dict) else {}
                except (UnicodeDecodeError, json.JSONDecodeError):
                    boundary_error = "cassette_json_invalid"

        invocation_key = sha256_json(
            {
                "schema_version": MODEL_INVOCATION_SCHEMA_VERSION,
                "context_hash": context["envelope_hash"],
                "request_hash": request_hash,
                "mode": selected_mode,
                "record_cassette": bool(record_cassette),
                "cassette_digest": cassette_digest,
                "boundary_error": boundary_error,
            }
        )
        existing = database.get_model_invocation_by_key(invocation_key)
        if existing:
            return self._snapshot(int(existing["id"]), idempotent=True)

        started_at = now_iso()
        status = "succeeded_fixture"
        error_code = ""
        response: dict[str, Any] = {}
        candidate: dict[str, Any] = {}

        if boundary_error:
            status = "blocked_replay"
            error_code = boundary_error
        elif selected_mode == "mock":
            response = self.build_mock_response(request)
        else:
            error_code = validate_cassette(cassette, request_hash)
            if error_code:
                status = "blocked_replay"
            else:
                response = cassette["response"]

        if response and not error_code:
            error_code = validate_provider_response(response, request)
            if error_code:
                status = "blocked_structured_output"
            else:
                candidate = build_candidate(context, response["output"])

        response_hash = sha256_json(response) if response else ""
        candidate_hash = candidate.get("content_hash", "")
        provider = str(response.get("provider") or "")
        model = str(response.get("model") or "")
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}

        if status == "succeeded_fixture" and record_cassette and selected_mode == "mock":
            cassette_path = record_mock_cassette(root, request_hash, response)
            cassette_relpath = cassette_path.relative_to(root).as_posix()
            cassette_digest = "sha256:" + hashlib.sha256(cassette_path.read_bytes()).hexdigest()

        completed_at = now_iso()
        invocation_id = database.add_model_invocation(
            {
                "context_id": context["id"],
                "schedule_id": context["schedule_id"],
                "plan_id": context["plan_id"],
                "node_id": context["node_id"],
                "role_id": context["role_id"],
                "invocation_key": invocation_key,
                "request_hash": request_hash,
                "mode": selected_mode,
                "provider": provider,
                "model": model,
                "status": status,
                "request_payload": request,
                "response_payload": response,
                "response_hash": response_hash,
                "candidate_payload": candidate,
                "candidate_hash": candidate_hash,
                "usage": usage,
                "error_code": error_code,
                "cassette_relpath": cassette_relpath,
                "cassette_digest": cassette_digest,
                "started_at": started_at,
                "completed_at": completed_at,
            }
        )
        self._record_events(
            invocation_id,
            status=status,
            mode=selected_mode,
            request_hash=request_hash,
            response_hash=response_hash,
            candidate_hash=candidate_hash,
            error_code=error_code,
        )
        return self._snapshot(invocation_id, idempotent=False)

    def get_invocation(self, invocation_id: int) -> dict[str, Any]:
        return self._snapshot(invocation_id, idempotent=False)

    @staticmethod
    def build_mock_response(request: dict[str, Any]) -> dict[str, Any]:
        context = request["context"]
        user_content = request["messages"][1]["content"]
        upstream_ids = [
            str(item["artifact_id"])
            for item in user_content.get("upstream_artifacts") or []
        ]
        output = {
            "schema_version": MODEL_OUTPUT_SCHEMA_VERSION,
            "contract_name": request["output_contract"]["name"],
            "producer_role": request["output_contract"]["producer_role"],
            "summary": f"Synthetic fixture output for {context['node_id']}",
            "content": {
                "node_kind": user_content["node_kind"],
                "allowed_paths": user_content["allowed_paths"],
                "completion_criteria": user_content["completion_criteria"],
                "upstream_artifact_ids": upstream_ids,
            },
            "evidence_refs": upstream_ids,
            "fixture_only": True,
            "business_valid": False,
        }
        input_tokens = bounded_token_estimate(
            request, int(request["limits"]["input_budget_tokens"])
        )
        output_tokens = bounded_token_estimate(
            output, int(request["limits"]["output_budget_tokens"])
        )
        return {
            "schema_version": MODEL_RESPONSE_SCHEMA_VERSION,
            "provider": "harness-offline",
            "model": "deterministic-structured-mock-v1",
            "output": output,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
            "finish_reason": "stop",
        }

    @staticmethod
    def _record_events(
        invocation_id: int,
        *,
        status: str,
        mode: str,
        request_hash: str,
        response_hash: str,
        candidate_hash: str,
        error_code: str,
    ) -> None:
        if status == "succeeded_fixture":
            events = [
                ("prepared", "pass", {"mode": mode, "request_hash": request_hash}),
                ("adapter_completed", "pass", {"response_hash": response_hash}),
                ("validated", "pass", {"candidate_hash": candidate_hash}),
                ("persisted", "pass", {"fixture_only": True}),
            ]
        else:
            events = [
                ("prepared", "pass", {"mode": mode, "request_hash": request_hash}),
                ("blocked", "fail", {"error_code": error_code}),
            ]
        for sequence, (event_type, event_status, details) in enumerate(events, start=1):
            database.add_model_invocation_event(
                {
                    "invocation_id": invocation_id,
                    "sequence": sequence,
                    "event_type": event_type,
                    "status": event_status,
                    "details": details,
                }
            )

    @staticmethod
    def _snapshot(invocation_id: int, *, idempotent: bool) -> dict[str, Any]:
        invocation = database.get_model_invocation(invocation_id)
        if invocation is None:
            raise ValueError(f"model fixture invocation 不存在：{invocation_id}")
        request_valid = invocation["request_hash"] == sha256_json(invocation["request_payload"])
        response_valid = bool(invocation["response_payload"]) and (
            invocation["response_hash"] == sha256_json(invocation["response_payload"])
        )
        candidate = invocation["candidate_payload"]
        candidate_valid = bool(candidate) and (
            invocation["candidate_hash"] == candidate.get("content_hash")
        )
        hashes_valid = request_valid and response_valid and candidate_valid
        return {
            "schema_version": MODEL_INVOCATION_SCHEMA_VERSION,
            "invocation": invocation,
            "request": invocation["request_payload"],
            "response": invocation["response_payload"],
            "structured_output": (invocation["response_payload"] or {}).get("output") or {},
            "candidate": candidate,
            "events": database.list_model_invocation_events(invocation_id),
            "cassette": {
                "recorded": invocation["mode"] == "mock" and bool(invocation["cassette_relpath"]),
                "relative_path": invocation["cassette_relpath"],
                "digest": invocation["cassette_digest"],
            },
            "hashes_valid": hashes_valid,
            "idempotent": idempotent,
            "fixture_only": True,
            "business_valid": False,
            "promotion_enabled": False,
            "scheduler_advance_enabled": False,
            "external_actions_enabled": False,
            "boundaries": [
                "只允许 mock/replay 离线适配器，不读取凭证或调用网络。",
                "结构化输出只形成 fixture 候选，不晋升 current contract。",
                "不执行 HIS 源码、worktree、PG、Git 或外部系统动作。",
            ],
        }


def validate_cassette(cassette: dict[str, Any], request_hash: str) -> str:
    if cassette.get("schema_version") != MODEL_CASSETTE_SCHEMA_VERSION:
        return "cassette_schema_invalid"
    if cassette.get("fixture_only") is not True:
        return "cassette_not_fixture_only"
    if cassette.get("request_hash") != request_hash:
        return "cassette_request_hash_mismatch"
    if not isinstance(cassette.get("response"), dict):
        return "cassette_response_invalid"
    credential_path = find_credential_field(
        {key: value for key, value in cassette.items() if key != "response"}
    )
    if credential_path:
        return "credential_field_forbidden"
    return ""


def validate_provider_response(response: dict[str, Any], request: dict[str, Any]) -> str:
    if not isinstance(response, dict) or response.get("schema_version") != MODEL_RESPONSE_SCHEMA_VERSION:
        return "provider_response_schema_invalid"
    if not isinstance(response.get("provider"), str) or not response["provider"].strip():
        return "provider_response_provider_invalid"
    if not isinstance(response.get("model"), str) or not response["model"].strip():
        return "provider_response_model_invalid"
    if response.get("finish_reason") != "stop":
        return "provider_response_finish_reason_invalid"
    credential_path = find_credential_field(response)
    if credential_path:
        return "credential_field_forbidden"
    if len(canonical_json(response).encode("utf-8")) > MAX_CONTRACT_CONTENT_BYTES:
        return "provider_response_too_large"
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return "provider_response_usage_invalid"
    for key in ("input_tokens", "output_tokens"):
        value = usage.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return "provider_response_usage_invalid"
    if usage["input_tokens"] > int(request["limits"]["input_budget_tokens"]):
        return "provider_response_input_budget_exceeded"
    if usage["output_tokens"] > int(request["limits"]["output_budget_tokens"]):
        return "provider_response_output_budget_exceeded"
    return validate_structured_output(response.get("output"), request)


def validate_structured_output(output: Any, request: dict[str, Any]) -> str:
    if not isinstance(output, dict):
        return "structured_output_invalid"
    if output.get("schema_version") != MODEL_OUTPUT_SCHEMA_VERSION:
        return "structured_output_schema_invalid"
    contract = request["output_contract"]
    if output.get("contract_name") != contract["name"]:
        return "structured_output_contract_name_invalid"
    if output.get("producer_role") != contract["producer_role"]:
        return "structured_output_producer_invalid"
    if not isinstance(output.get("summary"), str) or not output["summary"].strip():
        return "structured_output_summary_invalid"
    if not isinstance(output.get("content"), dict) or not output["content"]:
        return "structured_output_content_invalid"
    expected_refs = sorted(
        str(item["artifact_id"])
        for item in request["messages"][1]["content"].get("upstream_artifacts") or []
    )
    evidence_refs = output.get("evidence_refs")
    if not isinstance(evidence_refs, list) or sorted(str(item) for item in evidence_refs) != expected_refs:
        return "structured_output_evidence_refs_invalid"
    if output.get("fixture_only") is not True or output.get("business_valid") is not False:
        return "structured_output_boundary_invalid"
    credential_path = find_credential_field(output)
    if credential_path:
        return "credential_field_forbidden"
    return ""


def build_candidate(context: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    content_hash = sha256_json(output)
    upstream = context["envelope"]["upstream_artifacts"]
    return {
        "artifact_id": f"model-fixture-candidate:{context['id']}:{content_hash[-12:]}",
        "schema_name": context["envelope"]["node"]["output_contract"],
        "schema_version": "1.0",
        "producer": context["role_id"],
        "input_artifact_ids": [str(item["artifact_id"]) for item in upstream],
        "content_hash": content_hash,
        "status": "fixture_model_candidate",
        "payload": output,
    }


def record_mock_cassette(root: Path, request_hash: str, response: dict[str, Any]) -> Path:
    cassette_dir = root / "model-cassettes"
    cassette_dir.mkdir(parents=True, exist_ok=True)
    cassette_path = cassette_dir / f"{request_hash.removeprefix('sha256:')}.json"
    payload = {
        "schema_version": MODEL_CASSETTE_SCHEMA_VERSION,
        "fixture_only": True,
        "request_hash": request_hash,
        "response": response,
    }
    cassette_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return cassette_path


def write_model_invocation_outputs(
    output_dir: Path,
    snapshot: dict[str, Any],
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    invocation_path = output_dir / "model_fixture_invocation.json"
    events_path = output_dir / "model_fixture_events.json"
    markdown_path = output_dir / "model_fixture_invocation.md"
    invocation_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    events_path.write_text(
        json.dumps(snapshot.get("events") or [], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(model_invocation_to_markdown(snapshot), encoding="utf-8")
    return invocation_path, events_path, markdown_path


def model_invocation_to_markdown(snapshot: dict[str, Any]) -> str:
    invocation = snapshot.get("invocation") or {}
    return "\n".join(
        (
            "# Offline Model Fixture Invocation",
            "",
            f"- Invocation ID：{invocation.get('id')}",
            f"- Node：{invocation.get('node_id')}",
            f"- Mode：{invocation.get('mode')}",
            f"- Status：{invocation.get('status')}",
            f"- Hashes valid：{snapshot.get('hashes_valid')}",
            "- fixture-only：true",
            "- business_valid：false",
            "- promotion_enabled：false",
            "- credentials/network：disabled",
            "",
            "本运行只验证离线模型契约、结构化输出和 cassette replay，不代表真实智能体或业务完成。",
        )
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
