from __future__ import annotations

import tempfile
import unittest
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
from app.change_context_gate import ChangeContextGate
from app.change_context_repository import ChangeContextRepository


class ChangeContextGateTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.factory = lambda: database.connect_database(root / "harness.sqlite")
        database.init_db(connection_factory=self.factory)
        self.store = ChangeContextArtifactStore(root / "artifacts")
        self.repository = ChangeContextRepository(self.factory, self.store)
        self.gate = ChangeContextGate()

    def _layer(
        self,
        layer_type: str,
        *,
        status: str = "complete",
        blockers: tuple[str, ...] = (),
        missing: tuple[str, ...] = (),
        conflicts: tuple[str, ...] = (),
    ) -> tuple[ChangeContextLayer, dict[str, object]]:
        payload: dict[str, object] = {
            "facts": {"layer": layer_type},
            "missing": list(missing),
            "conflicts": list(conflicts),
        }
        layer = ChangeContextLayer.create(
            layer_type=layer_type,
            status=status,
            payload=payload,
            source_fingerprint=content_hash(payload),
            artifact_ref=self.store.reference_for_payload(payload),
            evidence_refs=(f"evidence://{layer_type}/one",),
            policy_rule_ids=("CTX-GATE-TEST",),
            blockers=blockers,
        )
        return layer, payload

    def _persisted_pack(
        self,
        *,
        overrides: dict[str, tuple[ChangeContextLayer, dict[str, object]]] | None = None,
    ) -> ChangeContextPack:
        pairs: list[tuple[ChangeContextLayer, dict[str, object]]] = []
        for layer_type in ("project_graph", "change_scope", "code_graph", "data_graph"):
            if overrides and layer_type in overrides:
                pair = overrides[layer_type]
            elif layer_type == "data_graph":
                pair = self._layer(layer_type, status="not_applicable")
            else:
                pair = self._layer(layer_type)
            pairs.append(pair)
            self.repository.persist_layer(*pair)
        layer_payloads = {layer.layer_type: payload for layer, payload in pairs}
        result = self.gate.evaluate_layers(
            layers=tuple(layer for layer, _ in pairs),
            layer_payloads=layer_payloads,
            required_layers=("project_graph", "change_scope", "code_graph"),
        )
        pack = ChangeContextPack.create(
            pack_version=1,
            status=result.status,
            task_binding=TaskBinding("LOCAL", "LOCAL-1", "rev-1", "sha256:" + "a" * 64),
            required_layers=("project_graph", "change_scope", "code_graph"),
            layers=tuple(layer for layer, _ in pairs),
            gate=result,
        )
        self.repository.create_pack_snapshot(pack)
        return pack

    def test_ready_pack_is_reopened_and_verified_before_ready(self) -> None:
        pack = self._persisted_pack()
        self.assertEqual(
            ChangeContextGateResult("ready", "CHANGE_CONTEXT_READY", (), (), ()),
            self.gate.evaluate(pack, self.repository),
        )

    def test_gate_returns_stable_source_conflict_and_incomplete_codes(self) -> None:
        cases = (
            (
                self._layer(
                    "project_graph",
                    status="incomplete",
                    blockers=("BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: gitlab.read failed.",),
                ),
                "BLOCKED_CONTEXT_SOURCE_UNAVAILABLE",
            ),
            (
                self._layer(
                    "project_graph",
                    status="incomplete",
                    blockers=("证据冲突。",),
                    conflicts=("branch identity conflict",),
                ),
                "BLOCKED_CONTEXT_CONFLICT",
            ),
            (
                self._layer(
                    "project_graph",
                    status="incomplete",
                    blockers=("项目关系缺失。",),
                    missing=("service relationship",),
                ),
                "BLOCKED_CONTEXT_INCOMPLETE",
            ),
        )
        for override, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                pack = self._persisted_pack(overrides={"project_graph": override})
                self.assertEqual(expected_code, self.gate.evaluate(pack, self.repository).code)

    def test_stale_layer_and_tampered_pack_are_rejected(self) -> None:
        stale = self._layer("code_graph", status="stale", blockers=("source fingerprint changed",))
        pack = self._persisted_pack(overrides={"code_graph": stale})
        self.assertEqual("BLOCKED_CONTEXT_STALE", self.gate.evaluate(pack, self.repository).code)

        ready = self._persisted_pack()
        forged = ChangeContextPack.create(
            pack_version=1,
            status="ready",
            task_binding=TaskBinding("LOCAL", "LOCAL-1", "rev-2", "sha256:" + "b" * 64),
            required_layers=ready.required_layers,
            layers=ready.layers,
            gate=ready.gate,
        )
        object.__setattr__(forged, "pack_id", ready.pack_id)
        self.assertEqual("BLOCKED_CONTEXT_HASH_MISMATCH", self.gate.evaluate(forged, self.repository).code)


if __name__ == "__main__":
    unittest.main()
