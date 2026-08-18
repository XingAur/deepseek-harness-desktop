# DeepSeek Harness Shell and Runtime Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep a trusted DeepSeek Harness Desktop title bar visible in every phase, repair the managed Web profile startup, report early process exits accurately, and standardize user-facing branding and diagnostics.

**Architecture:** The bundled React page remains the top-level Tauri document and owns all window controls. After the managed Runtime is healthy, Rust emits a validated renderer URL and React embeds the loopback workbench in a cross-origin iframe; the iframe receives no Tauri capability. Runtime startup composes the official base and web-app bundles before the Desktop plugin and races HTTP readiness against cancellation and child exit.

**Tech Stack:** Tauri 2.11, Rust 2024/Tokio, React 18, TypeScript 6, Vite 8, Vitest 4, DeepSeek Harness 0.1.0-rc.7.

---

## File structure

- `scripts/desktop-profile.mjs`: one focused helper that repairs the generated Desktop profile bundle order.
- `scripts/desktop-profile.test.ts`: tests clean, stale, and already-correct profile manifests.
- `scripts/build-runtime.mjs`: copies the helper into the Runtime and calls it from the generated launcher.
- `src-tauri/src/window.rs`: validates and decorates renderer URLs without navigating the top-level WebView.
- `src-tauri/src/runtime/model.rs`: adds the ready event and diagnostic snapshot types.
- `src-tauri/src/runtime/process.rs`: exposes non-blocking child status and bounded log flushing.
- `src-tauri/src/runtime/health.rs`: races health probes against cancellation and child exit.
- `src-tauri/src/runtime/manager.rs`: orchestrates ready delivery, post-ready monitoring, and diagnostic state.
- `src-tauri/src/runtime/diagnostics.rs`: serializes manager-provided failure metadata.
- `src-tauri/src/commands.rs`: contains local-page-only window commands.
- `src/window-client.ts`: testable TypeScript boundary for the five window operations.
- `src/TitleBar.tsx`: permanent traffic-light title bar.
- `src/App.tsx`, `src/runtime-contract.ts`, `src/runtime-reducer.ts`: trusted shell, ready iframe state, and recovery transitions.
- `src/app.css`: frameless shell, responsive recovery card, and iframe layout.
- `src-tauri/tauri*.conf.json`, `index.html`: frameless window, CSP, and product title.
- `packages/dsh-plugin-desktop/tsdown.config.ts`: removes the deprecated tsdown option.
- `README.md`, `runtime/README.md`, `CLAUDE.md`: user-facing full-name terminology.

### Task 1: Repair the generated Desktop profile

**Files:**
- Create: `scripts/desktop-profile.mjs`
- Create: `scripts/desktop-profile.test.ts`
- Modify: `scripts/build-runtime.mjs`

- [ ] **Step 1: Write the failing profile tests**

```ts
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { DESKTOP_BUNDLES, ensureDesktopProfile } from './desktop-profile.mjs'

function fixture(bundles: string[]) {
  const root = mkdtempSync(join(tmpdir(), 'deepseek-harness-profile-'))
  const path = join(root, 'package.json')
  writeFileSync(path, JSON.stringify({ name: 'dsh-profile-desktop', dsh: { profile: { bundles } } }))
  return path
}

describe('ensureDesktopProfile', () => {
  it('inserts the official web app between base and Desktop plugin', () => {
    const path = fixture(['@deepseek-ai/dsh-base', '@dsh/desktop-plugin'])
    expect(ensureDesktopProfile(path)).toBe(true)
    expect(JSON.parse(readFileSync(path, 'utf8')).dsh.profile.bundles).toEqual(DESKTOP_BUNDLES)
  })

  it('repairs stale ordering and preserves unrelated manifest fields', () => {
    const path = fixture(['@dsh/desktop-plugin', '@deepseek-ai/dsh-base'])
    const before = JSON.parse(readFileSync(path, 'utf8'))
    ensureDesktopProfile(path)
    const after = JSON.parse(readFileSync(path, 'utf8'))
    expect(after.name).toBe(before.name)
    expect(after.dsh.profile.bundles).toEqual(DESKTOP_BUNDLES)
  })

  it('does not rewrite an already-correct profile', () => {
    const path = fixture(DESKTOP_BUNDLES)
    expect(ensureDesktopProfile(path)).toBe(false)
  })
})
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `npx vitest run scripts/desktop-profile.test.ts`

Expected: FAIL because `scripts/desktop-profile.mjs` does not exist.

- [ ] **Step 3: Implement deterministic profile repair**

```js
// scripts/desktop-profile.mjs
import { readFileSync, writeFileSync } from 'node:fs'

