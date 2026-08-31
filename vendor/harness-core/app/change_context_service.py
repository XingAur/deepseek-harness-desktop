from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Protocol

from app.change_context_applicability import (
    ApplicabilityAssessment,
    CandidateTarget,
    ContextApplicabilityGate,
)
from app.change_context_collectors import CollectedContextLayer
from app.change_context_collectors import ChangeScopeCollector, CodeGraphCollector, DataGraphCollector, ProjectGraphCollector
from app.change_context_artifacts import ChangeContextArtifactStore
from app.change_context_contracts import (
    LAYER_TYPES,
    PROJECTION_ROLES,
    ChangeContextGateResult,
    ChangeContextLayer,
    ChangeContextPack,
    ChangeContextProjection,
    McpEvidenceReceipt,
    TaskBinding,
    content_hash,
)
from app.change_context_gate import ChangeContextGate
from app.change_context_projection import ChangeContextProjectionService
from app.change_context_repository import ChangeContextRepository
from app import database
from app.task_context import TaskIntentContext
from app.technical_decision import TechnicalContextDiscovery


class ProjectCollector(Protocol):
    def collect(self, discovery: TechnicalContextDiscovery) -> CollectedContextLayer: ...


class ChangeScopeCollectorProtocol(Protocol):
    def collect(self, **kwargs: object) -> CollectedContextLayer: ...


class DataCollector(Protocol):
    def collect(self, **kwargs: object) -> CollectedContextLayer: ...


@dataclass(frozen=True)
class ChangeContextBuildResult:
    pack: ChangeContextPack
    gate: ChangeContextGateResult
    applicability: ApplicabilityAssessment
    projections: dict[str, ChangeContextProjection]
    layer_payloads: dict[str, dict[str, object]]
    reused_layer_count: int
    recollected_layer_count: int

    def layer(self, layer_type: str) -> ChangeContextLayer:
        for layer in self.pack.layers:
            if layer.layer_type == layer_type:
                return layer
        raise KeyError(layer_type)


