# Harness Phase 1 Real Demand Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven-development for every implementation task. This plan is executed inline because the current Harness directory is not a Git repository and no Git delivery is authorized.

**Goal:** Replace case-name technical decisions with a replayable, evidence-first demand loop that can safely analyse five requirement classes before any local patch is considered; five user-provided real simple HIS requirements remain the final business acceptance set.

**Architecture:** Add a small `DemandCase` state record and a generic bounded `DiscoveryGraph` beside existing governance code. `technical_decision.py` consumes the graph as a compatibility adapter; it stops treating named requirements as semantic rules. A replay suite uses sanitized fixture repositories and declarative expected evidence to prove the core loop without a real model or external write.

**Tech Stack:** Python 3 standard library, dataclasses, JSON fixtures, `unittest`; existing Harness governance, project selection and plugin inventory modules.

## Global Constraints

- Keep current Harness database, run history, provider profiles, plugins and knowledge data unchanged; tests use temporary databases and directories only.
- Do not perform Yunxiao writes, Git commit/push, GitHub/GitLab writes, deployment or database writes.
- `needs_requirement_confirmation` can continue only the read-only discovery stage; it cannot create a writable contract.
- Discovery conclusions require source evidence; unsupported business rules become explicit unknowns, never model guesses.
- All implementation changes use test-first cycles and retain old public `build_technical_decision()` compatibility.

### Task 1: Add an auditable DemandCase stage record

**Files:**

- Create: `app/demand_case.py`
- Test: `tests/test_demand_case.py`

**Interfaces:**

- Produces: `DemandCase`, `DemandCaseStage`, `DemandCaseResult`, and `advance_demand_case()`.
- Consumes: sanitized demand text and stage results from Intake, discovery, contract, verification and review.

- [ ] **Step 1: Write the failing state-transition tests**

```python
case = DemandCase.create("显示病人备注")
case = advance_demand_case(case, "intake", "completed", evidence_refs=["ticket:DFHIS-1"])
case = advance_demand_case(case, "discovery", "completed", evidence_refs=["repo:view.vue"])
assert case.current_stage == "discovery"

with self.assertRaisesRegex(ValueError, "cannot advance"):
    advance_demand_case(case, "verification", "completed")
```

- [ ] **Step 2: Run the test and verify it fails because the module does not exist**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_demand_case`

Expected: import failure for `app.demand_case`.

- [ ] **Step 3: Implement the immutable stage record and transitions**

```python
STAGE_ORDER = ("intake", "discovery", "contract", "local_change", "verification", "review", "learning")
STAGE_STATUSES = {"completed", "blocked", "failed", "skipped"}

def advance_demand_case(case, stage, status, *, evidence_refs=(), failure_code=""):
    if stage not in STAGE_ORDER or status not in STAGE_STATUSES:
        raise ValueError("invalid DemandCase stage result")
    if STAGE_ORDER.index(stage) > STAGE_ORDER.index(case.current_stage) + 1:
        raise ValueError("cannot advance DemandCase past an unfinished stage")
    return case.with_stage_result(stage, status, evidence_refs, failure_code)
```

- [ ] **Step 4: Run the complete DemandCase test module**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_demand_case`

Expected: all tests pass.

### Task 2: Build generic, bounded discovery graph extraction

**Files:**

- Create: `app/demand_discovery.py`
- Test: `tests/test_demand_discovery.py`
- Reuse: `app/project_context.py`

**Interfaces:**

- Produces: `DiscoveryGraph`, `DiscoveryNode`, `DiscoveryEdge`, `DiscoveryResult`, and `discover_demand()`.
- Consumes: `demand_text`, `selected_projects`, `max_files`, and `max_file_bytes`.

- [ ] **Step 1: Write failing fixture-repository tests for evidence instead of case names**

```python
result = discover_demand(
    demand_text="收费病人查询增加上午下午筛选，默认全部",
    selected_projects=[frontend, bff, service],
)
assert result.find_nodes(kind="ui", path_suffix="guaHaoChaX/index.vue")
assert result.find_nodes(kind="stored_field", identifier="shangXiaWWsBz")
assert result.find_edges(kind="request_flow")
assert "上午传 0" not in result.proven_rules
```

Add a second fixture with a new unrelated field and endpoint to prove the extractor does not need the words `预交金备注`, `DFHIS-32010`, or `shangXiaWWsBz` in its source code.

- [ ] **Step 2: Run the test and verify it fails before implementation**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_demand_discovery`

Expected: import failure for `app.demand_discovery`.

- [ ] **Step 3: Implement candidate extraction and graph construction**

```python
def discover_demand(*, demand_text, selected_projects, max_files=1800, max_file_bytes=220_000):
    candidates = extract_candidate_terms(demand_text)
    files = bounded_source_files(selected_projects, max_files, max_file_bytes)
    nodes = classify_source_evidence(files, candidates)
    edges = connect_by_request_identifier_and_field(nodes)
    return DiscoveryResult(graph=DiscoveryGraph(nodes=nodes, edges=edges), unknowns=find_graph_gaps(nodes, edges))
