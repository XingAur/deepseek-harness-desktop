# Windows 完整安装生命周期 E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立分层的 Windows 完整安装生命周期门禁，在 PR 验证安装、首次启动、双会话和默认卸载，在每日/RC 验证真实覆盖升级、状态保留和完整卸载矩阵。

**Architecture:** 复用现有确定性 Runtime、模型夹具和安装后 WebDriver Harness。构建层从同一提交生成基线版与候选版两个 NSIS 制品；TypeScript 负责业务状态快照和断言，PowerShell 负责注册表、安装器和受控清理，独立 GitHub Actions 工作流负责 quick/full 分层及脱敏证据上传。

**Tech Stack:** TypeScript 6、Vitest 4、Node.js ESM、PowerShell、Tauri 2、NSIS、WebdriverIO/Tauri embedded driver、GitHub Actions。

---

## 范围检查

本计划实现确定性 P0.2 主门禁。上一正式 Release 使用生产 Bundle ID、生产数据根且不含 WebDriver feature，不能安全地作为当前 E2E Bundle 的基线安装包。设计中提到的“上一正式 Release → 当前候选版”可选兼容验证必须另写独立规格，在一次性 Windows Runner 上使用生产身份候选包和更严格的清理保护；它不阻塞本计划的 quick/full 完成条件，也不能通过替换本计划的 baseline 路径伪装实现。

## 文件结构

### 新建

- `scripts/e2e/installer-build-plan.mjs`：纯函数生成基线/候选版本和制品路径。
- `scripts/e2e/installer-build-plan.d.mts`：构建计划的 TypeScript 类型。
- `scripts/e2e/installer-build-plan.test.ts`：版本派生和制品计划测试。
- `e2e/support/lifecycle-state.ts`：读取 Profile、Runtime、Workspace 和 Session 的升级快照。
- `e2e/support/lifecycle-state.test.ts`：快照和不变量比较测试。
- `e2e/support/lifecycle-report.ts`：阶段记录、路径脱敏和敏感字段扫描。
- `e2e/support/lifecycle-report.test.ts`：报告脱敏测试。
- `e2e/specs/upgrade-and-uninstall.installer.e2e.ts`：full 模式的覆盖升级与卸载矩阵。
- `scripts/e2e/run-installer-suite.mjs`：跨平台设置 quick/full 环境并启动 Vitest。
- `.github/workflows/windows-installer-e2e.yml`：独立 quick/full Windows 安装生命周期工作流。

### 修改

- `scripts/e2e/build-instrumented-setup.mjs`：支持 quick/full 构建并输出 schema v2 元数据。
- `e2e/support/world.ts`：读取 schema v2 元数据并向 Harness 提供两个安装包。
- `e2e/support/installer.ts`：支持指定制品安装、记录运行身份及三种卸载模式。
- `scripts/e2e/uninstall-web-setup.ps1`：使用记录中的 E2E 数据根并支持 `/DELETEPROJECTS`。
- `scripts/e2e/reset-web-setup.ps1`：只清理通过路径和所有权校验的 E2E 状态。
- `src-tauri/src/platform/windows.rs`：仅在 `e2e` feature 下把文档根定向到受控测试目录。
- `src-tauri/src/data_cleanup.rs`：让 E2E 清理器只识别固定测试 Bundle ID，禁止触碰生产数据根。
- `e2e/support/assertions.ts`：增加删除/保留分域断言。
- `e2e/support/desktop.ts`：增加继续当前会话和稳定状态探测。
- `e2e/specs/provisioning-success.installer.e2e.ts`：收口为 quick 生命周期并验证默认卸载。
- `scripts/product-copy.test.ts`：约束 PowerShell 删除边界和双选项参数。
- `package.json`：增加 quick/full 安装生命周期命令。
- `doc/README.md`：记录 P0.2 自动门禁状态和剩余人工 RC 项。

---

### Task 1: 生成确定性的基线与候选构建计划

**Files:**
- Create: `scripts/e2e/installer-build-plan.mjs`
- Create: `scripts/e2e/installer-build-plan.d.mts`
- Create: `scripts/e2e/installer-build-plan.test.ts`

- [ ] **Step 1: 编写版本派生失败测试**

```ts
import { describe, expect, it } from 'vitest'
import { createInstallerBuildPlan, deriveBaselineVersion } from './installer-build-plan.mjs'

describe('installer build plan', () => {
  it('derives a strictly lower numeric Windows version', () => {
    expect(deriveBaselineVersion('0.1.26')).toBe('0.1.25')
    expect(deriveBaselineVersion('0.1.0')).toBe('0.0.65535')
    expect(() => deriveBaselineVersion('0.0.0')).toThrow('无法派生更低的基线版本')
  })

  it('creates candidate-only and dual installer plans', () => {
    const quick = createInstallerBuildPlan({ mode: 'quick', candidateVersion: '0.1.26', artifactsRoot: 'C:\\e2e' })
    expect(quick.variants.map((item) => item.name)).toEqual(['candidate'])
    const full = createInstallerBuildPlan({ mode: 'full', candidateVersion: '0.1.26', artifactsRoot: 'C:\\e2e' })
    expect(full.variants.map((item) => [item.name, item.version])).toEqual([
      ['baseline', '0.1.25'],
      ['candidate', '0.1.26'],
    ])
  })

})
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `npx vitest run scripts/e2e/installer-build-plan.test.ts`

Expected: FAIL，提示找不到 `installer-build-plan.mjs`。

- [ ] **Step 3: 实现纯构建计划函数**

```js
import { join, resolve } from 'node:path'

export function deriveBaselineVersion(candidateVersion) {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(candidateVersion)
  if (match === null) throw new Error(`桌面版本不是三段数字 SemVer：${candidateVersion}`)
  const [major, minor, patch] = match.slice(1).map(Number)
  if (patch > 0) return `${major}.${minor}.${patch - 1}`
  if (minor > 0) return `${major}.${minor - 1}.65535`
  if (major > 0) return `${major - 1}.65535.65535`
  throw new Error('无法派生更低的基线版本')
}

