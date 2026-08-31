# Enterprise Harness ChangeContextPack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a complete, fresh, content-addressed ChangeContextPack a mandatory prerequisite for every Harness code change, while using bounded role projections to reduce repeated prompt context and keeping Yunxiao, GitLab, and PostgreSQL as MCP-only external connectors.

**Architecture:** Split the current technical-decision routine into a read-only discovery phase and a post-context decision phase. Discovery produces bounded project and code facts; a deterministic applicability gate decides whether DataGraph is required; collectors create immutable layer artifacts; SQLite stores content-addressed metadata and lineage; a gate validates completeness, freshness, hashes, and byte budgets; only a bound role projection may reach technical decision, SinglePassChangeContract, or workers.

**Tech Stack:** Python 3 standard library, immutable dataclasses, Draft 2020-12 JSON Schema, SQLite schema v73, existing MCP capability runtime and PostgreSQL `database.inspect`, no-follow content-addressed artifact files, `unittest`, existing enterprise/architecture gates.

## Global Constraints

- The approved design at `docs/superpowers/specs/2026-08-30-enterprise-harness-change-context-pack-design.md` is authoritative. If implementation pressure conflicts with the design, stop and record the conflict instead of silently weakening the gate.
- Harness owns applicability, budgets, freshness, hashes, audit, and approval. Workers consume a specific projection and cannot widen scope, downgrade DataGraph, or replace evidence.
- Skills remain manuals. Yunxiao, GitLab, and PostgreSQL external reads remain MCP executions. Do not add Provider, browser, direct-driver, shell-client, alternate-token, or ambient-credential fallback.
- PostgreSQL remains catalog-only and read-only. This plan must not add SQL text arguments, row sampling, DML, DELETE, DDL, migration, privilege, COPY, procedure, transaction-control, or database-write capability.
- Local Harness SQLite schema migration is allowed because it stores governance metadata. Existing control data must be backed up and migration rollback tested before changing the default database.
- `ProjectGraph`, `ChangeScope`, and relevant `CodeGraph` are required for every mutation-capable run. `DataGraph=not_applicable` is permitted only by a deterministic rule with inspected targets and evidence refs; uncertainty means required.
- A ready pack is necessary but never sufficient for business validity, runtime validity, production readiness, Git delivery, external writes, or promotion.
- Tier 0 canonical UTF-8 JSON must be at most 2,048 bytes. Tier 1 canonical UTF-8 JSON must be at most 12,288 bytes. Tier 2 evidence remains behind content-addressed references.
- Semantic records are append-only. A transition, correction, stale decision, or human rejection creates a new pack snapshot with `supersedes_pack_id`; it never rewrites an earlier semantic record.
- Reject secret-shaped keys or values, raw MCP envelopes, database credentials, business rows, unbounded source content, and absolute credential paths before persistence or projection.
- `/Users/lym/WorkCode/ai/Harness` is currently not a Git repository. Do not fabricate commit steps. Before implementation, create a timestamped backup under `/Users/lym/WorkCode/ai/.his-harness-backups/`, preserve a SHA-256 manifest, and after every task review exact file diffs against that backup. If the directory later becomes a Git repository, commits still require separate user authorization.
- Follow TDD: first run the named test and preserve the expected RED signal; implement only enough to make that task GREEN; then run the regression set named for the task.
- Existing user-owned files and runtime data are not disposable. Never reset, delete, reinitialize, or replace the existing SQLite database to make tests pass.

---

## Task 1: Lock strict contracts, canonical identity, and schemas

**Files:**

- Create: `app/change_context_contracts.py`
- Create: `config/schemas/change_context_layer.v1.json`
- Create: `config/schemas/change_context_pack.v1.json`
- Create: `config/schemas/change_context_projection.v1.json`
- Create: `tests/test_change_context_contracts.py`

- [ ] Add RED tests proving equivalent mappings with different key order produce the same `sha256:` content hash and the same `ccl:sha256:` or `ccp:sha256:` identity.

- [ ] Add RED tests rejecting unknown fields, unknown versions, invalid enum values, duplicate layer types, malformed evidence refs, invalid hashes, non-canonical IDs, skipped supersession edges, and non-positive pack versions.

- [ ] Add RED tests rejecting secret-shaped keys and values such as `password`, `token`, `dsn`, `jdbc:`, `Authorization`, private-key markers, and database row payloads.

- [ ] Add RED tests proving volatile audit timestamps do not change semantic identity, while requirement revision, source fingerprint, layer content, gate result, or supersession does.

- [ ] Run the RED suite:

```bash
./.venv/bin/python -m unittest tests.test_change_context_contracts -v
```

Expected RED: import failure for `app.change_context_contracts`.

- [ ] Implement exact constants and canonical JSON helpers. The public constants must be:

```python
CHANGE_CONTEXT_LAYER_SCHEMA_VERSION = "change-context-layer.v1"
CHANGE_CONTEXT_PACK_SCHEMA_VERSION = "change-context-pack.v1"
CHANGE_CONTEXT_PROJECTION_SCHEMA_VERSION = "change-context-projection.v1"
LAYER_TYPES = ("project_graph", "change_scope", "code_graph", "data_graph")
LAYER_STATUSES = frozenset({"complete", "incomplete", "not_applicable", "stale"})
PACK_STATUSES = frozenset({"collecting", "ready", "blocked", "stale", "superseded"})
GATE_CODES = frozenset({
    "CHANGE_CONTEXT_READY",
    "BLOCKED_CONTEXT_INCOMPLETE",
    "BLOCKED_CONTEXT_STALE",
    "BLOCKED_CONTEXT_CONFLICT",
    "BLOCKED_CONTEXT_SOURCE_UNAVAILABLE",
    "BLOCKED_CONTEXT_HASH_MISMATCH",
    "BLOCKED_CONTEXT_PROJECTION_BUDGET",
    "BLOCKED_CONTEXT_VERSION_MISMATCH",
})
```

