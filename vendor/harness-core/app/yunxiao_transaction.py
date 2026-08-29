from __future__ import annotations

import hashlib
import json
import uuid
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app import database
from app.llm_client import redact_secrets
from app.yunxiao_read import DEFAULT_YUNXIAO_BASE_URL, first_configured_value, redact_query


# Existing transaction flows retain their compatibility behavior.  They are not
# registered as Manager Provider routes; Manager execution uses app.providers.yunxiao.
LEGACY_YUNXIAO_TRANSACTION_COMPATIBILITY_ONLY = True


YUNXIAO_ENTITY_KINDS = [
    "iteration",
    "requirement",
    "bug",
    "task",
    "sub_task",
    "assignee",
    "status",
    "comment",
    "attachment",
    "branch",
    "commit",
    "merge_request",
    "release",
]

YUNXIAO_ACTIONS = [
    "read",
    "comment",
    "upload_attachment",
    "assign",
    "transition",
    "update_iteration",
    "update_service_change",
    "create_task",
    "link_artifact",
    "close",
]
YUNXIAO_WRITE_ACTIONS = [
    "comment",
    "upload_attachment",
    "assign",
    "transition",
    "update_iteration",
    "update_service_change",
    "create_task",
    "link_artifact",
    "close",
]
DEFAULT_ENABLED_ACTIONS = {
    "read": True,
    "comment": False,
    "upload_attachment": False,
    "assign": False,
    "transition": False,
    "update_iteration": False,
    "update_service_change": False,
    "create_task": False,
    "link_artifact": False,
    "close": False,
}
HIGH_RISK_LEVELS = {"high", "critical"}
HIGH_RISK_TERMS = ["医保", "结算", "收费", "报表", "日报", "汇总日报", "统计报表", "对账", "政策校验", "核算", "权限控制", "优惠", "减免"]
DEFAULT_TRANSITIONS = {
    "待处理": ["分析中", "待澄清"],
    "待开发": ["开发中", "待澄清", "待测试", "待人工审核"],
    "分析中": ["开发中", "待澄清", "待人工审核"],
    "开发中": ["待测试", "待修复", "待人工审核"],
    "待修复": ["开发中", "待测试"],
    "待测试": ["测试中", "开发中", "待人工审核"],
    "测试中": ["待发布", "开发中", "待人工审核"],
    "待人工审核": ["开发中", "待测试", "待发布"],
    "待发布": ["已完成"],
}
OUTCOME_TRANSITION_RECOMMENDATIONS = {
    "analysis_unclear": "待澄清",
    "developed_unverified": "待测试",
    "verification_failed": "开发中",
    "high_risk_needs_review": "待人工审核",
    "all_passed": "",
}
YUNXIAO_WRITE_TIMEOUT_SECONDS = 30
WRITE_EXECUTED_STATUSES = {"write_executed", "write_skipped_idempotent"}
WRITE_FAILED_STATUSES = {"write_blocked", "write_failed", "verify_failed"}
YUNXIAO_WRITE_SCOPES = {"comment-only", "transition-fake"}
IDEMPOTENCY_MARKER_PREFIX = "HIS-HARNESS-IDEMPOTENCY:"
COMMENT_TEMPLATE_VERSION = "delivery-v2-hidden-marker"
DEFAULT_COMMENT_BASE_BRANCH = "RC_2.16.1_250514"
MEDIA_ARTIFACT_TYPES = {"screenshot", "video", "gif"}


@dataclass
class YunxiaoEntityRef:
    kind: str
    entity_id: str
    title: str = ""
    url: str = ""
    iteration_id: str = ""

    def validate(self) -> list[str]:
        issues: list[str] = []
        if self.kind not in YUNXIAO_ENTITY_KINDS:
            issues.append(f"未知云效实体类型：{self.kind}")
        if not self.entity_id:
            issues.append("云效实体缺少 entity_id")
        return issues


@dataclass
class YunxiaoActor:
    actor_id: str
    name: str
    actor_type: str = "ai_harness"


@dataclass
class YunxiaoWriteCredentialBundle:
    token: str = ""
    organization_id: str = ""
    token_source: str = ""
    organization_source: str = ""
    token_kind: str = "missing"
    missing_keys: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.token and self.organization_id)

    def safe_summary(self) -> dict:
        return {
            "write_token": "present" if self.token else "missing",
            "organization_id": "present" if self.organization_id else "missing",
            "write_token_source": self.token_source,
            "write_token_kind": self.token_kind,
            "organization_source": self.organization_source,
            "missing_keys": list(self.missing_keys),
        }


@dataclass
class YunxiaoTransactionRequest:
    project_key: str
    entity: YunxiaoEntityRef
    action: str
    run_id: int | None = None
    actor: YunxiaoActor = field(default_factory=lambda: YunxiaoActor(actor_id="his-harness", name="HIS AI Harness"))
    payload: dict = field(default_factory=dict)
    before_state: dict = field(default_factory=dict)
    expected_after_state: dict = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    risk_level: str = "medium"
    reason: str = ""
    model_mode: str = ""
    model_name: str = ""
    human_confirmed: bool = False

    def idempotency_key(self) -> str:
        evidence_ids = list(self.evidence_ids)
        payload = dict(self.payload)
        if self.action == "comment" and self.payload.get("comment_type") == "ai_harness_report":
            evidence_ids = [item for item in evidence_ids if not str(item).startswith("run:")]
            payload.setdefault("comment_template_version", COMMENT_TEMPLATE_VERSION)
        raw = json.dumps(
            {
                "project_key": self.project_key,
                "entity_kind": self.entity.kind,
                "entity_id": self.entity.entity_id,
                "action": self.action,
                "payload": payload,
                "expected_after_state": self.expected_after_state,
                "evidence_ids": evidence_ids,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return "yx-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class YunxiaoDecision:
    allowed: bool
    status: str
    reason: str
    required_human_confirmation: bool = False
    blocked_by: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class YunxiaoPolicy:
    project_key: str = "default"
    enabled_actions: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_ENABLED_ACTIONS))
    allowed_transitions: dict[str, list[str]] = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_TRANSITIONS, ensure_ascii=False)))
    high_risk_terms: list[str] = field(default_factory=lambda: list(HIGH_RISK_TERMS))
    block_close_by_default: bool = True
    require_run_id_for_writes: bool = True
    require_evidence_for_writes: bool = True
    require_reason_for_writes: bool = True
    field_mappings: dict[str, dict] = field(default_factory=dict)
    max_attachment_bytes: int = 10 * 1024 * 1024
    high_risk_human_gate_actions: list[str] = field(
        default_factory=lambda: [
            "upload_attachment",
            "assign",
            "transition",
            "update_iteration",
            "update_service_change",
            "close",
        ]
    )
    note: str = "默认只读；云效写入动作必须显式开启、带 run_id、证据和原因，并经过策略校验。"

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


