# Harness Local Real Agent Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a locally runnable, real Codex-powered code-change loop that creates an isolated worktree, persists Run/Attempt/Event facts, verifies and independently reviews the change, then waits for one-time human confirmation before local-only application.

**Architecture:** Keep the existing real DAG frozen and add a narrow local runner beside it. A validated JSON contract drives a fixed Codex CLI worker; a v69 append-only repository records lifecycle facts; the orchestrator reuses existing worktree and local-apply safety primitives and never exposes remote delivery or database mutation.

**Tech Stack:** Python 3.14, SQLite WAL, standard-library `subprocess`/`selectors`/`signal`, Git worktrees, bundled Codex CLI JSONL, `unittest`.

## Global Constraints

- Work only in `/Users/lym/Documents/Codex/2026-07-27/xia/work/his-harness-plugin-migration-base/.worktrees/implementation/Harness`.
- Do not edit or migrate `/Users/lym/WorkCode/ai/Harness` during this plan.
- Every test process must set `HARNESS_DB_PATH` and `HIS_KNOWLEDGE_HOME` to a fresh temporary directory before importing `app.database`.
- Never open, migrate, checkpoint, restore, delete, or replace the default Harness DB, WAL, SHM, or backups.
- Keep `REAL_MODEL_RUNTIME_FROZEN=True`; the local runner uses a separate single-run activation gate.
- No Git commit, push, branch publication, PR/MR, Yunxiao write, deployment, release, or business-database mutation.
- Business database access remains permanently read-only; the local runner does not receive database credentials or database tools.
- Production subprocesses use fixed argument arrays and `shell=False`; never use `--dangerously-bypass-approvals-and-sandbox`.
- Manager UI, SSO, RBAC, tenants, distributed queues, parallel workers and PostgreSQL are out of scope.
- TDD is mandatory: every production behavior starts with an observed failing test.
- Each task receives an independent implementation review before the next task begins.
- Do not commit plan or implementation changes; delivery remains a separately authorized action.

---

### Task 1: Immutable local-agent task contract and activation gate

**Files:**
- Create: `app/local_agent_contract.py`
- Modify: `app/runtime_policy.py`
- Create: `tests/test_local_agent_contract.py`
- Modify: `tests/test_runtime_policy.py`

**Interfaces:**
- Produces: `LocalAgentTask`, `load_local_agent_task(path: Path) -> LocalAgentTask`, `build_worker_prompt(task: LocalAgentTask) -> str`.
- Produces: `assert_local_agent_run_allowed(*, allow_real_agent: bool, authorization_id: str) -> LocalAgentActivationPreflight`, returning a frozen non-consumed preflight containing only a SHA-256 authorization fingerprint. Task 2 is the sole consumption boundary.
- Consumes later: Tasks 2, 4 and 7 bind persisted runs and CLI requests to `LocalAgentTask.contract_hash`.

- [ ] **Step 1: Write failing contract tests**

```python
def test_valid_contract_is_canonical_and_builds_bounded_prompt(self):
    task = load_local_agent_task(self.write_contract({
        "schema_version": "his-local-agent-task.v1",
        "task_key": "fixture-fix-1",
        "project_path": str(self.repo),
        "request": "Fix add() so the supplied unit test passes.",
        "allowed_paths": ["calculator.py"],
        "verification_commands": [[sys.executable, "-m", "unittest", "-q"]],
        "acceptance_criteria": ["The existing test passes."],
        "timeout_seconds": 120,
    }))
    self.assertRegex(task.contract_hash, r"^[0-9a-f]{64}$")
    self.assertNotIn("Authorization", build_worker_prompt(task))

def test_contract_rejects_shell_string_verification(self):
    payload = self.valid_payload()
    payload["verification_commands"] = ["python -m unittest -q"]
    with self.assertRaisesRegex(ValueError, "local_agent_contract_invalid"):
        load_local_agent_task(self.write_contract(payload))

def test_contract_rejects_parent_path_and_bearer_secret(self):
    payload = self.valid_payload()
    payload["allowed_paths"] = ["../outside.py"]
    payload["request"] = "Bearer " + "a" * 48
    with self.assertRaisesRegex(ValueError, "local_agent_contract_invalid"):
        load_local_agent_task(self.write_contract(payload))
```

