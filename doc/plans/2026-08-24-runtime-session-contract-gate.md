# Runtime Session 契约门禁实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在候选 Runtime 归档和发布前，使用真实候选进程与确定性模型验证 Workspace、Session 同步 binding、prompt-before-open 和事件投影契约。

**Architecture:** 纯执行器负责阶段、超时和错误归一化；候选驱动从组装后的 `app/node_modules` 动态加载固定 DSH 客户端公开实现，并连接真实 loopback Runtime；命令行入口负责临时目录、确定性模型、进程和 JSON 报告。`build-runtime.mjs` 在生成归档前调用门禁，上游同步在创建 tag 前构建并验证一个支持平台候选。

**Tech Stack:** Node.js ESM、Vitest、Cordis Client Runtime、DeepSeek Harness loopback API、GitHub Actions。

---

## 文件结构与职责

- 新建 `scripts/runtime-session-contract.mjs`
  - 定义阶段、错误类别、超时包装和可注入驱动的契约执行器。
- 新建 `scripts/runtime-session-contract.d.mts`
  - 暴露执行器、驱动和报告的 TypeScript 类型。
- 新建 `scripts/runtime-session-contract.test.ts`
  - 使用可编程假驱动锁定顺序、失败分类和清理语义。
- 新建 `scripts/runtime-session-contract-client.mjs`
  - 从候选 Runtime 加载 Client bundle，建立 Cordis Session/Workspace 公开服务并执行真实客户端操作。
- 新建 `scripts/runtime-session-contract-client.d.mts`
  - 声明候选客户端驱动的公开参数。
- 新建 `scripts/runtime-session-contract-client.test.ts`
  - 锁定候选 bundle 来源、安全根目录和同步 binding 行为。
- 新建 `scripts/run-runtime-session-contract.mjs`
  - 启动确定性模型和候选 Runtime，输出脱敏 JSON 报告并设置退出码。
- 修改 `scripts/build-runtime.mjs`
  - 在 `materializeRuntimeLinks` 后、归档前调用门禁。
- 修改 `package.json`
  - 增加 `runtime:session-contract` 命令。
- 修改 `.github/workflows/upstream-sync.yml`
  - 在创建升级 tag 前构建并验证 macOS 候选 Runtime。
- 修改 `scripts/workflow-contract.test.ts`
  - 锁定上游同步和正式 Runtime 构建都会执行门禁。
- 修改 `doc/README.md`
  - 标记 P0.1 Session Contract fixture 已完成并把下一开发入口推进到 P0.2。

## Task 1：实现纯契约执行器

**Files:**

- Create: `scripts/runtime-session-contract.mjs`
- Create: `scripts/runtime-session-contract.d.mts`
- Create: `scripts/runtime-session-contract.test.ts`

- [x] **Step 1：先写调用顺序与成功报告失败测试**

测试构造记录调用的驱动，并断言：

```ts
expect(calls).toEqual([
  'runtime-start',
  'runtime-ready',
  'workspace-create',
  'session-create',
  'session-binding',
  'session-prompt',
  'session-open',
  'session-event',
  'session-close',
  'cleanup',
])
expect(result).toMatchObject({ ok: true, failedStage: undefined })
```

驱动接口固定为：

```ts
export interface RuntimeSessionContractDriver {
  start(): Promise<void>
  ready(): Promise<void>
  createWorkspace(): Promise<string>
  createSession(workspaceId: string): Promise<string>
  requireBinding(sessionId: string): Promise<void>
  prompt(sessionId: string): Promise<void>
  open(sessionId: string): Promise<void>
  waitForEvents(sessionId: string): Promise<void>
  closeSession(sessionId: string): Promise<void>
  cleanup(): Promise<void>
}
```

- [x] **Step 2：写失败分类与清理测试**

覆盖以下用例：

```ts
it.each([
  ['ready', 'runtime-ready', 'timeout'],
  ['requireBinding', 'session-binding', 'binding-missing'],
  ['waitForEvents', 'session-event', 'event-missing'],
])('maps %s failure to a stable stage and category', async (method, failedStage, category) => {
  const driver = createRecordingDriver()
  driver[method].mockRejectedValueOnce(new RuntimeSessionContractError(category, `${method} failed`))

  const result = await runRuntimeSessionContract(driver, { timeoutMs: 100 })

  expect(result).toMatchObject({ ok: false, failedStage, category })
  expect(driver.cleanup).toHaveBeenCalledTimes(1)
})
```

同时断言业务失败后仍调用一次 `cleanup`；业务失败和清理失败同时出现时，`failedStage` 保留业务阶段，`cleanupFailure` 记录清理错误。

