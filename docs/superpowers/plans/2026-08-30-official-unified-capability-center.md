# Official Unified Capability Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the full 1,946-entry plugin market and an official-style capability center that unifies Skills, plugin MCP servers, custom MCP/HTTP/database connections, layered testing, and real diagnostics without rewriting existing user data.

**Architecture:** Keep the official Runtime and extension inventory as the execution source of truth. Add a typed client domain for generalized connection profiles, an additive Rust `harness_connection_profiles_v2` store alongside the legacy table, and a unified presenter that merges source-managed MCP entries with custom and legacy profiles. The direct browser preview imports the same generated plugin snapshot and remains read-only.

**Tech Stack:** React 18, TypeScript 6, Vitest and Testing Library, Tauri 2, Rust, rusqlite, existing `--dsw-*` official design tokens.

## Global Constraints

- The official DeepSeek Harness Runtime remains the owner of official plugins, Skills, MCP execution, and permission semantics.
- Do not destructively migrate, reset, delete, copy, or rewrite `harness_connection_profiles` rows automatically.
- Secrets, tokens, passwords, authorization headers, and environment-secret values must never cross the desktop bridge in a profile payload.
- Remote MCP and HTTP API endpoints require HTTPS, except loopback HTTP under the existing safety rule.
- The direct browser preview is read-only and must not install plugins or write formal profile data.
- Use the official visual source `/Users/lym/.codex/visualizations/2026/08/30/01a05094-96cd-7851-9148-c4d923b7bd86/official-04-plugin-config.png` as a hard fidelity contract.
- Use existing `--dsw-*` tokens; do not add a custom palette, gradients, glass effects, dashboard cards, emoji, text-symbol icons, handcrafted SVG, or CSS art.
- Preserve all pre-existing worktree changes and do not commit, push, create a PR, release, deploy, or mutate external systems in this implementation session.

---

### Task 1: Shared Preview Plugin Catalog

**Files:**
- Create: `packages/dsh-plugin-desktop/src/client/extensions/preview-catalog.ts`
- Modify: `packages/dsh-plugin-desktop/src/client/advanced-shell.ts`
- Modify: `packages/dsh-plugin-desktop/tests/advanced-shell.spec.ts`
- Modify: `packages/dsh-plugin-desktop/tests/plugin-market.spec.tsx`

**Interfaces:**
- Consumes: `plugin-catalog/plugins.json` with `{ count, entries }`.
- Produces: `listPreviewCatalog(payload): CatalogPage`, using the same query, category, offset, and limit contract as `plugin.catalog.list`.

- [ ] **Step 1: Write the failing full-snapshot and search tests**

```ts
it('pages the same generated snapshot in direct preview', async () => {
  const page = await createPreviewDesktopBridge().requestV2<CatalogPage>(
    'plugin.catalog.list', undefined, { offset: 0, limit: 30 },
  )
  expect(page.total).toBe(1946)
  expect(page.entries).toHaveLength(30)
})

it('searches Chinese and English descriptions in preview', async () => {
  const page = await createPreviewDesktopBridge().requestV2<CatalogPage>(
    'plugin.catalog.list', undefined, { query: 'BM25', offset: 0, limit: 30 },
  )
  expect(page.entries.some((entry) => entry.id === '00080000/dsh-project-memory')).toBe(true)
})
```

- [ ] **Step 2: Run the test and verify the current six-entry implementation fails**

Run: `npx vitest run packages/dsh-plugin-desktop/tests/advanced-shell.spec.ts packages/dsh-plugin-desktop/tests/plugin-market.spec.tsx`

Expected: the full-snapshot test reports `expected 6 to be 1946`.

- [ ] **Step 3: Implement one preview catalog source**

```ts
import snapshot from '../../../../../plugin-catalog/plugins.json'

export interface PreviewCatalogPayload {
  query?: string
  category?: string
  offset?: number
  limit?: number
}

export function listPreviewCatalog(payload: PreviewCatalogPayload) {
  const query = payload.query?.trim().toLocaleLowerCase() ?? ''
  const category = payload.category ?? ''
  const offset = Math.max(0, payload.offset ?? 0)
  const limit = Math.min(50, Math.max(1, payload.limit ?? 30))
  const entries = snapshot.entries.filter((entry) => {
    const searchable = `${entry.id} ${entry.displayName} ${entry.category} ${entry.descriptionZh} ${entry.descriptionEn}`.toLocaleLowerCase()
    return (category === '' || entry.category === category) && (query === '' || searchable.includes(query))
  })
  const counts = new Map<string, number>()
  for (const entry of snapshot.entries) counts.set(entry.category, (counts.get(entry.category) ?? 0) + 1)
  return { total: entries.length, offset, categories: [...counts].map(([id, count]) => ({ id, count })), entries: entries.slice(offset, offset + limit) }
}
```