class YunxiaoTransactionPolicy:
    def __init__(self, policy: YunxiaoPolicy | None = None) -> None:
        self.policy = policy or YunxiaoPolicy()

    def decide(self, request: YunxiaoTransactionRequest) -> YunxiaoDecision:
        blockers: list[str] = []

        if request.action not in YUNXIAO_ACTIONS:
            blockers.append(f"未知云效动作：{request.action}")
        blockers.extend(request.entity.validate())

        if request.action in YUNXIAO_ACTIONS and not self.policy.enabled_actions.get(request.action, False):
            blockers.append(f"动作未开启：{request.action}")

        if request.action in YUNXIAO_WRITE_ACTIONS:
            if self.policy.require_run_id_for_writes and request.run_id is None:
                blockers.append("云效写动作必须绑定 Harness run_id")
            if self.policy.require_evidence_for_writes and not request.evidence_ids:
                blockers.append("云效写动作必须绑定工程证据或报告证据")
            if self.policy.require_reason_for_writes and not request.reason.strip():
                blockers.append("云效写动作必须提供负责人/状态/内容变更理由")

        if request.action == "assign" and not request.payload.get("to_assignee"):
            blockers.append("负责人流转必须指定 to_assignee")

        if request.action == "transition":
            blockers.extend(self._validate_transition(request))

        if request.action == "upload_attachment":
            if request.payload.get("exists") is False:
                blockers.append(f"附件文件不存在：{request.payload.get('path') or request.payload.get('name') or '-'}")
            if not request.payload.get("sha256"):
                blockers.append("附件上传计划必须包含文件 sha256")

        if request.action == "update_iteration" and not request.payload.get("target_iteration"):
            blockers.append("迭代调整必须指定 target_iteration")

        if request.action == "update_service_change":
            if request.payload.get("valid") is False:
                blockers.append(request.payload.get("error") or "服务变更计划无效")
            if not request.payload.get("service_change"):
                blockers.append("服务变更计划必须包含 service_change 内容")

        if request.action == "link_artifact":
            if not request.payload.get("artifact_type") or not request.payload.get("artifact"):
                blockers.append("产物关联必须包含 artifact_type 和 artifact")

        high_risk = self._is_high_risk(request)
        if high_risk and request.action in self.policy.high_risk_human_gate_actions and not request.human_confirmed:
            blockers.append("高风险需求必须人工确认后才能执行该云效动作")

        if request.action == "close":
            if self.policy.block_close_by_default:
                blockers.append("默认禁止 AI 自动关闭云效需求或缺陷，只能生成关闭建议")
            if high_risk:
                blockers.append("高风险需求禁止自动关闭")

        if blockers:
            return YunxiaoDecision(
                allowed=False,
                status="rejected",
                reason="；".join(blockers),
                required_human_confirmation=any("人工确认" in item for item in blockers),
                blocked_by=blockers,
            )

        return YunxiaoDecision(allowed=True, status="allowed", reason="策略校验通过")

    def _validate_transition(self, request: YunxiaoTransactionRequest) -> list[str]:
        blockers: list[str] = []
        from_status = str(request.before_state.get("status") or request.payload.get("from_status") or "")
        to_status = str(request.expected_after_state.get("status") or request.payload.get("to_status") or "")
        if not to_status:
            blockers.append("状态流转必须指定目标状态")
            return blockers
        if from_status:
            allowed_targets = self.policy.allowed_transitions.get(from_status, [])
            if allowed_targets and to_status not in allowed_targets:
                blockers.append(f"不允许从“{from_status}”直接流转到“{to_status}”")
        return blockers

    def _is_high_risk(self, request: YunxiaoTransactionRequest) -> bool:
        if request.risk_level in HIGH_RISK_LEVELS:
            return True
        text = json.dumps(
            {
                "title": request.entity.title,
                "payload": request.payload,
                "reason": request.reason,
            },
            ensure_ascii=False,
        )
        return any(term in text for term in self.policy.high_risk_terms)


def load_yunxiao_write_credentials() -> YunxiaoWriteCredentialBundle:
    token, token_source, token_kind = read_write_token()
    organization_id, organization_source = first_configured_value(
        ["aliyun_devops_organization_id", "ALIYUN_DEVOPS_ORGANIZATION_ID"]
    )
    missing: list[str] = []
    if not token:
        missing.append("aliyun_devops_write_pat 或 aliyun_devops_pat")
    if not organization_id:
        missing.append("aliyun_devops_organization_id")
    return YunxiaoWriteCredentialBundle(
        token=token,
        organization_id=organization_id,
        token_source=token_source,
        token_kind=token_kind,
        organization_source=organization_source,
        missing_keys=missing,
    )


def read_write_token() -> tuple[str, str, str]:
    token, source = first_configured_value(["aliyun_devops_write_pat", "ALIYUN_DEVOPS_WRITE_PAT"])
    if token:
        return token, source, "dedicated_write_pat"
    token, source = first_configured_value(["aliyun_devops_pat", "ALIYUN_DEVOPS_PAT"])
    if token:
        return token, source, "fallback_read_pat"
    return "", "", "missing"


class YunxiaoWriteClient:
    def __init__(self, *, credentials: YunxiaoWriteCredentialBundle, base_url: str = DEFAULT_YUNXIAO_BASE_URL) -> None:
        self.credentials = credentials
        self.base_url = base_url.rstrip("/")

    def create_comment(self, *, entity: YunxiaoEntityRef, content: str) -> dict:
        body = {"content": content}
        candidates = [
            f"/oapi/v1/projex/organizations/{quote(self.credentials.organization_id)}/workitems/{quote(entity.entity_id)}/comments",
            f"/oapi/v1/projex/workitems/{quote(entity.entity_id)}/comments",
        ]
        return self._first_write_success("POST", candidates, body, label="CreateWorkitemComment")

    def list_comments(self, *, entity: YunxiaoEntityRef) -> dict:
        candidates = [
            f"/oapi/v1/projex/organizations/{quote(self.credentials.organization_id)}/workitems/{quote(entity.entity_id)}/comments",
            f"/oapi/v1/projex/workitems/{quote(entity.entity_id)}/comments",
        ]
        return self._first_write_success("GET", candidates, None, label="ListWorkitemComments")

    def find_comment_marker(self, *, entity: YunxiaoEntityRef, marker: str) -> dict:
        comments = self.list_comments(entity=entity)
        if not comments.get("ok"):
            return comments
        found = find_marker_comment(comments.get("data"), marker)
        return {
            "ok": True,
            "status": "write_executed",
            "found": bool(found),
            "comment": found,
            "data": comments.get("data"),
            "request_id": comments.get("request_id") or "",
            "attempts": comments.get("attempts") or [],
        }

    def update_work_item_property(self, *, entity: YunxiaoEntityRef, property_key: str, property_value: object, field_type: str = "") -> dict:
        body = {
            "organizationId": self.credentials.organization_id,
            "workitemIdentifier": entity.entity_id,
            "propertyKey": property_key,
            "propertyValue": property_value,
        }
        if field_type:
            body["fieldType"] = field_type
        return self._json_request("POST", "/oapi/v1/projex/workitems/update", body)

    def update_workitem_field(self, *, entity: YunxiaoEntityRef, field_identifier: str, field_value: object) -> dict:
        body = {
            "organizationId": self.credentials.organization_id,
            "workitemIdentifier": entity.entity_id,
            "updateWorkitemPropertyRequest": [
                {
                    "fieldIdentifier": field_identifier,
                    "fieldValue": field_value,
                }
            ],
        }
        return self._json_request("POST", "/oapi/v1/projex/workitems/updateWorkitemField", body)

    def get_attachment_create_meta(self, *, entity: YunxiaoEntityRef, file_name: str) -> dict:
        query = urllib.parse.urlencode({"organizationId": self.credentials.organization_id, "fileName": file_name})
        return self._json_request("GET", f"/oapi/v1/projex/workitems/{quote(entity.entity_id)}/attachments/createMeta?{query}", None)

    def upload_to_oss(self, *, upload_meta: dict, path: Path) -> dict:
        data = path.read_bytes()
        upload_url = str(upload_meta.get("host") or upload_meta.get("uploadHost") or "").strip()
        if not upload_url:
            return {"ok": False, "status": "write_failed", "error": "附件上传 meta 缺少 host/uploadHost", "request_id": ""}
        fields = {
            "key": str(upload_meta.get("dir") or upload_meta.get("key") or path.name),
            "policy": str(upload_meta.get("policy") or ""),
            "OSSAccessKeyId": str(upload_meta.get("accessid") or upload_meta.get("accessKeyId") or ""),
            "success_action_status": "200",
            "signature": str(upload_meta.get("signature") or upload_meta.get("Signature") or ""),
        }
        body, content_type = build_multipart_body(fields=fields, file_field="file", file_name=path.name, file_bytes=data)
        request = urllib.request.Request(upload_url, data=body, method="POST", headers={"Content-Type": content_type})
        try:
            with urllib.request.urlopen(request, timeout=YUNXIAO_WRITE_TIMEOUT_SECONDS) as response:
                raw = response.read().decode("utf-8", errors="replace")
            return {
                "ok": 200 <= int(response.status) < 300,
                "status": "write_executed",
                "http_status": response.status,
                "data": truncate_for_audit(raw),
                "file_key": fields["key"],
                "request_id": response.headers.get("x-oss-request-id", ""),
            }
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return {"ok": False, "status": "write_failed", "http_status": exc.code, "error": redact_secrets(truncate_for_audit(detail)), "request_id": ""}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {"ok": False, "status": "write_failed", "error": redact_secrets(truncate_for_audit(str(exc))), "request_id": ""}

    def create_attachment(self, *, entity: YunxiaoEntityRef, file_key: str, file_name: str) -> dict:
        body = {
            "organizationId": self.credentials.organization_id,
            "workitemIdentifier": entity.entity_id,
            "fileKey": file_key,
            "fileName": file_name,
        }
        return self._json_request("POST", "/oapi/v1/projex/workitems/attachments", body)

    def _json_request(self, method: str, path: str, body: dict | None) -> dict:
        url = self.base_url + path
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-yunxiao-token": self.credentials.token,
            "Authorization": f"Bearer {self.credentials.token}",
        }
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=YUNXIAO_WRITE_TIMEOUT_SECONDS) as response:
                raw = response.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw) if raw.strip() else {}
            return {
                "ok": 200 <= int(response.status) < 300,
                "status": "write_executed",
                "http_status": response.status,
                "data": parsed,
                "request_id": response.headers.get("x-acs-request-id", "") or extract_request_id(parsed),
            }
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return {"ok": False, "status": "write_failed", "http_status": exc.code, "error": redact_secrets(truncate_for_audit(detail)), "request_id": ""}
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "status": "write_failed", "error": redact_secrets(truncate_for_audit(str(exc))), "request_id": ""}

    def _first_write_success(self, method: str, paths: list[str], body: dict | None, *, label: str) -> dict:
        attempts: list[dict] = []
        last_response: dict = {"ok": False, "status": "write_failed", "error": f"{label} 未执行", "request_id": ""}
        for path in paths:
            response = self._json_request(method, path, body)
            attempt = {
                "label": label,
                "method": method,
                "path": redact_query(path),
                "status": "success" if response.get("ok") else "failed",
                "http_status": response.get("http_status"),
                "error": response.get("error") or "",
                "request_id": response.get("request_id") or "",
            }
            attempts.append(attempt)
            last_response = response
            if response.get("ok"):
                response = dict(response)
                response["attempts"] = attempts
                return response
        last_response = dict(last_response)
        last_response["attempts"] = attempts
        if not last_response.get("error"):
            last_response["error"] = f"{label} 失败"
        return last_response


