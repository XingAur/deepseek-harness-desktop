# DeepSeek Harness 上游跟踪与升级策略

## 基线与双坐标

官方上游唯一地址是 `https://github.com/deepseek-ai/deepseek-harness.git`。本项目把它作为 Agent、会话、工具编排、计划与目标、Jobs、Skills、MCP、subagent、workflow、审批、提问、设置和 Web 工作台的基础实现，不维护第二套同名主流程。

`release/versions.json` 同时记录两个不能混淆的坐标：

- `dshUpstream`：最新确认的官方源码 tag 与不可变 40 位 commit，用于差异审查和兼容性分析；
- `dshVersion`：受管 Runtime 实际安装、测试和发布的精确 npm 版本。

源码 tag 已出现而对应 npm 包尚不可验证时，状态记为“上游源码领先 / 发行包待发布”。此时只更新 `dshUpstream`，不得伪造 npm 版本、从未审核源码临时构建正式 Runtime，或宣称桌面端已经使用该源码版本。

## 自动观察，人工采用

`.github/workflows/upstream-watch.yml` 在每 4 小时的第 17 分钟观察官方 Git tag 和 npm `latest`，也支持人工触发。它只能：

1. 在默认分支的只读检出上准备版本变更；
2. 执行版本一致性检查和 `npm run check`；
3. 把允许清单内的版本文件写入专用分支 `automation/deepseek-harness-upstream`；
4. 创建或刷新一条供维护者人工审核并合并的 Pull Request。

该工作流不得自动合并、打标签或发布，不得向默认分支直接推送，也不得启动安装包发布。`.github/workflows/upstream-sync.yml` 仅保留 `workflow_dispatch`，用于升级 PR 人工合并后的显式发布流程。

本地只读检查命令：

```bash
git ls-remote --tags https://github.com/deepseek-ai/deepseek-harness.git 'refs/tags/dsh-v*'
npm run release:versions:check
npm run release:prepare
```

`release:prepare` 只负责准备文件，不代表已采用、已发布或已通过真实平台验收。

## PR 审核门禁

维护者合并上游升级 PR 前必须确认：

- tag 指向的 commit 未发生变更，来源仍是唯一官方仓库；
- npm 精确版本存在，CLI 直接依赖闭包全部安装，版本、入口、许可证和 SHA-256 闭包摘要均通过；
- Runtime Session 契约、桌面插件测试、Web 构建和主门禁通过；
- 普通对话仍由官方 conversation surface 直接拥有；
- HIS Harness 仍为本次会话显式启用的实验能力，没有获得隐式 Git、云效、数据库、发布或部署写权限；
- 上游新增或修改的许可证、第三方声明和破坏性变更已经人工审阅。

官方仓库处于 developer preview 时，升级可能包含破坏性变化。即使自动测试通过，也不能省略人工 diff 审查和真实 Windows/macOS 验收。

## 失败、回退与数据保护

观察失败时不修改专用分支和现有 PR；保留日志，恢复网络或上游服务后人工重跑 watcher。准备或测试失败时停止在 PR 阶段，不触发发布。

升级后的 Runtime 只有在签名、哈希、健康检查和 Session 契约全部通过后才可激活；失败时继续使用 last-known-good Runtime。回退只切换版本化 Runtime，不删除、重置、迁移或重新初始化 Profile、Workspace、会话、项目、凭证、缓存和 HIS Harness 归档数据。

若官方 tag commit 被异常改写，准备脚本必须拒绝继续；维护者应核对官方公告和仓库记录，而不是覆盖本项目保存的不可变来源证据。

## 复制上游源码的边界

默认优先消费官方发布包及其完整依赖闭包。只有发布包无法提供明确所需行为时，才可复制具体上游文件，并同时记录来源 tag、commit、文件路径、MIT License 声明、必要修改和未来移除条件。不得无边界整仓复制后形成无法持续同步的私有分叉。