Remove `PREVIEW_CATALOG` and delegate `plugin.catalog.list` to `listPreviewCatalog`.

- [ ] **Step 4: Verify the focused tests pass**

Run: `npx vitest run packages/dsh-plugin-desktop/tests/advanced-shell.spec.ts packages/dsh-plugin-desktop/tests/plugin-market.spec.tsx`

Expected: all selected tests pass and no preview install action is enabled.

- [ ] **Step 5: Review the diff without committing**

Run: `git diff --check -- packages/dsh-plugin-desktop/src/client/extensions/preview-catalog.ts packages/dsh-plugin-desktop/src/client/advanced-shell.ts packages/dsh-plugin-desktop/tests/advanced-shell.spec.ts packages/dsh-plugin-desktop/tests/plugin-market.spec.tsx`

Expected: exit code 0. A commit is intentionally not created because this session has no commit authorization.

---

### Task 2: Generalized Connection Domain And Bridge Contract

**Files:**
- Create: `packages/dsh-plugin-desktop/src/client/model-agent/connection-model.ts`
- Modify: `packages/dsh-plugin-desktop/src/client/bridge-contract.ts`
- Modify: `src/bridge-contract.ts`
- Modify: `src/workbench-bridge.ts`
- Create: `packages/dsh-plugin-desktop/tests/connection-model.spec.ts`
- Modify: `src/bridge-contract.test.ts`
- Modify: `src/workbench-bridge.test.ts`

**Interfaces:**
- Produces `ConnectionProfile` with `kind: 'mcp' | 'http-api' | 'database'`, `transport: 'stdio' | 'http' | 'sse' | 'database'`, `source: 'custom' | 'legacy'`, free-form `templateId`, safe typed config, and optional `lastTest`.
- Produces `ConnectionTestResult` with exactly five independently reported layers: configuration, network, protocol, authentication, permission.
- Extends existing `harness.connection.*` actions additively; existing payloads remain valid.

- [ ] **Step 1: Write failing allowlist, custom-type, and secret-rejection tests**

```ts
expect(isVersionedBridgeRequest(request('harness.connection.save', {
  kind: 'http-api', transport: 'http', templateId: 'custom', displayName: '内部知识库',
  endpoint: 'https://knowledge.example.com', enabled: true, readOnly: true,
}))).toBe(true)

expect(isVersionedBridgeRequest(request('harness.connection.save', {
  kind: 'http-api', transport: 'http', displayName: 'bad', endpoint: 'https://example.com',
  enabled: true, readOnly: true, authorization: 'Bearer secret',
}))).toBe(false)
```

- [ ] **Step 2: Run the bridge tests and verify they fail on unsupported types**

Run: `npx vitest run src/bridge-contract.test.ts src/workbench-bridge.test.ts packages/dsh-plugin-desktop/tests/connection-model.spec.ts`

Expected: `http-api` and safe type-specific fields are rejected before implementation.

- [ ] **Step 3: Implement normalized types and validators**

```ts
export type ConnectionKind = 'mcp' | 'http-api' | 'database'
export type ConnectionTransport = 'stdio' | 'http' | 'sse' | 'database'
export type TestLayerState = 'passed' | 'failed' | 'not-configured' | 'not-tested' | 'approval-required'

export interface ConnectionTestLayer {
  id: 'configuration' | 'network' | 'protocol' | 'authentication' | 'permission'
  label: '配置' | '网络' | '协议' | '认证' | '权限'
  state: TestLayerState
  message: string
}
```

Allow only `profileId`, `kind`, `transport`, `templateId`, `displayName`, `endpoint`, `command`, `args`, `environmentKeys`, `workingDirectoryPolicy`, `healthPath`, `readOnly`, `enabled`, and `credentialId`. Reject unknown keys and any nested key matching the existing secret-name denylist.

