from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

from app import database
from app.code_evidence_repository import CodeEvidenceRepository


class CodeEvidenceRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_dir.name) / "manager.sqlite"
        self.repository = CodeEvidenceRepository()

    def tearDown(self) -> None:
        database.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    @staticmethod
    def _sha(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _bundle(self, suffix: str = "a") -> dict[str, object]:
        return self.repository.create_bundle(
            bundle_key=f"bundle-{suffix}",
            conversation_key="conversation-a",
            task_key="task-a",
            repository_alias="repo-a",
            repository_identity_sha256=self._sha("repository-a"),
            head_sha="a" * 40,
            snapshot_sha256=self._sha("snapshot-a"),
            required_capabilities=("git.diff", "source.read"),
        )

    def _append_artifact(
        self,
        bundle_id: int,
        *,
        kind: str = "diff_patch",
        leaf: str = "full.patch",
    ) -> dict[str, object]:
        return self.repository.append_artifact(
            bundle_id,
            kind=kind,
            relative_path=f"bundle_{bundle_id}/{leaf}",
            sha256=self._sha(leaf),
            size_bytes=len(leaf),
            device=1,
            inode=bundle_id + 100,
            mode=0o600,
            link_count=1,
        )

    def test_fresh_database_is_v72_and_contains_all_evidence_tables(self) -> None:
        self.assertEqual(72, database.HARNESS_SCHEMA_VERSION)
        with database.connect() as connection:
            self.assertEqual(72, int(connection.execute("pragma user_version").fetchone()[0]))
            tables = {
                str(row[0])
                for row in connection.execute(
                    "select name from sqlite_master where type = 'table'"
                ).fetchall()
            }
        self.assertTrue(
            {
                "code_evidence_bundles",
                "code_evidence_artifacts",
                "code_evidence_events",
                "code_evidence_reviews",
                "code_evidence_sets",
                "code_evidence_set_members",
            }.issubset(tables)
        )

    def test_v69_database_migrates_to_v72_with_auditable_marker(self) -> None:
        path = Path(self.temp_dir.name) / "legacy-v69.sqlite"
        database.DB_PATH = path
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("pragma user_version = 69")
            connection.execute("create table legacy_sentinel(value text not null)")
            connection.execute("insert into legacy_sentinel values('preserved')")

        database.init_db()

        with database.connect() as connection:
            self.assertEqual("preserved", connection.execute("select value from legacy_sentinel").fetchone()[0])
            self.assertEqual(72, int(connection.execute("pragma user_version").fetchone()[0]))
            marker = connection.execute(
                "select migration_name from harness_schema_migrations where from_version = 69 and to_version = 72"
            ).fetchone()
        self.assertIsNotNone(marker)
        self.assertEqual("v0.72-flux-opd-lite-learning", str(marker[0]))

    def test_bundle_artifacts_events_review_and_seal_are_append_only(self) -> None:
        bundle = self._bundle()
        bundle_id = int(bundle["id"])
        event = self.repository.append_event(
            bundle_id,
            event_type="capture_started",
            status="running",
            details={"capability": "git.diff"},
        )
        artifact = self._append_artifact(bundle_id)
        sealed = self.repository.seal_bundle(
            bundle_id,
            seal_sha256=self._sha("seal"),
        )
        review = self.repository.append_review(
            bundle_id,
            verdict="approved",
            review_sha256=self._sha("review"),
            evidence_seal_sha256=self._sha("seal"),
            findings=(),
        )

        self.assertEqual("reviewed", review["bundle_status"])
        self.assertEqual("sealed", sealed["status"])
        record = self.repository.get_bundle(bundle_id)
        self.assertEqual([int(artifact["id"])], [item["id"] for item in record["artifacts"]])
        self.assertEqual([int(event["id"])], [item["id"] for item in record["events"]])
        self.assertEqual("approved", record["review"]["verdict"])

        with database.connect() as connection:
            for statement in (
                "update code_evidence_artifacts set size_bytes = 7",
                "delete from code_evidence_artifacts",
                "update code_evidence_events set status = 'failed'",
                "delete from code_evidence_events",
                "update code_evidence_reviews set verdict = 'changes_requested'",
                "delete from code_evidence_reviews",
            ):
                with self.subTest(statement=statement), self.assertRaises(sqlite3.DatabaseError):
                    connection.execute(statement)

    def test_sealed_bundle_rejects_new_artifacts_events_and_second_review(self) -> None:
        bundle_id = int(self._bundle()["id"])
        self._append_artifact(bundle_id)
        self.repository.seal_bundle(bundle_id, seal_sha256=self._sha("seal"))

        with self.assertRaisesRegex(ValueError, "code_evidence_state_invalid"):
            self.repository.append_artifact(
                bundle_id,
                kind="source",
                relative_path=f"bundle_{bundle_id}/source.txt",
                sha256=self._sha("source"),
                size_bytes=6,
                device=1,
                inode=202,
                mode=0o600,
                link_count=1,
            )
        with self.assertRaisesRegex(ValueError, "code_evidence_state_invalid"):
            self.repository.append_event(
                bundle_id,
                event_type="late_event",
                status="success",
                details={},
            )

        self.repository.append_review(
            bundle_id,
            verdict="approved",
            review_sha256=self._sha("review"),
            evidence_seal_sha256=self._sha("seal"),
            findings=(),
        )
        with self.assertRaisesRegex(ValueError, "code_evidence_state_invalid"):
            self.repository.append_review(
                bundle_id,
                verdict="approved",
                review_sha256=self._sha("review-two"),
                evidence_seal_sha256=self._sha("seal"),
                findings=(),
            )

    def test_concurrent_seal_has_exactly_one_winner(self) -> None:
        bundle_id = int(self._bundle()["id"])
        self._append_artifact(bundle_id)
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def worker(value: str) -> None:
            barrier.wait()
            try:
                self.repository.seal_bundle(bundle_id, seal_sha256=self._sha(value))
                outcomes.append("success")
            except ValueError:
                outcomes.append("blocked")

        threads = [threading.Thread(target=worker, args=(value,)) for value in ("one", "two")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(["blocked", "success"], sorted(outcomes))
        self.assertEqual("sealed", self.repository.get_bundle(bundle_id)["status"])

    def test_input_and_historical_relationship_pollution_fail_closed_without_echo(self) -> None:
        sentinel = "Bearer " + "A9" * 24
        with self.assertRaisesRegex(ValueError, "code_evidence_input_invalid") as raised:
            self.repository.create_bundle(
                bundle_key=sentinel,
                conversation_key="conversation-a",
                task_key="task-a",
                repository_alias="repo-a",
                repository_identity_sha256=self._sha("repository-a"),
                head_sha="a" * 40,
                snapshot_sha256=self._sha("snapshot-a"),
                required_capabilities=("git.diff",),
            )
        self.assertNotIn(sentinel, str(raised.exception))

        bundle_id = int(self._bundle("polluted")["id"])
        with database.connect() as connection:
            connection.execute("drop trigger trg_code_evidence_bundles_guarded_update")
            connection.execute(
                "update code_evidence_bundles set status = 'sealed', seal_sha256 = '' where id = ?",
                (bundle_id,),
            )
        with self.assertRaisesRegex(ValueError, "code_evidence_storage_invalid") as storage_error:
            self.repository.get_bundle(bundle_id)
        self.assertNotIn(sentinel, str(storage_error.exception))

    def test_evidence_set_members_are_unique_append_only_and_cross_checked(self) -> None:
        first = self._bundle("set-a")
        second = self.repository.create_bundle(
            bundle_key="bundle-set-b",
            conversation_key="conversation-a",
            task_key="task-a",
            repository_alias="repo-b",
            repository_identity_sha256=self._sha("repository-b"),
            head_sha="b" * 40,
            snapshot_sha256=self._sha("snapshot-b"),
            required_capabilities=("git.diff", "source.read"),
        )
        evidence_set = self.repository.create_evidence_set(
            set_key="set-a",
            conversation_key="conversation-a",
            required_repository_count=2,
        )
        set_id = int(evidence_set["id"])
        self.repository.append_set_member(set_id, repository_alias="repo-a", bundle_id=int(first["id"]), ordinal=1)
        self.repository.append_set_member(set_id, repository_alias="repo-b", bundle_id=int(second["id"]), ordinal=2)
        self.repository.seal_evidence_set(set_id, seal_sha256=self._sha("set-seal"))

        record = self.repository.get_evidence_set(set_id)
        self.assertEqual(["repo-a", "repo-b"], [item["repository_alias"] for item in record["members"]])
        with self.assertRaisesRegex(ValueError, "code_evidence_state_invalid"):
            self.repository.append_set_member(set_id, repository_alias="repo-c", bundle_id=int(first["id"]), ordinal=3)
        with database.connect() as connection:
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("delete from code_evidence_set_members")

    def test_safe_payloads_are_canonical_and_duplicate_or_secret_shapes_are_rejected(self) -> None:
        bundle_id = int(self._bundle("json")["id"])
        event = self.repository.append_event(
            bundle_id,
            event_type="capture_progress",
            status="running",
            details={"count": 2, "complete": False, "kind": "diff"},
        )
        self.assertEqual(
            '{"complete":false,"count":2,"kind":"diff"}',
            event["details_json"],
        )
        with self.assertRaisesRegex(ValueError, "code_evidence_input_invalid"):
            self.repository.append_event(
                bundle_id,
                event_type="capture_progress",
                status="running",
                details={"authorization": "token=SENTINEL"},
            )


if __name__ == "__main__":
    unittest.main()
