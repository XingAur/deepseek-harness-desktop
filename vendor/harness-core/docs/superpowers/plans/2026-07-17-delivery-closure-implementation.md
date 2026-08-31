# HIS Harness Delivery Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Status:** Implemented and verified on 2026-07-17. The checked steps below record the
completed v0.65 implementation; the final CLI uses two user-facing confirmation
commands and performs journal-based recovery automatically.

**Goal:** Implement the approved local-source Git delivery closure from release runtime acceptance through task commit, optional feature push, RC cherry-pick parity audit, RC runtime acceptance, and optional RC push.

**Architecture:** Add a focused `app/delivery_closure.py` state machine that owns controlled Git commands, immutable plans, repository-local journals, acceptance binding, remote guards, and parity evidence. Persist transaction summaries and events in versioned SQLite tables, expose the flow through a dedicated CLI, and let Task Manager reference the stable delivery objects without duplicating Git behavior.

**Tech Stack:** Python 3 standard library, SQLite, Git CLI with argument arrays, `unittest`, local temporary repositories and local bare remotes.

## Global Constraints

- The original business repository is the only place where delivery branches, commits, RC cherry-picks, and pushes may occur.
- Temporary worktrees remain code-trial and verification environments only.
- `release_2.15.3_250515` is the default development branch; `RC_2.16.1_250514` is the default integration branch.
- Requirement branches use `feature-DFHIS-{id}`; bug branches use `hotfix-DFHIS-{id}`.
- Remote writes are disabled unless the immutable plan contains the action, the matching approval event exists, and execution explicitly enables remote writes.
- Never use `git add .`, `git commit -a`, force push, destructive reset, anonymous stash, or shell-built Git commands.
- User-owned changes may only be moved when file and patch hashes prove ownership and exact recovery.
- High-risk or ambiguous conflict resolution is blocked and restored with `git cherry-pick --abort`.
- RC push is blocked until expected task delta and actual RC delta pass the parity audit and the RC runtime acceptance remains current.
- Tests must not access a real Git remote, Yunxiao, a model provider, business PG, or deployment environment.
- The Harness root is not currently a Git repository, so this plan cannot create Harness source commits; each task still ends with an independently passing test boundary.

---

### Task 1: Delivery Policy, Repository Snapshot, and Immutable Plan

**Files:**
- Create: `app/delivery_closure.py`
- Create: `tests/test_delivery_closure.py`

**Interfaces:**
- Produces: `DeliveryPolicy`, `DeliveryRequest`, `DeliveryClosure`, `DeliveryError`, `build_delivery_plan()`, `inspect_repository()`.
- Consumes: existing `app.worktree_executor.run_command`, path validation, and atomic file writing helpers.

- [x] **Step 1: Write failing tests**

Cover a non-Git path, wrong current branch, unsafe merge state, task diff outside the allowlist, exact task-owned diff, mixed separable paths, ambiguous same-file drift, and immutable plan hashing.

- [x] **Step 2: Verify RED**

Run:

```bash
python3 -m unittest tests.test_delivery_closure.DeliveryPlanningTests -v
```

Expected: import failure because `app.delivery_closure` does not exist.

- [x] **Step 3: Implement the minimum planner**

The planner must:

```python
snapshot = inspect_repository(project_path, allowed_paths, expected_diff)
plan = build_delivery_plan(request, policy, snapshot)
assert plan["plan_hash"] == stable_hash(plan_without_hash)
assert plan["remote_actions_enabled"] is False
```

It writes `delivery_plan.json`, `delivery_plan.md`, and a repository-local journal under the path returned by:

```bash
git rev-parse --git-path his-harness/delivery
```

- [x] **Step 4: Verify GREEN**

Run the planning test class and `python3 -m compileall -q app tests`.

### Task 2: Persistent Delivery Transaction and Event Audit

**Files:**
- Modify: `app/database.py`
- Modify: `tests/test_database_governance.py`
- Modify: `app/delivery_closure.py`
- Modify: `tests/test_delivery_closure.py`

**Interfaces:**
- Produces: `add_delivery_transaction()`, `update_delivery_transaction()`, `get_delivery_transaction()`, `add_delivery_event()`, `list_delivery_events()`.
- Consumes: Task 1 transaction IDs, plan hashes, safe JSON payloads, and repository journal paths.

- [x] **Step 1: Write failing migration and round-trip tests**

Assert schema version increments, migration backup still occurs, JSON fields decode, events are ordered, and no approval can be inferred from transaction status alone.

- [x] **Step 2: Verify RED**

Run:

```bash
python3 -m unittest tests.test_database_governance.DeliveryDatabaseTests tests.test_delivery_closure.DeliveryPersistenceTests -v
```