class YunxiaoTransactionExecutor:
    def __init__(self, *, policy: YunxiaoPolicy, transport: str = "real") -> None:
        if transport not in {"real", "fake"}:
            raise ValueError("yunxiao write transport 只能是 real 或 fake")
        self.policy = policy
        self.transport = transport
        self._fake_comment_markers: set[str] = set()

    def execute(self, request: YunxiaoTransactionRequest, record: dict) -> dict:
        if record.get("status") not in {"allowed"}:
            return record
        if self.transport == "fake":
            return self._fake_execute(request=request, record=record)
        credentials = load_yunxiao_write_credentials()
        if not credentials.ok:
            return self._write_blocked(record, "缺少云效写凭证：" + "、".join(credentials.missing_keys), {"credential_summary": credentials.safe_summary()})
        if credentials.token_kind != "dedicated_write_pat":
            return self._write_blocked(
                record,
                "真实云效写入必须使用专用 aliyun_devops_write_pat；fallback_read_pat 只允许 dry-run 或 fake transport。",
                {"credential_summary": credentials.safe_summary()},
            )
        client = YunxiaoWriteClient(credentials=credentials)
        return self._real_execute(client=client, request=request, record=record)

    def _fake_execute(self, *, request: YunxiaoTransactionRequest, record: dict) -> dict:
        if request.action == "comment":
            marker = idempotency_marker(record.get("idempotency_key") or request.idempotency_key())
            if marker in self._fake_comment_markers:
                return self._write_skipped_idempotent(record, response={"transport": "fake", "marker": marker, "verified": True})
            self._fake_comment_markers.add(marker)
        if request.action in {"assign", "transition", "close", "update_iteration", "update_service_change", "link_artifact"}:
            mapping = self._mapping_for(request.action)
            if not mapping:
                return self._write_blocked(record, f"{request.action} 缺少字段映射，fake transport 按真实写入规则阻断", {"transport": "fake"})
        fake_error = str(request.payload.get("fake_error") or "").strip()
        if fake_error:
            return self._write_failed(record, fake_error, {"transport": "fake", "simulated": True})
        response = {
            "transport": "fake",
            "action": request.action,
            "entity_id": request.entity.entity_id,
            "request_id": "fake-" + uuid.uuid4().hex[:12],
            "verified": True,
        }
        return self._write_executed(record, response=response, verification_status="verified")

    def _real_execute(self, *, client: YunxiaoWriteClient, request: YunxiaoTransactionRequest, record: dict) -> dict:
        if request.action == "comment":
            marker = idempotency_marker(record.get("idempotency_key") or request.idempotency_key())
            existing = client.find_comment_marker(entity=request.entity, marker=marker)
            if not existing.get("ok"):
                return self._write_blocked(
                    record,
                    "无法读取云效评论做幂等检查，已阻断真实写评论，避免重复评论。",
                    existing,
                )
            if existing.get("found"):
                return self._write_skipped_idempotent(record, response={"marker": marker, "existing_comment": existing.get("comment") or {}, "attempts": existing.get("attempts") or []})
            response = client.create_comment(entity=request.entity, content=build_comment_content(request=request, record=record))
            if not response.get("ok"):
                return self._from_client_response(record, response)
            verified = client.find_comment_marker(entity=request.entity, marker=marker)
            if verified.get("ok") and verified.get("found"):
                return self._write_executed(record, response={**response, "marker": marker, "verification": verified}, verification_status="verified")
            return self._verify_failed(
                record,
                "云效评论写入后无法回读隐藏幂等标记，可能被云效过滤；已标记验证失败，避免后续静默重复评论。",
                {**response, "marker": marker, "verification": verified},
            )
        if request.action == "upload_attachment":
            return self._execute_attachment(client=client, request=request, record=record)
        if request.action in {"assign", "transition", "close", "update_iteration"}:
            mapping = self._mapping_for(request.action)
            property_key = str(mapping.get("propertyKey") or mapping.get("fieldIdentifier") or "").strip()
            if not property_key:
                return self._write_blocked(record, f"{request.action} 缺少字段映射 propertyKey/fieldIdentifier", {"mapping": mapping})
            value = value_for_property_action(request)
            response = client.update_work_item_property(
                entity=request.entity,
                property_key=property_key,
                property_value=value,
                field_type=str(mapping.get("fieldType") or ""),
            )
            return self._from_client_response(record, response)
        if request.action in {"update_service_change", "link_artifact"}:
            mapping = self._mapping_for(request.action)
            field_identifier = str(mapping.get("fieldIdentifier") or mapping.get("propertyKey") or "").strip()
            if not field_identifier:
                return self._write_blocked(record, f"{request.action} 缺少字段映射 fieldIdentifier/propertyKey", {"mapping": mapping})
            response = client.update_workitem_field(
                entity=request.entity,
                field_identifier=field_identifier,
                field_value=json.dumps(request.payload, ensure_ascii=False),
            )
            return self._from_client_response(record, response)
        return self._write_blocked(record, f"暂不支持真实执行动作：{request.action}", {})

    def _execute_attachment(self, *, client: YunxiaoWriteClient, request: YunxiaoTransactionRequest, record: dict) -> dict:
        path = Path(str(request.payload.get("path") or "")).expanduser()
        if not path.exists() or not path.is_file():
            return self._write_blocked(record, f"附件文件不可读：{request.payload.get('path') or '-'}", {})
        size = path.stat().st_size
        if size > self.policy.max_attachment_bytes:
            return self._write_blocked(record, f"附件超过大小限制：{size} > {self.policy.max_attachment_bytes}", {})
        meta = client.get_attachment_create_meta(entity=request.entity, file_name=path.name)
        if not meta.get("ok"):
            return self._from_client_response(record, meta)
        upload_meta = normalize_upload_meta(meta.get("data") or {})
        uploaded = client.upload_to_oss(upload_meta=upload_meta, path=path)
        if not uploaded.get("ok"):
            return self._from_client_response(record, uploaded)
        created = client.create_attachment(entity=request.entity, file_key=uploaded.get("file_key") or upload_meta.get("dir") or path.name, file_name=path.name)
        return self._from_client_response(record, created, extra_response={"upload": uploaded, "meta": upload_meta})

    def _mapping_for(self, action: str) -> dict:
        mappings = self.policy.field_mappings or {}
        value = mappings.get(action) or {}
        return value if isinstance(value, dict) else {}

    def _from_client_response(self, record: dict, response: dict, extra_response: dict | None = None) -> dict:
        payload = dict(response)
        if extra_response:
            payload.update(extra_response)
        if response.get("ok"):
            return self._write_executed(record, response=payload, verification_status="unverified")
        return self._write_failed(record, response.get("error") or "云效写接口返回失败", payload)

    def _write_executed(self, record: dict, *, response: dict, verification_status: str) -> dict:
        record["status"] = "write_executed"
        record["real_write_status"] = "write_executed"
        record["executed_at"] = database.now_iso()
        record["external_request_id"] = str(response.get("request_id") or "")
        record["external_response"] = sanitize_external_response(response)
        record["verification_status"] = verification_status
        record["error"] = ""
        record["decision"] = dict(record.get("decision") or {})
        record["decision"].update({"status": "write_executed", "real_write_status": "write_executed", "verification_status": verification_status})
        record["payload"] = dict(record.get("payload") or {})
        record["payload"]["real_write"] = True
        record["payload"]["real_write_status"] = "write_executed"
        return record

    def _write_skipped_idempotent(self, record: dict, *, response: dict) -> dict:
        record["status"] = "write_skipped_idempotent"
        record["real_write_status"] = "write_skipped_idempotent"
        record["executed_at"] = ""
        record["external_request_id"] = str(response.get("request_id") or "")
        record["external_response"] = sanitize_external_response(response)
        record["verification_status"] = "verified"
        record["error"] = ""
        record["decision"] = dict(record.get("decision") or {})
        record["decision"].update(
            {
                "status": "write_skipped_idempotent",
                "reason": "云效已存在相同幂等标记评论，本次跳过真实写入。",
                "real_write_status": "write_skipped_idempotent",
                "verification_status": "verified",
            }
        )
        record["payload"] = dict(record.get("payload") or {})
        record["payload"]["real_write"] = False
        record["payload"]["real_write_status"] = "write_skipped_idempotent"
        return record

    def _write_blocked(self, record: dict, reason: str, response: dict) -> dict:
        record["status"] = "write_blocked"
        record["real_write_status"] = "not_executed"
        record["external_response"] = sanitize_external_response(response)
        record["verification_status"] = "not_executed"
        record["error"] = reason
        record["decision"] = dict(record.get("decision") or {})
        record["decision"].update({"allowed": False, "status": "write_blocked", "reason": reason, "real_write_status": "not_executed"})
        record["payload"] = dict(record.get("payload") or {})
        record["payload"]["real_write"] = False
        record["payload"]["real_write_status"] = "not_executed"
        return record

    def _write_failed(self, record: dict, reason: str, response: dict) -> dict:
        record["status"] = "write_failed"
        record["real_write_status"] = "failed"
        record["external_response"] = sanitize_external_response(response)
        record["verification_status"] = "failed"
        record["error"] = reason
        record["decision"] = dict(record.get("decision") or {})
        record["decision"].update({"status": "write_failed", "reason": reason, "real_write_status": "failed"})
        record["payload"] = dict(record.get("payload") or {})
        record["payload"]["real_write"] = False
        record["payload"]["real_write_status"] = "failed"
        return record

    def _verify_failed(self, record: dict, reason: str, response: dict) -> dict:
        record["status"] = "verify_failed"
        record["real_write_status"] = "failed"
        record["external_response"] = sanitize_external_response(response)
        record["verification_status"] = "failed"
        record["error"] = reason
        record["decision"] = dict(record.get("decision") or {})
        record["decision"].update({"status": "verify_failed", "reason": reason, "real_write_status": "failed", "verification_status": "failed"})
        record["payload"] = dict(record.get("payload") or {})
        record["payload"]["real_write"] = False
        record["payload"]["real_write_status"] = "failed"
        record["payload"]["verification_status"] = "failed"
        return record


