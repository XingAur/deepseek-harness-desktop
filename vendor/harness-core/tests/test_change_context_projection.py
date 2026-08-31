from __future__ import annotations

import unittest

from app.change_context_contracts import (
    ChangeContextGateResult,
    ChangeContextLayer,
    ChangeContextPack,
    TaskBinding,
    content_hash,
)
from app.change_context_projection import (
    ROLES,
    TIER0_MAX_BYTES,
    TIER1_MAX_BYTES,
    ChangeContextProjectionError,
    ChangeContextProjectionService,
    canonical_projection_bytes,
    enforce_projection_budget,
)


class MetricRepository:
    def __init__(self) -> None:
        self.metrics = []

    def record_projection_metric(self, **kwargs) -> None:
        self.metrics.append(kwargs)


def fixture(*, large_noise: int = 0):
    payloads = {
        "project_graph": {
            "schema_version": "project-graph.v1",
            "projects": [{"name": "web", "role": "frontend", "exists": True}],
            "relationships": [{"source": "web", "target": "api", "kind": "service_call", "endpoint": "listPatients"}],
            "explicit_scope": True,
        },
        "change_scope": {
            "schema_version": "change-scope.v1",
            "task_intent_hash": "intent",
            "provider": "manual",
            "ticket_id": "LOCAL-1",
            "requirement_revision": "rev-1",
            "current_user_correction": "只调整列表",
            "calibrated_scope": {"do": "列表", "do_not": ["接口"]},
        },
        "code_graph": {
            "schema_version": "code-graph.v1",
            "target_paths": ["src/pages/list.vue"],
            "tests": ["tests/list.test.js"],
            "call_edges": [{"source_path": "src/pages/list.vue", "target_path": "src/api/list.js", "identifier": "listPatients", "kind": "request"}],
            "file_hashes": [],
            "full_source": "x" * large_noise,
            "credential_path": "/private/credentials.json",
        },
        "data_graph": {
            "schema_version": "data-graph.v1",
            "schema": "public",
            "tables": [{"name": "patient", "columns": [{"name": "id", "type": "bigint", "nullable": False}]}],
            "mcp_receipts": [],
        },
    }
    layers = []
    for kind, payload in payloads.items():
        digest = content_hash(payload)
        layers.append(
            ChangeContextLayer.create(
                layer_type=kind,
                status="complete",
                payload=payload,
                source_fingerprint=digest,
                artifact_ref=f"artifact://sha256/{digest.removeprefix('sha256:')}",
                evidence_refs=(f"evidence://{kind}/one",),
                policy_rule_ids=("CTX-TEST-001",),
                blockers=(),
            )
        )
    pack = ChangeContextPack.create(
        pack_version=1,
        status="ready",
        task_binding=TaskBinding("LOCAL", "LOCAL-1", "rev-1", "sha256:" + "a" * 64),
        required_layers=("project_graph", "change_scope", "code_graph", "data_graph"),
        layers=layers,
        gate=ChangeContextGateResult("ready", "CHANGE_CONTEXT_READY", (), (), ()),
    )
    return pack, payloads


