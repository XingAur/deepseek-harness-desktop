# 新建会话同步过渡实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除官方新会话异步创建期间旧输入框仍可发送的竞态，让第二会话标题和正文无需刷新即可实时显示。

**Architecture:** 在桌面插件中新增独立的 `new-session-transition` 适配器，先按官方规则解析目标 Workspace，再同步清空当前 Session，最后调用原始 `workspaces.startSession`。会话内容仍完全由官方 Session Event 投影驱动；适配器通过 Cordis Effect 安装和卸载，不修改上游 Runtime 文件。

**Tech Stack:** TypeScript 6、Vitest、Cordis Client Plugin、Tauri 2、WebdriverIO、Chrome DevTools Protocol、NSIS。

---

## 文件结构

- Create: `packages/dsh-plugin-desktop/src/client/new-session-transition.ts`：解析目标 Workspace，并负责安装、引用计数和卸载同步过渡守卫。
- Create: `packages/dsh-plugin-desktop/tests/new-session-transition.spec.ts`：锁定显式、当前、最近 Workspace 规则以及同步清空、幂等安装和恢复行为。
- Modify: `packages/dsh-plugin-desktop/src/client/contracts.ts`：补齐 `startSession`、Session 列表和 `clear` 的最小官方契约。
- Modify: `packages/dsh-plugin-desktop/src/client/advanced-shell.ts`：在 Cordis Effect 中安装同步过渡守卫。
- Modify: `packages/dsh-plugin-desktop/tests/fixtures.ts`：让 Workspace、Session 测试夹具实现新增的真实契约。
- Modify: `packages/dsh-plugin-desktop/tests/advanced-shell.spec.ts`：断言 Advanced Shell 注册并清理过渡守卫。
- Modify: `e2e/support/desktop.ts`：保留真实新会话输入、切换断言和 WebSocket 诊断能力。
- Test: `e2e/specs/provisioning-success.installer.e2e.ts`：现有双会话、无刷新切换和重启恢复用例。

### Task 1：用单元测试锁定同步过渡契约

**Files:**
- Create: `packages/dsh-plugin-desktop/tests/new-session-transition.spec.ts`

- [x] **Step 1：写入目标 Workspace 解析测试**

```ts
import { describe, expect, it, vi } from 'vitest'
import {
  installNewSessionTransition,
  resolveNewSessionWorkspace,
} from '../src/client/new-session-transition'
import { sessionFixture, workspaceFixture } from './fixtures'

describe('new session transition', () => {
  const currentWorkspace = {
    workspaceId: 'w-current',
    path: 'C:\\current',
    title: '当前',
    sessionIds: ['s-current'],
    createdAt: '2026-08-24T00:00:00Z',
    updatedAt: '2026-08-24T00:00:00Z',
  }
  const recentWorkspace = {
    ...currentWorkspace,
    workspaceId: 'w-recent',
    path: 'C:\\recent',
    title: '最近',
    sessionIds: [],
  }

  it('preserves explicit, current, and recent workspace priority', () => {
    const snapshot = workspaceFixture([recentWorkspace, currentWorkspace]).list.getSnapshot()
    expect(resolveNewSessionWorkspace(snapshot, 's-current', 'w-explicit')).toBe('w-explicit')
    expect(resolveNewSessionWorkspace(snapshot, 's-current')).toBe('w-current')
    expect(resolveNewSessionWorkspace(snapshot, 'missing')).toBe('w-recent')
  })
})
```

- [x] **Step 2：写入同步清空和销毁恢复测试**

```ts
it('clears the old session before starting the resolved workspace and restores on dispose', () => {
  const order: string[] = []
  const workspaces = workspaceFixture([recentWorkspace, currentWorkspace])
  const sessions = sessionFixture('s-current')
  const original = workspaces.startSession
  vi.mocked(workspaces.startSession).mockImplementation((workspaceId) => {
    order.push(`start:${workspaceId ?? 'none'}`)
  })
  vi.mocked(sessions.clear).mockImplementation(() => { order.push('clear') })

  const dispose = installNewSessionTransition(workspaces, sessions)
  workspaces.startSession()

  expect(order).toEqual(['clear', 'start:w-current'])
  dispose()
  expect(workspaces.startSession).toBe(original)
})

it('shares one wrapper across repeated installations', () => {
  const workspaces = workspaceFixture([recentWorkspace])
  const sessions = sessionFixture()
  const original = workspaces.startSession
  const disposeFirst = installNewSessionTransition(workspaces, sessions)
  const wrapped = workspaces.startSession
  const disposeSecond = installNewSessionTransition(workspaces, sessions)

  expect(workspaces.startSession).toBe(wrapped)
  disposeFirst()
  expect(workspaces.startSession).toBe(wrapped)
  disposeSecond()
  expect(workspaces.startSession).toBe(original)
})
```

- [x] **Step 3：运行测试，确认先红**

Run:

```powershell
npm run test -w @dsh/desktop-plugin -- new-session-transition.spec.ts
```

Expected: FAIL，提示找不到 `../src/client/new-session-transition` 或导出不存在。

