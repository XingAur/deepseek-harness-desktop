from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app import database
from app.learning_candidate_repository import LearningCandidateRepository
from app.knowledge_index import query_knowledge_index
from app.manager_provider_repository import ManagerProviderRepository


class LearningCandidateRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager.sqlite"
        self.repository = LearningCandidateRepository()

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def _failed_sample(self, **overrides: object) -> dict[str, object]:
        sample: dict[str, object] = {
            "run_id": "controlled-run-42",
            "task_key": "DFHIS-31333",
            "failure_kind": "verification_failed",
            "summary": "金额汇总规则需要补充回归证据。",
            "evidence_refs": [
                "runs/controlled-run-42/replay-result.json",
                "runs/controlled-run-42/review-note.md",
            ],
            "scope": {"module": "门诊收费", "repo": "df-web-guahaosf"},
        }
        sample.update(overrides)
        return sample

    def _source_action_audit(self, *, status: str = "failed") -> int:
        return ManagerProviderRepository().record_action(
            profile_id=None,
            action_type="controlled.test.action",
            status=status,
            details={"result": "redacted"},
        )

    def test_runtime_candidate_set_requires_one_existing_failed_audit_and_is_bound_to_it(self) -> None:
        failed_audit_id = self._source_action_audit()
        succeeded_audit_id = self._source_action_audit(status="succeeded")

        first = self.repository.create_failed_run_candidates(
            self._failed_sample(), source_action_audit_id=failed_audit_id
        )
        second = self.repository.create_failed_run_candidates(
            self._failed_sample(run_id="same-audit-different-run"),
            source_action_audit_id=failed_audit_id,
        )

        self.assertEqual(4, first["created_count"])
        self.assertEqual(0, second["created_count"])
        with database.connect() as connection:
            rows = connection.execute(
                """
                select source_action_audit_id, candidate_type
                from manager_learning_candidates
                order by candidate_type
                """
            ).fetchall()
        self.assertEqual(4, len(rows))
        self.assertEqual({failed_audit_id}, {int(row["source_action_audit_id"]) for row in rows})

        for invalid_audit_id in (None, succeeded_audit_id, 999999):
            with self.subTest(source_action_audit_id=invalid_audit_id):
                with self.assertRaisesRegex(ValueError, "learning_candidate_source_audit_invalid"):
                    self.repository.create_failed_run_candidates(
                        self._failed_sample(run_id=f"invalid-{invalid_audit_id}"),
                        source_action_audit_id=invalid_audit_id,
                    )

    def test_failed_controlled_run_creates_idempotent_manager_candidate_set_without_raw_evidence_paths(self) -> None:
        sample = self._failed_sample()

        source_action_audit_id = self._source_action_audit()
        first = self.repository.create_failed_run_candidates(
            sample, source_action_audit_id=source_action_audit_id
        )
        second = self.repository.create_failed_run_candidates(
            sample, source_action_audit_id=source_action_audit_id
        )

        self.assertEqual(4, first["candidate_count"])
        self.assertEqual(4, first["created_count"])
        self.assertEqual(0, second["created_count"])
        self.assertEqual(
            [
                "contract_plugin.draft",
                "eval.sample",
                "knowledge.candidate",
                "rule_pack.draft",
            ],
            sorted(item["candidate_type"] for item in first["candidates"]),
        )
        self.assertTrue(all(item["state"] == "candidate" for item in first["candidates"]))
        self.assertTrue(all(item["requires_reviewer"] for item in first["candidates"]))
        with database.connect() as connection:
            rows = connection.execute(
                "select candidate_key, safe_summary_json from manager_learning_candidates"
            ).fetchall()
        self.assertEqual(4, len(rows))
        persisted = json.dumps([dict(row) for row in rows], ensure_ascii=False)
        self.assertNotIn("runs/controlled-run-42", persisted)
        self.assertNotIn("replay-result.json", persisted)
        self.assertIn("sha256:", persisted)

    def test_secret_or_raw_response_shaped_failure_never_creates_candidates(self) -> None:
        sample = self._failed_sample(
            summary='{"access_token":"SENTINEL_RESPONSE_TOKEN"}',
            evidence_refs=["Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345"],
        )

        with self.assertRaisesRegex(ValueError, "learning_candidate_input_invalid"):
            self.repository.create_failed_run_candidates(
                sample, source_action_audit_id=self._source_action_audit()
            )

        with database.connect() as connection:
            count = int(
                connection.execute("select count(*) from manager_learning_candidates").fetchone()[0]
            )
        self.assertEqual(0, count)

    def test_manager_candidate_never_persists_even_plain_text_provider_response(self) -> None:
        raw_response = "MODEL_RESPONSE_UNSAFE: provider returned a detailed generated diagnosis"
        created = self.repository.create_failed_run_candidates(
            self._failed_sample(summary=raw_response),
            source_action_audit_id=self._source_action_audit(),
        )

        with database.connect() as connection:
            stored = connection.execute(
                "select safe_summary_json from manager_learning_candidates"
            ).fetchall()
        rendered = json.dumps([dict(row) for row in stored], ensure_ascii=False)
        self.assertEqual(4, created["candidate_count"])
        self.assertNotIn(raw_response, rendered)
        self.assertIn("summary_hash", rendered)

    def test_only_explicit_reviewer_can_approve_and_promote_safe_knowledge_candidate(self) -> None:
        created = self.repository.create_failed_run_candidates(
            self._failed_sample(), source_action_audit_id=self._source_action_audit()
        )
        knowledge = next(
            item for item in created["candidates"]
            if item["candidate_type"] == "knowledge.candidate"
        )
        knowledge_key = str(knowledge["candidate_key"])
        home = Path(self.temp_dir.name) / "temporary-knowledge-home"

        with self.assertRaisesRegex(PermissionError, "candidate_not_approved"):
            self.repository.promote_knowledge_candidate(
                candidate_key=knowledge_key,
                reviewer_alias="reviewer-a",
                knowledge_home=home,
                knowledge_allowed_base=self.temp_dir.name,
            )
        with self.assertRaisesRegex(ValueError, "reviewer_alias"):
            self.repository.review_candidate(
                candidate_key=knowledge_key,
                decision="approve",
                reviewer_alias="",
            )

        approved = self.repository.review_candidate(
            candidate_key=knowledge_key,
            decision="approve",
            reviewer_alias="reviewer-a",
        )
        promoted = self.repository.promote_knowledge_candidate(
            candidate_key=knowledge_key,
            reviewer_alias="reviewer-a",
            knowledge_home=home,
            knowledge_allowed_base=self.temp_dir.name,
        )

        self.assertEqual("approved", approved["state"])
        self.assertEqual("promoted", promoted["state"])
        self.assertTrue(Path(str(promoted["markdown_path"])).is_file())
        self.assertTrue(Path(str(promoted["manifest_path"])).is_file())
        markdown = Path(str(promoted["markdown_path"])).read_text(encoding="utf-8")
        self.assertIn("status: approved", markdown)
        self.assertNotIn("runs/controlled-run-42", markdown)
        indexed = query_knowledge_index(home, "DFHIS-31333")
        self.assertTrue(indexed["answerable"])
        self.assertEqual([str(promoted["source_path"])], indexed["citations"])

    def test_draft_types_and_expired_candidates_can_never_be_promoted(self) -> None:
        active = self.repository.create_failed_run_candidates(
            self._failed_sample(), source_action_audit_id=self._source_action_audit()
        )
        draft = next(
            item for item in active["candidates"]
            if item["candidate_type"] == "eval.sample"
        )
        expired = self.repository.create_failed_run_candidates(
            self._failed_sample(
                run_id="controlled-run-expired",
                expires_at="2000-01-01T00:00:00+00:00",
            ),
            source_action_audit_id=self._source_action_audit(),
        )
        knowledge = next(
            item for item in expired["candidates"]
            if item["candidate_type"] == "knowledge.candidate"
        )
        home = Path(self.temp_dir.name) / "temporary-knowledge-home"

        self.repository.review_candidate(
            candidate_key=str(draft["candidate_key"]),
            decision="approve",
            reviewer_alias="reviewer-a",
        )
        with self.assertRaisesRegex(PermissionError, "knowledge_candidate_required"):
            self.repository.promote_knowledge_candidate(
                candidate_key=str(draft["candidate_key"]),
                reviewer_alias="reviewer-a",
                knowledge_home=home,
                knowledge_allowed_base=self.temp_dir.name,
            )
        with self.assertRaisesRegex(PermissionError, "candidate_expired"):
            self.repository.review_candidate(
                candidate_key=str(knowledge["candidate_key"]),
                decision="approve",
                reviewer_alias="reviewer-a",
            )

    def test_rejected_knowledge_candidate_cannot_be_approved_or_promoted_later(self) -> None:
        created = self.repository.create_failed_run_candidates(
            self._failed_sample(run_id="controlled-run-rejected"),
            source_action_audit_id=self._source_action_audit(),
        )
        knowledge = next(
            item for item in created["candidates"]
            if item["candidate_type"] == "knowledge.candidate"
        )
        key = str(knowledge["candidate_key"])
        rejected = self.repository.review_candidate(
            candidate_key=key,
            decision="reject",
            reviewer_alias="reviewer-a",
        )

        self.assertEqual("rejected", rejected["state"])
        with self.assertRaisesRegex(PermissionError, "candidate_review_state_invalid"):
            self.repository.review_candidate(
                candidate_key=key,
                decision="approve",
                reviewer_alias="reviewer-a",
            )
        with self.assertRaisesRegex(PermissionError, "candidate_not_approved"):
            self.repository.promote_knowledge_candidate(
                candidate_key=key,
                reviewer_alias="reviewer-a",
                knowledge_home=Path(self.temp_dir.name) / "temporary-knowledge-home",
                knowledge_allowed_base=self.temp_dir.name,
            )


if __name__ == "__main__":
    unittest.main()
