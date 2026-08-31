from __future__ import annotations

import json
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from app.capability_contracts import CapabilityResult, MutationLevel
from app.change_context_contracts import (
    CHANGE_CONTEXT_LAYER_SCHEMA_VERSION,
    CHANGE_CONTEXT_PACK_SCHEMA_VERSION,
    CHANGE_CONTEXT_PROJECTION_SCHEMA_VERSION,
    ChangeContextGateResult,
    ChangeContextLayer,
    ChangeContextPack,
    ChangeContextProjection,
    EvidenceReference,
    McpEvidenceReceipt,
    TaskBinding,
    canonical_json_bytes,
    content_hash,
    layer_id,
    pack_id,
)


ROOT = Path(__file__).parents[1]
SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64


def evidence(name: str = "project") -> EvidenceReference:
    return EvidenceReference(
        ref=f"evidence://{name}/one",
        kind=name,
        source="local",
        content_hash=SHA_A,
    )


def layer(kind: str, *, status: str = "complete") -> ChangeContextLayer:
    payload = {
        "facts": {"name": kind, "targets": ["repo:src/main.py"]},
        "missing": [] if status in {"complete", "not_applicable"} else ["source"],
        "conflicts": [],
    }
    return ChangeContextLayer.create(
        layer_type=kind,
        status=status,
        payload=payload,
        source_fingerprint=SHA_B,
        artifact_ref=f"artifact://change-context/{kind}",
        evidence_refs=(evidence(kind).ref,),
        policy_rule_ids=("CTX-BASE-001",),
        blockers=() if status in {"complete", "not_applicable"} else ("source",),
    )


def ready_pack(*, pack_version: int = 1, supersedes_pack_id: str = "") -> ChangeContextPack:
    layers = (
        layer("project_graph"),
        layer("change_scope"),
        layer("code_graph"),
        layer("data_graph", status="not_applicable"),
    )
    return ChangeContextPack.create(
        pack_version=pack_version,
        status="ready",
        task_binding=TaskBinding("LOCAL", "LOCAL-1", "rev-1", SHA_A),
        required_layers=("project_graph", "change_scope", "code_graph"),
        layers=layers,
        gate=ChangeContextGateResult("ready", "CHANGE_CONTEXT_READY", (), (), ()),
        supersedes_pack_id=supersedes_pack_id,
    )


