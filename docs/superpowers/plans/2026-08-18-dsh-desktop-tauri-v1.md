# DSH Desktop Tauri V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 Electron 目录整体重写为 Windows x64 与 macOS arm64 的 Tauri 2 桌面项目，完成联网 Runtime 自举、官方 DSH 工作台组合和精选社区插件的一键安装/更新/卸载源码。

**Architecture:** 本地 React 页面只显示启动、下载和恢复状态；Rust Runtime Manager 验证签名清单、下载并原子激活受管 Runtime、启动 loopback DSH Host，然后把主 WebView 导航到官方 DSH Web UI。独立的 DSH Host/Client 插件复用官方 surface 组成 anywhere 风格工作台，并以受控 HTTP/SSE 服务调用官方 `dsh plugin --profile desktop` 管理精选插件。

**Tech Stack:** Tauri 2.11、Rust stable、React 18、Vite 8、TypeScript 6、Vitest 4、DSH 0.1.0-rc.7、Cordis 4、tsdown 0.22。

**Specification:** `docs/superpowers/specs/2026-08-18-dsh-desktop-tauri-rewrite-design.md`

**Commit policy:** 用户要求第一版完成后统一提交；任务 1–13 不创建中间提交，任务 14 只提交一次。

---

## File map

现有 `dsh-desktop/` 按全新项目替换，保留可用图标素材，其余 Electron 主进程、preload、静态 renderer、fixtures 和 electron-builder 配置删除。

```text
dsh-desktop/
├─ package.json                    # npm workspace 与统一命令
├─ package-lock.json               # 锁定前端和 DSH 插件依赖
├─ index.html                      # 本地 bootstrap SPA 入口
├─ vite.config.ts                  # Tauri/Vite 固定端口与测试配置
├─ vitest.config.ts
├─ tsconfig.json
├─ src/
│  ├─ main.tsx                     # React 入口
│  ├─ App.tsx                      # 启动/恢复状态路由
│  ├─ app.css                      # 首启与恢复视觉
│  ├─ runtime-contract.ts          # 与 Rust command 对齐的类型
│  ├─ runtime-client.ts            # 唯一 invoke/listen 适配器
│  ├─ runtime-reducer.ts           # 纯状态机
│  └─ *.test.ts(x)                 # reducer 与 UI 测试
├─ src-tauri/
│  ├─ Cargo.toml
│  ├─ build.rs
│  ├─ tauri.conf.json
│  ├─ tauri.windows.conf.json
│  ├─ tauri.macos.conf.json
│  ├─ capabilities/bootstrap.json  # 仅本地页面可调用的命令
│  └─ src/
│     ├─ main.rs
│     ├─ lib.rs                    # Tauri setup、插件与 command 注册
│     ├─ commands.rs               # 前端可调用的窄接口
│     ├─ window.rs                 # 平台标题栏与 loopback 导航
│     └─ runtime/
│        ├─ mod.rs
│        ├─ model.rs               # 状态、清单和错误类型
│        ├─ paths.rs               # 平台数据目录
│        ├─ manifest.rs            # Ed25519、目标与兼容校验
│        ├─ download.rs            # 下载、进度、取消与 SHA-256
│        ├─ archive.rs             # 安全解包
│        ├─ activation.rs          # staging/current/rollback
│        ├─ process.rs             # 子进程树与日志
│        ├─ health.rs              # loopback 健康检查
│        ├─ diagnostics.rs         # 脱敏诊断包
│        └─ manager.rs             # 单一状态机协调器
├─ packages/dsh-plugin-desktop/
│  ├─ package.json
│  ├─ cordis.patch.yml             # Web profile 插入 Host/Client 插件
│  ├─ tsdown.config.ts
│  ├─ tsconfig*.json
│  ├─ THIRD_PARTY_NOTICES.md
│  ├─ src/index.ts                 # Host 插件入口
│  ├─ src/catalog.ts               # 精选目录签名与缓存
│  ├─ src/plugin-command.ts        # dsh plugin 子进程和互斥
│  ├─ src/market-routes.ts         # REST/SSE/取消
│  ├─ src/client/index.ts          # Client 插件入口
│  ├─ src/client/AdvancedFrame.tsx # 官方 surfaces 组合
│  ├─ src/client/MarketPage.tsx    # 社区插件页面
│  ├─ src/client/layout-*.ts       # 布局状态/服务
│  ├─ src/client/styles.ts         # anywhere 风格窗口 CSS
│  └─ tests/*.spec.ts(x)
├─ runtime/
│  ├─ manifests/dev-*.json         # 两平台开发清单夹具
│  ├─ catalog/community.json       # 可验证的内置空/示例精选目录
│  └─ README.md                    # 制品格式
├─ scripts/
│  ├─ canonical-json.mjs           # 与 Rust/Host 一致的签名载荷
│  ├─ sign-manifest.mjs            # 由外部私钥签清单
│  ├─ sign-catalog.mjs             # 由外部私钥签目录
│  └─ build-runtime.mjs            # 组装 Node + DSH + Desktop 插件
└─ .github/workflows/desktop.yml   # Windows 与 macOS arm64 检查/制品
```