class ChangeContextService:
    """Build immutable, content-addressed context before any mutation authority."""

    def __init__(
        self,
        *,
        repository: ChangeContextRepository,
        project_collector: ProjectCollector,
        change_scope_collector: ChangeScopeCollectorProtocol,
        code_collector: ProjectCollector,
        data_collector: DataCollector | None,
        gate: ChangeContextGate,
        projection_service: ChangeContextProjectionService,
        applicability_gate: ContextApplicabilityGate | None = None,
    ) -> None:
        if not isinstance(repository, ChangeContextRepository):
            raise ValueError("change_context_service_repository_invalid")
        self.repository = repository
        self.project_collector = project_collector
        self.change_scope_collector = change_scope_collector
        self.code_collector = code_collector
        self.data_collector = data_collector
        self.gate = gate
        self.projection_service = projection_service
        self.applicability_gate = applicability_gate or ContextApplicabilityGate()

    def build(
        self,
        *,
        discovery: TechnicalContextDiscovery,
        task_context: TaskIntentContext,
        normalized_requirement_evidence: Mapping[str, object],
        current_user_correction: str,
        calibrated_scope: Mapping[str, object],
        candidate_targets: Sequence[CandidateTarget],
        task_id: str,
        run_id: str,
        change_ownership: Mapping[str, object] | None = None,
        project_routing: Mapping[str, object] | None = None,
        mcp_receipt: McpEvidenceReceipt | None = None,
        data_connection_alias: str = "",
        data_schema: str = "",
        data_tables: Sequence[str] = (),
        reuse_pack_id: str = "",
    ) -> ChangeContextBuildResult:
        if not isinstance(discovery, TechnicalContextDiscovery):
            raise ValueError("change_context_discovery_invalid")
        if not isinstance(task_context, TaskIntentContext):
            raise ValueError("change_context_task_intent_invalid")
        if not isinstance(normalized_requirement_evidence, Mapping) or not isinstance(calibrated_scope, Mapping):
            raise ValueError("change_context_scope_input_invalid")
        targets = tuple(candidate_targets)
        applicability = self.applicability_gate.assess(
            task_context=task_context,
            candidate_targets=targets,
            change_ownership=change_ownership,
            project_routing=project_routing,
        )
        task_binding = _task_binding(
            task_context=task_context,
            normalized_requirement_evidence=normalized_requirement_evidence,
            current_user_correction=current_user_correction,
            calibrated_scope=calibrated_scope,
            candidate_targets=targets,
            change_ownership=change_ownership,
            project_routing=project_routing,
            mcp_receipt=mcp_receipt,
            data_connection_alias=data_connection_alias,
            data_schema=data_schema,
            data_tables=data_tables,
            fallback_ticket_id=task_id,
        )
        if reuse_pack_id:
            return self._reuse_ready_pack(
                pack_id=reuse_pack_id,
                expected_binding=task_binding,
                applicability=applicability,
            )

        collected: list[CollectedContextLayer] = [
            self.project_collector.collect(discovery),
            self.change_scope_collector.collect(
                task_context=task_context,
                normalized_requirement_evidence=normalized_requirement_evidence,
                current_user_correction=current_user_correction,
                calibrated_scope=calibrated_scope,
                task_id=task_id,
                run_id=run_id,
                mcp_receipt=mcp_receipt,
            ),
            self.code_collector.collect(discovery),
        ]
        data_decision = applicability.decision("data_graph")
        if data_decision.requirement == "not_applicable":
            collected.append(_not_applicable_data_layer(data_decision, targets))
        elif self.data_collector is None:
            collected.append(_unavailable_data_layer(data_connection_alias, data_schema, data_tables))
        else:
            collected.append(
                self.data_collector.collect(
                    connection_alias=data_connection_alias,
                    schema=data_schema,
                    tables=tuple(data_tables),
                    task_id=task_id,
                    run_id=run_id,
                )
            )
        layers, payloads = self._persist_layers(collected)
        required_layers = tuple(
            decision.layer_type for decision in applicability.decisions if decision.requirement == "required"
        )
        base_gate = self.gate.evaluate_layers(
            layers=layers,
            layer_payloads=payloads,
            required_layers=required_layers,
        )
        previous = self.repository.get_latest_pack(
            provider=task_binding.provider,
            ticket_id=task_binding.ticket_id,
        )
        collection_pack = self._create_collection_snapshot(
            previous=previous,
            task_binding=task_binding,
            required_layers=required_layers,
            layers=layers,
            applicability=applicability,
        )
        final_status = base_gate.status
        final_gate = base_gate
        final_pack = ChangeContextPack.create(
            pack_version=collection_pack.pack_version + 1,
            status=final_status,
            task_binding=task_binding,
            required_layers=required_layers,
            layers=layers,
            gate=final_gate,
            supersedes_pack_id=collection_pack.pack_id,
        )
        if base_gate.status == "ready":
            budget_gate = self.gate.evaluate_projection_budgets(pack=final_pack, layer_payloads=payloads)
            if budget_gate.status == "blocked":
                final_gate = budget_gate
                final_pack = ChangeContextPack.create(
                    pack_version=collection_pack.pack_version + 1,
                    status="blocked",
                    task_binding=task_binding,
                    required_layers=required_layers,
                    layers=layers,
                    gate=final_gate,
                    supersedes_pack_id=collection_pack.pack_id,
                )
        self.repository.create_pack_snapshot(final_pack, applicability.decisions)
        verified_gate = self.gate.evaluate(final_pack, self.repository)
        if verified_gate != final_gate:
            raise ValueError("change_context_gate_persistence_mismatch")

        reused_count = _reused_layer_count(previous, layers)
        projections = self._render_projections(
            pack=final_pack,
            payloads=payloads,
            reused_layer_count=reused_count,
            recollected_layer_count=len(LAYER_TYPES) - reused_count,
        ) if verified_gate.status == "ready" else {}
        return ChangeContextBuildResult(
            pack=final_pack,
            gate=verified_gate,
            applicability=applicability,
            projections=projections,
            layer_payloads=payloads,
            reused_layer_count=reused_count,
            recollected_layer_count=len(LAYER_TYPES) - reused_count,
        )

    def _reuse_ready_pack(
        self,
        *,
        pack_id: str,
        expected_binding: TaskBinding,
        applicability: ApplicabilityAssessment,
    ) -> ChangeContextBuildResult:
        pack = self.repository.get_pack(pack_id)
        gate = self.gate.evaluate(pack, self.repository)
        if gate.status != "ready":
            raise ValueError(gate.code)
        if pack.task_binding != expected_binding:
            raise ValueError("change_context_reuse_binding_mismatch")
        expected_required = tuple(
            item.layer_type for item in applicability.decisions if item.requirement == "required"
        )
        if pack.required_layers != expected_required:
            raise ValueError("change_context_reuse_applicability_mismatch")
        payloads = {
            layer.layer_type: self.repository.get_layer(layer.layer_id)[1]
            for layer in pack.layers
        }
        projections = self._render_projections(
            pack=pack,
            payloads=payloads,
            reused_layer_count=len(LAYER_TYPES),
            recollected_layer_count=0,
        )
        self.repository.append_event(pack.pack_id, "pack_reused", {"pack_id": pack.pack_id})
        return ChangeContextBuildResult(
            pack=pack,
            gate=gate,
            applicability=applicability,
            projections=projections,
            layer_payloads=payloads,
            reused_layer_count=len(LAYER_TYPES),
            recollected_layer_count=0,
        )

    def _persist_layers(
        self,
        collected: Sequence[CollectedContextLayer],
    ) -> tuple[tuple[ChangeContextLayer, ...], dict[str, dict[str, object]]]:
        by_type = {item.layer_type: item for item in collected if isinstance(item, CollectedContextLayer)}
        if set(by_type) != set(LAYER_TYPES):
            raise ValueError("change_context_collector_output_incomplete")
        layers: list[ChangeContextLayer] = []
        payloads: dict[str, dict[str, object]] = {}
        for layer_type in LAYER_TYPES:
            item = by_type[layer_type]
            payload = dict(item.payload)
            layer = ChangeContextLayer.create(
                layer_type=layer_type,
                status=item.status,
                payload=payload,
                source_fingerprint=item.source_fingerprint,
                artifact_ref=self.repository.artifact_store.reference_for_payload(payload),
                evidence_refs=item.evidence_refs,
                policy_rule_ids=item.policy_rule_ids,
                blockers=item.blockers,
            )
            self.repository.persist_layer(layer, payload)
            layers.append(layer)
            payloads[layer_type] = payload
        return tuple(layers), payloads

    def _create_collection_snapshot(
        self,
        *,
        previous: ChangeContextPack | None,
        task_binding: TaskBinding,
        required_layers: Sequence[str],
        layers: Sequence[ChangeContextLayer],
        applicability: ApplicabilityAssessment,
    ) -> ChangeContextPack:
        predecessor = previous
        if predecessor is not None and predecessor.status == "collecting":
            if (
                predecessor.task_binding == task_binding
                and predecessor.required_layers == tuple(required_layers)
                and predecessor.layers == tuple(layers)
            ):
                # The prior process may have stopped after the immutable
                # collecting snapshot but before its final ready/blocked
                # successor. Reuse that exact snapshot so an unattended retry
                # can finish without forking the lineage.
                return predecessor
        if predecessor is not None and predecessor.status != "superseded":
            tombstone = ChangeContextPack.create(
                pack_version=predecessor.pack_version + 1,
                status="superseded",
                task_binding=predecessor.task_binding,
                required_layers=predecessor.required_layers,
                layers=predecessor.layers,
                gate=predecessor.gate,
                supersedes_pack_id=predecessor.pack_id,
            )
            self.repository.create_pack_snapshot(tombstone)
            predecessor = tombstone
        pending_gate = ChangeContextGateResult(
            "blocked",
            "BLOCKED_CONTEXT_INCOMPLETE",
            ("collection finalization",),
            (),
            ("context collection snapshot is not executable",),
        )
        collecting = ChangeContextPack.create(
            pack_version=1 if predecessor is None else predecessor.pack_version + 1,
            status="collecting",
            task_binding=task_binding,
            required_layers=required_layers,
            layers=layers,
            gate=pending_gate,
            supersedes_pack_id="" if predecessor is None else predecessor.pack_id,
        )
        self.repository.create_pack_snapshot(collecting, applicability.decisions)
        return collecting

    def _render_projections(
        self,
        *,
        pack: ChangeContextPack,
        payloads: Mapping[str, Mapping[str, object]],
        reused_layer_count: int,
        recollected_layer_count: int,
    ) -> dict[str, ChangeContextProjection]:
        return {
            role: self.projection_service.render(
                pack=pack,
                layer_payloads=payloads,
                role=role,
                reused_layer_count=reused_layer_count,
                recollected_layer_count=recollected_layer_count,
            )
            for role in sorted(PROJECTION_ROLES)
        }


