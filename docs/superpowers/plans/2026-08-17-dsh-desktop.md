# dsh-desktop 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 DeepSeek Harness 的 Windows Electron 客户端：全离线内置 dsh、检测/启动/附加、插件市场运行时（捆绑 pnpm）、项目卡片（agent 生成的本地静态/start 项目）、托盘与 dsh 升级。

**Architecture:** Electron 主进程模块化（每个模块纯逻辑、依赖注入、可单测）；渲染层为纯静态 HTML；dsh 与 pnpm 以完整 node_modules 树 vendor 到 `resources/`，经 `ELECTRON_RUN_AS_NODE` 运行；数据统一在数据根（默认 `D:\DeepSeekHarness`）。

**Tech Stack:** Electron 43 + TypeScript + esbuild + electron-builder(NSIS) + vitest 4。规格见 `docs/superpowers/specs/2026-08-17-dsh-windows-client-design.md`。

**关键事实（已验证）:**

- `@deepseek-ai/dsh` 当前 `0.1.0-rc.6`，bin 入口 `node_modules/@deepseek-ai/dsh/lib/bin.js`，依赖约 255 包（必须整树安装，不能只 pack 单包）
- `pnpm` 当前 `11.22.0`，bin 入口 `node_modules/pnpm/bin/pnpm.mjs`
- Windows 10+ 自带 `tar.exe` 与 `taskkill.exe`
- 本机 dev 环境：Node 24、npm 10（git 2.26 旧版，分支用 `checkout -b`）

**约定:** 项目根 = `e:\code\deepseek-harness\dsh-desktop\`（新建子目录）。所有命令在 `dsh-desktop/` 下执行。每个任务独立提交。

---

## 任务列表

### Task 1: 项目脚手架

**Files:**

- Create: `dsh-desktop/package.json`, `tsconfig.json`, `vitest.config.ts`, `electron-builder.yml`, `.npmrc`, `scripts/copy-renderer.mjs`

- [ ] **Step 1: 创建目录与 package.json**

```bash
mkdir -p dsh-desktop && cd dsh-desktop
mkdir -p src/main src/renderer scripts resources fixtures/projects/hello-static fixtures/projects/hello-start tests
```

`package.json`：

```json
{
  "name": "dsh-desktop",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "main": "dist/main.js",
  "scripts": {
    "build": "esbuild src/main/index.ts --bundle --platform=node --external:electron --outfile=dist/main.js && esbuild src/preload.ts --bundle --platform=node --external:electron --outfile=dist/preload.js && node scripts/copy-renderer.mjs",
    "test": "vitest run",
    "fetch": "node scripts/fetch-dsh.mjs",
    "start": "npm run build && electron .",
    "dist": "npm run fetch && npm run build && electron-builder"
  }
}
```

`.npmrc`（国内源，install 与 fetch 都走）：

```text
registry=https://registry.npmmirror.com
```

- [ ] **Step 2: 安装 devDependencies**

```bash
npm i -D electron@^43 electron-builder@^26 esbuild typescript vitest @types/node
```

- [ ] **Step 3: tsconfig.json / vitest.config.ts / electron-builder.yml / copy 脚本**

`tsconfig.json`：

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "skipLibCheck": true,
    "noEmit": true,
    "types": ["node"]
  },
  "include": ["src/**/*.ts"]
}
```

`vitest.config.ts`：

```ts
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: { environment: 'node', include: ['src/**/*.test.ts', 'tests/**/*.test.ts'] },
})
```

`electron-builder.yml`：

```yaml
appId: com.deepseek.dshdesktop
productName: DeepSeek Harness
directories:
  output: release
files:
  - dist/**
  - resources/**
asarUnpack:
  - resources/**
win:
  target: nsis
nsis:
  oneClick: true
  perMachine: false
```

`scripts/copy-renderer.mjs`：

```js
import { cpSync, mkdirSync } from 'node:fs'

mkdirSync('dist', { recursive: true })
cpSync('src/renderer', 'dist/renderer', { recursive: true })
cpSync('fixtures', 'dist/fixtures', { recursive: true })
```

- [ ] **Step 4: 验证构建链**

```bash
npx tsc --noEmit && npm run build && ls dist
```

Expected: 无类型错误（此时还没有 ts 源文件也通过），`dist/renderer` 出现。

- [ ] **Step 5: Commit**

```bash
cd .. && git add dsh-desktop && git commit -m "chore(dsh-desktop): 脚手架（electron+esbuild+vitest+builder）"
```

---

### Task 2: paths.ts（数据根与统一路径）

**Files:**

- Create: `src/main/paths.ts`
- Test: `src/main/paths.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
import { mkdtempSync, rmSync, writeFileSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { appPaths, loadSettings, resolveDataRoot, saveSettings } from './paths.js'

const dirs: string[] = []
afterEach(() => { for (const d of dirs) rmSync(d, { recursive: true, force: true }); dirs.length = 0 })
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'paths-')); dirs.push(d); return d }

describe('resolveDataRoot', () => {
  it('显式 override 时直接使用并创建', () => {
    const root = join(tmp(), 'DataRoot')
    expect(resolveDataRoot(root)).toBe(root)
  })
  it('无 D 盘可写时回退 C 盘（注入探测）', () => {
    expect(resolveDataRoot(undefined, () => false)).toBe('C:\\DeepSeekHarness')
  })
})

describe('appPaths', () => {
  it('给出全部子路径', () => {
    const p = appPaths('R')
    expect(p.projectsDir).toBe(join('R', 'projects'))
    expect(p.dshHome).toBe(join('R', 'dsh-home'))
    expect(p.runtimeDshDir).toBe(join('R', 'runtime', 'dsh'))
    expect(p.binDir).toBe(join('R', 'bin'))
    expect(p.logsDir).toBe(join('R', 'logs'))
    expect(p.settingsFile).toBe(join('R', 'settings.json'))
  })
})

describe('settings', () => {
  it('roundtrip', () => {
    const f = join(tmp(), 'settings.json')
    saveSettings(f, { dataRoot: 'R', dshVersion: '1.0.0' })
    expect(loadSettings(f)).toEqual({ dataRoot: 'R', dshVersion: '1.0.0' })
  })
  it('文件不存在返回空对象', () => {
    expect(loadSettings(join(tmp(), 'nope.json'))).toEqual({ dataRoot: '' })
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/main/paths.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

```ts
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

export interface Settings { dataRoot: string; dshVersion?: string }

export function resolveDataRoot(override?: string, diskProbe: () => boolean = () => true): string {
  const root = override ?? (diskProbe() ? 'D:\\DeepSeekHarness' : 'C:\\DeepSeekHarness')
  mkdirSync(root, { recursive: true })
  return root
}

export interface AppPaths {
  dataRoot: string
  projectsDir: string
  dshHome: string
  runtimeDshDir: string
  binDir: string
  logsDir: string
  settingsFile: string
}

export function appPaths(dataRoot: string): AppPaths {
  return {
    dataRoot,
    projectsDir: join(dataRoot, 'projects'),
    dshHome: join(dataRoot, 'dsh-home'),
    runtimeDshDir: join(dataRoot, 'runtime', 'dsh'),
    binDir: join(dataRoot, 'bin'),
    logsDir: join(dataRoot, 'logs'),
    settingsFile: join(dataRoot, 'settings.json'),
  }
}

export function loadSettings(file: string): Settings {
  try { return JSON.parse(readFileSync(file, 'utf8')) } catch { return { dataRoot: '' } }
}

export function saveSettings(file: string, s: Settings): void {
  writeFileSync(file, JSON.stringify(s, null, 2), 'utf8')
}