### Task 1: Replace Electron scaffold with Tauri workspace

**Files:**
- Delete: `dsh-desktop/electron-builder.yml`, `dsh-desktop/src/main/**`, `dsh-desktop/src/preload.ts`, `dsh-desktop/src/renderer/**`, `dsh-desktop/fixtures/**`, obsolete Electron scripts
- Create: `dsh-desktop/package.json`, `index.html`, `vite.config.ts`, `vitest.config.ts`, `tsconfig.json`, `src/main.tsx`
- Create: `dsh-desktop/src-tauri/Cargo.toml`, `build.rs`, `tauri.conf.json`, platform configs, `src/main.rs`

- [ ] **Step 1: Remove the Electron-only tree and create the new directories**

Use `apply_patch` for tracked source deletion and explicit directory/file creation. Preserve `assets/icon.ico` and `assets/icon.png`; do not delete repository planning/spec files.

- [ ] **Step 2: Write the npm workspace**

`package.json` must expose these exact commands:

```json
{
  "name": "dsh-desktop",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "workspaces": ["packages/*"],
  "scripts": {
    "dev": "vite",
    "build:web": "tsc -b && vite build",
    "test": "vitest run",
    "test:watch": "vitest",
    "plugin:build": "npm run build -w @dsh/desktop-plugin",
    "plugin:test": "npm run test -w @dsh/desktop-plugin",
    "check": "npm run test && npm run plugin:test && npm run build:web && npm run plugin:build",
    "tauri": "tauri",
    "runtime:build": "node scripts/build-runtime.mjs"
  },
  "dependencies": {
    "@tauri-apps/api": "2.11.1",
    "@tauri-apps/plugin-dialog": "2.7.2",
    "@tauri-apps/plugin-process": "2.3.1",
    "@tauri-apps/plugin-updater": "2.10.1",
    "react": "18.3.1",
    "react-dom": "18.3.1"
  }
}
```

Add current compatible dev dependencies with exact lockfile versions, including `@tauri-apps/cli@2.11.4`, Vite 8, TypeScript 6, Vitest 4, Testing Library, jsdom and React type packages.

- [ ] **Step 3: Write minimal Tauri configuration**

Base window is initially local and hidden until setup completes:

```json
{
  "$schema": "https://schema.tauri.app/config/2",
  "productName": "DSH Desktop",
  "version": "0.1.0",
  "identifier": "ai.deepseek.harness.desktop",
  "build": {
    "beforeDevCommand": "npm run dev",
    "beforeBuildCommand": "npm run build:web",
    "devUrl": "http://localhost:1420",
    "frontendDist": "../dist"
  },
  "app": {
    "windows": [{
      "label": "main",
      "title": "DSH Desktop",
      "width": 1280,
      "height": 840,
      "minWidth": 900,
      "minHeight": 640,
      "visible": false,
      "decorations": true,
      "shadow": true
    }],
    "security": { "csp": "default-src 'self'; connect-src 'self' ipc: http://ipc.localhost" }
  },
  "bundle": {
    "active": true,
    "targets": ["nsis", "dmg"],
    "createUpdaterArtifacts": true,
    "icon": ["icons/32x32.png", "icons/128x128.png", "icons/icon.ico", "icons/icon.icns"]
  }
}
```