def _task_binding(
    *,
    task_context: TaskIntentContext,
    normalized_requirement_evidence: Mapping[str, object],
    current_user_correction: str,
    calibrated_scope: Mapping[str, object],
    candidate_targets: Sequence[CandidateTarget],
    change_ownership: Mapping[str, object] | None,
    project_routing: Mapping[str, object] | None,
    mcp_receipt: McpEvidenceReceipt | None,
    data_connection_alias: str,
    data_schema: str,
    data_tables: Sequence[str],
    fallback_ticket_id: str,
) -> TaskBinding:
    provider_value = str(
        normalized_requirement_evidence.get("source_type")
        or normalized_requirement_evidence.get("provider")
        or "LOCAL"
    ).strip()
    provider = "LOCAL" if provider_value.casefold() in {"manual", "local"} else provider_value.upper()
    ticket_id = str(normalized_requirement_evidence.get("ticket_id") or fallback_ticket_id or "LOCAL").strip()
    revision = str(
        normalized_requirement_evidence.get("revision")
        or normalized_requirement_evidence.get("requirement_revision")
        or "missing"
    ).strip()
    request_hash = content_hash(
        {
            "task_context": task_context.to_dict(),
            "requirement_evidence": dict(normalized_requirement_evidence),
            "current_user_correction": str(current_user_correction or "").strip(),
            "calibrated_scope": dict(calibrated_scope),
            "candidate_targets": [
                {
                    "repository_alias": item.repository_alias,
                    "relative_path": item.relative_path,
                    "target_kind": item.target_kind,
                    "evidence_refs": list(item.evidence_refs),
                    "relationships": list(item.relationships),
                }
                for item in candidate_targets
            ],
            "change_ownership": dict(change_ownership or {}),
            "project_routing": dict(project_routing or {}),
            "mcp_receipt": mcp_receipt.identity_payload() if mcp_receipt is not None else None,
            "data_scope": {
                "connection_alias": data_connection_alias,
                "schema": data_schema,
                "tables": list(data_tables),
            },
        }
    )
    return TaskBinding(provider, ticket_id, revision, request_hash)


