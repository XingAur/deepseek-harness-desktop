# Automated Upstream Release and Extension Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a daily, recoverable DeepSeek Harness upstream release pipeline, preserve signed Windows in-app updates, add safe unsigned macOS DMG notifications, and document the future provider/plugin/Skills/MCP extension foundation.

**Architecture:** A tracked `release/versions.json` becomes the source of truth for desktop, Runtime, DSH, Node, and pnpm versions. Deterministic Node scripts own version preparation, release-state classification, and release-manifest generation; GitHub workflows only orchestrate those tested primitives. The Rust update controller keeps Tauri's signed updater on Windows and consumes a separately validated GitHub `desktop-release.json` on Apple Silicon macOS, while React renders platform-specific actions without blocking Runtime startup.

**Tech Stack:** TypeScript 6, React 18, Vitest 4, Node.js ESM scripts, Rust 2024, Tauri 2, reqwest, semver, GitHub Actions, GitHub CLI.

## Global Constraints

- macOS Apple Silicon ships a DMG without Apple Developer ID signing or notarization; it must never claim silent in-app installation.
- Windows x64 keeps Tauri updater signature verification and passive NSIS update mode.
- `@deepseek-ai/dsh` tracks npm dist-tag `latest`, but Runtime builds always use a pinned exact version.
- Daily schedule is `02:30 UTC` / `10:30 China Standard Time` with a manual dispatch fallback.
- Existing Release and Runtime assets are immutable; matching hashes may be reused, differing hashes must stop the workflow.
- No update path may delete or migrate Profile, project, workspace, session, cache, Runtime fallback, or Application Support data.
- Production update and download URLs are restricted to `https://github.com/XingAur/deepseek-harness-desktop/` and GitHub-controlled redirect hosts.
- The current repository branch is explicitly authorized by the user for implementation and one final local commit; no push, PR, real tag, Secret mutation, or Release publication is authorized in this local run.
- New behavior follows red-green-refactor; configuration-only edits are guarded by static contract tests written first.
- `/Users/lym/WorkCode/ai/Harness` is used only for a final generic diff review with temporary outputs; its default database and task history are not modified.

---

### Task 1: Establish the tracked release version source

**Files:**
- Create: `release/versions.json`
- Create: `scripts/release-versions.mjs`
- Create: `scripts/release-versions.d.mts`
- Create: `scripts/release-versions.test.ts`
- Modify: `.gitignore`
- Modify: `package.json`
- Modify: `scripts/build-runtime.mjs`
- Modify: `scripts/windows-installer.mjs`
- Modify: `scripts/windows-installer.d.mts`
- Modify: `scripts/product-copy.test.ts`

**Interfaces:**
- Produces: `loadReleaseVersions(root?: string): ReleaseVersions`
- Produces: `validateReleaseVersions(value: unknown): ReleaseVersions`
- Produces: `assertReleaseVersionConsistency(root?: string): ReleaseVersions`
- Produces: CLI `node scripts/release-versions.mjs --check`.
- Consumed by: Runtime builder, Windows installer builder, upstream sync script, GitHub workflows, and release metadata generator.

- [ ] **Step 1: Write failing release-version tests**

Add tests that expect a strict schema and actual repository consistency:

