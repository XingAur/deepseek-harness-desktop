from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.yunxiao_read import (
    LEGACY_YUNXIAO_READ_COMPATIBILITY_ONLY,
    YunxiaoCredentialBundle,
    collect_yunxiao_evidence,
    extract_inline_file_refs,
    normalize_comment_list,
)


class YunxiaoCommentEvidenceTests(unittest.TestCase):
    def test_legacy_read_helpers_are_explicitly_compatibility_only(self) -> None:
        self.assertTrue(LEGACY_YUNXIAO_READ_COMPATIBILITY_ONLY)

    def test_normalize_comment_list_keeps_readable_comment_bodies(self) -> None:
        comments = normalize_comment_list(
            {
                "items": [
                    {"id": "1", "content": "前端只需传 sortField、sortOrder", "creator": {"name": "后端开发"}},
                    {"id": "2", "body": "服务端接口已支持 getGuaHaoPageList", "user": "后端开发"},
                ]
            }
        )

        self.assertEqual(2, len(comments))
        self.assertEqual("前端只需传 sortField、sortOrder", comments[0]["content"])
        self.assertEqual("服务端接口已支持 getGuaHaoPageList", comments[1]["content"])

    def test_collect_evidence_skips_comment_request_when_requested(self) -> None:
        class FakeClient:
            def __init__(self, **_kwargs: object) -> None:
                pass

            def get_work_item_info(self, _work_item_id: str) -> dict:
                return {"ok": True, "data": {"description": "需求正文"}, "attempts": []}

            def list_comments(self, _work_item_id: str) -> dict:
                raise AssertionError("include_comments=False 时不应读取评论")

            def list_attachments(self, _work_item_id: str) -> dict:
                return {"ok": True, "data": [], "attempts": []}

            def collect_file_details(self, *_args: object, **_kwargs: object) -> dict:
                return {"items": [], "attempts": []}

        credentials = YunxiaoCredentialBundle(pat="token", organization_id="org")
        with patch("app.yunxiao_read.load_yunxiao_credentials", return_value=credentials), patch(
            "app.yunxiao_read.YunxiaoReadClient", FakeClient
        ):
            evidence = collect_yunxiao_evidence(
                yunxiao_url="https://devops.aliyun.com/projex/req/DFHIS-31558",
                demand_text="DFHIS-31558",
                include_comments=False,
            )

        self.assertEqual("success", evidence["status"])
        self.assertEqual([], evidence["comments"])
        self.assertEqual({"status": "skipped", "error": "", "reason": "user_instruction"}, evidence["comment_read"])

    def test_optional_inline_failure_keeps_readable_work_item_as_partial(self) -> None:
        class FakeClient:
            def __init__(self, **_kwargs: object) -> None:
                pass

            def get_work_item_info(self, _work_item_id: str) -> dict:
                return {
                    "ok": True,
                    "data": {"id": "DFHIS-32010", "title": "可读需求", "description": {"htmlValue": "<p>正文</p>"}},
                    "attempts": [],
                }

            def list_comments(self, _work_item_id: str) -> dict:
                return {"ok": False, "error": "comments unavailable", "attempts": []}

            def list_attachments(self, _work_item_id: str) -> dict:
                return {"ok": True, "data": [{"fileIdentifier": "stale-inline"}], "attempts": []}

            def collect_file_details(self, *_args: object, **_kwargs: object) -> dict:
                return {
                    "items": [{"identifier": "stale-inline", "status": "failed", "error": "404"}],
                    "attempts": [],
                }

        credentials = YunxiaoCredentialBundle(pat="token", organization_id="org")
        with patch("app.yunxiao_read.load_yunxiao_credentials", return_value=credentials), patch(
            "app.yunxiao_read.YunxiaoReadClient", FakeClient
        ):
            evidence = collect_yunxiao_evidence(
                yunxiao_url="https://devops.aliyun.com/projex/req/DFHIS-32010",
                demand_text="DFHIS-32010",
            )

        self.assertEqual("partial", evidence["status"])
        self.assertEqual("needs_requirement_confirmation", evidence["decision_gate"]["state"])
        self.assertIn("inline_file_detail_failed", evidence["warnings"])
        self.assertIn("comments_read_failed", evidence["warnings"])

    def test_expired_inline_image_continues_analysis_without_confirmation(self) -> None:
        class FakeClient:
            def __init__(self, **_kwargs: object) -> None:
                pass

            def get_work_item_info(self, _work_item_id: str) -> dict:
                return {
                    "ok": True,
                    "data": {
                        "id": "DFHIS-32010",
                        "title": "可读需求",
                        "description": {
                            "htmlValue": "<p>正文规则</p><img src=\"https://example.test/file?fileIdentifier=expired-image\">"
                        },
                    },
                    "attempts": [],
                }

            def list_comments(self, _work_item_id: str) -> dict:
                return {"ok": True, "data": [], "attempts": []}

            def list_attachments(self, _work_item_id: str) -> dict:
                return {"ok": True, "data": [], "attempts": []}

            def collect_file_details(self, *_args: object, **_kwargs: object) -> dict:
                return {
                    "items": [
                        {
                            "identifier": "expired-image",
                            "kind": "inline_image",
                            "status": "failed",
                            "error": "404",
                        }
                    ],
                    "attempts": [],
                }

        credentials = YunxiaoCredentialBundle(pat="token", organization_id="org")
        with patch("app.yunxiao_read.load_yunxiao_credentials", return_value=credentials), patch(
            "app.yunxiao_read.YunxiaoReadClient", FakeClient
        ):
            evidence = collect_yunxiao_evidence(
                yunxiao_url="https://devops.aliyun.com/projex/req/DFHIS-32010",
                demand_text="DFHIS-32010",
            )

        self.assertEqual("partial", evidence["status"])
        self.assertEqual(
            "ready_for_analysis_with_warnings",
            evidence["decision_gate"]["state"],
        )
        self.assertIn("inline_image_detail_failed", evidence["warnings"])

    def test_jsonml_inline_image_keeps_metadata_without_using_editor_node_id_as_file_id(self) -> None:
        rich_text = {
            "htmlValue": '<p>相关截图：</p><img src="https://devops.aliyun.com/projex/api/workitem/file/url">',
            "jsonMLValue": [
                "article",
                [
                    "p",
                    [
                        "img",
                        {
                            "id": "sm6upg",
                            "name": "433b9c0de9103e954268dc4a828b197c.jpeg",
                            "size": 186859,
                            "src": "https://devops.aliyun.com/projex/api/workitem/file/url",
                        },
                    ],
                ],
            ],
        }

        refs = extract_inline_file_refs(json.dumps(rich_text, ensure_ascii=False))

        self.assertEqual(1, len(refs))
        self.assertEqual("", refs[0]["identifier"])
        self.assertEqual("sm6upg", refs[0]["source_node_id"])
        self.assertEqual("433b9c0de9103e954268dc4a828b197c.jpeg", refs[0]["name"])
        self.assertEqual(186859, refs[0]["size"])
        self.assertEqual("inline_image", refs[0]["kind"])

    def test_archive_policy_removes_legacy_file_count_limits_only_when_explicit(self) -> None:
        captured: dict[str, object] = {}

        class FakeClient:
            def __init__(self, **_kwargs: object) -> None:
                pass

            def get_work_item_info(self, _work_item_id: str) -> dict:
                return {"ok": True, "data": {"description": "需求正文"}, "attempts": []}

            def list_comments(self, _work_item_id: str) -> dict:
                return {"ok": True, "data": [], "attempts": []}

            def list_attachments(self, _work_item_id: str) -> dict:
                return {
                    "ok": True,
                    "data": [{"fileIdentifier": f"file-{index}", "fileName": f"{index}.txt"} for index in range(41)],
                    "attempts": [],
                }

            def collect_file_details(self, _work_item_id: str, files: list[dict], **kwargs: object) -> dict:
                captured["files"] = files
                captured["max_files"] = kwargs.get("max_files")
                return {"items": [], "attempts": []}

        credentials = YunxiaoCredentialBundle(pat="token", organization_id="org")
        with patch("app.yunxiao_read.load_yunxiao_credentials", return_value=credentials), patch(
            "app.yunxiao_read.YunxiaoReadClient", FakeClient
        ):
            evidence = collect_yunxiao_evidence(
                yunxiao_url="https://devops.aliyun.com/projex/req/DFHIS-32011",
                demand_text="DFHIS-32011",
                download_policy="archive",
            )

        self.assertEqual("archive", evidence["download_policy"])
        self.assertEqual(41, len(captured["files"]))
        self.assertIsNone(captured["max_files"])


if __name__ == "__main__":
    unittest.main()
