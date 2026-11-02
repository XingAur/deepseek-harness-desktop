# 常见问题

[English](faq.en.md)

本页只回答与当前代码一致的问题。功能范围的权威来源是[用户指南](user-guide.md)和[官方壳与 overlay 架构](official-architecture.md)。

## 这是什么？和官方 DeepSeek Harness 什么关系？

本仓库维护**官方 DeepSeek Harness 桌面应用（`apps/desktop`）+ 一个本地 overlay 插件**，并把它打成便携包。桌面壳、安装器、更新、插件管理都是官方的，overlay 只补几项官方没有的能力（手机连接、模型卡片工具、Codex/xAI 登录、MCP 卡片、模型网络代理、Skills 展示）。

它不是 DeepSeek 官方产品，也没有官方背书。这是个人定制版，不提供支持承诺。

## 旧版本（自带壳）还能用吗？

本仓库不再维护它，也不再提供更新。能力去向与数据迁移见[从 2.0.x 迁移](migration-from-2.0.md)；旧实现只留在 git 历史与历史 Release 里。按旧文档操作会与当前代码对不上。

## 支持哪些平台？

- Windows x64：NSIS 安装包 + 便携 ZIP。
- macOS ARM64：DMG + ZIP，ad-hoc 签名。
- 没有 Intel macOS 包，没有 Linux 包。

## 需要自己装 Node.js 吗？

用便携包的用户不需要，运行时已经打进去了。要自己构建或跑开发流程则需要 Node.js `^22.19.0` 或 `>=24.0.0`、`corepack` 提供的 pnpm、Git 和 `bash`；Windows 便携包要求 Windows x64 主机加 PowerShell 7（`pwsh`），macOS 包要求 Apple Silicon 主机。

## Windows 提示"已保护你的电脑"怎么办？

包没有代码签名，SmartScreen 会拦。点「更多信息」→「仍要运行」。这是未签名构建的正常行为，不是文件损坏；可以用同目录 `.sha256` 核对下载完整性。

## macOS 打不开应用怎么办？

包是 ad-hoc 签名、未公证。两种方式：

1. 在「应用程序」里右键（或 Control-点击）应用 →「打开」→ 再确认一次。
2. 或者先尝试打开一次，然后到「系统设置 → 隐私与安全性」底部点「仍要打开」。

如果仍然被拦，用 `xattr -dr com.apple.quarantine <应用路径>` 清掉隔离属性后再打开。只在确认来源可信时这么做。

## 怎么更新？

手动更新，不接自动更新：下载新包，Windows 退出应用后覆盖安装（或替换 ZIP 解压目录），macOS 打开新 DMG 拖进「应用程序」覆盖。数据留在 `DSH_HOME`，不会因为换包丢失。

## `DSH_HOME` 是什么，会不会动我原来的 `~/.dsh`？

`DSH_HOME` 是 DSH 的数据目录（会话、设置、凭据、profile）。

| 场景 | 默认值 |
|---|---|
| 官方应用直接运行 | `~/.dsh` |
| 便携包（未设 `DSH_HOME`） | `~/.dsh-official` |
| 开发脚本 `start-official.sh` | `~/.dsh-official-dev` |

便携包默认不写 `~/.dsh`，所以可以和日常使用的官方 Harness 并存。overlay 的配置只在当前 home 的 `overlay-custom.yaml` 里，不写官方 `settings.yaml`。设置 `DSH_HOME` 后，官方应用和 overlay 一起切换。

## 中继怎么配？

先部署 [`relay-server/`](../relay-server/README.md)（Node ≥ 22 + TLS），然后在应用的手机连接弹窗里填 origin，或用 `DSH_OVERLAY_RELAY_ORIGIN` 环境变量覆盖。只接受纯 origin：公网必须 `https://`，`localhost`/`127.0.0.1`/`[::1]` 才允许 `http://`。

中继能看到转发的明文流量，只应部署在自己控制的服务器上。

## 怎么换官方基线？

只允许换到官方**已发布标签**，并且要在官方树外完成：

1. 确认目标的官方 tag 与 commit，例如 `dsh-v0.3.0`。
2. 改 `official-lock.json` 的 `repository`、`commit`、`version` 三个字段。
3. 快进检出并重新构建官方产物：