```ts
import { describe, expect, it } from 'vitest'
import { assertReleaseVersionConsistency, validateReleaseVersions } from './release-versions.mjs'

describe('release versions', () => {
  it('keeps every derived desktop version aligned with the tracked source', () => {
    expect(assertReleaseVersionConsistency()).toMatchObject({
      desktopVersion: '0.1.12',
      runtimeVersion: '0.1.9-preview',
      dshVersion: '0.1.0-rc.8',
    })
  })

  it('rejects ranges, mutable tags, malformed runtime versions, and unknown keys', () => {
    expect(() => validateReleaseVersions({
      schemaVersion: 1,
      desktopVersion: '^0.1.12',
      runtimeVersion: 'latest',
      dshVersion: 'latest',
      nodeVersion: '24',
      pnpmVersion: '11',
      legacyReleaseBaseline: '0.1.12',
      extra: true,
    })).toThrow(/release versions/i)
  })
})
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `npm test -- scripts/release-versions.test.ts`

Expected: FAIL because `scripts/release-versions.mjs` does not exist.

- [ ] **Step 3: Implement the version source and consistency API**

Track this exact initial source, including the one-time legacy public-release baseline needed to migrate from v0.1.12, whose Release predates `desktop-release.json`:

```json
{
  "schemaVersion": 1,
  "desktopVersion": "0.1.12",
  "runtimeVersion": "0.1.9-preview",
  "dshVersion": "0.1.0-rc.8",
  "nodeVersion": "24.14.0",
  "pnpmVersion": "11.7.0",
  "legacyReleaseBaseline": "0.1.12"
}
```

`release-versions.mjs` must strictly validate keys and formats, then compare `desktopVersion` with root package/package-lock/Tauri/Cargo files and compare Runtime/DSH/Node/pnpm values with their consumers. The CLI must print validated JSON on success and return non-zero with a concrete mismatch on failure.

Change `.gitignore` from ignoring the whole `release/` directory to:

```gitignore
release/*
!release/versions.json
```

Add this package script:

```json
"release:versions:check": "node scripts/release-versions.mjs --check"
```

Replace hard-coded constants in Runtime and installer scripts with values from `loadReleaseVersions()`:

```js
const versions = loadReleaseVersions()
const NODE_VERSION = versions.nodeVersion
const DSH_VERSION = versions.dshVersion
const PNPM_VERSION = versions.pnpmVersion
export const MANAGED_RUNTIME_VERSION = versions.runtimeVersion
```

- [ ] **Step 4: Replace brittle product-copy version assertions**

Make `scripts/product-copy.test.ts` load the source and assert consumers are derived from it. Keep scenario fixtures such as `0.1.0-rc.8` when they test peer expansion rather than current release identity.

- [ ] **Step 5: Run focused and affected tests and verify GREEN**

Run:

```bash
npm test -- scripts/release-versions.test.ts scripts/windows-installer.test.ts scripts/product-copy.test.ts
npm run release:versions:check
```

Expected: all selected tests pass and the check prints the v0.1.12 source.

---

### Task 2: Implement idempotent upstream preparation and recovery classification

**Files:**
- Create: `scripts/prepare-upstream-release.mjs`
- Create: `scripts/prepare-upstream-release.d.mts`
- Create: `scripts/prepare-upstream-release.test.ts`
- Create: `scripts/release-state.mjs`
- Create: `scripts/release-state.d.mts`
- Create: `scripts/release-state.test.ts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `loadReleaseVersions()` and strict release schema from Task 1.
- Produces: `parseSemVer(value)`, `compareSemVer(left, right)`, `fetchLatestDshVersion(fetcher?)`, and `prepareUpstreamRelease({ root, latestVersion })`.
- Produces: `classifyReleaseState(input): { status: 'complete' | 'pending-tag' | 'pending-release' | 'blocked'; reason: string }`.
- Produces: CLI JSON suitable for GitHub Actions outputs.

- [ ] **Step 1: Write failing SemVer and preparation tests**

Use a temporary fixture containing every derived file. Cover:

```ts
it('bumps desktop and runtime patch versions for a newer DSH version', async () => {
  const root = await releaseFixture()
  const result = await prepareUpstreamRelease({ root, latestVersion: '0.1.1-rc.2' })
  expect(result).toEqual(expect.objectContaining({
    action: 'upgrade',
    previousDshVersion: '0.1.0-rc.8',
    dshVersion: '0.1.1-rc.2',
    desktopVersion: '0.1.13',
    runtimeVersion: '0.1.10-preview',
    tag: 'desktop-v0.1.13',
  }))
  expect(assertReleaseVersionConsistency(root).dshVersion).toBe('0.1.1-rc.2')
})

it('is a byte-preserving no-op when the version is unchanged', async () => {
  const root = await releaseFixture()
  const before = await snapshotFixture(root)
  expect((await prepareUpstreamRelease({ root, latestVersion: '0.1.0-rc.8' })).action).toBe('noop')
  expect(await snapshotFixture(root)).toEqual(before)
})

it.each(['0.1.0-rc.7', 'not-a-version', '^0.2.0', 'latest'])('rejects unsafe upstream %s', async (version) => {
  const root = await releaseFixture()
  await expect(prepareUpstreamRelease({ root, latestVersion: version })).rejects.toThrow()
})
```

Also test stable/prerelease ordering and that the npm fetcher rejects redirects, non-2xx responses, malformed JSON, and an unexpected version.

- [ ] **Step 2: Run preparation tests and verify RED**

Run: `npm test -- scripts/prepare-upstream-release.test.ts`

Expected: FAIL because the preparation module does not exist.

- [ ] **Step 3: Implement strict SemVer comparison and transactional file preparation**

Implement SemVer precedence for numeric core and prerelease identifiers. Build every target file in memory first, write same-directory temporary files, rename them, and restore original content if any write fails. Only these fields may change:

- `release/versions.json`: DSH, desktop, Runtime.
- `package.json`: root version.
- `package-lock.json`: top-level version and `packages[""]` version.
- `src-tauri/tauri.conf.json`: version.
- `src-tauri/Cargo.toml`: root package version.
- `src-tauri/Cargo.lock`: `deepseek-harness-desktop` package version.

The production fetcher must request only:

```text
https://registry.npmjs.org/@deepseek-ai%2Fdsh/latest
```

with a bounded timeout and `redirect: "error"`. The CLI accepts `--latest=<exact-version>` only as an explicit deterministic override; otherwise it uses the registry endpoint.

Add:

```json
"release:prepare": "node scripts/prepare-upstream-release.mjs"
```

- [ ] **Step 4: Write failing release-state tests**

Cover the bootstrap baseline and every recovery branch:

```ts
expect(classifyReleaseState({ version: '0.1.12', legacyReleaseBaseline: '0.1.12', tagExists: true, release: { isDraft: false, assets: [] } }).status).toBe('complete')
expect(classifyReleaseState({ version: '0.1.13', legacyReleaseBaseline: '0.1.12', tagExists: false, release: null }).status).toBe('pending-tag')
expect(classifyReleaseState({ version: '0.1.13', legacyReleaseBaseline: '0.1.12', tagExists: true, release: null }).status).toBe('pending-release')
expect(classifyReleaseState({ version: '0.1.13', legacyReleaseBaseline: '0.1.12', tagExists: true, release: { isDraft: true, assets: [] } }).status).toBe('pending-release')
expect(classifyReleaseState({ version: '0.1.13', legacyReleaseBaseline: '0.1.12', tagExists: true, release: { isDraft: false, assets: [{ name: 'desktop-release.json' }] } }).status).toBe('complete')
expect(classifyReleaseState({ version: '0.1.13', legacyReleaseBaseline: '0.1.12', tagExists: true, release: { isDraft: false, assets: [] } }).status).toBe('blocked')
```

- [ ] **Step 5: Implement and verify release-state classification**

The CLI reads `--version`, `--legacy-release-baseline`, `--tag-exists`, and `--release-json-file`, then prints the result as JSON. A public post-baseline Release is complete only when it contains `desktop-release.json`; a public incomplete Release is blocked because automatic repair must not overwrite public assets.

Run:

```bash
npm test -- scripts/prepare-upstream-release.test.ts scripts/release-state.test.ts
```

Expected: all tests pass.

---

### Task 3: Generate and verify immutable cross-platform release metadata

**Files:**
- Create: `scripts/desktop-release.mjs`
- Create: `scripts/desktop-release.d.mts`
- Create: `scripts/desktop-release.test.ts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `ReleaseVersions` from Task 1 and a local asset directory.
- Produces: `generateDesktopRelease({ assetDirectory, outputDirectory, repository, publishedAt, notes, versions })`.
- Produces: `latest.json` for Tauri Windows updater and `desktop-release.json` for cross-platform UI.
- Produces: `verifyDesktopReleaseAssets(...)` for final release gating.

- [ ] **Step 1: Write failing metadata tests**

Create temporary deterministic files named like real Tauri artifacts:

```ts
await writeFile(join(assets, 'DeepSeek.Harness.Desktop_0.1.13_x64-setup.exe'), 'installer')
await writeFile(join(assets, 'DeepSeek.Harness.Desktop_0.1.13_x64-setup.nsis.zip'), 'updater')
await writeFile(join(assets, 'DeepSeek.Harness.Desktop_0.1.13_x64-setup.nsis.zip.sig'), 'SIGNATURE\n')
await writeFile(join(assets, 'DeepSeek.Harness.Desktop_0.1.13_aarch64.dmg'), 'dmg')
```

Assert:

- `latest.json.platforms.windows-x86_64` points to the updater ZIP and embeds trimmed signature text.
- `desktop-release.json` has `in-app` for Windows and `manual-dmg` for macOS.
- Every URL belongs to the fixed repository/tag.
- SHA-256 and byte size match fixture bytes.
- macOS has `developerIdSigned: false` and `notarized: false`.
- Missing, duplicate, wrong-version, traversal-like, or unexpected-platform assets fail.

- [ ] **Step 2: Run metadata tests and verify RED**

Run: `npm test -- scripts/desktop-release.test.ts`

Expected: FAIL because the generator does not exist.

- [ ] **Step 3: Implement deterministic metadata generation**

The generator must select exactly one `.exe`, `.nsis.zip`, matching `.sig`, and `.dmg`; compute hashes from bytes; URL-encode filenames; write both JSON files with trailing newlines; and return all uploadable asset paths. It must not use network access.

The generated macOS platform entry must be:

```json
{
  "mode": "manual-dmg",
  "url": "https://github.com/XingAur/deepseek-harness-desktop/releases/download/desktop-v0.1.13/FILE.dmg",
  "sha256": "64-lowercase-hex",
  "size": 3,
  "developerIdSigned": false,
  "notarized": false
}
```

Add:

```json
"release:metadata": "node scripts/desktop-release.mjs"
```

- [ ] **Step 4: Run metadata tests and verify GREEN**

Run: `npm test -- scripts/desktop-release.test.ts`

Expected: all tests pass with no network calls.

---

### Task 4: Add daily sync and atomic cross-platform publication workflows

**Files:**
- Create: `.github/workflows/upstream-sync.yml`
- Modify: `.github/workflows/desktop.yml`
- Create: `scripts/workflow-contract.test.ts`
- Modify: `scripts/product-copy.test.ts`
- Modify: `scripts/write-updater-config.mjs`
- Modify: `scripts/write-updater-config.d.mts`
- Modify: `scripts/write-updater-config.test.ts`

**Interfaces:**
- Consumes: Task 1-3 CLIs.
- Produces: daily/manual upstream sync workflow and tag-ref desktop workflow.
- Produces: Windows release config with updater artifacts and macOS release config with bundled Runtime but no app-updater artifact.

- [ ] **Step 1: Write failing workflow and platform-config contract tests**

Assert the daily workflow contains:

```ts
expect(sync).toContain("cron: '30 2 * * *'")
expect(sync).toContain('contents: write')
expect(sync).toContain('actions: write')
expect(sync).toContain('cancel-in-progress: false')
expect(sync).toContain('node scripts/release-state.mjs')
expect(sync).toContain('node scripts/prepare-upstream-release.mjs')
expect(sync).toContain('git push --atomic')
expect(sync).toContain('gh workflow run desktop.yml')
expect(sync).not.toContain('--force')
```

Assert the desktop workflow:

- validates exact `desktop-v${desktopVersion}` tag refs;
- reads versions from `release/versions.json`;
- uses Windows x64 and macOS Apple Silicon matrix entries;
- sets `cancel-in-progress: false`;
- does not let Tauri Action publish directly;
- uploads platform artifacts to Actions storage;
- creates/uses a draft only in the final job;
- runs `desktop-release.mjs` after both builds;
- compares existing asset bytes and never uses `--clobber`;
- publishes only after `latest.json` and `desktop-release.json` exist;
- says the macOS DMG is not Developer ID signed or notarized.

Test updater config behavior:

```ts
expect(updaterConfig({ platform: 'windows-x86_64', publicKey: 'PUBLIC' }).bundle.createUpdaterArtifacts).toBe(true)
expect(updaterConfig({ platform: 'darwin-aarch64' }).bundle.createUpdaterArtifacts).toBe(false)
expect(() => updaterConfig({ platform: 'windows-x86_64' })).toThrow(/public key/i)
```

- [ ] **Step 2: Run contracts and verify RED**

Run:

```bash
npm test -- scripts/workflow-contract.test.ts scripts/write-updater-config.test.ts scripts/product-copy.test.ts
```

Expected: failures identify the missing schedule, hard-coded versions, direct Tauri release upload, and platform config API.

- [ ] **Step 3: Implement platform-specific generated Tauri config**

Change the API to:

```ts
updaterConfig(options: {
  platform: 'windows-x86_64' | 'darwin-aarch64'
  publicKey?: string
  endpoint?: string
}): UpdaterReleaseConfig
```

Both platforms bundle `../runtime/` as `runtime/`. Only Windows sets `createUpdaterArtifacts: true` and overrides updater key/endpoint/passive mode. macOS sets `createUpdaterArtifacts: false` and needs no updater private key.

- [ ] **Step 4: Implement `.github/workflows/upstream-sync.yml`**

The job must:

1. run only once via a non-cancelling concurrency group;
2. check out full history and install exact dependencies;
3. classify current tag/Release using `release-state.mjs`;
4. stop on `blocked`;
5. recover `pending-tag` or `pending-release` without another version bump;
6. prepare a new release only when current is complete;
7. run `npm run check`, version check, and locked Rust tests before mutation is pushed;
8. commit only version-derived files with a bot identity;
9. push main plus tag with one `git push --atomic` and no force;
10. avoid duplicate dispatch when the exact tag already has a queued/in-progress desktop run;
11. explicitly dispatch `desktop.yml` at the exact tag ref.

- [ ] **Step 5: Refactor `.github/workflows/desktop.yml` into build then publish**

The build matrix must not pass `tagName` or release fields to `tauri-apps/tauri-action`. Each platform uploads its desktop artifacts and Runtime assets via `actions/upload-artifact`.

The final Ubuntu job must:

1. download all matrix artifacts;
2. upload immutable Runtime assets to `runtime-v${runtimeVersion}` after byte comparison;
3. generate `latest.json` and `desktop-release.json` locally;
4. create or reuse a draft desktop Release;
5. upload every desktop asset with byte comparison and no clobber;
6. verify the complete expected asset set;
7. publish the draft as latest only after all checks succeed.

- [ ] **Step 6: Run workflow contracts and full script tests**

Run:

```bash
npm test -- scripts/workflow-contract.test.ts scripts/write-updater-config.test.ts scripts/product-copy.test.ts
npm run release:versions:check
```

Expected: all pass; YAML contains no force push or clobber upload.

---

### Task 5: Add safe macOS manual-update manifest handling in Rust

**Files:**
- Create: `src-tauri/src/app_update/manual.rs`
- Modify: `src-tauri/src/app_update/mod.rs`
- Modify: `src-tauri/src/app_update/model.rs`
- Modify: `src-tauri/src/app_update/controller.rs`
- Modify: `src-tauri/src/commands.rs`
- Modify: `src-tauri/src/lib.rs`

**Interfaces:**
- Produces: `AppUpdateMode::{InApp, ManualDmg}` serialized as `in-app` / `manual-dmg`.
- Extends: `UpdateInfo` with `mode`, `download_url`, `developer_id_signed`, and `notarized`.
- Produces: pure `manual_update_from_json(json, current_version, architecture)` parser/validator.
- Produces: `AppUpdateController::open_manual_download()` and Tauri command `open_app_update_download`.

- [ ] **Step 1: Write failing Rust manifest and transition tests**

Tests must verify:

```rust
let update = manual_update_from_json(valid_manifest(), "0.1.12", "aarch64")
    .unwrap()
    .unwrap();
assert_eq!(update.mode, AppUpdateMode::ManualDmg);
assert!(update.download_url.as_deref().unwrap().ends_with(".dmg"));
```

Also cover equal/older version no-op, non-arm64 no-op, malformed SemVer, wrong tag, HTTP URL, credentials, wrong owner/repo, query/fragment, wrong mode, invalid SHA-256, and a mismatched DMG filename/version. Add a state test proving manual updates cannot transition to `Downloading` or `Installing`.

- [ ] **Step 2: Run Rust update tests and verify RED**

Run:

```bash
cargo test --manifest-path src-tauri/Cargo.toml app_update --locked
```

Expected: compilation/test failure because manual update types and parser do not exist.

- [ ] **Step 3: Implement strict manifest parsing and network policy**

Production endpoint:

```text
https://github.com/XingAur/deepseek-harness-desktop/releases/latest/download/desktop-release.json
```

Use a reqwest client with a bounded timeout. Redirects may only remain HTTPS and target `github.com`, `objects.githubusercontent.com`, or `release-assets.githubusercontent.com` without credentials. The manifest's release page and DMG URL must use exact repository/tag path prefixes, no query/fragment, and a `.dmg` filename containing the declared version.

Under `feature = "e2e"` only, permit an explicit loopback fixture endpoint; production must never read an environment override.

- [ ] **Step 4: Split the controller by platform and expose trusted open action**

- Windows/non-macOS `check()` keeps Tauri updater and stores a signed pending update.
- Apple Silicon macOS `check()` fetches the manual manifest and stores only validated metadata.
- `download()`, `install_now()`, and `install_on_exit()` reject manual updates.
- `open_manual_download()` receives no URL from React; it opens only the validated URL held in controller state.
- `defer()` clears either pending mode without touching files.

- [ ] **Step 5: Run focused then all Rust tests**

Run:

```bash
cargo test --manifest-path src-tauri/Cargo.toml app_update --locked
cargo test --manifest-path src-tauri/Cargo.toml --locked
```

Expected: all tests pass.

---

### Task 6: Add shell-start update checks and platform-specific update UI

**Files:**
- Modify: `src/runtime-contract.ts`
- Modify: `src/runtime-client.ts`
- Modify: `src/App.tsx`
- Modify: `src/App.test.tsx`
- Modify: `src/app.css`
- Create: `product-review/update.html`
- Create: `product-review/update.tsx`

**Interfaces:**
- Extends: `AppUpdateInfo.mode?: 'in-app' | 'manual-dmg'` plus manual-download fields.
- Extends: `RuntimeClient.openAppUpdateDownload(): Promise<void>`.
- Exports: `AppUpdateBanner` for the deterministic review surface.

- [ ] **Step 1: Write failing React behavior tests**

Add tests proving automatic update check happens after shell mount even if Runtime never reaches ready, while automatic failures remain silent:

```ts
render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
await waitFor(() => expect(runtime.checkAppUpdate).toHaveBeenCalledWith('automatic'))
expect(runtime.bootstrapRuntime).toHaveBeenCalled()
```

Add a manual macOS update test:

```ts
vi.mocked(runtime.checkAppUpdate).mockResolvedValue({
  phase: 'available',
  update: {
    version: '0.1.13',
    notes: '同步新版 DeepSeek Harness',
    size: 2048,
    mode: 'manual-dmg',
    downloadUrl: 'https://github.com/XingAur/deepseek-harness-desktop/releases/download/desktop-v0.1.13/file.dmg',
    developerIdSigned: false,
    notarized: false,
  },
})
expect(await screen.findByRole('button', { name: '下载 DMG' })).toBeVisible()
expect(screen.getByText(/未使用 Apple Developer ID 签名/)).toBeVisible()
expect(screen.queryByRole('button', { name: '后台下载' })).not.toBeInTheDocument()
fireEvent.click(screen.getByRole('button', { name: '下载 DMG' }))
await waitFor(() => expect(runtime.openAppUpdateDownload).toHaveBeenCalled())
```

Also test “稍后提醒”, open failure, Windows action preservation, long notes, and no repeated automatic check under React Strict Mode.

- [ ] **Step 2: Run App tests and verify RED**

Run: `npm test -- src/App.test.tsx`

Expected: failures for missing runtime method, shell-start timing, and macOS controls.

- [ ] **Step 3: Implement contracts, client, startup timing, and UI**

- Invoke update check and take receipt once from a mount effect independent of Runtime generation.
- Keep automatic failures silent and manual failures actionable.
- Render Windows controls exactly as today for `in-app` or missing mode.
- Render macOS manual warning, “下载 DMG”, and “稍后提醒”; never call download/install APIs for this mode.
- On open failure, show the existing non-blocking update failure banner.
- Allow warning and notes to wrap; preserve responsive/minimum-window behavior.

- [ ] **Step 4: Create deterministic product-review surface**

`product-review/update.tsx` imports the production `AppUpdateBanner`, reads only a fixed local `scenario` query (`mac`, `windows-ready`, `failed`), and renders the real component against inert callbacks. It contains no secrets, network calls, or production routing.

- [ ] **Step 5: Run React tests and Web build**

Run:

```bash
npm test -- src/App.test.tsx
npm run build:web
```

Expected: all App tests pass and TypeScript/Vite build succeeds.

---

### Task 7: Update user documentation and extension architecture

**Files:**
- Create: `docs/architecture/extension-platform.md`
- Modify: `README.md`
- Modify: `runtime/README.md`
- Modify: `docs/superpowers/specs/2026-08-23-automated-upstream-release-and-extension-foundation-design.md`
- Modify: `scripts/product-copy.test.ts`

**Interfaces:**
- Documents: end-user update behavior, macOS Gatekeeper limitation, daily automation, recovery, and data preservation.
- Documents: Desktop Shell, Managed DSH Runtime, Provider Adapter, Extension Plane, and Governance Plane boundaries.

- [ ] **Step 1: Write failing documentation contracts**

Require README to contain:

- the current public GitHub Releases URL;
- Windows in-app update wording;
- macOS Apple Silicon DMG manual replacement wording;
- “未使用 Apple Developer ID 签名、未经过 Apple 公证”;
- data preservation warning;
- daily GitHub Actions sync explanation;
- no stale “尚未发布首个公开版本” copy.

Require the architecture document to name Codex, Claude, API Provider, CLI Worker, Plugins, Skills, MCP, Profile isolation, permissions, audit, rollback, and credential storage boundaries.

- [ ] **Step 2: Run documentation contracts and verify RED**

Run: `npm test -- scripts/product-copy.test.ts`

Expected: failures identify stale release/support copy and missing architecture document.

- [ ] **Step 3: Update docs and record the legacy bootstrap nuance**

Explain that v0.1.12 is the accepted pre-manifest baseline; every automatically produced later release requires `desktop-release.json`. Keep code-level capability separate from the still-unverified GitHub-hosted build and installed-upgrade evidence.

- [ ] **Step 4: Run documentation contracts and verify GREEN**

Run: `npm test -- scripts/product-copy.test.ts`

Expected: all product-copy and documentation checks pass.

---

### Task 8: Product audit, full verification, Harness review, and local commit

**Files:**
- Modify only files required to close findings from the audits.
- Create temporary evidence only below a fresh `/private/tmp` directory; do not commit it.

**Interfaces:**
- Consumes: all previous tasks.
- Produces: fresh deterministic verification evidence, screenshots, audit findings, Harness review output, and one local Git commit.

- [ ] **Step 1: Run the product review surface and capture actual screenshots**

Start the local Vite server on loopback and capture at least:

- macOS manual DMG update;
- Windows update ready;
- update-check failure;
- macOS manual update at minimum supported viewport.

Use the Product Design audit workflow to inspect screenshots for hierarchy, readability, clipping, button clarity, long Chinese copy, and responsive behavior. Fix every blocker/serious finding through a new failing UI test, then recapture.

- [ ] **Step 2: Run complete fresh verification**

Run:

```bash
npm run release:versions:check
npm run check
cargo test --manifest-path src-tauri/Cargo.toml --locked
npm run installer:verify
npm run icon:verify
git diff --check
ruby -e 'require "yaml"; YAML.load_file(".github/workflows/upstream-sync.yml"); YAML.load_file(".github/workflows/desktop.yml"); puts "workflow yaml ok"'
```

Also scan the related diff for secrets and forbidden mutation patterns, and inspect `git status --short` plus the full diff.

- [ ] **Step 3: Attempt local macOS unsigned build when prerequisites permit**

Run a local Apple Silicon Tauri build using the generated macOS bundle config without Apple credentials. If dependency download or platform tooling blocks it, record the exact command/error as an external verification boundary; do not claim the DMG build passed.

- [ ] **Step 4: Verify requirements line by line**

Re-read the approved spec and this plan. Check every acceptance criterion against code, test output, screenshot evidence, or an explicitly stated post-push boundary. Do not substitute Harness/model confidence for deterministic evidence.

- [ ] **Step 5: Create the reviewable local commit**

After all local gates are green, stage only related files and commit with:

```bash
git commit -m "feat: automate desktop upstream releases"
```

Do not push.

- [ ] **Step 6: Run the local Harness deterministic review-worktree gate**

Create the temporary root and pass it to the deterministic reviewer:

```bash
HARNESS_REVIEW_ROOT="$(mktemp -d /private/tmp/dsh-desktop-harness-review.XXXXXX)"
PYTHONPATH=/Users/lym/WorkCode/ai/Harness python3 -c 'import json,sys; from pathlib import Path; from app.review_executor import ReviewExecutionOptions,ReviewWorktreeExecutor; root=Path(sys.argv[1]); result=ReviewWorktreeExecutor().execute(ReviewExecutionOptions(project_path="/Users/lym/WorkCode/ai/deepseek-harness-desktop",run_id=1,review_commit="HEAD",review_base="HEAD^",worktree_root=str(root / "worktrees"),verify_commands=["npm run check", "cargo test --manifest-path src-tauri/Cargo.toml --locked"])); (root / "review.json").write_text(result.to_json(),encoding="utf-8"); (root / "review.md").write_text(result.to_markdown(),encoding="utf-8"); print(result.to_json()); raise SystemExit(0 if result.status == "success" else 1)' "$HARNESS_REVIEW_ROOT"
```

This path calls the local Harness `ReviewWorktreeExecutor` directly, creates detached base/head worktrees, compares the same verification commands on both revisions, writes only below the new temporary root, and does not open Harness SQLite. Read both generated reports. Independently reproduce any regression finding before changing source.

- [ ] **Step 7: Close review findings and finalize the single commit**

If Harness finds a real regression, add a failing test, implement the fix, rerun Steps 1-6, then stage related files and amend without changing the subject:

```bash
git commit --amend --no-edit
```

If Harness reports success, do not amend. Confirm the repository is clean and the commit contains no temporary review output. Report the final commit hash, verification counts, product/Harness findings, remaining post-push checks, and the exact GitHub permissions/Secrets the user must configure.