- [ ] **Step 2: Run the RED tests**

Run: `PYTHONDONTWRITEBYTECODE=1 /private/tmp/harness-stagea-crypto.EkwKkd/bin/python -m unittest -q tests.test_local_agent_contract tests.test_runtime_policy`

Expected: fail because `app.local_agent_contract` and the local activation gate do not exist.

- [ ] **Step 3: Implement the immutable contract**

```python
LOCAL_AGENT_TASK_SCHEMA_VERSION = "his-local-agent-task.v1"
MAX_REQUEST_CHARS = 12_000
MAX_ALLOWED_PATHS = 64
MAX_VERIFICATION_COMMANDS = 16

@dataclass(frozen=True)
class LocalAgentTask:
    task_key: str
    project_path: Path
    request: str
    allowed_paths: tuple[str, ...]
    verification_commands: tuple[tuple[str, ...], ...]
    acceptance_criteria: tuple[str, ...]
    timeout_seconds: int
    contract_hash: str
    repository_root_identity: tuple[int, int]
    git_dir_identity: tuple[int, int]
    initial_head: str
```

Validate JSON type/depth/size, non-secret aliases and text, repository root identity, `.git` identity, initial HEAD, normalized relative allowed paths and argv-only verification commands. Canonicalize with sorted compact JSON and SHA-256. `build_worker_prompt()` renders a fixed safety section plus only validated fields.

- [ ] **Step 4: Implement the separate activation gate**

```python
def assert_local_agent_run_allowed(*, allow_real_agent: bool, authorization_id: str) -> LocalAgentActivationPreflight:
    if allow_real_agent is not True:
        raise LocalAgentRunNotAllowedError()
    normalized = validate_one_time_authorization_text(authorization_id)
    return LocalAgentActivationPreflight(
        authorization_hash="sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        consumed=False,
    )
```

The function must not mutate `REAL_MODEL_RUNTIME_FROZEN` and must never return the raw authorization value. It performs validation only and must explicitly report `consumed=False`; it is not itself the one-time authorization consumer.

- [ ] **Step 5: Run GREEN and strict warning tests**

Run the Step 2 command with `PYTHONWARNINGS=error::ResourceWarning`.

Expected: all contract/runtime-policy tests pass with no warning.

---

### Task 2: v69 append-only Run/Attempt/Event/Artifact repository

**Files:**
- Modify: `app/database.py`
- Create: `app/local_agent_repository.py`
- Create: `tests/test_local_agent_repository.py`
- Modify: `tests/test_database_governance.py`

**Interfaces:**
- Produces: `LocalAgentRunRepository.consume_preflight(task, preflight) -> dict` as the only public run-creation boundary. It must accept the frozen `LocalAgentActivationPreflight`, never a raw hash string, and atomically persist the one-time authorization consumption together with the contract/repository/HEAD binding.
- Produces: `start_attempt(run_id: int) -> dict`, `append_event(run_id: int, attempt_id: int | None, event_type: str, payload: Mapping[str, object]) -> dict`, `transition(run_id: int, expected: str, target: str, summary: Mapping[str, object]) -> dict`, `add_artifact(run_id: int, attempt_id: int | None, kind: str, relative_path: str, sha256: str, size_bytes: int) -> dict`, `snapshot(run_id: int) -> dict`, and `mark_orphaned_attempts_interrupted() -> list[int]`.
- Consumes: Task 1 `LocalAgentTask` and contract hash.
- Consumed later: Tasks 3-7 use the repository as the only state mutation surface.

- [ ] **Step 1: Write failing migration and append-only tests**

