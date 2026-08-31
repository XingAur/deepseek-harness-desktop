# Enterprise Harness ChangeContextPack Design

**Status:** User-approved design, implementation not started
**Date:** 2026-08-30
**Scope:** First production-oriented ChangeContextPack increment for project, code, and database context governance

## 1. Goal

Build a versioned, evidence-bound `ChangeContextPack` that must be complete before Harness can produce a code-change contract. The pack must establish the relevant project relationships, requirement scope, code call chain, and—when the change can affect persisted data—the database structure and relationships.

The design must also reduce repeated model context over time. Full evidence remains independently recoverable, while workers receive only bounded role-specific projections and evidence references.

## 2. Fixed governance boundaries

1. Harness is the decision, policy, budget, gate, audit, and evidence authority. Workers execute only a versioned pack projection and may not widen scope or replace applicability decisions.
2. Skills are manuals and semantic routing instructions. MCP servers perform Yunxiao, GitLab, and PostgreSQL external reads and resolve their own credentials.
3. A failed MCP read fails closed. Harness must not fall back to a Provider, browser, direct database driver, shell client, alternate token, or ambient credential.
4. External business databases remain read-only. This feature does not register or execute INSERT, UPDATE, DELETE, MERGE, DDL, migration, privilege, DROP, or TRUNCATE operations.
5. Local Harness SQLite may store governance metadata through its normal versioned migration process. It must never store external database credentials or business rows.
6. A complete pack permits a modification decision to continue; it does not prove business validity, runtime validity, production readiness, or release permission.

## 3. Non-goals

The first increment does not introduce a graph database, vector database, new external service, autonomous database mutation, unrestricted SQL, automatic Git delivery, or cross-organization context sharing. It does not attempt to model every repository or every database table before knowing the bounded change scope.

## 4. Architecture

`ChangeContextPack` is a content-addressed manifest over four independently versioned layers:

| Layer | Responsibility | Required rule |
| --- | --- | --- |
| `ProjectGraph` | Project, repository, frontend/backend module, service dependency, branch, and version relationships | Required for every change |
| `ChangeScope` | Requirement background, goal, scenarios, allowed scope, protected scope, constraints, and acceptance criteria | Required for every change |
| `CodeGraph` | Entry points, call chain, API, DTO, configuration, exception paths, tests, and relevant source evidence | Required for every change, bounded to the relevant path |
| `DataGraph` | Tables, columns, primary/foreign keys, indexes, entity mapping, SQL, and read/write paths | Required only when deterministic applicability rules detect a data-contract or persistence impact |

The four layers are not concatenated into a broad model prompt. Each layer has its own content hash, source fingerprint, evidence references, completeness status, and freshness decision. A pack references those layers and records the final gate verdict.

### 4.1 Pack lifecycle

Pack status uses the following state machine:

```text
collecting -> ready
collecting -> blocked
collecting -> superseded
ready -> stale
ready -> superseded
blocked -> superseded
stale -> superseded
```

- `collecting`: required evidence is still being gathered or evaluated.
- `ready`: every required layer is complete, fresh, mutually consistent, and safe to project.
- `blocked`: a required layer is missing, unavailable, conflicting, or invalid.
- `stale`: a source fingerprint changed after the pack became ready.
- `superseded`: a newer pack version explicitly replaces this pack.

Pack records are immutable. A corrected requirement, changed repository state, changed database evidence, or recorded human rejection creates a new pack with `supersedes_pack_id`; it never rewrites the old pack. A retry after process interruption may finish the exact matching `collecting` lineage without recollecting unchanged layers. If the requirement or binding changed while collection was interrupted, the abandoned `collecting` snapshot is explicitly superseded before the corrected collection starts.

### 4.2 Layer status

Each layer uses one of:

- `complete`: required evidence is present and current.
- `incomplete`: required evidence or a required relationship is missing.
- `not_applicable`: deterministic policy proved that the layer is not required for this change.
- `stale`: the evidence no longer matches its source fingerprint.

`DataGraph=not_applicable` is valid only when the applicability gate records a policy rule ID, inspected targets, and evidence references. A worker or model statement alone is never sufficient.

## 5. Risk-adaptive applicability gate

The applicability gate is deterministic and executes before collectors are selected. Model output may suggest targets but may not reduce a required layer.

### 5.1 Always-required context

Every code modification requires:

- complete `ProjectGraph` for every repository in the bounded change;
- complete `ChangeScope` bound to the current requirement revision and current user instructions;
- complete relevant `CodeGraph`, including existing tests and affected boundaries.

### 5.2 DataGraph triggers

`DataGraph` is mandatory when any inspected target or call-chain evidence includes:

- backend controller/service/repository/DAO/mapper code with persistence participation;
- API request/response fields that may map to persisted state;
- DTO/entity/model persistence mapping;
- SQL, ORM query, migration, datasource, schema, table, column, index, or constraint configuration;
- data dictionary or configuration values stored in a database;
- a frontend change whose field origin or save path reaches a persisted backend contract;
- a multi-repository change where any participating repository triggers one of the rules above.

Entity, Mapper, Repository, DAO, SQL, database configuration, and migration targets always require `DataGraph`.

### 5.3 DataGraph not applicable

Pure documentation, copy-only, and style-only changes may use `DataGraph=not_applicable` when static evidence proves that the bounded diff does not alter API fields, state transitions, data loading, persistence calls, configuration keys, or backend contracts.

If the classifier cannot prove non-applicability, the conservative result is `DataGraph` required. This decision may block while the PostgreSQL MCP target is unavailable.

### 5.4 High-risk HIS paths

医保、收费、退费、结算、对账、发票、金额和政策校验 remain subject to their existing human and verification gates. A complete ContextPack cannot auto-approve a business-rule interpretation or external write.

## 6. Component boundaries

### 6.1 ChangeContextContracts

Defines immutable Python value objects and strict JSON Schemas for Pack, Layer, applicability decisions, source fingerprints, evidence references, projections, and gate results. Unknown fields, unknown enum values, unversioned payloads, invalid hashes, and sensitive-shaped fields fail validation.

### 6.2 ContextApplicabilityGate

Consumes the current `TaskIntentContext`, bounded candidate targets, change ownership evidence, and project routing evidence. Produces a deterministic list of required layers and rule IDs. It performs no network or database access.

### 6.3 ContextCollectors

Collectors coordinate existing capabilities only:

- requirement source revision and attachments: Yunxiao MCP when the source is Yunxiao;
- GitLab repository metadata: GitLab MCP when remote evidence is required;
- local source, Git state, call-chain evidence, and tests: governed local code-evidence capabilities;
- database schemas, tables, columns, constraints, indexes, and foreign keys: PostgreSQL MCP `database.inspect` only.

Collectors do not resolve credentials, invoke alternate transports, or write external systems. Each collector returns compact normalized facts plus `evidence_ref`; full raw evidence remains in its owning evidence store.

The legacy direct PostgreSQL evidence adapter is a permanent fail-closed compatibility tombstone. Catalog requests accept only the exact MCP request shape (`connection_alias`, `operation`, `schema`, and `table` where applicable); no SQL text, driver connection, Provider fallback, browser path, or ambient credential lookup is available through Harness.

### 6.4 ChangeContextRepository

Stores immutable Pack and Layer metadata in Harness SQLite. Layer payloads use the existing artifact boundary and content-addressed hashes. The repository deduplicates identical layer content and verifies hashes on read. A pack-to-layer relation binds exact layer IDs and hashes.

### 6.5 ContextProjectionService

Creates role-specific bounded projections:

- Manager: pack manifest, gate result, risk, missing context, and version delta;
- analysis worker: bounded project relationships, requirement scope, and investigation entry points;
- implementation worker: exact allowed paths, relevant call chain, data contracts, boundaries, and tests;
- review worker: change contract, relevant context, diff evidence, and verification evidence;
- ordinary knowledge answer: only the matched knowledge summary and evidence references.

The projection service never embeds full raw artifacts. It validates the pack and layer hashes immediately before producing a projection. Code workers receive the user's original bounded demand text alongside the implementation projection so projection compaction cannot rewrite the requested behavior. Governance and audit continue to use a separate normalized contract demand; broad calibration, technical-decision, MCP-envelope, and source-file blobs are not copied into worker prompts.

### 6.6 ChangeContextGate

Runs before technical change approval and before `SinglePassChangeContract` creation. It returns `ready` only when every required layer is complete and fresh, all hashes match, no evidence conflicts remain, and the projection budget is valid.

The gate does not execute implementation, apply, commit, push, database mutation, or external writes.

## 7. Contracts and identity

The initial public contract is `change-context-pack.v1`. A representative manifest shape is:

```json
{
  "schema_version": "change-context-pack.v1",
  "pack_id": "ccp:sha256:<canonical-content-hash>",
  "pack_version": 2,
  "status": "ready",
  "task_binding": {
    "provider": "LOCAL",
    "ticket_id": "LOCAL-20260830",
    "requirement_revision": "revision-id",
    "request_hash": "sha256:<hash>"
  },
  "required_layers": ["project_graph", "change_scope", "code_graph", "data_graph"],
  "layers": [
    {
      "layer_type": "project_graph",
      "layer_id": "ccl:sha256:<canonical-content-hash>",
      "status": "complete",
      "content_hash": "sha256:<hash>",
      "source_fingerprint": "sha256:<hash>",
      "evidence_refs": ["evidence://project/example"]
    }
  ],
  "gate": {
    "status": "ready",
    "code": "CHANGE_CONTEXT_READY",
    "missing": [],
    "conflicts": []
  },
  "supersedes_pack_id": "ccp:sha256:<older-hash>"
}
```

