# DeepSeek Harness Desktop 自动上游发布与扩展底座设计

日期：2026-08-23
状态：已批准并实施
仓库：`XingAur/deepseek-harness-desktop`

## 1. 目标

在不购买 Apple Developer Program 证书的前提下，建立一条可恢复、可审计、不会覆盖用户数据的桌面端更新链路：

- GitHub Actions 每天检查 npm 上 `@deepseek-ai/dsh` 的 `latest` 版本。
- 发现新版本后，自动更新统一版本源、验证代码、提交版本变更、创建桌面版本标签并触发跨平台构建。
- Windows 发布带 Tauri 更新签名的安装包，应用内可检查、下载并安装更新。
- macOS Apple Silicon 发布未使用 Apple Developer ID 签名、未公证的 DMG；应用内提示新版本并打开可信的 GitHub 下载地址，由用户手动替换应用。
- 发布链路任一步失败时，不发布不完整的新版本，不覆盖已有 Release 和 Runtime 资产，并能在后续定时任务或手动任务中安全恢复。
- 保持 DeepSeek Harness 作为运行底座，同时形成 Codex、Claude、其他模型、插件、Skills 和 MCP 的可扩展架构边界。

本设计追求“自动化发布可验证、失败可恢复、更新不破坏数据”，不承诺未经真实平台构建与用户验收即可达到绝对零缺陷。

## 2. 当前事实与问题

当前项目已经具备以下基础：

- Tauri 桌面壳负责应用生命周期、安全、Profile 和更新。
- 托管 Runtime 固定安装 `@deepseek-ai/dsh`、官方 Web App 和桌面插件。
- Windows 已有 Tauri updater 检查、后台下载、立即安装和退出时安装能力。
- GitHub Release 已发布 Windows 安装包、签名和 `latest.json`。
- macOS Apple Silicon 已能生成 DMG，但没有 Apple Developer ID 签名和 Apple 公证。
- CI 已覆盖 Windows x64 与 macOS Apple Silicon，但仅在手动触发、PR 或 `desktop-v*` 标签时运行。

当前缺口：

- `@deepseek-ai/dsh` 版本硬编码在构建脚本中，不能每日自动跟踪。
- 桌面版本、Runtime 版本和依赖版本分散在多个文件，人工升级容易遗漏。
- 没有每日定时任务，也没有“版本已提交但 Release 构建失败”后的恢复状态机。
- macOS 没有适合无证书场景的应用内更新提示和可信下载入口。
- 当前文案与测试仍包含“尚无公开版本”等已过时假设。
- Codex、Claude、Skills、MCP 与第三方插件尚未形成明确的能力边界、权限和审计模型。

## 3. 本期范围

### 3.1 必须交付

1. 统一版本源和一致性检查。
2. npm 上游版本检查、升级计算和幂等更新脚本。
3. 每日自动同步工作流及手动重跑入口。
4. 可恢复的版本提交、标签、构建和 Release 发布状态机。
5. Windows 应用内自动更新链路保持可用并增加回归验证。
6. macOS 应用内更新提示、可信 GitHub 下载入口和明确的手动安装说明。
7. 跨平台发布清单 `desktop-release.json`，与 Windows Tauri `latest.json` 分离。
8. 数据保护、URL 白名单、日志脱敏和版本降级防护。
9. 自动化脚本、工作流契约、前端状态和 Rust 安全边界的测试。
10. 当前运行界面的更新流程产品审核，保留截图证据。
11. 使用本地 Harness 对最终相关 diff 做独立审阅；所有临时输出写入独立临时目录，不修改 Harness 默认数据库和项目数据。
12. 补充扩展平台架构文档，明确未来模型、插件、Skills 和 MCP 的接入层次。
13. 验证通过后在当前本地分支创建一次提交；不自动推送远端。

### 3.2 本期明确不做

