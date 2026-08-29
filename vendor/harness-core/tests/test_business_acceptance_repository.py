from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app import database
from app.business_acceptance_repository import BusinessAcceptanceRepository


class BusinessAcceptanceRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager.sqlite"
        self.repository = BusinessAcceptanceRepository()

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    @staticmethod
    def _evidence(**overrides: object) -> dict[str, object]:
        evidence: dict[str, object] = {
            "evidence_key": "dfhis-acceptance-31333",
            "environment_alias": "his-test-a",
            "operator_alias": "operator-a",
            "test_data_alias": "outpatient-case-001",
            "technical_result": "passed",
            "runtime_verified": True,
            "scenarios": [
                {
                    "name": "outpatient-charge-save",
                    "status": "passed",
                    "expected": "charge-record-created",
                    "actual": "charge-record-created",
                    "evidence": "sha256:" + "a" * 64,
                }
            ],
        }
        evidence.update(overrides)
        return evidence

    def test_evidence_versions_are_immutable_and_reviewer_decisions_are_append_only(self) -> None:
        first = self.repository.create_evidence(self._evidence())
        second = self.repository.create_evidence(
            self._evidence(technical_result="failed")
        )
        rejected = self.repository.append_reviewer_decision(
            evidence_id=int(first["id"]),
            reviewer_alias="reviewer-a",
            decision="reject",
            reason="actual-result-mismatch",
        )
        accepted = self.repository.append_reviewer_decision(
            evidence_id=int(first["id"]),
            reviewer_alias="reviewer-b",
            decision="accept",
            reason="runtime-evidence-reviewed",
        )

        self.assertEqual(1, first["evidence_version"])
        self.assertEqual(2, second["evidence_version"])
        self.assertEqual("reject", rejected["decision"])
        self.assertEqual("accept", accepted["decision"])
        records = self.repository.list_evidence()
        self.assertEqual([2, 1], [item["evidence_version"] for item in records])
        self.assertFalse(records[0]["business_valid"])
        self.assertTrue(records[1]["business_valid"])
        self.assertEqual(2, len(records[1]["reviewer_decisions"]))

        with database.connect() as connection:
            with self.assertRaises(Exception):
                connection.execute(
                    "update manager_business_acceptance_evidence set created_at = ? where id = ?",
                    ("2099-01-01T00:00:00+00:00", int(first["id"])),
                )
            with self.assertRaises(Exception):
                connection.execute(
                    "update manager_business_acceptance_evidence set technical_result = 'failed' where id = ?",
                    (int(first["id"]),),
                )
            with self.assertRaises(Exception):
                connection.execute(
                    "update manager_business_acceptance_decisions set decision = 'reject' where id = ?",
                    (int(accepted["id"]),),
                )

    def test_only_complete_runtime_evidence_plus_latest_explicit_acceptance_is_business_valid(self) -> None:
        evidence = self.repository.create_evidence(self._evidence())
        before = self.repository.get_evidence(int(evidence["id"]))
        self.assertFalse(before["business_valid"])

        accepted = self.repository.append_reviewer_decision(
            evidence_id=int(evidence["id"]),
            reviewer_alias="reviewer-a",
            decision="accept",
            reason="verified-in-test-environment",
        )
        after = self.repository.get_evidence(int(evidence["id"]))

        self.assertEqual("accept", accepted["decision"])
        self.assertTrue(after["business_valid"])
        self.assertTrue(after["runtime_verified"])

        incomplete = self.repository.create_evidence(
            self._evidence(runtime_verified=False, scenarios=[])
        )
        self.repository.append_reviewer_decision(
            evidence_id=int(incomplete["id"]),
            reviewer_alias="reviewer-a",
            decision="accept",
            reason="offline-tests-only",
        )
        self.assertFalse(
            self.repository.get_evidence(int(incomplete["id"]))["business_valid"]
        )

        technically_failed = self.repository.create_evidence(
            self._evidence(
                evidence_key="dfhis-acceptance-failed",
                technical_result="failed",
            )
        )
        self.repository.append_reviewer_decision(
            evidence_id=int(technically_failed["id"]),
            reviewer_alias="reviewer-a",
            decision="accept",
            reason="reviewed-but-technical-result-failed",
        )
        self.assertFalse(
            self.repository.get_evidence(int(technically_failed["id"]))[
                "business_valid"
            ]
        )

    def test_secret_shaped_free_text_is_rejected_without_persistence_or_echo(self) -> None:
        sentinel = "SENTINEL_BUSINESS_SECRET"
        with self.assertRaisesRegex(ValueError, "business_acceptance_input_invalid") as raised:
            self.repository.create_evidence(
                self._evidence(
                    scenarios=[
                        {
                            "name": "secret-case",
                            "status": "passed",
                            "expected": "safe",
                            "actual": f"token={sentinel}",
                            "evidence": "sha256:" + "b" * 64,
                        }
                    ]
                )
            )

        self.assertNotIn(sentinel, str(raised.exception))
        with database.connect() as connection:
            self.assertEqual(
                0,
                int(
                    connection.execute(
                        "select count(*) from manager_business_acceptance_evidence"
                    ).fetchone()[0]
                ),
            )
        raw = b"".join(
            path.read_bytes()
            for path in database.DB_PATH.parent.glob("manager.sqlite*")
            if path.is_file()
        )
        self.assertNotIn(sentinel.encode(), raw)

    def test_bare_bearer_scalar_is_rejected_before_persistence(self) -> None:
        sentinel = "Bearer " + "A9" * 24

        with self.assertRaisesRegex(
            ValueError, "business_acceptance_input_invalid"
        ) as raised:
            self.repository.create_evidence(
                self._evidence(
                    scenarios=[
                        {
                            "name": "bearer-secret-case",
                            "status": "passed",
                            "expected": "safe",
                            "actual": sentinel,
                            "evidence": "sha256:" + "c" * 64,
                        }
                    ]
                )
            )

        self.assertNotIn(sentinel, str(raised.exception))
        self.assertEqual([], self.repository.list_evidence())
        raw = b"".join(
            path.read_bytes()
            for path in database.DB_PATH.parent.glob("manager.sqlite*")
            if path.is_file()
        )
        self.assertNotIn(sentinel.encode(), raw)

    def test_current_business_valid_uses_only_highest_version_and_latest_decision(self) -> None:
        first = self.repository.create_evidence(self._evidence())
        self.repository.append_reviewer_decision(
            evidence_id=int(first["id"]),
            reviewer_alias="reviewer-a",
            decision="accept",
            reason="version-one-reviewed",
        )
        self.assertTrue(self.repository.current_business_valid())

        failed = self.repository.create_evidence(
            self._evidence(technical_result="failed")
        )
        self.repository.append_reviewer_decision(
            evidence_id=int(failed["id"]),
            reviewer_alias="reviewer-b",
            decision="accept",
            reason="new-version-still-failed",
        )
        self.assertFalse(self.repository.current_business_valid())

    def test_current_business_valid_is_scoped_by_scope_type_key_and_evidence_key(self) -> None:
        rejected = self.repository.create_evidence(
            self._evidence(), scope_type="local", scope_key="scope-a"
        )
        self.repository.append_reviewer_decision(
            evidence_id=int(rejected["id"]),
            reviewer_alias="reviewer-a",
            decision="reject",
            reason="scope-a-rejected",
        )
        accepted = self.repository.create_evidence(
            self._evidence(), scope_type="team", scope_key="scope-b"
        )
        self.repository.append_reviewer_decision(
            evidence_id=int(accepted["id"]),
            reviewer_alias="reviewer-b",
            decision="accept",
            reason="scope-b-accepted",
        )

        self.assertFalse(self.repository.current_business_valid())

        newer_scope_a = self.repository.create_evidence(
            self._evidence(technical_result="failed"),
            scope_type="local",
            scope_key="scope-a",
        )
        self.repository.append_reviewer_decision(
            evidence_id=int(newer_scope_a["id"]),
            reviewer_alias="reviewer-c",
            decision="accept",
            reason="scope-a-new-version-failed",
        )
        self.assertFalse(self.repository.current_business_valid())

    def test_historical_sensitive_evidence_and_decision_fail_closed_on_every_read(self) -> None:
        sentinel = "Bearer " + "Q9" * 24
        private_key = "-----BEGIN PRIVATE KEY-----\nHISTORICAL_SECRET\n-----END PRIVATE KEY-----"
        created_at = database.now_iso()
        with database.connect() as connection:
            cursor = connection.execute(
                """
                insert into manager_business_acceptance_evidence(
                    evidence_key, evidence_version, scope_type, scope_key,
                    environment_alias, operator_alias, test_data_alias,
                    technical_result, evidence_hash, evidence_json,
                    business_valid, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    "historical-sensitive-case",
                    1,
                    "local",
                    "default",
                    "his-test-a",
                    "operator-a",
                    "case-a",
                    "passed",
                    "sha256:" + "a" * 64,
                    json.dumps(
                        {
                            "runtime_verified": True,
                            "scenarios": [
                                {
                                    "name": "save",
                                    "status": "passed",
                                    "expected": "ok",
                                    "actual": sentinel,
                                    "evidence": "sha256:" + "b" * 64,
                                    "evidence_hashes": [],
                                }
                            ],
                        }
                    ),
                    created_at,
                ),
            )
            evidence_id = int(cursor.lastrowid)
            connection.execute(
                """
                insert into manager_business_acceptance_decisions(
                    evidence_id, reviewer_alias, decision, reason_redacted, created_at
                ) values (?, ?, ?, ?, ?)
                """,
                (evidence_id, "reviewer-a", "accept", private_key, created_at),
            )

        for read in (
            lambda: self.repository.get_evidence(evidence_id),
            self.repository.list_evidence,
        ):
            with self.assertRaisesRegex(
                ValueError, "^business_acceptance_storage_invalid$"
            ) as raised:
                read()
            self.assertNotIn(sentinel, str(raised.exception))
            self.assertNotIn("HISTORICAL_SECRET", str(raised.exception))
        self.assertFalse(self.repository.current_business_valid())

    def test_latest_reviewer_decision_controls_current_evidence(self) -> None:
        rejected = self.repository.create_evidence(self._evidence())
        self.repository.append_reviewer_decision(
            evidence_id=int(rejected["id"]),
            reviewer_alias="reviewer-c",
            decision="accept",
            reason="initial-review",
        )
        self.repository.append_reviewer_decision(
            evidence_id=int(rejected["id"]),
            reviewer_alias="reviewer-d",
            decision="reject",
            reason="latest-review-rejected",
        )
        self.assertFalse(self.repository.current_business_valid())


if __name__ == "__main__":
    unittest.main()