- [x] **Step 3：运行测试确认红灯**

Run:

```powershell
npx vitest run scripts/runtime-session-contract.test.ts
```

Expected: FAIL，原因是执行器模块尚不存在。

- [x] **Step 4：实现最小阶段执行器**

导出固定集合：

```js
export const CONTRACT_STAGES = Object.freeze([
  'runtime-start', 'runtime-ready', 'workspace-create', 'session-create',
  'session-binding', 'session-prompt', 'session-open', 'session-event',
  'session-close', 'cleanup',
])

export const FAILURE_CATEGORIES = Object.freeze([
  'timeout', 'process-exited', 'protocol-mismatch', 'binding-missing',
  'event-missing', 'cleanup-failed', 'internal',
])
```

`runRuntimeSessionContract(driver, options)` 必须：

1. 每阶段通过 `runStage(stage, timeoutMs, action)` 执行；
2. 只保存阶段名、毫秒耗时和成功状态；
3. 在 `finally` 中仅调用一次 `driver.cleanup()`；
4. 通过带 `category` 的 `RuntimeSessionContractError` 归一化失败；
5. 不把原始 prompt、回复或完整路径放入报告。

- [x] **Step 5：运行定向测试并提交**

Run:

```powershell
npx vitest run scripts/runtime-session-contract.test.ts
git diff --check
```

Expected: 测试全部通过，diff 无格式错误。

Commit:

```powershell
git add -- scripts/runtime-session-contract.mjs scripts/runtime-session-contract.d.mts scripts/runtime-session-contract.test.ts
git diff --cached --check
git commit -m "feat(runtime): 增加 Session 契约执行器"
git push origin main
```

## Task 2：实现候选 Client Runtime 驱动

**Files:**

- Create: `scripts/runtime-session-contract-client.mjs`
- Create: `scripts/runtime-session-contract-client.d.mts`
- Create: `scripts/runtime-session-contract-client.test.ts`

- [x] **Step 1：写候选根目录与 bundle 安全测试**

测试必须断言：

- `appDirectory` 必须是绝对路径；
- `@deepseek-ai/dsh-client-runtime`、`dsh-client-connection`、`dsh-typert-registry`、`dsh-api-gateway`、`dsh-api-remotes` 和 `cordis` 必须全部位于候选 `app/node_modules` 内；
- 任何缺失包归类为 `protocol-mismatch`；
- 不允许回退到仓库根 `node_modules`。

使用临时目录写最小 `package.json` 与 `lib/client.js` 注册文件，不启动网络。

- [x] **Step 2：写同步 binding 失败测试**

注入最小客户端服务：`createSession()` 返回 `s-1`，`binding('s-1')` 返回 `undefined`。断言：

```ts
await expect(driver.requireBinding('s-1')).rejects.toMatchObject({
  category: 'binding-missing',
})
expect(binding).toHaveBeenCalledTimes(1)
```

不得使用 setTimeout、notifier 或第二次 Session create。

- [x] **Step 3：运行测试确认红灯**

Run:

```powershell
npx vitest run scripts/runtime-session-contract-client.test.ts
```

Expected: FAIL，原因是候选客户端驱动尚不存在。

- [x] **Step 4：实现候选 bundle materializer**

`loadCandidateClientModules(appDirectory)` 按以下规则执行：

1. 使用 `pathToFileURL` 加载候选 `@deepseek-ai/cordis/lib/index.js`；
2. 临时安装 `globalThis.window.__ModuleLoader__.load(registration)` 捕获候选 `lib/client.js` 的工厂；
3. 仅允许工厂从候选模块表读取依赖；
4. `dsh-client-runtime` 所需的 `dsh-client-ui-slots` 只提供不会被本驱动实例化的 `SlotCore` 占位，因为驱动直接创建 `SessionRuntime`，不创建 `SlotRegistry`；
5. 在 `finally` 中恢复原 `window`、`location` 和 `WebSocket` 全局值。

客户端模块实例化顺序固定为：

```text
cordis
→ dsh-client-connection/client
→ dsh-typert-registry/client
→ dsh-api-gateway/client
→ dsh-api-remotes/client
→ dsh-client-runtime/client
```

- [x] **Step 5：实现真实 Session/Workspace 服务装配**

`createCandidateSessionDriver(options)` 必须：