class ChangeContextContractTests(unittest.TestCase):
    def test_canonical_hashes_are_order_independent_and_ignore_audit_time(self) -> None:
        first = {"b": [2, 1], "a": {"x": True}, "collection_time": "2026-08-30T00:00:00Z"}
        second = {"a": {"x": True}, "collection_time": "2027-01-01T00:00:00Z", "b": [2, 1]}

        self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
        self.assertEqual(content_hash(first), content_hash(second))
        self.assertEqual(layer_id(first), layer_id(second))
        self.assertEqual(pack_id(first), pack_id(second))

    def test_hash_input_rejects_non_json_nan_secrets_and_raw_rows(self) -> None:
        invalid = (
            {"value": float("nan")},
            {"password": "hidden"},
            {"credential": "jdbc:postgresql://db/prod"},
            {"Authorization": "Bearer abc"},
            {"raw_envelope": {"ok": True}},
            {"business_rows": [{"patient": "P1"}]},
            {"private": "-----BEGIN PRIVATE KEY-----"},
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    content_hash(value)

    def test_evidence_reference_is_frozen_and_strict(self) -> None:
        item = evidence()
        self.assertEqual(item, EvidenceReference.from_dict(item.to_dict()))
        with self.assertRaises(FrozenInstanceError):
            item.kind = "changed"  # type: ignore[misc]
        malformed = item.to_dict()
        malformed["extra"] = True
        with self.assertRaises(ValueError):
            EvidenceReference.from_dict(malformed)

    def test_mcp_receipt_is_metadata_only_strict_and_excludes_collection_time_from_identity(self) -> None:
        result = CapabilityResult(
            request_id="request-1",
            capability="workitem.read",
            provider="yunxiao",
            status="success",
            mutation_level=MutationLevel.L1,
            changed=False,
            summary="ok",
            data={"item": {"id": "DFHIS-1"}},
            evidence=({"ref": "mcp-evidence:request-1:abc123"},),
            warnings=(),
            blockers=(),
            audit={
                "execution_kind": "mcp",
                "source_identity": "yunxiao:DFHIS-1",
                "source_version": "rev-1",
                "freshness_status": "fresh",
                "freshness_expires_at": "2026-08-30T01:00:00Z",
                "collected_at": "2026-08-30T00:00:00Z",
            },
        )
        receipt = McpEvidenceReceipt.from_capability_result(result)
        self.assertTrue(receipt.is_current)
        self.assertEqual(receipt, McpEvidenceReceipt.from_dict(receipt.to_dict()))
        changed_time = receipt.to_dict()
        changed_time["collected_at"] = "2026-08-30T00:05:00Z"
        self.assertEqual(receipt.identity_payload(), McpEvidenceReceipt.from_dict(changed_time).identity_payload())
        malformed = receipt.to_dict()
        malformed["execution_kind"] = "provider"
        with self.assertRaises(ValueError):
            McpEvidenceReceipt.from_dict(malformed)
        malformed = receipt.to_dict()
        malformed["raw_envelope"] = {"token": "secret"}
        with self.assertRaises(ValueError):
            McpEvidenceReceipt.from_dict(malformed)

    def test_layer_round_trip_rejects_unknown_version_status_and_tampered_id(self) -> None:
        item = layer("project_graph")
        self.assertEqual(CHANGE_CONTEXT_LAYER_SCHEMA_VERSION, item.schema_version)
        self.assertEqual(item, ChangeContextLayer.from_dict(item.to_dict()))
        for field, value in (
            ("schema_version", "change-context-layer.v2"),
            ("status", "ready"),
            ("layer_id", "ccl:sha256:" + "0" * 64),
        ):
            malformed = item.to_dict()
            malformed[field] = value
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    ChangeContextLayer.from_dict(malformed)

    def test_pack_round_trip_requires_all_layers_no_duplicates_and_immediate_supersession(self) -> None:
        item = ready_pack()
        self.assertEqual(CHANGE_CONTEXT_PACK_SCHEMA_VERSION, item.schema_version)
        self.assertEqual(item, ChangeContextPack.from_dict(item.to_dict()))

        missing = item.to_dict()
        missing["layers"] = missing["layers"][:-1]
        with self.assertRaises(ValueError):
            ChangeContextPack.from_dict(missing)

        duplicate = item.to_dict()
        duplicate["layers"][3] = duplicate["layers"][0]
        with self.assertRaises(ValueError):
            ChangeContextPack.from_dict(duplicate)

        with self.assertRaises(ValueError):
            ready_pack(pack_version=2)
        with self.assertRaises(ValueError):
            ready_pack(pack_version=1, supersedes_pack_id="ccp:sha256:" + "1" * 64)

    def test_pack_semantic_id_changes_with_gate_source_and_supersession(self) -> None:
        item = ready_pack()
        changed = item.to_dict()
        changed["gate"]["status"] = "blocked"
        changed["gate"]["code"] = "BLOCKED_CONTEXT_CONFLICT"
        changed["gate"]["conflicts"] = ["dependency"]
        changed["status"] = "blocked"
        changed["pack_id"] = ""
        rebuilt = ChangeContextPack.create_from_dict(changed)
        self.assertNotEqual(item.pack_id, rebuilt.pack_id)

    def test_projection_is_strict_frozen_and_hash_bound(self) -> None:
        item = ChangeContextProjection.create(
            pack_id=ready_pack().pack_id,
            role="implementation",
            tier0={"gate": "ready"},
            tier1={"allowed_paths": ["app/main.py"]},
            opened_evidence_refs=("evidence://code/one",),
        )
        self.assertEqual(CHANGE_CONTEXT_PROJECTION_SCHEMA_VERSION, item.schema_version)
        self.assertEqual(item, ChangeContextProjection.from_dict(item.to_dict()))
        with self.assertRaises(TypeError):
            item.tier0["gate"] = "blocked"  # type: ignore[index]
        malformed = item.to_dict()
        malformed["role"] = "admin"
        with self.assertRaises(ValueError):
            ChangeContextProjection.from_dict(malformed)
        malformed = item.to_dict()
        malformed["projection_hash"] = SHA_A
        with self.assertRaises(ValueError):
            ChangeContextProjection.from_dict(malformed)

    def test_public_json_schemas_are_strict_and_validate_round_trips(self) -> None:
        schema_names = (
            "change_context_layer.v1.json",
            "change_context_pack.v1.json",
            "change_context_projection.v1.json",
        )
        for name in schema_names:
            schema = json.loads((ROOT / "config/schemas" / name).read_text(encoding="utf-8"))
            self._assert_every_object_is_closed(schema, path=name)
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema is optional")
        fixtures = {
            "change_context_layer.v1.json": layer("project_graph").to_dict(),
            "change_context_pack.v1.json": ready_pack().to_dict(),
            "change_context_projection.v1.json": ChangeContextProjection.create(
                pack_id=ready_pack().pack_id,
                role="review",
                tier0={"gate": "ready"},
                tier1={"tests": ["unit"]},
                opened_evidence_refs=(),
            ).to_dict(),
        }
        for name, fixture in fixtures.items():
            with self.subTest(schema=name):
                schema = json.loads((ROOT / "config/schemas" / name).read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(schema)
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual([], list(Draft202012Validator(schema).iter_errors(fixture)))
                fixture["unexpected"] = True
                self.assertTrue(list(Draft202012Validator(schema).iter_errors(fixture)))

    def _assert_every_object_is_closed(self, value: object, *, path: str) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object":
                self.assertIs(value.get("additionalProperties"), False, path)
            for key, child in value.items():
                self._assert_every_object_is_closed(child, path=f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                self._assert_every_object_is_closed(child, path=f"{path}[{index}]")


if __name__ == "__main__":
    unittest.main()
