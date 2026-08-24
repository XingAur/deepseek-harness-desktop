# Session 同步绑定契约实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除本地项目流程中的 Session binding 轮询和重复创建兜底，严格使用固定 DSH Runtime 已承诺的同步绑定契约。

**Architecture:** `SessionRuntime.create()` 和 `workspaces.connectWorkspace()` 返回时，目标 Session 已进入 list store，`sessions.binding(id)` 必须同步可用。桌面插件只读取一次 binding；缺失时立即失败并保留可诊断错误，不等待 notifier、不创建第二个 Session。官方允许在 `open()` 前写入 Session machine，因此成功路径保持 `binding → prompt → open`。

**Tech Stack:** TypeScript、Vitest、`@deepseek-ai/dsh-client-runtime` Session/Workspace Contract、npm workspaces。

---

## 上游事实依据

固定 Runtime 的声明明确给出两个保证：

- `SessionRuntime.create()` Promise 完成时，Session 已在 list store 中，`binding(id)` 可以同步解析。
- `connectWorkspace()` 无论复用空会话还是创建新会话，返回的 ID 都可以被 `binding(id)` 同步解析；draft hand-off 可以在 `open()` 前写入 machine。

因此当前最多 100 次 `setTimeout(0)` 轮询会隐藏上游契约破坏，而 `modify()` 中 binding 缺失后再次调用 `sessions.create()` 会制造重复空会话风险。

## 文件职责

- `packages/dsh-plugin-desktop/tests/project-controller.spec.ts`：锁定单次 binding、禁止重复 Session 和成功调用顺序。
- `packages/dsh-plugin-desktop/src/client/project-controller.ts`：提供严格同步 binding 解析并删除轮询/重复创建。
- `doc/README.md`：记录经过源码核验的实际调用顺序。

### Task 1：增加同步绑定失败测试

**Files:**

- Modify: `packages/dsh-plugin-desktop/tests/project-controller.spec.ts`

- [ ] **Step 1：将“等待 binding”测试改为“首次缺失立即失败”**

用下面的测试替换 `waits for the newly created session binding before queuing the project`：

```ts
it('fails immediately when create resolves without a synchronous session binding', async () => {
  const workspaces = workspaceFixture()
  const sessions = sessionFixture()
  vi.mocked(sessions.binding)
    .mockReturnValueOnce(undefined)
    .mockReturnValue({ sessionId: 's-1', session: sessions.session })
  const controller = createProjectController(workspaces, sessions, locationGateway())
  const draft = await controller.prepare({ idea: '构建工具', profileId: 'p-a' })

  await expect(controller.confirm(draft)).rejects.toThrow('会话尚未准备好')

  expect(sessions.binding).toHaveBeenCalledTimes(1)
  expect(sessions.session.prompt).not.toHaveBeenCalled()
  expect(sessions.open).not.toHaveBeenCalled()
  expect(workspaces.delete).toHaveBeenCalledWith('w-new')
})
```

- [ ] **Step 2：增加 connectWorkspace 不得重复创建 Session 的测试**

```ts
it('does not create a duplicate session when connectWorkspace violates the binding contract', async () => {
  const workspaces = workspaceFixture()
  const sessions = sessionFixture()
  vi.mocked(sessions.binding).mockReturnValue(undefined)
  const controller = createProjectController(workspaces, sessions, locationGateway())

  await expect(controller.modify('w-1', '更新首页')).rejects.toThrow('会话尚未准备好')

  expect(workspaces.connectWorkspace).toHaveBeenCalledWith('w-1')
  expect(sessions.binding).toHaveBeenCalledTimes(1)
  expect(sessions.create).not.toHaveBeenCalled()
  expect(sessions.session.prompt).not.toHaveBeenCalled()
  expect(sessions.open).not.toHaveBeenCalled()
})
```

- [ ] **Step 3：锁定成功路径调用顺序**

在现有 confirm 成功测试中加入：