class YunxiaoTransactionManager:
    def __init__(
        self,
        policy: YunxiaoPolicy | None = None,
        *,
        external_write_enabled: bool = False,
        dry_run_enabled: bool = False,
        write_confirm: str = "",
        write_transport: str = "real",
        write_scope: str = "comment-only",
    ) -> None:
        if write_scope not in YUNXIAO_WRITE_SCOPES:
            raise ValueError("yunxiao write scope 只能是 comment-only 或 transition-fake")
        self.policy = policy or YunxiaoPolicy()
        self.policy_engine = YunxiaoTransactionPolicy(self.policy)
        self.external_write_enabled = external_write_enabled
        self.dry_run_enabled = dry_run_enabled
        self.write_confirm = write_confirm
        self.write_transport = write_transport
        self.write_scope = write_scope
        self.executor = YunxiaoTransactionExecutor(policy=self.policy, transport=write_transport) if external_write_enabled else None

    @classmethod
    def readonly(cls) -> "YunxiaoTransactionManager":
        return cls(policy=YunxiaoPolicy(), external_write_enabled=False)

    @classmethod
    def dry_run(cls, policy: YunxiaoPolicy | None = None) -> "YunxiaoTransactionManager":
        return cls(policy=policy or YunxiaoPolicy(), external_write_enabled=False, dry_run_enabled=True)

    @classmethod
    def controlled_write(
        cls,
        *,
        policy: YunxiaoPolicy | None = None,
        write_confirm: str,
        write_transport: str = "real",
        write_scope: str = "comment-only",
    ) -> "YunxiaoTransactionManager":
        return cls(
            policy=policy or YunxiaoPolicy(),
            external_write_enabled=True,
            dry_run_enabled=False,
            write_confirm=write_confirm,
            write_transport=write_transport,
            write_scope=write_scope,
        )

    def policy_summary(self) -> dict:
        if self.external_write_enabled:
            mode = "write"
        elif self.dry_run_enabled:
            mode = "dry_run"
        else:
            mode = "readonly"
        return {
            "mode": mode,
            "external_write_enabled": self.external_write_enabled,
            "dry_run_enabled": self.dry_run_enabled,
            "write_transport": self.write_transport if self.external_write_enabled else "",
            "write_scope": self.write_scope if self.external_write_enabled else "",
            "write_confirm_required": "WRITE:<entity_kind>:<entity_id>",
            "write_confirm_present": bool(self.write_confirm),
            "policy": self.policy.to_dict(),
            "action_levels": list(YUNXIAO_ACTIONS),
            "write_actions": list(YUNXIAO_WRITE_ACTIONS),
            "lifecycle_recommendations": dict(OUTCOME_TRANSITION_RECOMMENDATIONS),
            "safety_note": "v0.8.6 默认真实写入仍只允许 comment-only；transition-fake 只允许 fake transport 验证状态流转管道。",
        }

    def policy_summary_json(self) -> str:
        return json.dumps(self.policy_summary(), ensure_ascii=False, indent=2)

    def plan(self, request: YunxiaoTransactionRequest, *, persist_audit: bool = True) -> dict:
        decision = self.policy_engine.decide(request)
        if request.action in YUNXIAO_WRITE_ACTIONS and decision.allowed and not self.external_write_enabled:
            if self.dry_run_enabled:
                decision = YunxiaoDecision(
                    allowed=True,
                    status="dry_run_allowed",
                    reason="策略校验通过；v0.8.6 dry-run 不读取写 token、不调用云效写接口。",
                )
            else:
                decision = YunxiaoDecision(
                    allowed=False,
                    status="blocked_by_runtime_boundary",
                    reason="当前运行边界禁止直接写入云效事务；只能生成建议和审计记录。",
                    blocked_by=["external_write_disabled"],
                )
        if request.action in YUNXIAO_WRITE_ACTIONS and decision.allowed and self.external_write_enabled:
            if self.write_scope == "comment-only" and request.action != "comment":
                decision = YunxiaoDecision(
                    allowed=False,
                    status="write_blocked",
                    reason="当前写入范围为 comment-only，只允许真实写评论；状态、负责人、迭代、附件、服务变更、产物关联和关闭均被阻断。",
                    blocked_by=["write_scope_comment_only"],
                )
            elif self.write_scope == "transition-fake":
                if self.write_transport != "fake":
                    decision = YunxiaoDecision(
                        allowed=False,
                        status="write_blocked",
                        reason="transition-fake 只允许 fake transport；真实云效状态流转在 v0.8.6 仍被阻断。",
                        blocked_by=["write_scope_transition_fake_requires_fake"],
                    )
                elif request.action not in {"comment", "transition"}:
                    decision = YunxiaoDecision(
                        allowed=False,
                        status="write_blocked",
                        reason="当前写入范围为 transition-fake，只允许 fake 评论和 fake 状态流转；负责人、迭代、附件、服务变更、产物关联和关闭均被阻断。",
                        blocked_by=["write_scope_transition_fake"],
                    )
        if request.action in YUNXIAO_WRITE_ACTIONS and decision.allowed and self.external_write_enabled:
            expected_confirm = f"WRITE:{request.entity.kind}:{request.entity.entity_id}"
            if self.write_confirm != expected_confirm:
                decision = YunxiaoDecision(
                    allowed=False,
                    status="write_blocked",
                    reason=f"缺少写入确认：需要 --yunxiao-write-confirm {expected_confirm}",
                    blocked_by=["write_confirm_missing"],
                )
        record = self._build_audit_record(request=request, decision=decision)
        record["real_write_status"] = "not_executed"
        record["runtime_mode"] = "write" if self.external_write_enabled else ("dry_run" if self.dry_run_enabled else "readonly")
        record["executed_at"] = ""
        record["external_request_id"] = ""
        record["external_response"] = {}
        record["verification_status"] = "not_executed" if self.external_write_enabled else "not_applicable"
        record["decision"] = dict(record.get("decision") or {})
        record["decision"]["runtime_mode"] = record["runtime_mode"]
        record["decision"]["real_write_status"] = record["real_write_status"]
        record["decision"]["verification_status"] = record["verification_status"]
        record["payload"] = dict(record.get("payload") or {})
        record["payload"].setdefault("real_write", False)
        record["payload"]["runtime_mode"] = record["runtime_mode"]
        record["payload"]["real_write_status"] = record["real_write_status"]
        if request.action == "comment":
            record["payload"]["comment_preview"] = build_comment_content(request=request, record=record)
        if request.action in YUNXIAO_WRITE_ACTIONS and self.external_write_enabled and decision.allowed and self.executor is not None:
            record = self.executor.execute(request, record)
            record["runtime_mode"] = "write"
            record["decision"] = dict(record.get("decision") or {})
            record["decision"]["runtime_mode"] = "write"
            record["decision"].setdefault("real_write_status", record.get("real_write_status", "not_executed"))
            record["decision"].setdefault("verification_status", record.get("verification_status", "not_executed"))
            record["payload"] = dict(record.get("payload") or {})
            record["payload"]["runtime_mode"] = "write"
            record["payload"].setdefault("real_write_status", record.get("real_write_status", "not_executed"))
        if persist_audit and request.run_id is not None:
            record["audit_id"] = database.add_yunxiao_audit_event(record)
        return record

    def recommend_lifecycle_actions(
        self,
        *,
        project_key: str,
        entity: YunxiaoEntityRef,
        run_id: int,
        outcome: str,
        evidence_ids: list[str],
        risk_level: str,
        model_mode: str = "",
        model_name: str = "",
    ) -> list[dict]:
        target_status = OUTCOME_TRANSITION_RECOMMENDATIONS.get(outcome)
        reason = build_lifecycle_reason(outcome=outcome, risk_level=risk_level)
        requests = [
            YunxiaoTransactionRequest(
                project_key=project_key,
                entity=entity,
                action="comment",
                run_id=run_id,
                payload={"comment_type": "ai_harness_report", "outcome": outcome},
                evidence_ids=evidence_ids,
                risk_level=risk_level,
                reason=reason,
                model_mode=model_mode,
                model_name=model_name,
            )
        ]
        if target_status:
            requests.append(
                YunxiaoTransactionRequest(
                    project_key=project_key,
                    entity=entity,
                    action="transition",
                    run_id=run_id,
                    payload={"to_status": target_status, "outcome": outcome},
                    expected_after_state={"status": target_status},
                    evidence_ids=evidence_ids,
                    risk_level=risk_level,
                    reason=reason,
                    model_mode=model_mode,
                    model_name=model_name,
                )
            )
        return [self.plan(item) for item in requests]

    def _build_audit_record(self, *, request: YunxiaoTransactionRequest, decision: YunxiaoDecision) -> dict:
        return {
            "run_id": request.run_id,
            "project_key": request.project_key,
            "entity_kind": request.entity.kind,
            "entity_id": request.entity.entity_id,
            "entity_title": request.entity.title,
            "entity_url": request.entity.url,
            "action": request.action,
            "status": decision.status,
            "decision": decision.to_dict(),
            "idempotency_key": request.idempotency_key(),
            "actor": asdict(request.actor),
            "reason": request.reason,
            "before_state": request.before_state,
            "after_state": request.expected_after_state,
            "payload": request.payload,
            "evidence_ids": request.evidence_ids,
            "risk_level": request.risk_level,
            "model_mode": request.model_mode,
            "model_name": request.model_name,
            "error": "" if decision.allowed else decision.reason,
        }