- [x] **Step 3: Add schema version 62 tables**

Create `harness_delivery_transactions` and `harness_delivery_events`. Store task/run references, project path, state, plan hash, policy snapshot, repository snapshot, release acceptance, RC acceptance, commit records, remote results, parity result, journal path, last error, and timestamps.

- [x] **Step 4: Verify GREEN**

Run both targeted test classes.

### Task 3: Release Runtime Acceptance Binding

**Files:**
- Modify: `app/delivery_closure.py`
- Modify: `tests/test_delivery_closure.py`

**Interfaces:**
- Produces: `DeliveryClosure.record_runtime_acceptance(transaction_id, phase, summary, verifier)`.
- Consumes: immutable plan, current branch, current HEAD, current task patch hash, and current file hashes.

- [x] **Step 1: Write failing tests**

Cover release acceptance success, failed acceptance, missing summary, wrong branch, HEAD drift, task patch drift, and acceptance invalidation after a file change.

- [x] **Step 2: Verify RED**

Run the acceptance test class.

- [x] **Step 3: Implement acceptance evidence**

Release evidence binds:

```json
{
  "phase": "release",
  "branch": "release_2.15.3_250515",
  "head": "<sha>",
  "task_patch_hash": "<sha256>",
  "file_state_hash": "<sha256>",
  "status": "passed"
}
```

No acceptance method may create a branch, commit, push, or switch branches.

- [x] **Step 4: Verify GREEN**

Run acceptance and planning tests.

### Task 4: Task Branch Migration, Safety Shelf, Verification, and Commit

**Files:**
- Modify: `app/delivery_closure.py`
- Modify: `tests/test_delivery_closure.py`

**Interfaces:**
- Produces: `DeliveryClosure.execute_stage_one(..., stop_after_commit=True)`.
- Consumes: valid release acceptance and approved plan hash.

- [x] **Step 1: Write failing tests**

Cover:

- exact task diff creates the configured branch and commit while release remains unchanged;
- commit message exactly follows the policy template;
- explicit-path staging rejects extra staged paths;
- mixed separable unrelated tracked and untracked files are shelved and restored with the same hashes and staged state;
- same-file ambiguous drift blocks without switching;
- verification failure restores the starting release state;
- rerun after a completed commit is idempotent;
- rerun after a fully recovered RC integration failure reuses the recorded commit and does not recreate the task branch.

- [x] **Step 2: Verify RED**

Run the stage-one local test class.

- [x] **Step 3: Implement deterministic migration**

Use only explicit argument arrays:

```python
git(["switch", "-c", task_branch])
git(["add", "--", *allowed_paths])
git(["diff", "--cached", "--check"])
git(["commit", "-m", commit_message])
```

Safety Shelf stores index patch, worktree patch, untracked copies, SHA-256 values, and restore state. Failures before commit restore the original branch, index, worktree, and untracked files; unverifiable recovery becomes `recovery_required`.

- [x] **Step 4: Verify GREEN**

Run stage-one, acceptance, and planning tests.

### Task 5: Feature Push Guard and RC Synchronization

**Files:**
- Modify: `app/delivery_closure.py`
- Modify: `tests/test_delivery_closure.py`

**Interfaces:**
- Produces: guarded `push_task_branch()` and `synchronize_integration_branch()`.
- Consumes: stage-one commit record, plan action flags, explicit remote-write enablement, and remote references.

- [x] **Step 1: Write failing local bare-remote tests**

Cover absent task branch, identical remote commit, provable fast-forward, diverged remote branch, remote-write disabled, release push rejection, integration fast-forward sync, and integration divergence.

- [x] **Step 2: Verify RED**

Run the remote fixture test class.

- [x] **Step 3: Implement remote guards**

Read refs before writing with `git ls-remote`. Permit only configured task/integration refs, reject force options, and verify the remote ref after push. A plan with `push_feature=false` must never invoke `git push`.

- [x] **Step 4: Verify GREEN**

Run remote and stage-one tests.

### Task 6: RC Cherry-pick and Parity Audit

**Files:**
- Modify: `app/delivery_closure.py`
- Modify: `tests/test_delivery_closure.py`

**Interfaces:**
- Produces: `audit_cherry_pick_parity()`, `DeliveryClosure.integrate_rc()`, `cherry_pick_parity.json`, and `cherry_pick_parity.md`.
- Consumes: one or more ordered task commits, RC pre-integration HEAD, RC post-integration HEAD, allowlisted paths, and optional deterministic conflict evidence.

- [x] **Step 1: Write failing tests**

Cover single and multiple exact commits, already-present equivalent patch, conflict abort and HEAD restoration, duplicate patch prevention, unexpected missing file, unexpected extra file, unresolved semantic difference, and no RC push before parity.

