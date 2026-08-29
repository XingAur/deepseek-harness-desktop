from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from app.model_worker_smoke import (
    MODEL_WORKER_SMOKE_READINESS_SCHEMA_VERSION,
    build_model_worker_smoke_readiness,
)


class ModelWorkerSmokeReadinessTests(unittest.TestCase):
    def test_frozen_runtime_exposes_authorized_single_node_smoke_contract(self) -> None:
        readiness = build_model_worker_smoke_readiness()

        self.assertEqual(
            "his-model-worker-smoke-readiness.v1",
            MODEL_WORKER_SMOKE_READINESS_SCHEMA_VERSION,
        )
        self.assertEqual(MODEL_WORKER_SMOKE_READINESS_SCHEMA_VERSION, readiness["schema_version"])
        self.assertEqual("single_node_smoke_ready", readiness["state"])
        self.assertFalse(readiness["credentials_read"])
        self.assertFalse(readiness["network_called"])
        self.assertFalse(readiness["paid_network_calls_allowed"])
        self.assertFalse(readiness["real_model_dag_enabled"])
        self.assertTrue(readiness["single_node_smoke"]["allowed"])
        self.assertEqual("not_run", readiness["single_node_smoke"]["status"])
        self.assertEqual("model.single_node.smoke", readiness["single_node_smoke"]["execution_action"])
        self.assertEqual("dag_still_frozen", readiness["dag_state"])
        self.assertEqual(
            ["allow_credentials=true", "allow_network=true", "authorization_id provided"],
            readiness["single_node_smoke"]["required_authorization"],
        )
        self.assertIn(
            "real_model_runtime_frozen",
            [blocker["code"] for blocker in readiness["blockers"]],
        )
        self.assertNotIn(
            "real_model_smoke_not_allowed",
            [blocker["code"] for blocker in readiness["blockers"]],
        )

    def test_readiness_does_not_read_or_render_environment_secrets(self) -> None:
        sentinel = "SENTINEL_MODEL_WORKER_SECRET"
        with mock.patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": sentinel,
                "ANTHROPIC_API_KEY": sentinel,
                "ZHIPU_API_KEY": sentinel,
            },
        ):
            readiness = build_model_worker_smoke_readiness()

        rendered = json.dumps(readiness, ensure_ascii=False)
        self.assertNotIn(sentinel, rendered)
        self.assertFalse(readiness["credentials_read"])
        self.assertFalse(readiness["network_called"])

    def test_last_smoke_summary_keeps_only_a_safe_audit_subset(self) -> None:
        readiness = build_model_worker_smoke_readiness(
            last_smoke={
                "id": 12,
                "profile_key": "hospital-smoke",
                "endpoint_host": "api.example.test",
                "model": "safe-model",
                "status": "passed",
                "transport_status": "passed",
                "protocol_status": "passed",
                "marker_status": "passed",
                "completed_at": "2026-08-03T10:00:00+08:00",
                "credential_key_names": {"api_key": "OPENAI_API_KEY"},
                "error_detail": "Bearer must-not-appear",
            }
        )

        self.assertEqual(
            {
                "id": 12,
                "profile_key": "hospital-smoke",
                "endpoint_host": "api.example.test",
                "model": "safe-model",
                "status": "passed",
                "transport_status": "passed",
                "protocol_status": "passed",
                "marker_status": "passed",
                "completed_at": "2026-08-03T10:00:00+08:00",
            },
            readiness["single_node_smoke"]["last_smoke"],
        )
        self.assertNotIn("Bearer", json.dumps(readiness, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
