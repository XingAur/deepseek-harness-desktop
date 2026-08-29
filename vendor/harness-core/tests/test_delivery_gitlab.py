from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.delivery_gitlab import GitLabDeliveryExecutor


class _Profiles:
    def __init__(self) -> None:
        self.items = [
            SimpleNamespace(
                id=17,
                provider="gitlab",
                profile_key="company",
                enabled=True,
                connection={"host": "gitlab.example.test"},
            )
        ]

    def list_profiles(self):
        return list(self.items)


class _Authorizer:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.confirmed: list[dict] = []

    def create_plan(self, **kwargs):
        self.created.append(dict(kwargs))
        return SimpleNamespace(id=91)

    def confirm(self, plan_id, **kwargs):
        self.confirmed.append({"plan_id": plan_id, **kwargs})
        return SimpleNamespace(plan_id=plan_id)


class _ExecutionService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, authorization, request):
        self.calls.append({"authorization": authorization, "request": request})
        return {
            "status": "succeeded",
            "write_effect_status": "verified_applied",
            "actual_target_alias": "gl-h7-company-g5-dfhis-p6-guahao-m42",
            "external_calls": True,
        }


class GitLabDeliveryExecutorTests(unittest.TestCase):
    def test_declared_action_uses_matching_profile_and_returns_verified_receipt(self) -> None:
        profiles = _Profiles()
        authorizer = _Authorizer()
        service = _ExecutionService()
        executor = GitLabDeliveryExecutor(
            profiles,
            authorizer,
            execution_service_factory=lambda _profile: service,
        )
        action = {
            "action": "merge_request.create",
            "parameters": {
                "host_alias": "company",
                "project_alias": "dfhis/guahao",
                "source_branch": "feature-DFHIS-31557",
                "target_branch": "RC_2.16.1_250514",
                "title": "DFHIS-31557 挂号默认身份证",
            },
        }

        receipt = executor(
            transaction_id=12,
            approved_plan_hash="a" * 64,
            gitlab_action=action,
            plan={"plan_hash": "a" * 64},
        )

        self.assertEqual("merge_request.create", receipt["action"])
        self.assertEqual("success", receipt["status"])
        self.assertEqual("verified_applied", receipt["write_effect_status"])
        self.assertEqual("gl-h7-company-g5-dfhis-p6-guahao-m42", receipt["target_alias"])
        self.assertTrue(receipt["remote_dispatch_attempted"])
        self.assertEqual(17, authorizer.created[0]["profile_id"])
        self.assertEqual("merge_request.create", authorizer.created[0]["action"])
        self.assertEqual(
            "gl-h7-company-g5-dfhis-p6-guahao",
            authorizer.created[0]["target_alias"],
        )
        parameters = authorizer.created[0]["parameters"]
        self.assertEqual(action["parameters"], {key: parameters[key] for key in action["parameters"]})
        self.assertEqual(15, parameters["timeout_seconds"])
        self.assertEqual("delivery-12", authorizer.created[0]["requested_by"])
        self.assertEqual(91, authorizer.confirmed[0]["plan_id"])
        self.assertEqual(91, service.calls[0]["request"].plan_id)

    def test_origin_derived_host_uses_matching_profile_without_leaking_profile_key(self) -> None:
        profiles = _Profiles()
        authorizer = _Authorizer()
        service = _ExecutionService()
        executor = GitLabDeliveryExecutor(
            profiles,
            authorizer,
            execution_service_factory=lambda _profile: service,
        )
        action = {
            "action": "merge_request.create",
            "parameters": {
                "host_alias": "gitlab-example-test",
                "gitlab_host": "gitlab.example.test",
                "project_alias": "dfhis/guahao",
                "source_branch": "feature-DFHIS-31557",
                "target_branch": "RC_2.16.1_250514",
                "title": "DFHIS-31557 挂号默认身份证",
            },
        }

        receipt = executor(
            transaction_id=12,
            approved_plan_hash="a" * 64,
            gitlab_action=action,
            plan={"plan_hash": "a" * 64},
        )

        self.assertEqual(
            "gl-h19-gitlab-example-test-g5-dfhis-p6-guahao-m42",
            receipt["target_alias"],
        )
        parameters = authorizer.created[0]["parameters"]
        self.assertEqual("company", parameters["host_alias"])
        self.assertNotIn("gitlab_host", parameters)


if __name__ == "__main__":
    unittest.main()