- [ ] Implement `canonical_json_bytes(value)`, `content_hash(value)`, `layer_id(value)`, and `pack_id(value)` with UTF-8, sorted keys, compact separators, `allow_nan=False`, and no timestamps in the semantic hash input.

- [ ] Implement frozen `EvidenceReference`, `ChangeContextLayer`, `TaskBinding`, `ChangeContextGateResult`, `ChangeContextPack`, and `ChangeContextProjection` value objects. `from_dict` must enforce exact field sets; `to_dict` must return JSON-safe copies; `to_json` must be deterministic.

- [ ] Use this exact semantic split for layer content and metadata:

```python
@dataclass(frozen=True)
class ChangeContextLayer:
    schema_version: str
    layer_type: str
    layer_id: str
    status: str
    content_hash: str
    source_fingerprint: str
    artifact_ref: str
    evidence_refs: tuple[str, ...]
    policy_rule_ids: tuple[str, ...]
    blockers: tuple[str, ...]
```

The full layer payload is not embedded in this object; it lives in the immutable artifact store and must hash to `content_hash`.

- [ ] Make `ChangeContextPack` contain exactly one manifest entry per layer type, require all `required_layers` to exist, require non-required DataGraph to be present as `not_applicable`, and require a valid immediate `supersedes_pack_id` for `pack_version > 1`.

- [ ] Write strict Draft 2020-12 schemas with `additionalProperties: false` at every object level. Keep layer payload facts out of the pack schema.

- [ ] Run GREEN and schema validation:

```bash
./.venv/bin/python -m unittest tests.test_change_context_contracts tests.test_mcp_schema_validation -v
```

Expected GREEN: all ChangeContext contract tests pass; all existing MCP schemas remain valid.

- [ ] Review the new files for placeholders and broad payload fields:

```bash
rg -n "TODO|TBD|FIXME|raw_payload|raw_envelope|password|token|dsn|business_rows" app/change_context_contracts.py config/schemas/change_context_*.json tests/test_change_context_contracts.py
```

Expected: only negative-test fixtures may contain sensitive marker words; no TODO/TBD/FIXME and no production raw payload field.

## Task 2: Implement the deterministic DataGraph applicability gate

**Files:**

- Create: `app/change_context_applicability.py`
- Create: `tests/test_change_context_applicability.py`

- [ ] Add a table-driven RED matrix for documentation-only, copy-only, style-only, frontend state, frontend API/save field, controller, service, repository, DAO, mapper, entity, DTO, SQL, migration, datasource, configuration, and unknown targets.

- [ ] Add RED multi-repository tests proving one persistence-bearing repository makes DataGraph required for the whole bounded change.

- [ ] Add RED tests proving a model hint cannot set `not_applicable`, missing static evidence selects required, and high-risk HIS keywords add risk tags without auto-approving a business interpretation.

- [ ] Run RED:

```bash
./.venv/bin/python -m unittest tests.test_change_context_applicability -v
```

Expected RED: import failure for `app.change_context_applicability`.

- [ ] Implement a pure `ContextApplicabilityGate.assess(...)` that performs no filesystem, network, database, subprocess, model, or credential access.

- [ ] Use stable policy rule IDs:

```python
ALWAYS_REQUIRED = "CTX-BASE-001"
DATA_BACKEND_PERSISTENCE = "CTX-DATA-001"
DATA_API_PERSISTED_FIELD = "CTX-DATA-002"
DATA_MODEL_MAPPING = "CTX-DATA-003"
DATA_SQL_OR_SCHEMA = "CTX-DATA-004"
DATA_DATABASE_CONFIGURATION = "CTX-DATA-005"
DATA_FRONTEND_SAVE_PATH = "CTX-DATA-006"
DATA_MULTI_REPOSITORY = "CTX-DATA-007"
DATA_ALWAYS_TARGET = "CTX-DATA-008"
DATA_CONSERVATIVE_UNKNOWN = "CTX-DATA-009"
DATA_NOT_APPLICABLE_DOC = "CTX-DATA-NA-001"
DATA_NOT_APPLICABLE_COPY = "CTX-DATA-NA-002"
DATA_NOT_APPLICABLE_STYLE = "CTX-DATA-NA-003"
```

- [ ] Require every candidate target to carry repository alias, relative path, target kind, inspected evidence refs, and detected relationships. Reject absolute target paths in the serialized decision.

- [ ] Treat `Entity`, `Mapper`, `Repository`, `DAO`, `.sql`, migration, datasource, ORM mapping, and schema configuration as unconditional DataGraph triggers. Treat an unclassified source target as `CTX-DATA-009`, never `not_applicable`.

- [ ] Permit `not_applicable` only when every target belongs to one proven class and evidence proves no API field, state transition, data load, persistence call, configuration key, or backend contract change.

- [ ] Return all four layer decisions in deterministic order. ProjectGraph, ChangeScope, and CodeGraph are always `required`; DataGraph is `required` or `not_applicable` with rule IDs and evidence refs.