Windows override selects NSIS/x64 and Mica-compatible transparent chrome. macOS override selects `dmg`, arm64, `titleBarStyle: "Overlay"` and `trafficLightPosition`.

- [ ] **Step 4: Install JS dependencies without building Rust**

Run: `npm install`

Expected: `package-lock.json` is regenerated without Electron/electron-builder packages.

### Task 2: Define frontend runtime contract and reducer

**Files:**
- Create: `dsh-desktop/src/runtime-contract.ts`
- Create: `dsh-desktop/src/runtime-client.ts`
- Create: `dsh-desktop/src/runtime-reducer.ts`
- Test: `dsh-desktop/src/runtime-reducer.test.ts`

- [ ] **Step 1: Write reducer tests first**

Cover `checking → downloading → verifying → starting → ready`, cancellation to recovery, repair retry, and stale operation event rejection.

```ts
const initial: RuntimeViewState = { phase: 'checking', operationId: 'op-1', progress: null, error: null }
expect(runtimeReducer(initial, {
  type: 'runtime-progress', operationId: 'op-1', phase: 'downloading', completed: 20, total: 100,
})).toMatchObject({ phase: 'downloading', progress: { completed: 20, total: 100 } })
expect(runtimeReducer(initial, {
  type: 'runtime-progress', operationId: 'old', phase: 'ready', completed: 1, total: 1,
})).toBe(initial)
```

- [ ] **Step 2: Run the test and verify the missing-module failure**

Run: `npm test -- src/runtime-reducer.test.ts`

Expected: FAIL because the reducer does not exist.

- [ ] **Step 3: Implement the shared discriminated unions and pure reducer**

The Rust/TypeScript boundary uses these stable names:

```ts
export type RuntimePhase =
  | 'checking' | 'fetching-manifest' | 'downloading' | 'verifying'
  | 'activating' | 'starting' | 'ready' | 'cancelled' | 'failed'

export interface RuntimeProgressEvent {
  operationId: string
  phase: RuntimePhase
  completed: number
  total: number | null
  message: string
}

export interface RuntimeFailure {
  code: 'network' | 'signature' | 'archive' | 'process' | 'health-timeout' | 'cancelled' | 'internal'
  message: string
  recoverable: boolean
}
```

`runtime-client.ts` is the only file importing Tauri `invoke`/`listen`; expose `bootstrapRuntime()`, `cancelRuntime()`, `repairRuntime()`, `exportDiagnostics()` and `subscribeRuntimeProgress()`.

- [ ] **Step 4: Run reducer tests**

Run: `npm test -- src/runtime-reducer.test.ts`

Expected: PASS.

### Task 3: Build local bootstrap and recovery UI

**Files:**
- Create: `dsh-desktop/src/App.tsx`, `src/app.css`
- Modify: `dsh-desktop/src/main.tsx`
- Test: `dsh-desktop/src/App.test.tsx`

- [ ] **Step 1: Write UI tests**

Verify the initial check message, determinate download progress, cancel button, failure actions, and diagnostics success path. Use an injected `RuntimeClient` rather than mocking Tauri globals.

```tsx
render(<App runtime={fakeRuntime({ phase: 'failed', error: { code: 'process', message: '启动失败', recoverable: true } })} />)
expect(screen.getByRole('button', { name: '重试' })).toBeEnabled()
expect(screen.getByRole('button', { name: '修复运行时' })).toBeEnabled()
expect(screen.getByRole('button', { name: '导出诊断' })).toBeEnabled()
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `npm test -- src/App.test.tsx`

Expected: FAIL because `App` does not exist.

- [ ] **Step 3: Implement the UI**

Use one centered surface with DSH wordmark text, stage label, progress bar, expandable details and action row. No project cards, iframe, permanent dashboard or fake conversation UI.

- [ ] **Step 4: Run frontend tests**

Run: `npm test -- src/App.test.tsx src/runtime-reducer.test.ts`

Expected: PASS.

### Task 4: Define Rust runtime model, paths and signed manifest

**Files:**
- Create: `dsh-desktop/src-tauri/src/runtime/model.rs`, `paths.rs`, `manifest.rs`, `mod.rs`
- Test: inline `#[cfg(test)]` modules