export function dirWritable(dir: string): boolean {
  try {
    mkdirSync(dir, { recursive: true })
    const probe = join(dir, '.write-probe')
    writeFileSync(probe, '1')
    rmSync(probe)
    return true
  } catch { return false }
}
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/main/paths.test.ts`
Expected: PASS 全绿

- [ ] **Step 5: Commit**

```bash
git add src/main/paths.ts src/main/paths.test.ts && git commit -m "feat(dsh-desktop): 数据根与统一路径解析"
```

---

### Task 3: logger.ts（按天滚动日志）

**Files:**

- Create: `src/main/logger.ts`
- Test: `src/main/logger.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { createLogger } from './logger.js'

const dirs: string[] = []
afterEach(() => { for (const d of dirs) rmSync(d, { recursive: true, force: true }); dirs.length = 0 })
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'log-')); dirs.push(d); return d }
const today = () => new Date().toISOString().slice(0, 10)

describe('createLogger', () => {
  it('写入当天日志文件', () => {
    const dir = tmp()
    const log = createLogger(dir, 'main', 7)
    log('hello')
    const f = join(dir, `main-${today()}.log`)
    expect(existsSync(f)).toBe(true)
    expect(readFileSync(f, 'utf8')).toContain('hello')
  })
  it('只保留最近 N 天', () => {
    const dir = tmp()
    for (let i = 1; i <= 10; i++) writeFileSync(join(dir, `main-2026-01-${String(i).padStart(2, '0')}.log`), '')
    createLogger(dir, 'main', 7)
    const files = readdirSync(dir).filter(f => f.startsWith('main-'))
    expect(files.length).toBe(7)
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/main/logger.test.ts`
Expected: FAIL

- [ ] **Step 3: 实现**

```ts
import { appendFileSync, mkdirSync, readdirSync, rmSync } from 'node:fs'
import { join } from 'node:path'

export interface Logger { log(line: string): void }

export function createLogger(logsDir: string, name: string, keepDays: number): Logger {
  mkdirSync(logsDir, { recursive: true })
  prune(logsDir, name, keepDays)
  return {
    log(line: string) {
      const file = join(logsDir, `${name}-${new Date().toISOString().slice(0, 10)}.log`)
      appendFileSync(file, `${new Date().toISOString()} ${line}\n`, 'utf8')
    },
  }
}

function prune(logsDir: string, name: string, keepDays: number): void {
  const prefix = `${name}-`
  const files = readdirSync(logsDir).filter(f => f.startsWith(prefix)).sort()
  while (files.length > keepDays) rmSync(join(logsDir, files.shift()!), { force: true })
}
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/main/logger.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/main/logger.ts src/main/logger.test.ts && git commit -m "feat(dsh-desktop): 按天滚动日志"
```

---

### Task 4: port-probe.ts（端口探测与 dsh 特征判定）

**Files:**

- Create: `src/main/port-probe.ts`
- Test: `src/main/port-probe.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
import http from 'node:http'
import { AddressInfo } from 'node:net'
import { describe, expect, it } from 'vitest'
import { probe } from './port-probe.js'

function listen(handler: http.RequestListener): Promise<http.Server> {
  return new Promise(resolve => {
    const s = http.createServer(handler)
    s.listen(0, '127.0.0.1', () => resolve(s))
  })
}
const portOf = (s: http.Server) => (s.address() as AddressInfo).port

describe('probe', () => {
  it('无人监听 → none', async () => {
    const s = await listen((_q, res) => res.end())
    const port = portOf(s); s.close()
    await new Promise(r => setTimeout(r, 100))
    expect(await probe(port, 500)).toBe('none')
  })
  it('200 + text/html → dsh', async () => {
    const s = await listen((_q, res) => { res.setHeader('content-type', 'text/html'); res.end('<html></html>') })
    expect(await probe(portOf(s), 500)).toBe('dsh')
    s.close()
  })
  it('200 + json → foreign', async () => {
    const s = await listen((_q, res) => { res.setHeader('content-type', 'application/json'); res.end('{}') })
    expect(await probe(portOf(s), 500)).toBe('foreign')
    s.close()
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/main/port-probe.test.ts`
Expected: FAIL

- [ ] **Step 3: 实现**

```ts
import http from 'node:http'
import net from 'node:net'

export type ProbeResult = 'dsh' | 'foreign' | 'none'

export async function probe(port: number, timeoutMs = 2000): Promise<ProbeResult> {
  if (!(await tcpOk(port, timeoutMs))) return 'none'
  return (await httpLooksLikeDsh(port, timeoutMs)) ? 'dsh' : 'foreign'
}

function tcpOk(port: number, timeoutMs: number): Promise<boolean> {
  return new Promise(resolve => {
    const s = net.connect({ host: '127.0.0.1', port })
    s.setTimeout(timeoutMs)
    s.once('connect', () => { s.destroy(); resolve(true) })
    s.once('timeout', () => { s.destroy(); resolve(false) })
    s.once('error', () => resolve(false))
  })
}

function httpLooksLikeDsh(port: number, timeoutMs: number): Promise<boolean> {
  return new Promise(resolve => {
    const req = http.get({ host: '127.0.0.1', port, path: '/', timeout: timeoutMs }, res => {
      const ok = res.statusCode === 200 && String(res.headers['content-type'] ?? '').includes('text/html')
      res.resume()
      resolve(ok)
    })
    req.once('error', () => resolve(false))
    req.once('timeout', () => { req.destroy(); resolve(false) })
  })
}
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/main/port-probe.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/main/port-probe.ts src/main/port-probe.test.ts && git commit -m "feat(dsh-desktop): 端口探测与 dsh 特征判定"
```

---

### Task 5: dsh-resolver.ts（内置/用户区版本解析）

**Files:**

- Create: `src/main/dsh-resolver.ts`
- Test: `src/main/dsh-resolver.test.ts`

约定目录形态（fetch 脚本与升级器产出一致）：`<dir>/node_modules/@deepseek-ai/dsh/lib/bin.js`。

- [ ] **Step 1: 写失败测试**

```ts
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { resolveDsh } from './dsh-resolver.js'

const dirs: string[] = []
afterEach(() => { for (const d of dirs) rmSync(d, { recursive: true, force: true }); dirs.length = 0 })
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'res-')); dirs.push(d); return d }

function fakeInstall(root: string, version: string): void {
  const pkgDir = join(root, 'node_modules', '@deepseek-ai', 'dsh')
  mkdirSync(join(pkgDir, 'lib'), { recursive: true })
  writeFileSync(join(pkgDir, 'lib', 'bin.js'), '')
  writeFileSync(join(pkgDir, 'package.json'), JSON.stringify({ name: '@deepseek-ai/dsh', version }))
}

describe('resolveDsh', () => {
  it('无用户区版本 → 内置版', () => {
    const bundled = tmp(); fakeInstall(bundled, '0.1.0-rc.6')
    const r = resolveDsh(join(tmp(), 'runtime', 'dsh'), bundled)
    expect(r.source).toBe('bundled')
    expect(r.version).toBe('0.1.0-rc.6')
    expect(r.binPath).toBe(join(bundled, 'node_modules', '@deepseek-ai', 'dsh', 'lib', 'bin.js'))
  })
  it('用户区多版本取最大', () => {
    const runtime = join(tmp(), 'runtime', 'dsh')
    for (const v of ['0.1.0-rc.6', '0.2.0']) { const d = join(runtime, v); fakeInstall(d, v); }
    const bundled = tmp(); fakeInstall(bundled, '0.1.0-rc.6')
    const r = resolveDsh(runtime, bundled)
    expect(r.source).toBe('user')
    expect(r.version).toBe('0.2.0')
  })
  it('用户区目录缺 bin.js 视为无效并回退', () => {
    const runtime = join(tmp(), 'runtime', 'dsh')
    mkdirSync(join(runtime, '0.9.0'), { recursive: true })
    const bundled = tmp(); fakeInstall(bundled, '0.1.0-rc.6')
    expect(resolveDsh(runtime, bundled).source).toBe('bundled')
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/main/dsh-resolver.test.ts`
Expected: FAIL

- [ ] **Step 3: 实现**

```ts
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

export interface DshResolution { binPath: string; version: string; source: 'user' | 'bundled' }

const binIn = (root: string) => join(root, 'node_modules', '@deepseek-ai', 'dsh', 'lib', 'bin.js')
const numeric = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true }) > 0

export function resolveDsh(runtimeDshDir: string, bundledDir: string): DshResolution {
  const valid = existsSync(runtimeDshDir)
    ? readdirSync(runtimeDshDir).filter(v => existsSync(binIn(join(runtimeDshDir, v))))
    : []
  if (valid.length > 0) {
    const latest = valid.reduce((m, v) => (numeric(v, m) ? v : m))
    return { binPath: binIn(join(runtimeDshDir, latest)), version: latest, source: 'user' }
  }
  const pkg = JSON.parse(readFileSync(join(bundledDir, 'node_modules', '@deepseek-ai', 'dsh', 'package.json'), 'utf8'))
  return { binPath: binIn(bundledDir), version: pkg.version, source: 'bundled' }
}
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/main/dsh-resolver.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/main/dsh-resolver.ts src/main/dsh-resolver.test.ts && git commit -m "feat(dsh-desktop): dsh 版本解析（用户区优先，回退内置）"
```

---

### Task 6: pnpm-runtime.ts（子进程环境与 pnpm shim）

**Files:**

- Create: `src/main/pnpm-runtime.ts`
- Test: `src/main/pnpm-runtime.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { buildChildEnv, ensurePnpmShim } from './pnpm-runtime.js'

const dirs: string[] = []
afterEach(() => { for (const d of dirs) rmSync(d, { recursive: true, force: true }); dirs.length = 0 })
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'pnpm-')); dirs.push(d); return d }

describe('buildChildEnv', () => {
  it('注入 DSH_HOME、PATH 前缀、默认 npmmirror、ELECTRON_RUN_AS_NODE', () => {
    const env = buildChildEnv({ binDir: 'B', dshHome: 'H' })
    expect(env.DSH_HOME).toBe('H')
    expect(env.PATH!.startsWith('B' + ';')).toBe(true)
    expect(env.npm_config_registry).toBe('https://registry.npmmirror.com')
    expect(env.ELECTRON_RUN_AS_NODE).toBe('1')
  })
  it('已有 npm_config_registry 时不覆盖', () => {
    const prev = process.env.npm_config_registry
    process.env.npm_config_registry = 'https://example.com'
    const env = buildChildEnv({ binDir: 'B', dshHome: 'H' })
    expect(env.npm_config_registry).toBe('https://example.com')
    if (prev === undefined) delete process.env.npm_config_registry; else process.env.npm_config_registry = prev
  })
})

describe('ensurePnpmShim', () => {
  it('生成 pnpm.cmd 且内容包含 execPath 与 pnpm 入口', () => {
    const dir = tmp()
    ensurePnpmShim(join(dir, 'bin'), 'C:\\app\\dsh.exe', 'C:\\app\\resources\\runtime-pnpm\\node_modules\\pnpm\\bin\\pnpm.mjs')
    const f = join(dir, 'bin', 'pnpm.cmd')
    expect(existsSync(f)).toBe(true)
    const c = readFileSync(f, 'utf8')
    expect(c).toContain('C:\\app\\dsh.exe')
    expect(c).toContain('pnpm.mjs')
    expect(c).toContain('ELECTRON_RUN_AS_NODE')
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/main/pnpm-runtime.test.ts`
Expected: FAIL

- [ ] **Step 3: 实现**

```ts
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

export interface ChildEnvPaths { binDir: string; dshHome: string }

export function buildChildEnv(paths: ChildEnvPaths, extra: NodeJS.ProcessEnv = {}): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env, ...extra }
  env.ELECTRON_RUN_AS_NODE = '1'
  env.DSH_HOME = paths.dshHome
  env.PATH = `${paths.binDir};${env.PATH ?? ''}`
  if (!process.env.npm_config_registry) env.npm_config_registry = 'https://registry.npmmirror.com'
  return env
}

export function ensurePnpmShim(binDir: string, execPath: string, pnpmJs: string): void {
  mkdirSync(binDir, { recursive: true })
  const cmd = [
    '@echo off',
    'set ELECTRON_RUN_AS_NODE=1',
    `"${execPath}" "${pnpmJs}" %*`,
    '',
  ].join('\r\n')
  writeFileSync(join(binDir, 'pnpm.cmd'), cmd, 'utf8')
}
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/main/pnpm-runtime.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/main/pnpm-runtime.ts src/main/pnpm-runtime.test.ts && git commit -m "feat(dsh-desktop): 子进程环境构建与捆绑 pnpm shim"
```

---

### Task 7: static-server.ts（静态项目服务）

**Files:**

- Create: `src/main/static-server.ts`
- Test: `src/main/static-server.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { startStaticServer } from './static-server.js'

const dirs: string[] = []
const servers: Array<{ close(): Promise<void> }> = []
afterEach(async () => {
  for (const s of servers) await s.close()
  servers.length = 0
  for (const d of dirs) rmSync(d, { recursive: true, force: true })
  dirs.length = 0
})
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'static-')); dirs.push(d); return d }

describe('startStaticServer', () => {
  it('服务根目录并返回随机端口', async () => {
    const dir = tmp()
    writeFileSync(join(dir, 'index.html'), '<h1>hi</h1>')
    const s = await startStaticServer(dir)
    servers.push(s)
    expect(s.port).toBeGreaterThan(0)
    const res = await fetch(`http://127.0.0.1:${s.port}/`)
    expect(res.status).toBe(200)
    expect(res.headers.get('content-type')).toContain('text/html')
    expect(await res.text()).toContain('hi')
  })
  it('.. 路径穿越被拒绝', async () => {
    const dir = tmp()
    writeFileSync(join(dir, 'index.html'), 'x')
    const s = await startStaticServer(dir)
    servers.push(s)
    const res = await fetch(`http://127.0.0.1:${s.port}/..%5c..%5cwindows%5cwin.ini`)
    expect([403, 400]).toContain(res.status)
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/main/static-server.test.ts`
Expected: FAIL

- [ ] **Step 3: 实现**

```ts
import { createReadStream, existsSync, statSync } from 'node:fs'
import http from 'node:http'
import { extname, join, normalize, sep } from 'node:path'

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript',
  '.mjs': 'text/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
}

export interface StaticServer { port: number; close(): Promise<void> }

export function startStaticServer(rootDir: string): Promise<StaticServer> {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => serve(rootDir, req, res))
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const port = (server.address() as { port: number }).port
      resolve({
        port,
        close: () => new Promise<void>(done => server.close(() => done())),
      })
    })
  })
}

function serve(rootDir: string, req: http.IncomingMessage, res: http.ServerResponse): void {
  if (req.method !== 'GET' && req.method !== 'HEAD') { res.writeHead(405).end(); return }
  const url = new URL(req.url ?? '/', 'http://x')
  let rel = decodeURIComponent(url.pathname)
  if (rel.endsWith('/')) rel += 'index.html'
  const full = normalize(join(rootDir, rel))
  if (!full.startsWith(normalize(rootDir) + sep)) { res.writeHead(403).end(); return }
  if (!existsSync(full) || !statSync(full).isFile()) { res.writeHead(404).end(); return }
  res.writeHead(200, { 'content-type': MIME[extname(full).toLowerCase()] ?? 'application/octet-stream' })
  createReadStream(full).pipe(res)
}
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/main/static-server.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/main/static-server.ts src/main/static-server.test.ts && git commit -m "feat(dsh-desktop): 静态项目内置服务"
```

---

### Task 8: projects.ts（项目清单扫描）

**Files:**

- Create: `src/main/projects.ts`
- Test: `src/main/projects.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { listProjects } from './projects.js'

const dirs: string[] = []
afterEach(() => { for (const d of dirs) rmSync(d, { recursive: true, force: true }); dirs.length = 0 })
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'proj-')); dirs.push(d); return d }