export function createInstallerBuildPlan({ mode, candidateVersion, artifactsRoot }) {
  if (mode !== 'quick' && mode !== 'full') throw new Error(`不支持的 E2E 构建模式：${mode}`)
  const root = resolve(artifactsRoot)
  const variants = mode === 'full'
    ? [{ name: 'baseline', version: deriveBaselineVersion(candidateVersion) }, { name: 'candidate', version: candidateVersion }]
    : [{ name: 'candidate', version: candidateVersion }]
  return {
    mode,
    candidateVersion,
    variants: variants.map((variant) => ({
      ...variant,
      configPath: join(root, `tauri-${variant.name}.json`),
      installerPath: join(root, `DeepSeek-Harness-Desktop-E2E-${variant.name}-x64.exe`),
    })),
  }
}
```

`installer-build-plan.d.mts` 内容为：

```ts
export type InstallerBuildMode = 'quick' | 'full'
export interface InstallerVariant {
  name: 'baseline' | 'candidate'
  version: string
  configPath: string
  installerPath: string
}
export interface InstallerBuildPlan {
  mode: InstallerBuildMode
  candidateVersion: string
  variants: InstallerVariant[]
}
export function deriveBaselineVersion(candidateVersion: string): string
export function createInstallerBuildPlan(input: {
  mode: InstallerBuildMode
  candidateVersion: string
  artifactsRoot: string
}): InstallerBuildPlan
```

- [ ] **Step 4: 运行定向测试**

Run: `npx vitest run scripts/e2e/installer-build-plan.test.ts`

Expected: PASS，2 tests passed。

- [ ] **Step 5: 提交构建计划模块**

```powershell
git add scripts/e2e/installer-build-plan.mjs scripts/e2e/installer-build-plan.d.mts scripts/e2e/installer-build-plan.test.ts
git diff --cached --check
git commit -m "test(e2e): 定义双安装包构建计划"
```

### Task 2: 构建 quick/full 安装包并输出 schema v2 元数据

**Files:**
- Modify: `scripts/e2e/build-instrumented-setup.mjs`
- Modify: `e2e/support/world.ts`
- Test: `scripts/e2e/installer-build-plan.test.ts`

- [ ] **Step 1: 增加元数据结构测试**

在 `installer-build-plan.test.ts` 增加：

```ts
it('uses stable artifact names for world metadata', () => {
  const plan = createInstallerBuildPlan({ mode: 'full', candidateVersion: '2.4.0', artifactsRoot: 'C:\\e2e' })
  expect(plan.variants[0]?.installerPath).toMatch(/baseline-x64\.exe$/)
  expect(plan.variants[1]?.installerPath).toMatch(/candidate-x64\.exe$/)
})
```

- [ ] **Step 2: 运行测试并确认旧构建脚本尚未消费计划**

Run: `rg -n "createInstallerBuildPlan|schemaVersion: 2|installers" scripts/e2e/build-instrumented-setup.mjs e2e/support/world.ts`

Expected: 无匹配，命令退出码为 1。

- [ ] **Step 3: 修改构建脚本逐个生成和复制安装包**

在 `build-instrumented-setup.mjs` 中：

```js
import { createHash } from 'node:crypto'
import { createInstallerBuildPlan } from './installer-build-plan.mjs'

const modeArg = process.argv.find((value) => value.startsWith('--mode='))?.slice('--mode='.length) ?? 'quick'
const versions = loadReleaseVersions()
const plan = createInstallerBuildPlan({
  mode: modeArg,
  candidateVersion: versions.desktopVersion,
  artifactsRoot: artifacts,
})

for (const variant of plan.variants) {
  writeFileSync(variant.configPath, JSON.stringify({ version: variant.version }, null, 2), 'utf8')
  await run(process.execPath, [
    tauriCli,
    'build',
    '--features', 'e2e',
    '--config', 'src-tauri/tauri.e2e.conf.json',
    '--config', variant.configPath,
    '--bundles', 'nsis',
  ], {
    ...process.env,
    PATH: [cargoBin, process.env.PATH].filter(Boolean).join(';'),
    DSH_DESKTOP_RELEASE_PUBLIC_KEY: signing.publicKey,
  })
  copyFileSync(findLatestNsisBundle(), variant.installerPath)
}

const metadata = {
  schemaVersion: 2,
  mode: plan.mode,
  artifactRoot: artifacts,
  runtimeArchive: archive,
  runtimeVersion: process.env.DSH_E2E_RUNTIME_VERSION ?? versions.runtimeVersion,
  signingState,
  sourceCommit: process.env.GITHUB_SHA ?? null,
  installers: Object.fromEntries(plan.variants.map((variant) => [variant.name, {
    path: variant.installerPath,
    version: variant.version,
    sha256: createHash('sha256').update(readFileSync(variant.installerPath)).digest('hex'),
  }])),
}

function findLatestNsisBundle() {
  const candidates = readdirSync(bundleRoot)
    .filter((name) => name.toLowerCase().endsWith('.exe'))
    .map((name) => join(bundleRoot, name))
    .sort((left, right) => statSync(right).mtimeMs - statSync(left).mtimeMs)
  if (candidates.length === 0) throw new Error('Tauri 没有生成 NSIS 安装包')
  return candidates[0]
}
```

同时把 `readFileSync` 加入 `node:fs` 导入。Runtime 只构建一次，baseline 版本必须严格低于 candidate，否则构建立即失败。

- [ ] **Step 4: 修改 World 严格读取 schema v2**

```ts
interface InstrumentedSetup {
  schemaVersion: 2
  mode: 'quick' | 'full'
  artifactRoot: string
  runtimeArchive: string
  runtimeVersion: string
  signingState: string
  installers: {
    candidate: { path: string; version: string; sha256: string }
    baseline?: { path: string; version: string; sha256: string }
  }
}
```

`createE2EWorld()` 将 `build.installers` 传给 `WindowsInstallerHarness`，并继续用 candidate 作为默认安装包。拒绝 schema v1、缺少 candidate、非绝对制品路径或 SHA-256 不是 64 位十六进制的元数据。

- [ ] **Step 5: 运行构建计划和类型检查**

Run: `npx vitest run scripts/e2e/installer-build-plan.test.ts && npx tsc -b --pretty false`

Expected: 两个命令均 PASS。

- [ ] **Step 6: 提交双安装包构建能力**

```powershell
git add scripts/e2e/build-instrumented-setup.mjs e2e/support/world.ts scripts/e2e/installer-build-plan.test.ts
git diff --cached --check
git commit -m "feat(e2e): 构建基线与候选安装包"
```

### Task 3: 扩展安装器 Harness 和卸载安全参数

**Files:**
- Modify: `e2e/support/installer.ts`
- Modify: `e2e/support/world.ts`
- Modify: `scripts/e2e/uninstall-web-setup.ps1`
- Modify: `scripts/e2e/reset-web-setup.ps1`
- Modify: `src-tauri/src/platform/windows.rs`
- Modify: `src-tauri/src/data_cleanup.rs`
- Modify: `scripts/product-copy.test.ts`
- Modify: `e2e/support/assertions.ts`

- [ ] **Step 1: 编写 PowerShell 边界失败测试**

在 `scripts/product-copy.test.ts` 增加：

```ts
it('drives both uninstall choices from the recorded E2E roots', () => {
  const uninstall = readFileSync('scripts/e2e/uninstall-web-setup.ps1', 'utf8')
  expect(uninstall).toContain('[switch]$DeleteProjects')
  expect(uninstall).toContain("$arguments += '/DELETEPROJECTS'")
  expect(uninstall).toContain('[System.IO.Path]::GetFullPath($record.dataRoot)')
  expect(uninstall).not.toContain("Join-Path $env:LOCALAPPDATA 'ai.deepseek.harness.desktop'")
  expect(uninstall).toContain('.dsh-e2e-owned')
})
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `npx vitest run scripts/product-copy.test.ts -t "drives both uninstall choices"`

