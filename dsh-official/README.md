# DSH Official Overlay

新发行线：官方 `apps/desktop` 当壳，本目录的 overlay 插件叠定制功能。
现有 `dsh-plugin-desktop` 2.0.x **不动**，继续用仓库根上的 `deepseek-harness` pin。

签名/公证先不做。官方源码用 bootstrap 拉取官网 `master` 到 `dsh-official/deepseek-harness/`（gitignored 的独立 Git 检出），**禁止改那棵树来加功能**。本地发行脚本只在一次性 `git archive` staging 中注入 overlay 和打包适配。

## 同步官网代码（硬规则）

以后换官网版本只允许：

1. 首次用 `bash dsh-official/scripts/bootstrap.sh` 克隆官网 `master`；以后只运行 `bash dsh-official/scripts/update-official.sh` 快进到官网最新提交；准备发行时把已验证提交和版本写入 `official-lock.json`，本地与 GitHub 远程构建都只接受该锁定版本
2. 先退出 Harness Dev；更新脚本只接受干净工作树和 `--ff-only` 快进，**不准**在官方树里改文件、打补丁、cherry-pick 你的功能
3. 定制只在 `dsh-official/overlay/` 和 `dsh-official/scripts/`
4. 仓库根上的 `deepseek-harness`（2.0.x pin `82a5fd61a7`）**不准动**
5. 官方桌面必须用独立 `DSH_HOME`（推荐 `~/.dsh-official-dev`），禁止和 2.0.x 共用 `~/.dsh`
6. `update-official.sh` 自动执行 `pnpm install --frozen-lockfile` 和 `pnpm run build`；开发模式可用 `install-overlay.sh`，正式本地包无需手工安装插件
7. 打包时 overlay 作为 `@deepseek-ai/dsh-overlay-custom` 放进官方 runtime；首次启动和升级都会幂等修复 Profile，移除历史 `file:` 依赖和源码软链接，同时保留其他 bundle/patch 行
8. overlay 对官方 API **可选探测**：`authorization` 没有或槽位改名时，登录按钮显示不可用，**官方桌面仍要能启动**；不准让 overlay 变成官方的启动依赖

两边功能怎么保住：官网自带的（账号、调度、终端、Office、插件管理）永远用官网的；你的（中继、测模型、Codex/xAI OAuth 登录）永远只在 overlay。同步官网 = 换壳，overlay 原样再装上去。

## 怎么跑

```bash
bash dsh-official/scripts/bootstrap.sh          # 首次克隆官网 master
bash dsh-official/scripts/update-official.sh    # 以后快进更新、安装依赖并构建
bash dsh-official/scripts/start-official.sh     # 启动已有构建
DSH_HOME=$HOME/.dsh-official-dev bash dsh-official/scripts/install-overlay.sh # 仅源码开发
bash dsh-official/scripts/build-portable-macos-arm64.sh                   # macOS ARM64 DMG + ZIP
pwsh -File dsh-official/scripts/build-portable-windows-x64.ps1           # Windows x64 EXE + ZIP
```

便携构建产物写入 `dsh-official/dist/`，macOS 同时生成便于安装的 DMG 和备用 ZIP，Windows 同时生成 NSIS 安装 EXE 和备用 ZIP；每个产物都有 SHA-256 文件和构建来源记录。macOS 包仅做 ad-hoc 签名，没有付费 Developer ID 签名或公证；首次打开可能需要 Finder 右键“打开”，或在确认来源后执行 `xattr -dr com.apple.quarantine '/Applications/DeepSeek Harness.app'`。Windows 安装包未签名，首次启动可能显示 SmartScreen；EXE 是安装向导，ZIP 解压后入口为 `DeepSeek Harness.exe`。两种平台都使用手工更新，不得放入官方自动更新 feed。