- [x] **Step 2: Verify RED**

Run the parity and RC integration test classes.

- [x] **Step 3: Implement audit and integration**

The audit compares stable patch IDs, expected/actual changed paths, per-file change signatures, and final-state evidence. Allowed outcomes are:

```text
exact_match
already_present_equivalent
patch_id_equivalent
unexpected_missing
unexpected_extra
unresolved_semantic_difference
```

The last three outcomes set `rc_push_blocked=true`. Every cherry-pick conflict is
aborted and reported for user handling; the implementation does not resolve
conflicts automatically.

Textually different RC deltas with the exact allowed path set and the same stable
patch-id are reported as `patch_id_equivalent`; deltas that cannot prove this
equivalence are reported as `unresolved_semantic_difference` and remain blocked.

- [x] **Step 4: Verify GREEN**

Run parity, RC integration, remote, and stage-one tests.

### Task 7: RC Runtime Acceptance and Final Push

**Files:**
- Modify: `app/delivery_closure.py`
- Modify: `tests/test_delivery_closure.py`

**Interfaces:**
- Produces: RC acceptance binding and `DeliveryClosure.execute_stage_two()`.
- Consumes: current integration HEAD, passed parity, current plan hash, explicit approval, and remote precondition ref.

- [x] **Step 1: Write failing tests**

Cover RC acceptance success, HEAD drift invalidation, working-tree drift invalidation, parity blocker, remote race after acceptance, push disabled, successful local bare push, and post-push remote hash verification.

- [x] **Step 2: Verify RED**

Run the stage-two test class.

- [x] **Step 3: Implement final gate**

`execute_stage_two()` only pushes when:

```python
plan["actions"]["push_integration"] is True
and approved_plan_hash == plan["plan_hash"]
and rc_acceptance["status"] == "passed"
and parity["rc_push_blocked"] is False
and current_head == rc_acceptance["head"]
and remote_ref == recorded_remote_precondition
```

- [x] **Step 4: Verify GREEN**

Run all `tests.test_delivery_closure` tests.

### Task 8: CLI and Task Manager Integration

**Files:**
- Create: `tools/delivery.py`
- Modify: `app/task_manager.py`
- Create: `tests/test_delivery_cli.py`

**Interfaces:**
- Produces commands: `prepare`, `show`, `accept-release`, `first-confirmation`, `accept-rc`, and `second-confirmation`.
- Consumes: Task Manager task identity and existing run/change artifacts.

- [x] **Step 1: Write failing CLI tests**

Assert help output, JSON output, default no-push behavior, Task Manager linkage, and truthful exit codes for blocked/failed/recovery-required states.

- [x] **Step 2: Verify RED**

Run both CLI test modules.

- [x] **Step 3: Implement CLI adapters**

The CLI resolves and verifies the persisted plan hash internally. Users confirm the
displayed stage with `--confirm`; they are not expected to memorize or enter hashes.
Stage failures invoke journal-based automatic recovery. A failed or unverifiable
recovery is retained as `recovery_required` evidence instead of being hidden behind
an unsafe standalone recovery command.

- [x] **Step 4: Verify GREEN**

Run CLI and core delivery tests.

### Task 9: Documentation and Enterprise Gate

**Files:**
- Modify: `README.md`
- Modify: `HANDOFF.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/superpowers/specs/2026-07-17-harness-delivery-closure-design.md`
- Modify: `app/enterprise_gate.py`
- Modify: `tests/test_enterprise_gate.py`

**Interfaces:**
- Produces: documented offline acceptance evidence and stable handoff state.
- Consumes: all prior delivery tests and artifacts.

- [x] **Step 1: Add a failing enterprise-gate assertion**

Require the unit stage to include delivery closure tests and the final report to state that real Git remote writes remain unused during the gate.

- [x] **Step 2: Run targeted and full verification**

```bash
python3 -m compileall -q app tools harnesses tests
python3 -m unittest tests.test_delivery_closure tests.test_delivery_cli tests.test_database_governance.DeliveryDatabaseTests tests.test_enterprise_gate tests.test_release_bundle -v
python3 tools/enterprise_gate.py --output-dir /tmp/his_harness_delivery_closure_gate
```

- [x] **Step 3: Update documentation truthfully**

Document exact supported states, commands, default-off remote behavior, conflict boundary, parity classifications, recovery behavior, and remaining real-business validation boundary.

- [x] **Step 4: Final self-review**

Run:

```bash
rg -n "TODO|TBD|auto_push.: true|force push|git add \\." app tools tests config README.md HANDOFF.md
```

Inspect every changed file, run the full offline gate, and report any unsupported acceptance item instead of calling it complete.
