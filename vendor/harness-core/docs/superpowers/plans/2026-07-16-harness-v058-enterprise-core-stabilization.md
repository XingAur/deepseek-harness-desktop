# HIS Harness v0.58 Enterprise Core Stabilization Implementation Plan

> **For agentic workers:** Use test-driven development and verify each task before moving forward.

**Goal:** Freeze paid model execution and make the local HIS requirement-to-patch loop evidence-driven and transaction-safe.

**Architecture:** Add one small runtime policy boundary, one independent change-ownership evaluator, and one journaled patch transaction inside the existing worktree executor. Integrate the ownership result before core closure worktree execution and preserve all legacy mock/fixture behavior.

**Tech Stack:** Python standard library, unittest, SQLite, Git CLI, existing Harness artifacts.

## Global Constraints

- Harness root is `/Users/lym/WorkCode/ai/Harness` and is not a Git repository; do not initialize Git or fabricate commits.
- Do not call real models, business PG, external write APIs, Git remotes, deployment or Web UI.
- Use TDD: add a failing test, verify the expected failure, implement the minimum behavior, rerun the focused test.
- Preserve existing mock/replay and fixture self-check behavior.
- Do not expose credentials or read model credentials before the freeze gate.

### Task 1: Real Model Runtime Freeze

**Files:**
- Create: `app/runtime_policy.py`
- Create: `tests/test_runtime_policy.py`
- Modify: `app/llm_client.py`
- Modify: `app/model_provider_runtime.py`
- Modify: `tools/task_manager.py`
- Modify: `harnesses/his_requirement_workflow.py`

**Steps:**
- [x] Add tests proving real LLM aliases are blocked before credential loading and mock remains available.
- [x] Add a test proving provider smoke is blocked by default while an internal fake transport test path remains available.
- [x] Run focused tests and observe the missing-policy failures.
- [x] Implement `assert_runtime_mode_allowed()` and `RealModelRuntimeFrozenError`.
- [x] Integrate the policy at both public model boundaries and update CLI wording/defaults.
- [x] Run focused tests and existing model runtime tests.

### Task 2: Change Ownership Matrix and Core Gate

**Files:**
- Create: `app/change_ownership.py`
- Create: `tests/test_change_ownership.py`
- Modify: `app/harness.py`
- Modify: `app/core_closure.py`

**Steps:**
- [x] Add tests for frontend-only, unresolved cross-layer, source-proved backend, comment-only backend claim, database-optional and explicit user override cases.
- [x] Run tests and observe missing-module failures.
- [x] Implement immutable matrix rows, evidence classes, serialization and Markdown output.
- [x] Integrate the matrix before worktree execution and store JSON/Markdown artifacts.
- [x] Block core closure when a required layer is unresolved; keep read-only analysis output available.
- [x] Run ownership and core-closure focused tests.

### Task 3: Journaled Transactional Local Apply

**Files:**
- Modify: `app/worktree_executor.py`
- Modify: `tests/test_worktree_executor.py`

**Steps:**
- [x] Add a failing test for post-apply whitespace failure automatically restoring the target and preserving an unrelated dirty file.
- [x] Add a failing test for deterministic successful-apply idempotency.
- [x] Run both tests and confirm current behavior fails.
- [x] Implement deterministic application IDs, atomic journal writes, file hashes and Git metadata transaction paths.
- [x] Implement reverse-check, reverse-apply, restoration verification and `recovery_required` evidence.
- [x] Implement idempotent success reuse from journal plus current post hashes.
- [x] Run focused worktree and core-closure tests.

### Task 4: Documentation and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `HANDOFF.md`
- Modify: `tools/self_check.py` only when a new explicit v0.58 check is needed.

**Steps:**
- [x] Document v0.58 capability truth, frozen stages, transaction statuses and residual risks.
- [x] Run `python3 -m py_compile` for changed Python files.
- [x] Run all 217+ unit tests with an isolated `/tmp` database.
- [x] Run mock self-check with isolated `/tmp` storage and confirm `business_valid=false`.
- [x] Inspect changed files, whitespace and secret markers; record exact evidence in HANDOFF.

### Task 5: P3 Recovery Follow-up

**Files:**
- Create or modify only after Tasks 1-4 pass.

**Steps:**
- [x] Design startup reconciliation for stale runs and incomplete patch journals.
- [x] Add failure-injection tests around prepared and interrupted applied states.
- [x] Implement executable Task Manager rollback under an explicit local-only confirmation contract.