- [ ] Run GREEN:

```bash
./.venv/bin/python -m unittest tests.test_change_context_applicability tests.test_change_ownership -v
```

Expected GREEN: applicability matrix passes and existing ownership behavior remains unchanged.

## Task 3: Add immutable artifact storage and SQLite v73 metadata

**Files:**

- Create: `app/change_context_artifacts.py`
- Create: `app/change_context_repository.py`
- Modify: `app/database.py`
- Create: `tests/test_change_context_artifacts.py`
- Create: `tests/test_change_context_repository.py`
- Modify: `tests/test_database_governance.py`
- Modify: `tests/test_code_evidence_repository.py`
- Modify: `tests/test_mcp_phase_1a_acceptance.py`
- Modify: `tests/test_mcp_phase_1b_runtime_acceptance.py`

- [ ] Before editing `app/database.py`, back up the current control database and source files; record exact paths and SHA-256 values. Do not migrate the default DB during the RED/GREEN unit loop.

- [ ] Add RED artifact tests for absolute-root validation, mode `0700`, files mode `0600`, `O_NOFOLLOW`, one-link regular files, atomic create, duplicate rejection, size cap, hash verification on reopen, symlink/hard-link rejection, and immutable seal.

- [ ] Add RED repository tests for content deduplication, exact pack-layer hash binding, append-only events, immediate supersession, stale snapshots, gate-result persistence, tamper detection, distinct missing/corrupt/stale/unavailable recovery outcomes, projection metrics, and transaction rollback on partial writes.

- [ ] Add RED migration tests from v72 to v73, from supported legacy versions 69/70/71 to v73, future-version rejection, automatic restore after migration failure, and preservation of legacy sentinel rows.

- [ ] Run RED:

```bash
./.venv/bin/python -m unittest \
  tests.test_change_context_artifacts \
  tests.test_change_context_repository \
  tests.test_database_governance \
  tests.test_code_evidence_repository -v
```

Expected RED: missing ChangeContext storage modules and expected schema-version assertions still report 72.

- [ ] Implement `ChangeContextArtifactStore` as a focused sibling of `EvidenceArtifactStore`, rooted at an explicit absolute path. Use directories named from the full layer hash, not sequential pack IDs:

```text
sha256/<first-two-hex>/<remaining-62-hex>/layer.json
sha256/<first-two-hex>/<remaining-62-hex>/seal.json
```

- [ ] Permit only `layer_payload` and `layer_seal` kinds. Cap one layer artifact at 8 MiB. Refuse overwrite even when content is identical; deduplication must reuse the existing verified record through the repository.

- [ ] Increment database constants exactly:

```python
HARNESS_SCHEMA_VERSION = 73
SUPPORTED_MIGRATION_SOURCES = frozenset({0, 69, 70, 71, 72, HARNESS_SCHEMA_VERSION})
```

- [ ] Add these tables with foreign keys, check constraints, unique constraints, indexes, and append-only triggers:

```text
change_context_layers
change_context_layer_artifacts
change_context_packs
change_context_pack_layers
change_context_applicability_decisions
change_context_gate_results
change_context_events
change_context_projection_metrics
```

- [ ] Store canonical JSON only for bounded metadata: rule IDs, evidence refs, missing items, conflicts, and metrics. Do not store full layer payload in SQLite.

- [ ] Record migration name `v0.73-change-context-pack` in `harness_schema_migrations`. Update hard-coded schema-version assertions without rewriting historical plan documents.

- [x] Make every persisted pack snapshot immutable. Implement state transitions by `create_pack_snapshot(..., supersedes_pack_id=...)`; allow exactly `collecting -> ready`, `collecting -> blocked`, `collecting -> superseded`, `ready -> stale`, `ready -> superseded`, `blocked -> superseded`, and `stale -> superseded`; enforce `pack_version = previous.pack_version + 1` and reject every skipped, reverse, or implicit edge. A matching interrupted collection is recoverable; a corrected retry supersedes the abandoned collection.

- [ ] On every read, reopen the artifact with its stored device/inode/mode/link count, verify SHA-256, reconstruct the contract, and raise `change_context_hash_mismatch` instead of silently repairing metadata.

- [ ] Run GREEN:

```bash
./.venv/bin/python -m unittest \
  tests.test_change_context_artifacts \
  tests.test_change_context_repository \
  tests.test_database_governance \
  tests.test_code_evidence_repository \
  tests.test_mcp_phase_1a_acceptance \
  tests.test_mcp_phase_1b_runtime_acceptance -v
```

Expected GREEN: fresh and migrated databases report v73, old rows survive, and ChangeContext records are append-only and hash-verified.

## Task 4: Split read-only discovery from technical approval and collect local layers

**Files:**

- Modify: `app/technical_decision.py`
- Create: `app/change_context_collectors.py`
- Modify: `app/project_context.py`
- Modify: `config/projects.example.json`
- Create: `tests/test_technical_context_discovery.py`
- Create: `tests/test_change_context_local_collectors.py`
- Modify: `tests/test_technical_decision.py`

- [ ] Add RED equivalence tests proving the old `build_technical_decision(...)` call and `discover_technical_context(...)` followed by `build_technical_decision(discovery=...)` produce the same existing technical-decision payload for fixed fixtures.

- [ ] Add RED tests proving discovery exposes project selection, service graph, bounded code graph, target paths, relevant test inventory, Git fingerprints, and unknowns, but exposes no `can_patch`, allowed-path approval, write command, or mutation authority.