```python
def test_v68_migrates_to_v69_with_append_only_tables(self):
    self.create_v68_fixture()
    database.init_db()
    self.assertEqual(69, database.read_database_user_version(database.DB_PATH))

def test_events_reject_update_delete_and_replace(self):
    event = self.repository.append_event(self.run_id, self.attempt_id, "worker_started", {"pid": 123})
    statements = [
        ("update local_agent_run_events set event_type='changed' where id=?", (event["id"],)),
        ("delete from local_agent_run_events where id=?", (event["id"],)),
        ("insert or replace into local_agent_run_events(id,run_id,attempt_id,sequence_no,event_type,payload_json,created_at) values(?,?,?,?,?,?,?)",
         (event["id"], self.run_id, self.attempt_id, event["sequence_no"], "changed", "{}", event["created_at"])),
    ]
    for sql, parameters in statements:
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(sql, parameters)

def test_invalid_transition_and_cross_attempt_event_fail_closed(self):
    with self.assertRaisesRegex(ValueError, "local_agent_state_transition_invalid"):
        self.repository.transition(self.run_id, "created", "locally_applied", {})

def test_preflight_is_consumed_once_across_retry_and_restart(self):
    run = self.repository.consume_preflight(self.task, self.preflight)
    reopened = LocalAgentRunRepository(self.database_path)
    with self.assertRaisesRegex(ValueError, "local_agent_authorization_already_consumed"):
        reopened.consume_preflight(self.task, self.preflight)

def test_concurrent_preflight_consumption_has_exactly_one_winner(self):
    # Two independent connections race the same preflight. The database unique
    # constraint and BEGIN IMMEDIATE transaction must allow exactly one run.
    self.assertEqual(1, self.concurrent_consume_success_count())
```

- [ ] **Step 2: Run RED on a fresh temporary v68 database**

Run: `stagef_tmp=$(mktemp -d /private/tmp/his_harness_stage_f_repo.XXXXXX) && HARNESS_DB_PATH="$stagef_tmp/harness.sqlite" HIS_KNOWLEDGE_HOME="$stagef_tmp/knowledge" PYTHONDONTWRITEBYTECODE=1 PYTHONWARNINGS=error::ResourceWarning /private/tmp/harness-stagea-crypto.EkwKkd/bin/python -m unittest -q tests.test_local_agent_repository tests.test_database_governance`

Expected: fail because schema v69 and repository do not exist.

- [ ] **Step 3: Add v69 schema**

```sql
create table if not exists local_agent_runs (
    id integer primary key autoincrement,
    task_key text not null,
    contract_hash text not null unique,
    authorization_hash text not null unique,
    project_identity_json text not null,
    initial_head text not null,
    worktree_path text not null default '',
    status text not null,
    summary_json text not null default '{}',
    created_at text not null,
    updated_at text not null
);
create table if not exists local_agent_attempts (
    id integer primary key autoincrement,
    run_id integer not null references local_agent_runs(id),
    attempt_no integer not null,
    status text not null,
    worker_pid integer,
    worker_start_identity text not null default '',
    error_code text not null default '',
    started_at text not null,
    finished_at text,
    unique(run_id, attempt_no)
);
create table if not exists local_agent_run_events (
    id integer primary key autoincrement,
    run_id integer not null references local_agent_runs(id),
    attempt_id integer references local_agent_attempts(id),
    sequence_no integer not null,
    event_type text not null,
    payload_json text not null,
    created_at text not null,
    unique(run_id, sequence_no)
);
create table if not exists local_agent_artifacts (
    id integer primary key autoincrement,
    run_id integer not null references local_agent_runs(id),
    attempt_id integer references local_agent_attempts(id),
    kind text not null,
    relative_path text not null,
    sha256 text not null,
    size_bytes integer not null,
    created_at text not null,
    unique(run_id, kind, relative_path)
);
```

Add recursive-trigger-protected update/delete/replace rejection for events and artifacts. Register migration name `v0.69-local-real-agent-loop` and preserve the existing pre-migration backup/restore contract.