```js
const ctx = new modules.cordis.Context()
modules.connection.apply(ctx)
modules.typert.apply(ctx)
modules.gateway.apply(ctx)
const disposeRemotes = await modules.remotes.apply(ctx)
const sessions = new modules.runtime.SessionRuntime(ctx, ctx.connection.api, ctx.remote)
ctx.typert.contexts.registerClient('agent', { identity: candidate => sessions.scopeOf(candidate) })
const workspaces = new modules.runtime.WorkspaceRuntime(ctx, ctx.connection.api, sessions)
```

然后由 `ctx.connection.start` 将 mux/host envelope 分发给 `sessions` 和 `workspaces`，并在 `onConnected` 中调用两者的 `handleConnected()`。

业务操作固定为：

```js
const workspace = await workspaces.create({ path: workspacePath })
const sessionId = await workspaces.connectWorkspace(workspace.workspaceId)
const binding = sessions.binding(sessionId)
if (binding === undefined) throw contractError('binding-missing', 'session binding unavailable')
await binding.session.prompt([{ type: 'text', text: promptMarker }], 'queue')
sessions.open(sessionId)
```

事件等待订阅 `binding.session`，并通过候选 Runtime 公开的 Conversation Event/View Registry 建立无 UI 契约投影；只在内存中判断投影是否同时包含 prompt marker 与确定性回复，报告中不保存正文。

- [x] **Step 6：实现关闭与清理**

清理顺序：

```text
sessions.clear
→ connection.stop
→ disposeRemotes
→ ctx.fiber.dispose
→ 恢复全局对象
```

每个动作幂等，部分初始化失败时也能执行。

- [x] **Step 7：运行定向测试并提交**

Run:

```powershell
npx vitest run scripts/runtime-session-contract-client.test.ts scripts/runtime-session-contract.test.ts
git diff --check
```

Commit:

```powershell
git add -- scripts/runtime-session-contract-client.mjs scripts/runtime-session-contract-client.d.mts scripts/runtime-session-contract-client.test.ts
git diff --cached --check
git commit -m "feat(runtime): 连接候选 Session 客户端契约"
git push origin main
```

## Task 3：增加真实候选 Runtime 命令

**Files:**

- Create: `scripts/run-runtime-session-contract.mjs`
- Modify: `package.json`
- Test: `scripts/runtime-session-contract.test.ts`

- [x] **Step 1：写 CLI 参数与脱敏报告测试**

入口参数固定为：

```text
--runtime-root=<包含 node.exe 或 bin/node 的候选根目录>
--report=<JSON 报告路径>
--runtime-version=<受管 Runtime 版本>
```

测试断言缺少参数时退出失败；报告不得包含 `SESSION_CONTRACT_PROMPT`、`SESSION_CONTRACT_PONG`、API Key 或临时绝对路径。

- [x] **Step 2：实现 loopback 端口和临时目录准备**

使用 `mkdtemp` 创建：

```text
contract-root/
├─ dsh-home/
└─ 工作区 Ω/
```

使用 `node:net` 在 `127.0.0.1` 上保留端口后立即释放；Runtime 和模型服务都只监听 loopback。

- [x] **Step 3：启动确定性模型和候选 Runtime**

复用 `startFakeDeepSeek({ text: 'SESSION_CONTRACT_PONG' })`，以候选 Node 启动：

```text
<candidate-node> app/launcher.mjs --port <port> --no-open
```

环境必须包含：

```text
DSH_HOME=<临时 dsh-home>
DSH_DESKTOP_PROFILE_ID=contract
DSH_DESKTOP_PROFILE_REVISION=1
DSH_E2E_MODEL_ENDPOINT=<loopback fixture chat completions URL>
DEEPSEEK_BASE_URL=<loopback fixture URL>
DEEPSEEK_API_KEY=sk-session-contract-fixture
NODE_EXTRA_CA_CERTS=<临时 CA 文件>
```

Windows 使用 `windowsHide: true`，避免弹出 CMD 窗口。

- [x] **Step 4：运行执行器并写 JSON 报告**

报告 schema 固定为：

```json
{
  "schemaVersion": 1,
  "runtimeVersion": "0.1.3-preview",
  "platform": "win32-x64",
  "ok": true,
  "durationMs": 1234,
  "stages": [{ "stage": "runtime-ready", "ok": true, "durationMs": 100 }]
}
```

失败报告增加 `failedStage`、`category` 和可选 `processExitCode`，不写原始异常堆栈。失败时 `process.exitCode = 1`。

- [x] **Step 5：实现进程树和临时目录清理**

正常与异常路径都先终止候选 Runtime；Windows 使用已有受控进程清理方式或 `taskkill /PID <pid> /T /F` 的精确 PID，其他平台向进程组发送终止信号。确认进程退出后才删除本次创建的唯一临时目录。

- [x] **Step 6：增加 npm 命令并提交**

