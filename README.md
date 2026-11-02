# DSH Desktop

[English](README.en.md)

DSH Desktop 是 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 官方桌面应用（`apps/desktop`）的本地构建，叠加本仓库维护的 Cordis overlay 插件。Electron 壳、账号、任务调度、终端、Office 预览、插件管理、会话/工作区、安装器与更新均来自官方实现；本仓库只维护 `overlay/`、便携包构建脚本和手机中继服务端。

- 官方基线：`0.2.1-rc.5`，commit `b830ec950faba0a46dda564be3e2d854afed94f1`（见 [official-lock.json](official-lock.json)）
- 平台：Windows x64、macOS ARM64
- 架构：[docs/official-architecture.md](docs/official-architecture.md)
- License：MIT

## 下载

从 [GitHub Releases](https://github.com/XingAur/deepseek-harness-desktop/releases) 获取最新构建。发布标记为 **prerelease**。

| 平台 | 文件 | 形式 |
| --- | --- | --- |
| Windows x64 | `deepseek-harness-overlay-<build>-win-x64-unsigned-manual.exe` | NSIS 安装包 |
| Windows x64 | `deepseek-harness-overlay-<build>-win-x64-unsigned-manual.zip` | 便携 ZIP |
| macOS ARM64 | `deepseek-harness-overlay-<build>-mac-arm64-adhoc-manual.dmg` | DMG |
| macOS ARM64 | `deepseek-harness-overlay-<build>-mac-arm64-adhoc-manual.zip` | 便携 ZIP |

每个产物都带 `.sha256` 与 `.build.json`。`<build>` 是「锁定官方版本\_两位迭代号」，例如 `0.2.1-rc.5_01`。

发布产物未做商业代码签名：

- Windows 首次运行可能出现 SmartScreen 提示，选择「更多信息 → 仍要运行」。
- macOS 为 ad-hoc 签名且未公证，首次打开需右键 →「打开」，或在「系统设置 → 隐私与安全性」中允许。
- 更新方式为手动：下载新构建后重新安装或覆盖，不依赖自动更新 feed。

## 相对官方的定制

官方桌面自带账号登录、任务调度、终端、Office 预览、插件管理、会话/工作区、Web/CLI 运行时、安装器与更新。overlay 只补官方没有的部分：

- **手机远程控制**：侧栏「手机连接」配对页 + [`relay-server/`](relay-server/) 云中继。手机经 HTTPS 中继访问本机 Web UI，桌面主动建立出站隧道，无需公网暴露本机。配对按次开启，支持按工作区分组查看会话、新建/继续会话、发消息、停止任务、切换模型/预设，以及处理审批和提问；提供二维码与配对链接。
- **模型工具**：提供商卡片上的「测试连接」「拉取模型列表」「勾选显示在模型选择器中的模型」。拉取结果回填卡片并保存，重启后生效；测试失败显示上游错误。
- **Codex / xAI OAuth 登录**：在对应提供商卡片上发起官方 OAuth 登录。官方登录接口缺失时按钮显示不可用，不影响应用启动。
- **MCP 卡片管理**：设置中以卡片添加、编辑、测试 MCP 服务器，内置常用模板；配置保存在 `overlay-custom.yaml`，重启后由 overlay 挂载。
- **模型网络代理**：模型请求可选择跟随系统、手动 HTTP(S) 代理或直连，支持绕过列表和连通性测试；不影响 MCP 或其他工具。
- **Skills 目录展示**：设置中的 Skills 区块列出当前 profile 可用的技能目录。
- **版本与更新提示**：侧栏「更新」入口带红点，检查官方 Releases 与本仓库 Releases，显示两边的版本号（`官方版本_迭代号`）和更新内容，可跳转对应发布页。只提示，不自动安装。

### 中继配置

桌面端在「手机连接」里填写中继地址，保存到 `$DSH_HOME/overlay-custom.yaml` 的 `remoteRelayOrigin`；也可用 `DSH_OVERLAY_RELAY_ORIGIN` 覆盖。中继地址只接受 `https://` origin（本机开发可用 `http://127.0.0.1`）。部署与协议见 [`relay-server/`](relay-server/)。

## 快速开始

### 安装版或便携版

1. 从 Releases 下载对应平台的文件。Windows 运行 `.exe` 安装，或解压 `.zip`；macOS 打开 `.dmg` 拖入「应用程序」，或解压 `.zip`。
2. 首次启动按上面的系统安全提示放行。
3. 应用数据默认写入 `~/.dsh-official`，不读取也不写入 `~/.dsh`；可与官方 DeepSeek Harness 安装共存。
4. 更新时重新下载新构建，按同样方式覆盖安装或替换便携目录。发布为 prerelease，更新是手动的。

### 验证下载

```bash
sha256sum -c <artifact>.sha256                 # Linux / macOS
Get-FileHash <artifact> -Algorithm SHA256      # Windows PowerShell，与 .sha256 对照
```

`.build.json` 记录构建版本、官方 commit、overlay 摘要与 `update: manual`。

## 开发

前置条件：Node.js `^22.19.0` 或 `>=24.0.0`、Git、Bash，以及官方 checkout 使用的 pnpm。overlay 使用 npm。Windows 便携包构建需要 Windows x64 + PowerShell 7；macOS 便携包构建需要 macOS ARM64。

```bash
# 1. 首次拉取官方源码到 deepseek-harness/（gitignored，独立 clone）
bash scripts/bootstrap.sh

# 2. 快进到锁定标签，安装依赖并构建官方产物
DSH_OFFICIAL_REF=dsh-v0.2.1-rc.5 bash scripts/update-official.sh

# 3. 启动官方桌面；DSH_HOME 固定为 ~/.dsh-official-dev
bash scripts/start-official.sh

# 4. 首次启动生成 profile 后，把 overlay 装进开发 profile
DSH_HOME=$HOME/.dsh-official-dev bash scripts/install-overlay.sh

# 5. overlay 单测
cd overlay && npm ci && npm run check

# 6. 全量检查（要求官方 checkout 与 official-lock.json 一致且干净）
cd .. && bash scripts/check.sh

# 7. 构建便携包
pwsh -NoProfile -File scripts/build-portable-windows-x64.ps1   # Windows x64
bash scripts/build-portable-macos-arm64.sh                     # macOS ARM64
```

根 `package.json` 提供同名 npm 别名：`official:bootstrap`、`official:sync`、`official:start`、`official:status`、`overlay:install`、`check`、`build:win`、`build:mac`。例如 `npm run check` 等价于 `bash scripts/check.sh`。

`deepseek-harness/` 是 gitignored 的官方源码检出，**禁止修改**。换基线时只允许快进到官方标签，再更新 `official-lock.json`；不要在其中打补丁、cherry-pick 或提交本地功能。`bash scripts/check.sh` 会校验 checkout 的 HEAD、版本和干净状态。

数据目录：

- 开发：`DSH_HOME=~/.dsh-official-dev`（`scripts/start-official.sh` 已设置）
- 便携包：默认 `~/.dsh-official`
- 不使用、不迁移、不覆盖用户日常的 `~/.dsh`。`install-overlay.sh` 在未设置 `DSH_HOME` 时会回退到 DSH 默认目录；请始终按上面的命令显式传入。

`scripts/install-overlay.sh` 会优先用官方 `dsh plugin` 把 `overlay/` 加到指定 profile；若该 profile 由 Electron 插件管理器接管，脚本会提示改为在插件管理器中添加 `file:<repo>/overlay`。

## 与官方的关系

本仓库不是 DeepSeek 的官方仓库；这里的「官方」指上游 [deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) 的 `apps/desktop` 实现。我们锁定一个官方 commit，只做快进同步，不修改官方源码树；overlay 使用官方公开的 Cordis Host 服务与 Client 插槽，打包时在临时 staging 中注入，官方桌面缺少相关服务时仍能正常启动。

官方自带功能的缺陷应反馈给上游；本仓库只接受 overlay、构建脚本、中继和文档相关的改动。同步节奏按需进行，不承诺跟随每个上游版本。

## License

MIT，见 [LICENSE](LICENSE)。