- [ ] **Step 1: Define serializable contract types matching TypeScript**

```rust
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeProgressEvent {
    pub operation_id: String,
    pub phase: RuntimePhase,
    pub completed: u64,
    pub total: Option<u64>,
    pub message: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RuntimeManifest {
    pub schema_version: u32,
    pub version: Version,
    pub target: RuntimeTarget,
    pub url: Url,
    pub size: u64,
    pub sha256: String,
    pub signature: String,
    pub archive: ArchiveKind,
    pub entrypoint: String,
    pub args: Vec<String>,
    pub health_path: String,
}
```

- [ ] **Step 2: Implement platform paths**

Resolve only through Tauri `AppHandle::path().app_local_data_dir()` and create `runtime/versions`, `runtime/downloads`, `logs`, `catalog`, and `diagnostics`. Reject relative paths and NULs at every manifest boundary.

- [ ] **Step 3: Implement canonical payload and Ed25519 verification**

Remove the `signature` field, serialize recursively sorted JSON keys, decode base64 signature, verify against the embedded release public key, then validate target exactly as `windows-x86_64` or `darwin-aarch64`.

- [ ] **Step 4: Add Rust unit tests in source**

Use deterministic fixture key material under `#[cfg(test)]` for valid signature, altered field, wrong target, invalid SHA-256 length, unsafe entrypoint and unsupported schema.

Rust tests are written now; execution is deferred because the current Windows environment has no Rust toolchain and the user explicitly allowed code completion before building.

### Task 5: Implement download, safe extraction and atomic activation

**Files:**
- Create: `dsh-desktop/src-tauri/src/runtime/download.rs`, `archive.rs`, `activation.rs`
- Test: inline Rust tests

- [ ] **Step 1: Implement cancellable streaming download**

Use `reqwest` streaming into `<version>.part`, honor an existing partial length with HTTP Range only when the server returns `206`, enforce manifest size, emit progress after each chunk, and delete invalid partial data.

- [ ] **Step 2: Verify the completed file**

Read the staged file with `sha2::Sha256`, compare constant-length lowercase hex, and return `RuntimeFailureCode::Signature` or `Archive` without activating it.

- [ ] **Step 3: Implement safe ZIP and tar.gz extraction**

Reject absolute paths, parent traversal, NULs, Windows prefix components, symlinks/hardlinks and any output escaping staging. Limit extracted file count and total uncompressed bytes.

- [ ] **Step 4: Implement activation and rollback**

Extract to `versions/<version>.staging-<operation>`, fsync metadata, rename to `versions/<version>`, then atomically replace `current.json`. Retain the previous current version until the new runtime passes health checks.

- [ ] **Step 5: Add focused Rust tests**

Cover resume fallback, checksum mismatch, `../escape`, absolute archive entries, oversized extraction, successful current switch and rollback after failed health.

### Task 6: Implement process lifecycle and health checks

**Files:**
- Create: `dsh-desktop/src-tauri/src/runtime/process.rs`, `health.rs`
- Test: inline Rust tests

- [ ] **Step 1: Build the process specification**

Resolve the entrypoint under the active runtime only, append a random `--port 0`/runtime-provided port contract, set `DSH_HOME`, `DSH_DESKTOP_MODE=advanced`, `DSH_DESKTOP_PLATFORM`, and a per-launch session token. Pipe stdout/stderr to daily logs and redact token-like values.

- [ ] **Step 2: Manage the complete process tree**

On Windows create a Job Object with kill-on-close; on macOS start a process group and terminate group descendants. Cancellation and application quit use graceful termination followed by bounded forced termination.