Expected: FAIL，缺少 `DeleteProjects`，且脚本仍包含硬编码生产 Bundle ID。

- [ ] **Step 3: 修改卸载脚本**

`uninstall-web-setup.ps1` 参数和命令组合改为：

```powershell
param(
  [Parameter(Mandatory = $true)][string]$RecordPath,
  [Parameter(Mandatory = $true)][string]$SentinelsPath,
  [switch]$DeleteAppData,
  [switch]$DeleteProjects
)

$recordedDataRoot = [System.IO.Path]::GetFullPath([string]$record.dataRoot)
$localAppData = [System.IO.Path]::GetFullPath($env:LOCALAPPDATA).TrimEnd('\')
if (-not $recordedDataRoot.StartsWith($localAppData + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
  throw 'Recorded data root is outside LOCALAPPDATA'
}
$ownershipMarker = Join-Path $recordedDataRoot '.dsh-e2e-owned'
if (($DeleteAppData -or $DeleteProjects) -and -not (Test-Path -LiteralPath $ownershipMarker -PathType Leaf)) {
  throw 'Explicit deletion requires an isolated root with a .dsh-e2e-owned marker'
}

$arguments = @('/P')
if ($DeleteProjects) { $arguments += '/DELETEPROJECTS' }
elseif ($DeleteAppData) { $arguments += '/DELETEAPPDATA' }
```

`DeleteProjects` 完成后验证应用数据根和 `scope = 'project'` 的哨兵都消失；`DeleteAppData` 只验证应用数据根消失并验证项目哨兵保留；默认模式验证全部哨兵哈希不变。

- [ ] **Step 4: 固定 E2E 后台清理的数据标识**

在 `src-tauri/src/data_cleanup.rs` 增加 feature 约束测试：

```rust
#[cfg(feature = "e2e")]
#[test]
fn e2e_cleanup_identifier_cannot_target_production_data() {
    assert_eq!(APP_IDENTIFIER, "ai.deepseek.harness.desktop.e2e");
    assert_eq!(PENDING_PREFIX, "ai.deepseek.harness.desktop.e2e.pending-delete-");
}
```

把常量改为编译期固定值，不读取可注入的环境变量：

```rust
#[cfg(not(feature = "e2e"))]
pub const APP_IDENTIFIER: &str = "ai.deepseek.harness.desktop";
#[cfg(feature = "e2e")]
pub const APP_IDENTIFIER: &str = "ai.deepseek.harness.desktop.e2e";

#[cfg(not(feature = "e2e"))]
const PENDING_PREFIX: &str = "ai.deepseek.harness.desktop.pending-delete-";
#[cfg(feature = "e2e")]
const PENDING_PREFIX: &str = "ai.deepseek.harness.desktop.e2e.pending-delete-";
```

`live_app_data_root()`、pending 根校验和后台 helper 全部继续只使用这两个常量。这样正式构建完全保持原标识，E2E 构建也不可能因参数或环境变量清理生产目录。

把现有断言中的生产 pending 目录字面量改为 `format!("{PENDING_PREFIX}{}", nonce)`，使默认构建和 E2E feature 都验证各自固定标识，而不是放宽路径校验。

- [ ] **Step 5: 为 E2E 构建隔离 Windows 文档根**

先在 `src-tauri/src/platform/windows.rs` 的测试模块增加：

```rust
#[cfg(feature = "e2e")]
#[test]
fn e2e_documents_root_requires_an_absolute_owned_directory() {
    let root = tempfile::tempdir().unwrap();
    std::fs::write(root.path().join(".dsh-e2e-documents-owned"), b"owned").unwrap();
    let actual = validate_e2e_documents_root(root.path().to_path_buf()).unwrap();
    assert_eq!(actual, root.path());
}
```

生产分支保持 `known_folder_path`，只在 E2E feature 中读取覆盖：

```rust
fn documents_dir(&self) -> Result<PathBuf, RuntimeFailure> {
    #[cfg(feature = "e2e")]
    if let Some(root) = std::env::var_os("DSH_E2E_DOCUMENTS_ROOT").map(PathBuf::from) {
        return validate_e2e_documents_root(root);
    }
    known_folder_path(&FOLDERID_Documents)
}

#[cfg(feature = "e2e")]
fn validate_e2e_documents_root(root: PathBuf) -> Result<PathBuf, RuntimeFailure> {
    if !root.is_absolute() || !root.join(".dsh-e2e-documents-owned").is_file() {
        return Err(RuntimeFailure::internal("E2E 文档目录缺少所有权标记"));
    }
    Ok(root)
}
```

`createE2EWorld()` 在启动夹具前创建 `$E2E_ROOT/projects-owned/.dsh-e2e-documents-owned`，并把其绝对路径写入 `DSH_E2E_DOCUMENTS_ROOT`。生产构建未启用 `e2e` feature，因此该环境变量不会改变正式应用行为。

- [ ] **Step 6: 扩展 TypeScript Harness**

```ts
export type InstallerVariantName = 'baseline' | 'candidate'
export type UninstallMode = 'preserve-all' | 'delete-app-data' | 'delete-all'

export interface PreservationSentinel {
  path: string
  sha256: string
  scope: 'app-data' | 'project' | 'external'
}

export interface InstallerHarness {
  installClean(variant?: InstallerVariantName): Promise<InstallationRecord>
  installOver(variant: InstallerVariantName): Promise<InstallationRecord>
  recordRuntimeIdentity(input: { runtimePid: number; runtimePort: number }): Promise<InstallationRecord>
  writePreservationSentinels(projectPath: string): Promise<PreservationSentinels>
  uninstall(mode: UninstallMode): Promise<void>
  cleanupOwnedProject(projectPath: string): Promise<void>
  cleanupRecordedProcesses(): Promise<void>
  appBinaryExists(): Promise<boolean>
}
```

`installClean()` 只在首次场景调用 `reset-web-setup.ps1`；`installOver()` 不重置数据。安装成功后，Harness 创建记录中的 E2E 数据根和 `.dsh-e2e-owned`，首次启动在该根内继续创建正式目录。`recordRuntimeIdentity()` 同时更新内存记录和 `latest-install.json`。`uninstall()` 把模式映射为无参数、`-DeleteAppData` 或 `-DeleteProjects`。