export const DESKTOP_BUNDLES = Object.freeze([
  '@deepseek-ai/dsh-base',
  '@deepseek-ai/dsh-web-app',
  '@dsh/desktop-plugin',
])

export function ensureDesktopProfile(manifestPath) {
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
  manifest.dsh ??= {}
  manifest.dsh.profile ??= {}
  const current = manifest.dsh.profile.bundles
  if (JSON.stringify(current) === JSON.stringify(DESKTOP_BUNDLES)) return false
  manifest.dsh.profile.bundles = [...DESKTOP_BUNDLES]
  writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')
  return true
}
```

Modify `build-runtime.mjs` to copy the helper into `stage/app/desktop-profile.mjs`, then generate this launcher sequence after the optional plugin add:

```js
import { ensureDesktopProfile } from './desktop-profile.mjs'
const profileManifest = join(home, 'profiles', 'desktop', 'package.json')
ensureDesktopProfile(profileManifest)
const child = spawn(process.execPath, [dsh, '--profile', 'desktop', ...process.argv.slice(2)], { stdio: 'inherit', env })
```

- [ ] **Step 4: Run profile and root tests and verify GREEN**

Run: `npx vitest run scripts/desktop-profile.test.ts scripts/canonical-json.test.ts`

Expected: both files pass.

- [ ] **Step 5: Commit only these task files**

Run: `git commit --only -m "fix(runtime): compose the DeepSeek Harness web profile" -- scripts/desktop-profile.mjs scripts/desktop-profile.test.ts scripts/build-runtime.mjs`

### Task 2: Deliver a validated renderer URL instead of navigating away

**Files:**
- Modify: `src-tauri/src/window.rs`
- Modify: `src-tauri/src/runtime/model.rs`
- Modify: `src-tauri/src/runtime/manager.rs`
- Modify: `src/runtime-contract.ts`
- Modify: `src/runtime-reducer.ts`
- Modify: `src/runtime-reducer.test.ts`

- [ ] **Step 1: Add failing reducer and URL tests**

Add to `src/runtime-reducer.test.ts`:

```ts
it('accepts renderer URL only from the active ready operation', () => {
  const state: RuntimeViewState = { ...initialRuntimeState, operationId: 'op-1' }
  const next = runtimeReducer(state, {
    type: 'runtime-event',
    event: { kind: 'ready', operationId: 'op-1', rendererUrl: 'http://127.0.0.1:39000/?dsh-desktop-mode=advanced' },
  })
  expect(next).toMatchObject({ phase: 'ready', rendererUrl: expect.stringContaining('127.0.0.1:39000') })
})

it('clears a ready renderer when the active process fails', () => {
  const state: RuntimeViewState = { ...initialRuntimeState, phase: 'ready', operationId: 'op-1', rendererUrl: 'http://127.0.0.1:39000/' }
  const next = runtimeReducer(state, {
    type: 'runtime-event',
    event: { kind: 'failure', operationId: 'op-1', payload: { code: 'process', message: '进程已退出', recoverable: true } },
  })
  expect(next.rendererUrl).toBeNull()
  expect(next.phase).toBe('failed')
})
```

Add Rust tests in `window.rs` that accept `http://127.0.0.1:39000/` when the expected port is `39000`, reject `http://127.0.0.1:39001/`, and reject `http://localhost:39000/`.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `npx vitest run src/runtime-reducer.test.ts`