`consume_preflight()` must first call `assert_local_agent_task_is_current(task)`, require `preflight.consumed is False`, and in one `BEGIN IMMEDIATE` transaction insert the unique authorization hash plus the task contract hash, repository root/git identities and initial HEAD. Duplicate, concurrent, or post-restart reuse must fail closed with a generic stable error; no later runner may accept a bare authorization hash or an unconsumed preflight directly.

- [ ] **Step 4: Implement transactional repository methods**

Use `begin immediate` for attempt allocation, event sequence allocation and state transitions. Validate every read-back row, JSON payload and relationship. Historical corruption must return a generic `local_agent_storage_invalid` without echoing stored text.

- [ ] **Step 5: Implement orphan recovery**

`mark_orphaned_attempts_interrupted()` changes only attempts currently marked `worker_running` whose stored PID is absent or does not match the stored process-start identity. It appends an `attempt_interrupted` event and transitions the run to `interrupted` in the same transaction.

- [ ] **Step 6: Run GREEN, v68 migration and strict warning tests**

Expected: repository and governance tests pass on a fresh temporary DB; the default DB path is never observed by patched `sqlite3.connect`.

---

### Task 3: Fixed Codex CLI process boundary

**Files:**
- Create: `app/codex_cli_worker.py`
- Create: `tests/test_codex_cli_worker.py`

**Interfaces:**
- Produces: `CodexCliWorker.start(request: CodexWorkerRequest, sink: WorkerEventSink) -> CodexWorkerResult`.
- Produces immutable `CodexWorkerRequest(worktree_path, prompt, timeout_seconds, sandbox_mode, output_schema_path)`.
- Consumes later: Task 4 invokes the worker twice, once writable and once read-only.

- [ ] **Step 1: Write failing argv, streaming, timeout and cleanup tests**

```python
def test_worker_uses_fixed_safe_argv_and_streams_jsonl(self):
    result = self.worker.start(self.request(), self.sink)
    self.assertEqual("workspace-write", self.factory.argv_value("--sandbox"))
    self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", self.factory.argv)
    self.assertEqual(["thread.started", "item.completed"], self.sink.types)

def test_timeout_kills_and_reaps_process_group(self):
    factory = FakeProcessFactory(mode="timeout")
    result = CodexCliWorker(process_factory=factory).start(self.request(timeout_seconds=1), self.sink)
    self.assertEqual("worker_timeout", result.error_code)
    self.assertTrue(factory.process_group_cleaned)
    self.assertTrue(factory.leader_reaped)

def test_output_overflow_and_callback_failure_always_cleanup(self):
    for mode, expected in (("overflow", "worker_output_too_large"), ("callback_error", "worker_event_sink_failed")):
        factory = FakeProcessFactory(mode=mode)
        result = CodexCliWorker(process_factory=factory).start(self.request(), self.sink)
        self.assertEqual(expected, result.error_code)
        self.assertTrue(factory.process_group_cleaned)
```

- [ ] **Step 2: Run RED**

Expected: import failure for `app.codex_cli_worker`.

- [ ] **Step 3: Implement fixed production process creation**

```python
argv = [
    CODEX_EXECUTABLE, "exec", "--json", "--ephemeral", "--ignore-user-config",
    "--sandbox", request.sandbox_mode, "--approve-for-me",
    "--cd", str(request.worktree_path), "-",
]
```

For reviewer requests append `--output-schema <anchored-schema-path>` and use `read-only`. Start with `stdin=PIPE`, `stdout=PIPE`, `stderr=PIPE`, `start_new_session=True`, `shell=False`, `close_fds=True`. No caller-provided executable or extra argv is allowed.

- [ ] **Step 4: Implement bounded drain and unconditional cleanup**

Use nonblocking/selectable reads, per-line and aggregate byte budgets, a monotonic deadline and periodic heartbeat. In `finally`, always attempt process-group termination, reap the leader, and close all streams. Hash raw bytes before discarding them; persist only validated safe event fields.