```

Rules: score source evidence by exact identifiers before Chinese display terms; record file-relative path and bounded snippet; do not infer enum values, time boundaries or API parameters unless the source text proves them.

- [ ] **Step 4: Run all discovery tests**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_demand_discovery`

Expected: fixture evidence graph tests pass and no source code uses demand-ID equality checks.

### Task 3: Adapt technical decisions to graph facts and remove semantic hard-coding

**Files:**

- Modify: `app/technical_decision.py`
- Test: `tests/test_technical_decision.py`
- Test: `tests/test_demand_discovery.py`

**Interfaces:**

- Consumes: `DiscoveryResult` from `discover_demand()`.
- Preserves: `build_technical_decision()` and `TechnicalDecisionResult` JSON/Markdown consumers.
- Produces: graph-backed `field_provenance`, `query_chain`, `unknowns`, and a read-only `implementation_decision` unless a separately validated contract exists.

- [ ] **Step 1: Write failing compatibility tests**

```python
decision = build_technical_decision(demand_text="新页面按已存状态筛选", project_root=root)
assert decision.field_provenance["evidence_graph"]["nodes"]
assert decision.implementation_decision["can_patch"] is False
assert "需要继续侦查" in "\n".join(decision.implementation_decision["blockers"])
```

Keep the two existing cases, but assert they are supported by discovered source evidence rather than by a `target["kind"]` case branch.

- [ ] **Step 2: Run the targeted tests and verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_technical_decision`

Expected: assertions fail because the current result has no evidence graph.

- [ ] **Step 3: Route decisions through discovery and preserve compatibility fields**

```python
discovery = discover_demand(demand_text=combined_text, selected_projects=selected)
provenance = build_graph_provenance(discovery, selected)
implementation = decide_readonly_from_graph(provenance)
```

Delete semantic branches that assign a target field or business value solely because of demand wording. Retain only generic presentation mappings supplied by discovered source evidence.

- [ ] **Step 4: Run technical-decision and discovery regression tests**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_technical_decision tests.test_demand_discovery`

Expected: all tests pass; the original morning/afternoon replay still locates the stored field and does not ask for a clock boundary.

### Task 4: Connect Intake and governance to a single read-only DemandCase

**Files:**

- Modify: `app/harness.py`
- Modify: `app/technical_decision.py`
- Modify: `../plugins/his-harness-core/skills/harness-workitem-intake/scripts/intake.py`
- Test: `tests/test_requirement_governance_integration.py`
- Test: `../plugins/his-harness-core/skills/harness-workitem-intake/tests/test_intake.py`

**Interfaces:**

- Produces: one `demand_case_json` artifact per workflow run and matching stage status.
- Consumes: Intake `mutation_allowed`/`readonly_discovery_allowed` and graph-backed technical decision.

- [ ] **Step 1: Write failing cross-entry tests**

```python
result = runner.run(..., requirement_governance="observe", execution_mode="readonly")
case = load_artifact(result.run_id, "demand_case_json")
assert case["stages"]["intake"]["status"] == "completed"
assert case["stages"]["discovery"]["status"] == "completed"
assert case["stages"]["local_change"]["status"] != "completed"
```

For partial provider evidence, assert Intake returns process success for discovery, records `analysis=pending`, and `DemandCase` records `contract=blocked` before a writable executor can run.

- [ ] **Step 2: Run the two focused modules and verify the missing artifact assertions fail**

Run: `env HARNESS_DB_PATH=/private/tmp/harness-demandcase-integration.sqlite PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_requirement_governance_integration`

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v skills.harness-workitem-intake.tests.test_intake`

- [ ] **Step 3: Store DemandCase as an additive run artifact and enforce stage guards**

```python
case = build_demand_case_from_intake_and_discovery(...)
database.add_artifact(run_id, "demand_case_json", "DemandCase v1", case.to_json())
if case.stage_status("contract") != "completed":
    block_local_mutation(case)
```

Do not change existing artifacts or write paths. Ensure only `ready_for_analysis` plus a valid governance contract can mark `contract=completed`.

- [ ] **Step 4: Run cross-entry regression tests**

Run: `env HARNESS_DB_PATH=/private/tmp/harness-demandcase-integration.sqlite PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_requirement_governance_integration`

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v skills.harness-workitem-intake.tests.test_intake`

Expected: partial evidence continues discovery only; no route reaches mutation.

### Task 5: Add five declarative replay classes and failure classification

**Files:**

- Create: `fixtures/demand_cases/v1/*.json`
- Create: `app/demand_case_replay.py`
- Test: `tests/test_demand_case_replay.py`
- Modify: `app/real_replay_suite.py`