The canonical content hash excludes volatile presentation fields such as render timestamps. IDs use canonical UTF-8 JSON with sorted keys and compact separators. Time fields may be stored for audit but do not silently change a layer's semantic identity.

`supersedes_pack_id` is absent or an empty string for the first pack version. Every later version must name the exact earlier pack it replaces; a caller may not invent, skip, or rewrite a supersession edge.

## 8. Fingerprints, freshness, and invalidation

### 8.1 ProjectGraph fingerprint

Includes project profile version, repository identity, relevant module inventory, dependency relationships, branch/ref policy, and collector version. It excludes credentials, absolute personal secrets, and unbounded file content.

### 8.2 ChangeScope fingerprint

Includes requirement provider, work-item ID, provider revision/version when available, normalized comment and attachment hashes, `TaskIntentContext` hash, and current user correction hash.

### 8.3 CodeGraph fingerprint

Includes repository identity, target Git revision, relevant working-tree content hashes, bounded target paths, analysis rule version, and relevant test inventory. An unrelated file change must not invalidate an independently bounded code layer.

### 8.4 DataGraph fingerprint

Includes only the connection alias, database object identity returned by MCP, schema/table scope, MCP content version, normalized catalog evidence hash, and collector version. It never includes DSN, username, password, endpoint secrets, or business rows.

For a new modification decision that requires `DataGraph`, PostgreSQL MCP must revalidate current catalog evidence. A historical cached DataGraph may explain prior decisions but may not satisfy the new gate when current evidence cannot be obtained.

### 8.5 Invalidation

- requirement change or user correction invalidates `ChangeScope` and every downstream applicability decision;
- relevant repository HEAD, working-tree content, project profile, or dependency change invalidates the affected project/code layers;
- DTO, entity, SQL, persistence mapping, or backend data-path change forces DataGraph revalidation;
- collector or schema-version change makes older output historical-only until rebuilt;
- contradictory evidence blocks the pack instead of choosing one source silently.

## 9. Token and context budget

The first increment uses deterministic UTF-8 byte limits because they can be enforced before a model call:

- Tier 0 gate manifest: at most 2 KiB;
- Tier 1 role projection: at most 12 KiB;
- Tier 2 evidence: separate content-addressed artifacts retrieved explicitly and in bounded pages.

Full MCP envelopes, full-repository search output, broad source files, and database rows are not copied into Tier 0 or Tier 1.

Each projection records:

- raw evidence bytes available;
- projected bytes sent;
- reused layer count;
- recollected layer count;
- evidence references opened by the worker;
- actual input/output token usage when the model runtime reports it.

For an unchanged pack used by retry or review, collectors do not run again. A new task may reuse matching project and code layers, but a required DataGraph still follows the current-decision revalidation rule.

The deterministic acceptance fixture must contain at least 100 KiB of full evidence and prove that the normal Tier 0 + Tier 1 worker projection is at least 80% smaller without dropping required gate facts.

## 10. Persistence and audit

Harness SQLite stores pack metadata, layer metadata, pack-layer bindings, applicability decisions, gate results, supersession edges, and projection metrics. Full layer content remains in independent artifact files or the owning MCP evidence store and is referenced by stable IDs.

Persistence requirements:

- immutable semantic content;
- content-hash deduplication;
- foreign-key integrity between pack, layer, and binding records;
- hash verification on read;
- no silent repair of tampered records;
- append-only audit events for creation, collection, gate decision, projection, invalidation, and supersession;
- recovery must distinguish missing artifact, corrupt artifact, stale source, and unavailable source.

Generated user-facing artifacts are:

- `change_context_pack.json`: strict machine-readable manifest;
- `change_context_pack.md`: bounded human review summary;
- content-addressed layer artifacts referenced by the manifest.

## 11. Error handling

Stable top-level gate codes include:

- `CHANGE_CONTEXT_READY`
- `BLOCKED_CONTEXT_INCOMPLETE`
- `BLOCKED_CONTEXT_STALE`
- `BLOCKED_CONTEXT_CONFLICT`
- `BLOCKED_CONTEXT_SOURCE_UNAVAILABLE`
- `BLOCKED_CONTEXT_HASH_MISMATCH`
- `BLOCKED_CONTEXT_PROJECTION_BUDGET`
- `BLOCKED_CONTEXT_VERSION_MISMATCH`

