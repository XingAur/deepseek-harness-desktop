# Extension Center Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Extension Center operations from deleting externally managed MCP/Skill data, keep usage collection responsive and private, and publish the repair as desktop `0.1.42`.

**Architecture:** MCP adds a SQLite per-target projection ledger and changes config only where that ledger proves ownership. Skills installation is prepared in target-local staging directories and committed with compensating renames. Usage scanning receives explicit resource limits and moves to Tokio's blocking pool. The release uses the existing Runtime desktop-plugin fingerprint guard, verified by a workflow contract test, and new immutable desktop/runtime identities.

**Tech Stack:** Rust 2024, rusqlite, serde JSON/TOML, Tokio/Tauri, React/TypeScript/Vitest, Node release scripts, GitHub Actions.

---

## File structure

- Modify: `src-tauri/src/mcp_manager/model.rs` — projection and conflict types.
- Modify: `src-tauri/src/mcp_manager/store.rs` — SQLite projection migration and methods.
- Modify: `src-tauri/src/mcp_manager/service.rs` — ownership-aware reconciliation and Rust tests.
- Modify: `packages/dsh-plugin-desktop/src/client/extension-center/McpPanel.tsx` — target-wide reconciliation entrypoint.
- Modify: `packages/dsh-plugin-desktop/tests/mcp-panel.spec.tsx` — UI regression test.
- Modify: `src-tauri/src/skills_manager/service.rs` — staged transaction, rollback, and path-name tests.
- Modify: `src-tauri/src/usage_stats/service.rs` — bounds, path-free diagnostics, and tests.
- Modify: `src-tauri/src/commands.rs` — `spawn_blocking` command dispatch.
- Modify: `packages/dsh-plugin-desktop/tests/usage-panel.spec.tsx` — sanitized failure fixture.
- Modify: `release/versions.json`, `package.json`, `package-lock.json`, `src-tauri/tauri.conf.json`, `src-tauri/Cargo.toml`, `src-tauri/Cargo.lock` — release identity.
- Modify: `scripts/workflow-contract.test.ts` — fingerprint guard ordering contract.

### Task 1: Make MCP projection ownership explicit

**Files:**
- Modify: `src-tauri/src/mcp_manager/model.rs`
- Modify: `src-tauri/src/mcp_manager/store.rs`
- Modify: `src-tauri/src/mcp_manager/service.rs`
- Test: `src-tauri/src/mcp_manager/store.rs`
- Test: `src-tauri/src/mcp_manager/service.rs`

- [ ] **Step 1: Write failing ownership regressions.**

Add three tests beside `delete_removes_from_store_and_both_target_files`:

1. Synchronize `fetch` only to Claude, write a same-named Codex entry by hand, delete `fetch`, and assert only the Claude entry is removed.
2. Synchronize a Claude+Codex definition, change Claude's `command` directly, remove Claude from `targets`, then assert `sync_target(Claude)` returns a conflict without changing the external value.
3. Synchronize a Claude+Codex definition, remove Codex from `targets`, sync Codex, and assert the formerly managed Codex entry is removed.

~~~rust
assert!(matches!(
    service.sync_target(McpTarget::Claude),
    Err(McpManagerError::ExternalChange { target: McpTarget::Claude, name }) if name == "fetch"
));
assert_eq!(read_claude_json(&env)["mcpServers"]["fetch"]["command"], "external-node");
~~~

- [ ] **Step 2: Verify the regressions fail on current behavior.**

Run:

~~~powershell
& C:\Users\xyj\.cargo\bin\cargo.exe test --manifest-path src-tauri/Cargo.toml --locked mcp_manager:: --lib
~~~

Expected: the external Codex entry is removed and a deselected entry remains, proving the unsafe behavior.

- [ ] **Step 3: Add a transactional projection table and data model.**

Extend `CREATE_TABLE` with `mcp_projections(server_id, target, name, fingerprint, PRIMARY KEY(server_id, target))`, plus an index on `(target, name)`. Add methods to list by target/server and atomically replace one target's set. `McpStore::delete` must delete a definition and its projections in one SQLite transaction.

~~~rust
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct McpProjection {
    pub server_id: String,
    pub target: McpTarget,
    pub name: String,
    pub fingerprint: String,
}

pub fn projections_for_target(&self, target: McpTarget) -> Result<Vec<McpProjection>>;
pub fn projections_for_server(&self, server_id: &str) -> Result<Vec<McpProjection>>;
pub fn replace_projections_for_target(
    &self, target: McpTarget, projections: &[McpProjection],
) -> Result<()>;
~~~

