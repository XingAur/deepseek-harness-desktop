from __future__ import annotations

import json
import unittest

from app.external_write_plan import build_external_write_dry_run_plan


class ExternalWritePlanTests(unittest.TestCase):
    def test_dry_run_plan_is_inert_for_all_external_write_actions(self) -> None:
        plan = build_external_write_dry_run_plan(
            [
                {"capability": "workitem.write", "target": "DFHIS-31333", "operation": "comment"},
                {"capability": "git.push", "target": "origin/feature-DFHIS-31333", "operation": "push"},
                {"capability": "gitlab.write", "target": "merge-request", "operation": "create_mr"},
                {"capability": "github.write", "target": "pull-request", "operation": "create_pr"},
                {"capability": "database.change", "target": "postgresql", "operation": "ddl_or_dml"},
            ]
        )

        self.assertEqual("his-external-write-dry-run-plan.v1", plan["schema_version"])
        self.assertEqual("dry_run", plan["mode"])
        self.assertFalse(plan["changed"])
        self.assertFalse(plan["external_write_attempted"])
        self.assertFalse(plan["execution_allowed"])
        self.assertTrue(plan["confirmation_required"])
        self.assertEqual(
            ["workitem.write", "git.push", "gitlab.write", "github.write", "database.change"],
            [item["capability"] for item in plan["actions"]],
        )
        self.assertTrue(all(item["status"] == "blocked_by_policy" for item in plan["actions"]))
        self.assertTrue(all(item["idempotency_key"].startswith("dryrun:") for item in plan["actions"]))

    def test_dry_run_plan_rejects_secret_shaped_payloads(self) -> None:
        with self.assertRaisesRegex(ValueError, "sensitive value"):
            build_external_write_dry_run_plan(
                [
                    {
                        "capability": "workitem.write",
                        "target": "DFHIS-31333",
                        "operation": "comment",
                        "payload_preview": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456",
                    }
                ]
            )

    def test_dry_run_plan_rejects_unknown_write_capability(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported external write capability"):
            build_external_write_dry_run_plan(
                [{"capability": "email.send", "target": "someone", "operation": "send"}]
            )


if __name__ == "__main__":
    unittest.main()