Run: `cargo test window --manifest-path src-tauri/Cargo.toml`

Expected: TypeScript has no `ready` event/`rendererUrl`; Rust has no pure URL builder.

- [ ] **Step 3: Add the ready contract and pure URL builder**

Use this event shape on both sides:

```ts
export interface RuntimeReadyEnvelope {
  kind: 'ready'
  operationId: string
  rendererUrl: string
}
export type RuntimeEvent = RuntimeProgressEnvelope | RuntimeReadyEnvelope | RuntimeFailureEnvelope
```

Add `rendererUrl: string | null` to `RuntimeViewState`, initialize it to `null`, set it on matching ready, and clear it on bootstrap/failure.

In Rust add:

```rust
Ready {
    #[serde(rename = "operationId")]
    operation_id: String,
    #[serde(rename = "rendererUrl")]
    renderer_url: String,
},
```

Replace `navigate_to_runtime` with a pure `runtime_renderer_url(mut renderer, expected_port, session_token) -> Result<Url, RuntimeFailure>` that performs the existing scheme/host/port checks and query decoration. `RuntimeManager` emits `RuntimeEvent::Ready` with that URL and does not call `window.navigate`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `npx vitest run src/runtime-reducer.test.ts && cargo test window --manifest-path src-tauri/Cargo.toml`

Expected: all focused tests pass.

- [ ] **Step 5: Commit only these task files**

Run: `git commit --only -m "feat(shell): keep the trusted page after Runtime ready" -- src-tauri/src/window.rs src-tauri/src/runtime/model.rs src-tauri/src/runtime/manager.rs src/runtime-contract.ts src/runtime-reducer.ts src/runtime-reducer.test.ts`

### Task 3: Add local-only permanent window controls

**Files:**
- Create: `src/window-client.ts`
- Create: `src/TitleBar.tsx`
- Create: `src/TitleBar.test.tsx`
- Modify: `src-tauri/src/commands.rs`
- Modify: `src-tauri/src/lib.rs`
- Modify: `src-tauri/tauri.conf.json`
- Modify: `src-tauri/tauri.windows.conf.json`
- Modify: `src-tauri/tauri.macos.conf.json`

- [ ] **Step 1: Write failing component tests**

```tsx
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TitleBar } from './TitleBar'

it('maps traffic lights to window operations', () => {
  const controls = { close: vi.fn(), minimize: vi.fn(), toggleMaximize: vi.fn(), startDragging: vi.fn() }
  render(<TitleBar controls={controls} />)
  fireEvent.click(screen.getByRole('button', { name: '关闭窗口' }))
  fireEvent.click(screen.getByRole('button', { name: '最小化窗口' }))
  fireEvent.click(screen.getByRole('button', { name: '最大化或还原窗口' }))
  expect(controls.close).toHaveBeenCalledOnce()
  expect(controls.minimize).toHaveBeenCalledOnce()
  expect(controls.toggleMaximize).toHaveBeenCalledOnce()
})

it('drags blank title space and toggles maximize on double click', () => {
  const controls = { close: vi.fn(), minimize: vi.fn(), toggleMaximize: vi.fn(), startDragging: vi.fn() }
  render(<TitleBar controls={controls} />)
  const bar = screen.getByRole('banner')
  fireEvent.mouseDown(bar)
  fireEvent.doubleClick(bar)
  expect(controls.startDragging).toHaveBeenCalledOnce()
  expect(controls.toggleMaximize).toHaveBeenCalledOnce()
})
```

- [ ] **Step 2: Run the test and verify RED**

Run: `npx vitest run src/TitleBar.test.tsx`

Expected: FAIL because `TitleBar` does not exist.

- [ ] **Step 3: Implement the TypeScript/Rust boundary and title bar**

`window-client.ts` exports:

```ts
export interface WindowControls {
  close(): Promise<void>
  minimize(): Promise<void>
  toggleMaximize(): Promise<void>
  startDragging(): Promise<void>
}
export const tauriWindowControls: WindowControls = {
  close: () => invoke('close_window'),
  minimize: () => invoke('minimize_window'),
  toggleMaximize: () => invoke('toggle_maximize_window'),
  startDragging: () => invoke('start_drag'),
}
```

`TitleBar.tsx` renders three buttons, inline SVG glyphs, and “DeepSeek Harness Desktop”; it ignores drag initiation when `event.target.closest('button')` is present.

Rust commands receive `tauri::WebviewWindow` and call `close`, `minimize`, `is_maximized` plus `maximize`/`unmaximize`, and `start_dragging`. Register exactly these commands in `generate_handler!`. Set `decorations: false` in common and platform window configs.

- [ ] **Step 4: Run component and Rust checks and verify GREEN**

Run: `npx vitest run src/TitleBar.test.tsx`

Run: `cargo check --manifest-path src-tauri/Cargo.toml`

Expected: both commands exit 0.

- [ ] **Step 5: Commit only these task files**

Run: `git commit --only -m "feat(shell): add permanent traffic-light window controls" -- src/window-client.ts src/TitleBar.tsx src/TitleBar.test.tsx src-tauri/src/commands.rs src-tauri/src/lib.rs src-tauri/tauri.conf.json src-tauri/tauri.windows.conf.json src-tauri/tauri.macos.conf.json`

### Task 4: Render bootstrap/recovery or the workbench inside the trusted shell

**Files:**
- Modify: `src/App.tsx`
- Modify: `src/App.test.tsx`
- Modify: `src/main.tsx`
- Modify: `src/app.css`
- Modify: `src-tauri/tauri.conf.json`

- [ ] **Step 1: Write failing shell persistence tests**

Add tests that render `App` with fake `WindowControls`, assert the title remains before and after a ready event, and assert:

```tsx
emit({ kind: 'ready', operationId: 'op-1', rendererUrl: 'http://127.0.0.1:39000/?dsh-desktop-mode=advanced' })
const frame = await screen.findByTitle('DeepSeek Harness 工作台')
expect(frame).toHaveAttribute('src', expect.stringContaining('127.0.0.1:39000'))
expect(screen.getByText('DeepSeek Harness Desktop')).toBeInTheDocument()
expect(document.querySelector('.windowShell')).toBeInTheDocument()
```

Also assert all recovery buttons remain reachable after a failure event following ready.

- [ ] **Step 2: Run App tests and verify RED**

Run: `npx vitest run src/App.test.tsx`

Expected: no title bar or iframe exists.

- [ ] **Step 3: Implement the persistent shell and responsive layout**

Change `AppProps` to accept `runtime` and `windowControls`. Render:

```tsx
<main className="windowShell">
  <TitleBar controls={windowControls} />
  <div className="windowContent">
    {state.rendererUrl === null ? <BootstrapContent /> : (
      <iframe className="workbenchFrame" title="DeepSeek Harness 工作台" src={state.rendererUrl} />
    )}
  </div>
</main>
```

Move existing recovery content into the local `BootstrapContent` branch without changing behavior. Make `html`, `body`, `#root`, `.windowShell` exactly `height: 100%` and `overflow: hidden`; allow only `.windowContent` or the details panel to scroll. Reduce card vertical margins with height media queries so 800×600 does not create body scrolling.

Add to CSP: `frame-src http://127.0.0.1:*`. Do not add a `remote` capability or broaden `connect-src`.

- [ ] **Step 4: Run App tests and web build and verify GREEN**

Run: `npx vitest run src/App.test.tsx && npm run build:web`

Expected: tests pass and Vite builds.

- [ ] **Step 5: Commit only these task files**

Run: `git commit --only -m "feat(shell): embed the workbench under the trusted title bar" -- src/App.tsx src/App.test.tsx src/main.tsx src/app.css src-tauri/tauri.conf.json`

### Task 5: Report child exit before the health deadline