- 不购买、申请或模拟 Apple Developer ID 证书。
- 不声称 macOS DMG 已通过 Apple 公证或能够完全绕过 Gatekeeper。
- 不在 macOS 上伪造“应用内静默安装”；下载后仍由用户手动替换应用。
- 不在本期实现 Codex、Claude 的账号登录、CLI 桥接或真实 API 凭证配置。
- 不在本期建设插件市场、远程插件安装或任意代码自动执行能力。
- 不修改用户 Profile、项目、会话、工作区、缓存或 Application Support 数据目录。
- 不自动推送当前开发提交，不绕过 GitHub 分支保护，不写入真实发布密钥。

## 4. 总体架构

```text
npm @deepseek-ai/dsh latest
             |
             v
upstream-sync.yml (daily/manual)
             |
             +-- inspect current release state
             +-- prepare version change
             +-- run platform-independent validation
             +-- commit + desktop-vX.Y.Z tag
             +-- dispatch desktop.yml at exact tag
                                      |
                         +------------+-------------+
                         |                          |
                  Windows x64                 macOS arm64
                         |                          |
              signed updater assets        unsigned/unnotarized DMG
                         +------------+-------------+
                                      |
                           verify complete asset set
                                      |
                          publish Release atomically
                                      |
                 latest.json + desktop-release.json
                         |                          |
               Windows in-app update        macOS notice + download
```

职责边界：

- GitHub Actions 负责发现版本、构建、验证和发布，不依赖用户的 Mac 每天开机。
- 桌面应用只消费已公开发布并通过格式与 URL 校验的元数据。
- Windows 安装只信任 Tauri updater 的签名资产。
- macOS 只打开固定 GitHub 仓库下的 HTTPS Release 地址，不在应用内执行 DMG。
- DeepSeek Harness Runtime 的更新通过新的 Runtime 资产交付，桌面应用和用户数据相互独立。

## 5. 单一版本源

新增 `release/versions.json` 作为发布版本的唯一人工编辑入口：

```json
{
  "schemaVersion": 1,
  "desktopVersion": "0.1.12",
  "runtimeVersion": "0.1.9-preview",
  "dshVersion": "0.1.0-rc.8",
  "nodeVersion": "24.14.0",
  "pnpmVersion": "11.7.0"
}
```

约束：

- 所有字段必须是非空字符串，版本格式必须通过显式校验。
- `desktopVersion` 使用三段 SemVer；自动上游同步只增加 patch 位。
- `runtimeVersion` 使用 `X.Y.Z-preview`；每次 DSH 上游升级增加 patch 位。
- `dshVersion` 必须是 npm 已发布的精确版本，禁止范围版本和隐式 `latest`。
- Node 和 pnpm 继续使用固定精确版本，避免每日构建同时引入多个变量。
- `package.json`、`package-lock.json`、Tauri 配置、Cargo 包版本及生成的发布元数据必须与统一版本源一致。
- 构建脚本只读取统一版本源，不再保留重复的版本字面量。
- 一致性测试必须列出具体不一致文件和期望值，防止静默漂移。

## 6. 上游版本准备脚本

新增 `scripts/prepare-upstream-release.mjs`，职责如下：

1. 从可注入的数据源读取 npm `@deepseek-ai/dsh` 的 `latest` 版本；生产模式访问 npm，测试模式使用本地 fixture。
2. 校验远端结果是合法 SemVer，拒绝空值、标签、范围和非版本响应。
3. 比较当前 `dshVersion`：
   - 相同：输出 `noop`，不改文件。
   - 更旧：拒绝降级并失败。
   - 更新：计算新的桌面 patch 版本和 Runtime preview patch 版本。
4. 一次性更新统一版本源及所有派生版本文件。
5. 更新 lockfile 时只执行与版本同步直接相关的命令，不升级无关依赖。
6. 输出机器可读 JSON，至少包含 `action`、旧/新 DSH 版本、桌面版本、Runtime 版本和待创建标签。
7. 同一输入重复执行必须得到 `noop`，不能继续增加版本。
8. 任一校验或文件更新失败时以非零状态退出；工作流不得提交半成品。

配套新增只读一致性脚本，支持 CI 在任何构建前验证统一版本源与派生文件。

## 7. 每日自动同步工作流

新增 `.github/workflows/upstream-sync.yml`：