function mkProject(projectsDir: string, id: string, json: unknown, files: string[] = ['index.html']): void {
  const dir = join(projectsDir, id)
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, 'project.json'), typeof json === 'string' ? json : JSON.stringify(json))
  for (const f of files) writeFileSync(join(dir, f), 'x')
}

describe('listProjects', () => {
  it('目录不存在 → 空数组', () => {
    expect(listProjects(join(tmp(), 'nope'))).toEqual([])
  })
  it('合法静态项目被列出', () => {
    const dir = tmp()
    mkProject(dir, 'a', { name: 'A 工作台', icon: 'box', desc: 'd', entry: 'index.html' })
    const list = listProjects(dir)
    expect(list.length).toBe(1)
    expect(list[0].name).toBe('A 工作台')
    expect(list[0].start).toBeUndefined()
  })
  it('start 项目必须有 port，否则跳过', () => {
    const dir = tmp()
    mkProject(dir, 'ok', { name: 'S', entry: 'index.html', start: 'node server.js', port: 8801 })
    mkProject(dir, 'bad', { name: 'B', entry: 'index.html', start: 'node s.js' })
    const list = listProjects(dir)
    expect(list.length).toBe(1)
    expect(list[0].port).toBe(8801)
  })
  it('非法 JSON 与缺 name/entry 的目录被跳过', () => {
    const dir = tmp()
    mkProject(dir, 'broken', '{oops')
    mkProject(dir, 'noname', { entry: 'index.html' })
    expect(listProjects(dir)).toEqual([])
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/main/projects.test.ts`
Expected: FAIL

- [ ] **Step 3: 实现**

```ts
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

export interface Project {
  dir: string
  name: string
  icon: string
  desc: string
  entry: string
  start?: string
  port?: number
}

export function listProjects(projectsDir: string): Project[] {
  if (!existsSync(projectsDir)) return []
  const out: Project[] = []
  for (const ent of readdirSync(projectsDir, { withFileTypes: true })) {
    if (!ent.isDirectory()) continue
    const p = readProject(join(projectsDir, ent.name))
    if (p) out.push(p)
  }
  return out.sort((a, b) => a.name.localeCompare(b.name, 'zh'))
}

export function readProject(dir: string): Project | null {
  try {
    const raw = JSON.parse(readFileSync(join(dir, 'project.json'), 'utf8'))
    if (typeof raw.name !== 'string' || typeof raw.entry !== 'string') return null
    if (raw.start !== undefined) {
      if (typeof raw.start !== 'string' || typeof raw.port !== 'number') return null
    }
    return {
      dir,
      name: raw.name,
      icon: typeof raw.icon === 'string' ? raw.icon : 'apps',
      desc: typeof raw.desc === 'string' ? raw.desc : '',
      entry: raw.entry,
      start: raw.start,
      port: raw.port,
    }
  } catch { return null }
}
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/main/projects.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/main/projects.ts src/main/projects.test.ts && git commit -m "feat(dsh-desktop): project.json 清单扫描"
```

---

### Task 9: service-manager.ts（会话与项目进程编排）

**Files:**

- Create: `src/main/service-manager.ts`
- Test: `src/main/service-manager.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
import { EventEmitter } from 'node:events'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ServiceManager } from './service-manager.js'
import type { Project } from './projects.js'

const dirs: string[] = []
afterEach(() => { for (const d of dirs) rmSync(d, { recursive: true, force: true }); dirs.length = 0 })
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'sm-')); dirs.push(d); return d }