- [ ] Add RED collector tests for complete ProjectGraph, complete ChangeScope, complete CodeGraph, missing repository, missing call chain, relevant dirty-worktree hash changes, unrelated-file stability, multi-repository relationships, and existing-test discovery.

- [ ] Run RED:

```bash
./.venv/bin/python -m unittest \
  tests.test_technical_context_discovery \
  tests.test_change_context_local_collectors \
  tests.test_technical_decision -v
```

Expected RED: `discover_technical_context` and local collectors do not exist.

- [ ] Extract a frozen `TechnicalContextDiscovery` from the selection, service-architecture, and demand-discovery portion of `build_technical_decision`. Its fields must include canonical project root identity, selected projects, service graph, discovery graph, bounded candidate targets, relevant tests, fingerprints, and unknowns.

- [ ] Keep the existing public `build_technical_decision` signature compatible. Add optional keyword-only `discovery` and `change_context_projection`; when omitted, it invokes discovery internally for legacy tests. In governed Harness integration, both must be supplied and a ready projection must be required before `implementation_decision.can_patch` can become true.

- [ ] Do not duplicate scanners. Reuse `select_projects`, `discover_frontend_projects`, `build_service_graph`, `discover_demand`, and `build_service_architecture_catalog`; move only the orchestration boundary.

- [ ] Implement `ProjectGraphCollector`, `ChangeScopeCollector`, and `CodeGraphCollector` as local-only collectors. They return normalized layer payload plus source fingerprint and evidence refs; they never persist, call a model, or invoke an MCP server.

- [ ] ProjectGraph must capture selected repository identity, role, branch/ref policy, module inventory, dependency edges, and sibling relationships. It must distinguish change targets from evidence-only repositories.

- [ ] ChangeScope must bind current `TaskIntentContext.content_hash`, requirement provider/ticket/revision, normalized attachment/comment hashes, current user correction hash, calibrated scope, constraints, and acceptance criteria. Missing required intent fields yields `incomplete`.

- [ ] CodeGraph must capture only relevant entry points, call-chain edges, API/DTO/config/error paths, existing tests, and bounded source evidence refs. Do not embed full source files or broad grep output.

- [ ] Build ProjectGraph fingerprint from project-profile version, repository identities, relevant module inventory, dependency/sibling relationships, branch/ref policy, remote MCP receipt content version when used, and collector version. Build ChangeScope fingerprint from provider, work-item ID, provider revision/version, normalized comment/attachment hashes, `TaskIntentContext` hash, current user correction hash, and collector version. Exclude audit timestamps and credentials from both.

- [ ] Extend `ProjectProfile` and `config/projects.example.json` with optional non-secret metadata:

```json
"database_context": {
  "connection_alias": "his_test_readonly",
  "schemas": ["public"]
}
```

Validate that the alias ends in `_readonly`; absence is allowed for projects that do not require DataGraph and becomes a blocker when DataGraph is required.

- [ ] Compute CodeGraph fingerprint from repository identity, target Git revision, relevant working-tree content hashes, bounded target paths, collector version, and relevant test inventory. Exclude unrelated files from the fingerprint.

- [ ] Run GREEN:

```bash
./.venv/bin/python -m unittest \
  tests.test_technical_context_discovery \
  tests.test_change_context_local_collectors \
  tests.test_technical_decision \
  tests.test_service_architecture -v
```

Expected GREEN: legacy decision fixtures remain equivalent and the new discovery object carries no approval authority.

## Task 5: Collect external context through Yunxiao, GitLab, and PostgreSQL MCP only

**Files:**

- Modify: `app/change_context_contracts.py`
- Modify: `app/change_context_collectors.py`
- Modify: `tests/test_change_context_contracts.py`
- Create: `tests/test_change_context_external_collectors.py`
- Create: `tests/test_change_context_database_collector.py`
- Modify: `tests/test_mcp_capability_runtime.py`
- Modify: `tests/test_mcp_primary_provider_adapter.py`

- [ ] Add RED tests proving a Yunxiao-backed ChangeScope accepts only a current validated `workitem.read` MCP receipt, reuses a receipt already collected in the provider-evidence stage, and rejects legacy Provider/browser/direct-client provenance.

- [ ] Add RED tests proving ProjectGraph invokes `gitlab.read` only when remote baseline evidence is required, uses exact project/ref/object identity, reuses a current receipt, and never falls back after an MCP failure.

- [ ] Add a fake `McpCapabilityRuntime` RED fixture and assert exact `database.inspect` requests for `tables`, `columns`, `constraints`, `indexes`, and `foreign_keys` on only the inferred schema/table scope.

- [ ] Add RED tests proving no environment mapping, DSN, username, password, endpoint, SQL string, driver, Provider adapter, shell client, browser, or alternate capability is accepted by the collector.

- [ ] Add RED tests for missing `_readonly` alias, missing candidate table, MCP unavailable, timeout, connector error, partial metadata, contradictory relationships, wrong provider, `changed=true`, and stale evidence.

- [ ] Add RED tests proving current revalidation runs once for each new DataGraph-required decision, while retry/review of the same unchanged ready pack performs zero new MCP calls.

- [ ] Run RED:

```bash
./.venv/bin/python -m unittest \
  tests.test_change_context_external_collectors \
  tests.test_change_context_database_collector \
  tests.test_mcp_capability_runtime \
  tests.test_mcp_primary_provider_adapter -v
```