- 触发方式：
  - 每天 `02:30 UTC`，即中国标准时间 `10:30`。
  - `workflow_dispatch` 手动触发，用于立即检查和故障恢复。
- 权限最小化：仅在需要时授予 `contents: write` 和 `actions: write`。
- 使用单一并发组，`cancel-in-progress: false`，避免新任务中断正在发布的版本。
- 拉取完整 Git 历史和标签，先检查当前版本对应的发布状态，再检查 npm 新版本。

状态优先级：

1. **当前版本提交存在但标签缺失**：重新验证后补建标签并触发构建。
2. **标签存在但 Release 缺失或仍是未完成草稿**：不再次增版，重新触发同一标签构建。
3. **Release 已公开但资产集合不完整或哈希冲突**：停止并报错，禁止自动覆盖公开资产。
4. **当前版本发布完整且 npm 无新版**：成功退出，不产生提交。
5. **当前版本发布完整且 npm 有新版**：准备新版本、验证、提交、建标签并触发构建。

新版本路径：

1. 运行版本准备脚本。
2. 运行统一版本一致性测试、脚本单元测试、前端/插件测试和平台无关构建。
3. 仅在验证全部通过后创建机器人提交，提交信息包含 DSH 旧版本和新版本。
4. 创建 `desktop-vX.Y.Z` 标签并原子性推送提交和标签；如仓库分支保护不允许机器人写入，任务明确失败，不绕过保护。
5. 使用 GitHub `workflow_dispatch` 在精确标签 ref 上触发 `desktop.yml`。不能依赖由 `GITHUB_TOKEN` 创建的普通标签事件自动唤醒另一工作流。

工作流不得自动删除标签、回退提交、覆盖 Release 或强制推送。

## 8. 跨平台构建与发布工作流

调整 `.github/workflows/desktop.yml`：

- 保留 PR 验证、手动构建和 `desktop-v*` 标签入口。
- `workflow_dispatch` 发布时必须以 `desktop-v*` 标签为 ref；标签版本必须与统一版本源、Tauri 和 package 版本一致。
- 在所有构建任务开始前读取并导出统一版本源，不允许脚本各自硬编码版本。
- 构建矩阵：
  - Windows x64：Runtime、NSIS 安装包、Tauri updater 签名和 SHA-256。
  - macOS Apple Silicon：Runtime、DMG 和 SHA-256；明确标记未使用 Developer ID 签名、未公证。
- Runtime Release 也先创建为草稿，按“归档在前、签名清单在后”的固定顺序上传两平台共 4 个精确资产，完整后才公开为 prerelease。
- Runtime 标签和资产保持不可变；同名资产存在时比较字节，一致则复用，不一致则失败。中断后只有归档时可从不可变归档重建并重新签名清单；只有清单却缺少归档时拒绝恢复。
- 桌面 Release 先创建为草稿，所有平台构建成功后再组装元数据并检查完整资产清单。
- 只有 Windows 与 macOS 任务都成功、Runtime 与 Tauri updater 签名/哈希/安装器契约验证都通过，且 Windows updater 签名确实匹配编译进应用的公钥，才将草稿发布为公开 Release。
- 失败时保留可诊断的草稿或构建记录，但不让不完整版本成为 `latest`。
- 重跑同一标签时跳过哈希一致的已有资产，只补充缺失资产；绝不静默覆盖哈希不同的资产。

预期完整资产至少包括：

- Windows 安装包。
- Windows updater 包及其 `.sig`。
- Windows Runtime 压缩包、Runtime 清单与哈希。
- macOS Apple Silicon DMG。
- macOS Runtime 压缩包、Runtime 清单与哈希。
- `latest.json`，仅描述可安全应用内安装的 Windows updater。
- `desktop-release.json`，描述 Windows 与 macOS 的展示和下载方式。

## 9. 跨平台发布清单

新增 `desktop-release.json`，示例结构：