- [x] **Step 4：提交失败测试**

```powershell
git add -- packages/dsh-plugin-desktop/tests/new-session-transition.spec.ts
git commit -m "test(session): 锁定新建会话同步过渡契约"
git push origin main
```

### Task 2：实现同步过渡守卫并接入 Advanced Shell

**Files:**
- Create: `packages/dsh-plugin-desktop/src/client/new-session-transition.ts`
- Modify: `packages/dsh-plugin-desktop/src/client/contracts.ts`
- Modify: `packages/dsh-plugin-desktop/src/client/advanced-shell.ts`
- Modify: `packages/dsh-plugin-desktop/tests/fixtures.ts`
- Modify: `packages/dsh-plugin-desktop/tests/advanced-shell.spec.ts`
- Test: `packages/dsh-plugin-desktop/tests/new-session-transition.spec.ts`

- [x] **Step 1：补齐桌面插件最小契约**

在 `contracts.ts` 中增加：

```ts
export interface SessionListStateLike {
  current?: string
}

export interface WorkspacesLike {
  readonly list: ObservableSnapshot<WorkspaceListState>
  startSession(workspaceId?: string): void
  // 保留现有方法
}

export interface SessionsLike {
  readonly list: ObservableSnapshot<SessionListStateLike>
  clear(): void
  // 保留现有方法
}
```

- [x] **Step 2：实现目标解析和可恢复包装**

创建 `new-session-transition.ts`：

```ts
import type {
  SessionsLike,
  WorkspaceListState,
  WorkspacesLike,
} from './contracts'

interface Installation {
  references: number
  original: WorkspacesLike['startSession']
  wrapped: WorkspacesLike['startSession']
}

const installations = new WeakMap<WorkspacesLike, Installation>()

export function resolveNewSessionWorkspace(
  workspace: WorkspaceListState,
  currentSessionId?: string,
  requestedWorkspaceId?: string,
): string | undefined {
  if (requestedWorkspaceId !== undefined) return requestedWorkspaceId
  if (currentSessionId !== undefined) {
    const current = workspace.items.find((item) => item.sessionIds.includes(currentSessionId))
    if (current !== undefined) return current.workspaceId
  }
  return workspace.recentWorkspaceId
}

export function installNewSessionTransition(
  workspaces: WorkspacesLike,
  sessions: SessionsLike,
): () => void {
  const active = installations.get(workspaces)
  if (active !== undefined) {
    active.references += 1
    return () => release(workspaces, active)
  }

  const original = workspaces.startSession
  const installation: Installation = {
    references: 1,
    original,
    wrapped: (requestedWorkspaceId) => {
      const target = resolveNewSessionWorkspace(
        workspaces.list.getSnapshot(),
        sessions.list.getSnapshot().current,
        requestedWorkspaceId,
      )
      sessions.clear()
      original.call(workspaces, target)
    },
  }
  installations.set(workspaces, installation)
  workspaces.startSession = installation.wrapped
  return () => release(workspaces, installation)
}

function release(workspaces: WorkspacesLike, installation: Installation): void {
  installation.references -= 1
  if (installation.references > 0) return
  if (workspaces.startSession === installation.wrapped) {
    workspaces.startSession = installation.original
  }
  installations.delete(workspaces)
}
```

- [x] **Step 3：更新测试夹具**

`workspaceFixture` 增加：

```ts
startSession: vi.fn(),
```

`sessionFixture` 接受可选当前 Session，并增加列表和清空能力：

```ts
export function sessionFixture(current?: string) {
  let snapshot = { current }
  const listeners = new Set<() => void>()
  // 保留 prompt 和 binding
  const sessions = {
    list: {
      getSnapshot: () => snapshot,
      subscribe: (listener: () => void) => {
        listeners.add(listener)
        return () => listeners.delete(listener)
      },
    },
    clear: vi.fn(() => {
      snapshot = {}
      listeners.forEach((listener) => listener())
    }),
    // 保留现有方法
  }
  return sessions
}
```

- [x] **Step 4：在 Advanced Shell 生命周期内安装守卫**

`advanced-shell.ts` 增加：

```ts
import { installNewSessionTransition } from './new-session-transition'

ctx.effect(
  () => installNewSessionTransition(ctx.workspaces, ctx.sessions),
  'desktop: new session transition',
)
```

`advanced-shell.spec.ts` 使用完整 Workspace/Session 夹具，并断言 Effect 清理后 `startSession` 恢复原方法。

- [x] **Step 5：运行聚焦测试和类型检查**

Run:

```powershell
npm run test -w @dsh/desktop-plugin -- new-session-transition.spec.ts advanced-shell.spec.ts
npm run typecheck -w @dsh/desktop-plugin
```

Expected: 两个测试文件全部 PASS，TypeScript 无错误。

- [x] **Step 6：运行插件完整测试**

Run:

```powershell
npm run plugin:test
npm run plugin:build
```

Expected: 插件全部测试通过并生成 `packages/dsh-plugin-desktop/lib`。

- [x] **Step 7：提交实现**

