# Harness Database and Git Capability Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the personal Harness capable of bounded PostgreSQL read-only evidence and governed local/remote Git operations without exposing an unrestricted shell executor.

**Architecture:** Keep database access permanently read-only through the existing `database.inspect` capability and install its optional PostgreSQL driver in the isolated Harness environment. Extend the existing Git provider action system with explicit, one-use plans for read evidence, local history-changing operations, and remote synchronization; every mutation must validate repository identity, clean/dirty state, expected refs, and a read-back receipt before reporting success. Keep the existing immutable code-evidence bundle as the read/review path rather than returning arbitrary command output.

**Tech Stack:** Python 3, existing Harness capability runtime, `psycopg`, PostgreSQL read-only profile, `/usr/bin/git`, existing Manager provider authorization/database, unittest.

## Global Constraints

- Database actions remain read-only; DDL/DML/transaction/lock/procedure/COPY operations stay blocked.
- Git actions are never arbitrary shell commands; each action has a fixed argv grammar and bounded target scope.
- `pull`, `push`, `reset`, `cherry-pick`, `merge`, `fetch`, branch and commit operations require a fresh one-use authorization for the exact target and parameters.
- Push, pull, reset, merge and cherry-pick must never run against a dirty or drifted worktree unless the action contract explicitly supports that state and the plan records it.
- Existing user changes are preserved; no reset, clean, deletion, force-push or remote write is performed during tests.
- Secrets, remote URLs containing credentials, SQL credentials and complete Git output are never returned in public results.
- A successful fake/provider unit test is not a real database or remote Git verification.

## Capability Boundaries

1. Database: `database.inspect` plan -> explicit read-only execute; profile must be `test`/`development`, SQL must pass the existing guard, rows capped and sensitive fields masked.
2. Local Git read: `git.inspect`, complete diff bundle, source read/search, history, isolated verification and review.
3. Local Git mutation: existing `branch.create`/`commit.create` remain governed actions; `reset.local`, `cherry-pick.local` and `merge.local` use fixed argv, one-use authorization, preflight and read-back receipts. Remote operations remain plan-only.
4. Remote Git planning: existing `remote.fetch` remains separate; `git.operation.plan` validates `remote.pull`/`remote.push` targets but remote writes remain disabled until the exact execution contract and dedicated fixture tests are complete.

### Task 1: Database runtime closure

**Files:**
- Modify: `requirements.txt`
- Modify: `.venv` installation metadata only; do not commit generated environment files
- Test: `tests/test_database_capabilities.py`, `tests/test_pg_evidence.py`

**Interfaces:** Keep `build_database_capability_service()` and `database.inspect` request/result schemas unchanged. Add only the runtime dependency required by the existing provider.

- [x] Verify the isolated Harness interpreter is the one used by the provider and that `psycopg` is absent/present without printing credentials.
- [x] Add a bounded `psycopg[binary]` dependency compatible with the project Python version, install it only into the Harness `.venv`, and keep the system Python untouched.
- [x] Re-run plan then explicit execute against an allowlisted test profile with a harmless bounded SELECT; report only status, selected profile, table, row count and audit flags.
- [x] Add a smoke test that distinguishes `passed`, `failed`, `timeout` and `blocked` without asserting business data.

### Task 2: Canonical local Git read evidence

**Files:**
- Modify: `app/capability_service.py` or the existing capability bridge used by Manager
- Modify: `app/code_evidence_service.py`
- Modify: `app/provider_capability_status.py`
- Modify: `config/plugin_inventory.json`
- Modify: `plugins/his-engineering/capabilities.json`
- Test: `tests/test_code_evidence_capability_registration.py`, `tests/test_git_diff_evidence.py`, `tests/test_source_evidence_capabilities.py`, `tests/test_git_history_capability.py`

**Interfaces:** Public read requests select a repository alias and return a sealed evidence bundle reference/summary. They do not return arbitrary Git stdout.

- [x] Register `git.diff`, `source.read`, `source.search`, `git.history`, `verification.run-local` and `code.review-local` against the existing code-evidence services. Direct plugin execution remains intentionally fail-closed; Manager orchestration is the execution boundary.
- [x] Route requirement and code-inquiry messages to the configured `CodeEvidenceService` only when the repository scope is present; otherwise return a stable blocker.
- [x] Reuse immutable bundle seals, snapshot rechecks, path/sensitive-file guards, output limits and isolated verification already present in `app/code_evidence_*`.
- [x] Add a registration/runtime test proving each action reaches the Manager service and that direct/incomplete execution fails closed.

### Task 3: Local Git mutation actions