`reset-web-setup.ps1` 只有在数据根是 `LOCALAPPDATA` 的直接子目录、名称精确等于传入的 E2E Bundle ID、且存在 `.dsh-e2e-owned` 时才允许 `Remove-Item -Recurse`；缺少标记的旧目录必须报告人工处理，不能自动删除。

```powershell
if (Test-Path -LiteralPath $dataRoot) {
  $ownershipMarker = Join-Path $dataRoot '.dsh-e2e-owned'
  if (-not (Test-Path -LiteralPath $ownershipMarker -PathType Leaf)) {
    throw 'Refusing to reset an unowned E2E data root'
  }
  Remove-Item -LiteralPath $dataRoot -Recurse -Force
}
```

`cleanupOwnedProject()` 必须先验证目标是 `$E2E_ROOT/projects-owned` 的直接或间接子目录、存在 `.dsh-e2e-project-owned`，并拒绝 reparse point，然后才调用 `rmSync(projectPath, { recursive: true, force: true })`。`cleanupRecordedProcesses()` 只执行 `verify-cleanup.ps1 -TerminateRecorded`，不按进程名终止进程。

- [ ] **Step 7: 增加分域断言**

```ts
export async function expectSentinelScopes(
  sentinels: PreservationSentinels,
  expected: Record<PreservationSentinel['scope'], 'present' | 'absent'>,
): Promise<void> {
  for (const sentinel of sentinels.entries) {
    if (expected[sentinel.scope] === 'absent') {
      expect(existsSync(sentinel.path), `${sentinel.scope} should be removed`).toBe(false)
    } else {
      expect(existsSync(sentinel.path), `${sentinel.scope} should be preserved`).toBe(true)
      expect(sha256(readFileSync(sentinel.path))).toBe(sentinel.sha256)
    }
  }
}
```

- [ ] **Step 8: 运行定向测试、E2E feature Rust 测试和类型检查**

Run: `npx vitest run scripts/product-copy.test.ts && cargo test --locked --manifest-path src-tauri/Cargo.toml --features e2e e2e_cleanup_identifier_cannot_target_production_data && cargo test --locked --manifest-path src-tauri/Cargo.toml --features e2e e2e_documents_root_requires_an_absolute_owned_directory && npx tsc -b --pretty false`

Expected: PASS。

- [ ] **Step 9: 提交 Harness 与卸载安全扩展**

```powershell
git add e2e/support/installer.ts e2e/support/world.ts e2e/support/assertions.ts scripts/e2e/uninstall-web-setup.ps1 scripts/e2e/reset-web-setup.ps1 scripts/product-copy.test.ts src-tauri/src/platform/windows.rs src-tauri/src/data_cleanup.rs
git diff --cached --check
git commit -m "test(e2e): 覆盖安装器卸载模式"
```

### Task 4: 实现升级状态快照和不变量比较

该快照是 Profile selected、pending 和 last-known-good 的唯一升级证据；不能只比较页面文案。

**Files:**
- Create: `e2e/support/lifecycle-state.ts`
- Create: `e2e/support/lifecycle-state.test.ts`

- [ ] **Step 1: 编写快照比较失败测试**

```ts
import { describe, expect, it } from 'vitest'
import { compareUpgradeState, type LifecycleSnapshot } from './lifecycle-state'

const before: LifecycleSnapshot = {
  runtime: { version: '0.1.10-preview', activeDirToken: '$DATA_ROOT/runtime/active' },
  profile: { selectedId: 'p-1', lastKnownGoodId: 'p-1', revision: 2, pending: false },
  project: { workspaceId: 'w-1', pathToken: '$E2E_ROOT/projects-owned/中文 Ω', sentinelSha256: 'a'.repeat(64) },
  sessionIds: ['s-1', 's-2'],
}

describe('upgrade lifecycle state', () => {
  it('accepts preserved Runtime, Profile, project and sessions', () => {
    expect(compareUpgradeState(before, structuredClone(before))).toEqual([])
  })

  it('reports stable categories instead of raw objects', () => {
    const after = structuredClone(before)
    after.profile.pending = true
    after.sessionIds = ['s-1']
    expect(compareUpgradeState(before, after)).toEqual(['profile-pending', 'session-ids-changed'])
  })
})
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `npx vitest run e2e/support/lifecycle-state.test.ts`

Expected: FAIL，找不到 `lifecycle-state.ts`。

- [ ] **Step 3: 实现快照读取和比较**

实现以下公开接口：

```ts
export interface LifecycleSnapshot {
  runtime: { version: string; activeDirToken: string }
  profile: { selectedId: string; lastKnownGoodId: string; revision: number; pending: boolean }
  project: { workspaceId: string; pathToken: string; sentinelSha256: string }
  sessionIds: string[]
}

export function captureLifecycleSnapshot(input: {
  dataRoot: string
  projectPath: string
  roots: { dataRoot: string; e2eRoot: string; userHome: string; temp: string }
}): LifecycleSnapshot

export function compareUpgradeState(before: LifecycleSnapshot, after: LifecycleSnapshot): string[]

export function captureProjectPath(dataRoot: string): string
```

读取固定文件：

```ts
const receipt = readJson(join(dataRoot, 'state', 'provisioning.json'))
const profiles = readJson(join(dataRoot, 'profiles', 'profiles.json'))
const state = readJson(join(dataRoot, 'profiles', 'state.json'))
const selected = profiles.find((profile) => profile.id === state.selectedProfile?.profileId)
const workspaces = readJson(join(selected.dataRoot, 'storages', 'workspace.json'))
```

`readJson` 必须拒绝缺失文件、空文件和非对象 JSON：

```ts
function readJson(path: string): Record<string, any> {
  if (!existsSync(path)) throw new Error('lifecycle-state-missing-file')
  const value: unknown = JSON.parse(readFileSync(path, 'utf8'))
  if (value === null || typeof value !== 'object') throw new Error('lifecycle-state-invalid-json')
  return value as Record<string, any>
}
```

只接受 `global.workspaceIds` 正式登记且存在于 `tables.workspaces` 的 Workspace。Session ID 从目标 Workspace 的 `sessionIds` 读取并排序。比较函数返回稳定类别，不把原始路径或 JSON 写进错误消息。

`captureProjectPath(dataRoot)` 使用当前 selected Profile 的同一份 Workspace 注册表，要求恰好找到一个位于 `$E2E_ROOT/projects-owned` 下的 E2E Workspace；找不到或匹配多个都立即失败。测试不会从页面文本猜测项目路径。

- [ ] **Step 4: 运行快照测试**

Run: `npx vitest run e2e/support/lifecycle-state.test.ts`

Expected: PASS。

- [ ] **Step 5: 提交状态快照模块**

```powershell
git add e2e/support/lifecycle-state.ts e2e/support/lifecycle-state.test.ts
git diff --cached --check
git commit -m "test(e2e): 增加升级状态快照"
```

### Task 5: 实现生命周期报告脱敏

**Files:**
- Create: `e2e/support/lifecycle-report.ts`
- Create: `e2e/support/lifecycle-report.test.ts`

- [ ] **Step 1: 编写敏感信息失败测试**

```ts
import { describe, expect, it } from 'vitest'
import { sanitizeLifecycleReport } from './lifecycle-report'

