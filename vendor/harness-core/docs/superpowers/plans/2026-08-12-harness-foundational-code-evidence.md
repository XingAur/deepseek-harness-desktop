# Harness Foundational Code Evidence Implementation Plan

> **For Codex:** REQUIRED SKILL: Use test-driven-development for each task and verification-before-completion before every review/install gate. Execute tasks in order; do not update the formal Harness until Task 10 is green and the install plan is independently reproducible.

**Goal:** Add complete, immutable local code evidence capabilities so Harness can automatically read/search source, capture complete Git diffs/history, run deterministic verification, perform structured local review, aggregate multiple repositories, and then safely install the verified implementation into the formal Harness.

**Architecture:** Introduce a v70 append-only evidence repository and a no-follow evidence artifact store. Read-only capability services generate sealed bundles that bind repository identity, HEAD, index/worktree state, artifacts and hashes. Verification and Reviewer consume only sealed bundles. Intent/capability routing selects evidence automatically; multi-repository review aggregates sealed child bundles and fails closed when any child is incomplete.

**Tech Stack:** Python 3 standard library, SQLite v70 additive migration, `/usr/bin/git`, `/usr/bin/rg` or bounded Python fallback, existing Harness provider/capability contracts, existing `SafeGitBoundary`, `CodexCliWorker`, `LocalAgentReviewer`, unittest.

---

## Global execution boundaries

- Work only in the linked implementation worktree until the formal install gate.
- Every test process sets a fresh `HARNESS_DB_PATH`, evidence root and knowledge root before importing `app.database`.
- Never open or mutate the formal Harness database during implementation or regression testing.
- No network, cloud Provider, credentials, commit, push, PR/MR, Yunxiao write, deployment or business-database write.
- Preserve all existing dirty-worktree changes; do not reset or overwrite unrelated files.
- Every capability must fail closed on secret, unsafe path, symlink/hardlink/special file, identity race, size limit or incomplete evidence.

## Task 1: Evidence repository and v70 migration

**Files:**

- Create: `app/code_evidence_repository.py`
- Modify: `app/database.py`
- Create: `tests/test_code_evidence_repository.py`
- Modify: `tests/test_database_governance.py`

**Step 1: Write failing schema and repository tests**

Cover fresh v70, v69->v70 migration, append-only bundle/events/artifacts/reviews, bundle state transitions, exact JSON fields, cross-bundle artifact rejection, duplicate kinds/paths, optimistic concurrency, polluted status/hash/path/owner relationships, and default DB zero-open isolation.

**Step 2: Run RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONWARNINGS=error::ResourceWarning \
  python -m unittest -q tests.test_code_evidence_repository tests.test_database_governance