def build_lifecycle_reason(*, outcome: str, risk_level: str) -> str:
    mapping = {
        "analysis_unclear": "需求边界或证据不足，需要人工澄清后再进入开发。",
        "developed_unverified": "开发产物已生成但验证证据不足，需要进入测试验证。",
        "verification_failed": "自动验证失败，需要回到开发修复。",
        "high_risk_needs_review": "高风险 HIS 需求需要人工审核业务口径和验收证据。",
        "all_passed": "Harness 分析、执行和验证均通过，可生成完成建议但不默认自动关闭。",
    }
    return f"{mapping.get(outcome, 'Harness 产出状态变化建议。')} 风险等级：{risk_level}。"


def idempotency_marker(idempotency_key: str) -> str:
    return f"{IDEMPOTENCY_MARKER_PREFIX}{idempotency_key}"


def hidden_idempotency_marker(idempotency_key: str) -> str:
    return f"<!-- {idempotency_marker(idempotency_key)} -->"


def build_comment_content(*, request: YunxiaoTransactionRequest, record: dict) -> str:
    key = record.get("idempotency_key") or request.idempotency_key()
    marker = hidden_idempotency_marker(str(key))
    context = normalize_comment_context(request.payload.get("comment_context") or {})
    changed_files = context.get("changed_files") or []
    media_items = context.get("media") or []
    return "\n".join(
        [
            "## HIS Harness 研发交付评论",
            "",
            f"需求：{context.get('demand_id') or request.entity.entity_id or '-'}",
            f"提交：{context.get('commit') or '-'}",
            f"分支：{format_delivery_branch(entity=request.entity, context=context)}",
            "改动范围：",
            *format_changed_file_lines(changed_files),
            "",
            "改动说明：",
            *format_paragraph_lines(context.get("change_summary") or request.reason or "见 Harness 报告。"),
            "",
            "验证结果：",
            *format_verification_lines(context=context, media_items=media_items, changed_files=changed_files),
            "",
            "测试建议：",
            *format_test_suggestion_lines(context=context, request=request),
            "",
            marker,
        ]
    )