```bash
DSH_OFFICIAL_REF=dsh-v0.3.0 bash scripts/update-official.sh
```

`update-official.sh` 要求检出干净、当前在 `master` 分支、官方桌面未在运行；它只做 `merge --ff-only`，然后 `pnpm install --frozen-lockfile` 与 `pnpm run build`。

4. 跑一致性检查与 overlay 单测：

```bash
bash scripts/check.sh
```

5. 在 Windows x64 与 macOS ARM64 上各跑一次完整便携包构建与 smoke（`scripts/build-portable-windows-x64.ps1`、`scripts/build-portable-macos-arm64.sh`）。
6. 如果官方改动了 overlay 依赖的服务或插槽，改 overlay，**不要**在 `deepseek-harness/` 里打补丁或 cherry-pick。

## overlay 装了但界面没有变化，怎么排查？

按顺序看：

1. **`DSH_HOME` 指对了吗？** overlay 只读写当前 home。便携包默认 `~/.dsh-official`，开发脚本用 `~/.dsh-official-dev`，别去看 `~/.dsh`。
2. **profile 里有 overlay 吗？** 检查 `<DSH_HOME>/profiles/desktop/package.json` 的 `dsh.profile.bundles`（便携包为 `@deepseek-ai/dsh-overlay-custom`，开发为 `dsh-overlay-custom`）和 `cordis.patch.yml` 里的 `overlay-custom` 行。
3. **开发环境重装一次**：`bash scripts/install-overlay.sh`。如果脚本提示 desktop profile 由 Electron 管理，就在官方 Plugin Manager 里添加 `file:<仓库绝对路径>/overlay`。
4. **`lib/` 重新打了吗？** `overlay/lib/` 是 gitignored 的构建产物，缺了就什么都不会加载：

```bash
cd overlay && npm run bundle:host && npm run bundle:client && npm run check
```

5. **重启应用**：bundle 与插槽只在启动时组合，改完必须重启。
6. **看日志**：开发运行看 `~/.dsh-official-dev/official-desktop.log`，正常会有一行 `dsh-overlay-custom host mounted`。没有这行说明 Host 面没挂上，通常是 `webServer` 或 `connection` 服务缺失（官方改名了）。
7. **只有界面元素缺失、路由还在？** 说明官方 Client 插槽改名了。Host 路由（`/api/desktop/...`）与 Client 插槽是两件事，可以分别失效。
8. **便携包里还有旧的 `file:` 依赖吗？** 打包后的应用首次创建/升级 profile 会自动清理历史 `file:` 依赖与软链，并写 `portable-overlay-state.json`。如果这份文件在但界面仍无变化，回到第 2 步。

## 侧栏提示有新版本，但「本仓库 Releases」里没有新包？

这是正常的过渡状态：overlay 分别检查官方与本仓库。官方先发布新基线（例如 `0.2.2-...`）时，本仓库往往还没有对应构建，提示会先显示「官方最新」，本仓库一栏仍是旧版本。等本仓库按新基线发布 `official-v<版本>_NN` 后，重新检查即可看到两个来源都更新。

## 为什么不自动安装更新？

官方安装包有签名与 publisher 校验，本仓库的便携包未签名，不能直接套用官方自动更新器；直接安装官方更新还会把 overlay 构建替换成纯官方版。所以 overlay 只做提示和跳转，安装始终由你手动完成。详见[用户指南](user-guide.md)的「更新」一节。

## 手机扫码后页面打不开？

- 二维码/链接里的 origin 必须能被手机访问到，且证书有效。
- 应用重启后旧链接立即失效，需要重新点入口生成。
- 状态是「等待远程连接」而手机一直打不开时，先确认中继的 `/healthz` 正常、桌面到中继的 WebSocket 没被企业网络拦截。

## 模型勾选/登录后为什么还要重启？

模型选择要影响官方 Loader 的模型列表，overlay 把它写进 profile 的 `cordis.patch.yml`。官方 Loader 在启动时组合配置，所以保存后需要重启应用才生效。

## 在哪里反馈问题？

提 [GitHub Issue](https://github.com/LiuYuMiao-M/dsh-desktop/issues/new/choose)，附上：操作系统与架构、包文件名（含 `buildVersion`）、`DSH_HOME` 路径、复现步骤、相关日志片段。涉及凭据的内容请先脱敏。