MCP-specific failures remain the owning connector's stable error codes and are referenced without raw driver or credential messages. A source failure does not delete previously archived evidence; it marks that evidence historical and prevents it from satisfying a current required layer.

A worker response carrying the wrong `pack_id`, layer hash, or projection hash is rejected before apply, commit, or external write stages.

Scope confirmation carries the same pack ID, projection hash, layer hashes, and repository set from preview through the final core-closure contract. Adapting a legacy contract may not discard these fields. Worktree, full-stack, and multi-service workers reopen and revalidate this binding immediately before workspace access and again at the final pre-apply boundary.

Plugin compatibility shims derive the expected plugin identity, version, manifest hash, and reviewed source hashes from the frozen Harness plugin inventory. They do not retain a separately hard-coded version that can silently diverge from the installed plugin.

## 12. Integration sequence

The governed sequence becomes:

```text
work-item intake
  -> TaskIntentContext
  -> project routing and bounded candidate targets
  -> ContextApplicabilityGate
  -> ContextCollectors
  -> ChangeContextRepository
  -> ChangeContextGate
  -> role-specific ContextProjection
  -> technical decision
  -> SinglePassChangeContract
  -> implementation and verification gates
```

Existing requirement-governance, high-risk, review, verification, delivery, and external-write gates remain in force. ChangeContextPack adds a prerequisite; it does not replace those controls.

## 13. Test strategy

Implementation follows TDD and includes:

### 13.1 Contract tests

- reject unknown fields, statuses, versions, hashes, and sensitive-shaped content;
- prove deterministic IDs for canonical equivalent input;
- prove large source content, MCP envelopes, and business rows cannot enter the manifest;
- prove immutable round-trip and tamper detection.

### 13.2 Applicability matrix tests

- documentation/copy/style-only targets may produce evidence-backed `DataGraph=not_applicable`;
- frontend API fields, DTOs, backend persistence paths, entities, mappers, repositories, SQL, and database configuration require DataGraph as specified;
- multi-repository changes include every participating repository;
- uncertain classification chooses the conservative required result;
- high-risk HIS paths retain their separate human gates.

### 13.3 Freshness and reuse tests

- requirement, relevant Git content, project mapping, catalog version, and collector-version changes invalidate only the correct layers;
- identical layer content is deduplicated without overwriting old packs;
- corrected requirements create a version that supersedes the old pack;
- current DataGraph-required decisions block when live MCP catalog revalidation is unavailable;
- no source failure invokes a fallback connector.

### 13.4 Projection tests

- each role sees only its permitted fields and evidence references;
- Tier 0 and Tier 1 enforce 2 KiB and 12 KiB limits;
- unchanged retry/review projections do not recollect evidence;
- the 100 KiB fixture achieves at least 80% reduction;
- required gate facts survive compaction.

### 13.5 Integration tests

- incomplete pack cannot produce `SinglePassChangeContract`;
- ready pack can proceed to the existing technical-decision boundary;
- stale, conflicting, tampered, or wrong-version packs block;
- SQLite metadata, artifacts, audit events, and hashes reconcile;
- PostgreSQL MCP failure yields zero database writes and no Provider/direct-driver fallback.
- every verification entrypoint creates a fresh `/private/tmp` control database and knowledge root before importing Harness modules; direct test classes also patch the already-imported `database.DB_PATH`, so validation cannot append runs or artifacts to the formal control database.

## 14. Acceptance criteria

The first increment is accepted only when:

1. strict contracts, repository, applicability, projection, and gate tests pass;
2. a full local fixture demonstrates ProjectGraph, ChangeScope, CodeGraph, and required/not-applicable DataGraph decisions;
3. required database evidence uses PostgreSQL MCP only and remains read-only;
4. unchanged retry/review reuses layers without recollection;
5. user correction produces a superseding pack and rejects the older worker projection;
6. no secret, external credential, business row, full MCP envelope, or silent fallback enters a pack or projection;
7. architecture and external-I/O gates pass for the new files;
8. the enterprise offline gate result is reported truthfully. If the historical full unit gate remains non-green, the increment may be locally verified but cannot be called enterprise-accepted or promotion-ready;
9. `business_valid=false`, `runtime_verified=false`, and `promotion_enabled=false` remain unchanged until their independent acceptance processes pass.

## 15. Delivery order

The implementation should be divided into independently reviewable increments:

1. strict contracts and canonical hashing;
2. deterministic applicability gate;
3. immutable repository and artifact bindings;
4. project/change/code collectors using existing local evidence;
5. PostgreSQL MCP DataGraph collector and freshness behavior;
6. role-specific projections and token metrics;
7. pre-change integration gate, artifacts, documentation, and enterprise verification.

Each increment must preserve fail-closed behavior and include focused tests before proceeding to the next.