`package.json` 增加：

```json
"runtime:session-contract": "node scripts/run-runtime-session-contract.mjs"
```

Run:

```powershell
npx vitest run scripts/runtime-session-contract.test.ts scripts/runtime-session-contract-client.test.ts
git diff --check
```

Commit:

```powershell
git add -- package.json scripts/run-runtime-session-contract.mjs scripts/runtime-session-contract.test.ts
git diff --cached --check
git commit -m "feat(runtime): 增加候选 Session 契约命令"
git push origin main
```

## Task 4：接入 Runtime 构建与上游同步

**Files:**

- Modify: `scripts/build-runtime.mjs`
- Modify: `.github/workflows/upstream-sync.yml`
- Modify: `scripts/workflow-contract.test.ts`
- Test: `scripts/workflow-contract.test.ts`

- [x] **Step 1：写构建顺序失败测试**

在 `scripts/product-copy.test.ts` 中断言：

```text
inspectAssembledRuntimeCapabilities
< writeRuntimeLauncher
< materializeRuntimeLinks
< runRuntimeSessionContract
< archive creation
```

并断言门禁失败时不会调用 `writeUnsignedRuntimeManifest`。

- [x] **Step 2：写工作流失败测试**

`workflow-contract.test.ts` 断言：

- `desktop.yml` 的 Runtime 资产只能来自执行过新版 `build-runtime.mjs` 的目录；
- `upstream-sync.yml` 的 `verify_supported_platform` 在 `publish_refs` 之前运行候选构建；
- `publish_refs.needs` 包含候选 Runtime 验证 job。

- [x] **Step 3：运行测试确认红灯**

Run:

```powershell
npx vitest run scripts/workflow-contract.test.ts scripts/product-copy.test.ts
```

Expected: FAIL，构建脚本和上游同步尚未声明 Session 契约门禁。

- [x] **Step 4：在归档前执行门禁**

`build-runtime.mjs` 在 `materializeRuntimeLinks(stage, output)` 后调用 CLI，报告写入：

```text
<output>/session-contract-report.json
```

只有命令成功后才生成 Runtime archive 和 unsigned manifest。报告不进入 Runtime ZIP，只作为工作流验证证据。

- [x] **Step 5：在创建升级 tag 前构建支持平台候选**

`upstream-sync.yml` 的 `verify_supported_platform` 增加 Node 安装、`npm ci --legacy-peer-deps` 和：

```bash
node scripts/build-runtime.mjs \
  --target=darwin-aarch64 \
  --version="$(node -p "JSON.parse(require('fs').readFileSync('release/versions.json')).runtimeVersion")" \
  --url="file://${RUNNER_TEMP}/dsh-runtime-darwin-aarch64.tar.gz" \
  --output="${RUNNER_TEMP}/runtime-contract"
test -s "${RUNNER_TEMP}/runtime-contract/session-contract-report.json"
```

该 job 成功后 `publish_refs` 才能创建版本 tag。

- [x] **Step 6：运行门禁测试并提交**

Run:

```powershell
npx vitest run scripts/workflow-contract.test.ts scripts/product-copy.test.ts scripts/runtime-session-contract.test.ts scripts/runtime-session-contract-client.test.ts
git diff --check
```

Commit:

```powershell
git add -- scripts/build-runtime.mjs .github/workflows/upstream-sync.yml scripts/workflow-contract.test.ts scripts/product-copy.test.ts
git diff --cached --check
git commit -m "ci(runtime): 发布前验证 Session 契约"
git push origin main
```

## Task 5：运行真实候选验证并更新长期计划

**Files:**

- Modify: `doc/README.md`
- Modify: `doc/plans/2026-08-24-runtime-session-contract-gate.md`

- [x] **Step 1：构建本机候选 Runtime**

Run:

```powershell
node scripts/build-runtime.mjs --target=windows-x86_64 --version=0.1.10-preview --url=file:///<repo>/e2e-artifacts/dsh-runtime-windows-x86_64.zip --output=e2e-artifacts/runtime-contract-build
```

Expected:

- `session-contract-report.json` 存在且 `ok: true`；
- Runtime ZIP 和 unsigned manifest 只在门禁成功后出现；
- 事件阶段观察到确定性回复但报告不包含回复正文。

- [x] **Step 2：运行阶段工程门禁**

Run:

```powershell
npm run test -- scripts/runtime-session-contract.test.ts scripts/runtime-session-contract-client.test.ts scripts/workflow-contract.test.ts
npm run plugin:test
npm run build:web
npm run plugin:build
git diff --check
```