def normalize_comment_context(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    context = dict(value)
    context["changed_files"] = unique_texts(context.get("changed_files") or [])
    context["media"] = [item for item in context.get("media") or [] if isinstance(item, dict)]
    context["verification_notes"] = unique_texts(context.get("verification_notes") or [])
    context["test_suggestions"] = unique_texts(context.get("test_suggestions") or [])
    return context


def format_delivery_branch(*, entity: YunxiaoEntityRef, context: dict) -> str:
    base_branch = str(context.get("base_branch") or DEFAULT_COMMENT_BASE_BRANCH).strip() or DEFAULT_COMMENT_BASE_BRANCH
    branch = str(context.get("branch") or "").strip()
    if not branch:
        prefix = "hotfix" if entity.kind == "bug" else "feature"
        branch = f"{prefix}-{entity.entity_id}" if entity.entity_id else prefix
    if "+" in branch:
        return branch
    return f"{branch} + {base_branch}"


def format_changed_file_lines(changed_files: list[str]) -> list[str]:
    if not changed_files:
        return ["- 未读取到改动文件；请结合提交 diff 或 Harness 报告人工确认。"]
    return [f"- {path}" for path in changed_files]


def format_paragraph_lines(text: str) -> list[str]:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return [f"- {line}" for line in lines] if lines else ["- 见 Harness 报告。"]


def format_verification_lines(*, context: dict, media_items: list[dict], changed_files: list[str]) -> list[str]:
    lines: list[str] = []
    if media_items:
        lines.append("- 视觉证据：")
        for item in media_items:
            label = media_type_label(str(item.get("type") or "screenshot"))
            name = item.get("name") or "-"
            if item.get("exists"):
                lines.append(f"  - {label}：{name}，size={item.get('size') or 0}，sha256={item.get('sha256') or '-'}")
            else:
                error = item.get("error") or "未读取到本地文件元信息"
                lines.append(f"  - {label}：{name}（{error}）")
    else:
        lines.append("- 未提供截图/视频/GIF。")
    verification_notes = context.get("verification_notes") or []
    if verification_notes:
        lines.append("- 自动验证/证据摘要：")
        lines.extend(f"  - {item}" for item in verification_notes)
    else:
        lines.append("- 自动验证/证据摘要：未在评论上下文中提供验证日志；请查看 Harness 报告或本地验证输出。")
    if changed_files:
        lines.append("- 改动点说明：本次改动文件见“改动范围”，评论不声明已完成业务验收。")
    return lines


def format_test_suggestion_lines(*, context: dict, request: YunxiaoTransactionRequest) -> list[str]:
    suggestions = context.get("test_suggestions") or []
    if suggestions:
        return [f"- {item}" for item in suggestions]
    lines = [
        "- 按需求入口进入对应页面或流程，确认本次改动展示/交互符合预期。",
        "- 覆盖有数据、无数据、刷新/重新进入页面等边界场景。",
        "- 回归改动页面所在主流程，确认原查询、保存、结算或提交动作不受影响。",
    ]
    if request.risk_level in HIGH_RISK_LEVELS or any(term in (request.entity.title or "") for term in HIGH_RISK_TERMS):
        lines.append("- 涉及收费、结算、医保、报表或权限控制时，必须由业务测试人工复核后再流转。")
    return lines


def media_type_label(media_type: str) -> str:
    mapping = {"screenshot": "截图", "video": "视频", "gif": "GIF"}
    return mapping.get(media_type, media_type or "附件")


def unique_texts(items: object) -> list[str]:
    values = items if isinstance(items, list) else [items]
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def value_for_property_action(request: YunxiaoTransactionRequest) -> object:
    if request.action == "assign":
        return request.payload.get("to_assignee")
    if request.action in {"transition", "close"}:
        return request.expected_after_state.get("status") or request.payload.get("to_status")
    if request.action == "update_iteration":
        return request.payload.get("target_iteration")
    return request.payload


def normalize_upload_meta(data: object) -> dict:
    if isinstance(data, dict):
        for key in ["data", "result", "uploadInfo", "uploadMeta"]:
            nested = data.get(key)
            if isinstance(nested, dict):
                return nested
        return data
    return {}


def sanitize_external_response(value: object) -> dict:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        text = str(value)
    redacted = redact_secrets(text)
    if len(redacted) > 3000:
        redacted = redacted[:3000] + "...（已截断）"
    try:
        parsed = json.loads(redacted)
    except json.JSONDecodeError:
        parsed = {"text": redacted}
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def extract_request_id(data: object) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ["requestId", "request_id", "RequestId", "traceId", "trace_id"]:
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def find_marker_comment(data: object, marker: str) -> dict:
    if not marker:
        return {}
    if isinstance(data, dict):
        text_fields = []
        for key in ["content", "comment", "body", "text", "description"]:
            value = data.get(key)
            if isinstance(value, str):
                text_fields.append(value)
        if any(marker in value for value in text_fields):
            return {
                "id": str(data.get("id") or data.get("identifier") or data.get("commentId") or data.get("commentIdentifier") or ""),
                "content_excerpt": truncate_for_audit(" ".join(text_fields), 500),
            }
        for value in data.values():
            found = find_marker_comment(value, marker)
            if found:
                return found
    if isinstance(data, list):
        for item in data:
            found = find_marker_comment(item, marker)
            if found:
                return found
    if isinstance(data, str) and marker in data:
        return {"id": "", "content_excerpt": truncate_for_audit(data, 500)}
    return {}


def build_multipart_body(*, fields: dict[str, str], file_field: str, file_name: str, file_bytes: bytes) -> tuple[bytes, str]:
    boundary = "----his-harness-" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n".encode("utf-8")
    )
    chunks.append(file_bytes)
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def quote(value: object) -> str:
    return urllib.parse.quote(str(value or ""), safe="")


def truncate_for_audit(text: str, limit: int = 2000) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit] + "...（已截断）"


def build_yunxiao_transaction_plan(
    *,
    manager: YunxiaoTransactionManager,
    project_key: str,
    entity: YunxiaoEntityRef,
    run_id: int,
    outcome: str,
    evidence_ids: list[str],
    risk_level: str,
    model_mode: str = "",
    model_name: str = "",
    current_status: str = "",
    target_assignee: str = "",
    target_status: str = "",
    target_iteration: str = "",
    screenshot_paths: list[str] | None = None,
    service_change_file: str = "",
    artifacts: list[str] | None = None,
    human_confirmed: bool = False,
    persist_audit: bool = True,
) -> dict:
    if not entity.entity_id:
        return {
            "status": "failed",
            "mode": "dry_run" if manager.dry_run_enabled else "readonly",
            "summary": "缺少云效 entity id，无法生成事务计划。",
            "real_write_status": "not_executed",
            "errors": ["缺少云效 entity id"],
            "entity": asdict(entity),
            "outcome": outcome,
            "actions": [],
        }

    requests = build_lifecycle_requests(
        project_key=project_key,
        entity=entity,
        run_id=run_id,
        outcome=outcome,
        evidence_ids=evidence_ids,
        risk_level=risk_level,
        model_mode=model_mode,
        model_name=model_name,
        current_status=current_status,
        target_assignee=target_assignee,
        target_status=target_status,
        target_iteration=target_iteration,
        screenshots=build_screenshot_payloads(screenshot_paths or []),
        service_change=build_service_change_payload(service_change_file),
        artifacts=build_artifact_payloads(artifacts or []),
        human_confirmed=human_confirmed,
    )
    actions = [manager.plan(item, persist_audit=persist_audit) for item in requests]
    allowed_count = sum(1 for item in actions if item.get("decision", {}).get("allowed"))
    executed_count = sum(1 for item in actions if item.get("status") in WRITE_EXECUTED_STATUSES)
    failed_count = sum(1 for item in actions if item.get("status") in WRITE_FAILED_STATUSES)
    rejected_count = len(actions) - allowed_count
    mode = "write" if manager.external_write_enabled else ("dry_run" if manager.dry_run_enabled else "readonly")
    real_write_status = summarize_real_write_status(actions, mode)
    effective_write_status = summarize_effective_write_status(
        actions,
        mode=mode,
        write_scope=manager.write_scope if manager.external_write_enabled else "",
        write_transport=manager.write_transport if manager.external_write_enabled else "",
    )
    return {
        "status": "planned",
        "mode": mode,
        "write_scope": manager.write_scope if manager.external_write_enabled else "",
        "summary": build_plan_summary(
            mode=mode,
            action_count=len(actions),
            allowed_count=allowed_count,
            rejected_count=rejected_count,
            executed_count=executed_count,
            failed_count=failed_count,
        ),
        "real_write_status": real_write_status,
        "effective_write_status": effective_write_status,
        "entity": asdict(entity),
        "outcome": outcome,
        "risk_level": risk_level,
        "evidence_ids": evidence_ids,
        "inputs": {
            "current_status": current_status,
            "target_status": target_status,
            "target_assignee": target_assignee,
            "target_iteration": target_iteration,
            "screenshot_count": len(screenshot_paths or []),
            "service_change_file": service_change_file,
            "artifact_count": len(artifacts or []),
            "human_confirmed": human_confirmed,
        },
        "actions": actions,
        "errors": [],
    }


