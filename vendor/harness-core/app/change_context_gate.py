from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from app.change_context_contracts import (
    CHANGE_CONTEXT_LAYER_SCHEMA_VERSION,
    CHANGE_CONTEXT_PACK_SCHEMA_VERSION,
    ChangeContextGateResult,
    ChangeContextLayer,
    ChangeContextPack,
    PROJECTION_ROLES,
    content_hash,
)
from app.change_context_projection import ChangeContextProjectionError, ChangeContextProjectionService


class ChangeContextGateRepository(Protocol):
    def get_pack(self, pack_id: str) -> ChangeContextPack: ...

    def get_layer(self, layer_id: str) -> tuple[ChangeContextLayer, dict[str, object]]: ...

    def get_successor_pack_id(self, pack_id: str) -> str: ...


class ChangeContextGate:
    """Pure, fail-closed verifier over persisted ChangeContext artifacts."""

    def evaluate(
        self,
        pack: ChangeContextPack,
        repository: ChangeContextGateRepository,
    ) -> ChangeContextGateResult:
        if not isinstance(pack, ChangeContextPack) or pack.schema_version != CHANGE_CONTEXT_PACK_SCHEMA_VERSION:
            return _blocked("BLOCKED_CONTEXT_VERSION_MISMATCH", blockers=("pack schema version mismatch",))
        try:
            persisted = repository.get_pack(pack.pack_id)
        except KeyError:
            return _blocked("BLOCKED_CONTEXT_INCOMPLETE", missing=("persisted pack",))
        except ValueError as error:
            return _repository_failure(error)
        if persisted != pack:
            return _blocked("BLOCKED_CONTEXT_HASH_MISMATCH", blockers=("persisted pack identity mismatch",))
        try:
            successor_pack_id = repository.get_successor_pack_id(pack.pack_id)
        except ValueError as error:
            return _repository_failure(error)
        if successor_pack_id:
            return _blocked(
                "BLOCKED_CONTEXT_STALE",
                blockers=(f"pack superseded by {successor_pack_id}",),
            )
        if pack.status in {"stale", "superseded"}:
            return _blocked("BLOCKED_CONTEXT_STALE", blockers=(f"pack status is {pack.status}",))

        payloads: dict[str, Mapping[str, object]] = {}
        reopened_layers: list[ChangeContextLayer] = []
        for manifest_layer in pack.layers:
            try:
                layer, payload = repository.get_layer(manifest_layer.layer_id)
            except KeyError:
                return _blocked("BLOCKED_CONTEXT_INCOMPLETE", missing=(manifest_layer.layer_type,))
            except ValueError as error:
                return _repository_failure(error)
            if layer != manifest_layer or content_hash(payload) != manifest_layer.content_hash:
                return _blocked(
                    "BLOCKED_CONTEXT_HASH_MISMATCH",
                    blockers=(f"layer hash mismatch: {manifest_layer.layer_type}",),
                )
            reopened_layers.append(layer)
            payloads[layer.layer_type] = payload

        result = self.evaluate_layers(
            layers=tuple(reopened_layers),
            layer_payloads=payloads,
            required_layers=pack.required_layers,
        )
        if result.status != "ready":
            if pack.status == "blocked" and pack.gate == result:
                return result
            return _blocked("BLOCKED_CONTEXT_HASH_MISMATCH", blockers=("persisted gate result mismatch",))
        if pack.status == "blocked" and pack.gate.code == "BLOCKED_CONTEXT_PROJECTION_BUDGET":
            ready_probe = ChangeContextPack.create(
                pack_version=pack.pack_version,
                status="ready",
                task_binding=pack.task_binding,
                required_layers=pack.required_layers,
                layers=pack.layers,
                gate=result,
                supersedes_pack_id=pack.supersedes_pack_id,
            )
            budget_result = self.evaluate_projection_budgets(pack=ready_probe, layer_payloads=payloads)
            if budget_result == pack.gate:
                return budget_result
            return _blocked("BLOCKED_CONTEXT_HASH_MISMATCH", blockers=("persisted projection gate mismatch",))
        if pack.status != "ready" or pack.gate != result:
            return _blocked("BLOCKED_CONTEXT_HASH_MISMATCH", blockers=("persisted gate result mismatch",))
        return self.evaluate_projection_budgets(pack=pack, layer_payloads=payloads)

    def evaluate_layers(
        self,
        *,
        layers: Sequence[ChangeContextLayer],
        layer_payloads: Mapping[str, Mapping[str, object]],
        required_layers: Sequence[str],
    ) -> ChangeContextGateResult:
        by_type = {layer.layer_type: layer for layer in layers if isinstance(layer, ChangeContextLayer)}
        required = tuple(required_layers)
        if len(by_type) != 4 or set(layer_payloads) != set(by_type):
            return _blocked("BLOCKED_CONTEXT_INCOMPLETE", missing=("four context layers",))
        if any(layer.schema_version != CHANGE_CONTEXT_LAYER_SCHEMA_VERSION for layer in by_type.values()):
            return _blocked("BLOCKED_CONTEXT_VERSION_MISMATCH", blockers=("layer schema version mismatch",))
        for layer_type, layer in by_type.items():
            payload = layer_payloads.get(layer_type)
            if not isinstance(payload, Mapping) or content_hash(payload) != layer.content_hash:
                return _blocked("BLOCKED_CONTEXT_HASH_MISMATCH", blockers=(f"layer hash mismatch: {layer_type}",))

        stale = tuple(layer_type for layer_type in required if by_type[layer_type].status == "stale")
        if stale:
            return _blocked("BLOCKED_CONTEXT_STALE", blockers=tuple(f"stale layer: {item}" for item in stale))

        missing: list[str] = []
        conflicts: list[str] = []
        blockers: list[str] = []
        for layer_type in by_type:
            layer = by_type[layer_type]
            payload = layer_payloads[layer_type]
            blockers.extend(layer.blockers)
            missing.extend(_text_items(payload.get("missing")))
            conflicts.extend(_text_items(payload.get("conflicts")))
            if layer_type in required and layer.status != "complete":
                missing.append(layer_type)
            if layer_type not in required and layer_type == "data_graph" and layer.status != "not_applicable":
                missing.append("data_graph:not_applicable")

        unique_blockers = _unique(blockers)
        unique_missing = _unique(missing)
        unique_conflicts = _unique(conflicts)
        if any("BLOCKED_CONTEXT_SOURCE_UNAVAILABLE" in item for item in unique_blockers):
            return _blocked(
                "BLOCKED_CONTEXT_SOURCE_UNAVAILABLE",
                missing=unique_missing,
                conflicts=unique_conflicts,
                blockers=unique_blockers,
            )
        if unique_conflicts:
            return _blocked(
                "BLOCKED_CONTEXT_CONFLICT",
                missing=unique_missing,
                conflicts=unique_conflicts,
                blockers=unique_blockers or ("context evidence conflict",),
            )
        if unique_missing or unique_blockers:
            return _blocked(
                "BLOCKED_CONTEXT_INCOMPLETE",
                missing=unique_missing,
                blockers=unique_blockers or ("required context incomplete",),
            )
        return ChangeContextGateResult("ready", "CHANGE_CONTEXT_READY", (), (), ())

    @staticmethod
    def evaluate_projection_budgets(
        *,
        pack: ChangeContextPack,
        layer_payloads: Mapping[str, Mapping[str, object]],
    ) -> ChangeContextGateResult:
        projection_service = ChangeContextProjectionService()
        try:
            for role in sorted(PROJECTION_ROLES):
                projection_service.render(pack=pack, layer_payloads=layer_payloads, role=role)
        except ChangeContextProjectionError as error:
            code = str(error)
            if code not in {"BLOCKED_CONTEXT_PROJECTION_BUDGET", "BLOCKED_CONTEXT_HASH_MISMATCH"}:
                code = "BLOCKED_CONTEXT_INCOMPLETE"
            return _blocked(code, blockers=(str(error),))
        return ChangeContextGateResult("ready", "CHANGE_CONTEXT_READY", (), (), ())


def _repository_failure(error: ValueError) -> ChangeContextGateResult:
    message = str(error)
    if "version" in message:
        return _blocked("BLOCKED_CONTEXT_VERSION_MISMATCH", blockers=(message,))
    if "hash" in message or "identity" in message or "corrupt" in message:
        return _blocked("BLOCKED_CONTEXT_HASH_MISMATCH", blockers=(message,))
    return _blocked("BLOCKED_CONTEXT_SOURCE_UNAVAILABLE", blockers=(message,))


def _blocked(
    code: str,
    *,
    missing: Sequence[str] = (),
    conflicts: Sequence[str] = (),
    blockers: Sequence[str] = (),
) -> ChangeContextGateResult:
    bounded_missing = _unique(missing)[:64]
    bounded_conflicts = _unique(conflicts)[:64]
    bounded_blockers = _unique(blockers)[:64]
    if not any((bounded_missing, bounded_conflicts, bounded_blockers)):
        bounded_blockers = (code,)
    return ChangeContextGateResult(
        "blocked",
        code,
        bounded_missing,
        bounded_conflicts,
        bounded_blockers,
    )


def _text_items(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item)[:1024] for item in value if isinstance(item, str) and item.strip())


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(item)[:1024] for item in values if str(item).strip()))