```json
{
  "schemaVersion": 1,
  "version": "0.1.13",
  "tag": "desktop-v0.1.13",
  "publishedAt": "2026-08-23T00:00:00Z",
  "notes": "同步 @deepseek-ai/dsh 0.1.1-rc.2",
  "releasePageUrl": "https://github.com/XingAur/deepseek-harness-desktop/releases/tag/desktop-v0.1.13",
  "platforms": {
    "windows-x86_64": {
      "mode": "in-app",
      "url": "https://github.com/XingAur/deepseek-harness-desktop/releases/download/desktop-v0.1.13/FILE",
      "signatureUrl": "https://github.com/XingAur/deepseek-harness-desktop/releases/download/desktop-v0.1.13/FILE.sig",
      "sha256": "HEX"
    },
    "darwin-aarch64": {
      "mode": "manual-dmg",
      "url": "https://github.com/XingAur/deepseek-harness-desktop/releases/download/desktop-v0.1.13/FILE.dmg",
      "sha256": "HEX",
      "developerIdSigned": false,
      "notarized": false
    }
  }
}
```

生成规则：

- 文件名从真实构建产物读取，不手工猜测。
- 发布页、下载链接必须使用 `https://github.com/XingAur/deepseek-harness-desktop/` 固定前缀。
- `version`、`tag`、文件名和哈希必须与当前 Release 资产一致。
- 清单在所有资产上传完成后生成并上传，在发布草稿前做最终校验。
- 应用通过 `releases/latest/download/desktop-release.json` 读取已公开的最新版本。

## 10. 应用内更新体验

更新检查从 Runtime 启动成功与否中解耦，在桌面壳就绪后异步进行；网络失败、GitHub 不可达或元数据异常均不得阻塞现有 Runtime 和工作台。

### 10.1 Windows

- 继续使用 Tauri updater 的 `latest.json` 和签名验证。
- 检查到新版后展示版本号、更新说明和明确操作。
- 支持后台下载、立即安装、稍后处理和退出时安装。
- 安装前再次确认下载完成和签名有效。
- 更新失败保留当前可运行版本，展示可重试错误，不删除用户数据。
- 退出安装仍沿用不暴露数据删除选项的被动安装模式。

### 10.2 macOS Apple Silicon

- 使用 `desktop-release.json` 检查版本，不调用 Windows 的 Tauri 安装路径。
- 只有 `darwin-aarch64.mode` 为 `manual-dmg`、版本更高且 URL 校验通过时才展示更新弹窗。
- 弹窗明确显示：
  - 当前版本和新版本。
  - “未使用 Apple Developer ID 签名、未经过 Apple 公证”。
  - “下载 DMG 后退出应用，并将新版本拖入 Applications 覆盖旧应用；不要删除 Application Support 数据”。
  - Gatekeeper 可能要求右键打开或在“系统设置 > 隐私与安全性”中确认。
- 主按钮为“下载 DMG”，只通过受控的 Tauri opener/Rust 命令打开通过白名单校验的 HTTPS 地址。
- 提供“稍后提醒”，本次启动不反复弹出；下一次启动仍可检查。
- 不自动挂载 DMG、不执行 shell 命令、不替用户覆盖应用。

### 10.3 安全与降级规则

- 当前版本比较使用 SemVer，等于或更旧的远端版本不提示。
- 拒绝非 HTTPS、非 GitHub、仓库名不匹配、标签与版本不匹配的 URL。
- JSON 解析、超时、HTTP 状态和字段缺失都返回可诊断但不阻塞的错误状态。
- 日志不得打印令牌、签名私钥、完整环境变量或用户 Profile 内容。
- 更新 UI 必须覆盖：检查中、无更新、有更新、下载中、可安装、失败、稍后处理。

## 11. 数据保护

桌面更新与 Runtime 更新只替换程序和版本化 Runtime 资产，不修改以下用户数据：

- Profile 配置与凭证引用。
- 项目、工作区和本地目录。
- DeepSeek Harness 会话、历史记录和插件配置。
- Application Support 下的持久化数据。
- 用户自行安装或配置的扩展数据。

实现与测试要求：

- 安装器不得出现或默认勾选“删除应用数据”。
- 更新脚本不得对用户目录执行递归删除、重建或迁移。
- Runtime 资产按版本落盘；激活新版本失败时保留旧版本并允许回退到上次可用版本。
- macOS 文案必须区分“覆盖应用”与“删除数据目录”。