**Files:**
- Modify: `src-tauri/src/runtime/process.rs`
- Modify: `src-tauri/src/runtime/health.rs`
- Modify: `src-tauri/src/runtime/manager.rs`

- [ ] **Step 1: Write failing async process/health tests**

Create a test-only child with `cmd /C exit 7` on Windows and `sh -c 'exit 7'` on Unix. Call `wait_for_health` with a five-second deadline and assert it returns within one second with:

```rust
assert_eq!(failure.code, RuntimeFailureCode::Process);
assert!(failure.message.contains("7"));
```

Retain a separate test using a live child and an unused loopback port to assert a short deadline produces `HealthTimeout`.

- [ ] **Step 2: Run Rust health tests and verify RED**

Run: `cargo test health --manifest-path src-tauri/Cargo.toml -- --nocapture`

Expected: the exited child still waits until the deadline or the new helper is missing.

- [ ] **Step 3: Implement non-blocking status and bounded log flush**

Add to `ManagedRuntime`:

```rust
pub fn try_exit(&mut self) -> Result<Option<std::process::ExitStatus>, RuntimeFailure> {
    self.child.try_wait().map_err(RuntimeFailure::internal)
}

pub async fn flush_logs(&mut self, budget: Duration) {
    let deadline = tokio::time::Instant::now() + budget;
    for mut task in self.log_tasks.drain(..) {
        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        if tokio::time::timeout(remaining, &mut task).await.is_err() { task.abort(); }
    }
}
```

Pass `Arc<Mutex<ManagedRuntime>>` into `wait_for_health`. Before each HTTP probe, check `try_exit`; on exit call `flush_logs(Duration::from_millis(500))` and return a `Process` failure containing `status.code()` or the signal description. Preserve cancellation precedence and the existing foreign-service check.

After ready, spawn one monitor associated with the operation ID; if `try_exit` later returns a status and the operation is still current, emit failure and clear the stored child. Explicit terminate paths mark the operation superseded/cancelled before killing so they do not emit an unexpected-exit failure.

- [ ] **Step 4: Run Rust tests and verify GREEN**

Run: `cargo test --manifest-path src-tauri/Cargo.toml`

Expected: all Rust tests pass and the early-exit test completes in under one second.

- [ ] **Step 5: Commit only these task files**

Run: `git commit --only -m "fix(runtime): surface managed process exits immediately" -- src-tauri/src/runtime/process.rs src-tauri/src/runtime/health.rs src-tauri/src/runtime/manager.rs`

### Task 6: Preserve actionable diagnostic metadata through rollback

**Files:**
- Modify: `src-tauri/src/runtime/model.rs`
- Modify: `src-tauri/src/runtime/manager.rs`
- Modify: `src-tauri/src/runtime/diagnostics.rs`

- [ ] **Step 1: Write a failing diagnostics ZIP test**

Use a temporary `RuntimePaths` and a `RuntimeDiagnosticSnapshot` containing version `0.1.0`, target `windows-x86_64`, phase `starting`, failure code `process`, exit code `7`, and log filename. Export, read `diagnostics.json` from the ZIP, and assert those values exist while a supplied `sessionToken=secret` log line is redacted.

- [ ] **Step 2: Run the diagnostics test and verify RED**

Run: `cargo test diagnostics --manifest-path src-tauri/Cargo.toml -- --nocapture`

Expected: `export` has no snapshot argument and diagnostics only reports `current.json`.

- [ ] **Step 3: Add manager-owned diagnostic state**

Define a serializable snapshot with these optional fields:

```rust
pub struct RuntimeDiagnosticSnapshot {
    pub operation_id: Option<String>,
    pub runtime_version: Option<Version>,
    pub target: Option<RuntimeTarget>,
    pub phase: RuntimePhase,
    pub failure: Option<RuntimeFailure>,
    pub exit_code: Option<i32>,
    pub log_file: Option<String>,
}
```

