from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app import database
from app.change_context_artifacts import ChangeContextArtifactStore
from app.change_context_contracts import (
    ChangeContextGateResult,
    ChangeContextLayer,
    ChangeContextPack,
    TaskBinding,
    content_hash,
)
from app.change_context_repository import ChangeContextRepository


SHA_A = "sha256:" + "a" * 64


class ChangeContextRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.db_path = root / "harness.sqlite"
        self.factory = lambda: database.connect_database(self.db_path)
        database.init_db(connection_factory=self.factory)
        self.store = ChangeContextArtifactStore(root / "artifacts")
        self.repository = ChangeContextRepository(self.factory, self.store)

    def _layer(self, layer_type: str, *, status: str = "complete") -> tuple[ChangeContextLayer, dict]:
        payload = {"facts": {"layer": layer_type}, "missing": [], "conflicts": []}
        layer = ChangeContextLayer.create(
            layer_type=layer_type,
            status=status,
            payload=payload,
            source_fingerprint=SHA_A,
            artifact_ref=self.store.reference_for_payload(payload),
            evidence_refs=(f"evidence://{layer_type}/one",),
            policy_rule_ids=("CTX-BASE-001",),
            blockers=(),
        )
        return layer, payload

    def _pack(self, *, version: int = 1, status: str = "ready", supersedes: str = "") -> ChangeContextPack:
        pairs = [self._layer(name, status="not_applicable" if name == "data_graph" else "complete") for name in ("project_graph", "change_scope", "code_graph", "data_graph")]
        for layer, payload in pairs:
            self.repository.persist_layer(layer, payload)
        gate = ChangeContextGateResult("ready", "CHANGE_CONTEXT_READY", (), (), ())
        return ChangeContextPack.create(
            pack_version=version,
            status=status,
            task_binding=TaskBinding("LOCAL", "LOCAL-1", f"rev-{version}", SHA_A),
            required_layers=("project_graph", "change_scope", "code_graph"),
            layers=tuple(layer for layer, _ in pairs),
            gate=gate,
            supersedes_pack_id=supersedes,
        )

    def test_layer_deduplication_reuses_verified_artifact_without_overwrite(self) -> None:
        layer, payload = self._layer("project_graph")
        first = self.repository.persist_layer(layer, payload)
        second = self.repository.persist_layer(layer, payload)
        self.assertEqual(first, second)
        with closing(self.factory()) as connection:
            self.assertEqual(1, connection.execute("select count(*) from change_context_layers").fetchone()[0])
            self.assertEqual(1, connection.execute("select count(*) from change_context_layer_artifacts").fetchone()[0])

    def test_pack_round_trip_binds_exact_layers_gate_and_hashes(self) -> None:
        pack = self._pack()
        self.repository.create_pack_snapshot(pack)
        reopened = self.repository.get_pack(pack.pack_id)
        self.assertEqual(pack, reopened)
        for layer in reopened.layers:
            persisted, payload = self.repository.get_layer(layer.layer_id)
            self.assertEqual(layer, persisted)
            self.assertEqual(layer.content_hash, content_hash(payload))

    def test_repository_tables_are_append_only(self) -> None:
        pack = self._pack()
        self.repository.create_pack_snapshot(pack)
        with closing(self.factory()) as connection:
            for statement in (
                "update change_context_packs set status='blocked' where pack_id=?",
                "delete from change_context_gate_results where pack_id=?",
            ):
                with self.subTest(statement=statement):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(statement, (pack.pack_id,))

    def test_invalid_supersession_transition_and_skipped_version_are_rejected(self) -> None:
        ready = self._pack()
        self.repository.create_pack_snapshot(ready)
        skipped = self._pack(version=3, supersedes=ready.pack_id)
        with self.assertRaisesRegex(ValueError, "change_context_pack_version_edge_invalid"):
            self.repository.create_pack_snapshot(skipped)
        ready_v2 = self._pack(version=2, supersedes=ready.pack_id)
        with self.assertRaisesRegex(ValueError, "change_context_pack_transition_invalid"):
            self.repository.create_pack_snapshot(ready_v2)

    def test_projection_metrics_and_events_are_append_only_and_bounded(self) -> None:
        pack = self._pack()
        self.repository.create_pack_snapshot(pack)
        self.repository.record_projection_metric(
            pack_id=pack.pack_id,
            role="implementation",
            projection_hash=SHA_A,
            raw_bytes=110_000,
            projected_bytes=8_000,
            reused_layer_count=4,
            recollected_layer_count=0,
            evidence_refs_opened=2,
            reported_model_tokens=1_500,
        )
        self.repository.append_event(pack.pack_id, "projection", {"projection_hash": SHA_A})
        with closing(self.factory()) as connection:
            self.assertEqual(1, connection.execute("select count(*) from change_context_projection_metrics").fetchone()[0])
            self.assertGreaterEqual(connection.execute("select count(*) from change_context_events").fetchone()[0], 2)

    def test_missing_and_corrupt_artifacts_have_distinct_failures(self) -> None:
        layer, payload = self._layer("project_graph")
        self.repository.persist_layer(layer, payload)
        path = self.store.path_for(layer.content_hash)
        path.rename(path.with_suffix(".missing"))
        with self.assertRaisesRegex(ValueError, "change_context_artifact_missing"):
            self.repository.get_layer(layer.layer_id)

    def test_supported_legacy_versions_migrate_to_v73_without_losing_sentinel(self) -> None:
        for version in (69, 70, 71, 72):
            with self.subTest(version=version):
                path = Path(self.temporary.name) / f"legacy-{version}.sqlite"
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute("create table sentinel(value text not null)")
                    connection.execute("insert into sentinel values(?)", (f"v{version}",))
                    connection.execute(f"pragma user_version = {version}")
                factory = lambda path=path: database.connect_database(path)
                database.init_db(connection_factory=factory)
                with closing(factory()) as connection:
                    self.assertEqual(73, int(connection.execute("pragma user_version").fetchone()[0]))
                    self.assertEqual(f"v{version}", connection.execute("select value from sentinel").fetchone()[0])
                    marker = connection.execute(
                        "select migration_name from harness_schema_migrations where from_version=? and to_version=73",
                        (version,),
                    ).fetchone()
                    self.assertEqual("v0.73-change-context-pack", marker[0])


if __name__ == "__main__":
    unittest.main()
