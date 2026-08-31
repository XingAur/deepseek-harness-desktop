# Multi-Service Graph Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Harness service graph the authoritative, branch-level evidence source for multi-service HIS requirements without widening patch scope from mere page dependencies.

**Architecture:** Keep the existing `technical_decision.py` module and its JSON-compatible dictionaries, but preserve every matched frontend entry and every verified service branch. Derive query-chain completeness from all relevant graph branches, distinguish dependency scope from change scope, and harden route/DTO evidence with bounded structural checks and a per-run API index.

**Tech Stack:** Python 3, `unittest`, pathlib-based source scanning, existing Harness artifacts.

## Global Constraints

- No DFHIS business-code edits, Yunxiao writes, Git branch/commit/push, deployment, or database mutation.
- Preserve existing single-service behavior and explicit project/allowlist behavior.
- Keep evidence read-only and label code-level/local evidence separately from runtime or production certainty.
- Follow TDD: each behavior change starts with a failing test and ends with targeted regression tests.

---

### Task 1: Preserve all frontend entries and classify dependencies separately

**Files:**
- Modify: `app/technical_decision.py:351-364, 432-492`
- Test: `tests/test_technical_decision.py`

**Interfaces:**
- `discover_frontend_projects()` preserves bounded `entry_matches` instead of only the first match.
- `build_service_graph()` emits branch scopes `reachable_dependency`, `candidate_change`, `change_required`, or `impact_regression`.

- [ ] **Step 1: Write the failing tests**

Add tests that create two matching `src/views` pages and assert both endpoints appear in the service graph, and create a page with one requested endpoint plus one unrelated endpoint and assert only the requested branch is `change_required` while the other is `reachable_dependency`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_technical_decision.TechnicalDecisionTests.test_service_graph_keeps_all_matching_frontend_entries tests.test_technical_decision.TechnicalDecisionTests.test_service_graph_does_not_mark_every_page_dependency_as_change_required`

Expected: FAIL because only `matches[:1]` is retained and every microservice branch is currently marked `change_required` for generic change wording.

- [ ] **Step 3: Implement the minimal behavior**

Retain all bounded entry matches, add explicit truncation metadata when a cap is reached, and determine `change_required` from endpoint/field/operation evidence; default non-matched page dependencies to `reachable_dependency`.

- [ ] **Step 4: Run the tests to verify they pass**

Run the same command. Expected: PASS.

- [ ] **Step 5: Run the existing technical-decision tests**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_technical_decision`

Expected: all tests pass.

### Task 2: Make the service graph authoritative for query-chain closure

**Files:**
- Modify: `app/technical_decision.py:154-230, 1042-1119, 1395-1428`
- Test: `tests/test_technical_decision.py`

**Interfaces:**
- `build_discovery_query_chain()` accepts the service graph and returns branch-level endpoint/layer evidence.
- `decide_discovered_stored_filter()` blocks only when a required graph branch is unresolved, not because a single endpoint was selected.

- [ ] **Step 1: Write the failing test**

Create a stored-filter fixture with one frontend page, one direct microservice endpoint, and one BFF endpoint. Assert the graph has two verified branches and the query-chain result contains both branches and all required layers.

- [ ] **Step 2: Run the test to verify it fails**

Run the focused test. Expected: FAIL because the current result has only one `endpoint` and cannot close both BFF and service layers.

- [ ] **Step 3: Implement the minimal behavior**

Pass `service_graph` into query-chain construction, map discovery edges to graph branches, preserve a backward-compatible primary endpoint field, and add `branches`, `required_projects`, and `unresolved_branches` fields. Use these fields in the implementation gate.

- [ ] **Step 4: Run the focused and existing tests**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_technical_decision`

Expected: PASS.

### Task 3: Harden Controller and DTO contract evidence

**Files:**
- Modify: `app/technical_decision.py:596-626`
- Test: `tests/test_technical_decision.py`

**Interfaces:**
- `find_controller_paths_for_endpoint()` verifies class/method mapping within the same structural scope.
- `find_public_api_contract_paths()` ignores documentation and uses a per-run DTO index constrained to source files and module dependency evidence.

- [ ] **Step 1: Write failing tests**

Add one test where class-level and method-level paths occur in different Java classes in the same file and assert no route match. Add one test where a DTO name appears only in README text and assert it is not public API evidence.

- [ ] **Step 2: Run the tests to verify they fail**

Run both focused tests. Expected: FAIL because current implementation matches by independent string presence and scans every text file.

- [ ] **Step 3: Implement the minimal behavior**

Parse bounded Java class/method blocks for route pairing, filter API evidence to source-like files, and build/cache a DTO index once per `build_service_graph()` call.

- [ ] **Step 4: Run focused and regression tests**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_technical_decision tests.test_demand_discovery tests.test_demand_orchestrator tests.test_demand_case`

Expected: all targeted tests pass.

### Task 4: Version and artifact consistency, then verify locally

**Files:**
- Modify: `app/technical_decision.py:36-37, 70-80, 1458-1475`
- Test: `tests/test_technical_decision.py`

**Interfaces:**
- Technical decision output and service-graph artifact expose one consistent schema/version label.

- [ ] **Step 1: Write the failing test**

Assert that the result version, markdown heading, and service-graph schema label agree.

- [ ] **Step 2: Run the test to verify it fails**

Expected: FAIL because the result remains `0.8.8` while the graph artifact is labeled `v0.9`.

- [ ] **Step 3: Implement the minimal consistency fix**

Use a single declared technical-decision version and a separate explicit graph schema version only where needed; update artifact titles without changing unrelated historical artifact names.

- [ ] **Step 4: Run validation**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_technical_decision tests.test_demand_discovery tests.test_demand_orchestrator tests.test_demand_case
python3 - <<'PY'
import ast
from pathlib import Path
for name in ("app/technical_decision.py", "app/harness.py", "tests/test_technical_decision.py"):
    ast.parse(Path(name).read_text(encoding="utf-8"), filename=name)
    print(name, "AST OK")
PY
```

Expected: targeted tests pass and all three files parse successfully. A full suite remains environment-dependent if optional dependencies or writable database paths are unavailable.