- [ ] **Step 3: Implement health polling**

Poll `http://127.0.0.1:<port><health_path>` with a deadline, require the expected DSH marker/version, and return the renderer URL only after health passes. A foreign service response is a failure, never treated as DSH.

- [ ] **Step 4: Add process/health tests**

Use a tiny Rust test HTTP child for ready, timeout, foreign response, cancellation and log-redaction cases.

### Task 7: Implement Runtime Manager and Tauri command boundary

**Files:**
- Create: `dsh-desktop/src-tauri/src/runtime/manager.rs`
- Create: `dsh-desktop/src-tauri/src/commands.rs`, `window.rs`, `lib.rs`
- Modify: `dsh-desktop/src-tauri/src/main.rs`
- Create: `dsh-desktop/src-tauri/capabilities/bootstrap.json`

- [ ] **Step 1: Implement one-operation manager**

The manager owns a mutex-protected `RuntimeState`, one cancellation token, active child and previous known-good version. A second bootstrap/repair request returns the existing operation ID rather than launching duplicate work.

- [ ] **Step 2: Expose only four commands**

```rust
#[tauri::command]
async fn bootstrap_runtime(state: State<'_, RuntimeManager>) -> Result<BootstrapReply, RuntimeFailure>;
#[tauri::command]
async fn cancel_runtime(state: State<'_, RuntimeManager>) -> Result<(), RuntimeFailure>;
#[tauri::command]
async fn repair_runtime(state: State<'_, RuntimeManager>) -> Result<BootstrapReply, RuntimeFailure>;
#[tauri::command]
async fn export_diagnostics(state: State<'_, RuntimeManager>) -> Result<String, RuntimeFailure>;
```

- [ ] **Step 3: Navigate safely after Ready**

Validate the final URL is `http`, host is exactly `127.0.0.1`, and port equals the manager-owned port. Append the one-shot token and `dsh-desktop-mode=advanced`; then navigate the main WebView. Do not grant remote URL capabilities in `bootstrap.json`.

- [ ] **Step 4: Register Tauri plugins and shutdown**

Register single-instance, process, dialog and updater plugins; show the window after local DOM ready; on exit cancel operations and terminate the DSH process tree.

### Task 8: Implement native window presentation

**Files:**
- Modify: `dsh-desktop/src-tauri/tauri.windows.conf.json`, `tauri.macos.conf.json`
- Modify: `dsh-desktop/src-tauri/src/window.rs`

- [ ] **Step 1: Configure Windows**

Use hidden/overlay-compatible chrome and apply Mica when supported; fall back to an opaque dark window on older Windows. Keep native resize, shadow, min size and accessibility behavior.

- [ ] **Step 2: Configure macOS arm64**

Use `titleBarStyle: "Overlay"`, `decorations: true`, traffic lights at the approved sidebar inset, vibrancy/window effect where public Tauri APIs support it, and direct `.dmg` target only.

- [ ] **Step 3: Add navigation guards**

Allow local app assets and the manager-owned loopback origin only. Open `https:` documentation/repository links in the system browser; block every other in-WebView navigation.

### Task 9: Create the DSH Desktop Host/Client plugin and advanced layout

**Files:**
- Create: `dsh-desktop/packages/dsh-plugin-desktop/package.json`, configs and notices
- Create: `src/client/AdvancedFrame.tsx`, `advanced-shell.ts`, `environment.ts`, `layout-service.ts`, `layout-state.ts`, `styles.ts`, `theme-presenter.ts`, `contracts.ts`, `index.ts`
- Test: `tests/layout-state.spec.ts`, `client-environment.spec.ts`

- [ ] **Step 1: Configure the package against DSH rc.7**

Name the workspace `@dsh/desktop-plugin`, pin all used `@deepseek-ai/*` packages to `0.1.0-rc.7`, Cordis to `4.0.1`, React to `18.3.1`, and produce a Node ESM Host entry plus DSH module-loader Client entry with tsdown.

- [ ] **Step 2: Port the MIT advanced frame with attribution**