The fingerprint is lower-case SHA-256 over canonical `command`, `args`, and sorted `env`, never over JSON/TOML text formatting.

- [ ] **Step 4: Reconcile a target before any config write.**

Refactor `sync_claude` and `sync_codex` around target adapters that read and write a `BTreeMap<String, RawServer>`. Load desired definitions, existing config entries, and ledger records. Reject the complete operation before writing when a ledger-owned entry has a different current fingerprint. Remove a deselected entry only when the stored fingerprint still matches. A desired untracked same-name entry can be claimed only when its fingerprint already equals the desired payload; otherwise it is a conflict.

~~~rust
#[error("mcp_external_change: {target}/{name}")]
ExternalChange { target: McpTarget, name: String },
~~~

After `atomic_write` succeeds, replace that target's ledger rows with the desired projections. Preserve every unrelated JSON/TOML key and untracked MCP entry. Imported entries remain untracked until a matching sync claims them.

- [ ] **Step 5: Restrict delete to proven projections.**

Replace the loop over `McpTarget::ALL` in `delete` with `projections_for_server(id)`. Apply the same fingerprint validation for each record. If any projection is externally changed, return `ExternalChange` and retain the definition; otherwise remove matching config entries and delete the database row.

- [ ] **Step 6: Run tests and commit the MCP service.**

Run:

~~~powershell
& C:\Users\xyj\.cargo\bin\cargo.exe test --manifest-path src-tauri/Cargo.toml --locked mcp_manager:: --lib
~~~

Expected: all MCP tests pass, including cross-target isolation, stale-projection removal, and conflict rejection.

~~~powershell
git add -- src-tauri/src/mcp_manager/model.rs src-tauri/src/mcp_manager/store.rs src-tauri/src/mcp_manager/service.rs
git commit -m "fix(mcp): protect externally managed target entries"
~~~

### Task 2: Make deselected MCP targets reachable from the panel

**Files:**
- Modify: `packages/dsh-plugin-desktop/src/client/extension-center/McpPanel.tsx`
- Test: `packages/dsh-plugin-desktop/tests/mcp-panel.spec.tsx`

- [ ] **Step 1: Write a failing deselect-and-sync panel test.**

Use statuses with both targets installed. Render a server initially targeting both targets, edit it to keep only Codex, save, then click the target-wide synchronization button. Assert calls for both targets occur in `MCP_TARGETS` order.

~~~ts
expect(bridge.requestV2).toHaveBeenCalledWith('mcp.sync', undefined, { target: 'claude' })
expect(bridge.requestV2).toHaveBeenCalledWith('mcp.sync', undefined, { target: 'codex' })
~~~

- [ ] **Step 2: Run the test and verify it fails.**

~~~powershell
npm run test -w @dsh/desktop-plugin -- --run tests/mcp-panel.spec.tsx
~~~

Expected: current `server.targets.filter(installedOf)` sends only Codex.

- [ ] **Step 3: Reconcile every installed target.**

Replace the `syncRow` loop with the code below. Keep its busy state and post-operation refresh. Update the title so it explicitly says the operation reconciles all installed targets; `mcp.sync` is target-wide, not row-scoped.

~~~ts
for (const target of MCP_TARGETS.filter(installedOf)) {
  await syncTarget(bridge, target)
}
~~~

- [ ] **Step 4: Run the panel suite and commit.**

~~~powershell
npm run test -w @dsh/desktop-plugin -- --run tests/mcp-panel.spec.tsx
git add -- packages/dsh-plugin-desktop/src/client/extension-center/McpPanel.tsx packages/dsh-plugin-desktop/tests/mcp-panel.spec.tsx
git commit -m "fix(mcp-ui): reconcile deselected targets"
~~~

Expected: all MCP panel tests pass.

### Task 3: Make ZIP Skill installation all-or-rollback

**Files:**
- Modify: `src-tauri/src/skills_manager/service.rs`
- Test: `src-tauri/src/skills_manager/service.rs`

- [ ] **Step 1: Write a failing multi-target rollback test.**

Create different existing `demo` skills under Claude and Codex. Introduce a test-only commit failpoint that returns `SkillsError::Io("injected commit failure")` immediately before the second replacement. Install a new `demo` ZIP to both targets. Assert the method errors, both existing manifests remain, and neither root contains `.install-*` or `.transaction-*`.

~~~rust
assert_eq!(std::fs::read_to_string(claude.join("demo/SKILL.md")).unwrap(), "# old claude");
assert_eq!(std::fs::read_to_string(codex.join("demo/SKILL.md")).unwrap(), "# old codex");
~~~

