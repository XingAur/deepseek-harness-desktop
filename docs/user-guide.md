# 用户指南

本指南覆盖官方 DeepSeek Harness 桌面应用 + 本地 overlay 的使用。界面上的窗口、托盘、模型列表、会话、终端、插件管理来自官方；「手机连接」「技能」「MCP」和模型卡片里的额外按钮来自 overlay。

## 安装

### 便携包（推荐）

从 [GitHub Releases](https://github.com/LiuYuMiao-M/dsh-desktop/releases/latest) 下载对应平台的文件：

| 平台 | 文件 | 用法 |
|---|---|---|
| Windows x64 | `deepseek-harness-overlay-<版本>-win-x64-unsigned-manual.exe`（NSIS 安装包） | 双击安装 |
| Windows x64 | `...-win-x64-unsigned-manual.zip` | 解压到任意目录，运行 `DeepSeek Harness.exe` |
| macOS arm64 | `...-mac-arm64-adhoc-manual.dmg` | 打开后把应用拖进「应用程序」 |
| macOS arm64 | `...-mac-arm64-adhoc-manual.zip` | 解压后运行 `.app` |

只有两种架构：Windows x64 与 macOS ARM64。没有 Intel macOS 包，也没有 Linux 包。包未签名（macOS 为 ad-hoc 签名），首次打开的系统提示与放行方式见[常见问题](faq.md)。

可选：同目录的 `.sha256` 可校验下载完整性；`.build.json` 记录该包对应的官方提交、官方版本、构建版本与 overlay 哈希。

```powershell
Get-FileHash .\deepseek-harness-overlay-<版本>-win-x64-unsigned-manual.exe -Algorithm SHA256
Get-Content .\deepseek-harness-overlay-<版本>-win-x64-unsigned-manual.exe.sha256
```

### 从源码跑官方桌面

需要 Node.js `^22.19.0` 或 `>=24.0.0`、pnpm（通过 `corepack` 使用）、Git，以及能跑 `bash` 的环境（Windows 上用 Git Bash 或 WSL）。

```bash
bash scripts/bootstrap.sh
DSH_OFFICIAL_REF=dsh-v0.2.1-rc.5 bash scripts/update-official.sh
bash scripts/start-official.sh
bash scripts/install-overlay.sh
```

`start-official.sh` 用 `DSH_HOME=~/.dsh-official-dev` 启动官方桌面，日志写在同目录的 `official-desktop.log`。`install-overlay.sh` 把 overlay 以 `file:` 装进该 profile；如果 profile 由官方 Electron 管理，脚本会拒绝写入，此时在官方 Plugin Manager 里添加 `file:<仓库绝对路径>/overlay`。

## 数据放在哪

- 官方应用默认使用 `~/.dsh`。
- 便携包在未设置 `DSH_HOME` 时默认 `~/.dsh-official`，不会碰你的日常 `~/.dsh`。
- 开发脚本使用 `~/.dsh-official-dev`。

要放到别处，启动前设置 `DSH_HOME`：

```powershell
$env:DSH_HOME = "D:\dsh-data"; & "C:\Path\DeepSeek Harness.exe"
```

```bash
DSH_HOME=~/dsh-data bash scripts/start-official.sh
```

overlay 自己的配置写在 `<DSH_HOME>/overlay-custom.yaml`，不改官方 `settings.yaml`。

## 手机连接

前提：先有一个中继服务地址（见下一节）。

1. 点侧栏底部的手机图标。
2. 第一次使用时弹窗要求填写中继地址，填 `https://你的中继域名` 后点「保存并连接」。
3. 弹窗显示配对二维码与链接。用手机相机或浏览器扫码/打开链接。
4. 图标颜色表示状态：绿色=远程已连接，黄色=等待远程连接，默认色=未开启。

说明：

- 每次应用重启后远程控制默认关闭，需要重新点击入口建立隧道，并重新配对。
- 链接里带一次性会话参数；应用重启后旧链接失效。
- 手机页可以看会话、发消息、停止任务、批准工具调用与回答问题。桌面也可以同时回答，谁先回答以谁为准。

## 配置中继

中继是一个独立部署的 Node 服务，见 [`relay-server/README.md`](../relay-server/README.md)。最小步骤：

```bash
# 服务器上
cd /opt/dsh-relay
npm install --omit=dev
node main.mjs --host 127.0.0.1 --port 8787
curl https://relay.example.com/healthz   # → dsh-desktop relay
```

Caddy 反代与 systemd 单元文件见上面那份 README。要求：Node ≥ 22、必须有 TLS。

填写规则（overlay 会校验）：

- 只填 origin，例如 `https://relay.example.com`；带路径、查询串或用户名密码会被拒绝。
- 公网必须 `https://`；只有 `localhost` / `127.0.0.1` / `[::1]` 允许 `http://`，用于本地调试。

两种配置方式：

- 在手机连接弹窗里填写，写入 `<DSH_HOME>/overlay-custom.yaml` 的 `remoteRelayOrigin`，下次启动自动记住。
- 用环境变量覆盖（优先级更高）：

```powershell
$env:DSH_OVERLAY_RELAY_ORIGIN = "https://relay.example.com"
```

安全边界：中继能看到转发的明文 HTTP 流量，只应部署在自己控制的服务器上，并始终走 TLS。

## 模型：测试、拉取、勾选

设置 → 模型 → 任一提供商卡片（DeepSeek 以及自定义的 OpenAI 兼容路由）：

- **测试连接**：向该提供商请求模型列表，失败时显示 HTTP 状态与上游返回内容，瞬时故障会自动重试。
- **拉取模型列表 / 刷新最新模型**：拉取该提供商当前可用的模型 ID，回填到下方候选列表。
- **勾选 + 保存显示的模型**：只有勾选的模型会出现在模型选择器里。保存后**重启应用**才生效。

勾选结果写入 profile 的 `cordis.patch.yml` 中 `llm-pi-ai` 行，读取时会与 `settings.yaml` 里的同名配置合并。模型列表接口不可用（例如官方升级后）时，只会退化为没有线上列表，不影响已有模型继续使用。

## Codex / xAI 登录

Codex 与 xAI 的模型卡片上有「登录（OAuth）」：

1. 点击后应用会调用官方授权流程，状态显示「等待浏览器…」。
2. 浏览器打开登录页（应用里有「打开登录页」可再次打开）。
3. 需要粘贴授权码时，输入后点「提交」。
4. 登录成功后卡片显示状态点与「已登录」，之后可以拉取并使用线上模型列表。

凭据保存在 `<DSH_HOME>/.credentials.yaml`（`llm-pi-ai/openai-codex`、`llm-pi-ai/xai`），overlay 只读不写。如果官方授权服务缺失或改名，按钮会提示「本次官网升级后登录接口不可用」。

## MCP 管理

设置 → MCP：

- 每个 MCP 是一张卡片，可测试连接、编辑、启用/停用、删除、导出单个 JSON。
- 「常用 MCP」提供模板：数据库、云效、GitLab、Playwright、Filesystem、Memory、通用 HTTP。
- 「自定义 MCP」手动填：连接方式（本地命令 `stdio` / 远程 `streamable-http`）、命令与参数、环境变量、工作目录、URL 与请求头。
- 「导入 JSON」接受 Claude Desktop 的 `{"mcpServers": {...}}`、单个服务器或服务器数组。
- 环境变量、请求头里已保存的敏感值不会回显；留空表示保留原值。
- 「导出全部」不会包含已保存的 token、密码和请求头值。

配置保存在 `<DSH_HOME>/overlay-custom.yaml` 的 `mcpServers`，**保存后重启应用**才会把新的 MCP 挂进当前 Host 组合。参数里可以用 `${VAR}` 引用同一张卡片的 `KEY=value` 环境变量，避免把密码写进命令行。

## 模型网络代理

设置 → 模型 → 页面底部「模型网络」。它只作用于模型请求，不影响 MCP 或其它工具，也不需要维护厂商名单。

- **跟随系统**：使用检测到的系统 HTTP/HTTPS 代理与绕过规则；不支持或读取失败时不会静默改成直连。
- **手动代理**：填 `http://` 或 `https://` 代理地址（可带凭据）；代理不可用时不会回退直连。
- **直连**：忽略系统代理与代理环境变量。

「测试连接」按当前输入测一次，**不会保存**；「保存」之后的新模型请求才使用新配置，进行中的请求保持原路由。回环地址始终直连。已保存的代理密码不会回显，不修改地址即保留凭据。

碰到上游按地区拒绝（例如 OpenAI 提示地区不可用）时，先在这里确认生效路由与出口地区，再重新登录。

## Skills

设置 → 技能：列出当前官方组合已发现、可供会话调用的技能，包含来源、是否可被模型调用、是否可由用户调用，以及何时使用。在会话输入框打 `/` 也可以直接选择技能。

如果显示「当前组合未挂载技能目录」，说明当前运行环境没有官方的技能目录服务，此时该页只做提示，不提供管理功能。

## 更新

本项目**手动更新**，没有自动更新。侧栏「更新」入口会检查两个来源，有可用版本时显示红点：

1. **官方 Releases**（`deepseek-ai/deepseek-harness`，tag `dsh-v*`）：官方桌面应用发布了新版本。
2. **本仓库 Releases**（tag `official-v<buildVersion>`）：这里构建了新的便携包。

点开入口可以看到当前版本、官方最新、本仓库最新，以及两边对应的更新内容（release notes），并跳转到发布页。版本号格式是「锁定官方版本\_两位迭代号」，例如 `0.2.1-rc.5_01`、`0.2.1-rc.5_02`，官方升到 `0.2.2-...` 后变成 `0.2.2-..._01`。

提示只负责告知，不会下载或安装：

1. 官方有新版本：到官方 Releases 下载官方桌面版，或等本仓库按新基线重新构建。
2. 本仓库有新构建：下载对应平台的包，Windows 退出应用后覆盖安装（或替换 ZIP 目录），macOS 打开新 DMG 覆盖。
3. 数据留在 `<DSH_HOME>` 不变；重启应用即可。

说明：官方安装包有签名与 publisher 校验，本仓库的未签名便携包不能套用官方自动更新器，直接安装官方更新会把 overlay 构建替换掉，所以这里只提示、不自动装。

跨版本跳过多个小版本一般没问题，但涉及官方基线变更（版本号下划线前半段变化）时，建议看发布说明里的注意事项。

## 出问题时

先看[常见问题](faq.md)里的排查一节，尤其是 `DSH_HOME` 是否指到了预期目录、profile 里是否真的有 overlay 行。
