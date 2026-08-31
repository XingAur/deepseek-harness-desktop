from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from yunxiao_evidence import (  # noqa: E402
    CONTRACT_VERSION,
    SafeApiRedirectHandler,
    YunxiaoClient,
    collect_evidence,
    load_credentials,
    parse_work_item_id,
    render_markdown,
    validate_evidence,
    write_outputs,
)


def ok(data):
    return {"ok": True, "http_status": 200, "data": data, "error": ""}


def failed(status, message):
    return {"ok": False, "http_status": status, "data": None, "error": message}


def rehash(evidence):
    payload = json.loads(json.dumps(evidence, ensure_ascii=False))
    payload.pop("integrity", None)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    evidence["integrity"]["evidence_sha256"] = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def _result(self, operation, work_item_id="", relation_type="", url=""):
        key = (operation, work_item_id, relation_type or url)
        self.calls.append(key)
        if key not in self.responses:
            raise AssertionError(f"Unexpected client call: {key}")
        return self.responses[key]

    def get_work_item(self, work_item_id):
        return self._result("get_work_item", work_item_id)

    def list_comments(self, work_item_id):
        return self._result("list_comments", work_item_id)

    def list_attachments(self, work_item_id):
        return self._result("list_attachments", work_item_id)

    def list_relations(self, work_item_id, relation_type):
        return self._result("list_relations", work_item_id, relation_type)

    def get_workitem_file(self, work_item_id, file_identifier):
        return self._result("get_workitem_file", work_item_id, file_identifier)

    def download_file(self, url):
        return self._result("download_file", work_item_id=url)


