# Harness Pre-Change Confirmation Gate Plan

**Goal:** Make the user-facing pre-change scope confirmation an actual immutable gate for every mutating requirement execution, while preserving readonly analysis and existing technical governance.

**Scope:** Harness runtime, CLI, manager task options, run artifacts, and local run-detail page only. No HIS business repository, Yunxiao, Git remote, deployment, or production data changes.

## Design constraints

- Readonly and review-only runs remain runnable without confirmation.
- Worktree, fullstack-worktree, single-demand-trial, core-closure-trial, and auto-local must not enter code modification without a matching scope confirmation.
- The confirmation binds the execution mode, selected projects/services, allowed paths, verification commands, and the frozen change contract through a deterministic SHA-256 token.
- A missing or mismatched token fails closed and emits a clear confirmation artifact; it never guesses or broadens scope.
- Existing governance, diff review, verification, and external-write gates remain authoritative.
- The user confirms scope and business acceptance, not backend implementation details.

## Tasks

### 1. Add deterministic confirmation binding

Create a pure module that canonicalizes the scope payload, generates a short public hash, renders a confirmation token, and validates an exact token. Add unit tests for order independence, mismatch rejection, and sensitive-value exclusion.

### 2. Enforce the gate in RequirementWorkflowRunner

Add an optional confirmation input to the runner and CLI. Persist a pending/confirmed/blocked confirmation artifact after technical scope is known. Block mutating execution before worktree entry when the token is absent or mismatched. Preserve core-closure artifacts and the twelve-stage ledger.

### 3. Expose the gate through the manager/task UI path

Pass confirmation through Task Manager run options and render the confirmation token, affected scope, and exact next action on the run detail page. Keep the first implementation local and readonly; no external writes.

### 4. Verify regression and failure modes

Add tests proving readonly still runs, mutation without confirmation never calls the executor, matching confirmation reaches the existing executor, and a changed scope invalidates an earlier token. Run focused Harness, governance, core-closure, and server tests with a writable temporary SQLite path.