describe('lifecycle report sanitization', () => {
  it('replaces roots and rejects credentials or conversation bodies', () => {
    const report = sanitizeLifecycleReport({
      stage: 'candidate-first-launch',
      path: 'C:\\Users\\alice\\AppData\\Local\\ai.deepseek.harness.desktop.e2e\\state.json',
      apiKey: 'sk-secret',
      prompt: 'private prompt',
    }, {
      userHome: 'C:\\Users\\alice',
      dataRoot: 'C:\\Users\\alice\\AppData\\Local\\ai.deepseek.harness.desktop.e2e',
      e2eRoot: 'E:\\work',
      temp: 'C:\\Users\\alice\\AppData\\Local\\Temp',
    })
    expect(JSON.stringify(report)).toContain('$DATA_ROOT\\state.json')
    expect(JSON.stringify(report)).not.toContain('alice')
    expect(JSON.stringify(report)).not.toContain('sk-secret')
    expect(JSON.stringify(report)).not.toContain('private prompt')
  })
})
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `npx vitest run e2e/support/lifecycle-report.test.ts`

Expected: FAIL，找不到报告模块。

- [ ] **Step 3: 实现字段白名单与路径替换**

```ts
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const FORBIDDEN_KEYS = /^(apiKey|authorization|cookie|prompt|response|messages?)$/i
const ALLOWED_KEYS = new Set([
  'schemaVersion', 'mode', 'stage', 'status', 'category', 'durationMs',
  'desktopVersion', 'runtimeVersion', 'installerSha256', 'snapshot',
  'differences', 'path', 'startedAt', 'finishedAt', 'runtime', 'profile',
  'project', 'sessionIds', 'version', 'activeDirToken', 'selectedId',
  'lastKnownGoodId', 'revision', 'pending', 'workspaceId', 'pathToken',
  'sentinelSha256', 'artifactRoot', 'runtimeArchive', 'signingState',
  'sourceCommit', 'installers', 'baseline', 'candidate', 'sha256',
  'installerPath', 'exitCode', 'uninstallKey', 'installRoot', 'appBinary',
  'shortcuts', 'dataRoot', 'provisioningReceipt', 'completedInstallEntry',
  'activeCandidate', 'generationId', 'phase', 'recordedAt',
])

export function sanitizeLifecycleReport(value: unknown, roots: RedactionRoots): unknown {
  return visit(value, (key, scalar) => {
    if (FORBIDDEN_KEYS.test(key)) return undefined
    if (!ALLOWED_KEYS.has(key) && key !== '') return undefined
    return typeof scalar === 'string' ? redactRoots(scalar, roots) : scalar
  })
}

export function lifecycleRedactionRoots(dataRoot: string): RedactionRoots {
  return {
    dataRoot: resolve(dataRoot),
    e2eRoot: resolve(process.env.DSH_E2E_ROOT ?? '.'),
    userHome: resolve(process.env.USERPROFILE ?? process.env.HOME ?? '.'),
    temp: resolve(tmpdir()),
  }
}
```

`visit` 和路径替换实现为：

```ts
function visit(value: unknown, transform: (key: string, scalar: unknown) => unknown, key = ''): unknown {
  if (Array.isArray(value)) return value.map((entry) => visit(entry, transform, key))
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).flatMap(([childKey, childValue]) => {
      if (FORBIDDEN_KEYS.test(childKey) || !ALLOWED_KEYS.has(childKey)) return []
      return [[childKey, visit(childValue, transform, childKey)]]
    }))
  }
  return transform(key, value)
}

function redactRoots(value: string, roots: RedactionRoots): string {
  const replacements = [
    [roots.dataRoot, '$DATA_ROOT'],
    [roots.e2eRoot, '$E2E_ROOT'],
    [roots.userHome, '$USER_HOME'],
    [roots.temp, '$TEMP'],
  ].filter(([root]) => root.trim() !== '').sort((left, right) => right[0].length - left[0].length)
  return replacements.reduce(
    (result, [root, token]) => result.replace(new RegExp(escapeRegExp(root), 'gi'), token),
    value,
  )
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}
```

根路径按长度从长到短替换为 `$DATA_ROOT`、`$E2E_ROOT`、`$USER_HOME`、`$TEMP`，比较时忽略大小写。写文件前再次扫描 `sk-`、`Authorization:`、Windows 用户路径和测试模型正文；扫描失败只写 `{ schemaVersion, stage, status: 'redaction-failed' }`。

模块同时导出 `stageSafeLifecycleArtifacts({ artifactsRoot, roots })`。它重建 `e2e-artifacts/upload-safe`，只写入脱敏后的 `lifecycle-report.json`、`instrumented-setup.json`、`latest-install.json` 和 `generation-timeline.json`；截图只接受固定 E2E 场景名生成的 PNG。WebDriver 原始后端日志、Runtime 私钥、CA 私钥和未分类文件不进入 `upload-safe`。任何文本文件命中敏感扫描时，删除对应文件并写入 `redaction-failed.json`。

实现使用固定相对路径白名单：

```ts
export function stageSafeLifecycleArtifacts(input: { artifactsRoot: string; roots: RedactionRoots }): void {
  const output = join(input.artifactsRoot, 'upload-safe')
  rmSync(output, { recursive: true, force: true })
  mkdirSync(output, { recursive: true })
  const allowedJson = [
    'lifecycle-report.json',
    'instrumented-setup.json',
    join('installer-records', 'latest-install.json'),
    'generation-timeline.json',
  ]
  for (const relativePath of allowedJson) {
    const source = join(input.artifactsRoot, relativePath)
    if (!existsSync(source)) continue
    const sanitized = sanitizeLifecycleReport(JSON.parse(readFileSync(source, 'utf8')), input.roots)
    const text = JSON.stringify(sanitized, null, 2)
    if (/sk-[A-Za-z0-9_-]{8,}|Authorization:|C:\\Users\\/i.test(text)) {
      writeFileSync(join(output, 'redaction-failed.json'), JSON.stringify({ schemaVersion: 1, status: 'redaction-failed' }))
      continue
    }
    const destination = join(output, relativePath.replaceAll('\\', '-').replaceAll('/', '-'))
    writeFileSync(destination, text, 'utf8')
  }
  for (const name of readdirSync(input.artifactsRoot)) {
    if (/^(quick|full)-(baseline|candidate|preserve-all|delete-app-data|delete-all)-(failure|final)\.png$/.test(name)) {
      copyFileSync(join(input.artifactsRoot, name), join(output, name))
    }
  }
}
```

