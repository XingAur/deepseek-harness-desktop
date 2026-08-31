from __future__ import annotations

from app.change_context_applicability import (
    ApplicabilityAssessment,
    LayerApplicabilityDecision,
)
from app.change_context_contracts import (
    ChangeContextGateResult,
    ChangeContextLayer,
    ChangeContextPack,
    TaskBinding,
    content_hash,
)
from app.change_context_gate import ChangeContextGate
from app.change_context_projection import ChangeContextProjectionService
from app.change_context_service import ChangeContextBuildResult


class _StaticRepository:
    def __init__(self, pack: ChangeContextPack, payloads: dict[str, dict[str, object]]) -> None:
        self.pack = pack
        self.successor_pack_id = ""
        self.layers = {
            layer.layer_id: (layer, payloads[layer.layer_type])
            for layer in pack.layers
        }

    def get_pack(self, pack_id: str) -> ChangeContextPack:
        if pack_id != self.pack.pack_id:
            raise KeyError(pack_id)
        return self.pack

    def get_layer(self, layer_id: str):
        if layer_id not in self.layers:
            raise KeyError(layer_id)
        return self.layers[layer_id]

    def get_successor_pack_id(self, pack_id: str) -> str:
        if pack_id != self.pack.pack_id:
            raise KeyError(pack_id)
        return self.successor_pack_id

    def record_projection_metric(self, **kwargs) -> None:
        del kwargs


class ReadyChangeContextService:
    """Deterministic ready fixture for tests whose subject is not collection."""

    def __init__(self) -> None:
        payloads = {
            "project_graph": {
                "schema_version": "project-graph.v1",
                "projects": [{"name": "test-project", "role": "application", "exists": True}],
                "relationships": [],
                "explicit_scope": True,
            },
            "change_scope": {
                "schema_version": "change-scope.v1",
                "provider": "test",
                "ticket_id": "TEST-1",
                "requirement_revision": "test-revision",
                "current_user_correction": "execute the bounded test change",
                "calibrated_scope": {"do": "bounded change", "do_not": []},
            },
            "code_graph": {
                "schema_version": "code-graph.v1",
                "target_paths": ["src/view.vue"],
                "tests": ["tests/view.test.js"],
                "call_edges": [],
                "file_hashes": [],
            },
            "data_graph": {
                "schema_version": "data-graph.v1",
                "decision": "not_applicable",
                "reason": "test fixture is explicitly non-data",
                "missing": [],
                "conflicts": [],
            },
        }
        layers = []
        for layer_type, payload in payloads.items():
            digest = content_hash(payload)
            layers.append(
                ChangeContextLayer.create(
                    layer_type=layer_type,
                    status="not_applicable" if layer_type == "data_graph" else "complete",
                    payload=payload,
                    source_fingerprint=digest,
                    artifact_ref=f"artifact://sha256/{digest.removeprefix('sha256:')}",
                    evidence_refs=(f"evidence://{layer_type}/test",),
                    policy_rule_ids=("CTX-TEST-READY",),
                    blockers=(),
                )
            )
        gate = ChangeContextGateResult("ready", "CHANGE_CONTEXT_READY", (), (), ())
        pack = ChangeContextPack.create(
            pack_version=1,
            status="ready",
            task_binding=TaskBinding("test", "TEST-1", "test-revision", "sha256:" + "a" * 64),
            required_layers=("project_graph", "change_scope", "code_graph"),
            layers=layers,
            gate=gate,
        )
        self.repository = _StaticRepository(pack, payloads)
        self.gate = ChangeContextGate()
        projections = {
            role: ChangeContextProjectionService().render(
                pack=pack,
                layer_payloads=payloads,
                role=role,
            )
            for role in ("manager", "analysis", "implementation", "review", "knowledge_answer")
        }
        decisions = tuple(
            LayerApplicabilityDecision(
                layer_type=layer_type,
                requirement="not_applicable" if layer_type == "data_graph" else "required",
                rule_ids=("CTX-TEST-READY",),
                evidence_refs=(f"evidence://{layer_type}/test",),
                reasons=("test_fixture",),
            )
            for layer_type in ("project_graph", "change_scope", "code_graph", "data_graph")
        )
        self.result = ChangeContextBuildResult(
            pack=pack,
            gate=gate,
            applicability=ApplicabilityAssessment("ready", decisions, (), (), "sha256:" + "b" * 64),
            projections=projections,
            layer_payloads=payloads,
            reused_layer_count=4,
            recollected_layer_count=0,
        )

    def build(self, **kwargs) -> ChangeContextBuildResult:
        del kwargs
        return self.result