- [ ] **Step 5: Run GREEN with `ResourceWarning` as error**

Expected: all fake-process cases pass, including child closes stdio, callback raises, zero/partial reads, oversized line, invalid JSON, timeout and cancellation.

---

### Task 4: Local worktree runner and deterministic verification

**Files:**
- Create: `app/local_agent_runner.py`
- Create: `tests/test_local_agent_runner.py`
- Modify: `app/worktree_lifecycle.py`
- Modify: `app/worktree_executor.py`

**Interfaces:**
- Produces: `LocalAgentRunner.execute(task, preflight) -> dict` and `LocalAgentRunner.retry(run_id) -> dict`.
- `execute()` must begin by delegating to Task 2 `consume_preflight(task, preflight)`; no Runner API accepts a raw authorization hash. `retry()` is an additional bounded attempt of the already-authorized immutable run and may not create a new run or consume/reuse an authorization value.
- Consumes: Tasks 1-3, `create_worktree_marker`, `capture_worktree_snapshot`, `validate_patch`, and fixed-argv command execution.
- Produces for Task 5: a run in `verifying` with a safe diff and verification manifest.

- [ ] **Step 1: Write failing end-to-end fake-worker tests**

```python
def test_runner_creates_worktree_records_events_and_verifies_allowed_change(self):
    snapshot = self.runner.execute(self.task, self.preflight)
    self.assertEqual("reviewing", snapshot["run"]["status"])
    self.assertEqual(["calculator.py"], snapshot["change"]["changed_paths"])
    self.assertEqual(0, snapshot["verification"][0]["returncode"])

def test_runner_blocks_change_outside_allowed_paths(self):
    worker = FakeWorker(changes={"calculator.py": "safe", "outside.py": "forbidden"})
    snapshot = self.runner_with(worker).execute(self.task, self.preflight)
    self.assertEqual("failed_scope", snapshot["run"]["status"])
    self.assertFalse(self.remote.called)

def test_runner_blocks_verification_side_effect(self):
    task = replace(self.task, verification_commands=((sys.executable, "write_file.py"),))
    snapshot = self.runner.execute(task, self.preflight)
    self.assertEqual("failed_verification", snapshot["run"]["status"])
```

- [ ] **Step 2: Run RED**

Expected: import failure for `LocalAgentRunner`.

- [ ] **Step 3: Add a public local-agent worktree preparation primitive**

Extract the proven preflight/create/marker logic from `WorktreeCodeExecutor` without changing its behavior. The new function accepts a validated repository root, worktree root and run ID; it creates only `run_<id>` under a root matching `/tmp/his_harness` or `/private/tmp/his_harness` and returns anchored identities.

- [ ] **Step 4: Implement runner execution and verification**

Create state/events before process start. After Worker exit, revalidate source/worktree identities, reject `.git` mutation and changed paths outside the contract, run `git diff --check`, then execute each verification argv with `shell=False`. Capture before/after snapshots and reject verification commands that alter tracked or untracked worktree state.

- [ ] **Step 5: Implement retry as a new attempt**

Retry is allowed only for `interrupted`, `failed_worker`, `failed_verification` or `changes_requested`. Revalidate the original contract hash, source identity and initial HEAD. Keep the same worktree, append a new attempt, and never overwrite prior events or artifacts.

- [ ] **Step 6: Run GREEN plus existing worktree regression tests**

Run Task 4 tests with `tests.test_worktree_executor tests.test_worktree_lifecycle tests.test_task_manager_rollback` under strict warnings.

---

### Task 5: Independent read-only Reviewer and artifact manifest

**Files:**
- Create: `app/local_agent_review.py`
- Create: `tests/test_local_agent_review.py`
- Modify: `app/local_agent_runner.py`
- Modify: `tests/test_local_agent_runner.py`

**Interfaces:**
- Produces: `LocalAgentReviewer.review(run_snapshot, worktree_path) -> LocalAgentReviewResult`.
- Produces schema `his-local-agent-review.v1` with `verdict`, `findings`, `summary` and `review_hash`.
- Consumes: Task 3 worker in `read-only` mode and Task 2 artifact repository.