def build_lifecycle_requests(
    *,
    project_key: str,
    entity: YunxiaoEntityRef,
    run_id: int,
    outcome: str,
    evidence_ids: list[str],
    risk_level: str,
    model_mode: str,
    model_name: str,
    current_status: str,
    target_assignee: str,
    target_status: str,
    target_iteration: str,
    screenshots: list[dict],
    service_change: dict | None,
    artifacts: list[dict],
    human_confirmed: bool,
) -> list[YunxiaoTransactionRequest]:
    reason = build_lifecycle_reason(outcome=outcome, risk_level=risk_level)
    comment_context = build_comment_context(
        entity=entity,
        reason=reason,
        screenshots=screenshots,
        artifacts=artifacts,
    )
    requests = [
        YunxiaoTransactionRequest(
            project_key=project_key,
            entity=entity,
            action="comment",
            run_id=run_id,
            payload={
                "comment_type": "ai_harness_report",
                "comment_template_version": COMMENT_TEMPLATE_VERSION,
                "outcome": outcome,
                "report": "report.md",
                "real_write": False,
                "comment_context": comment_context,
            },
            before_state={"status": current_status} if current_status else {},
            evidence_ids=evidence_ids,
            risk_level=risk_level,
            reason=reason,
            model_mode=model_mode,
            model_name=model_name,
            human_confirmed=human_confirmed,
        )
    ]
    resolved_target_status = target_status.strip() or target_status_for_outcome(outcome)
    if resolved_target_status:
        requests.append(
            YunxiaoTransactionRequest(
                project_key=project_key,
                entity=entity,
                action="transition",
                run_id=run_id,
                payload={"from_status": current_status, "to_status": resolved_target_status, "outcome": outcome},
                before_state={"status": current_status} if current_status else {},
                expected_after_state={"status": resolved_target_status},
                evidence_ids=evidence_ids,
                risk_level=risk_level,
                reason=f"{reason} 建议目标状态：{resolved_target_status}。",
                model_mode=model_mode,
                model_name=model_name,
                human_confirmed=human_confirmed,
            )
        )
    if target_iteration:
        requests.append(
            YunxiaoTransactionRequest(
                project_key=project_key,
                entity=entity,
                action="update_iteration",
                run_id=run_id,
                payload={"target_iteration": target_iteration, "outcome": outcome},
                before_state={"status": current_status} if current_status else {},
                expected_after_state={"iteration": target_iteration},
                evidence_ids=evidence_ids,
                risk_level=risk_level,
                reason=f"{reason} 建议调整迭代：{target_iteration}。",
                model_mode=model_mode,
                model_name=model_name,
                human_confirmed=human_confirmed,
            )
        )
    if target_assignee:
        requests.append(
            YunxiaoTransactionRequest(
                project_key=project_key,
                entity=entity,
                action="assign",
                run_id=run_id,
                payload={"to_assignee": target_assignee, "outcome": outcome},
                before_state={"status": current_status} if current_status else {},
                evidence_ids=evidence_ids,
                risk_level=risk_level,
                reason=f"{reason} 建议流转负责人：{target_assignee}。",
                model_mode=model_mode,
                model_name=model_name,
                human_confirmed=human_confirmed,
            )
        )
    for screenshot in screenshots:
        requests.append(
            YunxiaoTransactionRequest(
                project_key=project_key,
                entity=entity,
                action="upload_attachment",
                run_id=run_id,
                payload={**screenshot, "attachment_type": "screenshot", "outcome": outcome},
                before_state={"status": current_status} if current_status else {},
                evidence_ids=evidence_ids,
                risk_level=risk_level,
                reason=f"{reason} 建议关联截图/附件：{screenshot.get('name') or screenshot.get('path') or '-'}。",
                model_mode=model_mode,
                model_name=model_name,
                human_confirmed=human_confirmed,
            )
        )
    if service_change:
        requests.append(
            YunxiaoTransactionRequest(
                project_key=project_key,
                entity=entity,
                action="update_service_change",
                run_id=run_id,
                payload={**service_change, "outcome": outcome},
                before_state={"status": current_status} if current_status else {},
                evidence_ids=evidence_ids,
                risk_level=risk_level,
                reason=f"{reason} 建议更新服务变更计划。",
                model_mode=model_mode,
                model_name=model_name,
                human_confirmed=human_confirmed,
            )
        )
    artifact_payloads = [
        {"artifact_type": "harness_report", "artifact": "report.md", "outcome": outcome, "real_write": False},
        *artifacts,
    ]
    for artifact in artifact_payloads:
        requests.append(
            YunxiaoTransactionRequest(
                project_key=project_key,
                entity=entity,
                action="link_artifact",
                run_id=run_id,
                payload={**artifact, "outcome": outcome, "real_write": False},
                before_state={"status": current_status} if current_status else {},
                evidence_ids=evidence_ids,
                risk_level=risk_level,
                reason=f"{reason} 建议关联产物：{artifact.get('artifact_type') or '-'}。",
                model_mode=model_mode,
                model_name=model_name,
                human_confirmed=human_confirmed,
            )
        )
    return requests


def build_comment_context(*, entity: YunxiaoEntityRef, reason: str, screenshots: list[dict], artifacts: list[dict]) -> dict:
    artifact_map = group_artifacts_by_type(artifacts)
    return {
        "demand_id": entity.entity_id,
        "commit": first_artifact_value(artifact_map, "commit"),
        "branch": first_artifact_value(artifact_map, "branch"),
        "base_branch": first_artifact_value(artifact_map, "base_branch") or DEFAULT_COMMENT_BASE_BRANCH,
        "changed_files": collect_changed_files(artifact_map),
        "change_summary": first_artifact_value(artifact_map, "change_summary")
        or first_artifact_value(artifact_map, "summary")
        or reason,
        "verification_notes": collect_values(
            artifact_map,
            ["verification", "verify", "verify_result", "test_report", "lint", "build"],
        ),
        "test_suggestions": collect_values(artifact_map, ["test_suggestion", "test_suggestions", "manual_test", "acceptance"]),
        "media": build_comment_media(screenshots=screenshots, artifact_map=artifact_map),
    }