**Interfaces:**

- Produces: `DemandCaseReplayResult` with passed/failed requirements and failure categories.
- Consumes: fixture input, temporary repository builder and `build_technical_decision()`.

- [ ] **Step 1: Write failing replay tests for five classes**

```python
result = run_demand_case_replay(case_path, repository_root)
self.assertTrue(result.passed, result.failures)
self.assertEqual("blocked", unsafe_case_result.final_status)
```

Fixtures: front-end state retention; stored enum filter; request parameter chain; returned-field display; high-risk ambiguous rule expected to block. These establish technical coverage only; use no real hospital data, credentials or external URLs. The later five user-provided requirements are a separate final acceptance input, not fabricated fixtures.

- [ ] **Step 2: Run the replay test module and verify it fails because fixtures/runner are absent**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_demand_case_replay`

- [ ] **Step 3: Implement declarative expectation comparison**

```python
def run_demand_case_replay(case_path, repository_root):
    fixture = load_case_fixture(case_path)
    decision = build_technical_decision(demand_text=fixture["demand_text"], project_root=repository_root)
    return compare_expected_evidence_and_boundary(fixture, decision)
```

Failure categories must be one of `intake`, `project_selection`, `discovery`, `business_rule`, `contract`, `verification`, `review`; an unknown category is a test failure.

- [ ] **Step 4: Run all five replay cases**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_demand_case_replay tests.test_real_replay_suite`

Expected: four fixture-based read-only analysis cases pass and the unsafe fixture is correctly blocked; this is not yet the five-real-requirement business acceptance claim.

### Task 6: Detect active/frozen plugin gate drift without modifying either copy

**Files:**

- Modify: `app/plugin_inventory.py`
- Modify: `app/plugin_replay_suite.py`
- Test: `tests/test_plugin_migration_security.py`
- Test: `tests/test_plugin_legacy_equivalence.py`

**Interfaces:**

- Produces: `gate_contract_status` with `aligned`, `drifted`, or `unavailable`.
- Consumes: active plugin manifest, frozen replay copy and a normalized public gate-contract projection.

- [ ] **Step 1: Write failing plugin-contract drift tests**

```python
status = compare_gate_contracts(active_root, frozen_root)
self.assertEqual("drifted", status["status"])
self.assertIn("needs_requirement_confirmation", status["differences"])
```

- [ ] **Step 2: Run the focused plugin tests and verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_plugin_migration_security tests.test_plugin_legacy_equivalence`

- [ ] **Step 3: Implement normalized read-only contract comparison**

```python
def compare_gate_contracts(active_root, frozen_root):
    active = load_public_gate_contract(active_root)
    frozen = load_public_gate_contract(frozen_root)
    return {"status": "aligned" if active == frozen else "drifted", "differences": diff_contracts(active, frozen)}
```

Do not copy, overwrite, install, update or delete plugins in this task. Report drift as a visible blocker for replay claims.

- [ ] **Step 4: Run plugin regressions**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_plugin_migration_security tests.test_plugin_legacy_equivalence`

Expected: aligned fixtures pass; intentionally divergent fixtures report drift without mutating files. The current active/frozen runtime status is recorded separately and a `drifted` result blocks any claim of replay/runtime equivalence.

### Task 7: Final Phase 1 capability verification and handoff

**Files:**

- Modify: `docs/superpowers/specs/2026-08-15-harness-core-loop-and-manager-design.md`
- Create: `runs/demand_case_acceptance/phase_1_report.md`

- [ ] **Step 1: Run all Phase 1 focused tests using an isolated database**

Run: `env HARNESS_DB_PATH=/private/tmp/harness-phase1-final.sqlite PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_demand_case tests.test_demand_discovery tests.test_technical_decision tests.test_demand_case_replay tests.test_requirement_governance_integration tests.test_plugin_migration_security tests.test_plugin_legacy_equivalence`

Expected: all selected tests pass.

- [ ] **Step 2: Run the five-case replay acceptance command**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_demand_case_replay`

Expected: four fixture-based evidence-backed read-only decisions and one safety block. The report must separately mark the five-real-requirement acceptance as pending until the user supplies those requirements.

- [ ] **Step 3: Inspect final diff and write an evidence-only acceptance report**

The report must list tests, case outcomes, current remote-freshness limitation, blocked external actions, and Phase 2 boundary. It must not claim real hospital business acceptance, real model generalization, or external provider connectivity.

## Phase 2 Boundary

After Phase 1 is accepted, create a separate Manager plan for capability-registry-backed pages/API/CLI, encrypted model/API-key profiles, Yunxiao/Git/GitLab/GitHub read-only connection tests, plugin lifecycle dry-runs, and action audit presentation. It must not widen external-write authorization.
