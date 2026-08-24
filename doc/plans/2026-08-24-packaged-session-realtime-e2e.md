# 打包版会话实时一致性 E2E 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用真实 Windows 安装包锁定“新建会话立即显示、两个会话无需刷新即可切换、重启后记录仍可见”的发布级契约。

**Architecture:** 测试继续复用现有 NSIS 安装、内置 Runtime、WebView2 CDP 和本地假模型链路，不引入仅测试可见的产品后门。桌面夹具只操作当前真实 UI：项目目录和权限由产品自动决定；会话断言只读取官方 `conversation.session` slot，左侧会话行通过官方操作按钮的无障碍标签识别，避免把工作区行或左侧标题误判为聊天内容。

**Tech Stack:** TypeScript、Vitest、WebdriverIO Tauri Service、WebView2 CDP、Tauri 2、NSIS、DeepSeek Harness Runtime

---

## 文件结构与职责

- 修改 `e2e/specs/provisioning-success.installer.e2e.ts`
  - 声明两个唯一会话标记。
  - 覆盖创建、实时显示、双向切换、退出重启和持久化恢复。
- 修改 `e2e/support/desktop.ts`
  - 让本地项目创建夹具匹配当前简化页面。
  - 增加发送新会话和按聊天正文定位会话的能力。
  - 将 DOM 细节封装在 CDP 夹具内，不泄露给测试用例。
- 修改 `doc/plans/2026-08-24-packaged-session-realtime-e2e.md`
  - 勾选完成项并记录最终验证结果、环境限制和安装包证据。

## 稳定 DOM 契约

测试只使用以下上游或本项目稳定语义：

```text
textarea[aria-label="项目需求"]
button 文本：检查并预览
button 文本：确认并开始构建
button[aria-label="新建会话"]
textarea[placeholder="给智能体发消息"]
textarea[placeholder="描述你想要构建的内容"]
button[aria-label="发送消息"]
[data-slot="conversation.session"]
div[role="treeitem"] 内含 button[aria-label^="会话“"]
```

不再使用已经从产品中删除的 `项目路径`、`构建权限模式` 和创建目录复选框。

### Task 1: 先写打包版会话回归场景

**Files:**
- Modify: `e2e/specs/provisioning-success.installer.e2e.ts`
- Test: `e2e/specs/provisioning-success.installer.e2e.ts`

- [ ] **Step 1: 保存可重启的候选应用路径**

在文件级保存第一次安装得到的应用路径：

```ts
let world: E2EWorld
let appBinary: string

// 安装测试内
if (installation.appBinary === undefined) throw new Error('安装记录缺少应用路径')
appBinary = installation.appBinary
```

- [ ] **Step 2: 将旧项目创建调用改为当前简化契约**

使用唯一的第一会话标记，不再传路径和权限：

```ts
const FIRST_SESSION_MARKER = 'E2E 第一会话 Ω'
const SECOND_SESSION_MARKER = 'E2E 第二会话 二'

await desktop.createProject({
  idea: `${FIRST_SESSION_MARKER}：请创建 README，并在完成后回复确认`,
})
```

- [ ] **Step 3: 写入无需刷新切换和重启恢复断言**

在同一测试中创建第二个会话并完成双向切换；随后退出、重新启动同一候选应用，再重复断言：

```ts
await desktop.createConversation(`${SECOND_SESSION_MARKER}：请回复确认`)
await desktop.assertSessionRoundTrip([FIRST_SESSION_MARKER, SECOND_SESSION_MARKER])

await desktop.quit()
await desktop.launch(appBinary)
await desktop.waitForWorkbench(8_000)
await desktop.assertSessionRoundTrip([FIRST_SESSION_MARKER, SECOND_SESSION_MARKER])
```

- [ ] **Step 4: 运行测试并确认旧夹具失败**

Run:

```powershell
npm run e2e:installer
```

Expected: FAIL。失败点应为旧 `createProject` 仍查找 `input[aria-label="项目路径"]`，或新 `createConversation` / `assertSessionRoundTrip` 尚未实现；不能是安装器、Runtime 签名或假模型启动失败。

- [ ] **Step 5: 提交失败测试**

```powershell
git add e2e/specs/provisioning-success.installer.e2e.ts
git commit -m "test(e2e): 锁定打包会话实时一致性"
git push origin main
```

### Task 2: 更新本地项目创建夹具

**Files:**
- Modify: `e2e/support/desktop.ts`
- Test: `e2e/specs/provisioning-success.installer.e2e.ts`

- [ ] **Step 1: 收窄公开创建契约**

将接口和实现签名统一为：

```ts
createProject(input: { idea: string }): Promise<void>
```

- [ ] **Step 2: 删除已下线控件操作**

`submitBuild` 只填写需求并点击预览、确认：

```ts
const submitBuild = async () => {
  await this.openLocalProjects(page)
  await page.setValue('textarea[aria-label="项目需求"]', input.idea)
  await page.clickText('检查并预览')
  await page.clickText('确认并开始构建')
}
```

保留既有首次声明“继续后重新提交”逻辑，因为首次声明会中断触发它的导航。

- [ ] **Step 3: 将成功断言限制在聊天正文**

把项目创建后的回复判断改为官方会话 slot：

```ts
const replyVisible = conversationContainsExpression('E2E_PONG')
await page.waitFor(replyVisible, {
  timeoutMs: 60_000,
  message: '本地项目会话没有收到确定性模型回复',
})
```

- [ ] **Step 4: 执行静态检查**

Run:

```powershell
rg -n "项目路径|构建权限模式|input\[type=\"checkbox\"\]" e2e/support/desktop.ts e2e/specs/provisioning-success.installer.e2e.ts
git diff --check
```

Expected: `rg` 无匹配；`git diff --check` 退出码为 0。