Copy and adapt only the layout modules from the inspected `anywhere-labs/dsh-plugin-desktop`; preserve its copyright in `THIRD_PARTY_NOTICES.md`. Keep official `sidebar`, `conversation`, `details` and `shell.overlay` slots as the rendered children.

- [ ] **Step 3: Adapt environment parsing for Tauri**

Accept only `mode=advanced` and platform `win32|darwin` from the manager-owned renderer query. Do not include Electron preload, terminal, updater or native directory picker bridges.

- [ ] **Step 4: Test and build the Client plugin**

Run: `npm run plugin:test && npm run plugin:build`

Expected: layout bounds and environment validation tests PASS; Host and Client bundles are emitted under `lib/`.

### Task 10: Implement signed curated catalog and plugin operations

**Files:**
- Create: `dsh-desktop/packages/dsh-plugin-desktop/src/catalog.ts`, `plugin-command.ts`, `market-routes.ts`
- Create: `src/client/MarketPage.tsx`, `market-client.ts`
- Test: `tests/catalog.spec.ts`, `plugin-command.spec.ts`, `market-routes.spec.ts`, `MarketPage.spec.tsx`

- [ ] **Step 1: Test catalog verification**

Verify valid Ed25519 catalog, tamper rejection, schema/target/DSH-range filtering and last-known-good cache behavior. Topic search results never enter the returned install list directly.

- [ ] **Step 2: Implement immutable catalog records**

Validate `id`, npm/GitHub repository URL, `installSpec`, package name, semver, supported targets and `verified: true`. Canonicalize the JSON exactly like the Rust manifest signer.

- [ ] **Step 3: Test plugin command construction and mutex**

Expected argument arrays are exact:

```ts
['plugin', '--profile', 'desktop', 'add', installSpec]
['plugin', '--profile', 'desktop', 'update', packageName]
['plugin', '--profile', 'desktop', 'remove', packageName]
```

Reject shell metacharacter interpretation by using `spawn(executable, args, { shell: false })`; validate identifiers; allow one write operation; cancellation kills the child tree; refresh installed state after exit.

- [ ] **Step 4: Implement REST and SSE routes**

Expose same-origin routes for catalog, installed state, operation start, operation event stream and cancel. Require a short-lived confirmation token minted from a preview request; apply origin and content-type checks to writes.

- [ ] **Step 5: Implement the market surface**

Register “社区插件” in the official sidebar/slot system. Show search, verified publisher/source, installed/update state, compatibility, install/update/uninstall confirmation, live stdout/stderr, cancellation and error recovery. Do not imitate a separate app-store shell.

- [ ] **Step 6: Run market tests**

Run: `npm run plugin:test`

Expected: catalog, process mutex, HTTP contract and React surface tests PASS.

### Task 11: Compose the Desktop profile and runtime bundle

**Files:**
- Create: `dsh-desktop/packages/dsh-plugin-desktop/cordis.patch.yml`, `src/index.ts`
- Create: `dsh-desktop/scripts/build-runtime.mjs`, `canonical-json.mjs`, signing scripts
- Create: `dsh-desktop/runtime/README.md`, development manifest/catalog fixtures

- [ ] **Step 1: Compose the Web profile**

Insert the Desktop Host plugin and market service after official Web bundles, set Web server host to `127.0.0.1`, port to `0`, `surfaceContext: true`, and disable the upstream root layout only in advanced mode while keeping official sidebar/conversation/details bundles.

- [ ] **Step 2: Build the runtime assembly script**

Given `--target windows-x86_64|darwin-aarch64` and an output directory, install Node 24 LTS, `@deepseek-ai/dsh@0.1.0-rc.7`, pnpm and the packed local Desktop plugin; write a launcher that boots profile `desktop` with the patch and prints a machine-readable ready record.

- [ ] **Step 3: Implement signing scripts**

Read private keys only from explicit environment/file arguments, never create or commit production keys. Canonicalize, sign with Ed25519, write signature fields, and include a development public key/fixture solely for tests with an explicit non-production marker.

