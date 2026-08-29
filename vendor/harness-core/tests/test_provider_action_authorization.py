from __future__ import annotations

import concurrent.futures
import dataclasses
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import database
from app.manager_provider_repository import ManagerProviderRepository
from app.provider_action_authorization import (
    ProviderActionAuthorization,
    ProviderActionAuthorizer,
    canonical_json_hash,
    redact_safe_result_summary,
)


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class ProviderActionAuthorizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "harness.sqlite"
        self.repository = ManagerProviderRepository()
        self.profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="model",
            profile_key="demo",
            display_name="Demo",
            enabled=True,
            connection={"provider_kind": "openai_compatible", "model": "demo-model"},
        )
        self.clock = MutableClock(datetime(2026, 8, 9, 2, 0, tzinfo=timezone.utc))
        self.authorizer = ProviderActionAuthorizer(self.repository, clock=self.clock)

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def _plan(self, *, parameters: dict[str, object] | None = None):
        return self.authorizer.create_plan(
            profile_id=self.profile.id,
            action="model.single_node.smoke",
            target_alias="model-demo",
            parameters=parameters or {"marker": "SMOKE_OK", "timeout_seconds": 5},
            requested_by="manager-user",
        )

    def test_plan_binds_scope_provider_profile_action_target_and_canonical_parameters(self) -> None:
        parameters_a = {"timeout_seconds": 5, "options": {"marker": "SMOKE_OK", "count": 1}}
        parameters_b = {"options": {"count": 1, "marker": "SMOKE_OK"}, "timeout_seconds": 5}

        plan = self._plan(parameters=parameters_a)
        stored = self.repository.get_action_plan(plan.id)

        self.assertEqual("local", plan.scope_type)
        self.assertEqual("default", plan.scope_key)
        self.assertEqual("model", plan.provider)
        self.assertEqual(self.profile.id, plan.profile_id)
        self.assertEqual("demo", plan.profile_key)
        self.assertEqual("model.single_node.smoke", plan.action)
        self.assertEqual("model-demo", plan.target_alias)
        self.assertEqual(canonical_json_hash(parameters_a), plan.parameter_hash)
        self.assertEqual(canonical_json_hash(parameters_b), plan.parameter_hash)
        self.assertEqual("planned", plan.state)
        self.assertEqual(plan.parameter_hash, stored["parameter_hash"])
        self.assertEqual(parameters_a, stored["reviewed_parameter_summary"])

    def test_plan_persists_bounded_exact_reviewed_summary_bound_to_parameter_hash(self) -> None:
        parameters = {"marker": "SMOKE_OK", "timeout_seconds": 5}

        plan = self._plan(parameters=parameters)
        stored = self.repository.get_action_plan(plan.id)

        self.assertEqual(parameters, plan.reviewed_parameter_summary)
        self.assertEqual(parameters, stored["reviewed_parameter_summary"])
        self.assertEqual(canonical_json_hash(parameters), stored["parameter_hash"])
        with database.connect() as connection:
            with self.assertRaises(Exception):
                connection.execute(
                    "update manager_provider_action_plans set reviewed_parameter_summary_json = '{}' where id = ?",
                    (plan.id,),
                )

    def test_execute_before_confirmation_fails_and_records_safe_attempt(self) -> None:
        plan = self._plan()

        decision = self.authorizer.consume(
            plan_id=plan.id,
            authorization=None,
            actor="manager-user",
            parameters={"marker": "SMOKE_OK", "timeout_seconds": 5},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual("authorization_required", decision.reason)
        self.assertEqual("planned", self.repository.get_action_plan(plan.id)["state"])
        rows = self.repository.list_action_audits()
        self.assertEqual(1, len(rows))
        self.assertEqual("rejected", rows[0]["status"])
        self.assertEqual({"reason": "authorization_required"}, rows[0]["details"])

    def test_confirmation_creates_expiring_one_use_authorization(self) -> None:
        plan = self._plan()
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)

        self.assertIsInstance(authorization, ProviderActionAuthorization)
        self.assertEqual(plan.id, authorization.plan_id)
        self.assertNotEqual(authorization.token, authorization.authorization_hash)
        self.assertEqual(self.clock.current + timedelta(seconds=60), authorization.expires_at)
        self.assertEqual("confirmed", self.repository.get_action_plan(plan.id)["state"])

        first = self.authorizer.consume(
            plan_id=plan.id,
            authorization=authorization,
            actor="manager-user",
            parameters={"marker": "SMOKE_OK", "timeout_seconds": 5},
        )
        second = self.authorizer.consume(
            plan_id=plan.id,
            authorization=authorization,
            actor="manager-user",
            parameters={"marker": "SMOKE_OK", "timeout_seconds": 5},
        )

        self.assertTrue(first.allowed)
        self.assertEqual("consumed", first.status)
        self.assertFalse(second.allowed)
        self.assertEqual("authorization_reused", second.reason)
        self.assertEqual("consumed", self.repository.get_action_plan(plan.id)["state"])
        self.assertEqual(2, len(self.repository.list_action_audits()))

    def test_changed_parameters_reject_and_invalidate_authorization(self) -> None:
        plan = self._plan()
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)

        decision = self.authorizer.consume(
            plan_id=plan.id,
            authorization=authorization,
            actor="manager-user",
            parameters={"marker": "CHANGED", "timeout_seconds": 5},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual("parameter_hash_mismatch", decision.reason)
        self.assertEqual("rejected", self.repository.get_action_plan(plan.id)["state"])

    def test_wrong_actor_rejects_and_invalidate_authorization(self) -> None:
        plan = self._plan()
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)

        decision = self.authorizer.consume(
            plan_id=plan.id,
            authorization=authorization,
            actor="other-user",
            parameters={"marker": "SMOKE_OK", "timeout_seconds": 5},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual("actor_mismatch", decision.reason)
        self.assertEqual("rejected", self.repository.get_action_plan(plan.id)["state"])

    def test_expired_authorization_fails_deterministically(self) -> None:
        plan = self._plan()
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)
        self.clock.current += timedelta(seconds=61)

        decision = self.authorizer.consume(
            plan_id=plan.id,
            authorization=authorization,
            actor="manager-user",
            parameters={"marker": "SMOKE_OK", "timeout_seconds": 5},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual("authorization_expired", decision.reason)
        self.assertEqual("expired", self.repository.get_action_plan(plan.id)["state"])

    def test_authorization_token_cannot_be_supplied_by_a_model_or_provider_response(self) -> None:
        plan = self._plan()
        with self.assertRaises(TypeError):
            self.authorizer.confirm(  # type: ignore[call-arg]
                plan.id,
                actor="manager-user",
                ttl_seconds=60,
                authorization_id="model-supplied-value",
            )

        decision = self.authorizer.consume(
            plan_id=plan.id,
            authorization={"approval": "model-supplied-value"},  # type: ignore[arg-type]
            actor="manager-user",
            parameters={"marker": "SMOKE_OK", "timeout_seconds": 5},
        )
        self.assertFalse(decision.allowed)
        self.assertEqual("trusted_authorization_required", decision.reason)

    def test_secret_shaped_untrusted_authorization_fails_without_audit(self) -> None:
        plan = self._plan()
        sentinel = "RandomOpaqueToken9Zx7Qp4Lm2Nv8Bc6"

        with self.assertRaisesRegex(
            ValueError,
            "^provider_action_authorization:sensitive_public_input$",
        ) as raised:
            self.authorizer.consume(
                plan_id=plan.id,
                authorization={"accessToken": sentinel},  # type: ignore[arg-type]
                actor="manager-user",
                parameters={"marker": "SMOKE_OK", "timeout_seconds": 5},
            )

        self.assertNotIn(sentinel, str(raised.exception))
        self.assertEqual("planned", self.repository.get_action_plan(plan.id)["state"])
        self.assertEqual([], self.repository.list_action_audits())

    def test_all_unsafe_untrusted_authorization_types_fail_without_audit(self) -> None:
        class UninspectableAuthorization:
            def __str__(self) -> str:
                raise AssertionError("unknown authorization must not be stringified")

            def __repr__(self) -> str:
                raise AssertionError("unknown authorization must not be represented")

        plan = self._plan()
        sentinel = "UntrustedAuthorizationToken9Zx7Qp4Lm2Nv8Bc6"
        cases = (
            ("string", f"Authorization: Bearer {sentinel}"),
            ("bytes", f"postgresql://audit:{sentinel}@db.example/his".encode()),
            (
                "bytearray",
                bytearray(
                    (
                        "-----BEGIN PRIVATE KEY-----\n"
                        f"{sentinel}\n"
                        "-----END PRIVATE KEY-----"
                    ).encode()
                ),
            ),
            ("sequence", ["safe-reference", {"accessToken": sentinel}]),
            ("unknown", UninspectableAuthorization()),
        )

        for label, authorization in cases:
            with self.subTest(kind=label):
                with self.assertRaisesRegex(
                    ValueError,
                    "^provider_action_authorization:sensitive_public_input$",
                ) as raised:
                    self.authorizer.consume(
                        plan_id=plan.id,
                        authorization=authorization,  # type: ignore[arg-type]
                        actor="manager-user",
                        parameters={"marker": "SMOKE_OK", "timeout_seconds": 5},
                    )
                self.assertNotIn(sentinel, str(raised.exception))

        self.assertEqual("planned", self.repository.get_action_plan(plan.id)["state"])
        self.assertEqual([], self.repository.list_action_audits())

    def test_trusted_authorization_with_mismatched_plan_id_is_explicitly_rejected(self) -> None:
        plan = self._plan()
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)
        mismatched = dataclasses.replace(authorization, plan_id=plan.id + 1000)

        decision = self.authorizer.consume(
            plan_id=plan.id,
            authorization=mismatched,
            actor="manager-user",
            parameters={"marker": "SMOKE_OK", "timeout_seconds": 5},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual("authorization_plan_mismatch", decision.reason)
        self.assertEqual("confirmed", self.repository.get_action_plan(plan.id)["state"])
        self.assertEqual(
            {"reason": "authorization_plan_mismatch"},
            self.repository.list_action_audits()[0]["details"],
        )

    def test_secret_shaped_public_input_cannot_create_plan_or_audit(self) -> None:
        sentinel = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"

        with self.assertRaisesRegex(ValueError, "sensitive_public_input") as raised:
            self._plan(parameters={"message": sentinel})

        self.assertNotIn(sentinel, str(raised.exception))
        with database.connect() as connection:
            plan_count = int(
                connection.execute("select count(*) from manager_provider_action_plans").fetchone()[0]
            )
            audit_count = int(
                connection.execute("select count(*) from manager_provider_action_audits").fetchone()[0]
            )
        self.assertEqual((0, 0), (plan_count, audit_count))

    def test_plan_service_rejects_unregistered_action_and_provider_mismatch_without_side_effects(self) -> None:
        cases = (
            ("unknown.action", {}),
            (
                "remote.fetch",
                {
                    "repository_alias": "repo",
                    "remote_alias": "origin",
                    "ref_name": "refs/heads/main",
                },
            ),
        )

        for action, parameters in cases:
            with self.subTest(action=action):
                with self.assertRaisesRegex(
                    ValueError,
                    "provider_action_(?:not_registered|provider_mismatch)",
                ):
                    self.authorizer.create_plan(
                        profile_id=self.profile.id,
                        action=action,
                        target_alias="model-demo",
                        parameters=parameters,
                        requested_by="manager-user",
                    )

        self.assertEqual([], self.repository.list_action_plans())
        self.assertEqual([], self.repository.list_action_audits())

    def test_git_unsafe_refs_are_rejected_before_action_plan_persists(self) -> None:
        profile = self.repository.upsert_profile(
            scope_type="local",
            scope_key="default",
            provider="git",
            profile_key="repo",
            display_name="Repository",
            enabled=True,
            connection={"repository_path": "/private/tmp/repo"},
        )
        base = "a" * 40
        invalid_requests = (
            ("branch.create", {"repository_alias": "repo", "branch_name": ".foo", "expected_base_sha": base}),
            ("commit.create", {
                "repository_alias": "repo", "branch_name": "feature/foo.lock",
                "expected_parent": base, "file_list": ["safe.txt"],
                "expected_file_blobs": {"safe.txt": None}, "message": "safe",
            }),
            ("remote.fetch", {"repository_alias": "repo", "remote_alias": "origin", "ref_name": "refs/heads/foo.LOCK"}),
        )

        for action, parameters in invalid_requests:
            with self.subTest(action=action, parameters=parameters):
                with self.assertRaisesRegex(ValueError, "git_(?:branch|refspec)_not_allowed"):
                    self.authorizer.create_plan(
                        profile_id=profile.id,
                        action=action,
                        target_alias="repo",
                        parameters=parameters,
                        requested_by="manager-user",
                    )

        branch_plan = self.authorizer.create_plan(
            profile_id=profile.id,
            action="branch.create",
            target_alias="repo",
            parameters={"repository_alias": "repo", "branch_name": "feature/nested/x", "expected_base_sha": base},
            requested_by="manager-user",
        )
        fetch_plan = self.authorizer.create_plan(
            profile_id=profile.id,
            action="remote.fetch",
            target_alias="repo",
            parameters={"repository_alias": "repo", "remote_alias": "origin", "ref_name": "refs/heads/feature/nested/x"},
            requested_by="manager-user",
        )
        self.assertEqual("planned", branch_plan.state)
        self.assertEqual("planned", fetch_plan.state)
        self.assertEqual([], self.repository.list_action_audits())

    def test_safe_result_summary_is_bounded_and_strictly_redacted(self) -> None:
        sentinel = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456"

        redacted = redact_safe_result_summary(
            {
                "status": "failed",
                "message": sentinel,
                "authorization": sentinel,
                "nested": {"password": "must-not-survive"},
                sentinel: "secret-shaped-key-must-not-survive",
            }
        )

        serialized = json.dumps(redacted, ensure_ascii=False)
        self.assertNotIn(sentinel, serialized)
        self.assertNotIn("secret-shaped-key-must-not-survive", serialized)
        self.assertNotIn("must-not-survive", serialized)
        self.assertIn("[REDACTED", serialized)

    def test_concurrent_consume_allows_exactly_one_execution(self) -> None:
        plan = self._plan()
        authorization = self.authorizer.confirm(plan.id, actor="manager-user", ttl_seconds=60)

        def consume_once() -> bool:
            return self.authorizer.consume(
                plan_id=plan.id,
                authorization=authorization,
                actor="manager-user",
                parameters={"marker": "SMOKE_OK", "timeout_seconds": 5},
            ).allowed

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: consume_once(), range(2)))

        self.assertEqual([False, True], sorted(results))
        self.assertEqual("consumed", self.repository.get_action_plan(plan.id)["state"])
        self.assertEqual(2, len(self.repository.list_action_audits()))


if __name__ == "__main__":
    unittest.main()
