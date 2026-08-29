from __future__ import annotations

import hmac
import base64
import html
import json
import os
import re
import secrets
import sqlite3
from collections.abc import Mapping
from http.cookies import SimpleCookie
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from app import database
from app.agent_backend_factory import build_agent_backend_status
from app.business_acceptance_repository import BusinessAcceptanceRepository
from app.core_status import build_core_status_snapshot
from app.code_evidence_artifacts import EvidenceArtifactRecord, EvidenceArtifactStore
from app.code_evidence_repository import CodeEvidenceRepository
from app.code_evidence_service import CodeEvidenceService, plan_code_evidence
from app.repository_scope import RepositoryScope
from app.harness import RequirementWorkflowRunner
from app.run_scheduler import WEB_RUN_SCHEDULER
from app.runtime_preflight import format_preflight_report, run_runtime_preflight
from app.knowledge_consultation import consult_knowledge
from app.learning_candidate_repository import LearningCandidateRepository
from app.manager_credential_crypto import CredentialEncryptionUnavailable
from app.manager_model_smoke_preflight import build_model_smoke_preflight
from app.manager_provider_repository import (
    DEFAULT_LOCAL_SCOPE,
    ManagerProviderRepository,
)
from app.provider_connection_tests import (
    load_provider_connection_test_audit,
    run_provider_connection_test,
)
from app.provider_action_authorization import (
    ProviderActionAuthorizer,
    redact_safe_result_summary,
)
from app.provider_capability_status import build_provider_capability_status
from app.provider_readonly_smoke import (
    build_provider_readonly_smoke_plan,
    build_provider_readonly_smoke_audit_failure,
    load_provider_readonly_smoke_audit,
    record_provider_readonly_smoke_failure,
    run_provider_readonly_smoke,
)
from app.provider_profiles import (
    build_provider_connection_test_plan,
    build_provider_profile_status,
    load_provider_profiles,
)
from app.provider_field_schema import (
    PROVIDER_CONNECTION_FIELDS,
    PROVIDER_CREDENTIAL_FIELDS,
    provider_field_specs,
    provider_profile_from_typed_form,
)
from app.provider_execution import ACTION_DESCRIPTORS
from app.sensitive_text import contains_sensitive_text, validate_public_identifier
from app.task_intent_router import IntentContext
from app.task_intent_repository import TaskIntentRepository
from app.task_intent_service import TaskIntentRoutingResult, TaskIntentService


HOST = "127.0.0.1"
DEFAULT_PORT = 8765
_MANAGER_FORM_CSRF_TOKEN = secrets.token_urlsafe(32)
_MANAGER_PROTECTED_FORM_PATHS = frozenset(
    {
        "/providers",
        "/providers/credentials",
        "/runs",
        "/knowledge/consult",
        "/routing/classify",
        "/actions/plans",
        "/actions/confirm",
        "/learning-candidates/review",
        "/business-acceptance/evidence",
        "/business-acceptance/decisions",
        "/api/provider-profiles/test-connection",
        "/api/provider-profiles/readonly-smoke",
    }
)
DEFAULT_KNOWLEDGE_HOME = "/Users/lym/WorkCode/ai/his-knowledge"
_MANAGER_CONVERSATION_COOKIE = "harness_manager_conversation"
CANONICAL_PROVIDER_MANIFESTS = {
    "yunxiao": "/Users/lym/plugins/yunxiao/capabilities.json",
    "his-engineering": "/Users/lym/plugins/his-engineering/capabilities.json",
    "his-knowledge": "/Users/lym/plugins/his-knowledge/capabilities.json",
}
# The readonly smoke bridge deliberately keeps its separate, Git-only manifest input.
CANONICAL_PROVIDER_CAPABILITIES = CANONICAL_PROVIDER_MANIFESTS["his-engineering"]


class ManagerDownstreamFailure(RuntimeError):
    """A downstream failed after its safe routing receipt was persisted."""

    def __init__(self, routing_result: TaskIntentRoutingResult) -> None:
        self.routing_result = routing_result
        super().__init__("manager_downstream_failed")