def _not_applicable_data_layer(decision: object, targets: Sequence[CandidateTarget]) -> CollectedContextLayer:
    payload: dict[str, object] = {
        "schema_version": "data-graph.v1",
        "status": "not_applicable",
        "rule_ids": list(getattr(decision, "rule_ids")),
        "inspected_targets": [
            {
                "repository_alias": item.repository_alias,
                "relative_path": item.relative_path,
                "target_kind": item.target_kind,
                "relationships": list(item.relationships),
            }
            for item in targets
        ],
        "missing": [],
        "conflicts": [],
    }
    return CollectedContextLayer(
        layer_type="data_graph",
        status="not_applicable",
        payload=payload,
        source_fingerprint=content_hash(payload),
        evidence_refs=tuple(getattr(decision, "evidence_refs")),
        policy_rule_ids=tuple(getattr(decision, "rule_ids")),
        blockers=(),
    )


def _unavailable_data_layer(
    connection_alias: str,
    schema: str,
    tables: Sequence[str],
) -> CollectedContextLayer:
    blocker = "BLOCKED_CONTEXT_SOURCE_UNAVAILABLE: current database.inspect MCP evidence is required."
    payload: dict[str, object] = {
        "schema_version": "data-graph.v1",
        "connection_alias": connection_alias,
        "schema": schema,
        "tables": list(tables),
        "missing": ["current database catalog evidence"],
        "conflicts": [],
    }
    return CollectedContextLayer(
        layer_type="data_graph",
        status="incomplete",
        payload=payload,
        source_fingerprint=content_hash(payload),
        evidence_refs=(),
        policy_rule_ids=("CTX-DATA-MCP-ONLY",),
        blockers=(blocker,),
    )