def group_artifacts_by_type(artifacts: list[dict]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for item in artifacts:
        artifact_type = str(item.get("artifact_type") or "").strip().lower()
        artifact = str(item.get("artifact") or "").strip()
        if not artifact_type or not artifact:
            continue
        grouped.setdefault(artifact_type, []).append(artifact)
    return grouped


def first_artifact_value(artifact_map: dict[str, list[str]], artifact_type: str) -> str:
    values = artifact_map.get(artifact_type) or []
    return values[0] if values else ""


def collect_values(artifact_map: dict[str, list[str]], artifact_types: list[str]) -> list[str]:
    values: list[str] = []
    for artifact_type in artifact_types:
        values.extend(artifact_map.get(artifact_type) or [])
    return unique_texts(values)


def collect_changed_files(artifact_map: dict[str, list[str]]) -> list[str]:
    values = collect_values(artifact_map, ["changed_file", "changed_files", "file", "files"])
    changed_files: list[str] = []
    for value in values:
        for part in str(value).replace("\n", ",").split(","):
            text = part.strip()
            if text:
                changed_files.append(text)
    return unique_texts(changed_files)


def build_comment_media(*, screenshots: list[dict], artifact_map: dict[str, list[str]]) -> list[dict]:
    media: list[dict] = []
    for screenshot in screenshots:
        item = dict(screenshot)
        item["type"] = "screenshot"
        item["source"] = "yunxiao_screenshot"
        item["name"] = item.get("name") or safe_file_name(item.get("path") or "")
        media.append(item)
    for artifact_type in sorted(MEDIA_ARTIFACT_TYPES):
        for raw_path in artifact_map.get(artifact_type) or []:
            media.append(build_media_artifact_payload(raw_path, artifact_type=artifact_type))
    return media


def build_media_artifact_payload(raw_path: str, *, artifact_type: str) -> dict:
    path_text = str(raw_path or "").strip()
    path = Path(path_text).expanduser()
    payload = {
        "type": artifact_type,
        "source": "yunxiao_artifact",
        "name": safe_file_name(path_text),
        "exists": False,
        "size": 0,
        "sha256": "",
        "error": "未读取到本地文件元信息",
    }
    if not path_text:
        payload["error"] = "文件路径为空"
    elif path.exists() and path.is_file():
        data = path.read_bytes()
        payload.update(
            {
                "exists": True,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "error": "",
            }
        )
    elif path.exists():
        payload["error"] = "路径不是文件"
    return payload


def safe_file_name(path_text: object) -> str:
    text = str(path_text or "").strip()
    if not text:
        return "-"
    return Path(text).name or text


def summarize_real_write_status(actions: list[dict], mode: str) -> str:
    if mode != "write":
        return "not_executed"
    if not actions:
        return "not_executed"
    statuses = {str(item.get("status") or "") for item in actions}
    if statuses and statuses.issubset(WRITE_EXECUTED_STATUSES):
        return "executed"
    if any(status in WRITE_EXECUTED_STATUSES for status in statuses):
        return "partial"
    if any(status in WRITE_FAILED_STATUSES for status in statuses):
        return "failed"
    return "not_executed"


def summarize_effective_write_status(actions: list[dict], *, mode: str, write_scope: str, write_transport: str) -> str:
    if mode != "write":
        return "not_executed"
    if not actions:
        return "not_executed"
    if write_scope == "transition-fake" and write_transport != "fake":
        return "blocked_by_safety"

    expected_actions = expected_write_actions_for_scope(write_scope)
    executed = [item for item in actions if str(item.get("status") or "") in WRITE_EXECUTED_STATUSES]
    expected_blocks = []
    failures = []
    policy_blocks = []

    for item in actions:
        action = str(item.get("action") or "")
        status = str(item.get("status") or "")
        if status in WRITE_EXECUTED_STATUSES:
            continue
        if action not in expected_actions and status in {"rejected", "write_blocked"}:
            expected_blocks.append(item)
            continue
        if status in {"rejected", "write_blocked"}:
            policy_blocks.append(item)
            continue
        if status in WRITE_FAILED_STATUSES or status:
            failures.append(item)

    if failures:
        return "failed"
    if policy_blocks:
        return "blocked_by_policy"
    if executed:
        has_skip = any(str(item.get("status") or "") == "write_skipped_idempotent" for item in executed)
        if expected_blocks:
            return "idempotent_success_with_expected_blocks" if has_skip else "success_with_expected_blocks"
        return "idempotent_success" if has_skip else "success"
    if expected_blocks:
        return "blocked_by_scope"
    return "not_executed"


def expected_write_actions_for_scope(write_scope: str) -> set[str]:
    if write_scope == "transition-fake":
        return {"comment", "transition"}
    return {"comment"}


def build_plan_summary(
    *,
    mode: str,
    action_count: int,
    allowed_count: int,
    rejected_count: int,
    executed_count: int,
    failed_count: int,
) -> str:
    if mode == "write":
        return (
            f"已生成 {action_count} 条云效事务建议：{allowed_count} 条策略允许，{rejected_count} 条策略阻断；"
            f"真实写入执行 {executed_count} 条，阻断/失败 {failed_count} 条。"
        )
    return f"已生成 {action_count} 条云效事务建议：{allowed_count} 条策略允许，{rejected_count} 条被策略阻断；真实写入未执行。"


def target_status_for_outcome(outcome: str) -> str:
    if outcome == "analysis_unclear":
        return "待澄清"
    if outcome == "developed_unverified":
        return "待测试"
    if outcome == "verification_failed":
        return "开发中"
    if outcome == "high_risk_needs_review":
        return "待人工审核"
    return ""


def build_screenshot_payloads(paths: list[str]) -> list[dict]:
    payloads: list[dict] = []
    for raw_path in paths:
        path_text = str(raw_path or "").strip()
        path = Path(path_text).expanduser()
        payload = {
            "path": path_text,
            "name": path.name if path_text else "",
            "exists": False,
            "size": 0,
            "sha256": "",
        }
        if not path_text:
            payload["error"] = "截图路径为空"
        elif not path.exists():
            payload["error"] = "截图文件不存在"
        elif not path.is_file():
            payload["error"] = "截图路径不是文件"
        else:
            data = path.read_bytes()
            payload.update(
                {
                    "exists": True,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "error": "",
                }
            )
        payloads.append(payload)
    return payloads


def build_service_change_payload(path_text: str) -> dict | None:
    if not str(path_text or "").strip():
        return None
    path = Path(path_text).expanduser()
    payload: dict = {
        "file": str(path_text),
        "name": path.name,
        "valid": False,
        "sha256": "",
        "service_change": {},
        "summary": "",
        "error": "",
    }
    if not path.exists():
        payload["error"] = "服务变更文件不存在"
        return payload
    if not path.is_file():
        payload["error"] = "服务变更路径不是文件"
        return payload
    data = path.read_bytes()
    payload["sha256"] = hashlib.sha256(data).hexdigest()
    try:
        service_change = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        payload["error"] = f"服务变更文件不是合法 JSON：{exc}"
        return payload
    payload.update(
        {
            "valid": True,
            "service_change": service_change,
            "summary": summarize_service_change(service_change),
            "error": "",
        }
    )
    return payload


def summarize_service_change(value: object) -> str:
    if isinstance(value, dict):
        for key in ["summary", "title", "name", "description", "change", "service"]:
            text = str(value.get(key) or "").strip()
            if text:
                return text[:300]
        return "keys=" + ",".join(str(key) for key in list(value)[:12])
    if isinstance(value, list):
        return f"list_items={len(value)}"
    return type(value).__name__


def build_artifact_payloads(items: list[str]) -> list[dict]:
    payloads: list[dict] = []
    for raw_item in items:
        text = str(raw_item or "").strip()
        if "=" not in text:
            payloads.append({"artifact_type": "", "artifact": "", "raw": text, "valid": False, "error": "产物参数必须使用 type=value 格式"})
            continue
        artifact_type, artifact = text.split("=", 1)
        payloads.append(
            {
                "artifact_type": artifact_type.strip(),
                "artifact": artifact.strip(),
                "raw": text,
                "valid": bool(artifact_type.strip() and artifact.strip()),
                "error": "" if artifact_type.strip() and artifact.strip() else "产物类型和值不能为空",
            }
        )
    return payloads


def transaction_plan_to_markdown(plan: dict) -> str:
    lines = [
        "## v0.8.6 云效事务计划/写入结果",
        "",
        f"- 状态：{plan.get('status')}",
        f"- 模式：{plan.get('mode')}",
        f"- 写入范围：{plan.get('write_scope') or '-'}",
        f"- 结论：{plan.get('summary')}",
        f"- 真实写入状态：{plan.get('real_write_status') or 'not_executed'}",
        f"- 有效写入状态：{plan.get('effective_write_status') or plan.get('real_write_status') or 'not_executed'}",
        f"- 云效实体：{(plan.get('entity') or {}).get('kind', '-')}/{(plan.get('entity') or {}).get('entity_id', '-')}",
        f"- 运行结果分类：{plan.get('outcome') or '-'}",
        f"- 风险等级：{plan.get('risk_level') or '-'}",
        "",
    ]
    errors = plan.get("errors") or []
    if errors:
        lines.extend(["### 错误", ""])
        lines.extend(f"- {item}" for item in errors)
        lines.append("")
    lines.extend(["### 建议动作", ""])
    actions = plan.get("actions") or []
    if not actions:
        lines.append("- 无。")
    for index, action in enumerate(actions, start=1):
        decision = action.get("decision") or {}
        lines.extend(
            [
                f"#### {index}. {action.get('action')}",
                "",
                f"- 策略是否允许：{'是' if decision.get('allowed') else '否'}",
                f"- 决策状态：{decision.get('status') or action.get('status') or '-'}",
                f"- 执行状态：{action.get('status') or '-'}",
                f"- 阻断/原因：{decision.get('reason') or action.get('reason') or '-'}",
                f"- 计划载荷：{short_json(action.get('payload') or {})}",
                f"- 幂等键：{action.get('idempotency_key') or '-'}",
                f"- 审计 ID：{action.get('audit_id') or '-'}",
                f"- 真实写入状态：{action.get('real_write_status') or 'not_executed'}",
                f"- 回读验证状态：{action.get('verification_status') or '-'}",
                f"- 外部请求 ID：{action.get('external_request_id') or '-'}",
                "",
            ]
        )
        comment_preview = str((action.get("payload") or {}).get("comment_preview") or "").strip()
        if action.get("action") == "comment" and comment_preview:
            lines.extend(["评论预览：", "", "```markdown", comment_preview, "```", ""])
    lines.extend(
        [
            "### 边界",
            "",
            "- off/dry-run 模式不读取写 token、不调用云效写接口。",
            "- write 模式必须显式传入 `WRITE:<entity_kind>:<entity_id>` 确认；高风险动作还需要人工确认。",
            "- v0.8.6 real write 默认 `comment-only`，只允许写评论，状态、负责人、迭代、附件、服务变更、产物关联和关闭均不执行。",
            "- `transition-fake` 只允许 fake transport 验证评论和状态流转管道；真实云效状态流转仍不执行。",
            "- 本地 fake transport 只验证写入链路和审计，不代表真实云效已写入。",
        ]
    )
    return "\n".join(lines)


def short_json(value: object, limit: int = 500) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "...（已截断）"


def load_yunxiao_policy(config_path: str | Path | None = None, *, project_key: str = "default") -> YunxiaoPolicy:
    if config_path is None:
        return YunxiaoPolicy(project_key=project_key)
    path = Path(config_path).expanduser()
    data = json.loads(path.read_text(encoding="utf-8"))
    project_data = None
    for item in data.get("projects", []):
        if item.get("project_key") == project_key:
            project_data = item
            break
    if project_data is None:
        raise KeyError(f"云效事务策略不存在：{project_key}")
    policy_data = dict(project_data.get("policy", {}))
    enabled_actions = dict(DEFAULT_ENABLED_ACTIONS)
    enabled_actions.update(policy_data.pop("enabled_actions", {}))
    allowed_transitions = json.loads(json.dumps(DEFAULT_TRANSITIONS, ensure_ascii=False))
    allowed_transitions.update(policy_data.pop("allowed_transitions", {}))
    return YunxiaoPolicy(
        project_key=project_key,
        enabled_actions=enabled_actions,
        allowed_transitions=allowed_transitions,
        **policy_data,
    )