## 12. 扩展平台边界

未来能力按五层组织，避免将供应商、执行器和权限逻辑混在 UI 中：

1. **Desktop Shell**：窗口、生命周期、更新、Profile、安全提示、凭证引用和本地资源授权。
2. **Managed DSH Runtime**：Agent、Session、Tool、Model 调度和官方 Web UI。
3. **Provider Adapters**：DeepSeek、OpenAI-compatible、Anthropic 等模型协议适配；Codex/Claude 必须先明确是 API Provider、CLI Worker，还是账号/应用桥接，三者不能混为一种接入。
4. **Extension Plane**：优先复用官方 DSH 插件机制；Skills 与 MCP 通过受治理的适配器注册，声明能力、输入输出和权限。
5. **Governance Plane**：权限审批、能力契约、审计记录、超时/重试、回滚、评估和版本兼容。

扩展设计原则：

- Profile 隔离：Provider、凭证、MCP 服务和 Skills 启用状态按 Profile 隔离。
- 凭证只存系统安全存储或现有受控配置引用，不写仓库、日志或发布资产。
- 插件和 MCP 默认最小权限，文件、网络、命令执行分别声明。
- 每次调用保留来源、能力、结果和失败原因等审计信息，但不记录敏感正文。
- 第三方扩展必须有兼容版本范围、启停开关和失败隔离，不得拖垮桌面壳。
- Harness 作为治理与评估层，不能把模型输出本身当作验证结论；发布仍以确定性测试和平台构建证据为准。

本期新增一份独立架构文档，记录上述层次、首批 Provider/Skills/MCP 接入顺序和后续里程碑，但不提前写入未验证的真实接入代码。

## 13. 失败恢复与幂等性

| 场景 | 行为 | 是否发布 |
| --- | --- | --- |
| npm 不可达或响应非法 | 在修改文件前失败，保留日志 | 否 |
| 远端版本低于当前版本 | 拒绝降级并失败 | 否 |
| 没有新版本且当前 Release 完整 | 无改动成功退出 | 否 |
| 版本文件更新或测试失败 | 不提交、不建标签 | 否 |
| 提交已推送但标签缺失 | 下次任务校验后补建标签 | 否，直到构建完成 |
| 标签存在但构建失败 | 下次任务重派发同一标签 | 否 |
| 草稿存在且部分资产缺失 | 校验已有资产后补传缺失项 | 否，直到完整 |
| Runtime 草稿只有归档、清单缺失 | 从不可变归档重建清单、重新签名并验证 | 否，直到 4 个精确资产完整 |
| Runtime 草稿只有清单、归档缺失 | 拒绝伪造归档并停止 | 否 |
| Runtime 或 Desktop 公开版本资产不完整 | 视为不可变冲突并停止，不自动补写 | 否 |
| 同名资产哈希不一致 | 停止，要求人工处理 | 否 |
| 两个平台全部成功 | 校验元数据后公开草稿 | 是 |
| 应用更新检查失败 | 保持当前版本运行，可稍后重试 | 不影响已有版本 |
| 新 Runtime 启动失败 | 保留并回退上次可用 Runtime | 不破坏用户数据 |

自动任务不得以删除旧标签、旧 Release、旧 Runtime 或用户数据作为恢复手段。

## 14. 测试与验证方案

### 14.1 测试驱动顺序

每个实现增量先增加会失败的测试，再写最小实现并确认测试转绿：

1. 统一版本源读取与一致性。
2. 上游版本比较、升级、拒绝降级、非法输入、重复运行。
3. 工作流触发、权限、并发、标签校验、恢复和不覆盖资产的静态契约。
4. Windows/macOS 平台分流及更新状态模型。
5. macOS URL 白名单和清单解析。
6. 更新弹窗文案、按钮和错误/稍后状态。

### 14.2 必须执行的本地验证

- `npm run test`
- `npm run plugin:test`
- `npm run build:web`
- `npm run plugin:build`
- `npm run check`
- `cargo test --locked`
- 版本一致性检查。
- Windows NSIS 模板契约检查。
- Windows 图标检查。
- 发布元数据和工作流契约测试。
- `git diff --check`、完整相关 diff 审核和敏感信息扫描。