```ts
expect(vi.mocked(sessions.create).mock.invocationCallOrder[0])
  .toBeLessThan(vi.mocked(sessions.binding).mock.invocationCallOrder[0])
expect(vi.mocked(sessions.binding).mock.invocationCallOrder[0])
  .toBeLessThan(sessions.session.prompt.mock.invocationCallOrder[0])
expect(sessions.session.prompt.mock.invocationCallOrder[0])
  .toBeLessThan(vi.mocked(sessions.open).mock.invocationCallOrder[0])
```

- [ ] **Step 4：运行定向测试并确认红灯**

Run:

```powershell
npm run test -w @dsh/desktop-plugin -- project-controller.spec.ts
```

Expected: 至少两个新增场景失败；旧实现会二次读取 binding，并在 modify 中额外调用 `sessions.create()`。

- [ ] **Step 5：提交失败测试**

```powershell
git add -- packages/dsh-plugin-desktop/tests/project-controller.spec.ts
git diff --cached --check
git commit -m "test(session): 锁定同步绑定契约"
```

### Task 2：删除轮询和重复创建兜底

**Files:**

- Modify: `packages/dsh-plugin-desktop/src/client/project-controller.ts`

- [ ] **Step 1：增加严格 binding helper**

在文件底部增加：

```ts
function requireSessionBinding(sessions: SessionsLike, sessionId: string) {
  const binding = sessions.binding(sessionId)
  if (binding === undefined) throw new Error('项目会话尚未准备好，请重试')
  return binding
}
```

- [ ] **Step 2：修改 confirm 使用单次同步解析**

将：

```ts
const binding = await waitForSessionBinding(sessions, sessionId)
```

替换为：

```ts
const binding = requireSessionBinding(sessions, sessionId)
```

保持 `prompt()` 成功后再 `sessions.open(sessionId)`，使提示失败时不会导航到已回滚的 Workspace。

- [ ] **Step 3：修改 modify 禁止重复创建**

将 connect 后的 fallback 分支替换为：

```ts
const sessionId = await workspaces.connectWorkspace(workspaceId)
const binding = requireSessionBinding(sessions, sessionId)
```

删除 binding 缺失时的第二次 `sessions.create()`。

- [ ] **Step 4：删除 waitForSessionBinding**

完整删除最多循环 100 次和 `setTimeout(0)` 的 helper，不保留异步轮询代码。

- [ ] **Step 5：运行定向测试并确认绿灯**

Run:

```powershell
npm run test -w @dsh/desktop-plugin -- project-controller.spec.ts
```

Expected: `project-controller.spec.ts` 全部通过。

- [ ] **Step 6：运行插件类型检查和完整测试**

Run:

```powershell
npm run typecheck -w @dsh/desktop-plugin
npm run test -w @dsh/desktop-plugin
```

Expected: 类型检查通过，插件全部测试通过。

- [ ] **Step 7：提交最小实现**

```powershell
git add -- packages/dsh-plugin-desktop/src/client/project-controller.ts
git diff --cached --check
git commit -m "fix(session): 使用同步会话绑定契约"
```

### Task 3：验证并记录本步骤

**Files:**

- Modify: `doc/plans/2026-08-24-session-binding-contract.md`

- [ ] **Step 1：执行仓库相关门禁**

Run:

```powershell
npm run check
git diff --check
```

Expected: 根级测试、插件测试、Web 构建和插件构建全部通过；工作树不存在格式错误。

- [ ] **Step 2：在本计划中勾选已完成步骤并记录测试结果**

在文末增加实际测试命令、通过数量和已知限制。不得写“测试通过”而不记录具体命令。

- [ ] **Step 3：提交计划进度**

```powershell
git add -- doc/plans/2026-08-24-session-binding-contract.md
git diff --cached --check
git commit -m "docs(session): 记录同步绑定验证结果"
```

## 后续独立计划

本计划只处理 binding 契约，不把安装包 E2E 混入同一实现提交。完成后单独创建“打包会话实时一致性 E2E”计划，覆盖：

- 项目创建后首条消息和回复实时出现；
- Session 标题实时进入左侧列表；
- 创建第二个 Session；
- 两个 Session 之间切换时各自消息无需刷新；
- 应用重启后 Session 仍可见；
- 固定 Runtime 更新后重复执行相同 Contract 场景。
