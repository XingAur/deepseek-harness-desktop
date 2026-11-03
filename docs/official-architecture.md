# 架构：官方壳 + 本地 Overlay

本仓库只维护**一个产品**：官方 DeepSeek Harness 桌面应用（`apps/desktop`）+ 一个本地 Cordis overlay 插件。
仓库不再包含自写的 Electron 壳、自有安装器或自有更新器——那些职责全部还给官方实现。

## 仓库结构

| 路径 | 职责 |
|---|---|
| `overlay/` | 唯一的自研代码：Cordis Host/Client bundle，叠加中继、模型工具、MCP、OAuth、Skills、代理 |
| `scripts/` | 官方源码同步与便携包构建：bootstrap / update-official / 打包 / 封存 / 校验 / smoke |
| `official-lock.json` | 官方仓库地址、锁定提交、锁定版本；本地与 CI 都只接受该版本 |
| `relay-server/` | 手机中继服务端（Node），供 overlay 的「手机连接」使用 |
| `docs/` | 架构、用户指南、FAQ；面向用户的说明 |
| `.github/workflows/` | `ci.yml`（overlay 与构建脚本检查）与 `release-official-portable.yml`（Windows/macOS 便携包发布） |

官方源码检出在 `deepseek-harness/`（gitignored 的独立 Git 仓库），**禁止修改**那棵树来加功能。

## 依赖方向（硬规则）

1. overlay 只能使用官方公开的 Cordis 服务与 Client 插槽，不 import 官方源码树、不读取官方内部文件路径。
2. 官方 `apps/desktop`、`packages/**` 一律不改；打包只在一次性 staging（`git archive` 解出的临时树）里注入 overlay 与打包适配。
3. overlay 不得成为官方桌面的启动依赖：`authorization`、`connection`、`webServer` 等官方服务缺失或改名时，登录/中继入口显示不可用，官方桌面仍须正常启动。
4. overlay 不引入 Electron API（`electron` import 会被检查拒绝），它只是普通 Web Host/Client 插件。
5. 数据目录隔离：开发用 `DSH_HOME=~/.dsh-official-dev`，便携包默认 `~/.dsh-official`，不得写入用户日常的 `~/.dsh`。

## 加载方式

overlay 通过 `dsh.bundle.patch` 挂载，而不是改官方源码：

- `overlay/cordis.patch.yml` 向官方 desktop profile 追加 `dsh-overlay-custom` 一行；
- `overlay/package.json` 声明 `dsh.client.inject`（侧栏、设置、模型卡片插槽）与 `platform: web`；
- 打包时 overlay 作为 `@deepseek-ai/dsh-overlay-custom` 放进官方 runtime，首次启动/升级幂等修复 profile（清理历史 `file:` 依赖与源码软链，保留其它 bundle/patch 行）。

## 版本锁定与同步

- 基线见 [official-lock.json](../official-lock.json)：官方仓库 + 提交 + 版本（当前 `0.2.1-rc.5`）。
- 本地开发：`bash scripts/bootstrap.sh` 首次克隆，`DSH_OFFICIAL_REF=dsh-v<版本> bash scripts/update-official.sh` 快进到已发布标签并自动 `pnpm install --frozen-lockfile` + `pnpm run build`。
- 换基线只允许快进官方标签、更新锁文件，再做一次完整便携包构建与 smoke；**不允许**在官方树里打补丁或 cherry-pick 本地功能。

## 构建与发布

```
bootstrap/update-official  → 官方 checkout + 构建产物
build-portable-windows-x64.ps1 / build-portable-macos-arm64.sh
  → staging（git archive）→ 注入 overlay → 重新打包 ASAR
  → 路径清理与原生签名校验 → finalize（对账库存、更新 ASAR 摘要）
  → smoke（真实启动便携包）→ dist/ 产物 + sha256 + build.json
```

- 产物：Windows x64 未签名 NSIS EXE + ZIP；macOS ARM64 ad-hoc 签名 DMG + ZIP。
- 更新方式：**手动更新**，不接入官方自动更新 feed；发布 Release 标记为 prerelease。
- 版本号：`<锁定官方版本>_<两位迭代号>`（例如 `0.2.1-rc.5_01`）。打包脚本把它烘焙进 overlay，并用于产物名与发布 tag。
- 详见 [官方打包脚本](../scripts/) 与 [发布工作流](../.github/workflows/release-official-portable.yml)。

## 更新提示

overlay 的 Host 面提供只读的更新检查（`/api/desktop/updates/state`），Client 面在侧栏渲染「更新」入口：有可用版本时显示红点，点开显示当前版本、官方最新、本仓库最新与对应的 release notes。

- 官方最新：GitHub 上 `deepseek-ai/deepseek-harness` 的非 draft release，tag 形如 `dsh-v*`，按 `published_at` 取最近发布的一条；不同发布通道可能分叉，不能只按 semver 取最大值。
- 本仓库最新：本仓库的非 draft release，tag 形如 `official-v<buildVersion>`。
- 比较规则：官方版本按发布时间选最新，再按 semver 判断是否高于当前基线；本仓库版本先比官方基线，再比两位迭代号。
- 更新内容优先提取 release body 的中文段落并转为纯文本；本仓库发版说明使用中文。
- 检查在启动 5 秒后、以及每次打开更新面板时触发；结果缓存 30 分钟。网络失败只显示「暂时无法检查」，不影响应用。`overlay-custom.yaml` 的 `updates.autoCheck: false` 可关闭启动检查。
- 只提示，不自动下载、不自动安装：有更新时「立即更新」打开对应 Release 下载页；官方安装包有签名与 publisher 校验，本仓库的未签名便携包不能套用官方更新器，安装会替换掉 overlay 构建。

## 功能归属

官方自带、overlay 不重复实现：账号登录、任务调度、终端、Office 预览、插件管理、会话与工作区、Web/CLI 运行时、安装器与更新。

overlay 只做官方没有的：云中继手机控制与配对页、模型「测试连接/拉取模型/勾选显示」、Codex/xAI OAuth 登录、MCP 卡片管理、模型网络代理、Skills 目录展示、版本与更新提示。

## 验证

```bash
bash scripts/check.sh            # overlay 单测 + 构建脚本语法 + 与锁文件一致性
cd overlay && npm ci && npm run check
```

完整验证需要在 Windows x64 / macOS ARM64 上跑一次便携包构建与 smoke；Linux/macOS 单测通过不等于真实安装包已验证。
