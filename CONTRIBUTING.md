# 参与贡献

DSH Desktop 由官方 DeepSeek Harness 桌面应用（`apps/desktop`）和本仓库的 Cordis overlay 组成。这个仓库只维护 overlay、便携包构建脚本、手机中继和文档；官方源码保持未修改。

开始写代码前，请先读 [docs/official-architecture.md](docs/official-architecture.md)。它说明了依赖方向、功能归属和打包边界，比本文更详细。

## 可以贡献什么

- `overlay/`：官方没有的桌面能力，例如手机中继、模型工具、OAuth、MCP、代理和 Skills 展示。
- `scripts/`：官方源码同步、便携包构建、封装与校验。
- `relay-server/`：手机中继服务端及其协议。
- 文档、翻译和问题复现。

以下能力由官方实现，不在本仓库修复或重做：账号登录、任务调度、终端、Office 预览、插件管理、会话/工作区、Web/CLI 运行时、安装器与更新。遇到这些问题请反馈给上游。

## 开发环境

需要 Node.js `^22.19.0` 或 `>=24.0.0`、Git、Bash，以及官方 checkout 使用的 pnpm。overlay 单独使用 npm。

```bash
# 官方源码（首次）
bash scripts/bootstrap.sh

# 快进到锁定标签并构建官方产物
DSH_OFFICIAL_REF=dsh-v0.2.1-rc.5 bash scripts/update-official.sh

# 启动官方桌面，开发数据目录为 ~/.dsh-official-dev
bash scripts/start-official.sh

# 官方桌面首次启动生成 profile 后，安装 overlay
DSH_HOME=$HOME/.dsh-official-dev bash scripts/install-overlay.sh

# overlay 单测
cd overlay && npm ci && npm run check

# 全量检查：overlay + 构建脚本 + 官方 checkout 与锁文件一致性
cd .. && bash scripts/check.sh
```

便携包构建：

```bash
pwsh -NoProfile -File scripts/build-portable-windows-x64.ps1   # Windows x64
bash scripts/build-portable-macos-arm64.sh                     # macOS ARM64
```

根 `package.json` 还提供 `npm run official:*`、`npm run overlay:install`、`npm run check`、`npm run build:win` 和 `npm run build:mac` 别名。

## 仓库边界

- `deepseek-harness/` 是 gitignored 的独立官方 clone，**禁止修改**。换基线只能快进到官方标签并更新 `official-lock.json`；不要打补丁、cherry-pick 或提交本地功能。
- overlay 是唯一加载进桌面的自研代码。它只能使用官方公开的 Cordis Host 服务和 Client 插槽，不能 import 官方源码树、读取官方内部路径或引入 Electron API。
- overlay 必须可缺席：官方服务缺失或改名时，对应入口显示不可用，官方桌面仍要能启动。
- 打包只在 `git archive` 解出的一次性 staging 中注入 overlay，不修改官方源码树。
- 数据目录隔离：开发用 `DSH_HOME=~/.dsh-official-dev`，便携包用 `~/.dsh-official`，不得读写用户的 `~/.dsh`。`install-overlay.sh` 在未设置 `DSH_HOME` 时会回退到 DSH 默认目录，务必显式传入。
- 不要给 overlay 或中继增加遥测、内容上报或隐式上传；中继只转发配对会话，不记录会话内容。

## 提交与 PR

- 提交信息使用 conventional commits，例如 `fix(overlay): ...`、`docs: ...`。
- 提交前至少运行 `cd overlay && npm ci && npm run check`；改动构建脚本或锁文件时再运行 `bash scripts/check.sh`。
- 改动打包、ASAR 重封、签名或产物布局时，在真实的 Windows x64 / macOS ARM64 上跑一次对应构建与 smoke；Linux 单测通过不等于安装包已验证。
- 文档改动保持中英成对，并更新对应的 `README.i18n.yaml` 或 `PRIVACY.i18n.yaml` hash 记录。
- PR 请说明改动内容、动机和验证方式。CI 只覆盖 overlay 与构建脚本；完整产物验证由发布工作流在真实平台完成。
- 不要提交 `deepseek-harness/`、`dist/`、`node_modules/`、日志、凭据、诊断包或构建缓存。

## 行为准则

请保持友善、尊重，就事论事。完整的[参与者公约](CODE_OF_CONDUCT.md)适用于所有项目空间。