GitHub Actions 的 `Release Official DSH Portable` 工作流从 `official-lock.json` 获取官网仓库和提交，在 Windows x64 与 macOS runner 上分别构建 Windows x64（EXE + ZIP）和 macOS ARM64（DMG + ZIP）包，然后把安装包、校验文件和构建记录直接上传到独立的 `official-v*` GitHub Release。工作流不需要 Apple 或 Windows 付费签名凭据；正式发布的 Release 会标记为 prerelease，避免被误认为官网签名发行版。

GitHub 直连不可用时，可显式选择可信的加速地址：`DSH_OFFICIAL_FETCH_URL=<git-url> bash dsh-official/scripts/update-official.sh`。更新仍写入 `origin/master` 并只接受快进。

本 overlay **不要**加进仓库根 Yarn workspace：2.0.x 使用固定上游版本，官网线持续跟踪 `master`，两套依赖不能混进一个 lockfile。

## 功能怎么留

| 现有能力 | 新线上的做法 |
|---|---|
| 云中继 / 侧栏手机连接 / 配对 | overlay 已接隧道、配对 JSON、`/api/desktop/mobile/*`、`/mobile` 静态页（列表/发消息/批准）。origin 读 `DSH_OVERLAY_RELAY_ORIGIN` 或当前 `DSH_HOME/overlay-custom.yaml` 的 `remoteRelayOrigin`，保存一次后持续生效 |
| 提供商卡片「测试连接 / 拉模型」 | overlay 已接 `/api/desktop/models/probe` + 官方 provider-card slot |
| Codex / xAI 订阅登录（OAuth） | overlay 调官方 `ctx.authorization.begin`（`llm-pi-ai/openai-codex`、`llm-pi-ai/xai`）；设置卡片上「登录」；**禁止从 ~/.dsh 拷凭据** |
| 刷新最新模型 + 勾选显示 | overlay：`/api/desktop/models/live`（OAuth 拉线上清单，失败降级为已配置）+ `/api/desktop/models/selection`（勾选写入 `profiles/desktop/cordis.patch.yml`，重启生效）；GPT/Grok 模型同时记录文字与图片输入能力，便携包启动前会补齐当前选中模型，避免图片消息被误报为 `session/agent-busy`；卡片上有绿/红登录状态点 |
| Codex / xAI 走代理 | overlay undici 按 host 分流（`api.x.ai` / `chatgpt.com` 等）；DeepSeek/智谱直连；配置写当前 `DSH_HOME/overlay-custom.yaml`；设置页底部「模型代理」；样式用官方 `--dsw-*` token |
| Skills 目录 | overlay 设置页展示当前 preset 的实际技能目录、来源和调用方式；会话输入框继续使用官方 `/` 技能选择器 |
| MCP 管理 | 每个 MCP 是独立卡片，支持状态、测试、编辑、启停、删除、脱敏 JSON 导出，并支持全量导入/导出；模板包含数据库（PostgreSQL/MySQL/Oracle/SQL Server/SQLite）、云效、GitLab、Playwright、Filesystem、Memory、远程 HTTP；数据库只需选择类型并填写连接参数；敏感值不回显，配置写 `DSH_HOME/overlay-custom.yaml`，重启后挂载官方 `dsh-mcp-client` |
| 托盘模型诊断 | overlay；尽量改成页内窗口，不再开一套 Electron 壳 |
| 系统代理检测/测试 | 后续 overlay |
| 调度器、DeepSeek 账号、插件管理、侧栏终端/浏览器、Office 预览 | **用官方的**（0.1.7 host 已带 `dsh-schedule`、`dsh-deepseek-account`） |
| compatibility / extended / enhanced 窗框、自有安装器、自有更新源 | **丢掉**（那是旧壳） |
| asar 预设 / 凭据探针等稳定性补丁 | 先当官方已修；回归失败再单独补 overlay |

数据：便携包在没有显式 `DSH_HOME` 时使用 `~/.dsh-official`，源码开发默认使用 `~/.dsh-official-dev`，都不与旧 2.0.x 的 `~/.dsh` 混用。若需要其他位置，启动前显式设置 `DSH_HOME`。