**Files:**
- Modify: `app/providers/git.py`
- Modify: `app/provider_execution.py`
- Modify: `app/provider_action_authorization.py`
- Modify: `app/provider_capability_status.py`
- Modify: `plugins/his-engineering/capabilities.json`
- Test: `tests/test_git_provider.py`, `tests/test_git_provider_security.py`, `tests/test_provider_action_authorization.py`

**Interfaces:** Add fixed actions with exact parameters and read-back receipts:

```text
reset.local(repository_alias, mode=soft|mixed|hard, target_sha, expected_head_sha, allow_dirty=false)
cherry-pick.local(repository_alias, commit_sha, expected_head_sha, allow_conflict=false)
merge.local(repository_alias, source_ref, expected_head_sha, strategy=ff-only|no-ff, allow_conflict=false)
```

- [x] Extend the plan grammar; reset, cherry-pick, merge, pull and push require explicit target parameters.
- [x] Add local preflight evidence to every history-changing plan: current HEAD/branch, worktree cleanliness, target commit existence, drift blockers and remote-readback requirement.
- [x] Extend executable local mutation actions after the target/worktree transaction and read-back contract was implemented and fixture-tested.
- [x] Capture pre-state (HEAD, branch, status, target commit) before starting; reject drift and unexpected dirty files.
- [x] Execute through fixed `/usr/bin/git` argv with hooks, pagers, external diff/textconv and user/system config disabled.
- [x] On success, read back HEAD/branch/status and return a redacted receipt. On failure, distinguish bounded `git_operation_not_started`, `git_operation_timeout`, `git_operation_failed`, `git_operation_conflict`, and `git_operation_readback_failed`; never claim rollback if Git may have mutated state.
- [x] Add disposable-repository tests for clean reset/cherry-pick/merge success, authorization, dirty rejection, expected-head drift, malicious ref/argument rejection and post-operation read-back.

### Task 4: Remote synchronization actions

**Files:**
- Modify: `app/providers/git.py`
- Modify: `app/provider_execution.py`
- Modify: `app/provider_capability_status.py`
- Modify: `plugins/his-engineering/capabilities.json`
- Modify: `plugins/his-engineering/skills/his-git-delivery/SKILL.md`
- Test: `tests/test_git_provider.py`, `tests/test_git_provider_security.py`, `tests/test_provider_execution.py`

**Interfaces:** Add explicit actions:

```text
remote.pull(repository_alias, remote_alias, ref_name, expected_head_sha, strategy=ff-only|no-ff)
remote.push(repository_alias, remote_alias, source_ref, target_ref, expected_remote_sha, force=false)
```

- [ ] Keep `remote.fetch` read/metadata synchronization separate from `remote.pull` and `remote.push`.
- [ ] Bind remote alias and HTTPS host to the repository profile; reject credential-bearing URLs, unknown hosts, force push and ambiguous refspecs.
- [ ] Require exact one-use authorization and a fresh read-back of local/remote refs; on network interruption report `unknown` rather than `not_applied`.
- [ ] Keep push disabled in replay/readonly modes and add a live smoke that uses a disposable non-production repository only after explicit user approval.

### Task 5: End-to-end Harness presentation and verification

**Files:**
- Modify: `app/server.py`
- Modify: `app/harness.py`
- Modify: `README.md`
- Test: `tests/test_server_core_status_api.py`, `tests/test_harness_capability_routing.py`, targeted database/Git suites

**Interfaces:** UI/API exposes planned action, exact target, risk, confirmation state, execution state, read-back state and remaining blocker; it never exposes credentials or raw command strings.

- [x] Add action catalog/status entries for database read, Git read evidence, local mutations and remote sync.
- [ ] Show a plan before every mutation, consume one authorization only once, and make conflicts/drift/recovery visible.
- [x] Run focused tests, then a local disposable-repository matrix; separately report database real-connect result and remote Git result.
- [x] Update README capability matrix so “registered”, “locally tested”, “real environment verified” and “disabled” are distinct.

## Verification Commands

```bash
cd /Users/lym/WorkCode/ai/Harness
.venv/bin/python -m unittest tests.test_database_capabilities tests.test_pg_evidence -v
.venv/bin/python -m unittest tests.test_git_provider tests.test_git_provider_security tests.test_provider_action_authorization -v
.venv/bin/python -m unittest tests.test_code_evidence_capability_registration tests.test_git_diff_evidence tests.test_source_evidence_capabilities tests.test_git_history_capability -v
```

No command in this plan performs `git push`, production database access, remote merge, reset of a user repository, or deletion of existing data.