function fakeChild(): any {
  const c = new EventEmitter() as any
  c.pid = 4242
  return c
}

function makeManager(probeResult: () => Promise<string>) {
  const states: string[] = []
  const kill = vi.fn()
  const child = fakeChild()
  const mgr = new ServiceManager({
    sessionPort: 39999,
    probe: probeResult as any,
    spawnChild: () => child,
    treeKill: (pid) => kill(pid),
    staticServe: async () => 45678,
    log: () => {},
    onState: (s) => states.push(s),
    startTimeoutMs: 3000,
    pollIntervalMs: 50,
  })
  return { mgr, states, child, kill }
}

describe('ensureSession', () => {
  it('探测到 dsh → attached，不 spawn', async () => {
    const { mgr, states } = makeManager(async () => 'dsh')
    const r = await mgr.ensureSession('bin.js', {})
    expect(r.mode).toBe('attached')
    expect(states).toContain('attached')
  })
  it('none → spawn 并等待就绪 → started', async () => {
    let call = 0
    const { mgr, states } = makeManager(async () => (call++ === 0 ? 'none' : 'dsh'))
    const r = await mgr.ensureSession('bin.js', {})
    expect(r.mode).toBe('started')
    expect(states).toContain('ready')
  })
  it('foreign → PORT_CONFLICT', async () => {
    const { mgr } = makeManager(async () => 'foreign')
    await expect(mgr.ensureSession('bin.js', {})).rejects.toThrow('PORT_CONFLICT')
  })
  it('子进程退出 → error-crashed 状态', async () => {
    let call = 0
    const { mgr, child, states } = makeManager(async () => (call++ === 0 ? 'none' : 'dsh'))
    await mgr.ensureSession('bin.js', {})
    child.emit('exit')
    expect(states).toContain('error-crashed')
  })
})

describe('ensureProject', () => {
  it('静态项目 → 静态服务端口 URL', async () => {
    const { mgr } = makeManager(async () => 'none')
    const p: Project = { dir: tmp(), name: 'S', icon: 'a', desc: '', entry: 'index.html' }
    writeFileSync(join(p.dir, 'index.html'), 'x')
    const url = await mgr.ensureProject(p, {})
    expect(url).toBe('http://127.0.0.1:45678/index.html')
  })
  it('start 项目 → spawn 并等端口就绪', async () => {
    let call = 0
    const { mgr } = makeManager(async () => (call++ === 0 ? 'none' : 'dsh'))
    const p: Project = { dir: tmp(), name: 'T', icon: 'a', desc: '', entry: 'index.html', start: 'node s.js', port: 8899 }
    const url = await mgr.ensureProject(p, {})
    expect(url).toBe('http://127.0.0.1:8899/index.html')
  })
  it('重复进入静态项目幂等（同端口）', async () => {
    const { mgr } = makeManager(async () => 'none')
    const p: Project = { dir: tmp(), name: 'S2', icon: 'a', desc: '', entry: 'index.html' }
    const a = await mgr.ensureProject(p, {})
    const b = await mgr.ensureProject(p, {})
    expect(a).toBe(b)
  })
})