def _reused_layer_count(
    previous: ChangeContextPack | None,
    layers: Sequence[ChangeContextLayer],
) -> int:
    if previous is None:
        return 0
    previous_ids = {item.layer_id for item in previous.layers}
    return sum(1 for item in layers if item.layer_id in previous_ids)


class _LazyDefaultMcpRuntime:
    """Delay MCP assembly until an external context layer is actually required."""

    def __init__(self, *, harness_root: Path, state_root: Path) -> None:
        self.harness_root = harness_root
        self.state_root = state_root
        self._runtime: object | None = None

    def execute(self, request: object) -> object:
        if self._runtime is None:
            from app.mcp_runtime_factory import build_persistent_mcp_runtime

            config = json.loads(
                (self.harness_root / "config" / "capabilities.json").read_text(encoding="utf-8")
            )
            plugin_roots = config.get("plugin_roots")
            if not isinstance(plugin_roots, list) or any(not isinstance(item, str) for item in plugin_roots):
                raise ValueError("mcp_plugin_roots_unavailable")
            self._runtime = build_persistent_mcp_runtime(
                harness_root=self.harness_root,
                manifest_path=self.harness_root / "config" / "mcp_capabilities.json",
                plugin_inventory_path=self.harness_root / "config" / "plugin_inventory.json",
                plugin_roots=[Path(item) for item in plugin_roots],
                state_root=self.state_root,
                environment=dict(os.environ),
            ).runtime
        return getattr(self._runtime, "execute")(request)


def build_default_change_context_service() -> ChangeContextService:
    """Build the production service from the control DB and frozen MCP factory."""

    harness_root = Path(__file__).resolve().parents[1]
    control_db_path = Path(database.DB_PATH).expanduser().absolute()
    state_parent = control_db_path.parent
    artifact_root = state_parent / "change-context-artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    mcp_state_value = os.environ.get("HARNESS_MCP_STATE_ROOT", "").strip()
    mcp_state_root = (
        Path(mcp_state_value).expanduser().absolute()
        if mcp_state_value
        else state_parent / "mcp-runtime"
    )
    runtime = _LazyDefaultMcpRuntime(harness_root=harness_root, state_root=mcp_state_root)
    repository = ChangeContextRepository(
        lambda: database.connect_database(control_db_path),
        ChangeContextArtifactStore(artifact_root),
    )
    return ChangeContextService(
        repository=repository,
        project_collector=ProjectGraphCollector(),
        change_scope_collector=ChangeScopeCollector(runtime=runtime),  # type: ignore[arg-type]
        code_collector=CodeGraphCollector(),
        data_collector=DataGraphCollector(runtime=runtime),  # type: ignore[arg-type]
        gate=ChangeContextGate(),
        projection_service=ChangeContextProjectionService(repository=repository),
    )
