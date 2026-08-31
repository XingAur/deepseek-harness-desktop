from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.provider_execution import ProviderExecutionContext, ProviderExecutionRequest
from app.providers.yunxiao import YunxiaoHttpResponse, YunxiaoProviderAdapter


BASE = "https://openapi-rdc.aliyuncs.com"
WORKITEM_PATH = "/oapi/v1/projex/organizations/org-main/workitems/DFHIS-42"
HEADERS = {
    "Accept": "application/json",
    "Authorization": "Bearer manager-only-test-pat",
    "x-yunxiao-token": "manager-only-test-pat",
}


class StrictTransport:
    def __init__(self, expected: list[tuple[str, str, dict[str, str], object, object]]) -> None:
        self.expected = list(expected)
        self.calls: list[dict[str, object]] = []

    def __call__(self, *, method, url, headers, body, timeout_seconds):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if not self.expected:
            raise AssertionError("unexpected transport call")
        expected_method, path, expected_headers, expected_body, payload = self.expected.pop(0)
        if method != expected_method or url != BASE + path or headers != expected_headers:
            raise AssertionError("yunxiao HTTP contract mismatch")
        actual_body = json.loads(body.decode("utf-8")) if body is not None else None
        if actual_body != expected_body:
            raise AssertionError("yunxiao JSON body mismatch")
        return YunxiaoHttpResponse(
            status_code=200,
            headers={"x-acs-request-id": "req-123"},
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )


class YunxiaoProviderAdapterTests(unittest.TestCase):
    def context(self) -> ProviderExecutionContext:
        return ProviderExecutionContext(
            profile_id=41,
            required_credential_fields=("pat",),
            network_allowed=True,
            credential_resolver=lambda profile_id, field: (
                "manager-only-test-pat" if (profile_id, field) == (41, "pat") else ""
            ),
        )

    def request(self, action: str, **parameters: object) -> ProviderExecutionRequest:
        return ProviderExecutionRequest(
            plan_id=7,
            actor="manager-user",
            action=action,
            parameters={
                "organization_alias": "org-main",
                "project_alias": "DFHIS",
                "work_item_alias": "DFHIS-42",
                **parameters,
            },
        )

    def comment(self) -> dict[str, str]:
        return {
            "business_logic": "按确认结果同步处理结论",
            "trigger_condition": "完成需求验证后",
            "handling_result": "已生成待测试结论",
            "covered_scenarios": "正常提交和空数据场景",
        }

    def test_workitem_read_uses_known_get_contract_and_bounded_redacted_evidence(self) -> None:
        secret = "Authorization: Bearer never-return-this-secret-1234567890"
        transport = StrictTransport(
            [("GET", WORKITEM_PATH, HEADERS, None, {"identifier": "DFHIS-42", "description": secret})]
        )
        adapter = YunxiaoProviderAdapter(transport=transport)

        result = adapter.execute(self.request("workitem.read"), self.context())

        self.assertEqual([], transport.expected)
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertEqual("yunxiao", result["source"])
        self.assertEqual("req-123", result["request_id"])
        self.assertEqual(64, len(result["content_hash"]))
        self.assertNotIn(secret, rendered)
        self.assertLessEqual(len(rendered.encode("utf-8")), 16_384)

    def test_comment_write_uses_known_contract_and_readback_id(self) -> None:
        comment = self.comment()
        final_comment = (
            "业务逻辑：按确认结果同步处理结论\n"
            "触发条件：完成需求验证后\n"
            "处理结果：已生成待测试结论\n"
            "覆盖场景：正常提交和空数据场景"
        )
        transport = StrictTransport(
            [
                ("POST", WORKITEM_PATH + "/comments", {**HEADERS, "Content-Type": "application/json"}, {"content": final_comment}, {"id": "comment-1"}),
                ("GET", WORKITEM_PATH + "/comments", HEADERS, None, {"items": [{"id": "comment-1", "content": final_comment}]}),
            ]
        )
        adapter = YunxiaoProviderAdapter(transport=transport)
        request = self.request("workitem.comment.write", comment=comment)
        context = self.context()

        write_result = adapter.execute(request, context)
        verified = adapter.verify("workitem.comments.read", "workitem.comment.write", request, "org-main.dfhis-42", context)

        self.assertEqual([], transport.expected)
        self.assertEqual("comment", write_result["change"]["field"])
        self.assertNotIn("value_hash", json.dumps(write_result, ensure_ascii=False))
        self.assertNotIn(final_comment, json.dumps(write_result, ensure_ascii=False))
        self.assertEqual("verified_applied", verified)

    def test_owner_and_status_use_known_field_update_contract(self) -> None:
        cases = (
            ("workitem.owner.update", "owner_value", "张三", "assignee"),
            ("workitem.status.update", "status_value", "待测试", "status"),
        )
        for action, parameter, value, field in cases:
            with self.subTest(action=action):
                body = {
                    "organizationId": "org-main",
                    "workitemIdentifier": "DFHIS-42",
                    "updateWorkitemPropertyRequest": [{"fieldIdentifier": field, "fieldValue": value}],
                }
                transport = StrictTransport(
                    [
                        ("POST", "/oapi/v1/projex/workitems/updateWorkitemField", {**HEADERS, "Content-Type": "application/json"}, body, {"updateId": "update-1"}),
                        ("GET", WORKITEM_PATH, HEADERS, None, {"identifier": "DFHIS-42", field: value, "lastUpdateId": "update-1"}),
                    ]
                )
                adapter = YunxiaoProviderAdapter(transport=transport)
                request = self.request(action, **{parameter: value})
                context = self.context()

                adapter.execute(request, context)
                result = adapter.verify("workitem.read", action, request, "org-main.dfhis-42", context)

                self.assertEqual("verified_applied", result)
                self.assertEqual([], transport.expected)

    def test_target_mismatch_and_unsafe_identifiers_fail_before_credential_or_transport(self) -> None:
        rejected = ("..", "%2F", "DF HIS", "DFHIS/42", "DFHIS\\42", "https://evil", "ＤＦＨＩＳ-42")
        for bad in rejected:
            with self.subTest(alias=bad):
                transport = StrictTransport([])
                context = self.context()
                with self.assertRaisesRegex(ValueError, "yunxiao_identifier_invalid"):
                    YunxiaoProviderAdapter(transport=transport).execute(
                        self.request("workitem.read", work_item_alias=bad), context
                    )
                self.assertFalse(context.credential_resolver_called)
                self.assertEqual([], transport.calls)

        for parameter, bad in (("organization_alias", ".."), ("project_alias", "DF HIS")):
            with self.subTest(parameter=parameter, alias=bad):
                transport = StrictTransport([])
                context = self.context()
                with self.assertRaisesRegex(ValueError, "yunxiao_identifier_invalid"):
                    YunxiaoProviderAdapter(transport=transport).execute(
                        self.request("workitem.read", **{parameter: bad}), context
                    )
                self.assertFalse(context.credential_resolver_called)
                self.assertEqual([], transport.calls)

        transport = StrictTransport([])
        context = self.context()
        with self.assertRaisesRegex(ValueError, "yunxiao_target_mismatch"):
            YunxiaoProviderAdapter(transport=transport).verify(
                "workitem.comments.read", "workitem.comment.write", self.request("workitem.comment.write", comment=self.comment()), "org-main.dfhis-43", context
            )
        self.assertFalse(context.credential_resolver_called)
        self.assertEqual([], transport.calls)

    def test_action_schema_rejects_extra_business_fields_before_credential_or_transport(self) -> None:
        cases = (
            self.request("workitem.status.update", status_value="待测试", owner_value="张三"),
            self.request("workitem.owner.update", owner_value="张三", status_value="待测试"),
            self.request("workitem.read", comment=self.comment()),
        )
        for request in cases:
            with self.subTest(action=request.action):
                transport = StrictTransport([])
                context = self.context()
                with self.assertRaisesRegex(ValueError, "yunxiao_parameters_invalid"):
                    YunxiaoProviderAdapter(transport=transport).execute(request, context)
                self.assertFalse(context.credential_resolver_called)
                self.assertEqual([], transport.calls)

    def test_comment_requires_bounded_business_structure_and_rejects_technical_content(self) -> None:
        invalid_values = (
            "python -m unittest tests.test_yunxiao_provider",
            "C:\\work\\app\\providers\\yunxiao.py",
            "请查看 app/providers 后确认",
            "请查看 src/module 后确认",
            "请执行 npm test 后确认",
            "请执行 node --test 后确认",
            "请执行 mvn test 后确认",
            "请执行 bash -c 后确认",
            "YunxiaoProviderAdapter.verify",
            '{"content":"normal json"}',
            "```python\nprint('x')\n```",
        )
        for invalid in invalid_values:
            with self.subTest(value=invalid):
                comment = self.comment()
                comment["business_logic"] = invalid
                with self.assertRaisesRegex(ValueError, "yunxiao_comment_not_business_oriented"):
                    YunxiaoProviderAdapter(transport=StrictTransport([])).render_plan(
                        self.request("workitem.comment.write", comment=comment)
                    )

        plan = YunxiaoProviderAdapter(transport=StrictTransport([])).render_plan(
            self.request("workitem.comment.write", comment=self.comment())
        )
        self.assertEqual("org-main.dfhis-42", plan["target_alias"])
        self.assertEqual("comment", plan["change"]["field"])

    def test_unassociated_or_incomplete_readback_is_unknown_not_negative_or_false_positive(self) -> None:
        final_comment = "业务逻辑：按确认结果同步处理结论\n触发条件：完成需求验证后\n处理结果：已生成待测试结论\n覆盖场景：正常提交和空数据场景"
        request = self.request("workitem.comment.write", comment=self.comment())
        cases = (
            ({"id": "comment-2"}, {"items": [{"id": "historic-1", "content": final_comment}]}),
            ({"id": "comment-1"}, {"items": [{"id": "comment-1", "content": final_comment}], "hasMore": True}),
            ({"id": "comment-1"}, {"items": [{"id": "historic-1", "content": final_comment}]}),
        )
        for post, readback in cases:
            with self.subTest(post=post):
                transport = StrictTransport(
                    [
                        ("POST", WORKITEM_PATH + "/comments", {**HEADERS, "Content-Type": "application/json"}, {"content": final_comment}, post),
                        ("GET", WORKITEM_PATH + "/comments", HEADERS, None, readback),
                    ]
                )
                adapter = YunxiaoProviderAdapter(transport=transport)
                context = self.context()
                adapter.execute(request, context)
                self.assertEqual("unknown", adapter.verify("workitem.comments.read", "workitem.comment.write", request, "org-main.dfhis-42", context))
                self.assertEqual([], transport.expected)


if __name__ == "__main__":
    unittest.main()