- [ ] **Step 2: Run the test and verify the partial install.**

~~~powershell
& C:\Users\xyj\.cargo\bin\cargo.exe test --manifest-path src-tauri/Cargo.toml --locked skills_manager:: --lib
~~~

Expected: under the failpoint, current sequential copying leaves the first target replaced.

- [ ] **Step 3: Prepare all work before changing final destinations.**

Validate every target, archive entry, and resolved skill name first; reject duplicate skill names. Copy each verified source into a unique `.install-<uuid>/<name>` directory inside the target root so the final rename stays within one filesystem. Track each prepared replacement.

~~~rust
struct PendingInstall {
    target: SkillTarget,
    name: String,
    staged: PathBuf,
    destination: PathBuf,
    backup: Option<PathBuf>,
}
~~~

- [ ] **Step 4: Commit with reverse compensation.**

For every pending record, rename an old destination to `.transaction-<uuid>/backup/<name>`, then rename the staged directory to its final destination. Record completed moves. On any failure, traverse completed records in reverse: remove the new destination if it exists, then rename the backup back. Return the original error after best-effort cleanup. On success, remove all `.install-*` and `.transaction-*` directories. Keep `sync`'s existing single-target `.trash-*` behavior unchanged.

- [ ] **Step 5: Tighten the skill-name boundary.**

Reject control characters in `validate_skill_name` as well as separators, dot names, and NUL. Extend the current invalid-name test matrix with `"bad\\nname"`.

- [ ] **Step 6: Run tests and commit.**

~~~powershell
& C:\Users\xyj\.cargo\bin\cargo.exe test --manifest-path src-tauri/Cargo.toml --locked skills_manager:: --lib
git add -- src-tauri/src/skills_manager/service.rs
git commit -m "fix(skills): roll back failed multi-target installs"
~~~

Expected: normal installs, validation, and fault-injected rollback all pass.

### Task 4: Bound and isolate usage collection

**Files:**
- Modify: `src-tauri/src/usage_stats/service.rs`
- Modify: `src-tauri/src/commands.rs`
- Modify: `packages/dsh-plugin-desktop/tests/usage-panel.spec.tsx`
- Test: `src-tauri/src/usage_stats/service.rs`
- Test: `packages/dsh-plugin-desktop/tests/usage-panel.spec.tsx`

- [ ] **Step 1: Write failing resource/privacy tests.**

Update platform-specific unreadable-file tests to prove that no failure contains `data_root.display()` or an absolute session path. Add a unique-model test that exceeds the aggregate-entry cap and a total-byte test that proves later files are not scanned after the shared budget is consumed.

~~~rust
assert!(summary.failures.iter().all(|failure| !failure.contains(&data_root.display().to_string())));
assert!(summary.entries.len() <= MAX_AGGREGATE_ENTRIES);
~~~

- [ ] **Step 2: Run the focused usage tests and verify redaction fails.**

~~~powershell
& C:\Users\xyj\.cargo\bin\cargo.exe test --manifest-path src-tauri/Cargo.toml --locked usage_stats:: --lib
~~~

Expected: current failure text includes absolute paths.

- [ ] **Step 3: Enforce deterministic limits and redact at the Rust boundary.**

Add these named limits, pass the remaining total budget to `scan_file`/`read_capped`, and stop scanning once it is exhausted. Skip overlong JSONL lines. Normalize overlong model identifiers to `unknown`; refuse new aggregate keys after the cap and record one generic limit failure.

~~~rust
const MAX_TOTAL_BYTES: usize = 128 * 1024 * 1024;
const MAX_FILE_BYTES: usize = 2 * 1024 * 1024;
const MAX_LINE_BYTES: usize = 128 * 1024;
const MAX_AGGREGATE_ENTRIES: usize = 1_024;
~~~

Use generic strings such as `会话文件读取失败` and `会话目录无法读取`, not paths or OS error text. Retain `UsageSummary.failures: Vec<String>` to avoid an unnecessary bridge contract change.

- [ ] **Step 4: Dispatch scanning through Tokio's blocking pool.**

After generation/session validation, clone the state Arc and invoke `summary` through `spawn_blocking`. Preserve the existing command result shape and surface a bounded join error.

~~~rust
let service = Arc::clone(service.inner());
tokio::task::spawn_blocking(move || service.summary())
    .await
    .map_err(|error| format!("用量统计任务异常结束: {error}"))
~~~

