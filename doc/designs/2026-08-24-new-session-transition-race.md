# 新建会话同步过渡设计

日期：2026-08-24
状态：已实施并通过 Windows 打包端到端验证

## 1. 背景

Windows 打包端到端测试已经稳定复现“新建会话后消息暂时不可见，刷新后恢复”的问题。

协议日志证明消息和模型回复没有丢失：

1. 用户点击“新建会话”后，官方 Runtime 异步调用 `session.create`。
2. 新会话创建完成前，旧会话的输入框仍然可编辑、可发送。
3. 用户消息、`user/message`、`assistant/message` 和确定性回复都进入旧 Session。
4. 新的空白 Session 随后才收到 `host/session-added` 并被选中。
5. 页面最终停留在新的空白会话，因此用户误以为消息消失；刷新或重新选择旧会话后，持久记录会重新出现。

这不是 WebSocket 丢帧、服务端未持久化或会话历史损坏，而是“创建新会话”和“旧输入框继续发送”之间的交互竞态。

## 2. 目标

- 点击任意官方“新建会话”入口后，旧会话必须同步退出可发送状态。
- 保留官方目标 Workspace 选择规则：显式 Workspace 优先，其次当前 Session 所属 Workspace，最后最近使用的 Workspace。
- 新 Session 创建成功后仍由官方 Runtime 完成列表写入、Session Binding 和打开动作。
- 不增加轮询，不以刷新页面作为恢复手段，不维护第二份会话权威状态。
- 不修改安装后 `node_modules`，避免上游 Runtime 升级覆盖修复。

## 3. 方案选择

采用桌面插件同步过渡守卫。

桌面插件在 Advanced Shell 激活时包装官方 `workspaces.startSession`：

1. 在清空当前选择前，按照官方规则解析目标 Workspace。
2. 同步调用 `sessions.clear()`，使界面立即进入无当前 Session 的新会话状态，旧输入框随之卸载。
3. 将已解析的显式 Workspace ID 传给原始 `startSession`。
4. 原始方法继续负责复用空白 Session、创建 Session、写入官方列表并调用 `sessions.open()`。
5. 插件作用域销毁时恢复原始方法，避免重复包装和跨 Generation 残留。

没有 Workspace 时仍调用官方原始方法，由其保持既有清空语义。

## 4. 组件边界

新增 `new-session-transition.ts`，只负责：

- 解析官方规则下的新会话目标 Workspace；
- 安装和卸载 `startSession` 包装；
- 保证 `sessions.clear()` 发生在异步创建之前。

`advanced-shell.ts` 只负责在 Cordis Effect 生命周期内安装守卫。

`contracts.ts` 仅补齐实际使用的最小契约：

- `WorkspacesLike.startSession(workspaceId?)`
- `SessionsLike.list.getSnapshot().current`
- `SessionsLike.clear()`
- `WorkspacesLike.refresh()`
- `SessionsLike.refresh()`

真实安装包验证还发现：新 Session 的正文已能实时显示时，Workspace 归属和会话标题投影仍可能晚一个基线刷新。因此过渡守卫会监听新会话的空白、运行和标题签名，只在签名实际变化时同步官方 Workspace/Session 基线。这是有界的事件驱动协调，不是定时轮询，也不建立第二份会话数据。

不会让 UI 直接消费 DSH 内部事件，也不会新增本地 Session Store。

## 5. 数据流

```text
点击新建会话
  → 桌面过渡守卫解析目标 Workspace
  → sessions.clear() 同步卸载旧会话输入框
  → 官方 workspaces.startSession(目标 Workspace)
  → connectWorkspace 复用或创建空白 Session
  → sessions.open(新 Session)
  → 新会话投影变化时同步 Workspace/Session 基线
  → 官方 Session Event 投影驱动标题、消息和回复
```

## 6. 异常处理

- `startSession` 的创建失败仍由官方 Runtime 输出诊断并保持新会话空状态，不回切旧会话，避免用户误把后续输入继续发给旧 Session。
- 安装守卫时若发现方法已被当前插件包装，直接复用现有包装，避免重复清空。
- Effect 清理只在当前方法仍是本次包装时恢复原方法，防止覆盖其他 Generation 后续安装的新实现。

## 7. 测试设计

单元测试覆盖：

1. 显式 Workspace 优先。
2. 未显式指定时使用当前 Session 所属 Workspace。
3. 当前 Session 无归属时使用最近 Workspace。
4. `sessions.clear()` 在原始 `startSession` 之前同步发生。
5. 没有 Workspace 时保留官方空状态行为。
6. Effect 销毁后恢复原始方法。

打包端到端测试覆盖：

1. 安装候选 Windows 包。
2. 首次启动准备 Runtime，第二次快速启动。
3. 创建本地项目并完成第一会话。
4. 点击真实“新建会话”按钮后立即输入第二条消息。
5. 第二条消息只能进入新 Session，且无需刷新即可显示。
6. 第一、第二会话往返切换时正文同步出现。
7. 重启桌面应用后再次完成相同切换。

## 8. 非目标

- 不修改官方 DSH Runtime 源码或已安装依赖。
- 不恢复此前删除的通用 Session Recovery Guard。
- 不通过定时刷新 `session.list` 掩盖竞态。
- 不改变 Session Event 作为会话内容唯一事实来源的架构。

## 9. 验收标准

- 用户点击新建会话后，旧会话输入框在同一事件循环内不可继续发送。
- 第二会话的 `session.prompt`、`user/message` 和 `assistant/message` 使用新 Session ID。
- 左侧会话标题与右侧正文无需刷新即可出现。
- 打包端到端测试在首次启动、热启动和应用重启后三个阶段全部通过。

## 10. 实施与验证结果

- 桌面插件测试：23 个测试文件、89 个用例通过。
- Windows 安装包端到端测试：2/2 通过。
- 会话链路：创建第二会话、发送消息、标题出现、两会话往返、退出重启后再次往返，全程未刷新页面。
- 最终通过运行的时延记录：首次 Generation 63,872 ms，热启动 6,599 ms。
- 候选安装包：`e2e-artifacts/DeepSeek-Harness-Desktop-E2E-Web-Setup-x64.exe`。
- 全仓 `npm run check` 已执行；本机未开启 Windows 符号链接权限，11 个依赖 `symlink` 的安全/包导出用例以 `EPERM` 结束。其余根测试 294 项、插件 89 项、Web/插件/Agent 构建均通过；未为规避本机权限而降低符号链接安全校验。
