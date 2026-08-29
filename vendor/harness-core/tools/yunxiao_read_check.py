from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.llm_client import redact_secrets
from app.yunxiao_read import (
    collect_yunxiao_evidence,
    credentials_file_path,
    credentials_file_permission_issue,
    load_yunxiao_credentials,
    parse_work_item_id,
)
from app.yunxiao_transaction import (
    YunxiaoEntityRef,
    YunxiaoWriteClient,
    find_marker_comment,
    load_yunxiao_write_credentials,
)


DEFAULT_URLS = [
    "https://devops.aliyun.com/projex/req/DFHIS-31226",
    "https://devops.aliyun.com/projex/req/DFHIS-31216",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Yunxiao credentials and read-only work item access.")
    parser.add_argument("--url", action="append", default=[], help="Yunxiao work item URL to read; repeatable")
    parser.add_argument("--output-dir", default="yunxiao_read_checks", help="directory for report/json outputs")
    parser.add_argument("--credentials-file", default="", help="override HARNESS_CREDENTIALS_FILE for this check")
    parser.add_argument("--credentials-only", action="store_true", help="only check credential loading, do not call Yunxiao")
    parser.add_argument("--include-comments", action="store_true", help="also read comment metadata and optional idempotency marker matches")
    parser.add_argument("--comment-marker", default="", help="HIS Harness idempotency marker to find in Yunxiao comments")
    args = parser.parse_args()

    if args.credentials_file:
        os.environ["HARNESS_CREDENTIALS_FILE"] = args.credentials_file

    output_dir = Path(args.output_dir).expanduser()
    result = run_check(
        urls=args.url or DEFAULT_URLS,
        credentials_only=args.credentials_only,
        output_dir=output_dir,
        include_comments=args.include_comments,
        comment_marker=args.comment_marker,
    )
    write_outputs(output_dir, result)

    print(f"Yunxiao read check status: {result['status']}")
    print(f"Report: {output_dir / 'yunxiao_read_check_report.md'}")
    print(f"JSON: {output_dir / 'yunxiao_read_check_result.json'}")
    # A readable ticket with only expired inline media is a usable requirement
    # intake.  Keep a non-zero exit for a true source-read failure, not for a
    # warning that the normal workflow can safely carry forward.
    raise SystemExit(0 if result["status"] in {"passed", "passed_with_warnings"} else 1)


def run_check(
    *,
    urls: list[str],
    credentials_only: bool = False,
    output_dir: Path | None = None,
    include_comments: bool = False,
    comment_marker: str = "",
) -> dict:
    read_credentials = load_yunxiao_credentials()
    write_credentials = load_yunxiao_write_credentials()
    permission_issue = credentials_file_permission_issue()
    result: dict[str, Any] = {
        "status": "running",
        "mode": "readonly",
        "credentials_file": str(credentials_file_path()),
        "credentials_file_exists": credentials_file_path().exists(),
        "credentials_file_permission_issue": permission_issue,
        "read_credentials": read_credentials.safe_summary(),
        "write_credentials": write_credentials.safe_summary(),
        "credentials_only": credentials_only,
        "include_comments": include_comments,
        "comment_marker": comment_marker,
        "items": [],
        "summary": "",
    }

    if not read_credentials.ok:
        result["status"] = "failed"
        result["summary"] = "缺少云效只读凭证：" + "、".join(read_credentials.missing_keys)
        return result

    if credentials_only:
        result["status"] = "passed"
        result["summary"] = "云效只读凭证可识别；未执行云效读取。"
        return result

    hard_failure = False
    warning_count = 0
    for url in urls:
        evidence = collect_yunxiao_evidence(
            yunxiao_url=url,
            demand_text=url,
            output_dir=(output_dir / "downloads") if output_dir else None,
        )
        item = summarize_evidence(evidence)
        if include_comments:
            item["comments"] = read_comment_summary(
                url=url,
                work_item_id=item.get("work_item_id") or "",
                marker=comment_marker,
                write_credentials=write_credentials,
            )
        result["items"].append(item)
        if item["status"] == "partial":
            warning_count += 1
        elif item["status"] != "success":
            hard_failure = True
        if include_comments and (item.get("comments") or {}).get("status") != "success":
            hard_failure = True

    result["status"] = (
        "failed"
        if hard_failure
        else "passed_with_warnings"
        if warning_count
        else "passed"
    )
    result["summary"] = (
        "至少一个云效工作项正文读取失败。"
        if hard_failure
        else "云效主需求可读；部分内联媒体不可用，已按警告继续。"
        if warning_count
        else "云效只读 smoke 验证通过。"
    )
    return result


def read_comment_summary(*, url: str, work_item_id: str, marker: str, write_credentials: Any) -> dict:
    entity_id = work_item_id or parse_work_item_id(url)
    if not entity_id:
        return {"status": "failed", "comment_count": 0, "marker": marker, "marker_found": False, "marker_comment_id": "", "error": "无法解析云效工作项 ID"}
    if not write_credentials.ok:
        return {
            "status": "failed",
            "comment_count": 0,
            "marker": marker,
            "marker_found": False,
            "marker_comment_id": "",
            "error": "缺少可用于读取评论的云效凭证：" + "、".join(write_credentials.missing_keys),
        }
    client = YunxiaoWriteClient(credentials=write_credentials)
    response = client.list_comments(entity=YunxiaoEntityRef(kind=infer_entity_kind(url), entity_id=entity_id))
    if not response.get("ok"):
        return {
            "status": "failed",
            "comment_count": 0,
            "marker": marker,
            "marker_found": False,
            "marker_comment_id": "",
            "attempts": response.get("attempts") or [],
            "error": redact_secrets(str(response.get("error") or "读取评论失败")),
        }
    found = find_marker_comment(response.get("data"), marker) if marker else {}
    return {
        "status": "success",
        "comment_count": count_comment_nodes(response.get("data")),
        "marker": marker,
        "marker_found": bool(found),
        "marker_comment_id": str(found.get("id") or ""),
        "attempts": response.get("attempts") or [],
        "error": "",
    }


def infer_entity_kind(url: str) -> str:
    if "/bug/" in url:
        return "bug"
    if "/req/" in url:
        return "requirement"
    return "task"


def count_comment_nodes(data: object) -> int:
    if isinstance(data, list):
        return sum(count_comment_nodes(item) for item in data)
    if isinstance(data, dict):
        has_comment_text = any(isinstance(data.get(key), str) and data.get(key) for key in ["content", "comment", "body", "text", "description"])
        has_comment_id = any(data.get(key) for key in ["id", "identifier", "commentId", "commentIdentifier"])
        nested_total = sum(count_comment_nodes(value) for value in data.values())
        return (1 if has_comment_text and has_comment_id else 0) + nested_total
    return 0


def summarize_evidence(evidence: dict) -> dict:
    work_item = evidence.get("work_item") if isinstance(evidence.get("work_item"), dict) else {}
    attachments = evidence.get("attachments") if isinstance(evidence.get("attachments"), list) else []
    attempts = evidence.get("request_attempts") if isinstance(evidence.get("request_attempts"), list) else []
    gate = evidence.get("decision_gate") if isinstance(evidence.get("decision_gate"), dict) else {}
    warnings = evidence.get("warnings") if isinstance(evidence.get("warnings"), list) else []
    return {
        "url": evidence.get("yunxiao_url") or "",
        "work_item_id": evidence.get("work_item_id") or "",
        "status": evidence.get("status") or "failed",
        "title": first_text(work_item, ["title", "subject", "name", "summary"]),
        "work_item_status": first_text(work_item, ["status", "statusName", "state", "stateName"]),
        "assignee": first_text(work_item, ["assignee", "assignedTo", "owner", "ownerName"]),
        "attachment_count": len(attachments),
        "clean_text": str(evidence.get("clean_text") or ""),
        "html_excerpt": str(evidence.get("html_excerpt") or ""),
        "inline_files": evidence.get("inline_files") or [],
        "file_details": evidence.get("file_details") or [],
        "inline_file_downloads": evidence.get("inline_file_downloads") or [],
        "analysis_gate": str(gate.get("state") or ""),
        "analysis_gate_reason": redact_secrets(str(gate.get("reason") or "")),
        "warnings": [redact_secrets(str(item)) for item in warnings if str(item).strip()][:20],
        "text_excerpt": str(evidence.get("text_excerpt") or "")[:1200],
        "attempts": attempts,
        "error": redact_secrets(str(evidence.get("error") or "")),
    }


def first_text(value: Any, keys: list[str]) -> str:
    if isinstance(value, dict):
        for key in keys:
            found = value.get(key)
            text = stringify_text(found)
            if text:
                return text
        for nested in value.values():
            text = first_text(nested, keys)
            if text:
                return text
    if isinstance(value, list):
        for item in value[:20]:
            text = first_text(item, keys)
            if text:
                return text
    return ""


def stringify_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ["name", "displayName", "value", "label", "title"]:
            text = stringify_text(value.get(key))
            if text:
                return text
    return ""


def write_outputs(output_dir: Path, result: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "yunxiao_read_check_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "yunxiao_read_check_report.md").write_text(build_report(result), encoding="utf-8")


def build_report(result: dict) -> str:
    lines = [
        "# 云效只读凭证与 Smoke 验证报告",
        "",
        f"- 状态：{result.get('status')}",
        f"- 总结：{result.get('summary') or '-'}",
        f"- 凭证文件：`{result.get('credentials_file')}`",
        f"- 凭证文件存在：{'是' if result.get('credentials_file_exists') else '否'}",
        f"- 权限提示：{result.get('credentials_file_permission_issue') or '-'}",
        "",
        "## 凭证摘要",
        "",
        f"- 只读 token：{(result.get('read_credentials') or {}).get('pat')}",
        f"- 只读 token 来源：{(result.get('read_credentials') or {}).get('pat_source') or '-'}",
        f"- 写 token：{(result.get('write_credentials') or {}).get('write_token')}",
        f"- 写 token 来源：{(result.get('write_credentials') or {}).get('write_token_source') or '-'}",
        f"- 写 token 类型：{(result.get('write_credentials') or {}).get('write_token_kind') or '-'}",
        f"- 组织 ID：{(result.get('read_credentials') or {}).get('organization_id')}",
        f"- 组织 ID 来源：{(result.get('read_credentials') or {}).get('organization_source') or '-'}",
        f"- 项目 ID：{(result.get('read_credentials') or {}).get('project_id')}",
        f"- 项目 ID 来源：{(result.get('read_credentials') or {}).get('project_source') or '-'}",
        "",
        "## 只读工作项",
        "",
    ]
    items = result.get("items") or []
    if not items:
        lines.append("- 未执行云效读取。")
    for item in items:
        lines.extend(
            [
                f"### {item.get('work_item_id') or item.get('url')}",
                "",
                f"- 状态：{item.get('status')}",
                f"- 标题：{item.get('title') or '-'}",
                f"- 云效状态：{item.get('work_item_status') or '-'}",
                f"- 负责人：{item.get('assignee') or '-'}",
                f"- 附件数：{item.get('attachment_count')}",
                f"- 内联图片/文件数：{len(item.get('inline_files') or [])}",
                f"- 内联下载数：{len(item.get('inline_file_downloads') or [])}",
                f"- 分析闸口：{item.get('analysis_gate') or '-'}",
                f"- 警告：{'、'.join(item.get('warnings') or []) or '-'}",
                f"- 错误：{item.get('error') or '-'}",
                "",
                "#### 清洗后的需求正文",
                "",
                (item.get("clean_text") or item.get("text_excerpt") or "-")[:2000],
                "",
                "#### 内联图片/文件",
                "",
            ]
        )
        inline_files = item.get("inline_files") or []
        if not inline_files:
            lines.append("- 无。")
        for file_ref in inline_files[:20]:
            lines.append(
                f"- {file_ref.get('kind') or 'file'}：fileIdentifier={file_ref.get('identifier') or '-'}，"
                f"name={file_ref.get('name') or '-'}"
            )
        downloads = item.get("inline_file_downloads") or []
        lines.extend(["", "#### 内联下载摘要", ""])
        if not downloads:
            lines.append("- 无。")
        for download in downloads[:20]:
            lines.append(
                f"- {download.get('status')}：fileIdentifier={download.get('identifier') or '-'}，"
                f"size={download.get('size') or '-'}，content_type={download.get('content_type') or '-'}，"
                f"path={download.get('path') or '-'}，error={download.get('error') or '-'}"
            )
        comments = item.get("comments") or {}
        if comments:
            lines.extend(["", "#### 评论幂等检查", ""])
            lines.extend(
                [
                    f"- 状态：{comments.get('status')}",
                    f"- 评论数量：{comments.get('comment_count')}",
                    f"- 幂等标记：{comments.get('marker') or '-'}",
                    f"- 是否命中：{'是' if comments.get('marker_found') else '否'}",
                    f"- 命中评论 ID：{comments.get('marker_comment_id') or '-'}",
                    f"- 错误：{comments.get('error') or '-'}",
                ]
            )
        lines.extend(
            [
                "",
                "#### 接口尝试",
                "",
            ]
        )
        attempts = item.get("attempts") or []
        if not attempts:
            lines.append("- 无。")
        for attempt in attempts:
            lines.append(
                f"- {attempt.get('label')}: {attempt.get('status')} HTTP {attempt.get('http_status') or '-'} "
                f"{attempt.get('error') or ''}"
            )
        lines.append("")
    lines.extend(
        [
            "## 边界",
            "",
            "- 本工具只读取云效工作项、附件列表和描述内文件信息。",
            "- 使用 `--include-comments` 时会读取评论元信息和幂等标记命中情况，但不输出完整评论正文。",
            "- 不写评论、不流转状态、不改负责人、不上传附件、不关闭任务。",
            "- 报告只显示 token 是否存在和来源，不显示 token 原文。",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