class FakeHttpResponse:
    def __init__(self, body, *, status=200, content_type="application/json"):
        self.body = body
        self.status = status
        self.headers = {"content-type": content_type}

    def read(self, _limit=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False


class YunxiaoEvidenceTests(unittest.TestCase):
    def test_collects_parent_chain_relations_comments_and_downloaded_files(self):
        responses = {
            ("get_work_item", "DFHIS-90002", ""): ok(
                {
                    "id": "child-2",
                    "serialNumber": "DFHIS-90002",
                    "title": "门诊结算页面调整",
                    "description": "按原始需求实现，见父需求附件。",
                    "formatType": "RICHTEXT",
                    "parentId": "root-1",
                    "idPath": "root-1,child-2",
                    "categoryId": "Req",
                }
            ),
            ("list_comments", "child-2", ""): ok([]),
            ("list_attachments", "child-2", ""): ok([]),
            ("list_relations", "child-2", "PARENT"): ok(
                [
                    {
                        "id": "rel-parent",
                        "relationType": "PARENT",
                        "resourceType": "WORKITEM",
                        "resourceId": "root-1",
                    }
                ]
            ),
            ("list_relations", "child-2", "SUB"): ok([]),
            ("list_relations", "child-2", "ASSOCIATED"): ok(
                [
                    {
                        "id": "rel-associated",
                        "relationType": "ASSOCIATED",
                        "resourceType": "WORKITEM",
                        "resourceId": "bug-3",
                    }
                ]
            ),
            ("list_relations", "child-2", "DEPEND_ON"): ok([]),
            ("list_relations", "child-2", "DEPENDED_BY"): ok([]),
            ("get_work_item", "root-1", ""): ok(
                {
                    "id": "root-1",
                    "serialNumber": "DFHIS-90001",
                    "title": "门诊结算规则原始需求",
                    "description": '<p>类型 B 保持原逻辑。</p><img src="https://files.example/inline.png">',
                    "formatType": "RICHTEXT",
                    "categoryId": "Req",
                }
            ),
            ("list_comments", "root-1", ""): ok(
                [
                    {
                        "id": "comment-1",
                        "content": "移动医保不启用该按钮。",
                        "contentFormat": "RICHTEXT",
                        "gmtCreate": "2026-07-20T10:00:00+08:00",
                        "user": {"id": "user-1", "name": "产品经理"},
                    }
                ]
            ),
            ("list_attachments", "root-1", ""): ok(
                [
                    {
                        "id": "attachment-1",
                        "fileId": "file-1",
                        "fileName": "../原始交互规则.png",
                        "size": 5,
                        "suffix": ".png",
                        "url": "https://files.example/original.png?signature=secret",
                    }
                ]
            ),
            ("list_relations", "root-1", "PARENT"): ok([]),
            ("get_work_item", "bug-3", ""): ok(
                {
                    "id": "bug-3",
                    "serialNumber": "DFHIS-89999",
                    "title": "历史兼容缺陷",
                    "description": "类型 B 禁止再次显示按钮。",
                    "categoryId": "Bug",
                }
            ),
            ("list_comments", "bug-3", ""): ok([]),
            ("list_attachments", "bug-3", ""): ok([]),
            ("download_file", "https://files.example/original.png?signature=secret", ""): ok(
                {"content": b"image", "content_type": "image/png"}
            ),
            ("download_file", "https://files.example/inline.png", ""): ok(
                {"content": b"inline-image", "content_type": "image/png"}
            ),
        }
        client = FakeClient(responses)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir).resolve()
            evidence = collect_evidence(
                source="https://devops.aliyun.com/projex/req/DFHIS-90002",
                client=client,
                output_dir=output_dir,
                download_files=True,
                fetched_at="2026-07-24T14:30:00+08:00",
            )

            self.assertEqual(CONTRACT_VERSION, evidence["contract_version"])
            self.assertEqual("readonly", evidence["mode"])
            self.assertEqual("DFHIS-90002", evidence["source"]["requested_id"])
            self.assertEqual(["root-1", "child-2"], evidence["lineage"])
            self.assertEqual("root-1", evidence["root_work_item_id"])
            self.assertEqual("ready_for_analysis", evidence["decision_gate"]["state"])
            self.assertEqual("complete", evidence["completeness"]["status"])

            by_id = {item["id"]: item for item in evidence["work_items"]}
            self.assertEqual({"child-2", "root-1", "bug-3"}, set(by_id))
            self.assertEqual("移动医保不启用该按钮。", by_id["root-1"]["comments"][0]["content"])
            self.assertIn("类型 B 保持原逻辑", by_id["root-1"]["description"]["text"])

            attachment = by_id["root-1"]["attachments"][0]
            self.assertEqual("原始交互规则.png", attachment["name"])
            self.assertEqual(hashlib.sha256(b"image").hexdigest(), attachment["sha256"])
            self.assertFalse(Path(attachment["local_path"]).is_absolute())
            self.assertTrue((output_dir / attachment["local_path"]).is_file())
            self.assertNotIn("signature=secret", json.dumps(evidence, ensure_ascii=False))

            inline_file = by_id["root-1"]["inline_files"][0]
            self.assertEqual(hashlib.sha256(b"inline-image").hexdigest(), inline_file["sha256"])
            self.assertFalse(Path(inline_file["local_path"]).is_absolute())
            self.assertTrue((output_dir / inline_file["local_path"]).is_file())

            associated = [
                edge
                for edge in evidence["relations"]
                if edge["type"] == "ASSOCIATED"
            ]
            self.assertEqual("bug-3", associated[0]["to_id"])
            self.assertEqual([], validate_evidence(evidence))
            tampered = json.loads(json.dumps(evidence, ensure_ascii=False))
            tampered["work_items"][0]["title"] = "被篡改的标题"
            self.assertIn(
                "integrity.evidence_sha256 does not match evidence content",
                validate_evidence(tampered),
            )

            absolute_path = json.loads(json.dumps(evidence, ensure_ascii=False))
            next(
                item for item in absolute_path["work_items"] if item["id"] == "root-1"
            )["attachments"][0]["local_path"] = "/tmp/file.bin"
            rehash(absolute_path)
            self.assertIn(
                "downloaded file local_path must be a safe relative path",
                validate_evidence(absolute_path),
            )

            escaping_path = json.loads(json.dumps(evidence, ensure_ascii=False))
            next(
                item for item in escaping_path["work_items"] if item["id"] == "root-1"
            )["attachments"][0]["local_path"] = "../file.bin"
            rehash(escaping_path)
            self.assertIn(
                "downloaded file local_path must be a safe relative path",
                validate_evidence(escaping_path),
            )

            outputs = write_outputs(evidence=evidence, output_dir=output_dir)
            self.assertTrue(Path(outputs["json"]).is_file())
            self.assertTrue(Path(outputs["markdown"]).is_file())
            markdown = render_markdown(evidence)
            self.assertIn("DFHIS-90001", markdown)
            self.assertIn("移动医保不启用该按钮。", markdown)
            self.assertIn("原始交互规则.png", markdown)
            self.assertIn(attachment["sha256"], markdown)
            with self.assertRaises(FileExistsError):
                write_outputs(evidence=evidence, output_dir=output_dir)

            malformed_contract = json.loads(json.dumps(evidence, ensure_ascii=False))
            malformed_contract["source"] = {}
            malformed_contract["decision_gate"]["reasons"] = "not-an-array"
            rehash(malformed_contract)
            contract_errors = validate_evidence(malformed_contract)
            self.assertTrue(any("source" in error for error in contract_errors))
            self.assertTrue(any("reasons" in error for error in contract_errors))

    def test_blocks_when_original_requirement_or_relation_evidence_is_unavailable(self):
        leaked_token = "pt-real-secret-value"
        responses = {
            ("get_work_item", "DFHIS-91000", ""): ok(
                {
                    "id": "bug-10",
                    "serialNumber": "DFHIS-91000",
                    "title": "退费金额异常",
                    "description": "按原需求修复。",
                    "parentId": "req-9",
                    "idPath": "req-9,bug-10",
                    "categoryId": "Bug",
                }
            ),
            ("list_comments", "bug-10", ""): failed(
                403,
                (
                    f"forbidden x-yunxiao-token: {leaked_token}; "
                    "GET https://files.example/a.png?signature=temporary-secret"
                ),
            ),
            ("list_attachments", "bug-10", ""): ok([]),
            ("list_relations", "bug-10", "PARENT"): ok(
                [
                    {
                        "relationType": "PARENT",
                        "resourceType": "WORKITEM",
                        "resourceId": "req-9",
                    }
                ]
            ),
            ("list_relations", "bug-10", "SUB"): ok([]),
            ("list_relations", "bug-10", "ASSOCIATED"): failed(
                500, f"server error; Authorization: Bearer {leaked_token}"
            ),
            ("list_relations", "bug-10", "DEPEND_ON"): ok([]),
            ("list_relations", "bug-10", "DEPENDED_BY"): ok([]),
            ("get_work_item", "req-9", ""): failed(403, f"permission denied {leaked_token}"),
        }
        client = FakeClient(responses)
        evidence = collect_evidence(
            source=(
                "https://devops.aliyun.com/projex/bug/DFHIS-91000"
                f"?access_token={leaked_token}"
            ),
            client=client,
            secrets=[leaked_token],
            fetched_at="2026-07-24T15:00:00+08:00",
        )

        serialized = json.dumps(evidence, ensure_ascii=False)
        self.assertNotIn(leaked_token, serialized)
        self.assertNotIn("temporary-secret", serialized)
        self.assertIn("[REDACTED]", serialized)
        self.assertNotIn("access_token", evidence["source"]["input"])
        self.assertEqual("needs_requirement_confirmation", evidence["decision_gate"]["state"])
        self.assertEqual("partial", evidence["completeness"]["status"])
        self.assertIn("parent_work_item_unavailable", {item["code"] for item in evidence["errors"]})
        self.assertIn("relation_read_failed", {item["code"] for item in evidence["warnings"]})
        self.assertEqual(1, client.calls.count(("get_work_item", "req-9", "")))
        self.assertEqual([], validate_evidence(evidence))

    def test_rejects_parent_relation_conflict_and_cycles(self):
        responses = {
            ("get_work_item", "DFHIS-92000", ""): ok(
                {
                    "id": "child",
                    "serialNumber": "DFHIS-92000",
                    "title": "存在冲突的父关系",
                    "parentId": "parent-a",
                    "idPath": "parent-a,child",
                }
            ),
            ("list_comments", "child", ""): ok([]),
            ("list_attachments", "child", ""): ok([]),
            ("list_relations", "child", "PARENT"): ok(
                [
                    {
                        "relationType": "PARENT",
                        "resourceType": "WORKITEM",
                        "resourceId": "parent-b",
                    }
                ]
            ),
            ("list_relations", "child", "SUB"): ok([]),
            ("list_relations", "child", "ASSOCIATED"): ok([]),
            ("list_relations", "child", "DEPEND_ON"): ok([]),
            ("list_relations", "child", "DEPENDED_BY"): ok([]),
            ("get_work_item", "parent-a", ""): ok(
                {
                    "id": "parent-a",
                    "serialNumber": "DFHIS-91999",
                    "title": "父需求 A",
                    "parentId": "child",
                    "idPath": "parent-a,child",
                }
            ),
            ("list_comments", "parent-a", ""): ok([]),
            ("list_attachments", "parent-a", ""): ok([]),
            ("list_relations", "parent-a", "PARENT"): ok(
                [
                    {
                        "relationType": "PARENT",
                        "resourceType": "WORKITEM",
                        "resourceId": "child",
                    }
                ]
            ),
            ("get_work_item", "parent-b", ""): ok(
                {
                    "id": "parent-b",
                    "serialNumber": "DFHIS-91998",
                    "title": "父需求 B",
                }
            ),
            ("list_comments", "parent-b", ""): ok([]),
            ("list_attachments", "parent-b", ""): ok([]),
        }
        evidence = collect_evidence(
            source="DFHIS-92000",
            client=FakeClient(responses),
            fetched_at="2026-07-24T15:10:00+08:00",
        )

        warning_codes = {item["code"] for item in evidence["warnings"]}
        self.assertIn("parent_relation_conflict", warning_codes)
        self.assertIn("parent_cycle_detected", warning_codes)
        self.assertEqual("needs_requirement_confirmation", evidence["decision_gate"]["state"])

    def test_blocks_when_multiple_parent_relations_are_declared(self):
        responses = {
            ("get_work_item", "DFHIS-92500", ""): ok(
                {
                    "id": "child-25",
                    "serialNumber": "DFHIS-92500",
                    "title": "多个父需求",
                    "parentId": "parent-1",
                    "idPath": "parent-1,child-25",
                }
            ),
            ("list_comments", "child-25", ""): ok([]),
            ("list_attachments", "child-25", ""): ok([]),
            ("list_relations", "child-25", "PARENT"): ok(
                [
                    {"relationType": "PARENT", "resourceType": "WORKITEM", "resourceId": "parent-1"},
                    {"relationType": "PARENT", "resourceType": "WORKITEM", "resourceId": "parent-2"},
                ]
            ),
            ("list_relations", "child-25", "SUB"): ok([]),
            ("list_relations", "child-25", "ASSOCIATED"): ok([]),
            ("list_relations", "child-25", "DEPEND_ON"): ok([]),
            ("list_relations", "child-25", "DEPENDED_BY"): ok([]),
            ("get_work_item", "parent-1", ""): ok(
                {"id": "parent-1", "serialNumber": "DFHIS-92499", "title": "父需求一"}
            ),
            ("list_comments", "parent-1", ""): ok([]),
            ("list_attachments", "parent-1", ""): ok([]),
            ("list_relations", "parent-1", "PARENT"): ok([]),
            ("get_work_item", "parent-2", ""): ok(
                {"id": "parent-2", "serialNumber": "DFHIS-92498", "title": "父需求二"}
            ),
            ("list_comments", "parent-2", ""): ok([]),
            ("list_attachments", "parent-2", ""): ok([]),
        }

        evidence = collect_evidence(
            source="DFHIS-92500",
            client=FakeClient(responses),
            fetched_at="2026-07-24T15:15:00+08:00",
        )

        self.assertEqual("needs_requirement_confirmation", evidence["decision_gate"]["state"])
        self.assertIn(
            "multiple_parent_candidates",
            {item["code"] for item in evidence["warnings"]},
        )

    def test_parse_work_item_id_supports_links_and_plain_ids(self):
        self.assertEqual(
            "DFHIS-31680",
            parse_work_item_id("https://devops.aliyun.com/projex/req/DFHIS-31680#"),
        )
        self.assertEqual("abc123", parse_work_item_id("https://example.test/workitems/abc123"))
        self.assertEqual("DFHIS-1", parse_work_item_id("DFHIS-1"))
        self.assertEqual("", parse_work_item_id("https://devops.aliyun.com/projex/req/"))

    def test_http_client_uses_official_readonly_endpoints_and_does_not_leak_token_to_files(self):
        requests = []

        def opener(request, *, timeout):
            requests.append((request, timeout))
            if request.full_url.startswith("https://files.example/"):
                return FakeHttpResponse(b"file-bytes", content_type="image/png")
            return FakeHttpResponse(b"[]")

        client = YunxiaoClient(
            token="pt-test-secret",
            organization_id="org-1",
            opener=opener,
            timeout_seconds=9,
        )
        self.assertTrue(client.get_work_item("item-1")["ok"])
        self.assertTrue(client.list_comments("item-1")["ok"])
        self.assertTrue(client.list_attachments("item-1")["ok"])
        self.assertTrue(client.list_relations("item-1", "DEPEND_ON")["ok"])
        self.assertTrue(client.get_workitem_file("item-1", "file-1")["ok"])
        downloaded = client.download_file("https://files.example/a.png?signature=abc")
        self.assertTrue(downloaded["ok"])
        self.assertEqual(b"file-bytes", downloaded["data"]["content"])

        api_requests = [request for request, _ in requests[:-1]]
        file_request = requests[-1][0]
        self.assertTrue(all(request.get_method() == "GET" for request in api_requests))
        self.assertTrue(
            api_requests[0].full_url.endswith(
                "/oapi/v1/projex/organizations/org-1/workitems/item-1"
            )
        )
        self.assertIn("/workitems/item-1/comments", api_requests[1].full_url)
        self.assertIn("/workitems/item-1/attachments", api_requests[2].full_url)
        self.assertIn("relationRecords?relationType=DEPEND_ON", api_requests[3].full_url)
        self.assertIn("/workitems/item-1/files/file-1", api_requests[4].full_url)
        self.assertTrue(
            all(request.headers.get("X-yunxiao-token") == "pt-test-secret" for request in api_requests)
        )
        self.assertEqual("GET", file_request.get_method())
        self.assertIsNone(file_request.headers.get("X-yunxiao-token"))
        self.assertTrue(all(timeout == 9 for _, timeout in requests))

    def test_http_client_rejects_insecure_download_and_enforces_size_limit(self):
        client = YunxiaoClient(
            token="pt-test-secret",
            organization_id="org-1",
            opener=lambda _request, *, timeout: FakeHttpResponse(
                b"123456", content_type="application/octet-stream"
            ),
            max_download_bytes=5,
        )
        insecure = client.download_file("http://files.example/a.bin")
        oversized = client.download_file("https://files.example/a.bin")

        self.assertFalse(insecure["ok"])
        self.assertIn("HTTPS", insecure["error"])
        self.assertFalse(oversized["ok"])
        self.assertIn("超过", oversized["error"])

    def test_http_client_rejects_untrusted_api_origins_and_api_redirects(self):
        for base_url in (
            "http://openapi-rdc.aliyuncs.com",
            "https://evil.example",
            "https://user:password@openapi-rdc.aliyuncs.com",
        ):
            with self.subTest(base_url=base_url):
                with self.assertRaises(ValueError):
                    YunxiaoClient(
                        token="pt-test-secret",
                        organization_id="org-1",
                        base_url=base_url,
                    )

        handler = SafeApiRedirectHandler()
        with self.assertRaises(urllib.error.HTTPError) as redirect_error:
            handler.redirect_request(
                urllib.request.Request(
                    "https://openapi-rdc.aliyuncs.com/oapi/v1/projex/test",
                    headers={"x-yunxiao-token": "pt-test-secret"},
                ),
                None,
                302,
                "Found",
                {},
                "https://evil.example/steal",
            )
        # Python 3.9's HTTPError.close cannot close an intentionally absent
        # response stream; newer runtimes need the explicit cleanup.
        if sys.version_info >= (3, 10):
            redirect_error.exception.close()

    def test_http_client_treats_unsuccessful_json_envelope_as_failure(self):
        client = YunxiaoClient(
            token="pt-test-secret",
            organization_id="org-1",
            opener=lambda _request, *, timeout: FakeHttpResponse(
                json.dumps(
                    {
                        "success": False,
                        "errorCode": "Forbidden",
                        "errorMessage": "denied pt-test-secret",
                    }
                ).encode("utf-8")
            ),
        )

        result = client.get_work_item("item-1")

        self.assertFalse(result["ok"])
        self.assertEqual(200, result["http_status"])
        self.assertIn("Forbidden", result["error"])
        self.assertNotIn("pt-test-secret", result["error"])

    def test_load_credentials_prefers_environment_and_never_returns_secret_sources_as_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            credentials_file = Path(temp_dir) / "credentials.json"
            credentials_file.write_text(
                json.dumps(
                    {
                        "aliyun_devops_pat": "file-token",
                        "aliyun_devops_organization_id": "file-org",
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "ALIYUN_DEVOPS_PAT": "env-token",
                    "ALIYUN_DEVOPS_ORGANIZATION_ID": "env-org",
                },
                clear=True,
            ):
                credentials = load_credentials(credentials_file=credentials_file)

        self.assertEqual("env-token", credentials["token"])
        self.assertEqual("env-org", credentials["organization_id"])
        self.assertEqual("env:ALIYUN_DEVOPS_PAT", credentials["token_source"])
        self.assertEqual(
            "env:ALIYUN_DEVOPS_ORGANIZATION_ID",
            credentials["organization_id_source"],
        )
        self.assertNotIn("env-token", credentials["safe_summary"].values())

    def test_load_credentials_can_explicitly_select_write_pat_without_read_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            credentials_file = Path(temp_dir) / "credentials.json"
            credentials_file.write_text(
                json.dumps(
                    {
                        "aliyun_devops_pat": "read-token",
                        "aliyun_devops_write_pat": "write-token",
                        "aliyun_devops_organization_id": "file-org",
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                credentials = load_credentials(
                    credentials_file=credentials_file,
                    credential_kind="write",
                )

            self.assertEqual("write-token", credentials["token"])
            self.assertEqual(
                "file:aliyun_devops_write_pat",
                credentials["token_source"],
            )
            self.assertEqual("write", credentials["credential_kind"])
            self.assertEqual("write", credentials["safe_summary"]["credential_kind"])
            self.assertNotIn("write-token", credentials["safe_summary"].values())

            credentials_file.write_text(
                json.dumps(
                    {
                        "aliyun_devops_pat": "read-token",
                        "aliyun_devops_organization_id": "file-org",
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                missing_write = load_credentials(
                    credentials_file=credentials_file,
                    credential_kind="write",
                )

        self.assertEqual("", missing_write["token"])
        self.assertIn("aliyun_devops_write_pat", missing_write["missing_keys"])

    def test_resolves_inline_file_identifier_before_downloading(self):
        responses = {
            ("get_work_item", "DFHIS-93000", ""): ok(
                {
                    "id": "item-30",
                    "serialNumber": "DFHIS-93000",
                    "title": "正文内联图片",
                    "description": (
                        '<p>规则截图：</p><img src="https://devops.aliyun.com/'
                        'projex/api/workitem/file/url?fileIdentifier=inline-1">'
                    ),
                }
            ),
            ("list_comments", "item-30", ""): ok([]),
            ("list_attachments", "item-30", ""): ok([]),
            ("list_relations", "item-30", "PARENT"): ok([]),
            ("list_relations", "item-30", "SUB"): ok([]),
            ("list_relations", "item-30", "ASSOCIATED"): ok([]),
            ("list_relations", "item-30", "DEPEND_ON"): ok([]),
            ("list_relations", "item-30", "DEPENDED_BY"): ok([]),
            ("get_workitem_file", "item-30", "inline-1"): ok(
                {
                    "id": "inline-1",
                    "name": "规则截图.png",
                    "size": 6,
                    "suffix": ".png",
                    "url": "https://files.example/inline-1.png?signature=temporary",
                }
            ),
            (
                "download_file",
                "https://files.example/inline-1.png?signature=temporary",
                "",
            ): ok({"content": b"inline", "content_type": "image/png"}),
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir).resolve()
            evidence = collect_evidence(
                source="DFHIS-93000",
                client=FakeClient(responses),
                output_dir=output_dir,
                download_files=True,
                fetched_at="2026-07-24T15:20:00+08:00",
            )

        item = evidence["work_items"][0]
        self.assertEqual("ready_for_analysis", evidence["decision_gate"]["state"])
        self.assertEqual("inline-1", item["inline_files"][0]["file_id"])
        self.assertEqual("规则截图.png", item["inline_files"][0]["name"])
        self.assertEqual("success", item["inline_files"][0]["download_status"])
        self.assertNotIn("signature=temporary", json.dumps(evidence, ensure_ascii=False))

    def test_rejects_successful_but_empty_work_item_payload(self):
        evidence = collect_evidence(
            source="DFHIS-94000",
            client=FakeClient(
                {
                    ("get_work_item", "DFHIS-94000", ""): ok({}),
                }
            ),
            fetched_at="2026-07-24T15:30:00+08:00",
        )

        self.assertEqual("fetch_failed", evidence["decision_gate"]["state"])
        self.assertEqual("failed", evidence["completeness"]["status"])
        self.assertEqual(
            ["requested_work_item_invalid"],
            [item["code"] for item in evidence["errors"]],
        )

    def test_treats_malformed_list_responses_as_incomplete(self):
        responses = {
            ("get_work_item", "DFHIS-94500", ""): ok(
                {
                    "id": "item-45",
                    "serialNumber": "DFHIS-94500",
                    "title": "返回结构异常",
                }
            ),
            ("list_comments", "item-45", ""): ok({"unexpected": []}),
            ("list_attachments", "item-45", ""): ok([]),
            ("list_relations", "item-45", "PARENT"): ok([]),
            ("list_relations", "item-45", "SUB"): ok([]),
            ("list_relations", "item-45", "ASSOCIATED"): ok([]),
            ("list_relations", "item-45", "DEPEND_ON"): ok([]),
            ("list_relations", "item-45", "DEPENDED_BY"): ok([]),
        }

        evidence = collect_evidence(
            source="DFHIS-94500",
            client=FakeClient(responses),
            fetched_at="2026-07-24T15:35:00+08:00",
        )

        self.assertEqual("needs_requirement_confirmation", evidence["decision_gate"]["state"])
        self.assertEqual("failed", evidence["work_items"][0]["comments_status"])
        comment_log = next(
            item
            for item in evidence["request_log"]
            if item["operation"] == "list_comments"
        )
        self.assertEqual("failed", comment_log["status"])
        self.assertIn(
            "comments_response_invalid",
            {item["code"] for item in evidence["warnings"]},
        )

    def test_rejects_work_items_and_list_entries_without_usable_fields(self):
        empty_item = collect_evidence(
            source="DFHIS-94600",
            client=FakeClient(
                {
                    ("get_work_item", "DFHIS-94600", ""): ok({"title": None}),
                }
            ),
            fetched_at="2026-07-24T15:36:00+08:00",
        )
        self.assertEqual("fetch_failed", empty_item["decision_gate"]["state"])

        malformed_comments = {
            ("get_work_item", "DFHIS-94601", ""): ok(
                {
                    "id": "item-461",
                    "serialNumber": "DFHIS-94601",
                    "title": "评论元素异常",
                }
            ),
            ("list_comments", "item-461", ""): ok([{"unexpected": "value"}]),
            ("list_attachments", "item-461", ""): ok([]),
            ("list_relations", "item-461", "PARENT"): ok([]),
            ("list_relations", "item-461", "SUB"): ok([]),
            ("list_relations", "item-461", "ASSOCIATED"): ok([]),
            ("list_relations", "item-461", "DEPEND_ON"): ok([]),
            ("list_relations", "item-461", "DEPENDED_BY"): ok([]),
        }
        malformed = collect_evidence(
            source="DFHIS-94601",
            client=FakeClient(malformed_comments),
            fetched_at="2026-07-24T15:37:00+08:00",
        )
        self.assertEqual("needs_requirement_confirmation", malformed["decision_gate"]["state"])
        self.assertEqual("failed", malformed["work_items"][0]["comments_status"])

    def test_redacts_known_secret_from_work_item_and_comment_content(self):
        secret = "pt-content-secret"
        responses = {
            ("get_work_item", "DFHIS-95000", ""): ok(
                {
                    "id": "item-50",
                    "serialNumber": "DFHIS-95000",
                    "title": f"不要保留 {secret}",
                    "description": f"<p>诊断文本 {secret}</p>",
                }
            ),
            ("list_comments", "item-50", ""): ok(
                [{"id": "comment-50", "content": f"评论 {secret}"}]
            ),
            ("list_attachments", "item-50", ""): ok([]),
            ("list_relations", "item-50", "PARENT"): ok([]),
            ("list_relations", "item-50", "SUB"): ok([]),
            ("list_relations", "item-50", "ASSOCIATED"): ok([]),
            ("list_relations", "item-50", "DEPEND_ON"): ok([]),
            ("list_relations", "item-50", "DEPENDED_BY"): ok([]),
        }

        evidence = collect_evidence(
            source="DFHIS-95000",
            client=FakeClient(responses),
            secrets=[secret],
            fetched_at="2026-07-24T15:40:00+08:00",
        )

        serialized = json.dumps(evidence, ensure_ascii=False)
        self.assertNotIn(secret, serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_rejects_nonempty_or_symlink_output_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            nonempty = root / "nonempty"
            nonempty.mkdir()
            (nonempty / "old.txt").write_text("old", encoding="utf-8")
            linked = root / "linked"
            linked.symlink_to(nonempty, target_is_directory=True)
            real_parent = root / "real-parent"
            real_parent.mkdir()
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            client = FakeClient({})

            with self.assertRaises(ValueError):
                collect_evidence(
                    source="DFHIS-96000",
                    client=client,
                    output_dir=nonempty,
                )
            with self.assertRaises(ValueError):
                collect_evidence(
                    source="DFHIS-96000",
                    client=client,
                    output_dir=linked,
                )
            with self.assertRaises(ValueError):
                collect_evidence(
                    source="DFHIS-96000",
                    client=client,
                    output_dir=linked_parent / "new-revision",
                )


if __name__ == "__main__":
    unittest.main()