在当前 Mac 环境可行时，再执行 macOS Apple Silicon 的本地未公证构建/DMG 验证；如受网络、工具链或 GitHub 密钥限制，必须明确记录为未验证边界。

### 14.3 产品审核

运行可交互应用或确定性 UI fixture，实际截取并审核以下界面：

- Windows 有更新、下载中、安装就绪和失败状态。
- macOS 有更新、手动下载说明、稍后提醒和链接失败状态。
- 无更新及检查失败时工作台仍可使用。
- 更新弹窗在当前窗口尺寸、最小窗口和中文长文案下不遮挡关键操作。

审核必须基于截图和实际状态，不只阅读组件代码。发现的阻塞级或严重问题应在提交前修复并重新截图验证。

### 14.4 Harness 独立审阅

- 使用 `/Users/lym/WorkCode/ai/Harness` 的通用代码审阅/证据能力检查最终相关 diff。
- 审阅输入限定为本仓库改动、测试结果和发布设计，不启用 HIS/DFHIS 专用业务流程。
- 使用临时输出目录，不写 Harness 默认 SQLite、任务历史或本仓库用户数据。
- Harness 结论作为补充审阅证据；最终完成判断仍要求确定性测试和人工可核对的 diff。

### 14.5 真实平台边界

以下证据只有在提交推送到 GitHub 后才能最终获得，本地提交阶段不得伪称已验证：

- GitHub 定时任务能否使用仓库权限成功提交和建标签。
- GitHub-hosted Windows/macOS runner 的真实完整构建。
- Release 草稿的跨 job 资产汇总和自动公开。
- 已安装 Windows 旧版本到新版本的真实签名升级。
- 从公开 GitHub Release 下载 macOS DMG 的真实 Gatekeeper 体验。

## 15. 验收标准

本期本地实现完成必须同时满足：

1. 所有版本信息由统一版本源驱动，仓库不存在旧的重复 DSH 版本硬编码。
2. 上游同步脚本覆盖新版、无新版、降级、非法版本、幂等和恢复输入。
3. 每日工作流具备并发保护、最小权限、失败不发布和待发布版本恢复能力。
4. Windows 仍走签名应用内更新；macOS 明确走 DMG 手动更新，两者不会误入对方路径。
5. 更新失败不阻塞工作台，不删除或迁移任何用户数据。
6. 发布元数据只允许固定 GitHub 仓库的 HTTPS 地址并验证版本、标签、平台和哈希字段。
7. Release 只有在 Windows 与 macOS 资产完整后才公开，已有资产哈希冲突时停止。
8. 自动化测试、Rust 测试、Web/插件构建、安装器契约和 diff 检查通过。
9. 更新 UI 完成真实截图审核，严重问题已闭环。
10. 本地 Harness 完成独立相关 diff 审阅，结论和限制有记录。
11. README 清楚说明自动更新差异、macOS 无证书限制、数据保留和手动安装步骤。
12. 扩展架构文档清楚定义 Provider、插件、Skills、MCP 和治理边界。
13. 本地提交创建成功，提交中不包含密钥、构建产物、临时数据库或无关文件。

## 16. 部署前置条件与交付边界

GitHub 仓库需要具备：

- Actions 对仓库内容和工作流派发的写权限。
- 现有 Windows Tauri updater 私钥及密码 Secrets；这不是付费 Apple 证书，但必须妥善保管。
- 分支保护允许指定自动化身份提交版本，或改为由机器人创建 PR 后人工合并；工作流不会绕过保护。
- macOS 不配置 `APPLE_CERTIFICATE`、`APPLE_SIGNING_IDENTITY` 和公证凭证时，只发布未使用 Developer ID 签名、未公证的 DMG。

本地交付完成后只创建本地 Git 提交。推送、创建真实标签、运行 GitHub Release、修改仓库 Actions 权限或写入 Secrets 都属于外部写操作，需要用户另行明确授权或自行完成。
