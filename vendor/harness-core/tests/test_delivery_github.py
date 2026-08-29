from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.delivery_github import GitHubDeliveryExecutor


class _Profiles:
    def __init__(self) -> None:
        self.items = [
            SimpleNamespace(
                id=23,
                provider="github",
                profile_key="github-dfhis",
                enabled=True,
                connection={"owner": "dfhis", "repository": "guahao"},
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
        return SimpleNamespace(id=97)

    def confirm(self, plan_id, **kwargs):
        self.confirmed.append({"plan_id": plan_id, **kwargs})
        return SimpleNamespace(plan_id=plan_id)


class _ExecutionService:
    def __init__(self, result=None) -> None:
        self.calls: list[dict] = []
        self.result = result or {
            "status": "succeeded",
            "write_effect_status": "verified_applied",
            "actual_target_alias": "gh-o5-dfhis-r6-guahao-p42",
            "external_calls": True,
        }

    def execute(self, authorization, request):
        self.calls.append({"authorization": authorization, "request": request})
        return dict(self.result)


class GitHubDeliveryExecutorTests(unittest.TestCase):
    def test_declared_pull_request_uses_matching_profile_and_verified_receipt(self) -> None:
        profiles = _Profiles()
        authorizer = _Authorizer()
        service = _ExecutionService()
        executor = GitHubDeliveryExecutor(
            profiles,
            authorizer,
            execution_service_factory=lambda _profile: service,
        )
        action = {
            "action": "github.pull_request.create",
            "parameters": {
                "owner": "dfhis",
                "repository": "guahao",
                "head": "feature-DFHIS-31557",
                "base": "RC_2.16.1_250514",
                "title": "DFHIS-31557 挂号默认身份证",
            },
        }

        receipt = executor(
            transaction_id=12,
            approved_plan_hash="a" * 64,
            github_action=action,
            plan={"plan_hash": "a" * 64},
        )

        self.assertEqual("github.pull_request.create", receipt["action"])
        self.assertEqual("success", receipt["status"])
        self.assertEqual("verified_applied", receipt["write_effect_status"])
        self.assertEqual("gh-o5-dfhis-r6-guahao-p42", receipt["target_alias"])
        self.assertTrue(receipt["remote_dispatch_attempted"])
        self.assertEqual(23, authorizer.created[0]["profile_id"])
        self.assertEqual("github.pull_request.create", authorizer.created[0]["action"])
        self.assertEqual(
            "gh-o5-dfhis-r6-guahao",
            authorizer.created[0]["target_alias"],
        )
        self.assertEqual(20, authorizer.created[0]["parameters"]["timeout_seconds"])
        self.assertEqual("delivery-12", authorizer.created[0]["requested_by"])
        self.assertEqual(97, authorizer.confirmed[0]["plan_id"])
        self.assertEqual(97, service.calls[0]["request"].plan_id)

    def test_profile_must_match_the_exact_declared_repository(self) -> None:
        profiles = _Profiles()
        authorizer = _Authorizer()
        executor = GitHubDeliveryExecutor(
            profiles,
            authorizer,
            execution_service_factory=lambda _profile: _ExecutionService(),
        )
        action = {
            "action": "github.pull_request.comment.write",
            "parameters": {
                "owner": "other",
                "repository": "project",
                "pull_request_number": 7,
                "body": "验证通过",
            },
        }

        with self.assertRaisesRegex(ValueError, "github_delivery_profile_missing"):
            executor(
                transaction_id=12,
                approved_plan_hash="a" * 64,
                github_action=action,
                plan={"plan_hash": "a" * 64},
            )

    def test_success_without_actual_readback_target_is_rejected(self) -> None:
        service = _ExecutionService(
            {
                "status": "succeeded",
                "write_effect_status": "verified_applied",
                "external_calls": True,
            }
        )
        executor = GitHubDeliveryExecutor(
            _Profiles(),
            _Authorizer(),
            execution_service_factory=lambda _profile: service,
        )
        action = {
            "action": "github.pull_request.comment.write",
            "parameters": {
                "owner": "dfhis",
                "repository": "guahao",
                "pull_request_number": 42,
                "body": "验证通过",
            },
        }

        with self.assertRaisesRegex(ValueError, "github_delivery_readback_missing"):
            executor(
                transaction_id=12,
                approved_plan_hash="a" * 64,
                github_action=action,
                plan={"plan_hash": "a" * 64},
            )


if __name__ == "__main__":
    unittest.main()