class HarnessRequestHandler(BaseHTTPRequestHandler):
    server_version = "HISHarnessLite/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self.send_html(render_home())
            return
        if path == "/experts":
            self.send_html(render_experts())
            return
        if path == "/runs":
            self.send_html(render_runs())
            return
        match = re.fullmatch(r"/run-jobs/([a-f0-9]{32})", path)
        if match:
            self.send_html(render_run_job(match.group(1)))
            return
        match = re.fullmatch(r"/api/run-jobs/([a-f0-9]{32})", path)
        if match:
            job = WEB_RUN_SCHEDULER.get(match.group(1))
            if job is None:
                self.send_json({"status": "not_found", "job_id": match.group(1)}, status=HTTPStatus.NOT_FOUND)
            else:
                self.send_json(job)
            return
        if path == "/providers":
            self.send_html(render_provider_profiles_page())
            return
        if path == "/actions":
            self.send_html(render_actions_page())
            return
        if path == "/knowledge":
            self.send_html(render_knowledge_consultation_page())
            return
        if path == "/routing":
            conversation_key = _manager_conversation_key(self.headers, {})
            self.send_html(
                render_routing_page(),
                response_headers={
                    "Set-Cookie": _manager_conversation_cookie(conversation_key)
                },
            )
            return
        if path == "/code-evidence":
            self.send_html(render_code_evidence_page())
            return
        if path == "/learning-candidates":
            self.send_html(render_learning_candidates_page())
            return
        if path == "/business-acceptance":
            self.send_html(render_business_acceptance_page())
            return
        if path == "/api/core-status":
            self.send_json(build_core_status_snapshot())
            return
        if path == "/api/agent-backends":
            try:
                self.send_json(build_agent_backend_status())
            except ValueError as error:
                self.send_json(
                    {
                        "schema_version": "his-agent-backend-status.v1",
                        "status": "blocked",
                        "error_code": str(error),
                    },
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
            return
        if path == "/api/runtime-preflight":
            report = run_runtime_preflight(
                database_path=database.DB_PATH,
                output_dir=Path(os.environ.get("HARNESS_OUTPUT_DIR", "runs")),
                worktree_root=Path(os.environ.get("HARNESS_WORKTREE_ROOT", "/tmp/his_harness_worktrees")),
                allow_mock=True,
            )
            report["text"] = format_preflight_report(report)
            self.send_json(report)
            return
        if path == "/api/provider-profiles":
            self.send_json(build_provider_profile_status(load_provider_profiles()))
            return
        if path == "/api/provider-profiles/test-plan":
            self.send_json(build_provider_connection_test_plan(load_provider_profiles()))
            return
        if path == "/api/provider-profiles/capability-status":
            self.send_json(
                build_provider_capability_status(
                    load_provider_profiles(), manifest_paths=CANONICAL_PROVIDER_MANIFESTS
                )
            )
            return
        if path == "/api/provider-profiles/readonly-smoke-plan":
            self.send_json(
                build_provider_readonly_smoke_plan(
                    load_provider_profiles(), manifest_path=CANONICAL_PROVIDER_CAPABILITIES
                )
            )
            return
        if path == "/api/manager/providers":
            self.send_json(build_manager_provider_status())
            return
        if path == "/api/manager/actions":
            self.send_json(build_manager_actions_status())
            return
        if path == "/api/manager/knowledge":
            self.send_json(build_manager_knowledge_status())
            return
        if path == "/api/manager/routing":
            self.send_json(build_manager_routing_status())
            return
        if path == "/api/manager/code-evidence":
            self.send_json(build_manager_code_evidence_status())
            return
        if path == "/api/manager/code-evidence/artifact":
            values = parse_qs(parsed.query)
            try:
                bundle_id = int((values.get("bundle_id") or [""])[0])
                kind = (values.get("kind") or [""])[0]
                offset = int((values.get("offset") or ["0"])[0])
                limit = int((values.get("limit") or ["65536"])[0])
                self.send_json(build_manager_code_evidence_artifact(
                    bundle_id=bundle_id, kind=kind, offset=offset, limit=limit
                ))
            except (TypeError, ValueError):
                self.send_json(
                    {"schema_version": "his-manager-code-evidence-artifact.v1", "status": "blocked", "error_code": "code_evidence_artifact_invalid"},
                    status=HTTPStatus.BAD_REQUEST,
                )
            return
        if path == "/api/manager/learning-candidates":
            self.send_json(build_manager_learning_candidates_status())
            return
        if path == "/api/manager/business-acceptance":
            self.send_json(build_manager_business_acceptance_status())
            return
        if path == "/api/manager/model-smoke-preflight":
            profile_key = (parse_qs(parsed.query).get("profile_key") or [""])[0].strip()
            profile = _find_manager_profile_status(provider="model", profile_key=profile_key)
            self.send_json(
                build_model_smoke_preflight(profile, requested_profile_key=profile_key)
            )
            return
        match = re.fullmatch(r"/runs/(\d+)", path)
        if match:
            self.send_html(render_run_detail(int(match.group(1))))
            return
        match = re.fullmatch(r"/artifacts/(\d+)", path)
        if match:
            artifact = database.get_artifact(int(match.group(1)))
            if artifact is None:
                self.send_error(HTTPStatus.NOT_FOUND, "artifact not found")
                return
            self.send_text(artifact["content"], content_type="text/plain; charset=utf-8")
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        data = parse_qs(body, keep_blank_values=True)
        if (
            parsed.path in _MANAGER_PROTECTED_FORM_PATHS
            and not _manager_form_request_is_authorized(self.headers, data)
        ):
            self.send_html(
                layout(
                    "请求被拒绝",
                    '<section class="alert">请求校验失败，配置未发生变更。</section>',
                ),
                status=HTTPStatus.FORBIDDEN,
            )
            return
        if parsed.path == "/api/provider-profiles/readonly-smoke":
            provider = (data.get("provider") or [""])[0].strip()
            profile_key = (data.get("profile_key") or [""])[0].strip()
            confirmation_text = (data.get("confirmation_text") or [""])[0].strip()
            try:
                _validate_provider_action_inputs(
                    provider=provider,
                    profile_key=profile_key,
                    requested_by="manager",
                    confirmation_text=confirmation_text,
                )
            except ValueError:
                self.send_json(_provider_action_input_error(), status=HTTPStatus.BAD_REQUEST)
                return
            try:
                result = run_provider_readonly_smoke(
                    load_provider_profiles(),
                    provider=provider,
                    profile_key=profile_key,
                    requested_by="manager",
                    confirmation_text=confirmation_text,
                )
            except Exception:
                try:
                    result = record_provider_readonly_smoke_failure(
                        provider=provider,
                        profile_key=profile_key,
                        requested_by="manager",
                    )
                except Exception:
                    result = build_provider_readonly_smoke_audit_failure(
                        provider=provider,
                        profile_key=profile_key,
                        requested_by="manager",
                    )
                self.send_json(result)
                return
            self.send_json(result)
            return
        if parsed.path == "/api/provider-profiles/test-connection":
            provider = (data.get("provider") or [""])[0].strip()
            profile_key = (data.get("profile_key") or [""])[0].strip()
            requested_by = (data.get("requested_by") or ["manager"])[0].strip() or "manager"
            confirmation_text = (data.get("confirmation_text") or [""])[0].strip()
            try:
                _validate_provider_action_inputs(
                    provider=provider,
                    profile_key=profile_key,
                    requested_by=requested_by,
                    confirmation_text=confirmation_text,
                )
            except ValueError:
                self.send_json(_provider_action_input_error(), status=HTTPStatus.BAD_REQUEST)
                return
            try:
                result = run_provider_connection_test(
                    load_provider_profiles(),
                    provider=provider,
                    profile_key=profile_key,
                    requested_by=requested_by,
                )
            except Exception:
                self.send_json(
                    {
                        "schema_version": "his-provider-connection-test-result.v2",
                        "plan_id": None,
                        "provider": provider,
                        "profile_key": profile_key,
                        "requested_by": requested_by,
                        "action": f"{provider}.connection_test",
                        "risk": "",
                        "status": "failed",
                        "reason": "provider_connection_test_failed",
                        "changed": False,
                        "credentials_read": False,
                        "external_calls": False,
                        "execution_allowed": False,
                        "error_code": "provider_connection_test_failed",
                        "message": "Provider 连接测试未执行。",
                    }
                )
                return
            self.send_json(result)
            return
        if parsed.path == "/knowledge/consult":
            try:
                query = _form_text_value(data, "query")
            except ValueError:
                self.send_json(
                    {
                        "schema_version": "his-knowledge-consultation.v1",
                        "answerable": False,
                        "model_used": False,
                        "model_escalation_required": False,
                        "retrieval_status": "invalid_query",
                        "message": "咨询内容不能为空。",
                        "results": [],
                        "citations": [],
                    },
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            conversation_key = _manager_conversation_key(self.headers, data)
            try:
                evidence_service, configured_aliases, configured_commands = (
                    _manager_code_evidence_configuration()
                )
                selected_aliases = _select_code_evidence_repositories(
                    query, configured_aliases
                )
                routed = dispatch_manager_message(
                    query,
                    IntentContext(
                        conversation_key=conversation_key,
                    ),
                    code_evidence_service=evidence_service,
                    repository_aliases=selected_aliases,
                    verification_commands=configured_commands,
                    enforce_code_evidence=True,
                )
            except ManagerDownstreamFailure as exc:
                safe_failure = _public_downstream_failure(exc.routing_result)
                self.send_json(
                    safe_failure,
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                    response_headers={
                        "Set-Cookie": _manager_conversation_cookie(
                            str(safe_failure["conversation_key"])
                        )
                    },
                )
                return
            except ValueError:
                self.send_json(
                    _routing_input_error(),
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            except Exception:
                self.send_json(
                    {
                        "schema_version": "his-manager-routed-message.v1",
                        "status": "blocked",
                        "error_code": "routing_dispatch_failed",
                        "message": "消息已识别，但当前下游流程未能完成。",
                        "changed": False,
                    },
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            result = (
                {**routed["knowledge"], "routing": _public_routed_summary(routed)}
                if routed["mode"] == "question"
                else routed
            )
            self.send_json(
                result,
                response_headers={
                    "Set-Cookie": _manager_conversation_cookie(conversation_key)
                },
            )
            return
        if parsed.path == "/routing/classify":
            try:
                message = _form_text_value(data, "message", preserve_whitespace=True)
                conversation_key = _manager_conversation_key(self.headers, data)
                work_item_id = _optional_form_text_value(data, "work_item_id")
                current_phase = _optional_form_text_value(data, "current_phase")
                explicit_override = _optional_form_text_value(data, "explicit_override")
                evidence_service, configured_aliases, configured_commands = (
                    _manager_code_evidence_configuration()
                )
                selected_aliases = _select_code_evidence_repositories(
                    message, configured_aliases
                )
                result = dispatch_manager_message(
                    message,
                    IntentContext(
                        conversation_key=conversation_key,
                        work_item_id=work_item_id or None,
                        current_phase=current_phase or None,
                    ),
                    explicit_override=explicit_override or None,
                    code_evidence_service=evidence_service,
                    repository_aliases=selected_aliases,
                    verification_commands=configured_commands,
                    enforce_code_evidence=True,
                )
            except ManagerDownstreamFailure as exc:
                safe_failure = _public_downstream_failure(exc.routing_result)
                self.send_json(
                    safe_failure,
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                    response_headers={
                        "Set-Cookie": _manager_conversation_cookie(
                            str(safe_failure["conversation_key"])
                        )
                    },
                )
                return
            except ValueError:
                self.send_json(
                    _routing_input_error(),
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            except Exception:
                self.send_json(
                    {
                        "schema_version": "his-manager-routed-message.v1",
                        "status": "blocked",
                        "error_code": "routing_dispatch_failed",
                        "message": "消息路由或下游流程未能完成。",
                        "changed": False,
                    },
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            self.send_json(
                result,
                response_headers={
                    "Set-Cookie": _manager_conversation_cookie(conversation_key)
                },
            )
            return
        if parsed.path == "/actions/plans":
            try:
                parameters = json.loads(_form_text_value(data, "parameters_json"))
                if not isinstance(parameters, dict):
                    raise ValueError("parameters must be an object")
                profile_id = int(_form_text_value(data, "profile_id"))
                action = _form_text_value(data, "action")
                _validate_manager_plan_descriptor(
                    profile_id=profile_id,
                    action=action,
                )
                plan = ProviderActionAuthorizer(
                    ManagerProviderRepository(),
                    clock=lambda: datetime.now(timezone.utc),
                ).create_plan(
                    profile_id=profile_id,
                    action=action,
                    target_alias=_form_text_value(data, "target_alias"),
                    parameters=parameters,
                    requested_by=_form_text_value(data, "requested_by"),
                )
            except (KeyError, TypeError, ValueError, PermissionError):
                self.send_json(_manager_input_error("action_plan_input_invalid"), status=HTTPStatus.BAD_REQUEST)
                return
            self.send_json({
                "schema_version": "his-manager-action-plan-result.v1",
                "status": plan.state,
                "plan_id": plan.id,
                "parameter_hash": plan.parameter_hash,
                "exact_parameter_summary": redact_safe_result_summary(parameters),
                "risk": _action_risk(plan.action),
                "authorization_rendered": False,
            })
            return
        if parsed.path == "/actions/confirm":
            try:
                authorization = ProviderActionAuthorizer(
                    ManagerProviderRepository(),
                    clock=lambda: datetime.now(timezone.utc),
                ).confirm(
                    int(_form_text_value(data, "plan_id")),
                    actor=_form_text_value(data, "reviewer_alias"),
                )
            except (KeyError, TypeError, ValueError, PermissionError):
                self.send_json(_manager_input_error("action_plan_confirmation_invalid"), status=HTTPStatus.BAD_REQUEST)
                return
            self.send_json({
                "schema_version": "his-manager-action-confirmation-result.v1",
                "status": "confirmed",
                "plan_id": authorization.plan_id,
                "authorization_rendered": False,
                "execution_started": False,
            })
            return
        if parsed.path == "/learning-candidates/review":
            try:
                result = LearningCandidateRepository().review_candidate(
                    candidate_key=_form_text_value(data, "candidate_key"),
                    decision=_form_text_value(data, "decision"),
                    reviewer_alias=_form_text_value(data, "reviewer_alias"),
                )
            except (KeyError, TypeError, ValueError, PermissionError):
                self.send_json(_manager_input_error("candidate_review_invalid"), status=HTTPStatus.BAD_REQUEST)
                return
            self.send_json({"schema_version": "his-manager-candidate-review-result.v1", **result})
            return
        if parsed.path == "/business-acceptance/evidence":
            try:
                result = BusinessAcceptanceRepository().create_evidence(
                    {
                        "evidence_key": _form_text_value(data, "evidence_key"),
                        "environment_alias": _form_text_value(data, "environment_alias"),
                        "operator_alias": _form_text_value(data, "operator_alias"),
                        "test_data_alias": _form_text_value(data, "test_data_alias"),
                        "technical_result": _form_text_value(data, "technical_result"),
                        "runtime_verified": _form_text_value(data, "runtime_verified") == "true",
                        "scenarios": [{
                            "name": _form_text_value(data, "scenario_name"),
                            "status": _form_text_value(data, "scenario_status"),
                            "expected": _form_text_value(data, "scenario_expected"),
                            "actual": _form_text_value(data, "scenario_actual"),
                            "evidence": _form_text_value(data, "scenario_evidence"),
                        }],
                    }
                )
            except (KeyError, TypeError, ValueError):
                self.send_json(_manager_input_error("business_evidence_input_invalid"), status=HTTPStatus.BAD_REQUEST)
                return
            self.send_json({"schema_version": "his-manager-business-evidence-result.v1", **result})
            return
        if parsed.path == "/business-acceptance/decisions":
            try:
                result = BusinessAcceptanceRepository().append_reviewer_decision(
                    evidence_id=int(_form_text_value(data, "evidence_id")),
                    reviewer_alias=_form_text_value(data, "reviewer_alias"),
                    decision=_form_text_value(data, "decision"),
                    reason=_form_text_value(data, "reason"),
                )
            except (KeyError, TypeError, ValueError):
                self.send_json(_manager_input_error("business_decision_input_invalid"), status=HTTPStatus.BAD_REQUEST)
                return
            self.send_json({"schema_version": "his-manager-business-decision-result.v1", **result})
            return
        if parsed.path == "/providers":
            try:
                _save_manager_provider_profile(_without_manager_form_security_fields(data))
            except CredentialEncryptionUnavailable:
                self.send_html(
                    render_provider_profiles_page(
                        error="Profile 已保存，但凭证加密服务不可用；本次凭证未写入。"
                    ),
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            except (KeyError, ValueError):
                self.send_html(
                    render_provider_profiles_page(
                        error="配置字段不符合 Provider 白名单，请检查必填项和字段类型。"
                    ),
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/providers?saved=1")
            self.end_headers()
            return
        if parsed.path == "/providers/credentials":
            try:
                _save_manager_provider_credential(_without_manager_form_security_fields(data))
            except CredentialEncryptionUnavailable:
                self.send_html(
                    render_provider_profiles_page(
                        error="凭证加密服务不可用；本次凭证未写入。"
                    ),
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            except (KeyError, ValueError):
                self.send_html(
                    render_provider_profiles_page(
                        error="凭证字段不符合当前 Provider 白名单；本次凭证未写入。"
                    ),
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/providers?credential_saved=1")
            self.end_headers()
            return
        if parsed.path != "/runs":
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        title = data.get("title", ["手工需求"])[0].strip() or "手工需求"
        demand_text = data.get("demand_text", [""])[0].strip()
        project_path = data.get("project_path", [""])[0].strip()
        if not demand_text:
            self.send_html(render_home(error="需求内容不能为空"))
            return
        job_id = WEB_RUN_SCHEDULER.submit(title=title, demand_text=demand_text, project_path=project_path)
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", f"/run-jobs/{job_id}")
        self.end_headers()

    def send_html(
        self,
        body: str,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        response_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        for name, value in (response_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def send_text(self, body: str, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def send_json(
        self,
        payload: dict,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        response_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        for name, value in (response_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        print(f"[http] {self.address_string()} - {format % args}")


def render_home(error: str = "") -> str:
    recent_runs = database.list_runs(limit=8)
    readiness_card = render_readiness_card()
    operating_console = render_operating_console()
    run_rows = "".join(
        f"""
        <tr>
          <td>{escape(run['started_at'])}</td>
          <td><a href="/runs/{run['id']}">{escape(run['title'])}</a></td>
          <td>{status_badge(run['status'])}</td>
          <td>{run['current_step']}/{run['total_steps']}</td>
        </tr>
        """
        for run in recent_runs
    )
    body = f"""
    <section class="hero">
      <div>
        <h1>HIS需求研发专家团 Lite</h1>
        <p>第一版 Harness：手工需求输入 -> 专家团 Workflow -> 阶段报告 -> 最终评审。</p>
      </div>
      <div class="hero-actions">
        <a class="button secondary" href="/experts">专家中心</a>
        <a class="button secondary" href="/runs">运行记录</a>
        <a class="button secondary" href="/providers">Provider维护</a>
        <a class="button secondary" href="/actions">动作与审计</a>
        <a class="button secondary" href="/routing">自动路由</a>
        <a class="button secondary" href="/knowledge">知识库</a>
        <a class="button secondary" href="/learning-candidates">知识候选</a>
        <a class="button secondary" href="/business-acceptance">业务验收</a>
      </div>
    </section>
    {error_box(error)}
    <section class="panel">
      <h2>发起需求分析</h2>
      <form method="post" action="/runs">
        <input type="hidden" name="_csrf_token" value="{escape(_MANAGER_FORM_CSRF_TOKEN)}" />
        <label>标题</label>
        <input name="title" placeholder="例如：门诊收费字段展示调整" />
        <label>需求描述</label>
        <textarea name="demand_text" rows="12" placeholder="粘贴云效需求、钉钉消息或手工需求描述..."></textarea>
        <label>项目路径（可选，只读扫描）</label>
        <input name="project_path" placeholder="/path/to/his-project" />
        <div class="form-actions">
          <button type="submit">启动专家团</button>
        </div>
      </form>
    </section>
    {readiness_card}
    {operating_console}
    <section class="panel">
      <h2>最近运行</h2>
      <table>
        <thead><tr><th>时间</th><th>标题</th><th>状态</th><th>步骤</th></tr></thead>
        <tbody>{run_rows or '<tr><td colspan="4">暂无运行记录</td></tr>'}</tbody>
      </table>
    </section>
    """
    return layout("HIS需求研发专家团 Lite", body)


OPERATING_CONSOLE_SECTIONS = (
    {
        "id": "connection-profiles",
        "title": "连接维护",
        "summary": "维护云效、Git/GitLab、数据库、模型和知识库 provider profile；凭证只显示引用，不展示 secret 原文。",
        "readiness": ("external_writes", "real_model_worker", "knowledge_home"),
        "next_action": "下一步接入 provider_profiles schema 与测试连接结果。",
    },
    {
        "id": "capability-state",
        "title": "能力状态",
        "summary": "统一展示 provider capability 是否启用、禁用原因、前置条件和最近验证结果。",
        "readiness": (
            "real_model_worker",
            "learning_loop",
            "business_acceptance",
            "external_writes",
            "knowledge_home",
        ),
        "next_action": "继续复用 /api/core-status 的 readiness 合同。",
    },
    {
        "id": "transaction-plans",
        "title": "执行计划",
        "summary": "云效写、Git push 和 GitLab 写必须先生成 dry-run transaction plan；数据库永久只读，只生成 SQL 草案交给人工执行。",
        "readiness": ("external_writes",),
        "next_action": "下一步补 last_dry_run、plan hash、目标对象和回滚说明。",
    },
    {
        "id": "review-confirmation",
        "title": "审核确认",
        "summary": "真实外部写入必须经过人工 review 和本次 explicit confirmation，不能无人值守自动执行。",
        "readiness": ("external_writes", "learning_loop"),
        "next_action": "下一步接入 confirmation record 和审计链路。",
    },
    {
        "id": "business-evidence",
        "title": "业务证据",
        "summary": "记录 HIS 测试环境、账号别名、测试数据、步骤、预期/实际结果和验收结论。",
        "readiness": ("business_acceptance",),
        "next_action": "下一步补业务验收 evidence schema 和录入接口。",
    },
    {
        "id": "knowledge-candidates",
        "title": "知识候选",
        "summary": "失败样本、规则草稿和知识草稿先进入 candidate 队列，正式知识必须人工 promote。",
        "readiness": ("learning_loop", "knowledge_home"),
        "next_action": "下一步补 candidate 列表、审核动作和 Obsidian 索引状态。",
    },
)


def render_operating_console(
    *, manager_status: Mapping[str, object] | None = None
) -> str:
    snapshot = build_core_status_snapshot(manager_status=manager_status)
    readiness_items = {
        str(item.get("id")): item
        for item in (snapshot.get("readiness") or {}).get("items") or []
    }
    section_cards = "".join(
        _render_operating_console_section(section, readiness_items)
        for section in OPERATING_CONSOLE_SECTIONS
    )
    pipeline = "云效/Git/GitLab 写动作：dry-run -> review -> explicit confirmation -> execute -> audit"
    database_policy = "数据库永久只读：Harness 只生成 SQL 草案，由人工在 Harness 外执行；绝不提供数据库执行按钮、API、任务队列或执行流程。"
    return f"""
    <section class="panel">
      <div class="row-between">
        <div>
          <h2>运营控制台</h2>
          <p>统一承载连接维护、能力状态、执行计划、审核确认、业务证据和知识候选。真实外部写入默认禁用。</p>
          <p class="meta">连接维护入口：<a href="/providers">Provider维护</a></p>
          <p class="meta">{escape(pipeline)}</p>
          <p class="meta">{escape(database_policy)}</p>
        </div>
      </div>
      <div class="ops-grid">{section_cards}</div>
    </section>
    """


def _render_operating_console_section(
    section: dict[str, object],
    readiness_items: dict[str, dict[str, object]],
) -> str:
    readiness_ids = tuple(str(item) for item in section.get("readiness", ()))
    state_rows = "".join(
        f"<li>{escape((item.get('title') or readiness_id))}：{escape(item.get('state') or 'unknown')} / {escape((item.get('verification') or {}).get('status') or '-')}</li>"
        for readiness_id in readiness_ids
        if (item := readiness_items.get(readiness_id))
    )
    return f"""
        <article class="ops-card" id="ops-section-{escape(section['id'])}">
          <h3>{escape(section["title"])}</h3>
          <p>{escape(section["summary"])}</p>
          <ul>{state_rows or '<li>暂无状态</li>'}</ul>
          <p class="meta">{escape(section["next_action"])}</p>
        </article>
    """


def build_manager_provider_status(
    repository: ManagerProviderRepository | None = None,
) -> dict[str, object]:
    provider_repository = repository or ManagerProviderRepository()
    profiles = []
    try:
        records = provider_repository.list_profiles()
        for record in records:
            item = provider_repository.profile_status(record.id)
            item["action_readiness"] = _manager_action_readiness(record.provider)
            profiles.append(item)
    except (TypeError, ValueError):
        return {
            "schema_version": "his-manager-provider-status.v1",
            "status": "blocked",
            "reason": "provider_profile_storage_invalid",
            "changed": False,
            "secret_values_rendered": False,
            "profiles": [],
            "credentials_read": False,
            "external_calls": False,
            "write_performed": False,
        }
    return {
        "schema_version": "his-manager-provider-status.v1",
        "status": "ready",
        "changed": False,
        "secret_values_rendered": False,
        "profiles": profiles,
        "credentials_read": False,
        "external_calls": False,
        "write_performed": False,
    }


def build_manager_actions_status() -> dict[str, object]:
    repository = ManagerProviderRepository()
    return {
        "schema_version": "his-manager-actions-status.v1",
        "plans": repository.list_action_plans(limit=100),
        "audits": repository.list_action_audits(limit=100),
        "authorization_rendered": False,
        "credentials_read": False,
        "generic_command_executor": False,
        "database_write_executor": False,
    }


def build_manager_knowledge_status() -> dict[str, object]:
    consultations = ManagerProviderRepository().list_knowledge_consultations(limit=100)
    return {
        "schema_version": "his-manager-knowledge-status.v1",
        "consultations": consultations,
        "model_used": any(bool(item.get("model_used")) for item in consultations),
        "credentials_read": False,
        "external_calls": False,
    }


def dispatch_manager_message(
    message: str,
    context: IntentContext,
    *,
    explicit_override: str | None = None,
    knowledge_home: str | Path | None = None,
    knowledge_repository: ManagerProviderRepository | None = None,
    knowledge_capability_service: object | None = None,
    knowledge_legacy_retrieval: object | None = None,
    workflow_runner: RequirementWorkflowRunner | None = None,
    code_evidence_service: CodeEvidenceService | None = None,
    repository_aliases: tuple[str, ...] = (),
    verification_commands: Mapping[str, tuple[tuple[str, ...], ...]] | None = None,
    enforce_code_evidence: bool = True,
) -> dict[str, object]:
    """Classify once, then continue through the selected mandatory boundary."""

    routing_result = TaskIntentService().route(
        message,
        context,
        explicit_override=explicit_override,
    )
    decision = routing_result.decision
    payload = _public_routing_receipt(routing_result)
    evidence_plan = plan_code_evidence(
        message, routing_result, repository_aliases=repository_aliases
    )
    payload["code_evidence_plan"] = {
        "route": evidence_plan.route,
        "required_capabilities": list(evidence_plan.required_capabilities),
        "repository_aliases": list(evidence_plan.repository_aliases),
        "blockers": list(evidence_plan.blockers),
        "mutation_allowed": evidence_plan.mutation_allowed,
        "yunxiao_required": evidence_plan.yunxiao_required,
        "provider_status": evidence_plan.provider_status,
    }
    if evidence_plan.required_capabilities and (
        enforce_code_evidence or code_evidence_service is not None or repository_aliases
    ):
        if evidence_plan.blockers or code_evidence_service is None:
            raise ManagerDownstreamFailure(routing_result)
        try:
            if evidence_plan.route in {"code_review", "requirement_workflow"}:
                payload["code_evidence"] = code_evidence_service.review_changes(
                    conversation_key=str(decision.conversation_key),
                    task_key=f"routing-{routing_result.event_id}",
                    repository_aliases=evidence_plan.repository_aliases,
                    commands=verification_commands or {},
                )
                if evidence_plan.route == "code_review":
                    payload["downstream"] = "code_evidence_review"
                    return payload
            else:
                payload["code_evidence"] = code_evidence_service.inspect(
                    message=message,
                    conversation_key=str(decision.conversation_key),
                    task_key=f"routing-{routing_result.event_id}",
                    repository_aliases=evidence_plan.repository_aliases,
                    include_history="git.history" in evidence_plan.required_capabilities,
                )
        except Exception:
            raise ManagerDownstreamFailure(routing_result) from None
    if decision.mode == "question":
        payload["downstream"] = "knowledge"
        try:
            consultation_kwargs: dict[str, object] = {
                "repository": knowledge_repository or ManagerProviderRepository(),
                "routing_result": routing_result,
                "capability_service": knowledge_capability_service,
                "legacy_retrieval": knowledge_legacy_retrieval,
            }
            if knowledge_legacy_retrieval is not None:
                consultation_kwargs["knowledge_home"] = knowledge_home
            payload["knowledge"] = consult_knowledge(
                message,
                **consultation_kwargs,
            )
        except Exception:
            raise ManagerDownstreamFailure(routing_result) from None
        return payload

    try:
        default_local_runner = workflow_runner is None
        runner = workflow_runner or RequirementWorkflowRunner(
            mode="mock",
            allow_mock=True,
        )
        workflow_result = runner.run(
            title="Manager 自动路由需求",
            demand_text=message,
            source_type="manager-routing",
            execution_mode="readonly",
            requirement_governance="observe",
            routing_result=routing_result,
        )
        payload["workflow"] = _public_workflow_summary(
            workflow_result,
            analysis_backend=(
                "local_deterministic" if default_local_runner else "supplied_runner"
            ),
            real_model_used=(
                False
                if default_local_runner
                else not bool(getattr(getattr(runner, "llm_client", None), "is_mock", True))
            ),
        )
    except Exception:
        raise ManagerDownstreamFailure(routing_result) from None
    payload["downstream"] = "requirement_workflow"
    return payload


def _public_routing_receipt(
    routing_result: TaskIntentRoutingResult,
) -> dict[str, object]:
    decision = routing_result.decision
    return {
        "schema_version": "his-manager-routed-message.v1",
        "status": "routed",
        "event_id": routing_result.event_id,
        "conversation_key": decision.conversation_key,
        "mode": decision.mode,
        "reason_codes": list(decision.reason_codes),
        "confidence": decision.confidence,
        "sticky": decision.sticky,
        "linked_work_item": decision.linked_work_item,
        "yunxiao_status": decision.yunxiao_status,
        "current_phase": decision.current_phase,
        "next_route": decision.next_route,
        "mutation_requested": routing_result.mutation_requested,
        "explicit_correction": decision.reason_codes == ("explicit_override",),
        "credentials_read": False,
        "external_calls": False,
        "write_performed": False,
    }


def _public_downstream_failure(
    routing_result: TaskIntentRoutingResult,
) -> dict[str, object]:
    payload = _public_routing_receipt(routing_result)
    payload.update(
        {
            "status": "blocked",
            "error_code": "downstream_failed",
            "message": "消息路由已记录，但当前下游流程未能完成。",
            "changed": True,
            "downstream_completed": False,
        }
    )
    return payload


def _routing_input_error() -> dict[str, object]:
    return {
        "schema_version": "his-manager-routed-message.v1",
        "status": "blocked",
        "error_code": "routing_input_invalid",
        "message": "消息或路由参数无效。",
        "changed": False,
        "downstream_completed": False,
    }


def _public_workflow_summary(
    result: object,
    *,
    analysis_backend: str,
    real_model_used: bool,
) -> dict[str, object]:
    run_id = getattr(result, "run_id", None)
    status = getattr(result, "status", None)
    evaluation_status = getattr(result, "evaluation_status", None)
    events = getattr(result, "orchestration_events", None)
    if (
        not isinstance(run_id, int)
        or isinstance(run_id, bool)
        or run_id < 1
        or not isinstance(status, str)
        or not isinstance(evaluation_status, str)
        or not isinstance(events, tuple)
    ):
        raise ValueError("manager_workflow_result_invalid")
    public_events: list[dict[str, str]] = []
    for event in events:
        if not isinstance(event, Mapping):
            raise ValueError("manager_workflow_result_invalid")
        item = {
            field: event.get(field)
            for field in ("stage", "status", "reason_code")
        }
        if any(not isinstance(value, str) or not value for value in item.values()):
            raise ValueError("manager_workflow_result_invalid")
        public_events.append(item)
    return {
        "run_id": run_id,
        "status": status,
        "evaluation_status": evaluation_status,
        "stage_count": len(public_events),
        "stages": public_events,
        "run_path": f"/runs/{run_id}",
        "analysis_backend": analysis_backend,
        "technical_only": True,
        "real_model_used": real_model_used,
        "business_valid": False,
    }


def _public_routed_summary(payload: Mapping[str, object]) -> dict[str, object]:
    fields = (
        "event_id",
        "conversation_key",
        "mode",
        "reason_codes",
        "confidence",
        "sticky",
        "linked_work_item",
        "yunxiao_status",
        "current_phase",
        "next_route",
        "mutation_requested",
        "explicit_correction",
        "downstream",
    )
    return {field: payload[field] for field in fields}


def build_manager_routing_status(
    repository: TaskIntentRepository | None = None,
) -> dict[str, object]:
    """Return only public routing facts; message summaries and hashes stay private."""

    routing_repository = repository or TaskIntentRepository()
    try:
        events = [
            _public_routing_event(item)
            for item in routing_repository.list_recent_events(limit=100)
        ]
    except (TypeError, ValueError):
        return {
            "schema_version": "his-manager-routing-status.v1",
            "status": "blocked",
            "reason": "routing_storage_invalid",
            "conversations": [],
            "events": [],
            "credentials_read": False,
            "external_calls": False,
            "write_performed": False,
        }
    conversations: list[dict[str, object]] = []
    seen: set[str] = set()
    for event in events:
        conversation_key = str(event["conversation_key"])
        if conversation_key not in seen:
            conversations.append(dict(event))
            seen.add(conversation_key)
    return {
        "schema_version": "his-manager-routing-status.v1",
        "status": "ready",
        "conversations": conversations,
        "events": events,
        "credentials_read": False,
        "external_calls": False,
        "write_performed": False,
    }


def build_manager_code_evidence_status(
    repository: CodeEvidenceRepository | None = None,
    artifact_store: EvidenceArtifactStore | None = None,
) -> dict[str, object]:
    try:
        repo = repository or CodeEvidenceRepository()
        store = artifact_store or _manager_code_evidence_store()
        bundles = [_public_code_evidence_bundle(item, store) for item in repo.list_recent_bundles(limit=100)]
        evidence_sets = repo.list_recent_evidence_sets(limit=100)
    except Exception:
        return {
            "schema_version": "his-manager-code-evidence-status.v1",
            "status": "blocked",
            "reason": "code_evidence_storage_invalid",
            "configured_repositories": [],
            "reviewer_external_model_enabled": False,
            "bundles": [],
            "evidence_sets": [],
            "external_calls": False,
            "write_performed": False,
        }
    configured_service, configured_aliases, _configured_commands = _manager_code_evidence_configuration()
    configured = sorted(configured_aliases)
    return {
        "schema_version": "his-manager-code-evidence-status.v1",
        "status": "ready",
        "configured_repositories": configured,
        "reviewer_external_model_enabled": (
            configured_service is not None
            and configured_service.reviewer_external_model_enabled is True
        ),
        "bundles": bundles,
        "evidence_sets": evidence_sets,
        "external_calls": False,
        "write_performed": False,
    }


def build_manager_code_evidence_artifact(
    *, bundle_id: int, kind: str, offset: int, limit: int,
    repository: CodeEvidenceRepository | None = None,
    artifact_store: EvidenceArtifactStore | None = None,
) -> dict[str, object]:
    allowed = frozenset((
        "diff_patch", "diff_manifest", "source_manifest", "search_manifest",
        "history", "history_manifest", "verification_receipt", "review", "review_seal",
    ))
    if (
        not isinstance(bundle_id, int) or isinstance(bundle_id, bool) or bundle_id <= 0
        or kind not in allowed
        or not isinstance(offset, int) or isinstance(offset, bool) or offset < 0
        or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 65536
    ):
        raise ValueError("code_evidence_artifact_invalid")
    repo = repository or CodeEvidenceRepository()
    store = artifact_store or _manager_code_evidence_store()
    bundle = repo.get_bundle(bundle_id)
    matches = [item for item in bundle["artifacts"] if item["kind"] == kind]
    if len(matches) != 1:
        raise ValueError("code_evidence_artifact_invalid")
    record = _evidence_artifact_record(matches[0])
    content = store.reopen(record)
    chunk = content[offset:offset + limit]
    try:
        text_value: str | None = chunk.decode("utf-8", "strict")
    except UnicodeDecodeError:
        text_value = None
    return {
        "schema_version": "his-manager-code-evidence-artifact.v1",
        "status": "ready",
        "bundle_id": bundle_id,
        "kind": kind,
        "sha256": record.sha256,
        "size_bytes": record.size_bytes,
        "offset": offset,
        "next_offset": offset + len(chunk),
        "complete": offset + len(chunk) >= len(content),
        "content_text": text_value,
        "content_base64": "" if text_value is not None else base64.b64encode(chunk).decode("ascii"),
    }


def _public_code_evidence_bundle(bundle: Mapping[str, object], store: EvidenceArtifactStore) -> dict[str, object]:
    changed_paths: list[str] = []
    verification_status = "not_run"
    for item in bundle["artifacts"]:
        if item["kind"] == "diff_manifest":
            manifest = json.loads(store.reopen(_evidence_artifact_record(item)))
            changed_paths = [str(value["path"]) for value in manifest.get("files", []) if isinstance(value, Mapping) and isinstance(value.get("path"), str)]
        elif item["kind"] == "verification_receipt":
            receipt = json.loads(store.reopen(_evidence_artifact_record(item)))
            verification_status = str(receipt.get("verification_status", "not_run"))
    review = bundle.get("review")
    return {
        "id": bundle["id"], "bundle_key": bundle["bundle_key"],
        "repository_alias": bundle["repository_alias"], "status": bundle["status"],
        "required_capabilities": bundle["required_capabilities"],
        "seal_sha256": bundle["seal_sha256"], "head_sha": bundle["head_sha"],
        "snapshot_sha256": bundle["snapshot_sha256"], "changed_paths": changed_paths,
        "verification_status": verification_status,
        "review_verdict": review.get("verdict") if isinstance(review, Mapping) else "not_run",
        "findings": review.get("findings", []) if isinstance(review, Mapping) else [],
    }


def _evidence_artifact_record(value: Mapping[str, object]) -> EvidenceArtifactRecord:
    return EvidenceArtifactRecord(
        bundle_id=int(value["bundle_id"]), kind=str(value["kind"]), relative_path=str(value["relative_path"]),
        sha256=str(value["sha256"]), size_bytes=int(value["size_bytes"]), device=int(value["device"]),
        inode=int(value["inode"]), mode=int(value["mode"]), link_count=int(value["link_count"]),
    )


def _manager_code_evidence_store() -> EvidenceArtifactStore:
    root = Path(os.environ.get("HARNESS_CODE_EVIDENCE_ROOT", "data/code-evidence"))
    if not root.is_absolute():
        root = (Path(__file__).resolve().parents[1] / root).resolve()
    return EvidenceArtifactStore(root)


def _manager_code_evidence_configuration() -> tuple[CodeEvidenceService | None, tuple[str, ...], dict[str, tuple[tuple[str, ...], ...]]]:
    raw = os.environ.get("HARNESS_CODE_EVIDENCE_PROJECTS_JSON", "")
    if not raw:
        return None, (), {}
    try:
        payload = json.loads(raw)
        if not isinstance(payload, Mapping) or not payload or len(payload) > 16:
            raise ValueError
        scopes: dict[str, RepositoryScope] = {}
        commands: dict[str, tuple[tuple[str, ...], ...]] = {}
        for alias, value in payload.items():
            if not isinstance(alias, str) or not isinstance(value, Mapping) or set(value) != {"path", "allowed_paths", "verification_commands"}:
                raise ValueError
            path = value["path"]
            allowed_paths = value["allowed_paths"]
            raw_commands = value["verification_commands"]
            if not isinstance(path, str) or not isinstance(allowed_paths, list) or not isinstance(raw_commands, list):
                raise ValueError
            scopes[alias] = RepositoryScope(alias, path, allowed_paths=allowed_paths)
            commands[alias] = tuple(tuple(item) for item in raw_commands if isinstance(item, list) and all(isinstance(part, str) for part in item))
            if len(commands[alias]) != len(raw_commands):
                raise ValueError
        reviewer_switch = os.environ.get("HARNESS_CODE_EVIDENCE_REVIEWER_ENABLED", "0")
        if reviewer_switch not in {"0", "1"}:
            raise ValueError
        repository = CodeEvidenceRepository()
        service = CodeEvidenceService(
            repository,
            _manager_code_evidence_store(),
            scopes,
            allow_external_reviewer=reviewer_switch == "1",
        )
        return service, tuple(sorted(scopes)), commands
    except Exception:
        return None, (), {}


def _select_code_evidence_repositories(
    message: object, configured_aliases: tuple[str, ...]
) -> tuple[str, ...]:
    if not isinstance(message, str) or not configured_aliases:
        return ()
    selected = tuple(
        alias
        for alias in configured_aliases
        if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(alias)}(?![A-Za-z0-9_-])", message, re.IGNORECASE)
    )
    return selected or configured_aliases


def _public_routing_event(event: Mapping[str, object]) -> dict[str, object]:
    public_fields = (
        "id",
        "conversation_key",
        "event_type",
        "previous_mode",
        "mode",
        "reason_codes",
        "confidence",
        "sticky",
        "linked_work_item",
        "yunxiao_status",
        "current_phase",
        "next_route",
        "mutation_requested",
        "created_at",
    )
    return {field: event[field] for field in public_fields}


def build_manager_learning_candidates_status() -> dict[str, object]:
    return {
        "schema_version": "his-manager-learning-candidates-status.v1",
        "candidates": LearningCandidateRepository().list_candidates(limit=100),
        "auto_promote": False,
        "credentials_read": False,
        "external_calls": False,
    }


def build_manager_business_acceptance_status() -> dict[str, object]:
    repository = BusinessAcceptanceRepository()
    try:
        evidence = repository.list_evidence(limit=100)
        business_valid = repository.current_business_valid()
    except (TypeError, ValueError):
        return {
            "schema_version": "his-manager-business-acceptance-status.v1",
            "status": "blocked",
            "reason": "business_acceptance_storage_invalid",
            "evidence": [],
            "business_valid": False,
            "credentials_read": False,
            "external_calls": False,
        }
    return {
        "schema_version": "his-manager-business-acceptance-status.v1",
        "status": "ready",
        "evidence": evidence,
        "business_valid": business_valid,
        "credentials_read": False,
        "external_calls": False,
    }


def build_manager_readiness_status(
    *,
    provider_status: Mapping[str, object],
    business_status: Mapping[str, object],
) -> dict[str, object]:
    """Return only bounded Manager facts accepted by the pure Core renderer."""

    profiles = provider_status.get("profiles")
    business_valid = business_status.get("business_valid")
    if not isinstance(profiles, list) or len(profiles) > 500:
        raise ValueError("manager_readiness_input_invalid")
    if not isinstance(business_valid, bool):
        raise ValueError("manager_readiness_input_invalid")
    return {
        "schema_version": "his-manager-readiness-input.v1",
        "provider_profile_count": len(profiles),
        "business_valid": business_valid,
        "credentials_read": False,
        "external_calls": False,
    }


def _manager_input_error(error_code: str) -> dict[str, object]:
    return {
        "schema_version": "his-manager-input-error.v1",
        "status": "blocked",
        "error_code": error_code,
        "message": "Manager 输入未通过安全校验，未执行动作。",
        "credentials_read": False,
        "external_calls": False,
        "write_performed": False,
    }


def _action_risk(action: str) -> str:
    descriptor = ACTION_DESCRIPTORS.get(action)
    return descriptor.risk if descriptor is not None else "blocked_unknown"


def _validate_manager_plan_descriptor(*, profile_id: int, action: str) -> None:
    descriptor = ACTION_DESCRIPTORS.get(action)
    if descriptor is None:
        raise ValueError("provider_action_not_registered")
    profile = ManagerProviderRepository().profile_status(profile_id)
    if profile.get("provider") != descriptor.provider:
        raise ValueError("provider_action_provider_mismatch")


def _find_manager_profile_status(*, provider: str, profile_key: str) -> dict[str, object] | None:
    if not profile_key:
        return None
    for item in build_manager_provider_status()["profiles"]:
        if item.get("provider") == provider and item.get("profile_key") == profile_key:
            return item
    return None


def _manager_action_readiness(provider: str) -> dict[str, object]:
    if provider == "database":
        supported_actions = [
            "connection_preflight",
            "metadata_read",
            "read_only_select",
            "sql_draft",
        ]
        write_policy = "permanently_disabled"
    elif provider == "model":
        supported_actions = ["configuration_preflight", "controlled_single_node_smoke"]
        write_policy = "model_cannot_authorize_external_writes"
    elif provider == "knowledge":
        supported_actions = ["retrieve", "answer", "consultation_log", "candidate_create"]
        write_policy = "candidate_requires_manual_promotion"
    else:
        supported_actions = ["read_plan", "write_plan"]
        write_policy = "explicit_confirmation_required"
    return {
        "status": "configuration_only",
        "supported_actions": supported_actions,
        "executor_status": "blocked_executor_unregistered",
        "write_policy": write_policy,
        "credentials_read": False,
        "external_calls": False,
        "write_performed": False,
    }


def _save_manager_provider_profile(data: dict[str, list[str]]) -> None:
    typed = provider_profile_from_typed_form(data)
    repository = ManagerProviderRepository()
    profile = repository.upsert_profile(
        scope_type=DEFAULT_LOCAL_SCOPE[0],
        scope_key=DEFAULT_LOCAL_SCOPE[1],
        provider=typed.provider,
        profile_key=typed.profile_key,
        display_name=typed.display_name,
        enabled=typed.enabled,
        connection=typed.connection,
    )
    repository.record_action(
        profile_id=profile.id,
        action_type="provider.profile.updated",
        status="saved",
        details={"provider": typed.provider, "profile_key": typed.profile_key},
    )
    for field, plaintext in typed.credential_inputs.items():
        try:
            repository.upsert_credential(
                profile_id=profile.id,
                field=field,
                plaintext=plaintext,
            )
        except CredentialEncryptionUnavailable:
            repository.record_action(
                profile_id=profile.id,
                action_type="provider.credential.updated",
                status="blocked_encryption_unavailable",
                details={"field": field, "changed": False},
            )
            raise
        repository.record_action(
            profile_id=profile.id,
            action_type="provider.credential.updated",
            status="saved",
            details={"field": field, "changed": True},
        )


def _save_manager_provider_credential(data: dict[str, list[str]]) -> None:
    provider = _form_text_value(data, "provider")
    profile_key = _form_text_value(data, "profile_key")
    field = _form_text_value(data, "field")
    plaintext = _form_text_value(data, "credential_value", preserve_whitespace=True)
    repository = ManagerProviderRepository()
    profiles = [
        profile
        for profile in repository.list_profiles()
        if profile.provider == provider and profile.profile_key == profile_key
    ]
    if len(profiles) != 1:
        raise KeyError("manager provider profile not found")
    profile = profiles[0]
    try:
        repository.upsert_credential(
            profile_id=profile.id,
            field=field,
            plaintext=plaintext,
        )
    except CredentialEncryptionUnavailable:
        repository.record_action(
            profile_id=profile.id,
            action_type="provider.credential.updated",
            status="blocked_encryption_unavailable",
            details={"field": field, "changed": False},
        )
        raise
    repository.record_action(
        profile_id=profile.id,
        action_type="provider.credential.updated",
        status="saved",
        details={"field": field, "changed": True},
    )


def _form_text_value(
    data: dict[str, list[str]],
    name: str,
    *,
    preserve_whitespace: bool = False,
) -> str:
    values = data.get(name) or []
    value = values[0] if values else ""
    if not isinstance(value, str):
        raise ValueError("invalid form value")
    result = value if preserve_whitespace else value.strip()
    if not result:
        raise ValueError("missing form value")
    return result


def _optional_form_text_value(
    data: dict[str, list[str]],
    name: str,
) -> str:
    values = data.get(name) or []
    if not values:
        return ""
    value = values[0]
    if not isinstance(value, str):
        raise ValueError("invalid form value")
    return value.strip()


def _manager_conversation_key(
    headers: object,
    data: dict[str, list[str]],
) -> str:
    explicit = _optional_form_text_value(data, "conversation_key")
    if explicit:
        return explicit
    get_header = getattr(headers, "get", None)
    if callable(get_header):
        raw_cookie = str(get_header("Cookie", "") or "")
        try:
            cookies = SimpleCookie(raw_cookie)
            morsel = cookies.get(_MANAGER_CONVERSATION_COOKIE)
            if morsel is not None:
                candidate = morsel.value
                if re.fullmatch(r"manager-[0-9a-f]{12}", candidate):
                    return candidate
        except Exception:
            pass
    return f"manager-{uuid4().hex[:12]}"


def _manager_conversation_cookie(conversation_key: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", conversation_key):
        raise ValueError("routing_input_invalid")
    return (
        f"{_MANAGER_CONVERSATION_COOKIE}={conversation_key}; "
        "Path=/; HttpOnly; SameSite=Strict"
    )


def _without_manager_form_security_fields(
    data: dict[str, list[str]],
) -> dict[str, list[str]]:
    return {key: value for key, value in data.items() if key != "_csrf_token"}


def _provider_action_input_error() -> dict[str, object]:
    return {
        "schema_version": "his-provider-action-error.v1",
        "status": "blocked",
        "changed": False,
        "credentials_read": False,
        "external_calls": False,
        "write_performed": False,
        "error_code": "provider_action_input_invalid",
        "message": "Provider 动作输入不符合安全规则。",
    }


def _validate_provider_action_inputs(
    *,
    provider: str,
    profile_key: str,
    requested_by: str,
    confirmation_text: str,
) -> None:
    validate_public_identifier(
        provider,
        allowed_values=PROVIDER_CONNECTION_FIELDS,
    )
    validate_public_identifier(profile_key)
    validate_public_identifier(requested_by)
    if not isinstance(confirmation_text, str) or contains_sensitive_text(
        confirmation_text
    ):
        raise ValueError("provider_audit_input_invalid")


def _manager_form_request_is_authorized(
    headers: object,
    data: dict[str, list[str]],
) -> bool:
    token_values = data.get("_csrf_token") or []
    submitted_token = token_values[0] if token_values else ""
    if not isinstance(submitted_token, str) or not hmac.compare_digest(
        submitted_token, _MANAGER_FORM_CSRF_TOKEN
    ):
        return False

    get_header = getattr(headers, "get", None)
    if not callable(get_header):
        return False
    host = str(get_header("Host", "") or "").strip()
    origin = str(get_header("Origin", "") or "").strip()
    if not host or not origin:
        return False
    try:
        host_parts = urlparse(f"http://{host}")
        origin_parts = urlparse(origin)
        host_port = host_parts.port or 80
        origin_port = origin_parts.port or 80
    except ValueError:
        return False
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    return (
        host_parts.hostname in local_hosts
        and origin_parts.hostname in local_hosts
        and origin_parts.scheme == "http"
        and host_parts.hostname == origin_parts.hostname
        and host_port == origin_port
        and not origin_parts.username
        and not origin_parts.password
        and origin_parts.path in {"", "/"}
        and not origin_parts.params
        and not origin_parts.query
        and not origin_parts.fragment
    )


def render_provider_profiles_page(error: str = "") -> str:
    manager_status = build_manager_provider_status()
    manager_rows = _render_manager_provider_rows(manager_status["profiles"])
    profiles = load_provider_profiles()
    profile_status = build_provider_profile_status(profiles)
    test_plan = build_provider_connection_test_plan(profiles)
    capability_status = build_provider_capability_status(
        profiles, manifest_paths=CANONICAL_PROVIDER_MANIFESTS
    )
    readonly_smoke_plan = build_provider_readonly_smoke_plan(
        profiles, manifest_path=CANONICAL_PROVIDER_CAPABILITIES
    )
    audit = load_provider_connection_test_audit()
    readonly_smoke_audit = load_provider_readonly_smoke_audit()
    rows = "".join(
        _render_provider_profile_row(profile, test_item, smoke_item)
        for profile, test_item, smoke_item in zip(
            profile_status["profiles"], test_plan["tests"], readonly_smoke_plan["items"]
        )
    )
    plan_json = json.dumps(test_plan, ensure_ascii=False, indent=2, sort_keys=True)
    audit_json = json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True)
    readonly_smoke_plan_json = json.dumps(readonly_smoke_plan, ensure_ascii=False, indent=2, sort_keys=True)
    capability_rows = _render_provider_capability_rows(capability_status["items"])
    readonly_smoke_audit_json = json.dumps(readonly_smoke_audit, ensure_ascii=False, indent=2, sort_keys=True)
    return layout(
        "Provider Profile 维护",
        f"""
        <section class="page-title row-between">
          <div>
            <h1>Provider Profile 维护</h1>
            <p>维护云效、Git/GitLab、数据库、模型和知识库连接引用。当前页面只展示 profile 状态和测试连接计划，真实连接未执行。</p>
            <p class="meta">本地 Manager 数据库：{escape(database.DB_PATH)}</p>
          </div>
          <div class="hero-actions">
            <a class="button secondary" href="/api/provider-profiles">状态 JSON</a>
            <a class="button secondary" href="/api/provider-profiles/test-plan">测试连接入口</a>
            <a class="button secondary" href="/api/provider-profiles/capability-status">Canonical Provider 能力</a>
            <a class="button secondary" href="/api/provider-profiles/readonly-smoke-plan">本地只读 smoke 计划</a>
            <a class="button secondary" href="/api/manager/providers">Manager Profile 状态</a>
          </div>
        </section>
        {error_box(error)}
        <section class="panel">
          <h2>Manager Profile 与凭证状态</h2>
          <p>Profile 与加密凭证保存在 Manager 数据库；这里只显示配置字段和 configured 状态，不读取明文，也不返回密文。</p>
          <table>
            <thead><tr><th>Provider</th><th>Profile</th><th>连接配置</th><th>凭证状态</th><th>动作准备度</th></tr></thead>
            <tbody>{manager_rows}</tbody>
          </table>
        </section>
        <section class="panel">
          <h2>连接 Profile</h2>
          <p>凭证引用只显示 key 名称，不读取、不展示 secret 原文。数据库测试连接必须与正常运行连接使用同一组身份字段。</p>
          <table>
            <thead><tr><th>Provider</th><th>Profile</th><th>凭证引用</th><th>连接状态</th><th>测试连接计划</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </section>
        <section class="panel">
          <h2>新增/更新 Profile</h2>
          <p>按 Provider 白名单填写类型化字段。凭证只在 password 输入框提交一次；页面刷新后只显示 configured 状态。</p>
          {_render_typed_provider_form()}
        </section>
        <section class="panel">
          <h2>Canonical Provider 能力</h2>
          <p>合约 enabled 不代表已获执行授权：独立 OS sandbox executor 未登记，所有 Manager executor 均为 blocked，页面不会调用 Provider。</p>
          <p>写能力仅展示为 disabled 的信息状态；不会提供写入按钮或写入路由。</p>
          <table>
            <thead><tr><th>Provider</th><th>能力</th><th>Canonical Skill</th><th>合约状态</th><th>执行状态/原因</th></tr></thead>
            <tbody>{capability_rows}</tbody>
          </table>
        </section>
        <section class="panel">
          <h2>本地只读 Smoke</h2>
          <p>本动作只记录受治理的本地 smoke 申请，不在 Harness 内执行 Git。真正 Git 状态读取必须由独立 OS-sandboxed Git Provider/Skill 执行；云效、GitLab、数据库、模型和知识库均不通过此动作连接。</p>
          <p>Schema：{escape(readonly_smoke_plan["schema_version"])}。credentials_read=false，external_calls=false，write_performed=false。</p>
          <pre>{escape(readonly_smoke_plan_json)}</pre>
        </section>
        <section class="panel">
          <h2>最近本地只读 Smoke 记录</h2>
          <pre>{escape(readonly_smoke_audit_json)}</pre>
        </section>
        <section class="panel">
          <h2>测试连接入口</h2>
          <p>Schema：{escape(test_plan["schema_version"])}。当前只是计划：credentials_read=false，external_calls=false，execution_allowed=false。</p>
          <pre>{escape(plan_json)}</pre>
        </section>
        <section class="panel">
          <h2>最近测试连接记录</h2>
          <p>执行测试连接记录只写本地审计。当前 Manager 提交不会读取凭证、不会联网、不会连接数据库。</p>
          <pre>{escape(audit_json)}</pre>
        </section>
        """,
    )


def render_knowledge_consultation_page() -> str:
    consultations = ManagerProviderRepository().list_knowledge_consultations(limit=20)
    rows = "".join(
        "<tr>"
        f"<td>{escape(item['created_at'])}</td>"
        f"<td>{escape(item['query_redacted'])}</td>"
        f"<td>{escape(item['retrieval_status'])}</td>"
        f"<td>{escape(', '.join(str(value) for value in item['citations']) or '-')}</td>"
        f"<td>{escape('是' if item['model_used'] else '否')}</td>"
        "</tr>"
        for item in consultations
    )
    return layout(
        "本地知识咨询",
        f"""
        <section class="page-title row-between">
          <div><h1>本地知识咨询</h1>
            <p>优先查询本地已批准、证据完整且未过期的知识。无可靠命中时只提示知识缺口，不会自动调用模型。</p></div>
        </section>
        <section class="panel"><h2>发起咨询</h2>
          <form method="post" action="/knowledge/consult">
            <input type="hidden" name="_csrf_token" value="{escape(_MANAGER_FORM_CSRF_TOKEN)}" />
            <label>问题</label><textarea name="query" rows="5" required></textarea>
            <div class="form-actions"><button type="submit">查询本地知识</button></div>
          </form>
          <p class="meta">接口返回引用和片段；候选、冲突、未知、过期知识不会作为直接答案，也不会自动写入 Obsidian 或晋升知识。</p>
        </section>
        <section class="panel"><h2>最近咨询记录</h2>
          <p>只展示脱敏问题，不展示原问题哈希。</p>
          <table><thead><tr><th>时间</th><th>脱敏问题</th><th>检索状态</th><th>引用</th><th>使用模型</th></tr></thead>
            <tbody>{rows or '<tr><td colspan="5">暂无咨询记录</td></tr>'}</tbody></table>
        </section>
        """,
    )


def render_routing_page() -> str:
    status = build_manager_routing_status()
    rows = "".join(
        "<tr>"
        f"<td>{escape(item['conversation_key'])}</td>"
        f"<td>{escape(item['mode'])}</td>"
        f"<td>{escape(', '.join(str(value) for value in item['reason_codes']))}</td>"
        f"<td>{escape(item['linked_work_item'] or '-')}</td>"
        f"<td>{escape(item['yunxiao_status'])}</td>"
        f"<td>{escape(item['current_phase'])}</td>"
        f"<td>{escape(item['next_route'])}</td>"
        f"<td>{escape('是' if item['mutation_requested'] else '否')}</td>"
        "</tr>"
        for item in status["conversations"]
    )
    return layout(
        "自动意图路由",
        f"""
        <section class="page-title row-between">
          <div><h1>自动意图路由</h1>
            <p>Harness 自动判断后立即进入知识检索或完整需求流程，不要求用户每次先选择模式。</p></div>
          <a class="button secondary" href="/api/manager/routing">状态 JSON</a>
        </section>
        <section class="panel">
          <h2>提交消息</h2>
          <p>普通问题优先查询知识库；需求相关问题进入完整需求流程。服务端使用安全会话标识保持连续提交的需求粘滞，只有用户明确纠正时才切换。</p>
          <form method="post" action="/routing/classify">
            <input type="hidden" name="_csrf_token" value="{escape(_MANAGER_FORM_CSRF_TOKEN)}" />
            <label>消息</label><textarea name="message" rows="5" required></textarea>
            <label>会话别名（可选）</label><input name="conversation_key" placeholder="例如：dfhis-31333-session" />
            <label>工作项（可选）</label><input name="work_item_id" placeholder="例如：DFHIS-31333" />
            <label>当前阶段（可选）</label>
            <select name="current_phase">
              <option value="">自动判断</option>
              <option value="requirement_intake">requirement_intake</option>
            </select>
            <label>显式纠正（可选，仅系统判断错误时使用）</label>
            <select name="explicit_override">
              <option value="">不纠正，由 Harness 自动判断</option>
              <option value="question">纠正为普通咨询</option>
              <option value="task">纠正为需求流程</option>
            </select>
            <div class="form-actions"><button type="submit">自动判断并继续</button></div>
          </form>
        </section>
        <section class="panel"><h2>最近路由</h2><table>
          <thead><tr><th>会话</th><th>模式</th><th>判断原因</th><th>工作项</th><th>云效状态</th><th>当前阶段</th><th>下一路由</th><th>修改请求</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="8">暂无路由记录</td></tr>'}</tbody>
        </table></section>
        """,
    )


def render_code_evidence_page() -> str:
    status = build_manager_code_evidence_status()
    rows = "".join(
        "<tr>"
        f"<td>{item['id']}</td><td>{escape(str(item['repository_alias']))}</td>"
        f"<td>{escape(', '.join(str(value) for value in item['required_capabilities']))}</td>"
        f"<td>{escape(str(item['status']))}</td>"
        f"<td>{escape(str(item['verification_status']))}</td>"
        f"<td>{escape(str(item['review_verdict']))}</td>"
        f"<td>{escape(', '.join(str(value) for value in item['changed_paths']))}</td>"
        "</tr>"
        for item in status["bundles"]
    )
    return layout(
        "代码证据与审核",
        f"""
        <section class="page-title row-between"><div><h1>代码证据与审核</h1>
          <p>完整 diff、源码证据、隔离验证和只读 Reviewer 由 Harness 自动选择；本页只展示不可变证据与阻断原因。</p></div>
          <a class="button secondary" href="/api/manager/code-evidence">状态 JSON</a></section>
        <section class="panel"><h2>已配置仓库</h2><p>{escape(', '.join(status['configured_repositories']) or '尚未配置')}</p></section>
        <section class="panel"><h2>最近证据</h2><table><thead><tr><th>ID</th><th>仓库</th><th>能力</th><th>状态</th><th>验证</th><th>审核</th><th>变更路径</th></tr></thead>
        <tbody>{rows or '<tr><td colspan="7">暂无代码证据</td></tr>'}</tbody></table></section>
        """,
    )


def render_actions_page() -> str:
    status = build_manager_actions_status()
    plans = status["plans"]
    audits = status["audits"]
    plan_rows = "".join(
        "<tr>"
        f"<td>{escape(item['id'])}</td>"
        f"<td>{escape(item['provider'])}/{escape(item['profile_key'])}</td>"
        f"<td>{escape(item['action_type'])}</td>"
        f"<td>{escape(item['target_alias'])}</td>"
        f"<td>{escape(_action_risk(str(item['action_type'])))}</td>"
        f"<td>{escape(item['parameter_hash'])}</td>"
        f"<td><pre>{escape(json.dumps(item['reviewed_parameter_summary'], ensure_ascii=False, sort_keys=True))}</pre></td>"
        f"<td>{escape(item['state'])}</td>"
        "</tr>"
        for item in plans
    )
    audit_rows = "".join(
        "<tr>"
        f"<td>{escape(item['created_at'])}</td>"
        f"<td>{escape(item['action_type'])}</td>"
        f"<td>{escape(item['target_alias'])}</td>"
        f"<td>{escape(item['status'])}</td>"
        f"<td><pre>{escape(json.dumps(item['details'], ensure_ascii=False, sort_keys=True))}</pre></td>"
        "</tr>"
        for item in audits
    )
    profile_options = "".join(
        f'<option value="{record.id}">{escape(record.provider)}/{escape(record.profile_key)}</option>'
        for record in ManagerProviderRepository().list_profiles()
    )
    action_options = "".join(
        f'<option value="{escape(action)}">{escape(action)} / {escape(descriptor.risk)}</option>'
        for action, descriptor in ACTION_DESCRIPTORS.items()
    )
    confirmation_options = "".join(
        f'<option value="{item["id"]}">Plan {item["id"]} / {escape(item["provider"])} / {escape(item["action_type"])} / {escape(item["parameter_hash"])} / {escape(json.dumps(item["reviewed_parameter_summary"], ensure_ascii=False, sort_keys=True))}</option>'
        for item in plans
        if item.get("state") == "planned"
    )
    return layout(
        "Provider 动作计划与审计",
        f"""
        <section class="page-title row-between"><div>
          <h1>Provider 动作计划与审计</h1>
          <p>动作计划绑定 Provider、Profile、目标别名和参数哈希。确认令牌不会在 HTML/JSON 中展示。</p>
        </div><a class="button secondary" href="/api/manager/actions">状态 JSON</a></section>
        <section class="panel"><h2>创建受控计划</h2>
          <p>精确参数摘要和风险在创建结果中展示；执行仍必须走一次性授权与 Provider execution/read-back 边界。</p>
          <form method="post" action="/actions/plans">
            <input type="hidden" name="_csrf_token" value="{escape(_MANAGER_FORM_CSRF_TOKEN)}" />
            <label>Profile</label><select name="profile_id">{profile_options}</select>
            <label>Canonical 动作</label><select name="action">{action_options}</select>
            <label>目标别名</label><input name="target_alias" required />
            <label>精确参数摘要（此通用面板仅允许 canonical 空参数计划）</label>
            <pre>{{}}</pre><input type="hidden" name="parameters_json" value="{{}}" />
            <label>申请人别名</label><input name="requested_by" required />
            <button type="submit">创建计划</button>
          </form>
        </section>
        <section class="panel"><h2>计划与风险</h2><table>
          <thead><tr><th>ID</th><th>Profile</th><th>动作</th><th>目标</th><th>风险</th><th>参数哈希</th><th>已审核参数摘要</th><th>状态</th></tr></thead>
          <tbody>{plan_rows or '<tr><td colspan="8">暂无计划</td></tr>'}</tbody>
        </table>
        <form method="post" action="/actions/confirm">
          <input type="hidden" name="_csrf_token" value="{escape(_MANAGER_FORM_CSRF_TOKEN)}" />
          <label>已显示的待确认计划</label><select name="plan_id" required>{confirmation_options}</select>
          <label>审核人别名</label><input name="reviewer_alias" required />
          <button type="submit">确认一次性计划</button>
        </form></section>
        <section class="panel"><h2>执行与 read-back 审计</h2><table>
          <thead><tr><th>时间</th><th>动作</th><th>目标</th><th>状态</th><th>脱敏结果</th></tr></thead>
          <tbody>{audit_rows or '<tr><td colspan="5">暂无审计</td></tr>'}</tbody>
        </table></section>
        """,
    )


def render_learning_candidates_page() -> str:
    candidates = LearningCandidateRepository().list_candidates(limit=100)
    rows = "".join(
        "<tr>"
        f"<td>{escape(item['candidate_key'])}</td>"
        f"<td>{escape(item['candidate_type'])}</td>"
        f"<td>{escape(item['state'])}</td>"
        f"<td>{escape(item['reviewer_alias'] or '-')}</td>"
        "</tr>"
        for item in candidates
    )
    return layout(
        "知识候选审核",
        f"""
        <section class="page-title row-between"><div><h1>知识候选审核</h1>
          <p>候选只能由审核人批准或拒绝；不会自动晋升、执行规则草稿或调用模型。</p></div>
          <a class="button secondary" href="/api/manager/learning-candidates">状态 JSON</a></section>
        <section class="panel"><table><thead><tr><th>Candidate</th><th>类型</th><th>状态</th><th>审核人</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="4">暂无候选</td></tr>'}</tbody></table></section>
        <section class="panel"><h2>审核候选</h2><form method="post" action="/learning-candidates/review">
          <input type="hidden" name="_csrf_token" value="{escape(_MANAGER_FORM_CSRF_TOKEN)}" />
          <label>Candidate Key</label><input name="candidate_key" required />
          <label>决定</label><select name="decision"><option value="approve">批准</option><option value="reject">拒绝</option></select>
          <label>审核人别名</label><input name="reviewer_alias" required />
          <button type="submit">追加审核决定</button>
        </form></section>
        """,
    )


def render_business_acceptance_page() -> str:
    try:
        records = BusinessAcceptanceRepository().list_evidence(limit=100)
    except (TypeError, ValueError):
        return layout(
            "业务验收证据",
            '<section class="alert">验收证据存储不可安全读取，已阻断展示与审核。</section>',
        )
    rows = "".join(
        "<tr>"
        f"<td>Evidence ID {escape(item['id'])}<br>{escape(item['evidence_key'])} v{escape(item['evidence_version'])}</td>"
        f"<td>{escape(item['environment_alias'])}</td>"
        f"<td>{escape(item['operator_alias'])}</td>"
        f"<td>{escape(item['test_data_alias'])}</td>"
        f"<td>{escape(item['technical_result'])}<br>runtime_verified={escape(str(item['runtime_verified']).lower())}</td>"
        f"<td><pre>{escape(json.dumps(item['scenarios'], ensure_ascii=False, sort_keys=True))}</pre></td>"
        f"<td><pre>{escape(json.dumps(item['reviewer_decisions'], ensure_ascii=False, sort_keys=True))}</pre></td>"
        f"<td>{escape(item['business_valid'])}</td>"
        "</tr>"
        for item in records
    )
    evidence_options = "".join(
        f'<option value="{item["id"]}">{escape(item["evidence_key"])} v{escape(item["evidence_version"])} / Evidence ID {escape(item["id"])}</option>'
        for item in records
    )
    return layout(
        "业务验收证据",
        f"""
        <section class="page-title row-between"><div><h1>业务验收证据</h1>
          <p>证据按版本追加，审核决定不可覆盖。只有完整运行时证据和最新明确接受决定才能使 business_valid=true。</p></div>
          <a class="button secondary" href="/api/manager/business-acceptance">状态 JSON</a></section>
        <section class="panel"><h2>新增证据版本</h2><form method="post" action="/business-acceptance/evidence">
          <input type="hidden" name="_csrf_token" value="{escape(_MANAGER_FORM_CSRF_TOKEN)}" />
          <label>Evidence Key</label><input name="evidence_key" required />
          <label>测试环境别名</label><input name="environment_alias" required />
          <label>实际操作者别名</label><input name="operator_alias" required />
          <label>测试数据别名</label><input name="test_data_alias" required />
          <label>技术结果</label><select name="technical_result"><option value="passed">passed</option><option value="failed">failed</option><option value="not_verified">not_verified</option></select>
          <label><input type="checkbox" name="runtime_verified" value="true" /> 已完成运行时核验</label>
          <label>场景别名</label><input name="scenario_name" required />
          <label>场景状态</label><select name="scenario_status"><option value="passed">passed</option><option value="failed">failed</option><option value="needs_evidence">needs_evidence</option></select>
          <label>预期</label><input name="scenario_expected" required />
          <label>实际</label><input name="scenario_actual" required />
          <label>证据引用（安全摘要或哈希）</label><input name="scenario_evidence" required />
          <button type="submit">追加证据版本</button>
        </form></section>
        <section class="panel"><h2>版本化证据</h2><table><thead><tr><th>ID / 证据版本</th><th>环境</th><th>操作者</th><th>测试数据</th><th>技术/运行时结果</th><th>场景预期/实际/证据</th><th>追加审核历史</th><th>业务有效</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="8">暂无业务证据</td></tr>'}</tbody></table></section>
        <section class="panel"><h2>追加审核决定</h2><form method="post" action="/business-acceptance/decisions">
          <input type="hidden" name="_csrf_token" value="{escape(_MANAGER_FORM_CSRF_TOKEN)}" />
          <label>已显示的证据版本</label><select name="evidence_id" required>{evidence_options}</select>
          <label>审核人别名</label><input name="reviewer_alias" required />
          <label>决定</label><select name="decision"><option value="accept">接受</option><option value="reject">拒绝</option></select>
          <label>原因</label><input name="reason" required />
          <button type="submit">追加审核决定</button>
        </form></section>
        """,
    )
def _render_manager_provider_rows(items: object) -> str:
    if not isinstance(items, list):
        return '<tr><td colspan="5">暂无已配置 Profile</td></tr>'
    rows: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "")
        connection = json.dumps(
            item.get("connection") or {}, ensure_ascii=False, sort_keys=True
        )
        credential_statuses = item.get("credentials") or {}
        credentials = json.dumps(
            credential_statuses, ensure_ascii=False, sort_keys=True
        )
        readiness = item.get("action_readiness") or {}
        readiness_text = (
            f"{readiness.get('status') or 'configuration_only'} / "
            f"{readiness.get('executor_status') or 'blocked_executor_unregistered'}"
        )
        credential_fields = PROVIDER_CREDENTIAL_FIELDS.get(provider, ())
        credential_form = _render_credential_update_form(
            provider=provider,
            profile_key=str(item.get("profile_key") or ""),
            fields=credential_fields,
        )
        rows.append(
            "<tr>"
            f"<td>{escape(_provider_label(provider))}</td>"
            f"<td>{escape(item.get('display_name') or '-')}<br><span class=\"meta\">{escape(item.get('profile_key') or '-')}</span></td>"
            f"<td><pre>{escape(connection)}</pre></td>"
            f"<td><pre>{escape(credentials)}</pre>{credential_form}</td>"
            f"<td>{escape(readiness_text)}</td>"
            "</tr>"
        )
    return "".join(rows) or '<tr><td colspan="5">暂无已配置 Profile</td></tr>'


def _render_credential_update_form(
    *, provider: str, profile_key: str, fields: tuple[str, ...]
) -> str:
    if not fields:
        return '<p class="meta">当前 Provider 无凭证字段</p>'
    options = "".join(
        f'<option value="{escape(field)}">{escape(_field_label(field))}</option>'
        for field in fields
    )
    return f"""
        <details>
          <summary>更新凭证</summary>
          <form method="post" action="/providers/credentials">
            <input type="hidden" name="_csrf_token" value="{escape(_MANAGER_FORM_CSRF_TOKEN)}" />
            <input type="hidden" name="provider" value="{escape(provider)}" />
            <input type="hidden" name="profile_key" value="{escape(profile_key)}" />
            <label>凭证字段</label>
            <select name="field">{options}</select>
            <label>新凭证</label>
            <input type="password" name="credential_value" autocomplete="new-password" />
            <button type="submit">加密保存凭证</button>
          </form>
        </details>
    """


def _render_typed_provider_form() -> str:
    providers = tuple(PROVIDER_CONNECTION_FIELDS)
    options = "".join(
        f'<option value="{escape(provider)}">{escape(_provider_label(provider))}</option>'
        for provider in providers
    )
    groups: list[str] = []
    for index, provider in enumerate(providers):
        controls = []
        for spec in provider_field_specs(provider):
            input_type = "password" if spec.secret else "text"
            autocomplete = ' autocomplete="new-password"' if spec.secret else ""
            controls.append(
                f'<label>{escape(_field_label(spec.name))}</label>'
                f'<input type="{input_type}" name="{escape(spec.name)}"{autocomplete} '
                f'placeholder="{escape(_field_placeholder(spec.name))}" />'
            )
        disabled = "" if index == 0 else " disabled"
        groups.append(
            f'<fieldset class="typed-provider-fields" data-provider="{escape(provider)}"{disabled}>'
            f'<legend>{escape(_provider_label(provider))}字段</legend>'
            + "".join(controls)
            + "</fieldset>"
        )
    return f"""
      <form method="post" action="/providers" id="typed-provider-form">
        <input type="hidden" name="_csrf_token" value="{escape(_MANAGER_FORM_CSRF_TOKEN)}" />
        <label>Provider</label>
        <select name="provider" id="typed-provider-selector">{options}</select>
        <label>Profile Key</label>
        <input name="profile_key" placeholder="company-yunxiao / his-main-db / default-model" />
        <label>显示名称</label>
        <input name="display_name" placeholder="公司云效 / HIS 只读库 / 默认模型" />
        <label><input type="checkbox" name="enabled" checked /> 启用此 Profile</label>
        {''.join(groups)}
        <div class="form-actions"><button type="submit">保存 Manager Profile</button></div>
      </form>
      <script>
      (() => {{
        const selector = document.getElementById("typed-provider-selector");
        const groups = document.querySelectorAll(".typed-provider-fields");
        const sync = () => groups.forEach((group) => {{
          group.disabled = group.dataset.provider !== selector.value;
        }});
        selector.addEventListener("change", sync);
        sync();
      }})();
      </script>
    """


def _field_label(field: str) -> str:
    return {
        "organization_id": "组织 ID",
        "project_id": "项目 ID",
        "project_key": "项目 Key",
        "workitem_scope": "工作项范围",
        "repository_path": "仓库路径",
        "remote": "Remote",
        "branch_policy": "分支策略",
        "allowed_paths": "允许路径",
        "host": "主机",
        "group": "Group",
        "project": "项目",
        "target_branch": "目标分支",
        "driver": "数据库驱动",
        "port": "端口",
        "database": "数据库名",
        "schema": "Schema",
        "username": "只读账号",
        "readonly_policy": "只读策略",
        "provider_kind": "模型协议",
        "base_url": "Base URL",
        "model": "模型名称",
        "allowed_endpoint_host": "允许的 Endpoint Host",
        "timeout_seconds": "超时秒数",
        "max_output_tokens": "最大输出 Tokens",
        "knowledge_home": "知识库目录",
        "obsidian_vault": "Obsidian Vault",
        "index_path": "索引路径",
        "allowed_sources": "允许来源",
        "pat": "云效 PAT",
        "https_token": "Git HTTPS Token",
        "ssh_private_key": "Git SSH Private Key",
        "access_token": "GitLab Access Token",
        "password": "数据库密码",
        "api_key": "模型 API Key",
    }.get(field, field)


def _field_placeholder(field: str) -> str:
    if field in {
        "pat",
        "https_token",
        "ssh_private_key",
        "access_token",
        "password",
        "api_key",
    }:
        return "仅提交保存，不会回显"
    if field == "readonly_policy":
        return "required"
    if field == "branch_policy":
        return "protected-branch-block"
    return ""


def _render_provider_profile_row(
    profile: dict[str, object], test_item: dict[str, object], smoke_item: dict[str, object]
) -> str:
    provider = str(profile.get("provider") or "")
    issues = ", ".join(str(issue) for issue in profile.get("issues") or []) or "无"
    return f"""
        <tr>
          <td>{escape(_provider_label(provider))}</td>
          <td>{escape(profile.get("profile_key") or "-")}</td>
          <td>凭证引用：{escape(profile.get("credential_ref") or "-")}</td>
          <td>真实连接未执行；测试状态：{escape(profile.get("test_connection_status") or "not_run")}；问题：{escape(issues)}</td>
          <td>
            {escape(test_item.get("status") or "planned")} / confirmation_required={escape(test_item.get("confirmation_required"))}
            <form method="post" action="/api/provider-profiles/test-connection">
              <input type="hidden" name="_csrf_token" value="{escape(_MANAGER_FORM_CSRF_TOKEN)}" />
              <input type="hidden" name="provider" value="{escape(provider)}" />
              <input type="hidden" name="profile_key" value="{escape(profile.get("profile_key") or "")}" />
              <input type="hidden" name="requested_by" value="manager" />
              <input type="hidden" name="confirmation_text" value="只允许本地记录，不允许读取凭证或联网" />
              <button type="submit">执行测试连接记录</button>
            </form>
            <p class="meta">本地只读 smoke：{escape(smoke_item.get("status") or "blocked")}</p>
            <form method="post" action="/api/provider-profiles/readonly-smoke">
              <input type="hidden" name="_csrf_token" value="{escape(_MANAGER_FORM_CSRF_TOKEN)}" />
              <input type="hidden" name="provider" value="{escape(provider)}" />
              <input type="hidden" name="profile_key" value="{escape(profile.get("profile_key") or "")}" />
              <label>确认本地只读 smoke</label>
              <input name="confirmation_text" required placeholder="确认仅执行本地、只读、免凭证且离线的 Git smoke 检查" />
              <button type="submit">执行本地只读 smoke</button>
            </form>
          </td>
        </tr>
    """


def _render_provider_capability_rows(items: list[dict[str, object]]) -> str:
    rows: list[str] = []
    for item in items:
        provider = str(item.get("provider") or "")
        label = _provider_label(provider)
        capabilities = item.get("capabilities") or []
        if capabilities:
            for capability in capabilities:
                if not isinstance(capability, dict):
                    continue
                rows.append(
                    "<tr>"
                    f"<td>{escape(label)}</td>"
                    f"<td>{escape(capability.get('name') or '-')}</td>"
                    f"<td>{escape(capability.get('skill') or '-')}</td>"
                    f"<td>{escape(capability.get('contract_status') or '-')}</td>"
                    f"<td>{escape(capability.get('execution_status') or 'blocked')} / "
                    f"{escape(capability.get('execution_reason') or '-')}</td>"
                    "</tr>"
                )
            continue
        rows.append(
            "<tr>"
            f"<td>{escape(label)}</td>"
            "<td>未注册 Provider 合约</td>"
            "<td>-</td>"
            "<td>unregistered</td>"
            f"<td>{escape(item.get('execution_status') or 'blocked')} / "
            f"{escape(item.get('execution_reason') or 'canonical_provider_contract_unregistered')}</td>"
            "</tr>"
        )
    return "".join(rows) or "<tr><td colspan=\"5\">暂无已声明能力</td></tr>"


def _provider_label(provider: str) -> str:
    return {
        "yunxiao": "云效",
        "gitlab": "GitLab",
        "git": "Git",
        "database": "数据库",
        "knowledge": "知识库",
        "model": "模型",
    }.get(provider, provider)


def render_readiness_card(
    *, manager_status: Mapping[str, object] | None = None
) -> str:
    snapshot = build_core_status_snapshot(manager_status=manager_status)
    readiness = snapshot.get("readiness") or {}
    items = readiness.get("items") or []
    levels = readiness.get("verification_levels") or []
    level_rows = "".join(
        f'<tr id="verification-level-{escape(item.get("id") or "unknown")}" data-state="{escape(item.get("state") or "unknown")}"><td>{escape(item.get("label") or item.get("id") or "-")}</td>'
        f"<td>{escape(item.get('state') or 'unknown')}</td></tr>"
        for item in levels
    )
    rows = "".join(
        f"""
        <tr>
          <td>{escape(str(item.get("title") or item.get("id") or "-"))}</td>
          <td>{status_badge(str(item.get("state") or "unknown"))}</td>
          <td>{escape(str((item.get("verification") or {}).get("status") or "-"))}</td>
          <td>
            {escape(_readiness_contract_note(item))}
            {_readiness_detail(item)}
          </td>
        </tr>
        """
        for item in items
    )
    return f"""
    <section class="panel">
      <div class="row-between">
        <div>
          <h2>五缺口状态</h2>
          <p>只读展示真实模型、学习闭环、业务验收、外部写动作和知识库状态。完整 JSON：<a href="/api/core-status">/api/core-status</a></p>
        </div>
      </div>
      <table>
        <thead><tr><th>状态层级</th><th>当前状态</th></tr></thead>
        <tbody>{level_rows or '<tr><td colspan="2">暂无层级状态</td></tr>'}</tbody>
      </table>
      <table>
        <thead><tr><th>能力</th><th>状态</th><th>验证</th><th>合同/说明</th></tr></thead>
        <tbody>{rows or '<tr><td colspan="4">暂无 readiness 数据</td></tr>'}</tbody>
      </table>
    </section>
    """


def _readiness_contract_note(item: dict) -> str:
    smoke = item.get("smoke_readiness") or {}
    if item.get("smoke_contract_schema"):
        single_node = smoke.get("single_node_smoke") or {}
        boundary = single_node.get("boundary") or "-"
        return f"{item.get('smoke_contract_schema')} / {boundary}"
    if item.get("dry_run_plan_schema"):
        return str(item.get("dry_run_plan_schema"))
    capabilities = item.get("capabilities") or []
    if capabilities:
        return ", ".join(str(capability) for capability in capabilities[:3])
    return "-"


def _readiness_detail(item: dict) -> str:
    item_id = str(item.get("id") or "unknown")
    payload = json.dumps(item, ensure_ascii=False, indent=2, sort_keys=True)
    return f"""
            <details id="readiness-detail-{escape(item_id)}">
              <summary>详情</summary>
              <pre>{escape(payload)}</pre>
            </details>
    """


def render_experts() -> str:
    experts = database.list_experts()
    cards = "".join(
        f"""
        <article class="card">
          <div class="expert-mark">{escape(expert['name'][:1])}</div>
          <div>
            <h3>{escape(expert['name'])}</h3>
            <p class="muted">{escape(expert['role'])} · {escape(expert['tags'])}</p>
            <p>{escape(expert['description'])}</p>
            <details>
              <summary>查看提示词</summary>
              <pre>{escape(expert['prompt'])}</pre>
            </details>
          </div>
        </article>
        """
        for expert in experts
    )
    return layout(
        "专家中心",
        f"""
        <section class="page-title">
          <h1>专家中心</h1>
          <p>第一版内置专家配置，后续可扩展成可编辑配置页。</p>
        </section>
        <section class="grid">{cards}</section>
        """,
    )


def render_runs() -> str:
    runs = database.list_runs(limit=50)
    rows = "".join(
        f"""
        <tr>
          <td>{run['id']}</td>
          <td>{escape(run['started_at'])}</td>
          <td><a href="/runs/{run['id']}">{escape(run['title'])}</a></td>
          <td>{status_badge(run['status'])}</td>
          <td>{run['current_step']}/{run['total_steps']}</td>
        </tr>
        """
        for run in runs
    )
    return layout(
        "运行记录",
        f"""
        <section class="page-title"><h1>运行记录</h1><p>每次运行都保留输入、阶段输出和最终产物。</p></section>
        <section class="panel">
          <table>
            <thead><tr><th>ID</th><th>时间</th><th>标题</th><th>状态</th><th>步骤</th></tr></thead>
            <tbody>{rows or '<tr><td colspan="5">暂无运行记录</td></tr>'}</tbody>
          </table>
        </section>
        """,
    )


def render_run_job(job_id: str) -> str:
    job = WEB_RUN_SCHEDULER.get(job_id)
    if job is None:
        return layout("后台任务不存在", "<section class='panel'>后台任务不存在或服务已重启。</section>")
    status = str(job.get("status") or "queued")
    next_link = f"<a class='button secondary' href='/runs/{job['run_id']}'>打开运行详情</a>" if job.get("run_id") else ""
    error = render_error(str(job.get("error") or ""))
    return layout(
        "后台运行任务",
        f"""
        <meta http-equiv="refresh" content="3">
        <section class="page-title"><h1>后台运行任务</h1><p>Job {escape(job_id)}</p></section>
        <section class="panel">
          <p><strong>{escape(job.get('title') or '')}</strong></p>
          <p>状态：{status_badge(status)} · 阶段：{escape(job.get('stage') or '-')}</p>
          <p>开始：{escape(job.get('started_at') or '-')} · 结束：{escape(job.get('finished_at') or '-')}</p>
          {error}
          <p>{escape(job.get('recovery_action') or '')}</p>
          {next_link}
          <a class="button secondary" href="/runs">运行记录</a>
        </section>
        """,
    )
def render_run_detail(run_id: int) -> str:
    run = database.get_run(run_id)
    if run is None:
        return layout("运行不存在", "<section class='panel'>运行不存在</section>")
    steps = database.get_step_runs(run_id)
    artifacts = database.get_artifacts(run_id)
    verification_status = "not_run"
    for artifact in artifacts:
        if artifact.get("kind") != "verification_matrix_json":
            continue
        try:
            payload = json.loads(str(artifact.get("content") or "{}"))
        except json.JSONDecodeError:
            payload = {}
        verification_status = str(payload.get("verification_status") or payload.get("overall_status") or "not_run")
        break
    artifact_links = "".join(
        f'<a class="button secondary" href="/artifacts/{artifact["id"]}">{escape(artifact["title"])}</a>'
        for artifact in artifacts
    )
    step_rows = "".join(
        f"""
        <tr>
          <td>{step['step_order']}</td>
          <td>{escape(step['step_name'])}</td>
          <td>{escape(step['expert_name'])}</td>
          <td>{status_badge(step['status'])}</td>
          <td>{step['duration_ms']} ms</td>
          <td>{step['prompt_tokens']}/{step['completion_tokens']}</td>
        </tr>
        """
        for step in steps
    )
    step_reports = "".join(
        f"""
        <details class="report" open>
          <summary>{step['step_order']}. {escape(step['step_name'])} / {escape(step['expert_name'])}</summary>
          <div class="meta">状态：{escape(step['status'])} · 耗时：{step['duration_ms']} ms · Tokens：{step['prompt_tokens']}/{step['completion_tokens']}</div>
          {render_error(step['error'])}
          <h4>专家输出</h4>
          <pre>{escape(step['output_text'] or '-')}</pre>
          <h4>本步输入</h4>
          <pre>{escape(step['input_text'])}</pre>
        </details>
        """
        for step in steps
    )
    scope_confirmation = render_scope_confirmation_card(artifacts)
    body = f"""
    <section class="page-title row-between">
      <div>
        <h1>{escape(run['title'])}</h1>
        <p>Run #{run['id']} · {escape(run['started_at'])}</p>
      </div>
      <div class="hero-actions">{artifact_links}<a class="button secondary" href="/runs">返回运行记录</a></div>
    </section>
    <section class="stats">
      <div><span>状态</span>{status_badge(run['status'])}</div>
      <div><span>当前步骤</span><strong>{run['current_step']}/{run['total_steps']}</strong></div>
      <div><span>开始</span><strong>{escape(run['started_at'])}</strong></div>
      <div><span>结束</span><strong>{escape(run['finished_at'] or '-')}</strong></div>
      <div><span>验证状态</span><strong>{escape(verification_status)}</strong></div>
    </section>
    {render_error(run['error'])}
    {scope_confirmation}
    <section class="panel">
      <h2>原始需求</h2>
      <pre>{escape(run['demand_text'])}</pre>
    </section>
    <section class="panel">
      <h2>步骤执行</h2>
      <table>
        <thead><tr><th>顺序</th><th>阶段</th><th>专家</th><th>状态</th><th>耗时</th><th>Tokens</th></tr></thead>
        <tbody>{step_rows}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>专家报告</h2>
      {step_reports}
    </section>
    """
    return layout(f"Run #{run_id}", body)


def render_scope_confirmation_card(artifacts: list[dict]) -> str:
    """Show the latest immutable scope token without exposing provider data."""
    payload = None
    for artifact in reversed(artifacts):
        if artifact.get("kind") != "pre_change_confirmation_json":
            continue
        try:
            candidate = json.loads(artifact.get("content") or "{}")
        except json.JSONDecodeError:
            candidate = None
        if isinstance(candidate, dict):
            payload = candidate
            break
    if not payload:
        return ""
    scope = payload.get("scope") or {}
    projects = scope.get("projects") or []
    change_scopes = {"change_required", "candidate_change"}
    scope_descriptions = {
        "change_required": "需求已命中实际调用链，进入改动范围",
        "candidate_change": "已定位到实际调用链，仍需改动合同确认",
        "existing_dependency": "现有依赖，仅用于链路证据，不代表要改",
        "contract_check": "仅用于接口契约核验，不代表要改",
        "candidate_only": "仅候选，未形成实际改动证据",
        "legacy_selected": "旧数据未记录分层，暂按已选择项目展示",
    }
    change_projects = [
        item for item in projects
        if isinstance(item, dict)
        and (str(item.get("selection_scope") or "legacy_selected") in change_scopes or not item.get("selection_scope"))
    ]
    evidence_projects = [item for item in projects if item not in change_projects]

    def project_row(item: dict) -> str:
        selection_scope_value = item.get("selection_scope") or "legacy_selected"
        selection_scope = escape(selection_scope_value)
        return (
            f"<li><code>{escape(item.get('name') or '-')}</code>"
            f"（{escape(item.get('role') or 'unknown')}，{selection_scope}）："
            f"{escape(item.get('path') or '-')}；"
            f"{escape(scope_descriptions.get(selection_scope_value, selection_scope_value))}</li>"
        )

    project_rows = "".join(
        project_row(item)
        for item in change_projects
        if isinstance(item, dict)
    ) or "<li>尚未形成项目范围</li>"
    evidence_rows = "".join(
        project_row(item)
        for item in evidence_projects
        if isinstance(item, dict)
    ) or "<li>无</li>"
    path_rows = "".join(
        f"<li><code>{escape(path)}</code></li>"
        for path in scope.get("allowed_paths") or []
    ) or "<li>无</li>"
    command_rows = "".join(
        f"<li><code>{escape(command)}</code></li>"
        for command in scope.get("verify_commands") or []
    ) or "<li>未配置</li>"
    status = str(payload.get("status") or "pending")
    status_text = {
        "confirmed": "已确认，等待上游评估通过",
        "pending": "待确认；没有令牌不会进入改码",
        "blocked": "已阻断",
        "not_required": "只读模式，不需要确认",
    }.get(status, status)
    token = escape(payload.get("confirmation_token") or "-")
    return f"""
    <section class="panel">
      <h2>改动前范围确认</h2>
      <p><strong>{escape(status_text)}</strong> · 执行模式：<code>{escape(scope.get('execution_mode') or '-')}</code></p>
      <p>{escape(payload.get('reason') or '')}</p>
      <p>确认令牌（仅绑定本次项目、路径、验证命令和变更合同）：<br><code>{token}</code></p>
      <p class="muted">请确认下列范围无误后，在下一次同一需求运行中提交该令牌；范围变化后令牌自动失效。</p>
      <details><summary>查看冻结范围</summary>
        <h4>实际改动候选项目 / 服务</h4><ul>{project_rows}</ul>
        <h4>证据与核验项目（不代表要改）</h4><ul>{evidence_rows}</ul>
        <h4>允许路径</h4><ul>{path_rows}</ul>
        <h4>验证命令</h4><ul>{command_rows}</ul>
      </details>
    </section>
    """


def layout(title: str, body: str) -> str:
    return f"""
    <!doctype html>
    <html lang="zh-CN">
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>{escape(title)}</title>
      <style>{STYLE}</style>
    </head>
    <body>
      <header>
        <a class="brand" href="/">东昉HIS Bot Manager</a>
        <nav>
          <a href="/">需求入口</a>
          <a href="/experts">专家中心</a>
          <a href="/runs">运行记录</a>
          <a href="/providers">Provider维护</a>
          <a href="/routing">自动路由</a>
          <a href="/code-evidence">代码证据</a>
          <a href="/knowledge">知识咨询</a>
        </nav>
      </header>
      <main>{body}</main>
    </body>
    </html>
    """


def status_badge(status: str) -> str:
    css = "ok" if status == "success" else "bad" if status == "failed" else "running"
    text = {"success": "成功", "failed": "失败", "running": "运行中"}.get(status, status)
    return f'<span class="badge {css}">{escape(text)}</span>'


def error_box(error: str) -> str:
    if not error:
        return ""
    return f'<section class="alert">{escape(error)}</section>'


def render_error(error: str) -> str:
    if not error:
        return ""
    return f'<div class="alert">{escape(error)}</div>'


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def main() -> None:
    try:
        database.init_db()
        database.reconcile_stale_runs(max_age_hours=24)
        database.reconcile_stale_tasks(max_age_hours=24)
    except (OSError, sqlite3.OperationalError) as exc:
        # Keep the UI available for readonly diagnostics when the checkout's
        # default data directory is not writable. Do not touch or replace it.
        from app.runtime_preflight import choose_private_runtime_root

        fallback_root = choose_private_runtime_root(prefix="his_harness_server_")
        database.DB_PATH = fallback_root / "harness.sqlite"
        database.init_db()
        print(f"Harness runtime degraded_readonly: {type(exc).__name__}: {exc}")
    port = int(os.environ.get("HARNESS_PORT", str(DEFAULT_PORT)))
    server = ThreadingHTTPServer((HOST, port), HarnessRequestHandler)
    print(f"HIS AI Harness Lite running at http://{HOST}:{port}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


STYLE = """
:root {
  --bg: #f6f8fb;
  --panel: #ffffff;
  --line: #d9e0ea;
  --text: #172033;
  --muted: #5e6b7f;
  --brand: #1458d4;
  --brand-dark: #0f3f9b;
  --ok: #118454;
  --bad: #c93535;
  --running: #0b70d7;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  color: var(--text);
  background: var(--bg);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 14px;
}
header {
  height: 42px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 18px;
  background: #20252b;
  color: #fff;
}
header a { color: #e8edf5; text-decoration: none; }
.brand { font-weight: 700; font-size: 15px; }
nav { display: flex; gap: 18px; font-size: 13px; }
main { max-width: 980px; margin: 0 auto; padding: 20px 16px 48px; }
h1, h2, h3 { margin: 0; line-height: 1.35; }
h1 { font-size: 26px; }
h2 { font-size: 17px; margin-bottom: 14px; }
h3 { font-size: 15px; }
p { margin: 8px 0; color: var(--muted); }
.hero, .page-title {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.hero-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.panel, .card, .stats > div, .alert {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 6px;
}
.panel { padding: 14px; margin-bottom: 14px; }
.alert {
  color: #8a1f1f;
  border-color: #f0b7b7;
  background: #fff5f5;
  padding: 12px;
  margin-bottom: 14px;
}
label { display: block; font-weight: 700; margin: 12px 0 6px; }
input, textarea, select {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 5px;
  padding: 9px 10px;
  font: inherit;
  background: #fff;
}
textarea { resize: vertical; min-height: 210px; }
.form-actions { display: flex; justify-content: flex-end; margin-top: 12px; }
button, .button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 32px;
  padding: 0 12px;
  border: 1px solid var(--brand);
  border-radius: 5px;
  color: #fff;
  background: var(--brand);
  font: inherit;
  text-decoration: none;
  cursor: pointer;
}
button:hover, .button:hover { background: var(--brand-dark); }
.button.secondary {
  color: var(--brand);
  background: #fff;
}
.button.secondary:hover {
  color: #fff;
  background: var(--brand);
}
table { width: 100%; border-collapse: collapse; }
th, td {
  border-bottom: 1px solid var(--line);
  padding: 10px 8px;
  text-align: left;
  vertical-align: top;
}
th { font-weight: 700; color: #263246; background: #fbfcfe; }
.badge {
  display: inline-block;
  padding: 2px 7px;
  border-radius: 5px;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
}
.badge.ok { background: var(--ok); }
.badge.bad { background: var(--bad); }
.badge.running { background: var(--running); }
.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.card {
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr);
  gap: 12px;
  padding: 12px;
}
.ops-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
.ops-card {
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fbfcfe;
  padding: 12px;
}
.ops-card ul {
  margin: 8px 0;
  padding-left: 18px;
  color: var(--muted);
}
.ops-card li { margin: 4px 0; }
.expert-mark {
  width: 36px;
  height: 36px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #c8f2b6;
  color: #183412;
  font-weight: 800;
}
.muted, .meta { color: var(--muted); font-size: 13px; }
details { margin-top: 8px; }
summary { cursor: pointer; font-weight: 700; }
pre {
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  margin: 8px 0 0;
  padding: 10px;
  border-radius: 5px;
  border: 1px solid #e1e7f0;
  background: #f9fbfe;
  font-size: 13px;
  line-height: 1.55;
}
.stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}
.stats > div { padding: 12px; }
.stats span { display: block; color: var(--muted); font-size: 12px; margin-bottom: 8px; }
.report {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 10px;
  margin-bottom: 10px;
}
.row-between { align-items: center; }
@media (max-width: 760px) {
  header { height: auto; align-items: flex-start; flex-direction: column; gap: 8px; padding: 10px 14px; }
  nav { flex-wrap: wrap; gap: 10px; }
  .hero, .page-title, .row-between { flex-direction: column; }
  .grid, .stats, .ops-grid { grid-template-columns: 1fr; }
}
"""