Expected RED: MCP evidence receipts and external ContextPack collectors are missing.

- [ ] Implement a strict `McpEvidenceReceipt` carrying only capability, provider, request ID, source identity, source revision/content version, payload hash, evidence refs, freshness, and collection time. It must reject credentials, raw envelopes, business rows, or an `execution_kind` other than `mcp`. Collection time is audit metadata and must be excluded from semantic layer and pack hashes.

- [ ] Let ChangeScope consume the already collected `workitem.read` receipt from the provider-evidence stage. If the task is Yunxiao-backed and no current receipt exists, perform exactly one `workitem.read` through `McpCapabilityRuntime`; do not call legacy `collect_yunxiao_evidence` as a fallback.

- [ ] Implement `GitLabProjectGraphCollector` with `gitlab.read` operations `project` and `commit` for an exact configured project/ref. Repository-file reads are allowed only for a bounded file required to prove a declared remote dependency or version; broad repository traversal is forbidden.

- [ ] Implement `DataGraphCollector` with a constructor-injected `McpCapabilityRuntime`. Construct requests directly with this fixed envelope:

```python
CapabilityRequest.from_dict({
    "schema_version": "his-capability-request.v1",
    "request_id": request_id,
    "capability": "database.inspect",
    "provider": "postgresql",
    "mode": "preview",
    "mutation_level": "L1",
    "authorization": {"explicit": False, "scope": ["database:inspect"]},
    "input": {
        "connection_alias": connection_alias,
        "operation": operation,
        "schema": schema,
        "table": table,
    },
    "context": {"task_id": task_id, "run_id": run_id},
})
```

- [ ] Call `runtime.execute(request)` without an environment argument and without timeout override. Do not import `app.providers.mcp_readonly`, DB-API drivers, subprocess, urllib, requests, or browser code in the collector.

- [ ] Normalize only catalog metadata required by the graph: tables, columns, nullability/default classification, primary/unique/check constraints, index names/definitions, and foreign-key edges. Do not preserve business rows or full MCP envelopes.

- [ ] Store connector evidence refs and stable MCP error codes. On any required-source failure, return an incomplete DataGraph and `BLOCKED_CONTEXT_SOURCE_UNAVAILABLE`; retain historical evidence only for explanation, never current approval.

- [ ] Build DataGraph fingerprint from connection alias, object identities, schema/table scope, normalized catalog hash, MCP content version, and collector version. Exclude all credential and endpoint material.

- [ ] Run GREEN and the existing connector contract suite:

```bash
./.venv/bin/python -m unittest \
  tests.test_change_context_external_collectors \
  tests.test_change_context_database_collector \
  tests.test_mcp_capability_runtime \
  tests.test_mcp_primary_provider_adapter \
  tests.test_mcp_connector_server_contracts -v
```

Expected GREEN: Yunxiao, GitLab, and DataGraph external evidence comes through read-only MCP routes, same-run receipts are reused, and all no-fallback assertions pass.

## Task 6: Add bounded role projections, reuse, and token metrics

**Files:**

- Create: `app/change_context_projection.py`
- Create: `tests/test_change_context_projection.py`

- [ ] Add RED tests for manager, analysis, implementation, review, and knowledge-answer field allowlists.

- [ ] Add RED tests at exactly 2,048/2,049 bytes for Tier 0 and 12,288/12,289 bytes for Tier 1, using canonical UTF-8 byte length rather than character count.

- [ ] Add a deterministic test fixture with at least 110,000 bytes of full evidence and assert ordinary Tier 0 plus Tier 1 is at least 80% smaller while retaining pack ID, gate status/code, required layers, missing/conflicts, allowed paths, call-chain refs, data-contract refs, and tests.

- [ ] Add RED tests proving full source, full MCP envelope, business rows, secrets, and absolute credential paths never appear in a projection; overflow blocks instead of truncating a required fact.

- [ ] Add RED tests proving same-pack retry and review reuse layers without collector calls and record `reused_layer_count`, `recollected_layer_count`, raw bytes, projected bytes, evidence refs opened, and reported model tokens.

- [ ] Run RED:

```bash
./.venv/bin/python -m unittest tests.test_change_context_projection -v
```

Expected RED: projection service is missing.

- [ ] Implement role policies as explicit field selectors, not a generic recursive truncator. Tier 0 must contain only manifest/gate/delta facts; Tier 1 must contain role-specific bounded summaries and references.

- [ ] Use exact role names:

```python
ROLES = frozenset({"manager", "analysis", "implementation", "review", "knowledge_answer"})
TIER0_MAX_BYTES = 2_048
TIER1_MAX_BYTES = 12_288
```

- [ ] Calculate `projection_hash` over schema version, pack ID, role, Tier 0, Tier 1, and opened evidence refs. Validate pack and layer hashes immediately before rendering.

- [ ] Reject a required-fact overflow with `BLOCKED_CONTEXT_PROJECTION_BUDGET`; optional detail may be represented only by an evidence ref and count, never silently cut mid-field.

- [ ] Persist projection metrics through `ChangeContextRepository.record_projection_metric` after successful rendering; do not persist prompt text or model output in the metric table.

- [ ] Run GREEN:

```bash
./.venv/bin/python -m unittest \
  tests.test_change_context_projection \
  tests.test_harness_artifact_compaction -v
```

Expected GREEN: all byte boundaries hold and the 110 KiB fixture achieves at least 80% reduction.