- [ ] **Step 1: Write failing structured-review tests**

```python
def test_approved_review_moves_run_to_human_confirmation(self):
    snapshot = self.runner.execute(self.task, self.preflight)
    self.assertEqual("awaiting_human_confirmation", snapshot["run"]["status"])
    self.assertEqual("approved", snapshot["review"]["verdict"])

def test_changes_requested_review_never_reaches_confirmation(self):
    payload = {"schema_version": "his-local-agent-review.v1", "verdict": "changes_requested", "findings": [{"severity": "important", "path": "calculator.py", "line": 1, "message": "Incorrect boundary"}], "summary": "Fix required"}
    snapshot = self.run_review(payload)
    self.assertEqual("changes_requested", snapshot["run"]["status"])

def test_secret_bearing_review_fails_closed(self):
    payload = {"schema_version": "his-local-agent-review.v1", "verdict": "approved", "findings": [], "summary": "Bearer " + "a" * 48}
    snapshot = self.run_review(payload)
    self.assertEqual("failed_review", snapshot["run"]["status"])
```

- [ ] **Step 2: Run RED**

Expected: Reviewer module missing and runner stops before confirmation.

- [ ] **Step 3: Implement the fixed review schema and prompt**

Validate maximum finding count, stable severity values, relative paths, bounded line numbers and sensitive-text rejection. The Reviewer process receives read-only sandbox and cannot mutate the worktree.

- [ ] **Step 4: Persist safe artifacts**

Write artifacts under the run-owned output root using atomic writes: `final.diff`, `final.patch`, `verification.json`, `review.json`, `manifest.json`. Repository rows store only relative path, SHA-256, size and kind. Re-read and hash every artifact before returning a snapshot.

- [ ] **Step 5: Run GREEN and tamper tests**

Tampered artifact bytes, symlink output paths, malformed review JSON, secret-bearing findings and Reviewer worktree mutation must fail closed.

---

### Task 6: One-time human confirmation and local-only application

**Files:**
- Create: `app/local_agent_confirmation.py`
- Create: `tests/test_local_agent_confirmation.py`
- Modify: `app/local_agent_runner.py`

**Interfaces:**
- Produces: `issue_local_apply_confirmation(run_id, requested_by) -> LocalApplyConfirmation`.
- Produces: `confirm_and_apply(run_id, token, requested_by) -> dict`.
- Consumes: `apply_final_diff_to_project()` and its existing journal/recovery contract.

- [ ] **Step 1: Write failing confirmation tests**

```python
def test_valid_one_time_confirmation_applies_only_final_diff_locally(self):
    initial_head = self.git("rev-parse", "HEAD", cwd=self.project).stdout.strip()
    confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user")
    result = self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user")
    self.assertEqual("locally_applied", result["run"]["status"])
    self.assertEqual(initial_head, self.git("rev-parse", "HEAD", cwd=self.project).stdout.strip())

def test_expired_confirmation_has_zero_apply(self):
    confirmation = self.service.issue_local_apply_confirmation(self.run_id, "local-user", now=self.now)
    result = self.service.confirm_and_apply(self.run_id, confirmation.token, "local-user", now=self.now + timedelta(hours=2))
    self.assertEqual("confirmation_expired", result["status"])
    self.assertEqual(self.initial_source, (self.project / "calculator.py").read_text())
```

- [ ] **Step 2: Run RED**

Expected: confirmation service missing.

- [ ] **Step 3: Implement confirmation issue/consume**

Store only token hash, bind it to run ID, contract hash, artifact hash, requester and expiry. Consume inside `begin immediate`; reuse and concurrent confirmation must have one winner. Do not expose the token in list/status/audit APIs.

- [ ] **Step 4: Revalidate immediately before local application**