- [ ] **Step 5: 提交夹具修正**

```powershell
git add e2e/support/desktop.ts
git commit -m "fix(e2e): 对齐简化后的项目创建流程"
git push origin main
```

### Task 3: 实现真实会话发送与正文级切换

**Files:**
- Modify: `e2e/support/desktop.ts`
- Test: `e2e/specs/provisioning-success.installer.e2e.ts`

- [ ] **Step 1: 增加公开会话能力**

在 `DesktopHarness` 中增加：

```ts
createConversation(prompt: string): Promise<void>
assertSessionRoundTrip(markers: readonly string[]): Promise<void>
```

- [ ] **Step 2: 实现新建会话并等待实时回复**

实现必须点击真实“新建会话”按钮，兼容普通和 Hero 两种输入框，再点击真实发送按钮：

```ts
async createConversation(prompt: string): Promise<void> {
  await this.withWorkbenchTarget(async (page) => {
    await page.click('button[aria-label="新建会话"]')
    const composer = conversationComposerExpression()
    await page.waitFor(`${composer} !== null`, {
      timeoutMs: 30_000,
      message: '新会话输入框未出现',
    })
    await page.setValueFromExpression(composer, prompt)
    await page.click('button[aria-label="发送消息"]')
    await page.waitFor(conversationContainsExpression(prompt), {
      timeoutMs: 30_000,
      message: `用户消息未实时显示：${prompt}`,
    })
    await page.waitFor(conversationContainsExpression('E2E_PONG'), {
      timeoutMs: 60_000,
      message: '新会话没有收到确定性模型回复',
    })
  })
}
```

- [ ] **Step 3: 只识别真实会话行**

会话行表达式必须排除同为 `treeitem` 的工作区行：

```ts
function sessionRowsExpression(): string {
  return `Array.from(document.querySelectorAll('div[role="treeitem"]')).filter((row) => row.querySelector('button[aria-label^="会话“"]') !== null)`
}
```

- [ ] **Step 4: 按正文标记查找并双向切换**

对每个 marker 逐行点击，等待该行 `aria-selected="true"`，并在有限时间内检查 `[data-slot="conversation.session"]`。执行顺序为所有 marker 后再回到第一个 marker，证明不是单向打开：

```ts
const sequence = markers.length > 1 ? [...markers, markers[0]] : [...markers]
for (const marker of sequence) {
  const found = await this.openSessionContaining(page, marker)
  if (!found) throw new Error(`找不到包含正文标记的会话：${marker}`)
}
```

`openSessionContaining` 不得刷新页面、重新加载 iframe、点击工作区或调用 Runtime 内部 API。

- [ ] **Step 5: 为表达式值增加受控输入能力**

给 `CdpPage` 增加 `setValueFromExpression`，复用 React 受控输入需要的原型 setter 和 `input/change` 事件；现有 `setValue(selector, value)` 改为委托它，避免重复实现：

```ts
async setValueFromExpression(expression: string, value: string): Promise<void> {
  await this.evaluate(`(() => {
    const element = ${expression};
    if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement)) {
      throw new Error('Element does not accept a value');
    }
    const prototype = element instanceof HTMLInputElement
      ? HTMLInputElement.prototype
      : element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLSelectElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, ${JSON.stringify(value)});
    element.dispatchEvent(new Event('input', { bubbles: true }));
    element.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  })()`)
}
```

- [ ] **Step 6: 提交会话夹具实现**

```powershell
git add e2e/support/desktop.ts
git commit -m "feat(e2e): 验证打包会话实时切换"
git push origin main
```

### Task 4: 构建候选安装包并运行完整场景

**Files:**
- Test: `e2e/specs/provisioning-success.installer.e2e.ts`
- Test: `scripts/e2e/build-instrumented-setup.mjs`

- [ ] **Step 1: 构建启用测试能力的完整安装包**

Run:

```powershell
npm run e2e:setup:build
```

Expected: `e2e-artifacts/instrumented-setup.json` 指向存在的 NSIS 安装包、Runtime ZIP、签名状态和 Artifact Root。

- [ ] **Step 2: 运行安装包端到端测试**

Run:

```powershell
npm run e2e:installer
```

Expected:

```text
安装成功
首次启动成功
二次启动不下载 Runtime
自动创建本地项目
第一会话正文实时显示 E2E_PONG
第二会话正文实时显示 E2E_PONG
第一 → 第二 → 第一切换不刷新
退出重启后第一 → 第二 → 第一仍可切换
```

- [ ] **Step 3: 运行相关工程门禁**

Run:

```powershell
npm run test -- scripts/e2e
npm run build:web
npm run plugin:build
git diff --check
```

Expected: 全部通过。若根级测试仅因 Windows 符号链接权限 `EPERM` 失败，必须记录具体文件和通过数量，不能将其描述为产品回归。

- [ ] **Step 4: 记录结果并提交文档**

在本文末尾追加实际命令、通过数量、耗时、安装包路径和 SHA-256，然后提交：

```powershell
git add doc/plans/2026-08-24-packaged-session-realtime-e2e.md
git commit -m "docs(e2e): 记录打包会话回归结果"
git push origin main
```

## 验收边界

- 测试不得调用 `location.reload()`、Webdriver refresh 或重新设置 iframe 地址。
- 测试不得通过再次点击工作区来恢复会话。
- 测试不得读取本地 Session JSON 代替 UI 验证。
- 会话正文必须在 `[data-slot="conversation.session"]` 中出现；仅左侧标题出现不算通过。
- 会话行至少有两个，且每个都通过官方“会话…的操作”按钮结构识别。
- 重启使用同一安装目录和用户数据目录，不能重新安装或清空数据。

## 最终验证结果

尚未执行；完成 Task 4 后以实际证据更新本节。
