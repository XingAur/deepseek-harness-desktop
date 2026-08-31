# Codex、更新器与插件市场问题修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复用户确认的 1、2、3、4、5、6、7、8 项问题。

**Architecture:** 保持现有 Codex app-server、DSH 附件服务、Tauri 更新器和静态插件目录结构。Codex 适配器增加真实取消、失败状态与上下文边界处理；macOS 更新流程下载并校验 DMG 后再打开；插件目录刷新显式失效缓存。Apple Developer ID 签名与公证属于发布凭据边界，本计划只修复代码与发布检查，不能伪造凭据。

**Tech Stack:** TypeScript、Vitest、Rust/Tauri、Cargo tests、GitHub Actions。

## Global Constraints

- 主聊天与 Agent 工作台统一权限映射：请求批准弹出审批，智能批准使用 `on-request`，完全访问权限使用 `never`。
- 不删除、回退或覆盖工作区已有修改。
- 不提交、推送、创建 Release 或上传发布包。
- macOS DMG 必须在本地下载后校验大小和 SHA-256；未签名/未公证状态必须继续明确提示。
- 每个行为修复先增加失败测试，再实现，再运行专项测试。

---

### Task 1: Codex 权限、取消、超时、失败状态和消息边界

**Files:**
- Modify: `packages/dsh-plugin-desktop/src/server/codex-chat.ts`
- Modify: `packages/dsh-plugin-desktop/src/index.ts`
- Test: `packages/dsh-plugin-desktop/tests/codex-chat-adapter.spec.ts`

- [ ] 为权限映射、取消传播、失败 completion 和历史消息裁剪增加失败测试。
- [ ] 让 app-server 使用会话权限配置；AbortSignal 触发时发送 `turn/interrupt` 并关闭通道；增加有限超时。
- [ ] 检查 `turn/completed` 的 status，只把 completed 当作成功。
- [ ] 避免向已 resume 的线程重复发送完整历史，只发送当前用户轮次；限制单轮图片输入。

### Task 2: 真实附件服务与模型目录边界

**Files:**
- Modify: `packages/dsh-plugin-desktop/src/index.ts`
- Test: `packages/dsh-plugin-desktop/tests/codex-chat-adapter.spec.ts`

- [ ] 覆盖只有 `ctx.get('attachments')` 的运行时上下文。
- [ ] 对未知/过期模型和模型目录失败提供稳定的默认推理能力与可诊断错误。
- [ ] 补齐 `max`、`ultra` 推理强度显示名称，并确保只发送模型实际支持的 effort。

### Task 3: macOS 更新下载校验与发布边界

**Files:**
- Modify: `src-tauri/src/app_update/model.rs`
- Modify: `src-tauri/src/app_update/manual.rs`
- Modify: `src-tauri/src/app_update/controller.rs`
- Modify: `src-tauri/src/commands.rs`
- Modify: `src/runtime-client.ts`
- Modify: `src/runtime-contract.ts`
- Modify: `src/App.tsx`
- Test: `src-tauri/src/app_update/manual.rs`
- Test: `src/App.test.tsx`

- [ ] 增加 macOS DMG 下载到受控目录、大小限制和 SHA-256 比对。
- [ ] 下载成功后打开本地 DMG，失败时展示可恢复错误。
- [ ] 继续禁止未签名/未公证 DMG 进入应用内自动安装。
- [ ] 更新界面准确区分“已下载待手动替换”和“应用内安装”。

### Task 4: 插件市场刷新与安装诊断

**Files:**
- Modify: `src-tauri/src/plugin_market.rs`
- Modify: `packages/dsh-plugin-desktop/src/client/extensions/PluginMarket.tsx`
- Test: `src-tauri/src/plugin_market.rs`
- Test: `packages/dsh-plugin-desktop/tests/plugin-market.spec.tsx`

- [ ] 增加显式 reload 入口，刷新时重新读取目录快照。
- [ ] 保留分页和安装状态，不因刷新清空用户当前任务。
- [ ] 合并 stdout/stderr 并保留超限/读取失败诊断。

### Task 5: 单包 typecheck 与预览边界

**Files:**
- Modify: `packages/dsh-plugin-desktop/package.json`
- Test: package scripts / build verification

- [ ] 让直接运行桌面插件 typecheck 自动确保 agent-adapter 类型产物存在。
- [ ] 保持 preview 明确标注为原型，不把 preview 当作运行时验收依据。

### Task 6: 验证与交接

- [ ] 运行 Codex、插件市场和 Agent 定向测试。
- [ ] 运行桌面插件构建、typecheck、Rust updater/plugin-market 测试和 diff 检查。
- [ ] 复核权限映射与 Agent 工作台一致，说明 Apple 签名/公证仍需发布凭据与 CI 配置。