Store it in `ManagerState`, update it at manifest selection, phase changes, and failure, and clone it before `spawn_blocking`. Change `diagnostics::export(&paths, &snapshot)` to serialize this snapshot alongside the current pointer. Do not include environment values, command arguments, source, conversations, or tokens.

- [ ] **Step 4: Run diagnostics and all Rust tests and verify GREEN**

Run: `cargo test --manifest-path src-tauri/Cargo.toml`

Expected: all Rust tests pass.

- [ ] **Step 5: Commit only these task files**

Run: `git commit --only -m "feat(diagnostics): retain failed Runtime metadata" -- src-tauri/src/runtime/model.rs src-tauri/src/runtime/manager.rs src-tauri/src/runtime/diagnostics.rs`

### Task 7: Standardize full-name copy and remove the tsdown warning

**Files:**
- Modify: `src/App.tsx`
- Modify: `src-tauri/tauri.conf.json`
- Modify: `src-tauri/tauri.windows.conf.json`
- Modify: `src-tauri/src/lib.rs`
- Modify: `src-tauri/src/runtime/health.rs`
- Modify: `src-tauri/src/runtime/manager.rs`
- Modify: `src-tauri/src/runtime/process.rs`
- Modify: `index.html`
- Modify: `.github/workflows/desktop.yml`
- Modify: `README.md`
- Modify: `runtime/README.md`
- Modify: `CLAUDE.md`
- Modify: `packages/dsh-plugin-desktop/src/catalog.ts`
- Modify: `packages/dsh-plugin-desktop/src/client/MarketPage.tsx`
- Modify: `packages/dsh-plugin-desktop/src/plugin-command.ts`
- Modify: `packages/dsh-plugin-desktop/tsdown.config.ts`

- [ ] **Step 1: Add a failing user-copy source test**

Create a Vitest source test that scans the listed UI/config files and rejects standalone user-facing strings such as `DSH Desktop`, `DSH 工作台`, `DSH Runtime`, `非 DSH 服务`, and `DSH 插件体系`, while explicitly excluding `DSH_HOME`, `DSH_DESKTOP_*`, `@dsh/`, package names, URLs, and historical plan files.

- [ ] **Step 2: Run the copy test and plugin build and verify RED/warning**

Run: `npx vitest run scripts/product-copy.test.ts`

Run: `npm run plugin:build`

Expected: the copy test reports current abbreviated strings and tsdown prints the `external` deprecation warning.

- [ ] **Step 3: Replace visible branding and migrate tsdown config**

Use “DeepSeek Harness Desktop” for product/window/release titles and “DeepSeek Harness” in status/error/risk copy. In prose, introduce “DeepSeek Harness（DSH）” once before any necessary technical abbreviation. Do not rename environment variables, Rust/TypeScript identifiers, JSON fields, `@dsh/*`, repository URLs, or profile names.

Replace:

```ts
external: ['react', 'react/jsx-runtime', 'react-dom'],
```

with:

```ts
deps: { neverBundle: ['react', 'react/jsx-runtime', 'react-dom'] },
```

- [ ] **Step 4: Run copy test and plugin build and verify GREEN/clean output**

Run: `npx vitest run scripts/product-copy.test.ts && npm run plugin:build`

Expected: test passes and no `external` deprecation warning appears.

- [ ] **Step 5: Commit only these task files**

Run:

```powershell
git commit --only -m "chore: use the full DeepSeek Harness product name" -- src/App.tsx src-tauri/tauri.conf.json src-tauri/tauri.windows.conf.json src-tauri/src/lib.rs src-tauri/src/runtime/health.rs src-tauri/src/runtime/manager.rs src-tauri/src/runtime/process.rs index.html .github/workflows/desktop.yml README.md runtime/README.md CLAUDE.md packages/dsh-plugin-desktop/src/catalog.ts packages/dsh-plugin-desktop/src/client/MarketPage.tsx packages/dsh-plugin-desktop/src/plugin-command.ts packages/dsh-plugin-desktop/tsdown.config.ts scripts/product-copy.test.ts
```

### Task 8: Verify the original failure path and the complete desktop shell