```powershell
git add -- packages/dsh-plugin-desktop/src/client/new-session-transition.ts packages/dsh-plugin-desktop/src/client/contracts.ts packages/dsh-plugin-desktop/src/client/advanced-shell.ts packages/dsh-plugin-desktop/tests/fixtures.ts packages/dsh-plugin-desktop/tests/advanced-shell.spec.ts
git commit -m "fix(session): 新建会话时同步退出旧会话"
git push origin main
```

### Task 3：完成真实安装包无刷新会话回归

**Files:**
- Modify: `e2e/support/desktop.ts`
- Test: `e2e/specs/provisioning-success.installer.e2e.ts`

- [x] **Step 1：保留真实会话交互和协议诊断**

`DesktopHarness` 保留以下能力：

```ts
createConversation(prompt: string): Promise<void>
assertSessionRoundTrip(markers: readonly string[]): Promise<void>
```

`createConversation` 必须：

- 点击真实 `button[aria-label="新建会话"]`；
- 等待 Hero 或普通会话输入框；
- 立即输入并发送第二会话标记；
- 只在 `[data-slot="conversation.session"]` 内断言用户消息和 `E2E_PONG`；
- 不刷新页面，不直接调用 Session API。

CDP 诊断保留 `Network.webSocketFrameReceived` 中包含 `session/` 或 `host/session` 的帧，并在失败时输出最近 80 条。

- [x] **Step 2：构建包含最新插件的 Runtime 和 E2E 安装包**

Run:

```powershell
npm run e2e:setup:build
```

Expected:

- 生成 `e2e-artifacts/runtime-build-windows-x86_64/dsh-runtime-windows-x86_64.zip`；
- 生成 `e2e-artifacts/DeepSeek-Harness-Desktop-E2E-Web-Setup-x64.exe`；
- Runtime 使用 `release/versions.json` 中的 `dshVersion`，不复用不匹配的依赖缓存。

- [x] **Step 3：运行真实安装包 E2E**

Run:

```powershell
npm run e2e:installer
```

Expected:

- 首次启动准备一次 Runtime；
- 热启动不重复下载，工作台在 8 秒阈值内出现；
- 第二条消息的 `session.prompt` 和事件帧使用新 Session ID；
- 两个会话无需刷新即可往返切换；
- 重启后两个会话仍可见且正文正确。

- [ ] **Step 4：运行仓库级检查（已执行，本机符号链接权限阻塞 11 项）**

Run:

```powershell
npm run check
```

Expected: Web、桌面插件、Agent Adapter 的测试、构建和类型检查全部通过。

- [x] **Step 5：提交通过验证的 E2E 夹具**

```powershell
git add -- e2e/support/desktop.ts
git commit -m "test(e2e): 验证打包会话实时切换"
git push origin main
```

### Task 4：记录实施结果

**Files:**
- Modify: `doc/designs/2026-08-24-new-session-transition-race.md`
- Modify: `doc/plans/2026-08-24-new-session-transition-race.md`

- [x] **Step 1：更新设计状态和计划勾选**

将设计文档状态改为：

```text
状态：已实施并通过 Windows 打包端到端验证
```

勾选本计划中实际完成的步骤，并记录：

- 失败测试的原始竞态证据；
- 修复后第二会话使用的新 Session ID；
- 首次启动和热启动耗时；
- Windows E2E 安装包路径。

- [x] **Step 2：检查文档完整性和格式**

Run:

```powershell
$forbidden = @('T' + 'BD', '待' + '定', '待' + '补')
Select-String -Path doc/designs/2026-08-24-new-session-transition-race.md,doc/plans/2026-08-24-new-session-transition-race.md -Pattern $forbidden
git diff --check -- doc/designs/2026-08-24-new-session-transition-race.md doc/plans/2026-08-24-new-session-transition-race.md
```

Expected: 两条命令均无错误输出。

- [x] **Step 3：提交实施记录**

```powershell
git add -- doc/designs/2026-08-24-new-session-transition-race.md doc/plans/2026-08-24-new-session-transition-race.md
git commit -m "docs(session): 记录新建会话竞态验证结果"
git push origin main
```

## 实施记录

- 原始失败证据：新会话异步创建期间，旧输入框仍可发送，消息进入旧 Session；新空白 Session 随后被选中，造成视觉上的“消失”。
- 最终实现：同步退出旧 Session，保留官方 Workspace 选择规则，并以事件签名变化驱动 Workspace/Session 基线协调。
- 安装包验证：`npm run e2e:installer` 2/2 通过；第二会话实时显示，两会话无刷新往返，重启后再次往返通过。
- 最终通过运行的时延样本：首次 Generation 63,872 ms，热启动 6,599 ms。
- 安装包：`e2e-artifacts/DeepSeek-Harness-Desktop-E2E-Web-Setup-x64.exe`。
- 仓库检查：插件 89 项、根测试中不依赖符号链接的 294 项、Web/插件/Agent 构建通过；本机因 Windows `symlink` 权限缺失保留 11 项 `EPERM` 环境失败。