- [ ] **Step 4: Update the workbench mapper and both bridge action unions**

`mapVersionedPayload` must forward only validated fields, preserve legacy calls, and never forward secret values. `harness.connection.list` accepts `kind: 'mcp' | 'http-api' | 'database'` or no filter.

- [ ] **Step 5: Verify the bridge tests pass and diff is clean**

Run: `npx vitest run src/bridge-contract.test.ts src/workbench-bridge.test.ts packages/dsh-plugin-desktop/tests/connection-model.spec.ts`

Run: `git diff --check -- packages/dsh-plugin-desktop/src/client/model-agent/connection-model.ts packages/dsh-plugin-desktop/src/client/bridge-contract.ts src/bridge-contract.ts src/workbench-bridge.ts`

Expected: selected tests and diff check pass. No commit is created.

---

### Task 3: Additive Rust V2 Connection Store And Layered Test Reply

**Files:**
- Modify: `src-tauri/src/commands.rs`
- Modify: `src-tauri/src/harness/mod.rs`

**Interfaces:**
- Keeps `harness_connection_profiles` intact.
- Creates `harness_connection_profiles_v2` only with `CREATE TABLE IF NOT EXISTS`.
- Lists a union of v2 and legacy rows, with v2 taking precedence only for the exact edited profile ID.
- Returns `HarnessConnectionTestReply { profileId, tested, summary, layers }` while retaining `testKind` and `message` for compatibility.

- [ ] **Step 1: Add failing Rust tests for additive schema and validation**

```rust
#[test]
fn generalized_connection_schema_preserves_legacy_rows() {
    let connection = rusqlite::Connection::open_in_memory().unwrap();
    ensure_harness_connection_table(&connection).unwrap();
    connection.execute(
        "INSERT INTO harness_connection_profiles
         (profile_id, kind, provider_id, display_name, endpoint, read_only, enabled, credential_id, created_at, updated_at)
         VALUES (?1, 'mcp', 'yunxiao', '云效旧连接', 'https://devops.aliyun.com', 1, 1, NULL, ?2, ?2)",
        rusqlite::params!["legacy-yunxiao", "2026-08-30T00:00:00Z"],
    ).unwrap();
    ensure_harness_connection_v2_table(&connection).unwrap();
    let legacy_count: i64 = connection.query_row("SELECT COUNT(*) FROM harness_connection_profiles", [], |row| row.get(0)).unwrap();
    assert_eq!(legacy_count, 1);
}

#[test]
fn tcp_success_does_not_mark_protocol_or_authentication_passed() {
    let reply = layered_network_result("profile", Ok(Duration::from_millis(3)));
    assert_eq!(reply.layers[1].state, "passed");
    assert_eq!(reply.layers[2].state, "not-tested");
    assert_eq!(reply.layers[3].state, "not-tested");
}
```

- [ ] **Step 2: Run the focused Rust tests and verify missing v2 helpers fail**

Run: `cargo test --manifest-path src-tauri/Cargo.toml commands::tests::generalized_connection_schema_preserves_legacy_rows commands::tests::tcp_success_does_not_mark_protocol_or_authentication_passed`

Expected: compilation fails because the v2 schema and layered result helpers do not exist.

- [ ] **Step 3: Implement the additive table and union mapping**

```sql
CREATE TABLE IF NOT EXISTS harness_connection_profiles_v2 (
  profile_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK(kind IN ('mcp', 'http-api', 'database')),
  transport TEXT NOT NULL CHECK(transport IN ('stdio', 'http', 'sse', 'database')),
  template_id TEXT NOT NULL DEFAULT 'custom',
  display_name TEXT NOT NULL,
  endpoint TEXT NOT NULL DEFAULT '',
  command TEXT NOT NULL DEFAULT '',
  args_json TEXT NOT NULL DEFAULT '[]',
  environment_keys_json TEXT NOT NULL DEFAULT '[]',
  working_directory_policy TEXT NOT NULL DEFAULT 'workspace',
  health_path TEXT NOT NULL DEFAULT '',
  read_only INTEGER NOT NULL CHECK(read_only IN (0, 1)),
  enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
  credential_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
```