- [ ] **Step 4: 运行报告测试**

Run: `npx vitest run e2e/support/lifecycle-report.test.ts`

Expected: PASS。

- [ ] **Step 5: 提交脱敏报告模块**

```powershell
git add e2e/support/lifecycle-report.ts e2e/support/lifecycle-report.test.ts
git diff --cached --check
git commit -m "test(e2e): 生成脱敏生命周期报告"
```

### Task 6: 收口 PR quick 安装生命周期

本任务形成 PR 快速门禁，并继续使用包含中文和 `Ω` 的 Unicode 项目/会话标记。

**Files:**
- Modify: `e2e/support/desktop.ts`
- Modify: `e2e/specs/provisioning-success.installer.e2e.ts`
- Modify: `package.json`
- Create: `scripts/e2e/run-installer-suite.mjs`

- [ ] **Step 1: 为继续当前会话增加失败断言**

在 quick 用例重启恢复后增加：

```ts
await desktop.continueConversation('E2E 升级后继续 Ω')
await desktop.assertSessionRoundTrip([
  FIRST_SESSION_MARKER,
  SECOND_SESSION_MARKER,
  'E2E 升级后继续 Ω',
])
```

并在默认卸载前：

```ts
const projectPath = captureProjectPath(installation.dataRoot)
const sentinels = await installer.writePreservationSentinels(projectPath)
await desktop.quit()
await installer.uninstall('preserve-all')
expect(await installer.appBinaryExists()).toBe(false)
await expectSentinelScopes(sentinels, { 'app-data': 'present', project: 'present', external: 'present' })
```

保存模块级 `latestDataRoot`；安装成功后赋值。把现有 `afterAll` 改为：

```ts
import { resolve } from 'node:path'
import { lifecycleRedactionRoots, stageSafeLifecycleArtifacts } from '../support/lifecycle-report'

afterAll(async () => {
  try {
    await world?.close()
  } finally {
    if (latestDataRoot !== undefined) {
      stageSafeLifecycleArtifacts({
        artifactsRoot: resolve(process.env.DSH_E2E_ARTIFACTS ?? 'e2e-artifacts'),
        roots: lifecycleRedactionRoots(latestDataRoot),
      })
    }
  }
})
```

- [ ] **Step 2: 运行类型检查并确认接口缺失**

Run: `npx tsc -b --pretty false`

Expected: FAIL，提示 `continueConversation`、新 `writePreservationSentinels` 参数或 `uninstall` 尚未在所有实现中匹配。

- [ ] **Step 3: 实现继续当前会话**

在 `DesktopHarness` 和 `PackagedDesktopHarness` 增加：

```ts
async continueConversation(prompt: string): Promise<void> {
  if (prompt.trim() === '') throw new Error('继续会话消息不能为空')
  await this.withWorkbenchTarget(async (page) => {
    const composer = conversationComposerExpression()
    await page.waitFor(`${composer} !== null`, { timeoutMs: 30_000, message: '会话输入框未出现' })
    await page.setValueFromExpression(composer, prompt)
    await page.click('button[aria-label="发送消息"]')
    await page.waitFor(conversationContainsExpression(prompt), { timeoutMs: 30_000, message: '用户消息未实时显示' })
    await page.waitFor(conversationContainsExpression('E2E_PONG'), { timeoutMs: 60_000, message: '模型回复未实时显示' })
  })
}
```

启动后调用 `installer.recordRuntimeIdentity({ runtimePid: await desktop.runtimePid(), runtimePort: await desktop.runtimePort() })`，使卸载和收尾断言使用真实记录。

- [ ] **Step 4: 增加 quick/full 命令**

```json
{
  "e2e:setup:quick": "node scripts/e2e/build-instrumented-setup.mjs --mode=quick",
  "e2e:setup:full": "node scripts/e2e/build-instrumented-setup.mjs --mode=full",
  "e2e:installer:quick": "node scripts/e2e/run-installer-suite.mjs quick",
  "e2e:installer:full": "node scripts/e2e/run-installer-suite.mjs full"
}
```

本仓库不新增 `cross-env` 依赖。创建 `scripts/e2e/run-installer-suite.mjs`：

```js
import { spawn } from 'node:child_process'
import { resolve } from 'node:path'

const mode = process.argv[2]
if (mode !== 'quick' && mode !== 'full') throw new Error('安装生命周期模式必须是 quick 或 full')
const vitest = resolve('node_modules/vitest/vitest.mjs')
const args = ['run', '--config', 'vitest.e2e.config.ts']
if (mode === 'quick') args.push('e2e/specs/provisioning-success.installer.e2e.ts')
const child = spawn(process.execPath, [vitest, ...args], {
  stdio: 'inherit',
  windowsHide: true,
  env: { ...process.env, DSH_E2E_MODE: mode },
})
child.once('error', (error) => { throw error })
child.once('exit', (code, signal) => {
  if (signal !== null) process.kill(process.pid, signal)
  else process.exitCode = code ?? 1
})
```

- [ ] **Step 5: 运行 TypeScript 和 quick 用例的收集检查**

Run: `npx tsc -b --pretty false && npx vitest list --config vitest.e2e.config.ts`

Expected: 类型检查 PASS，列表包含 quick 和 full 两个 spec；full spec 后续用 `describe.runIf` 控制执行。

- [ ] **Step 6: 提交 quick 生命周期**

```powershell
git add e2e/support/desktop.ts e2e/specs/provisioning-success.installer.e2e.ts package.json scripts/e2e/run-installer-suite.mjs
git diff --cached --check
git commit -m "test(e2e): 完成 PR 安装生命周期"
```

### Task 7: 实现 full 覆盖升级与卸载矩阵

**Files:**
- Create: `e2e/specs/upgrade-and-uninstall.installer.e2e.ts`
- Modify: `e2e/support/world.ts`
- Modify: `e2e/support/installer.ts`
- Test: `e2e/support/lifecycle-state.test.ts`

- [ ] **Step 1: 编写完整生命周期用例骨架**