- [ ] **Step 4: Verify scripts with Node tests**

Add Node/Vitest tests that sign fixtures, verify canonical byte equality with expected vectors and reject a mutated document.

### Task 12: Diagnostics and updater wiring

**Files:**
- Create: `dsh-desktop/src-tauri/src/runtime/diagnostics.rs`
- Modify: `dsh-desktop/src-tauri/src/lib.rs`, `tauri.conf.json`
- Test: inline Rust tests and frontend updater tests

- [ ] **Step 1: Implement redacted diagnostic archive**

Include versions, target, health summary, catalog status and bounded recent logs. Redact API keys, bearer/session tokens, environment values, conversation content and user source paths. Save through a native dialog under user control.

- [ ] **Step 2: Configure Tauri updater**

Enable signed updater artifacts and HTTPS endpoint templates for `windows-x86_64` and `darwin-aarch64`. Keep updater calls in the local bootstrap/settings boundary; official DSH remote content receives no updater permission.

- [ ] **Step 3: Add UI update states**

Expose check/download/relaunch progress in the local recovery/settings bridge and require user confirmation before installation.

### Task 13: CI, documentation and code-only verification

**Files:**
- Create: `dsh-desktop/.github/workflows/desktop.yml`
- Rewrite: `dsh-desktop/README.md`
- Modify: repository `.gitignore`

- [ ] **Step 1: Add two-platform CI**

Use Windows runner for x86_64 and Apple Silicon runner for aarch64. Install Node, npm dependencies and Rust stable; run JS/plugin tests, `cargo test`, frontend/plugin builds, then Tauri packaging in a release-only job. Signing/notarization steps activate only when secrets exist and never print secrets.

- [ ] **Step 2: Document developer and release flows**

Document architecture, first-run behavior, data locations, runtime/catalog signing, community-plugin warnings, Windows x64/macOS arm64 support, required certificates and exact commands.

- [ ] **Step 3: Run code-only checks requested for this cycle**

Run:

```powershell
npm test
npm run plugin:test
npm run build:web
npm run plugin:build
```

Expected: all TypeScript/React tests and builds PASS. Do not run `cargo build`, `cargo test`, `npm run tauri build` or installer packaging in this cycle because Rust is absent and the user approved code completion first.

- [ ] **Step 4: Inspect the full diff**

Run `git diff --check`, search for Electron imports, shell command concatenation, production private keys, placeholder markers and remote Tauri permissions. Expected: none remain; only approved planning files and V1 implementation changes are untracked/modified.

### Task 14: Unified V1 commit

**Files:** all approved V1 source, tests, specs, plans and documentation; exclude `.superpowers/`, local logs, build outputs and unrelated machine files.

- [ ] **Step 1: Review staged scope**

Stage explicit project/spec/plan paths, then run `git diff --cached --name-status`. Confirm `build-err.log`, `.claude/`, `.superpowers/`, `node_modules/`, `dist/`, `target/` and secrets are absent.

- [ ] **Step 2: Create the single requested commit**

```powershell
git commit -m "feat: rewrite DSH Desktop with Tauri"
```

- [ ] **Step 3: Report verification limits**

Report the passing JS/plugin checks and explicitly list Rust compilation, Windows installer smoke and macOS arm64 signing/notarization as not executed in this code-only cycle.

---

## Self-review checklist

- Spec coverage: Tasks 1–8 implement Tauri shell/runtime/security/window behavior; Tasks 9–11 implement official DSH workbench and community plugin lifecycle; Task 12 covers diagnostics/update; Task 13 covers both release targets and tests.
- Platform coverage: only Windows x64 and macOS arm64 enter configs, manifests and CI; Linux and macOS Intel have no release job.
- Data migration: no old Electron data reader or migration module is created.
- Type consistency: `RuntimePhase`, progress event fields and command names match between TypeScript and Rust snippets.
- Security: renderer never supplies executable paths or shell strings; manifests/catalogs are signed; remote DSH UI receives no Tauri capability.
- Commit policy: one final commit only, matching the user's instruction.