class ChangeContextProjectionTests(unittest.TestCase):
    def test_all_roles_use_explicit_tier1_field_allowlists(self) -> None:
        pack, payloads = fixture()
        service = ChangeContextProjectionService()
        expected = {
            "manager": {"project_relationships", "requirement_scope", "boundaries", "tests", "evidence_refs"},
            "analysis": {"project_relationships", "requirement_scope", "entry_points", "allowed_paths", "call_chain", "data_contracts", "boundaries", "tests", "evidence_refs"},
            "implementation": {"allowed_paths", "call_chain", "data_contracts", "tests", "change_contract", "evidence_refs"},
            "review": {"allowed_paths", "call_chain", "data_contracts", "tests", "diff_evidence_refs", "verification_evidence_refs", "evidence_refs"},
            "knowledge_answer": {"knowledge_summary", "boundaries", "evidence_refs"},
        }
        self.assertEqual(set(expected), set(ROLES))
        for role, fields in expected.items():
            with self.subTest(role=role):
                projection = service.render(pack=pack, layer_payloads=payloads, role=role)
                self.assertEqual(fields, set(projection.tier1))
                self.assertEqual(pack.pack_id, projection.tier0["pack_id"])
                self.assertEqual("CHANGE_CONTEXT_READY", projection.tier0["gate_code"])

    def test_utf8_byte_budgets_accept_exact_boundary_and_reject_one_more(self) -> None:
        def exact_payload(limit: int):
            base = len(canonical_projection_bytes({"gate": ""}))
            return {"gate": "x" * (limit - base)}

        tier0 = exact_payload(TIER0_MAX_BYTES)
        tier1 = exact_payload(TIER1_MAX_BYTES)
        self.assertEqual(TIER0_MAX_BYTES, len(canonical_projection_bytes(tier0)))
        self.assertEqual(TIER1_MAX_BYTES, len(canonical_projection_bytes(tier1)))
        enforce_projection_budget(tier0, maximum=TIER0_MAX_BYTES)
        enforce_projection_budget(tier1, maximum=TIER1_MAX_BYTES)
        with self.assertRaisesRegex(ChangeContextProjectionError, "BLOCKED_CONTEXT_PROJECTION_BUDGET"):
            enforce_projection_budget({"gate": tier0["gate"] + "x"}, maximum=TIER0_MAX_BYTES)
        with self.assertRaisesRegex(ChangeContextProjectionError, "BLOCKED_CONTEXT_PROJECTION_BUDGET"):
            enforce_projection_budget({"gate": tier1["gate"] + "x"}, maximum=TIER1_MAX_BYTES)

    def test_large_evidence_is_reduced_by_at_least_eighty_percent_with_required_facts(self) -> None:
        pack, payloads = fixture(large_noise=110_000)
        repository = MetricRepository()
        projection = ChangeContextProjectionService(repository=repository).render(
            pack=pack,
            layer_payloads=payloads,
            role="analysis",
            reused_layer_count=4,
            recollected_layer_count=0,
            reported_model_tokens=321,
        )
        metric = repository.metrics[0]
        self.assertLessEqual(metric["projected_bytes"], metric["raw_bytes"] * 0.2)
        for field in ("pack_id", "gate_status", "gate_code", "required_layers", "missing", "conflicts"):
            self.assertIn(field, projection.tier0)
        for field in ("allowed_paths", "call_chain", "data_contracts", "tests"):
            self.assertTrue(projection.tier1[field])
        rendered = str(projection.to_dict())
        self.assertNotIn("full_source", rendered)
        self.assertNotIn("credentials.json", rendered)
        self.assertEqual(4, metric["reused_layer_count"])
        self.assertEqual(321, metric["reported_model_tokens"])

    def test_hash_mismatch_and_required_fact_overflow_block_instead_of_truncating(self) -> None:
        pack, payloads = fixture()
        tampered = dict(payloads)
        tampered["code_graph"] = {**payloads["code_graph"], "target_paths": ["src/other.vue"]}
        with self.assertRaisesRegex(ChangeContextProjectionError, "BLOCKED_CONTEXT_HASH_MISMATCH"):
            ChangeContextProjectionService().render(pack=pack, layer_payloads=tampered, role="implementation")
        too_many = dict(payloads)
        too_many["code_graph"] = {**payloads["code_graph"], "target_paths": [f"src/{index}-" + "x" * 500 for index in range(40)]}
        # Rebind the layer so the budget, not hash validation, is the blocker.
        changed_payload = too_many["code_graph"]
        changed_layer = ChangeContextLayer.create(
            layer_type="code_graph", status="complete", payload=changed_payload,
            source_fingerprint=content_hash(changed_payload),
            artifact_ref="artifact://change-context/overflow", evidence_refs=("evidence://code/overflow",),
            policy_rule_ids=("CTX-TEST-001",), blockers=(),
        )
        layers = tuple(changed_layer if item.layer_type == "code_graph" else item for item in pack.layers)
        changed_pack = ChangeContextPack.create(
            pack_version=1, status="ready", task_binding=pack.task_binding,
            required_layers=pack.required_layers, layers=layers, gate=pack.gate,
        )
        with self.assertRaisesRegex(ChangeContextProjectionError, "BLOCKED_CONTEXT_PROJECTION_BUDGET"):
            ChangeContextProjectionService().render(pack=changed_pack, layer_payloads=too_many, role="implementation")


if __name__ == "__main__":
    unittest.main()
