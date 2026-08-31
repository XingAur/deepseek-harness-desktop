from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from typing import Any

from app.change_context_artifacts import ChangeContextArtifactRecord, ChangeContextArtifactStore
from app.change_context_contracts import (
    ChangeContextGateResult,
    ChangeContextLayer,
    ChangeContextPack,
    TaskBinding,
    canonical_json_bytes,
    content_hash,
)
from app.database import now_iso


_TRANSITIONS = frozenset(
    {
        ("collecting", "ready"),
        ("collecting", "blocked"),
        ("collecting", "superseded"),
        ("ready", "stale"),
        ("ready", "superseded"),
        ("blocked", "superseded"),
        ("stale", "superseded"),
        # A superseded tombstone closes the old decision.  The next immutable
        # snapshot starts collection for the corrected decision; without this
        # edge the documented lifecycle has no path from an old ready pack to
        # a new ready pack.
        ("superseded", "collecting"),
    }
)


class ChangeContextRepository:
    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        artifact_store: ChangeContextArtifactStore,
    ) -> None:
        if not callable(connection_factory) or not isinstance(artifact_store, ChangeContextArtifactStore):
            raise ValueError("change_context_repository_configuration_invalid")
        self.connection_factory = connection_factory
        self.artifact_store = artifact_store

    def persist_layer(
        self,
        layer: ChangeContextLayer,
        payload: Mapping[str, object],
    ) -> ChangeContextArtifactRecord:
        if not isinstance(layer, ChangeContextLayer) or layer.content_hash != content_hash(payload):
            raise ValueError("change_context_layer_payload_hash_mismatch")
        expected_ref = self.artifact_store.reference_for_payload(payload)
        if layer.artifact_ref != expected_ref:
            raise ValueError("change_context_layer_artifact_ref_mismatch")
        with closing(self.connection_factory()) as connection:
            existing = connection.execute(
                "select * from change_context_layers where layer_id = ?",
                (layer.layer_id,),
            ).fetchone()
            if existing is not None:
                persisted = self._record_for_content_hash(connection, layer.content_hash)
                self.artifact_store.reopen(persisted)
                if self._layer_from_row(existing) != layer:
                    raise ValueError("change_context_layer_identity_collision")
                return persisted

        try:
            record = self.artifact_store.persist_layer(payload)
        except ValueError as error:
            if str(error) != "change_context_artifact_exists":
                raise
            record = self.artifact_store.inspect_verified(layer.content_hash)

        with closing(self.connection_factory()) as connection, connection:
            connection.execute(
                """
                insert or ignore into change_context_layer_artifacts(
                    content_hash, artifact_ref, relative_path, size_bytes,
                    device, inode, mode, link_count, created_at
                ) values(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.content_hash,
                    record.artifact_ref,
                    record.relative_path,
                    record.size_bytes,
                    record.device,
                    record.inode,
                    record.mode,
                    record.link_count,
                    now_iso(),
                ),
            )
            stored_record = self._record_for_content_hash(connection, layer.content_hash)
            if stored_record != record:
                raise ValueError("change_context_artifact_identity_collision")
            connection.execute(
                """
                insert into change_context_layers(
                    layer_id, schema_version, layer_type, status, content_hash,
                    source_fingerprint, artifact_ref, evidence_refs_json,
                    policy_rule_ids_json, blockers_json, created_at
                ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    layer.layer_id,
                    layer.schema_version,
                    layer.layer_type,
                    layer.status,
                    layer.content_hash,
                    layer.source_fingerprint,
                    layer.artifact_ref,
                    _json(list(layer.evidence_refs)),
                    _json(list(layer.policy_rule_ids)),
                    _json(list(layer.blockers)),
                    now_iso(),
                ),
            )
        return record

    def get_layer(self, layer_id: str) -> tuple[ChangeContextLayer, dict[str, Any]]:
        with closing(self.connection_factory()) as connection:
            row = connection.execute(
                "select * from change_context_layers where layer_id = ?",
                (layer_id,),
            ).fetchone()
            if row is None:
                raise KeyError(layer_id)
            layer = self._layer_from_row(row)
            record = self._record_for_content_hash(connection, layer.content_hash)
        payload = self.artifact_store.reopen(record)
        if content_hash(payload) != layer.content_hash:
            raise ValueError("change_context_hash_mismatch")
        return layer, payload

    def create_pack_snapshot(
        self,
        pack: ChangeContextPack,
        applicability_decisions: Sequence[object] = (),
    ) -> ChangeContextPack:
        if not isinstance(pack, ChangeContextPack):
            raise ValueError("change_context_pack_invalid")
        with closing(self.connection_factory()) as connection, connection:
            if connection.execute("select 1 from change_context_packs where pack_id = ?", (pack.pack_id,)).fetchone():
                raise ValueError("change_context_pack_exists")
            if pack.supersedes_pack_id:
                previous = connection.execute(
                    "select pack_version, status, provider, ticket_id from change_context_packs where pack_id = ?",
                    (pack.supersedes_pack_id,),
                ).fetchone()
                if previous is None:
                    raise ValueError("change_context_pack_supersession_missing")
                if connection.execute(
                    "select 1 from change_context_packs where supersedes_pack_id = ?",
                    (pack.supersedes_pack_id,),
                ).fetchone():
                    raise ValueError("change_context_pack_supersession_fork")
                if pack.pack_version != int(previous["pack_version"]) + 1:
                    raise ValueError("change_context_pack_version_edge_invalid")
                if (str(previous["status"]), pack.status) not in _TRANSITIONS:
                    raise ValueError("change_context_pack_transition_invalid")
                if (str(previous["provider"]), str(previous["ticket_id"])) != (pack.task_binding.provider, pack.task_binding.ticket_id):
                    raise ValueError("change_context_pack_task_edge_invalid")
            elif pack.pack_version != 1:
                raise ValueError("change_context_pack_version_edge_invalid")

            for layer in pack.layers:
                row = connection.execute(
                    "select content_hash from change_context_layers where layer_id = ?",
                    (layer.layer_id,),
                ).fetchone()
                if row is None or str(row["content_hash"]) != layer.content_hash:
                    raise ValueError("change_context_pack_layer_missing")
            connection.execute(
                """
                insert into change_context_packs(
                    pack_id, schema_version, pack_version, status, provider, ticket_id,
                    requirement_revision, request_hash, required_layers_json,
                    supersedes_pack_id, created_at
                ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pack.pack_id,
                    pack.schema_version,
                    pack.pack_version,
                    pack.status,
                    pack.task_binding.provider,
                    pack.task_binding.ticket_id,
                    pack.task_binding.requirement_revision,
                    pack.task_binding.request_hash,
                    _json(list(pack.required_layers)),
                    pack.supersedes_pack_id or None,
                    now_iso(),
                ),
            )
            for ordinal, layer in enumerate(pack.layers):
                connection.execute(
                    "insert into change_context_pack_layers(pack_id, ordinal, layer_type, layer_id, content_hash) values(?, ?, ?, ?, ?)",
                    (pack.pack_id, ordinal, layer.layer_type, layer.layer_id, layer.content_hash),
                )
            gate = pack.gate
            connection.execute(
                "insert into change_context_gate_results(pack_id, status, code, missing_json, conflicts_json, blockers_json, created_at) values(?, ?, ?, ?, ?, ?, ?)",
                (pack.pack_id, gate.status, gate.code, _json(list(gate.missing)), _json(list(gate.conflicts)), _json(list(gate.blockers)), now_iso()),
            )
            for decision in applicability_decisions:
                connection.execute(
                    "insert into change_context_applicability_decisions(pack_id, layer_type, requirement, rule_ids_json, evidence_refs_json, reasons_json, created_at) values(?, ?, ?, ?, ?, ?, ?)",
                    (
                        pack.pack_id,
                        str(getattr(decision, "layer_type")),
                        str(getattr(decision, "requirement")),
                        _json(list(getattr(decision, "rule_ids"))),
                        _json(list(getattr(decision, "evidence_refs"))),
                        _json(list(getattr(decision, "reasons"))),
                        now_iso(),
                    ),
                )
            self._insert_event(connection, pack.pack_id, "pack_snapshot", {"pack_id": pack.pack_id, "status": pack.status})
        return pack

    def get_latest_pack(self, *, provider: str, ticket_id: str) -> ChangeContextPack | None:
        with closing(self.connection_factory()) as connection:
            row = connection.execute(
                """
                select pack_id from change_context_packs
                where provider = ? and ticket_id = ?
                order by pack_version desc, rowid desc limit 1
                """,
                (provider, ticket_id),
            ).fetchone()
        return self.get_pack(str(row["pack_id"])) if row is not None else None

    def get_successor_pack_id(self, pack_id: str) -> str:
        with closing(self.connection_factory()) as connection:
            rows = connection.execute(
                "select pack_id from change_context_packs where supersedes_pack_id = ? order by rowid",
                (pack_id,),
            ).fetchall()
        if len(rows) > 1:
            raise ValueError("change_context_pack_supersession_fork")
        return str(rows[0]["pack_id"]) if rows else ""

    def get_pack(self, pack_id: str) -> ChangeContextPack:
        with closing(self.connection_factory()) as connection:
            row = connection.execute("select * from change_context_packs where pack_id = ?", (pack_id,)).fetchone()
            if row is None:
                raise KeyError(pack_id)
            layer_rows = connection.execute(
                """
                select layers.* from change_context_pack_layers bindings
                join change_context_layers layers on layers.layer_id = bindings.layer_id
                where bindings.pack_id = ? order by bindings.ordinal
                """,
                (pack_id,),
            ).fetchall()
            gate_row = connection.execute("select * from change_context_gate_results where pack_id = ?", (pack_id,)).fetchone()
            if len(layer_rows) != 4 or gate_row is None:
                raise ValueError("change_context_pack_incomplete_storage")
            layers = tuple(self._layer_from_row(item) for item in layer_rows)
            for layer in layers:
                record = self._record_for_content_hash(connection, layer.content_hash)
                payload = self.artifact_store.reopen(record)
                if content_hash(payload) != layer.content_hash:
                    raise ValueError("change_context_hash_mismatch")
            gate = ChangeContextGateResult(
                str(gate_row["status"]),
                str(gate_row["code"]),
                tuple(_list(gate_row["missing_json"])),
                tuple(_list(gate_row["conflicts_json"])),
                tuple(_list(gate_row["blockers_json"])),
            )
            value = {
                "schema_version": str(row["schema_version"]),
                "pack_id": str(row["pack_id"]),
                "pack_version": int(row["pack_version"]),
                "status": str(row["status"]),
                "task_binding": TaskBinding(
                    str(row["provider"]),
                    str(row["ticket_id"]),
                    str(row["requirement_revision"]),
                    str(row["request_hash"]),
                ).to_dict(),
                "required_layers": _list(row["required_layers_json"]),
                "layers": [layer.to_dict() for layer in layers],
                "gate": gate.to_dict(),
                "supersedes_pack_id": str(row["supersedes_pack_id"] or ""),
            }
        return ChangeContextPack.from_dict(value)

    def record_projection_metric(
        self,
        *,
        pack_id: str,
        role: str,
        projection_hash: str,
        raw_bytes: int,
        projected_bytes: int,
        reused_layer_count: int,
        recollected_layer_count: int,
        evidence_refs_opened: int,
        reported_model_tokens: int,
    ) -> None:
        numbers = (raw_bytes, projected_bytes, reused_layer_count, recollected_layer_count, evidence_refs_opened, reported_model_tokens)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in numbers):
            raise ValueError("change_context_projection_metric_invalid")
        with closing(self.connection_factory()) as connection, connection:
            connection.execute(
                """
                insert or ignore into change_context_projection_metrics(
                    pack_id, role, projection_hash, raw_bytes, projected_bytes,
                    reused_layer_count, recollected_layer_count, evidence_refs_opened,
                    reported_model_tokens, created_at
                ) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (pack_id, role, projection_hash, *numbers, now_iso()),
            )

    def append_event(self, pack_id: str, event_type: str, payload: Mapping[str, object]) -> None:
        with closing(self.connection_factory()) as connection, connection:
            self._insert_event(connection, pack_id, event_type, payload)

    def _insert_event(self, connection: sqlite3.Connection, pack_id: str, event_type: str, payload: Mapping[str, object]) -> None:
        payload_json = canonical_json_bytes(payload).decode("utf-8")
        connection.execute(
            "insert into change_context_events(pack_id, event_type, payload_hash, payload_json, created_at) values(?, ?, ?, ?, ?)",
            (pack_id, event_type, content_hash(payload), payload_json, now_iso()),
        )

    def _record_for_content_hash(self, connection: sqlite3.Connection, value: str) -> ChangeContextArtifactRecord:
        row = connection.execute("select * from change_context_layer_artifacts where content_hash = ?", (value,)).fetchone()
        if row is None:
            raise ValueError("change_context_artifact_missing")
        return ChangeContextArtifactRecord(
            content_hash=str(row["content_hash"]),
            artifact_ref=str(row["artifact_ref"]),
            relative_path=str(row["relative_path"]),
            size_bytes=int(row["size_bytes"]),
            device=int(row["device"]),
            inode=int(row["inode"]),
            mode=int(row["mode"]),
            link_count=int(row["link_count"]),
        )

    def _layer_from_row(self, row: sqlite3.Row) -> ChangeContextLayer:
        return ChangeContextLayer.from_dict(
            {
                "schema_version": str(row["schema_version"]),
                "layer_type": str(row["layer_type"]),
                "layer_id": str(row["layer_id"]),
                "status": str(row["status"]),
                "content_hash": str(row["content_hash"]),
                "source_fingerprint": str(row["source_fingerprint"]),
                "artifact_ref": str(row["artifact_ref"]),
                "evidence_refs": _list(row["evidence_refs_json"]),
                "policy_rule_ids": _list(row["policy_rule_ids_json"]),
                "blockers": _list(row["blockers_json"]),
            }
        )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _list(value: object) -> list[str]:
    try:
        result = json.loads(str(value))
    except json.JSONDecodeError:
        raise ValueError("change_context_metadata_invalid") from None
    if not isinstance(result, list) or any(not isinstance(item, str) for item in result):
        raise ValueError("change_context_metadata_invalid")
    return result