```ts
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createE2EWorld, type E2EWorld } from '../support/world'
import type { DesktopHarness } from '../support/desktop'
import { captureLifecycleSnapshot, captureProjectPath, compareUpgradeState } from '../support/lifecycle-state'
import { lifecycleRedactionRoots, stageSafeLifecycleArtifacts } from '../support/lifecycle-report'

const FIRST_SESSION_MARKER = 'E2E 第一会话 Ω'
const SECOND_SESSION_MARKER = 'E2E 第二会话 二'
const POST_UPGRADE_MARKER = 'E2E 升级后继续 Ω'
let world: E2EWorld
let latestDataRoot: string | undefined

beforeEach(async () => { world = await createE2EWorld() })
afterEach(async () => {
  try {
    await world?.close()
    await world?.installer.cleanupRecordedProcesses()
  } finally {
    if (latestDataRoot !== undefined) {
      stageSafeLifecycleArtifacts({
        artifactsRoot: resolve(process.env.DSH_E2E_ARTIFACTS ?? 'e2e-artifacts'),
        roots: lifecycleRedactionRoots(latestDataRoot),
      })
    }
  }
})

async function createTwoSessionState(desktop: DesktopHarness) {
  await desktop.createProject({ idea: `${FIRST_SESSION_MARKER}：请创建 README，并在完成后回复确认` })
  await desktop.createConversation(`${SECOND_SESSION_MARKER}：请回复确认`)
  await desktop.assertSessionRoundTrip([FIRST_SESSION_MARKER, SECOND_SESSION_MARKER])
}

describe.runIf(process.env.DSH_E2E_MODE === 'full')('Windows installer full lifecycle', () => {
  it('upgrades baseline to candidate without changing user state', async () => {
    const baseline = await world.installer.installClean('baseline')
    latestDataRoot = baseline.dataRoot
    await world.desktop.launch(baseline.appBinary)
    await world.desktop.waitForWorkbench(120_000)
    await createTwoSessionState(world.desktop)
    const projectPath = captureProjectPath(baseline.dataRoot)
    const before = captureLifecycleSnapshot({
      dataRoot: baseline.dataRoot,
      projectPath,
      roots: lifecycleRedactionRoots(baseline.dataRoot),
    })
    await world.desktop.quit()

    const candidate = await world.installer.installOver('candidate')
    await world.desktop.launch(candidate.appBinary)
    await world.desktop.waitForWorkbench(8_000)
    const after = captureLifecycleSnapshot({
      dataRoot: candidate.dataRoot,
      projectPath,
      roots: lifecycleRedactionRoots(candidate.dataRoot),
    })
    expect(compareUpgradeState(before, after)).toEqual([])
    await world.desktop.assertSessionRoundTrip([FIRST_SESSION_MARKER, SECOND_SESSION_MARKER])
    await world.desktop.continueConversation(POST_UPGRADE_MARKER)
    await world.desktop.quit()
    await world.desktop.launch(candidate.appBinary)
    await world.desktop.waitForWorkbench(8_000)
    await world.desktop.assertSessionRoundTrip([FIRST_SESSION_MARKER, SECOND_SESSION_MARKER, POST_UPGRADE_MARKER])
  })
})

```

- [ ] **Step 2: 增加三个独立卸载场景**

```ts
it.each([
  ['preserve-all', { 'app-data': 'present', project: 'present', external: 'present' }],
  ['delete-app-data', { 'app-data': 'absent', project: 'present', external: 'present' }],
  ['delete-all', { 'app-data': 'absent', project: 'absent', external: 'present' }],
] as const)('uninstalls with %s', async (mode, expected) => {
  const installation = await world.installer.installClean('candidate')
  latestDataRoot = installation.dataRoot
  await world.desktop.launch(installation.appBinary)
  await world.desktop.waitForWorkbench(120_000)
  await world.desktop.createProject({ idea: `E2E 卸载 ${mode} Ω` })
  const projectPath = captureProjectPath(installation.dataRoot)
  const sentinels = await world.installer.writePreservationSentinels(projectPath)
  await world.desktop.quit()
  await world.installer.uninstall(mode)
  await expectSentinelScopes(sentinels, expected)
})
```

每个场景的项目目录必须位于 `$E2E_ROOT/projects-owned`，并写入 `.dsh-e2e-project-owned`。保留项目的场景完成断言后，调用 Harness 的 `cleanupOwnedProject()`；该函数重新校验父目录和标记后只删除单个测试项目。

- [ ] **Step 3: 运行 full spec 并确认真实失败点**

Run: `npm run e2e:setup:full && npm run e2e:installer:full`

Expected: 第一次运行允许在尚未完成的断言处 FAIL，但必须完成双安装包构建并进入 `baseline-install`；记录实际失败阶段，不放宽断言。

- [ ] **Step 4: 完成阶段报告和失败收尾**

每个主要阶段调用：

```ts
await report.stage('candidate-first-launch', async () => {
  await world.desktop.launch(candidate.appBinary)
  await world.desktop.waitForWorkbench(8_000)
})
```

`afterEach` 保持上面给出的 `world.close()` 和 `world.installer.cleanupRecordedProcesses()` 顺序；后者复用 `verify-cleanup.ps1 -TerminateRecorded`，只终止路径位于记录安装根或 Runtime 根的 PID。报告写入 `e2e-artifacts/lifecycle-report.json`。

- [ ] **Step 5: 重跑完整本地生命周期**

Run: `npm run e2e:setup:full && npm run e2e:installer:full`

Expected: PASS；报告包含 `candidate-install`、`state-comparison`、三个卸载模式，且不存在真实用户目录、API Key 或对话正文。

- [ ] **Step 6: 提交完整生命周期**

```powershell
git add e2e/specs/upgrade-and-uninstall.installer.e2e.ts e2e/support/world.ts e2e/support/installer.ts e2e/support/lifecycle-state.test.ts
git diff --cached --check
git commit -m "test(e2e): 覆盖升级与卸载矩阵"
```

### Task 8: 接入独立 GitHub Actions 工作流

**Files:**
- Create: `.github/workflows/windows-installer-e2e.yml`
- Modify: `package.json`
- Modify: `scripts/product-copy.test.ts`

- [ ] **Step 1: 编写工作流静态约束测试**

在 `scripts/product-copy.test.ts` 增加：

```ts
it('separates quick pull-request and full scheduled installer lifecycles', () => {
  const workflow = readFileSync('.github/workflows/windows-installer-e2e.yml', 'utf8')
  expect(workflow).toContain('pull_request:')
  expect(workflow).toContain('schedule:')
  expect(workflow).toContain('workflow_dispatch:')
  expect(workflow).toContain('npm run e2e:setup:quick')
  expect(workflow).toContain('npm run e2e:setup:full')
  expect(workflow).toContain('retention-days: 14')
})
```

- [ ] **Step 2: 运行静态测试并确认失败**

Run: `npx vitest run scripts/product-copy.test.ts -t "installer lifecycles"`

Expected: FAIL，工作流文件不存在。

- [ ] **Step 3: 创建 quick/full 工作流**

工作流触发和模式选择使用：