Legacy rows are mapped as `source = legacy`, MCP transport `http` when an endpoint exists, database transport `database`, and no automatic INSERT into v2.

- [ ] **Step 4: Implement bounded layered probes**

Configuration validates type-specific fields. Network uses the existing six-second timeout. Legacy and database probes leave protocol and authentication `not-tested`. MCP HTTP/SSE and stdio return a truthful protocol state only after a real initialize and tool-discovery probe is available; unsupported transports are `not-tested`, never passed. Permission reports `approval-required` for local stdio execution and mutating scopes.

- [ ] **Step 5: Update Harness profile resolution without deleting legacy compatibility**

Lookup v2 first for an explicitly selected profile ID, then fall back to `harness_connection_profiles`. Continue injecting only safe endpoint and credential references; never inject stored raw secret values.

- [ ] **Step 6: Run Rust verification and inspect schema diff**

Run: `cargo test --manifest-path src-tauri/Cargo.toml commands::tests`

Run: `cargo fmt --manifest-path src-tauri/Cargo.toml -- --check`

Expected: command tests pass, formatting check passes, and no DELETE/UPDATE migration targets the legacy table. No commit is created.

---

### Task 4: Official-Style Skills And Unified MCP/Connection UI

**Files:**
- Create: `packages/dsh-plugin-desktop/src/client/model-agent/SkillInventoryView.tsx`
- Create: `packages/dsh-plugin-desktop/src/client/model-agent/UnifiedConnectionRegistry.tsx`
- Create: `packages/dsh-plugin-desktop/src/client/model-agent/ConnectionEditorDialog.tsx`
- Create: `packages/dsh-plugin-desktop/src/client/model-agent/ConnectionTestPresenter.tsx`
- Modify: `packages/dsh-plugin-desktop/src/client/model-agent/ModelAgentCenter.tsx`
- Retain for compatibility but stop rendering: `packages/dsh-plugin-desktop/src/client/model-agent/ConnectionProfilesPanel.tsx`
- Modify: `packages/dsh-plugin-desktop/tests/model-agent-center.spec.tsx`
- Create: `packages/dsh-plugin-desktop/tests/connection-editor-dialog.spec.tsx`
- Create: `packages/dsh-plugin-desktop/tests/unified-connection-registry.spec.tsx`

**Interfaces:**
- `SkillInventoryView` receives official extension inventory and renders only reported facts, using `未提供` for missing version/integrity/compatibility.
- `UnifiedConnectionRegistry` merges source-managed MCP extensions and connection profiles without allowing source-managed endpoint edits.
- `ConnectionEditorDialog` emits a safe `ConnectionSaveInput` and never calls the bridge directly.

- [ ] **Step 1: Write failing information-architecture and editor-flow tests**

```tsx
expect(screen.getByRole('tab', { name: 'MCP 与连接' })).toBeVisible()
expect(screen.queryByRole('tab', { name: '连接器' })).toBeNull()

fireEvent.click(screen.getByRole('button', { name: '新增连接' }))
fireEvent.change(screen.getByLabelText('连接类型'), { target: { value: 'http-api' } })
fireEvent.change(screen.getByLabelText('连接名称'), { target: { value: '内部知识库' } })
fireEvent.change(screen.getByLabelText('服务地址'), { target: { value: 'https://knowledge.example.com' } })
fireEvent.click(screen.getByRole('button', { name: '保存连接' }))
expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ kind: 'http-api', templateId: 'custom' }))
```

Cover custom MCP stdio/HTTP/SSE, HTTP API, database, Yunxiao/GitLab templates, edit, enable/disable, exact-name delete confirmation, preview write blocking, and a five-layer test result.

- [ ] **Step 2: Run the component tests and verify the old five-tab UI fails**

Run: `npx vitest run packages/dsh-plugin-desktop/tests/model-agent-center.spec.tsx packages/dsh-plugin-desktop/tests/connection-editor-dialog.spec.tsx packages/dsh-plugin-desktop/tests/unified-connection-registry.spec.tsx`

Expected: missing `MCP 与连接`, missing editor, and old `连接器` assertions fail.

- [ ] **Step 3: Implement compact official rows and dialogs**

Use semantic HTML list rows, official underline tabs, labelled inputs/selects/toggles, `role="status"` for tests, and `role="alertdialog"` for exact-profile deletion. The editor presents type first and switches only the type-specific safe fields.