**Files:**
- Verification only; return to the owning task if a check fails.

- [ ] **Step 1: Run every automated gate fresh**

Run: `npm run check`

Expected: all root/plugin tests and both builds pass with no warning.

Run: `cargo test --manifest-path src-tauri/Cargo.toml`

Expected: all Rust unit/integration/doc tests pass.

- [ ] **Step 2: Rebuild the Windows Runtime fixture**

Run:

```powershell
npm run runtime:build -- --target=windows-x86_64 --version=0.1.1-local --url=file:///D:/TraeCode/deepseek-harness-desktop/runtime-build/windows-x86_64/dsh-runtime-windows-x86_64.zip --output=runtime-build/windows-x86_64-fixed
```

Expected: archive and unsigned manifest are created; generated `app/desktop-profile.mjs` exists.

- [ ] **Step 3: Run the isolated startup feedback loop**

Run this PowerShell feedback loop:

```powershell
$stage = 'D:\TraeCode\deepseek-harness-desktop\runtime-build\windows-x86_64-fixed\stage'
$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = $listener.LocalEndpoint.Port
$listener.Stop()
$reproHome = Join-Path ([System.IO.Path]::GetTempPath()) ('deepseek-harness-runtime-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $reproHome | Out-Null
$env:DSH_HOME = $reproHome
$env:DSH_DESKTOP_MODE = 'advanced'
$env:DSH_DESKTOP_PLATFORM = 'win32'
$env:DSH_DESKTOP_SESSION_TOKEN = 'runtime-verification'
$env:DSH_DESKTOP_DSH_BIN = Join-Path $stage 'app\node_modules\@deepseek-ai\dsh\lib\bin.js'
$env:DSH_DESKTOP_CATALOG_PATH = Join-Path $stage 'catalog\community.json'
$env:DSH_DESKTOP_DSH_VERSION = '0.1.0-rc.7'
$env:DSH_DESKTOP_CATALOG_PUBLIC_KEY = 'cmFlmJvjXIrMN8AbIXxF2c6Gnpt9rDFd_Zhbl0U7AlI'
$process = Start-Process -FilePath (Join-Path $stage 'node.exe') -ArgumentList @((Join-Path $stage 'app\launcher.mjs'), '--port', $port) -PassThru -WindowStyle Hidden
try {
  $deadline = [DateTime]::UtcNow.AddSeconds(10)
  do {
    try { $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/" -TimeoutSec 1 } catch { $response = $null }
    if ($response -and $response.StatusCode -eq 200 -and $response.Content -match 'deepseek|dsh') { break }
    Start-Sleep -Milliseconds 250
  } while ([DateTime]::UtcNow -lt $deadline -and -not $process.HasExited)
  if (-not $response -or $response.StatusCode -ne 200 -or $response.Content -notmatch 'deepseek|dsh') { throw 'Runtime did not become healthy' }
} finally {
  if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
}
```

Expected: HTTP 200 body contains `deepseek` or `dsh`; output does not contain `waiting for service: webServer`.

- [ ] **Step 4: Prove the Runtime regression check is red-capable**

Copy the isolated profile manifest, remove `@deepseek-ai/dsh-web-app`, and invoke the underlying profile once without running `ensureDesktopProfile`.

Expected: process exits non-zero with `pending (waiting for service: webServer)`. Restore the generated helper path and rerun Step 3; it returns HTTP 200.

- [ ] **Step 5: Run the Windows Tauri shell acceptance check**

Run: `npm run tauri dev`

Verify: title bar remains during preparation, recovery, and workbench; red closes, yellow minimizes, green maximizes/restores; blank title space drags and double-click toggles maximize; no outer scrollbar appears at 800×600; workbench DevTools cannot invoke Tauri commands.

- [ ] **Step 6: Inspect the final diff and preserve unrelated work**

Run: `git status --short` and `git diff --check`.

Expected: no whitespace errors; pre-existing staged/user changes remain present and no unrelated file was reverted.