```yaml
name: Windows Installer Lifecycle E2E

on:
  pull_request:
    paths:
      - 'src-tauri/windows/**'
      - 'src-tauri/src/runtime/**'
      - 'src-tauri/src/profile/**'
      - 'src-tauri/src/projects/**'
      - 'src-tauri/src/platform/**'
      - 'src-tauri/src/data_cleanup.rs'
      - 'src-tauri/tauri*.json'
      - 'packages/dsh-plugin-desktop/**'
      - 'e2e/**'
      - 'scripts/e2e/**'
      - 'package.json'
      - 'package-lock.json'
      - 'release/versions.json'
      - '.github/workflows/windows-installer-e2e.yml'
  schedule:
    - cron: '30 18 * * *'
  workflow_dispatch:
    inputs:
      mode:
        description: Installer lifecycle mode
        type: choice
        options: [quick, full]
        default: full

concurrency:
  group: windows-installer-e2e-${{ github.ref }}
  cancel-in-progress: false
```

Job 使用以下完整骨架；PR 固定 quick，schedule 固定 full，手动任务使用输入值：

```yaml
jobs:
  lifecycle:
    runs-on: windows-latest
    timeout-minutes: 90
    permissions:
      contents: read
    env:
      E2E_MODE: ${{ github.event_name == 'pull_request' && 'quick' || github.event_name == 'schedule' && 'full' || inputs.mode }}
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false

      - name: Read Node version
        id: versions
        shell: pwsh
        run: |
          $versions = Get-Content -LiteralPath 'release/versions.json' -Raw | ConvertFrom-Json
          "node_version=$($versions.nodeVersion)" >> $env:GITHUB_OUTPUT

      - uses: actions/setup-node@v4
        with:
          node-version: ${{ steps.versions.outputs.node_version }}
          cache: npm

      - uses: dtolnay/rust-toolchain@stable

      - name: Install dependencies
        run: npm ci

      - name: Test deterministic fixtures
        run: npm run e2e:fixtures

      - name: Build installer candidates
        shell: pwsh
        run: |
          if ($env:E2E_MODE -eq 'quick') { npm run e2e:setup:quick }
          else { npm run e2e:setup:full }
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

      - name: Run installer lifecycle
        shell: pwsh
        run: |
          if ($env:E2E_MODE -eq 'quick') { npm run e2e:installer:quick }
          else { npm run e2e:installer:full }
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

- [ ] **Step 4: 添加失败时始终上传和受控收尾**

```yaml
- name: Verify recorded process cleanup
  if: always()
  shell: pwsh
  run: |
    $record = 'e2e-artifacts/installer-records/latest-install.json'
    if (Test-Path -LiteralPath $record) {
      ./scripts/e2e/verify-cleanup.ps1 -RecordPath $record -TerminateRecorded
    }

- uses: actions/upload-artifact@v4
  if: always()
  with:
    name: windows-installer-lifecycle-${{ github.run_id }}
    path: e2e-artifacts/upload-safe
    if-no-files-found: warn
    retention-days: 14
```

上传前无论测试成功还是失败都调用 `stageSafeLifecycleArtifacts()`。扫描失败时不复制原始报告，只保留 `redaction-failed.json`。

- [ ] **Step 5: 运行工作流静态测试和 YAML 检查**

Run: `npx vitest run scripts/product-copy.test.ts && node -e "require('fs').readFileSync('.github/workflows/windows-installer-e2e.yml','utf8'); console.log('workflow readable')"`

Expected: 测试 PASS，工作流文件可读。

- [ ] **Step 6: 提交 CI 门禁**

```powershell
git add .github/workflows/windows-installer-e2e.yml package.json scripts/product-copy.test.ts
git diff --cached --check
git commit -m "ci(e2e): 接入 Windows 安装生命周期"
```

### Task 9: 完整验证并更新开发入口

**Files:**
- Modify: `doc/README.md`

- [ ] **Step 1: 更新 P0.2 状态**

在 `doc/README.md` 将 P0.2 标记为：

```markdown
### P0.2 Windows 完整安装生命周期 E2E

已完成自动化底座：PR quick 门禁验证安装、首次启动、双会话、重启恢复和默认保留卸载；每日/手动 full 门禁验证双安装包覆盖升级、状态不变量和三个有效卸载结果。

仍需人工完成：每个 Release Candidate 在真实安装版连续执行 30 轮创建、回复和会话切换，并记录机器、版本、结果和诊断摘要。
```

“下一步从这里开始”改为 P0.3 Runtime 激活前契约门禁的剩余项，明确已有 Session Contract，不重复实现已完成内容。

- [ ] **Step 2: 运行所有 JS/TS 门禁**

Run: `npm run check`

Expected: 单元测试、插件测试、Agent 测试和三个构建全部 PASS。

- [ ] **Step 3: 运行 Rust 门禁**

Run: `cargo test --locked --manifest-path src-tauri/Cargo.toml`

Expected: PASS；若 Windows 本机仍出现已记录的测试二进制 `STATUS_ENTRYPOINT_NOT_FOUND`，不得写成通过，必须保留失败证据并依赖 CI 的干净 Windows Runner 复核。

- [ ] **Step 4: 运行 quick 生命周期**

Run: `npm run e2e:setup:quick && npm run e2e:installer:quick`

Expected: PASS，默认卸载保留应用数据、项目和外部哨兵。

- [ ] **Step 5: 运行 full 生命周期**

Run: `npm run e2e:setup:full && npm run e2e:installer:full`

Expected: PASS，升级不变量为空差异，三个卸载结果通过，报告完成脱敏。

- [ ] **Step 6: 检查提交内容**

Run: `git status --short && git diff --check && rg -n "sk-[A-Za-z0-9_-]{8,}|Authorization:|C:\\Users\\" e2e-artifacts doc docs .github scripts e2e`

Expected: 工作区只包含本任务预期文档改动；源码和文档不包含真实密钥或用户绝对路径。`e2e-artifacts` 保持被忽略，不进入暂存区。

- [ ] **Step 7: 提交开发状态文档**

```powershell
git add doc/README.md
git diff --cached --check
git commit -m "docs(e2e): 记录安装生命周期门禁"
```

- [ ] **Step 8: 推送并观察工作流**

Run: `git push origin main`

Expected: 推送成功；相关路径变更触发 `Windows Installer Lifecycle E2E` quick Job。等待 Job 完成后记录运行链接和结论；若失败，按报告的稳定阶段修复并单独提交，不跳过门禁。

---

## 实施检查点

1. 完成 Task 1-3 后检查双安装包元数据和删除安全边界。
2. 完成 Task 4-6 后执行一次本地 quick 生命周期。
3. 完成 Task 7 后执行一次本地 full 生命周期，确认升级与三种卸载结果。
4. 完成 Task 8-9 后观察 GitHub Actions 的干净 Windows Runner 结果。
5. 安装版连续 30 轮仍作为 Release Candidate 人工验收，不因为自动化通过而省略。