```

Expected: missing repository/v70 contract failures only.

**Step 3: Implement minimal v70 additive migration**

Add tables:

- `code_evidence_bundles`
- `code_evidence_artifacts`
- `code_evidence_events`
- `code_evidence_reviews`
- `code_evidence_sets`
- `code_evidence_set_members`

Use foreign keys, CHECK constraints, append-only triggers, uniqueness and status indexes. Repository is the only mutation surface and uses `BEGIN IMMEDIATE` for state/seal/finalize transitions.

**Step 4: Run GREEN and migration recovery fixtures**

Include manually constructed v69 database, interrupted migration recovery and backup-manifest assertions on temporary files only.

## Task 2: No-follow evidence artifact store and completeness model

**Files:**

- Create: `app/code_evidence_artifacts.py`
- Create: `app/code_evidence_contracts.py`
- Create: `tests/test_code_evidence_artifacts.py`
- Create: `tests/test_code_evidence_contracts.py`

**Step 1: Write RED tests**

Cover same-parent atomic writes, exact owner/mode, directory FD traversal, no-follow, file/dir replacement, hardlinks, symlinks, special files, duplicate leaves, byte/inode/truncate/extend races, size/count budgets, canonical JSON, seal creation, sealed-bundle immutability and no automatic deletion.

**Step 2: Implement artifact primitives**

Implement immutable artifact records with exact kind/path/size/SHA-256 and a bundle seal that includes sorted artifact facts plus repository snapshot facts.

**Step 3: Implement completeness gate**

Return only `complete` or stable blockers. Required artifacts/capabilities, sensitive/incomplete/limit flags, snapshot consistency and review/verification bindings must be exact.

**Step 4: Run focused strict suite**

## Task 3: Complete `git.diff` capability

**Files:**

- Create: `app/code_evidence_git.py`
- Modify: `app/providers/git.py`
- Modify: `app/provider_execution.py`
- Modify: `app/provider_capability_status.py`
- Modify: `app/harness.py`
- Modify: `plugins/his-engineering/capabilities.json`
- Modify: `config/plugin_inventory.json`
- Modify: `plugins/his-engineering/scripts/git_local.py`
- Create: `tests/test_git_diff_capability.py`
- Modify: `tests/test_git_provider.py`
- Modify: `tests/test_git_provider_security.py`
- Modify: `tests/test_provider_capability_status.py`

**Step 1: Write complete diff matrix RED**

Cover tracked/staged/unstaged/untracked/add/delete/rename/mode/binary/gitlink; `--binary --full-index`; per-file before/after SHA-256/mode/size/stats; `git diff --check`; manifest and patch replay with `git apply --check` in a disposable clone.

**Step 2: Write security RED**

Cover hooks, pager, external diff, textconv, clean/smudge/process filters, fsmonitor, replace objects, malicious attributes/config, symlink/hardlink/special files, sensitive paths/content, output/count/size limits and HEAD/index/worktree races.

**Step 3: Implement fixed diff capture**

Promote `git.diff` as an L0 canonical capability. `repo.diff.read` remains a bounded summary action for compatibility, while `git.diff` generates a sealed evidence bundle and returns only safe artifact references and manifest summary.

**Step 4: Run matrix, security and legacy compatibility suites**

## Task 4: `source.read`, `source.search` and `git.history`

**Files:**

- Create: `app/code_evidence_source.py`
- Create: `app/code_evidence_history.py`
- Modify: `app/provider_execution.py`
- Modify: `app/provider_capability_status.py`
- Modify: `app/harness.py`
- Modify: `plugins/his-engineering/capabilities.json`
- Modify: `config/plugin_inventory.json`
- Create: `tests/test_source_evidence_capabilities.py`
- Create: `tests/test_git_history_capability.py`

**Step 1: Write RED tests**

Cover exact relative paths, line/byte budgets, UTF-8/non-UTF-8, no-follow identity checks, secret/sensitive refusal, bounded search matches/context, search incompleteness, SHA/ref restrictions, log/show/blame artifacts and configuration/helper zero execution.

**Step 2: Implement read/search/history services**

Use verified repository scopes and fixed command/scan shapes. Never accept arbitrary argv or revision expressions.

**Step 3: Register capabilities and run compatibility suites**

## Task 5: `verification.run-local`

**Files:**

- Create: `app/code_evidence_verification.py`
- Modify: `app/runtime_policy.py`
- Modify: `app/worktree_lifecycle.py`
- Create: `tests/test_code_evidence_verification.py`
- Modify: `tests/test_runtime_policy.py`
- Modify: `tests/test_worktree_lifecycle.py`

**Step 1: Write RED tests**

Cover fixed argv, no shell, minimal environment, no network, isolated workspace, exact patch replay, process group cleanup, timeout/output limits, stubborn child, cache containment, source/common-git/index/HEAD side-effect checks, verification receipt hash binding and restart-safe facts.

**Step 2: Implement verification runner**

Reuse hardened worktree and process primitives, but consume a sealed evidence bundle instead of caller-provided patch/path facts.

**Step 3: Run focused and Local Agent compatibility suites**

## Task 6: `code.review-local`

**Files:**

- Create: `app/code_evidence_review.py`
- Modify: `app/local_agent_review.py`
- Modify: `app/local_agent_events.py`
- Create: `tests/test_code_evidence_review.py`
- Modify: `tests/test_local_agent_review.py`

**Step 1: Write RED tests**

Cover exact required artifact set, no-follow re-open, bundle seal, verification receipt, diff/source/history binding, incomplete evidence blockers, findings path/line mapping, schema validation, secret/output bounds, Reviewer event reduction, timeout/process failure and review transaction atomicity.

**Step 2: Implement general read-only Reviewer orchestration**

Reuse fixed Reviewer role and strict local parser. It must not infer approval from missing evidence or accept caller-supplied artifact facts.

**Step 3: Run Reviewer and Local Agent compatibility suites**

## Task 7: Automatic capability planning and Manager status

**Files:**

- Create: `app/code_evidence_service.py`
- Modify: `app/task_capability_routing.py`
- Modify: `app/harness.py`
- Modify: `app/server.py`
- Modify: `README.md`
- Modify: `docs/manager-runbook.md`
- Create: `tests/test_code_evidence_service.py`
- Modify: `tests/test_task_capability_routing.py`
- Modify: `tests/test_harness_capability_routing.py`
- Modify: `tests/test_server_core_status_api.py`

**Step 1: Write routing RED tests**

Cover general knowledge skip, source location, history question, code review, requirement inquiry without mutation, requirement modification, missing Provider, incomplete evidence, no Yunxiao and sticky task mode.

**Step 2: Implement automatic evidence planning**

User input selects the smallest evidence plan automatically. Requirement mode cannot downgrade; code review cannot skip diff/completeness/Reviewer; mutations remain behind existing Worker and confirmation gates.

**Step 3: Add Manager status/API**

Show selected capabilities, progress, bundle hash, changed paths, completeness blockers, verification and review. Large artifacts use bounded artifact endpoints; no bypass controls.

**Step 4: Run Manager and complete-flow suites**

## Task 8: Multi-repository evidence sets

**Files:**

- Create: `app/code_evidence_set.py`
- Create: `tests/test_code_evidence_set.py`
- Modify: `app/code_evidence_service.py`
- Modify: `app/server.py`
- Modify: `tests/test_server_core_status_api.py`

**Step 1: Write RED tests**

Cover two/three repositories, deterministic member order, per-repo bundle hash, aggregate seal, final all-repository revalidation, one-member mutation/incomplete/sensitive failure and repository alias in findings.

**Step 2: Implement sequential freeze plus final-set revalidation**

Do not claim filesystem-level atomicity. Any member failure invalidates the entire set.

**Step 3: Run single/multi repository compatibility suites**

## Task 9: Real disposable acceptance and regression gate

**Files:**

- Create: `tests/test_complete_code_evidence_flow.py`
- Create/update: `.superpowers/sdd/code-evidence-capabilities-report.md`

**Step 1: Run real single-repository fixture**

Use a no-remote temporary repository with tracked modification, add, delete, rename, mode, binary and untracked changes. Automatically route, freeze full diff, read/search/history, verify and review. Assert source repository read-only facts unchanged.

**Step 2: Run real two-repository fixture**

Build and review an aggregate evidence set. Mutate one repository during final revalidation and prove whole-set invalidation; rerun on stable fixtures and prove approved review.

**Step 3: Run affected strict suites, then full suite once**

Use a fresh explicit temporary DB/evidence/knowledge/worktree root. Treat `ResourceWarning` as error for focused suites. Record exact counts, skips, failures and environment-only exclusions.

**Step 4: Run py_compile, JSON schema validation and diff checks**

## Task 10: Formal install plan, migration and rollback

**Files:**

- Create: `.superpowers/sdd/code-evidence-install-plan.json`
- Create/update: `.superpowers/sdd/code-evidence-install-report.md`
- Modify generated formal managed-file manifest only after plan approval.

**Step 1: Freeze implementation inventory**

List every managed file with source/destination SHA-256, mode, create/replace action and total plan SHA-256. Reject unexpected dirty/unmanaged overlap.

**Step 2: Build database migration rehearsal**

Copy the formal v69 DB family to a private rehearsal directory using a DB-aware backup process. Rehearse v69->v70, integrity, schema/data invariants and rollback to byte-identical v69 backup. Do not alter formal DB.

**Step 3: Present exact install plan hash**

The plan includes file inventory, formal paths, DB backup location, migration command, service stop/start checks, rollback trigger and recovery verification. Obtain exact user confirmation for that hash.

**Step 4: Install with automatic rollback**

Stop formal Harness safely, create verified backup, apply files atomically, migrate DB, run integrity/smoke/startup gates. On any failure, restore files and DB family, verify v69 and prior startup state, then report failure.

**Step 5: Formal personal-use acceptance**

Run from the formal directory:

- ordinary question without Yunxiao;
- source location/history question;
- existing multi-file code review;
- requirement inquiry with zero mutation;
- disposable requirement change through Worker, verification, Reviewer, confirmation and local apply.

Confirm no commit/push/remote write/business DB write. Report exact supported and remaining product/UI boundaries.