## Task 7: Orchestrate pack creation and gate technical decisions

**Files:**

- Create: `app/change_context_gate.py`
- Create: `app/change_context_service.py`
- Modify: `app/single_pass_change_contract.py`
- Modify: `config/schemas/single_pass_change_contract.v1.json`
- Modify: `app/scope_confirmation.py`
- Modify: `app/harness.py`
- Create: `tests/test_change_context_gate.py`
- Create: `tests/test_change_context_service.py`
- Modify: `tests/test_single_pass_change_contract.py`
- Modify: `tests/test_requirement_governance_integration.py`
- Modify: `tests/test_harness_capability_routing.py`

- [ ] Add RED gate tests for complete, incomplete, stale, conflicting, source-unavailable, hash-mismatched, projection-over-budget, and version-mismatched packs.

- [ ] Add RED service tests for collecting-to-ready snapshots, collecting-to-blocked snapshots, corrected requirement supersession, layer deduplication, current DataGraph revalidation, and no collector rerun for unchanged retry/review. Add table-driven invalidation tests proving requirement/user-correction changes invalidate ChangeScope and downstream applicability, relevant repository/profile/dependency changes invalidate only affected project/code layers, persistence-path changes force DataGraph revalidation, collector/schema-version changes make old output historical-only, unrelated files do not invalidate a bounded CodeGraph, and contradictory evidence blocks instead of silently choosing a source.

- [ ] Add RED contract tests proving `SinglePassChangeContract` blocks unless given a real ready `ChangeContextGateResult`, exact ready pack ID, implementation projection hash, and layer hashes.

- [ ] Add RED Harness integration tests proving context discovery and ChangeContext gate occur before technical decision, before SinglePassChangeContract, before worktree creation, and before any local apply capability.

- [ ] Add RED task-stage tests for this exact order:

```python
TASK_CAPABILITY_SEQUENCE = (
    "intake",
    "provider_evidence",
    "calibration",
    "context_discovery",
    "change_context",
    "technical_decision",
    "ownership",
    "acceptance",
    "understanding",
    "governance",
    "single_pass_contract",
    "local_engineering",
    "verification",
    "knowledge_candidate",
    "audit",
)
```

- [ ] Run RED:

```bash
./.venv/bin/python -m unittest \
  tests.test_change_context_gate \
  tests.test_change_context_service \
  tests.test_single_pass_change_contract \
  tests.test_requirement_governance_integration \
  tests.test_harness_capability_routing -v
```

Expected RED: the pack gate is not yet accepted by the contract or Harness sequence.

- [ ] Implement `ChangeContextGate.evaluate(pack, repository)` as a pure verifier over persisted, reopened layers. It must return one stable top-level code and preserve connector-specific failures only as bounded blockers/evidence refs.

- [ ] Implement `ChangeContextService.build(...)` orchestration in this order: discovery input validation, local layer collection, applicability, required DataGraph MCP collection, artifact persistence, metadata binding, pack snapshot, gate, and role projection.

- [ ] Add a keyword-only `change_context_service: ChangeContextService | None = None` constructor argument to `RequirementWorkflowRunner`. The constructor assignment must be explicit and fail closed:

```python
self.change_context_service = (
    change_context_service
    if change_context_service is not None
    else build_default_change_context_service()
)
```

Production default builds the real service from the existing repository/artifact root/MCP runtime factory. Tests may inject deterministic fakes; production may not bypass the gate with `None`.

- [ ] Add a keyword-only `task_intent_context: TaskIntentContext | None = None` input to `RequirementWorkflowRunner.run`. When supplied, validate and hash that exact structured input. Otherwise derive it only from normalized requirement evidence, requirement calibration, and the current explicit user instruction already present in the run input; do not depend on the later acceptance-matrix stage. Missing fields remain missing and block mutation.

- [ ] Preserve the validated `CapabilityResult`/`McpEvidenceReceipt` from the provider-evidence stage until ChangeScope collection. Do not reduce the result to `data` alone before the pack is built. A Yunxiao-backed mutation route with no current `workitem.read` MCP receipt must perform exactly one MCP read or block; it must not call `collect_yunxiao_evidence`, a browser, or a Provider adapter as fallback.

- [ ] Call `discover_technical_context` after calibration, then build/gate the pack, then call `build_technical_decision(discovery=..., change_context_projection=analysis_projection)`. Do not calculate `implementation_decision.can_patch` before the pack gate.

- [ ] Extend `SinglePassChangeContract` with immutable binding fields:

```python
change_context_pack_id: str
change_context_projection_hash: str
change_context_layer_hashes: tuple[dict[str, str], ...]
```

Blocked contracts must carry no executable paths or commands but may carry the blocked pack ID and blockers for diagnosis.

- [ ] Include pack ID and projection hash in `scope_confirmation_binding`; a superseding pack or changed projection must invalidate the old confirmation token.

- [ ] Preserve readonly analysis behavior: a blocked pack may produce bounded diagnostic artifacts and next read-only actions, but every mutation-capable mode must stop before local engineering.

- [ ] Replace broad technical inputs to post-pack model steps with role projections. `_build_step_input` must accept a `ChangeContextProjection` and omit duplicated full evidence/calibration/technical blobs when a ready projection exists.

- [ ] Run GREEN:

```bash
./.venv/bin/python -m unittest \
  tests.test_change_context_gate \
  tests.test_change_context_service \
  tests.test_single_pass_change_contract \
  tests.test_requirement_governance_integration \
  tests.test_harness_capability_routing \
  tests.test_scope_confirmation -v
```

Expected GREEN: no mutation route reaches technical approval or local engineering without a ready, hash-bound pack.

## Task 8: Bind workers and exported artifacts to the exact pack

**Files:**

- Modify: `app/harness.py`
- Modify: `app/worktree_executor.py`
- Modify: `app/fullstack_executor.py`
- Modify: `app/core_closure.py`
- Modify: `tests/test_worktree_executor.py`
- Modify: `tests/test_core_closure.py`
- Modify: `tests/test_harness_capability_routing.py`
- Modify: `tests/test_harness_artifact_compaction.py`
- Create: `tests/test_change_context_worker_binding.py`

- [ ] Add RED tests requiring every worker prompt and worktree/fullstack manifest to carry exact `pack_id`, projection hash, and bound layer hashes.

- [ ] Add RED tests rejecting missing, stale, superseded, or wrong worker bindings before diff apply, commit, external write, or verification claims.

- [ ] Add RED artifact tests for stable output names `change_context_pack.json`, `change_context_pack.md`, and `change_context_projection_<role>.json`.

- [ ] Run RED:

```bash
./.venv/bin/python -m unittest \
  tests.test_change_context_worker_binding \
  tests.test_worktree_executor \
  tests.test_core_closure \
  tests.test_harness_capability_routing \
  tests.test_harness_artifact_compaction -v
```

Expected RED: existing workers and output allowlists have no ChangeContext binding.

- [ ] Add a deterministic worker binding header to role prompts:

```text
Context-Pack: ccp:sha256:<64 lowercase hex>
Context-Projection: sha256:<64 lowercase hex>
```

Validate the echoed binding before accepting model output. Missing or mismatched binding is a failed worker attempt, not a warning.

- [x] Pass the implementation projection plus the user's original bounded demand text to code workers, and only the review projection plus diff/verification refs to review workers. Use a separate normalized contract demand for governance. Do not append calibration blobs, technical-decision blobs, full MCP envelopes, or full source artifacts to worker prompts.

- [x] Add the binding to worktree/fullstack/multi-service options and manifests. Reopen the pack before workspace access and revalidate it again at the final pre-apply boundary; reject stale, superseded, missing, or hash-mismatched bindings.

- [ ] Persist run artifacts with kinds `change_context_pack_json`, `change_context_pack_markdown`, and `change_context_projection_<role>_json`. Add them to report rendering, compact run manifests, export allowlists, and stable file-name mapping.

- [ ] Ensure `build_json_payload` contains only artifact hashes, byte sizes, and output names—not duplicate pack/projection content.

- [ ] Run GREEN:

```bash
./.venv/bin/python -m unittest \
  tests.test_change_context_worker_binding \
  tests.test_worktree_executor \
  tests.test_core_closure \
  tests.test_harness_capability_routing \
  tests.test_harness_artifact_compaction \
  tests.test_requirement_governance_integration -v
```

Expected GREEN: wrong bindings fail before any apply path and exported artifacts are stable and compact.

## Task 9: Align Skills, documentation, frozen inventory, and verification gates

**Files:**

- Modify: `/Users/lym/plugins/his-harness-core/skills/his-harness/SKILL.md`
- Modify: `/Users/lym/plugins/his-harness-core/.codex-plugin/plugin.json`
- Modify: `/Users/lym/plugins/his-harness-core/capabilities.json`
- Modify: `/Users/lym/plugins/his-harness-core/tests/test_scaffold_contract.py`
- Review and preserve: `/Users/lym/plugins/his-engineering/skills/his-gitlab/SKILL.md`
- Review and preserve: `/Users/lym/plugins/his-engineering/skills/his-database-read/SKILL.md`
- Review and preserve: `/Users/lym/plugins/yunxiao/skills/yunxiao-workitem-read/SKILL.md`
- Modify: `config/plugin_inventory.json`
- Modify: `README.md`
- Modify: `scripts/verify.sh`
- Create: `tests/test_change_context_architecture.py`
- Update: HarnessHistory task `LOCAL-20260830`, run `20260830-160001`

- [ ] Add RED architecture tests proving ChangeContext collectors cannot import direct network/database/provider/browser modules, Yunxiao/GitLab/DataGraph external reads use only `McpCapabilityRuntime`, and no new database mutation capability or MCP tool appears.

- [ ] Add RED Skill contract assertions that `his-harness` requires a ready ContextPack before modification, workers consume bounded projections, and Skills do not connect to Yunxiao/GitLab/database. Preserve the already-green external Skill boundaries: `his-gitlab`, `his-database-read`, and `yunxiao-workitem-read` remain manuals that select MCP capabilities and must not gain sockets, credentials, direct clients, fallback, or external-write behavior.

- [ ] Run RED:

```bash
./.venv/bin/python -m unittest \
  tests.test_change_context_architecture \
  tests.test_plugin_inventory -v

cd /Users/lym/plugins/his-harness-core
/Users/lym/WorkCode/ai/Harness/.venv/bin/python -m unittest tests.test_scaffold_contract -v

cd /Users/lym/plugins/his-engineering
env PYTHONDONTWRITEBYTECODE=1 /Users/lym/WorkCode/ai/Harness/.venv/bin/python -m unittest tests.test_mcp_skill_boundaries tests.test_skill_contracts -v

cd /Users/lym/plugins/yunxiao
env PYTHONDONTWRITEBYTECODE=1 /Users/lym/WorkCode/ai/Harness/.venv/bin/python -m unittest tests.test_skill_contract -v
```