- [ ] **Step 4: Wire reversible actions through the bridge**

Save uses `harness.connection.save`. Enable/disable saves the same exact profile with only `enabled` changed. Delete calls `harness.connection.delete` only after named confirmation. Test calls `harness.connection.test` and renders each returned layer independently.

- [ ] **Step 5: Verify component tests pass and no legacy panel is rendered**

Run: `npx vitest run packages/dsh-plugin-desktop/tests/model-agent-center.spec.tsx packages/dsh-plugin-desktop/tests/connection-editor-dialog.spec.tsx packages/dsh-plugin-desktop/tests/unified-connection-registry.spec.tsx`

Expected: all selected tests pass, plugin MCP and custom/legacy rows are visible together, and source-managed endpoints have no edit control. No commit is created.

---

### Task 5: Real Diagnostics

**Files:**
- Create: `packages/dsh-plugin-desktop/src/client/model-agent/capability-diagnostics.ts`
- Modify: `packages/dsh-plugin-desktop/src/client/model-agent/DiagnosticsPanel.tsx`
- Modify: `packages/dsh-plugin-desktop/src/client/model-agent/ModelAgentCenter.tsx`
- Create: `packages/dsh-plugin-desktop/tests/capability-diagnostics.spec.ts`
- Modify: `packages/dsh-plugin-desktop/tests/model-agent-center.spec.tsx`

**Interfaces:**
- `buildCapabilityDiagnostics` consumes loaded Provider metadata, capability inventory, extension inventory, CLI status, and connection/test data.
- Produces Runtime, executor, extension, and connection groups with safe label, failed layer, explanation, and next action.

- [ ] **Step 1: Write failing tests for non-empty Provider and partial-failure diagnostics**

```ts
expect(buildCapabilityDiagnostics(fixture).groups.executor.items[0]).toMatchObject({
  label: 'Codex', state: 'needs-attention', nextAction: '配置凭证',
})
expect(buildCapabilityDiagnostics(partialFixture).summary).not.toContainEqual(expect.objectContaining({ label: '可用', count: expect.any(Number) }))
```

- [ ] **Step 2: Run tests and verify hard-coded empty diagnostics fail**

Run: `npx vitest run packages/dsh-plugin-desktop/tests/capability-diagnostics.spec.ts packages/dsh-plugin-desktop/tests/model-agent-center.spec.tsx`

Expected: the missing diagnostics builder and empty Provider input fail.

- [ ] **Step 3: Load Provider metadata and build truthful grouped diagnostics**

`ModelAgentCenter.load()` requests `provider.metadata.list`, `capability.inventory`, `extension.inventory`, all connection profiles, and the Codex login/install state. A failure in one request is captured for that group without discarding successful inventory from the others.

- [ ] **Step 4: Render counts only from loaded groups**

Show compact official rows for Runtime/dependency closure, executor, Skills/MCP, and connections. Each failed row contains one next action; do not claim availability from file presence or from network-only success.

- [ ] **Step 5: Verify diagnostics tests pass**

Run: `npx vitest run packages/dsh-plugin-desktop/tests/capability-diagnostics.spec.ts packages/dsh-plugin-desktop/tests/model-agent-center.spec.tsx`

Expected: Provider metadata is requested, partial failures remain visible, and diagnostics has no hard-coded empty provider list. No commit is created.

---

### Task 6: Official Visual Contract

**Files:**
- Modify: `packages/dsh-plugin-desktop/src/client/styles.ts`
- Modify: `packages/dsh-plugin-desktop/tests/official-style-contract.spec.ts`
- Modify: `packages/dsh-plugin-desktop/tests/plugin-market.spec.tsx`
- Modify: `packages/dsh-plugin-desktop/tests/model-agent-center.spec.tsx`

**Interfaces:**
- Keeps all plugin and capability content inside the official 760px settings region.
- Provides reusable official row, disclosure, toolbar, field, dialog, toggle, and status classes backed by `--dsw-*` tokens.

- [ ] **Step 1: Extend the failing style contract**