describe('shutdownAll', () => {
  it('杀掉自己启动的子进程', async () => {
    let call = 0
    const { mgr, kill } = makeManager(async () => (call++ === 0 ? 'none' : 'dsh'))
    await mgr.ensureSession('bin.js', {})
    await mgr.shutdownAll()
    expect(kill).toHaveBeenCalledWith(4242)
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/main/service-manager.test.ts`
Expected: FAIL

- [ ] **Step 3: 实现**

```ts
import type { ChildProcess } from 'node:child_process'
import type { Project } from './projects.js'
import type { ProbeResult } from './port-probe.js'

export type SessionState = 'idle' | 'checking' | 'attached' | 'starting' | 'ready' | 'error-port' | 'error-crashed'

export interface ServiceDeps {
  sessionPort: number
  probe: (port: number) => Promise<ProbeResult>
  spawnChild: (cmd: string, args: string[], cwd: string, env: NodeJS.ProcessEnv) => ChildProcess
  treeKill: (pid: number) => void
  staticServe: (dir: string) => Promise<number>
  log: (line: string) => void
  onState: (state: SessionState) => void
  startTimeoutMs: number
  pollIntervalMs: number
}

interface ChildEntry { child: ChildProcess; kind: 'dsh' | 'project' }

export class ServiceManager {
  private children = new Map<string, ChildEntry>()
  private staticPorts = new Map<string, number>()

  constructor(private deps: ServiceDeps) {}

  async ensureSession(binPath: string, env: NodeJS.ProcessEnv): Promise<{ mode: 'attached' | 'started'; url: string }> {
    const { sessionPort: port, probe } = this.deps
    const url = `http://127.0.0.1:${port}/`
    if (this.children.has('session')) return { mode: 'started', url }
    this.deps.onState('checking')
    const r = await probe(port)
    if (r === 'dsh') { this.deps.onState('attached'); return { mode: 'attached', url } }
    if (r === 'foreign') { this.deps.onState('error-port'); throw new Error('PORT_CONFLICT') }
    this.deps.onState('starting')
    const child = this.deps.spawnChild(process.execPath, [binPath, 'web'], process.cwd(), env)
    this.track('session', { child, kind: 'dsh' })
    await this.waitReady(port)
    this.deps.onState('ready')
    return { mode: 'started', url }
  }

  async ensureProject(project: Project, env: NodeJS.ProcessEnv): Promise<string> {
    const key = `project:${project.name}`
    if (this.staticPorts.has(key)) return this.projectUrl(key, project.entry)
    const entry = this.children.get(key)
    if (entry) return this.projectUrl(key, project.entry)
    if (project.start && project.port) {
      const child = this.deps.spawnChild(project.start, [], project.dir, env)
      this.track(key, { child, kind: 'project' })
      await this.waitPort(project.port)
      this.staticPorts.set(key, project.port)
      return this.projectUrl(key, project.entry)
    }
    const port = await this.deps.staticServe(project.dir)
    this.staticPorts.set(key, port)
    return this.projectUrl(key, project.entry)
  }

  private projectUrl(key: string, entry: string): string {
    return `http://127.0.0.1:${this.staticPorts.get(key)}/${entry.replace(/^\/+/, '')}`
  }

  private track(key: string, entry: ChildEntry): void {
    this.children.set(key, entry)
    entry.child.once('exit', (code) => {
      if (this.children.get(key)?.child === entry.child) {
        this.children.delete(key)
        this.staticPorts.delete(key)
        this.deps.log(`child exit key=${key} code=${code}`)
        if (key === 'session') this.deps.onState('error-crashed')
      }
    })
  }

  private async waitReady(port: number): Promise<void> {
    await this.waitPort(port, (r) => r === 'dsh')
  }

  private async waitPort(port: number, accept: (r: ProbeResult) => boolean = (r) => r !== 'none'): Promise<void> {
    const deadline = Date.now() + this.deps.startTimeoutMs
    while (Date.now() < deadline) {
      if (accept(await this.deps.probe(port))) return
      await new Promise(r => setTimeout(r, this.deps.pollIntervalMs))
    }
    throw new Error('START_TIMEOUT')
  }

  async shutdownAll(): Promise<void> {
    for (const [key, entry] of this.children) {
      try { if (entry.child.pid) this.deps.treeKill(entry.child.pid) } catch (e) { this.deps.log(`kill fail ${key}: ${e}`) }
      this.children.delete(key)
    }
    this.staticPorts.clear()
  }
}
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/main/service-manager.test.ts`
Expected: PASS 全绿

- [ ] **Step 5: Commit**

```bash
git add src/main/service-manager.ts src/main/service-manager.test.ts && git commit -m "feat(dsh-desktop): 会话/项目进程编排（附加-启动-清理）"
```

---

### Task 10: updater.ts（dsh 检查更新与安装用户区版本）

**Files:**

- Create: `src/main/updater.ts`
- Test: `src/main/updater.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchLatest, installUserVersion } from './updater.js'

const dirs: string[] = []
afterEach(() => { for (const d of dirs) rmSync(d, { recursive: true, force: true }); dirs.length = 0; vi.restoreAllMocks() })
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'upd-')); dirs.push(d); return d }

describe('fetchLatest', () => {
  it('npmmirror 成功 → 返回版本与 tarball', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({ version: '0.2.0', dist: { tarball: 'https://r/t.tgz' } }),
    })))
    const info = await fetchLatest()
    expect(info).toEqual({ version: '0.2.0', tarball: 'https://r/t.tgz' })
  })
  it('npmmirror 失败回退 npmjs', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ version: '0.3.0', dist: { tarball: 'https://n/t.tgz' } }) })
    vi.stubGlobal('fetch', fetchMock)
    expect((await fetchLatest()).version).toBe('0.3.0')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
  it('全部失败 → REGISTRY_UNREACHABLE', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false })))
    await expect(fetchLatest()).rejects.toThrow('REGISTRY_UNREACHABLE')
  })
})

