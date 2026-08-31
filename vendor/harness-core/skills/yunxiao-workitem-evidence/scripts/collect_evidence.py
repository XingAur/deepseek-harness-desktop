#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from yunxiao_evidence import (
    DEFAULT_BASE_URL,
    DEFAULT_MAX_DOWNLOAD_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    YunxiaoClient,
    collect_evidence,
    load_credentials,
    redact_for_output,
    write_outputs,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只读收集云效当前工作项、原始父级、关系、评论和附件证据。",
    )
    parser.add_argument("source", help="云效工作项 URL、编号或内部 ID")
    parser.add_argument("--output-dir", required=True, help="证据和附件输出目录")
    parser.add_argument(
        "--credentials-file",
        default="",
        help="可选凭证 JSON；默认读取 YUNXIAO_CREDENTIALS_FILE 或本机既有凭证文件",
    )
    parser.add_argument(
        "--credential-kind",
        choices=("read", "write"),
        default="read",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("YUNXIAO_API_BASE_URL") or DEFAULT_BASE_URL,
        help="云效 OpenAPI 服务接入点；仅允许官方 HTTPS 主机",
    )
    parser.add_argument(
        "--no-download-files",
        action="store_true",
        help="只保存附件元数据，不下载附件和正文内联图片",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="单次只读请求超时秒数",
    )
    parser.add_argument(
        "--max-download-bytes",
        type=int,
        default=DEFAULT_MAX_DOWNLOAD_BYTES,
        help="单个附件最大下载字节数",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    credentials = load_credentials(
        credentials_file=Path(args.credentials_file) if args.credentials_file else None,
        credential_kind=args.credential_kind,
    )
    if credentials["missing_keys"]:
        print(
            json.dumps(
                {
                    "status": "fetch_failed",
                    "error": "缺少云效只读凭证。",
                    "missing_keys": credentials["missing_keys"],
                    "credential_summary": credentials["safe_summary"],
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1

    try:
        client = YunxiaoClient(
            token=credentials["token"],
            organization_id=credentials["organization_id"],
            base_url=args.base_url,
            timeout_seconds=max(1, args.timeout),
            max_download_bytes=max(1, args.max_download_bytes),
        )
        evidence = collect_evidence(
            source=args.source,
            client=client,
            output_dir=args.output_dir,
            download_files=not args.no_download_files,
            secrets=[credentials["token"]],
        )
        outputs = write_outputs(evidence=evidence, output_dir=args.output_dir)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "fetch_failed",
                    "error": redact_for_output(exc, [credentials["token"]]),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1

    state = evidence["decision_gate"]["state"]
    print(
        json.dumps(
            {
                "status": state,
                "completeness": evidence["completeness"]["status"],
                "work_item_count": len(evidence["work_items"]),
                "relation_count": len(evidence["relations"]),
                "outputs": outputs,
            },
            ensure_ascii=False,
        )
    )
    if state == "ready_for_analysis":
        return 0
    if state == "needs_requirement_confirmation":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