若 `npm run check` 仅在 Windows symlink fixture 创建阶段因 `EPERM` 失败，记录具体文件和通过数量；不能把环境限制写成产品回归，也不能声称完整门禁通过。

- [x] **Step 3：更新 P0.1 状态**

在 `doc/README.md` 中：

- 标记 Runtime 升级后的 Session Contract fixture 已完成；
- 保留“安装版连续 30 轮”作为人工/发布验收项；
- 将“下一步从这里开始”推进到 P0.2 Windows 完整安装生命周期 E2E 的升级、数据保留和卸载闭环。

- [x] **Step 4：记录真实验证证据**

在本文追加：实际命令、候选 Runtime 版本、各阶段耗时、报告路径、通过测试数量和环境限制。不得记录完整用户路径、模型正文、Key 或临时 DSH_HOME。

- [x] **Step 5：提交文档并推送**

```powershell
git add -- doc/README.md doc/plans/2026-08-24-runtime-session-contract-gate.md
git diff --cached --check
git commit -m "docs(runtime): 记录 Session 契约门禁结果"
git push origin main
```

## 验收边界

- 不增加真实模型 Key 或公网依赖。
- 不通过轮询掩盖同步 binding 失败。
- 不在报告、日志或 Artifact 中保存 prompt 和回复正文。
- 不在本阶段改变 Rust active pointer、last-known-good 或应用内升级事务。
- 不用 UI 选择器证明 Session 核心契约；安装包 E2E 仍负责用户界面与持久化。
- 任一业务阶段失败后必须清理候选进程和本次唯一临时目录。

## 实施与验证结果（2026-08-24）

### 实现结果

- 纯执行器、候选 Client Runtime 驱动、真实 CLI、构建接入和上游同步接入均已完成。
- 候选客户端通过 Runtime 公开导出的 `ConversationEventRegistry` 与 `ConversationViewRegistry` 建立无 UI 的契约投影；没有读取私有 history 方法，也没有加载 React 页面插件。
- `scripts/build-runtime.mjs` 在 Runtime links 物化后、ZIP 和 unsigned manifest 生成前执行门禁。
- `.github/workflows/upstream-sync.yml` 在创建升级 tag 前于受支持的 macOS 平台构建候选 Runtime，并检查机器报告的 `ok` 值。
- 报告保存在构建输出目录，不进入 Runtime ZIP。

### 真实 Windows 候选

验证版本：`0.1.10-preview`。

构建命令使用仓库相对输出目录，并复用同版本候选依赖缓存：

```powershell
node scripts/build-runtime.mjs `
  --target=windows-x86_64 `
  --version=0.1.10-preview `
  --url=file:///<repo>/e2e-artifacts/dsh-runtime-windows-x86_64.zip `
  --output=e2e-artifacts/runtime-contract-build `
  --dependency-cache=e2e-artifacts/runtime-build-windows-x86_64/stage/app/node_modules
```

结果：

- `e2e-artifacts/runtime-contract-build/session-contract-report.json`：`ok: true`；
- 总耗时 `9414 ms`；Runtime ready `8176 ms`；Workspace `45 ms`；Session create `367 ms`；同步 binding `2 ms`；prompt `46 ms`；open `2 ms`；事件投影 `160 ms`；cleanup `418 ms`；
- 确定性模型请求已发生，但报告不包含 prompt、回复正文、Key、临时目录或异常堆栈；
- Runtime ZIP 为 `111463572` 字节，检查确认不包含 Session 契约报告；
- ZIP 与 unsigned manifest 均在契约通过后生成。

### 工程门禁

- Session、工作流与产品构建顺序：4 个测试文件、50 项测试通过；
- Desktop 插件：23 个测试文件、89 项测试通过；
- `npm run build:web` 通过；
- `npm run plugin:build` 通过；
- Rust 工程完成测试配置编译，但本机测试二进制启动返回 `STATUS_ENTRYPOINT_NOT_FOUND (0xc0000139)`，因此不计为 Rust 测试通过；该限制与本次 Node.js 契约脚本无直接关系，后续应在 GitHub macOS 支持平台 job 和干净 Windows 构建机复核。

### 提交记录

- `61b5609 feat(runtime): 增加 Session 契约执行器`
- `ea9e73e feat(runtime): 连接候选 Session 客户端契约`
- `1c5c30a feat(runtime): 增加候选 Session 契约命令`
- `a707180 ci(runtime): 发布前验证 Session 契约`
- `d2c09cd fix(runtime): 补齐 Session 契约类型声明`
- `c361bb2 fix(ci): 修正 macOS Runtime 契约资产名`