- [ ] **Step 5: Verify the UI receives only sanitized data.**

Change the UsagePanel warning fixture to a generic failure category and assert it renders. Do not add client-side pathname parsing: privacy belongs at the Rust bridge boundary.

- [ ] **Step 6: Run suites and commit.**

~~~powershell
& C:\Users\xyj\.cargo\bin\cargo.exe test --manifest-path src-tauri/Cargo.toml --locked usage_stats:: --lib
npm run test -w @dsh/desktop-plugin -- --run tests/usage-panel.spec.tsx
git add -- src-tauri/src/usage_stats/service.rs src-tauri/src/commands.rs packages/dsh-plugin-desktop/tests/usage-panel.spec.tsx
git commit -m "fix(usage): bound scans and redact local paths"
~~~

Expected: Rust limits/privacy tests and UsagePanel tests pass.

### Task 5: Advance immutable release identities and prove the Runtime guard order

**Files:**
- Modify: `release/versions.json`
- Modify: `package.json`
- Modify: `package-lock.json`
- Modify: `src-tauri/tauri.conf.json`
- Modify: `src-tauri/Cargo.toml`
- Modify: `src-tauri/Cargo.lock`
- Modify: `scripts/workflow-contract.test.ts`
- Test: `scripts/release-versions.test.ts`
- Test: `scripts/workflow-contract.test.ts`

- [ ] **Step 1: Write a workflow ordering contract for the existing fingerprint gate.**

In `builds both platforms before one immutable final publication job`, assert `Verify runtime plugin currency` exists and occurs before `Assemble managed Runtime`.

~~~ts
const pluginCurrency = workflow.indexOf('      - name: Verify runtime plugin currency')
const runtimeAssemblyStart = workflow.indexOf('      - name: Assemble managed Runtime')
expect(pluginCurrency).toBeGreaterThan(-1)
expect(pluginCurrency).toBeLessThan(runtimeAssemblyStart)
~~~

- [ ] **Step 2: Run release-contract tests.**

~~~powershell
npm run test -- --run scripts/workflow-contract.test.ts scripts/release-versions.test.ts scripts/check-runtime-plugin-currency.test.ts
~~~

Expected: all pass, proving the release retains the existing Runtime fingerprint protection rather than duplicating it.

- [ ] **Step 3: Bump every derived release source.**

Set desktop version to `0.1.42` and Runtime version to `0.1.19-preview`. Update only values asserted by `assertReleaseVersionConsistency`; do not change DSH, Node, pnpm, or upstream pins. Update `package-lock.json` and `Cargo.lock` through their native package records, never a broad text replacement.

- [ ] **Step 4: Run version and targeted regression checks.**

~~~powershell
npm run release:versions:check
npm run test -- --run scripts/workflow-contract.test.ts scripts/release-versions.test.ts scripts/check-runtime-plugin-currency.test.ts
npm run test -w @dsh/desktop-plugin -- --run tests/mcp-panel.spec.tsx tests/skills-panel.spec.tsx tests/usage-panel.spec.tsx
& C:\Users\xyj\.cargo\bin\cargo.exe test --manifest-path src-tauri/Cargo.toml --locked mcp_manager:: --lib
& C:\Users\xyj\.cargo\bin\cargo.exe test --manifest-path src-tauri/Cargo.toml --locked skills_manager:: --lib
& C:\Users\xyj\.cargo\bin\cargo.exe test --manifest-path src-tauri/Cargo.toml --locked usage_stats:: --lib
~~~

Expected: every command exits 0. Preserve and report an unrelated baseline failure before tagging.

- [ ] **Step 5: Commit release preparation.**

~~~powershell
git add -- release/versions.json package.json package-lock.json src-tauri/tauri.conf.json src-tauri/Cargo.toml src-tauri/Cargo.lock scripts/workflow-contract.test.ts
git commit -m "release: prepare desktop v0.1.42"
~~~

- [ ] **Step 6: Push, tag, and validate actual published assets.**

After a clean audit, push `main`, create only `desktop-v0.1.42`, and wait for GitHub Actions. Verify the release target SHA, Windows EXE/signature/latest metadata, macOS DMG, both Runtime archives/manifests, and manifest desktop-plugin fingerprints. Treat any mismatch as a publishing failure; never attempt to overwrite `desktop-v0.1.41`.

~~~powershell
git status --short --branch
git log --oneline origin/main..HEAD
git push origin main
git tag -a desktop-v0.1.42 -m "DeepSeek Harness Desktop 0.1.42"
git push origin desktop-v0.1.42
~~~