describe('installUserVersion', () => {
  it('调用 pnpm add 并产出目录', async () => {
    const runtime = join(tmp(), 'runtime', 'dsh')
    const run = vi.fn((args: string[], cwd: string) => {
      expect(args[0]).toBe('add')
      expect(args[1]).toBe('@deepseek-ai/dsh@0.2.0')
      expect(cwd.startsWith(runtime)).toBe(true)
      return { status: 0 }
    })
    const dest = await installUserVersion(runtime, '0.2.0', { runPnpm: run })
    expect(existsSync(dest)).toBe(true)
  })
  it('pnpm 失败 → INSTALL_FAILED 且清理目录', async () => {
    const runtime = join(tmp(), 'runtime', 'dsh')
    await expect(installUserVersion(runtime, '0.2.0', { runPnpm: () => ({ status: 1 }) }))
      .rejects.toThrow('INSTALL_FAILED')
  })
  it('已存在则幂等返回', async () => {
    const runtime = join(tmp(), 'runtime', 'dsh')
    const run = vi.fn(() => ({ status: 0 }))
    await installUserVersion(runtime, '0.2.0', { runPnpm: run })
    await installUserVersion(runtime, '0.2.0', { runPnpm: run })
    expect(run).toHaveBeenCalledTimes(1)
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/main/updater.test.ts`
Expected: FAIL

- [ ] **Step 3: 实现**

```ts
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const REGISTRIES = ['https://registry.npmmirror.com', 'https://registry.npmjs.org']

export interface LatestInfo { version: string; tarball: string }

export async function fetchLatest(): Promise<LatestInfo> {
  for (const reg of REGISTRIES) {
    try {
      const res = await fetch(`${reg}/@deepseek-ai/dsh/latest`)
      if (!res.ok) continue
      const j = await res.json() as { version: string; dist: { tarball: string } }
      return { version: j.version, tarball: j.dist.tarball }
    } catch { /* try next registry */ }
  }
  throw new Error('REGISTRY_UNREACHABLE')
}

export interface InstallOpts { runPnpm?: (args: string[], cwd: string) => { status: number | null } }

export async function installUserVersion(runtimeDshDir: string, version: string, opts: InstallOpts = {}): Promise<string> {
  const dest = join(runtimeDshDir, version)
  if (existsSync(join(dest, 'node_modules', '@deepseek-ai', 'dsh'))) return dest
  mkdirSync(dest, { recursive: true })
  writeFileSync(join(dest, 'package.json'), JSON.stringify({ name: 'dsh-user', private: true }, null, 2))
  const runPnpm = opts.runPnpm ?? ((args: string[], cwd: string) => {
    const r = spawnSync('cmd.exe', ['/c', 'pnpm.cmd', ...args], { cwd, stdio: 'pipe' })
    return { status: r.status }
  })
  const result = runPnpm(['add', `@deepseek-ai/dsh@${version}`], dest)
  if (result.status !== 0) {
    rmSync(dest, { recursive: true, force: true })
    throw new Error('INSTALL_FAILED')
  }
  return dest
}
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/main/updater.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/main/updater.ts src/main/updater.test.ts && git commit -m "feat(dsh-desktop): dsh 检查更新与用户区安装"
```

---

### Task 11: preload + 渲染层页面（home / splash / error / 顶栏）

**Files:**

- Create: `src/preload.ts`, `src/renderer/home.html`, `src/renderer/splash.html`, `src/renderer/error.html`, `src/renderer/bar.html`

- [ ] **Step 1: preload.ts**

```ts
import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('dsh', {
  listProjects: () => ipcRenderer.invoke('projects:list'),
  open: (target: string) => ipcRenderer.invoke('open', target),
  retry: () => ipcRenderer.invoke('retry'),
  state: () => ipcRenderer.invoke('state'),
})
```

- [ ] **Step 2: home.html（项目主页）**

```html
<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>DeepSeek Harness</title>
<style>
  body { font-family: system-ui, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 32px; }
  h1 { font-size: 20px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; margin-top: 24px; }
  .card { background: #1e293b; border-radius: 12px; padding: 20px; cursor: pointer; border: 1px solid #334155; }
  .card:hover { border-color: #64748b; }
  .card .icon { font-size: 28px; }
  .card .name { font-size: 16px; font-weight: 600; margin: 10px 0 4px; }
  .card .desc { font-size: 12px; color: #94a3b8; }
  .card.pinned { border-color: #3b82f6; }
  .err { color: #f87171; font-size: 13px; }
</style>
</head>
<body>
  <h1>DeepSeek Harness</h1>
  <div class="grid" id="cards"></div>
  <p class="err" id="err"></p>
  <script>
    async function load() {
      const el = document.getElementById('cards')
      el.innerHTML = ''
      const session = document.createElement('div')
      session.className = 'card pinned'
      session.innerHTML = '<div class="icon">💬</div><div class="name">AI 会话</div><div class="desc">标准 dsh Web UI</div>'
      session.onclick = async () => { const r = await window.dsh.open('session'); if (!r.ok) document.getElementById('err').textContent = r.error }
      el.appendChild(session)
      const projects = await window.dsh.listProjects()
      for (const p of projects) {
        const c = document.createElement('div')
        c.className = 'card'
        c.innerHTML = `<div class="icon">📦</div><div class="name"></div><div class="desc"></div>`
        c.querySelector('.name').textContent = p.name
        c.querySelector('.desc').textContent = p.desc
        c.onclick = async () => { const r = await window.dsh.open('project:' + p.name); if (!r.ok) document.getElementById('err').textContent = r.error }
        el.appendChild(c)
      }
    }
    load()
    setInterval(load, 5000)
  </script>
</body>
</html>
```

- [ ] **Step 3: splash.html / error.html / bar.html**

`splash.html`：

```html
<!doctype html>
<html lang="zh">
<head><meta charset="utf-8"><title>启动中</title>
<style>body{font-family:system-ui;background:#0f172a;color:#e2e8f0;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}.s{font-size:15px}</style>
</head>
<body><p class="s" id="msg">正在启动 DeepSeek Harness…</p></body>
</html>
```

`error.html`（参数经 query 传入 `?msg=`、`?log=`）：

```html
<!doctype html>
<html lang="zh">
<head><meta charset="utf-8"><title>出错了</title>
<style>body{font-family:system-ui;background:#0f172a;color:#e2e8f0;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;margin:0;gap:12px}button{padding:8px 24px}</style>
</head>
<body>
  <p id="msg"></p>
  <p id="log" style="color:#94a3b8;font-size:12px"></p>
  <button onclick="location.href='home://back'">⟵ 返回项目</button>
  <button onclick="window.dsh.retry()">重试</button>
  <script>
    const q = new URLSearchParams(location.search)
    document.getElementById('msg').textContent = q.get('msg') ?? '发生错误'
    document.getElementById('log').textContent = q.get('log') ?? ''
  </script>
</body>
</html>
```

`bar.html`（内容区顶栏，返回主页）：

```html
<!doctype html>
<html lang="zh">
<head><meta charset="utf-8"><title></title>
<style>html,body{margin:0;height:100%;background:#1e293b;-webkit-app-region:drag}button{-webkit-app-region:no-drag;margin:4px 8px;padding:2px 12px;font-size:12px}</style>
</head>
<body><button onclick="window.dsh.open('home')">⟵ 项目</button></body>
</html>
```

- [ ] **Step 4: 构建验证**

Run: `npm run build`
Expected: dist/ 出现 main.js、preload.js、renderer/（renderer 引用 window.dsh，纯静态无类型检查）

- [ ] **Step 5: Commit**

```bash
git add src/preload.ts src/renderer && git commit -m "feat(dsh-desktop): preload 与渲染层静态页"
```

---

### Task 12: index.ts + tray.ts（装配）

**Files:**

- Create: `src/main/index.ts`, `src/main/tray.ts`

- [ ] **Step 1: tray.ts**

```ts
import { Menu, Tray, app } from 'electron'
import { join } from 'node:path'

export function createTray(onOpenHome: () => void, onCheckUpdate: () => void, onQuit: () => void): Tray {
  const icon = join(process.resourcesPath, 'icon.ico')
  const tray = new Tray(icon)
  tray.setToolTip('DeepSeek Harness')
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '打开主页', click: onOpenHome },
    { label: '检查更新', click: onCheckUpdate },
    { type: 'separator' },
    { label: '退出', click: onQuit },
  ]))
  return tray
}
```

图标问题：先放一个占位 icon。运行 `npm run fetch` 前手动放置：从 Electron 自带图标取一份（`node_modules/electron/dist/electron.ico` 不存在时用 builder 默认）——若不存在则 tray 用 `app.getFileIcon(process.execPath)` 兜底。实现时：`new Tray(tryIcon())`，`tryIcon()` 返回存在的 icon 路径或 `process.execPath` 的 fileIcon。简化实现：

```ts
import { app, nativeImage, Tray } from 'electron'

export function createTray(onOpenHome: () => void, onCheckUpdate: () => void, onQuit: () => void): Tray {
  const tray = new Tray(nativeImage.createFromPath('').isEmpty()
    ? nativeImage.createFromBuffer(require('electron').app.getFileIcon(process.execPath, { size: 32 }).toPNG())
    : nativeImage.createFromPath(''))
  // 上面两行为示意；实际实现直接使用 app.getFileIcon：
  const img = app.getFileIcon(process.execPath, { size: 32 })
  const tray2 = new Tray(img)
  return tray2
}
```

**以如下最终版为准**（`tray.ts` 完整内容）：

```ts
import { app, Menu, Tray } from 'electron'

export function createTray(onOpenHome: () => void, onCheckUpdate: () => void, onQuit: () => void): Tray {
  const tray = new Tray(app.getFileIcon(process.execPath, { size: 32 }))
  tray.setToolTip('DeepSeek Harness')
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '打开主页', click: onOpenHome },
    { label: '检查更新', click: onCheckUpdate },
    { type: 'separator' },
    { label: '退出', click: onQuit },
  ]))
  return tray
}
```

- [ ] **Step 2: index.ts**

```ts
import { app, BrowserWindow, dialog, ipcMain, WebContentsView } from 'electron'
import { spawn } from 'node:child_process'
import { join } from 'node:path'
import { appPaths, dirWritable, loadSettings, resolveDataRoot, saveSettings } from './paths.js'
import { createLogger } from './logger.js'
import { probe } from './port-probe.js'
import { resolveDsh } from './dsh-resolver.js'
import { buildChildEnv, ensurePnpmShim } from './pnpm-runtime.js'
import { listProjects, type Project } from './projects.js'
import { startStaticServer } from './static-server.js'
import { ServiceManager, type SessionState } from './service-manager.js'
import { fetchLatest, installUserVersion } from './updater.js'
import { createTray } from './tray.js'

const isDev = !app.isPackaged
const baseDir = isDev ? process.cwd() : process.resourcesPath
const bundledDshDir = join(baseDir, 'resources', 'dsh')
const bundledPnpmJs = join(baseDir, 'resources', 'runtime-pnpm', 'node_modules', 'pnpm', 'bin', 'pnpm.mjs')

let win: BrowserWindow | null = null
let contentView: WebContentsView | null = null
let state: SessionState = 'idle'

const settings = loadSettings(join(resolveDataRoot(), 'settings.json'))
const dataRoot = resolveDataRoot(settings.dataRoot || undefined, () => dirWritable('D:\\'))
saveSettings(join(dataRoot, 'settings.json'), { ...settings, dataRoot })
const paths = appPaths(dataRoot)
const mainLog = createLogger(paths.logsDir, 'main', 7)
const dshLog = createLogger(paths.logsDir, 'dsh', 7)

const mgr = new ServiceManager({
  sessionPort: 3080,
  probe: (p) => probe(p),
  spawnChild: (cmd, args, cwd, env) => {
    mainLog(`spawn ${cmd} ${args.join(' ')} (cwd=${cwd})`)
    const child = cmd === process.execPath
      ? spawn(cmd, args, { env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true })
      : spawn(cmd, { shell: true, cwd, env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true })
    child.stdout?.on('data', (d) => dshLog(String(d).trim()))
    child.stderr?.on('data', (d) => dshLog(`[err] ${String(d).trim()}`))
    return child
  },
  treeKill: (pid) => spawn('taskkill', ['/pid', String(pid), '/T', '/F']),
  staticServe: async (dir) => (await startStaticServer(dir)).port,
  log: (l) => mainLog(l),
  onState: (s) => { state = s },
  startTimeoutMs: 30000,
  pollIntervalMs: 500,
})

function childEnv() {
  ensurePnpmShim(paths.binDir, process.execPath, bundledPnpmJs)
  return buildChildEnv({ binDir: paths.binDir, dshHome: paths.dshHome })
}

function createWindow(): void {
  win = new BrowserWindow({ width: 1280, height: 840, show: false })
  const bar = new WebContentsView({})
  bar.webContents.loadFile(join(baseDir, 'dist', 'renderer', 'bar.html'))
  win.contentView.addChildView(bar)
  contentView = new WebContentsView({})
  win.contentView.addChildView(contentView)
  const layout = () => {
    const [w, h] = win!.getContentSize()
    bar.setBounds({ x: 0, y: 0, width: w, height: 32 })
    contentView!.setBounds({ x: 0, y: 32, width: w, height: h - 32 })
  }
  win.on('resize', layout)
  layout()
  win.once('ready-to-show', () => win?.show())
  showHome()
}

function loadInContent(url: string): void {
  contentView?.webContents.loadURL(url)
}

function showHome(): void {
  loadInContent(`file://${join(baseDir, 'dist', 'renderer', 'home.html').replace(/\\/g, '/')}`)
}

function showError(msg: string): void {
  const u = `file://${join(baseDir, 'dist', 'renderer', 'error.html').replace(/\\/g, '/')}?msg=${encodeURIComponent(msg)}&log=${encodeURIComponent(join(paths.logsDir, 'dsh-*.log'))}`
  loadInContent(u)
}

ipcMain.handle('projects:list', () => listProjects(paths.projectsDir).map(p => ({ name: p.name, desc: p.desc, icon: p.icon })))
ipcMain.handle('open', async (_e, target: string) => {
  try {
    if (target === 'home') { showHome(); return { ok: true } }
    if (target === 'session') {
      const dsh = resolveDsh(paths.runtimeDshDir, bundledDshDir)
      const r = await mgr.ensureSession(dsh.binPath, childEnv())
      loadInContent(r.url)
      return { ok: true }
    }
    if (target.startsWith('project:')) {
      const name = target.slice('project:'.length)
      const p = listProjects(paths.projectsDir).find(x => x.name === name)
      if (!p) return { ok: false, error: 'PROJECT_NOT_FOUND' }
      if (p.start) {
        const ok = dialog.showMessageBoxSync(win!, {
          type: 'question', buttons: ['取消', '启动'],
          message: `首次启动项目「${p.name}」`, detail: '该项目将在本机执行启动命令，仅启动你信任的项目。',
        }) === 1
        if (!ok) return { ok: false, error: 'CANCELLED' }
      }
      const url = await mgr.ensureProject(p, childEnv())
      loadInContent(url)
      return { ok: true }
    }
    return { ok: false, error: 'BAD_TARGET' }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    if (msg === 'PORT_CONFLICT') showError('端口 3080 被其他程序占用，请关闭后重试')
    else if (msg === 'START_TIMEOUT') showError('服务启动超时（30s）')
    else showError(`启动失败：${msg}`)
    return { ok: false, error: msg }
  }
})
ipcMain.handle('retry', () => { showHome(); return { ok: true } })
ipcMain.handle('state', () => state)

async function checkUpdate(): Promise<void> {
  try {
    const latest = await fetchLatest()
    const cur = resolveDsh(paths.runtimeDshDir, bundledDshDir)
    if (latest.version === cur.version) { dialog.showMessageBox(win!, { message: `已是最新版本 ${cur.version}` }); return }
    const yes = dialog.showMessageBoxSync(win!, {
      type: 'question', buttons: ['取消', '升级'],
      message: `发现新版本 ${latest.version}（当前 ${cur.version}）`,
    }) === 1
    if (!yes) return
    await installUserVersion(paths.runtimeDshDir, latest.version)
    saveSettings(join(dataRoot, 'settings.json'), { ...settings, dataRoot, dshVersion: latest.version })
    dialog.showMessageBox(win!, { message: `已安装 ${latest.version}，返回主页重新进入生效` })
    showHome()
  } catch (e) {
    dialog.showErrorBox('检查更新失败', e instanceof Error ? e.message : String(e))
  }
}

if (!app.requestSingleInstanceLock()) app.quit()
else {
  app.on('second-instance', () => { win?.show(); win?.focus() })
  app.whenReady().then(() => {
    createTray(showHome, checkUpdate, async () => { await mgr.shutdownAll(); app.quit() })
    createWindow()
  })
  app.on('window-all-closed', () => { /* 托盘常驻 */ })
}
```

注意：Task 9 的 `ensureSession(binPath, env)` 签名与此处调用一致；`ensureProject(project, env)` 同。

- [ ] **Step 3: 类型检查 + 构建**

Run: `npx tsc --noEmit && npm run build`
Expected: 0 错误（electron 类型来自 devDep）

- [ ] **Step 4: Commit**

```bash
git add src/main/index.ts src/main/tray.ts && git commit -m "feat(dsh-desktop): 主进程装配（窗口/顶栏/IPC/托盘/升级）"
```

---

### Task 13: scripts/fetch-dsh.mjs（vendor dsh+pnpm 全树）

**Files:**

- Create: `scripts/fetch-dsh.mjs`
- Modify: `.gitignore`（项目级）

- [ ] **Step 1: 写脚本**

```js
import { cpSync, execSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const DSH_VER = process.env.DSH_VERSION ?? '0.1.0-rc.6'
const PNPM_VER = process.env.PNPM_VERSION ?? '11.22.0'
const REG = process.env.NPM_REGISTRY ?? 'https://registry.npmmirror.com'

rmSync('build', { recursive: true, force: true })
rmSync('resources', { recursive: true, force: true })

async function vendor(pkg, outDir) {
  const stage = join('build', outDir.replaceAll(/[\\/]/g, '-'))
  mkdirSync(stage, { recursive: true })
  writeFileSync(join(stage, 'package.json'), JSON.stringify({ name: 'stage', private: true }))
  execSync(`npm install ${pkg} --omit=dev --registry ${REG} --no-audit --no-fund --ignore-scripts`, { cwd: stage, stdio: 'inherit' })
  mkdirSync(outDir, { recursive: true })
  cpSync(join(stage, 'node_modules'), join(outDir, 'node_modules'), { recursive: true })
  console.log(`vendored ${pkg} -> ${outDir}`)
}

await vendor(`@deepseek-ai/dsh@${DSH_VER}`, join('resources', 'dsh'))
await vendor(`pnpm@${PNPM_VER}`, join('resources', 'runtime-pnpm'))
```

`dsh-desktop/.gitignore` 追加：

```text
build/
resources/
release/
dist/
```

（根仓库 `.gitignore` 已有 `node_modules/` 与 `dist/`，此处覆盖 dsh-desktop 内部产物。）

- [ ] **Step 2: 运行验证**

Run: `npm run fetch && ls resources/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js resources/runtime-pnpm/node_modules/pnpm/bin/pnpm.mjs`
Expected: 两个文件都存在（下载需几分钟，约 255+ 包）

- [ ] **Step 3: Commit**

```bash
git add scripts/fetch-dsh.mjs .gitignore && git commit -m "build(dsh-desktop): vendor dsh+pnpm 全依赖树脚本"
```

---

### Task 14: fixture 项目

**Files:**

- Create: `fixtures/projects/hello-static/project.json`, `fixtures/projects/hello-static/index.html`
- Create: `fixtures/projects/hello-start/project.json`, `fixtures/projects/hello-start/index.html`, `fixtures/projects/hello-start/server.js`

- [ ] **Step 1: hello-static**

`project.json`：

```json
{ "name": "Hello 静态项目", "icon": "doc", "desc": "静态型 fixture", "entry": "index.html" }
```

`index.html`：

```html
<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>Hello Static</title></head>
<body style="font-family:system-ui;padding:40px"><h1>你好，静态项目</h1><p>由客户端内置静态服务加载。</p></body></html>
```

- [ ] **Step 2: hello-start**

`project.json`：

```json
{ "name": "Hello Start 项目", "icon": "bolt", "desc": "start 型 fixture", "entry": "index.html", "start": "node server.js", "port": 8801 }
```

`server.js`：

```js
import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const dir = dirname(fileURLToPath(import.meta.url))
createServer(async (_req, res) => {
  res.setHeader('content-type', 'text/html; charset=utf-8')
  res.end(await readFile(join(dir, 'index.html'), 'utf8'))
}).listen(8801, '127.0.0.1', () => console.log('hello-start on 8801'))
```

`index.html`：

```html
<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>Hello Start</title></head>
<body style="font-family:system-ui;padding:40px"><h1>你好，start 项目</h1><p>由捆绑 Node 运行 server.js 提供服务。</p></body></html>
```

- [ ] **Step 3: 本机端到端验证（开发模式）**

准备数据目录并把 fixture 拷进去：

```bash
mkdir -p "$LOCALAPPDATA/tmp-verify" 2>/dev/null; cp -r fixtures/projects /d/DeepSeekHarness/projects-fixture-backup 2>/dev/null || true
mkdir -p /d/DeepSeekHarness/projects && cp -r fixtures/projects/hello-static fixtures/projects/hello-start /d/DeepSeekHarness/projects/
npm run start
```

手动检查清单（记录结果）：
1. 主页出现「AI 会话」+ 2 张项目卡
2. 点「Hello 静态项目」→ 顶栏下方显示「你好，静态项目」
3. 点顶栏「⟵ 项目」回主页
4. 点「Hello Start 项目」→ 确认框 → 显示「你好，start 项目」；`netstat -ano | grep 8801` 有监听
5. 点「AI 会话」→ dsh Web UI 出现（首次约 10–30s）
6. 托盘「退出」后 `netstat -ano | grep -E "3080|8801"` 无残留（若 3080 本来就有外部实例则应保留）

- [ ] **Step 4: Commit**

```bash
git add fixtures && git commit -m "test(dsh-desktop): 静态/start 双 fixture 项目"
```

---

### Task 15: 打包与干净机验收

**Files:**

- Modify: `package.json`（无改动则跳过）

- [ ] **Step 1: 打 NSIS 安装包**

Run: `npm run dist`
Expected: `release/` 生成 `DeepSeek Harness Setup 0.1.0.exe`（约 120–200MB）

- [ ] **Step 2: 本机安装验收（模拟干净环境）**

安装前先把 `D:\DeepSeekHarness` 改名（模拟首装），安装 Setup.exe，启动后按 Task 14 Step 3 的清单复验。
验收后恢复目录或保留新目录（用户数据）。

- [ ] **Step 3: 干净虚拟机验收（分发的硬指标）**

在一台无 Node.js 的 Windows 虚拟机：安装 → 双击 → 主页 → 进「AI 会话」→ 配置 DeepSeek API Key → 发一条消息 → 「社区插件」页可打开。结果记录到本文件末尾。

- [ ] **Step 4: 最终提交与 tag**

```bash
git add -A && git commit -m "chore(dsh-desktop): v0.1.0 打包验收" && git tag dsh-desktop-v0.1.0
```

---

## Self-Review 结论

- **Spec 覆盖**：§1 五个目标（检测/一键安装/启动附加/插件市场/项目卡片）→ Task 4/12/13（检测）、1/13/15（安装）、9/12（启动附加）、6/13（市场运行时）、7/8/9/11/12/14（项目）。§5 升级 → Task 10/12。§6 → Task 6/13。§7 → Task 2/7/8/9/14。§8 错误 → Task 9/12。§9 测试 → 各任务 TDD + Task 14/15。§10 结构 → 全部文件对应。§11 风险缓解（端口冲突探测、start 确认框、D 盘回退）分别落在 Task 4/12/2。
- **占位符扫描**：Task 12 Step 1 中间出现的示意代码已用「以最终版为准」块消除歧义，执行者只写最终版。
- **类型一致性**：`ensureSession(binPath, env)` / `ensureProject(project, env)` / `probe(port)` / `listProjects(projectsDir)` / `buildChildEnv({binDir, dshHome})` 在 Task 9/12 与定义处一致；`Project` 字段与 Task 8 一致。