Expected RED: core Skill text/hash/version and verification entrypoint do not yet declare the new gate. The external connector Skill suites remain GREEN (currently 20/20) and prove no edit is needed there.

- [ ] Update the Harness core Skill fixed sequence to: source intake, task context, read-only discovery, applicability, collectors, ready ContextPack, bounded projection, technical decision, SinglePassChangeContract, implementation, verification, review, history. Explicitly state that a worker cannot override applicability or widen scope.

- [x] Preserve Yunxiao, GitLab, and PostgreSQL as connector-only MCP servers. Register the PostgreSQL MCP server's reviewed direct-driver calls in the external-I/O inventory while keeping Harness-side direct PostgreSQL evidence as a fail-closed tombstone; no connector or database mutation capability was added.

- [x] Derive legacy delivery compatibility plugin identity, version, manifest hash, and reviewed source hashes from `config/plugin_inventory.json` instead of a stale hard-coded version.

- [ ] Bump `his-harness-core` plugin version consistently to `0.3.1+codex.20260830-change-context-pack`, refresh only reviewed source hashes in `config/plugin_inventory.json`, and update scaffold expected values.

- [x] Add the focused ChangeContext suites to `./scripts/verify.sh architecture` after external-I/O validation. Every verification mode creates a fresh `/private/tmp` control database and knowledge root before imports, preventing tests from appending evidence to the formal Harness database.

- [x] Document in README: layer meanings, applicability matrix, MCP-only DataGraph path, failure codes, freshness/reuse behavior, byte budgets, worker bindings, local SQLite migration, non-goals, test isolation, and current enterprise acceptance status.

- [ ] Run focused tests:

```bash
./.venv/bin/python -m unittest \
  tests.test_change_context_contracts \
  tests.test_change_context_applicability \
  tests.test_change_context_artifacts \
  tests.test_change_context_repository \
  tests.test_technical_context_discovery \
  tests.test_change_context_local_collectors \
  tests.test_change_context_external_collectors \
  tests.test_change_context_database_collector \
  tests.test_change_context_projection \
  tests.test_change_context_gate \
  tests.test_change_context_service \
  tests.test_change_context_worker_binding \
  tests.test_change_context_architecture -v
```

Expected GREEN: all new suites pass.

- [ ] Run adjacent regressions:

```bash
./.venv/bin/python -m unittest \
  tests.test_database_governance \
  tests.test_code_evidence_repository \
  tests.test_technical_decision \
  tests.test_single_pass_change_contract \
  tests.test_requirement_governance \
  tests.test_requirement_governance_integration \
  tests.test_harness_capability_routing \
  tests.test_harness_artifact_compaction \
  tests.test_mcp_capability_runtime \
  tests.test_mcp_primary_provider_adapter \
  tests.test_mcp_connector_server_contracts -v
```

Expected GREEN: no regression in existing governance, evidence, or MCP-primary behavior.

- [ ] Run architecture and enterprise gates separately:

```bash
./scripts/verify.sh architecture
./scripts/verify.sh offline
```

Expected architecture result: external-I/O validation and architecture tests pass. Expected offline result: report exact gate outcome; do not claim enterprise acceptance if the historical full unit timeout or legacy self-check blocker remains.

- [ ] If a configured `_readonly` database alias is available, run one bounded current-catalog ContextPack fixture and confirm `changed=false`, zero DML/DDL, no fallback, and a ready DataGraph. If unavailable, preserve `BLOCKED_CONTEXT_SOURCE_UNAVAILABLE` as the truthful live-verification boundary.

- [ ] Verify token reduction from exported metrics using one first-run fixture and one unchanged retry/review. Acceptance requires at least 80% reduction for the 110 KiB fixture and zero recollection on unchanged reuse.

- [ ] Review every changed file against the backup, scan for secrets/placeholders, and record SHA-256 values. Do not delete the backup.

- [ ] Append HarnessHistory events for design implementation, test results, live MCP boundary, token metrics, residual risks, and final status. Record `technical_valid`, `business_valid`, `runtime_verified`, and `promotion_enabled` independently.

## Final acceptance checklist

- [ ] Every mutation route has a ready ProjectGraph, ChangeScope, CodeGraph, and required/not-applicable DataGraph before technical approval.
- [ ] DataGraph not-applicable decisions are deterministic, evidence-backed, and conservative.
- [ ] Required database context uses only PostgreSQL MCP catalog operations and is revalidated for a new decision.
- [ ] Layer and pack metadata are immutable, content-addressed, hash-verified, and supersession-aware.
- [ ] A stale, conflicting, unavailable, tampered, wrong-version, or over-budget pack blocks safely.
- [ ] Worker and scope-confirmation bindings invalidate on pack/projection changes.
- [ ] Tier 0 and Tier 1 remain within exact byte limits; the deterministic 110 KiB fixture proves at least 80% reduction.
- [ ] Same-pack retry/review performs zero collector calls and records reuse metrics.
- [ ] No secret, business row, raw MCP envelope, full source file, or alternate connector enters the pack or projection.
- [ ] No external write, Git delivery, database mutation, migration, deployment, or production action was enabled by this phase.
- [ ] Focused and adjacent tests pass; architecture and offline enterprise results are reported without overclaiming.