```ts
expect(capabilityCss).toContain('max-width: 760px')
expect(capabilityCss).toContain('var(--dsw-alias-bg-layer-3')
expect(capabilityCss).toContain('var(--dsw-alias-label-primary')
expect(capabilityCss).not.toMatch(/#[0-9a-f]{3,8}/i)
expect(capabilityCss).not.toContain('linear-gradient')
expect(capabilityCss).not.toContain('backdrop-filter')
expect(capabilityCss).not.toContain('box-shadow: 0 28px')
```

- [ ] **Step 2: Run the style test and verify existing custom colors/elevation fail**

Run: `npx vitest run packages/dsh-plugin-desktop/tests/official-style-contract.spec.ts`

Expected: the capability-center scope fails because it still contains literal colors and custom modal elevation.

- [ ] **Step 3: Replace cards with official compact rows and token-only controls**

Match the official source title baseline, 36px underline tabs, compact list rows, 34-36px controls, 8-10px radii, tokenized borders/surfaces, and inherited typography. Remove the uppercase review eyebrow from capability dialogs and use the same official modal hierarchy as the source.

- [ ] **Step 4: Add narrow-width behavior**

At supported narrow widths, toolbar actions wrap, list metadata stays horizontal or truncates, editor fields become one column, and action labels never turn into vertical text.

- [ ] **Step 5: Verify style and component tests pass**

Run: `npx vitest run packages/dsh-plugin-desktop/tests/official-style-contract.spec.ts packages/dsh-plugin-desktop/tests/plugin-market.spec.tsx packages/dsh-plugin-desktop/tests/model-agent-center.spec.tsx`

Expected: selected tests pass with token-only capability styling. No commit is created.

---

### Task 7: Integrated Verification And Design QA

**Files:**
- Modify: `design-qa.md`
- Modify only if a verified mismatch requires it: files listed in Tasks 1-6.

**Interfaces:**
- Source visual: `/Users/lym/.codex/visualizations/2026/08/30/01a05094-96cd-7851-9148-c4d923b7bd86/official-04-plugin-config.png`.
- Implementation: local read-only advanced workbench preview at the current Runtime URL.

- [ ] **Step 1: Run focused and package verification**

Run: `npm run plugin:test`

Run: `npm run typecheck -w @dsh/desktop-plugin`

Run: `npm run plugin:build`

Run: `npm run test`

Run: `npm run build:web`

Run: `cargo test --manifest-path src-tauri/Cargo.toml commands::tests`

Expected: every command exits 0 with no selected-test failures.

- [ ] **Step 2: Review all related diffs and data-safety invariants**

Run: `git diff --check`

Run: `git diff -- packages/dsh-plugin-desktop/src/client packages/dsh-plugin-desktop/tests src/bridge-contract.ts src/workbench-bridge.ts src-tauri/src/commands.rs src-tauri/src/harness/mod.rs design-qa.md`

Confirm there is no legacy-table deletion, automatic rewrite, raw secret persistence, plugin install in preview, unrelated formatting, commit, push, deployment, or external write.

- [ ] **Step 3: Verify the local preview in the Codex in-app browser**

Open the existing local advanced-mode URL in the in-app browser, verify the plugin total is 1,946, search and pagination work, then verify `执行器 / 技能 / MCP 与连接 / 诊断`, custom add/edit/enable/disable/delete/test interactions, keyboard focus, loading, empty, failure, and preview-read-only states. Check browser console errors.

- [ ] **Step 4: Capture equal-state visual evidence and compare in one input**

Capture the official source and implementation at the same 1280x720 viewport and dark theme. Place both images in one comparison image before judging dialog crop, navigation width, title baseline, tab geometry, row height, spacing, borders, radii, typography, controls, and states.

- [ ] **Step 5: Fix every P0/P1/P2 and repeat comparison**

Each visual fix requires rerunning its targeted component/style test and a new equal-state screenshot. Stop only when no actionable P0/P1/P2 remains.

- [ ] **Step 6: Write the final QA record**

`design-qa.md` records source path, implementation screenshot path, viewport, pixel dimensions, CSS size, density, state, full-view and focused comparison evidence, interaction checks, console check, comparison history, residual P3 notes, and exactly `final result: passed` or `final result: blocked`.

- [ ] **Step 7: Leave the verified preview running and hand off without Git delivery**

Report actual completed, partial, or blocked status with test counts, build results, visual QA result, preserved-data boundary, and one concrete user review action. Do not commit or push.