Recheck project/root/.git identities, HEAD, allowed-path dirtiness, artifact hash and approved review. Invoke `apply_final_diff_to_project()` only after all checks. Preserve its durable journal and recovery behavior; never commit or push.

- [ ] **Step 5: Run GREEN and existing local-apply recovery regressions**

Include `tests.test_task_manager_rollback tests.test_delivery_closure tests.test_worktree_executor` with strict warnings.

---

### Task 7: Local CLI and fully fake closed-loop acceptance

**Files:**
- Modify: `tools/task_manager.py`
- Create: `tests/test_local_agent_cli.py`
- Create: `tests/fixtures/local_agent_task.json`
- Modify: `README.md`
- Modify: `docs/manager-runbook.md`

**Interfaces:**
- Produces CLI commands: `local-agent run`, `local-agent status`, `local-agent retry`, `local-agent issue-confirmation`, `local-agent confirm-apply`.
- Consumes: Tasks 1-6 only; no Manager HTTP route is added.

- [ ] **Step 1: Write failing CLI tests**

Test help output, JSON-only safe output, mandatory explicit temporary DB, activation flag, contract path, status lookup, retry eligibility and confirmation flow. Patch subprocess creation with the Task 3 fake factory; assert no network, remote Git or default DB access.

- [ ] **Step 2: Run RED**

Expected: argparse rejects `local-agent` commands.

- [ ] **Step 3: Implement CLI commands**

`run` requires `--contract`, `--allow-real-agent` and `--authorization-id`; secrets never appear in output. `status` is read-only. `issue-confirmation` prints the token once. `confirm-apply` requires the token and returns a safe snapshot.

- [ ] **Step 4: Add local runbook**

Document the exact temporary-fixture workflow, what each state means, how to retry, how to confirm local apply and what remains disabled. Do not present fake-worker tests as real-model evidence.

- [ ] **Step 5: Run fake full-loop GREEN**

Run Tasks 1-7 tests, existing worktree/local-apply tests, runtime policy tests, database governance and Provider authorization tests under `ResourceWarning` strict mode.

---

### Task 8: Explicitly authorized real Codex fixture acceptance and final review

**Files:**
- Create: `.superpowers/sdd/stage-f-real-agent-acceptance.md`
- Modify only if a real acceptance defect is found: Task 1-7 files and their covering tests.

**Interfaces:**
- Consumes the CLI from Task 7 and bundled Codex CLI.
- Produces evidence for the exact claim “a single user can run one real local code-change loop on this Mac.”

- [ ] **Step 1: Build a disposable Git fixture outside the repository**

Create the repository with `fixture_root=$(mktemp -d /private/tmp/his_harness_stage_f_real.XXXXXX)`. It contains one failing Python unit test and one allowed source file. Record its initial HEAD and assert it has no remote.

- [ ] **Step 2: Run the real local-agent command once**

Use an explicit temporary `HARNESS_DB_PATH`, temporary knowledge home, a unique authorization ID and the bundled Codex executable. Do not use production credentials files or a business repository.

Expected states: `worker_running -> verifying -> reviewing -> awaiting_human_confirmation`.

- [ ] **Step 3: Inspect evidence before confirmation**

Verify the test passes in the worktree, Reviewer verdict is approved, changed paths equal the allowlist, artifact hashes validate, the original fixture repository remains unchanged and no remote/external/database write occurred.

- [ ] **Step 4: Issue one-time confirmation and apply locally**

Confirm once, expect `locally_applied`, run the fixture test in the original fixture repository, then prove a second confirmation attempt fails and no Git commit was created.

- [ ] **Step 5: Run independent whole-change review**

Review contract enforcement, subprocess lifecycle, persistence, artifact integrity, local application, sensitive output and regression evidence. Fix all Critical/Important findings with covering tests and repeat review.

- [ ] **Step 6: Write the acceptance report**

Record actual commands, run ID, state transitions, test/review results, artifact hashes, external-write proof, default DB metadata comparison and residual limitations. Mark the stage incomplete if the real model call, independent review or local apply was not actually executed successfully.
