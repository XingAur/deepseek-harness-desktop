# DSH Official Overlay

新发行线：官方 `apps/desktop` 当壳，本目录的 overlay 插件叠定制功能。
现有 `dsh-plugin-desktop` 2.0.x **不动**，继续用仓库根上的 `deepseek-harness` pin。

签名/公证先不做。官方源码用 bootstrap 拉取官网 `master` 到 `dsh-official/deepseek-harness/`（gitignored 的独立 Git 检出），**禁止改那棵树来加功能**。本地发行脚本只在一次性 `git archive` staging 中注入 overlay 和打包适配。

## 同步官网代码（硬规则）

以后换官网版本只允许：

1. 首次用 `bash dsh-official/scripts/bootstrap.sh` 克隆官网 `master`；发行更新优先用 `DSH_OFFICIAL_REF=dsh-v<版本> bash dsh-official/scripts/update-official.sh` 快进到已发布标签。不传 `DSH_OFFICIAL_REF` 时仍跟踪 `master`，不等于最新已发布桌面版。准备发行时把已验证提交和版本写入 [official-lock.json](official-lock.json)，本地与 GitHub 远程构建都只接受该锁定版本
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
DSH_OFFICIAL_REF=dsh-v0.2.1-rc.3 bash dsh-official/scripts/update-official.sh # 已发布版本，快进并构建
bash dsh-official/scripts/start-official.sh     # 启动已有构建
DSH_HOME=$HOME/.dsh-official-dev bash dsh-official/scripts/install-overlay.sh # 仅源码开发
bash dsh-official/scripts/build-portable-macos-arm64.sh                   # macOS ARM64 DMG + ZIP
pwsh -File dsh-official/scripts/build-portable-windows-x64.ps1           # Windows x64 EXE + ZIP
```

便携构建产物写入 `dsh-official/dist/`，macOS 同时生成便于安装的 DMG 和备用 ZIP，Windows 同时生成 NSIS 安装 EXE 和备用 ZIP；每个产物都有 SHA-256 文件和构建来源记录。打包时在一次性 staging 中清理 ASAR 的本地路径，并同步重建 `app.asar.unpacked`，逐项核对原本解包的原生依赖仍在磁盘上且 ASAR 头标记为 `unpacked`（包括 macOS 图片上传所需的 sharp 与 libvips）。macOS 包仅做 ad-hoc 签名，没有付费 Developer ID 签名或公证；首次打开可能需要 Finder 右键“打开”，或在确认来源后执行 `xattr -dr com.apple.quarantine '/Applications/DeepSeek Harness.app'`。Windows 安装包未签名，首次启动可能显示 SmartScreen；EXE 是安装向导，ZIP 解压后入口为 `DeepSeek Harness.exe`。两种平台都使用手工更新，不得放入官方自动更新 feed。

GitHub Actions 的 `Release Official DSH Portable` 工作流从 `official-lock.json` 获取官网仓库和提交，在 Windows x64 与 macOS runner 上分别构建 Windows x64（EXE + ZIP）和 macOS ARM64（DMG + ZIP）包，然后把安装包、校验文件和构建记录直接上传到独立的 `official-v*` GitHub Release。工作流不需要 Apple 或 Windows 付费签名凭据；正式发布的 Release 会标记为 prerelease，避免被误认为官网签名发行版。

GitHub 直连不可用时，可显式选择可信的加速地址：`DSH_OFFICIAL_FETCH_URL=<git-url> bash dsh-official/scripts/update-official.sh`。分支更新仍维护对应的 `origin` 跟踪引用；发布标签按刚抓取的提交更新。两者都只接受快进，不会重置分叉或有修改的官方树。

本 overlay **不要**加进仓库根 Yarn workspace：2.0.x 使用固定上游版本，官网线持续跟踪 `master`，两套依赖不能混进一个 lockfile。

## 功能怎么留

| 现有能力 | 新线上的做法 |
|---|---|
| 云中继 / 侧栏手机连接 / 配对 | overlay 已接隧道、配对 JSON、`/api/desktop/mobile/*`、`/mobile` 静态页（列表/发消息/批准）。origin 读 `DSH_OVERLAY_RELAY_ORIGIN` 或当前 `DSH_HOME/overlay-custom.yaml` 的 `remoteRelayOrigin`，保存一次后持续生效 |
| 提供商卡片「测试连接 / 拉模型」 | overlay 已接 `/api/desktop/models/probe` + 官方 provider-card slot |
| Codex / xAI 订阅登录（OAuth） | overlay 调官方 `ctx.authorization.begin`（`llm-pi-ai/openai-codex`、`llm-pi-ai/xai`）；设置卡片上「登录」；**禁止从 ~/.dsh 拷凭据** |
| 刷新最新模型 + 勾选显示 | overlay：`/api/desktop/models/live`（OAuth 拉线上清单，失败降级为已配置）+ `/api/desktop/models/selection`（勾选写入 `profiles/desktop/cordis.patch.yml`，重启生效）；便携包发送图片时不按模型目录预检拦截，未声明图片能力的模型把错误交给提供商返回，而不再误报 `session/agent-busy`；卡片上有绿/红登录状态点 |
| 模型网络 | 设置页底部「模型网络」统一提供跟随系统、手动代理、直连；不维护厂商或模型白名单。模型调用及定制卡片的鉴权/模型列表使用请求级路由，普通 MCP、web 工具和数据库连接保留其原出网策略；详见下方说明 |
| Skills 目录 | overlay 设置页展示当前 preset 的实际技能目录、来源和调用方式；会话输入框继续使用官方 `/` 技能选择器 |
| MCP 管理 | 每个 MCP 是独立卡片，支持状态、测试、编辑、启停、删除、脱敏 JSON 导出，并支持全量导入/导出；模板包含数据库（PostgreSQL/MySQL/Oracle/SQL Server/SQLite）、云效、GitLab、Playwright、Filesystem、Memory、远程 HTTP；数据库只需选择类型并填写连接参数；敏感值不回显，配置写 `DSH_HOME/overlay-custom.yaml`，重启后挂载官方 `dsh-mcp-client` |
| 托盘模型诊断 | overlay；尽量改成页内窗口，不再开一套 Electron 壳 |
| 系统代理检测/测试 | macOS 读取 scutil 静态 HTTP/HTTPS 代理与例外；Windows 读取当前用户 WinHTTP/IE 静态代理。不扫描本地端口，不用环境变量冒充系统代理。PAC、已发现的 WPAD、仅 SOCKS 或无法解析的代理列表会明确提示，不静默直连。刷新检测不覆盖未保存输入，连接测试不保存配置、不发送模型生成请求 |
| 调度器、DeepSeek 账号、插件管理、侧栏终端/浏览器、Office 预览 | **用官方的**（0.1.7 host 已带 `dsh-schedule`、`dsh-deepseek-account`） |
| compatibility / extended / enhanced 窗框、自有安装器、自有更新源 | **丢掉**（那是旧壳） |
| asar 预设 / 凭据探针等稳定性补丁 | 先当官方已修；回归失败再单独补 overlay |

数据：便携包在没有显式 `DSH_HOME` 时使用 `~/.dsh-official`，源码开发默认使用 `~/.dsh-official-dev`，都不与旧 2.0.x 的 `~/.dsh` 混用。若需要其他位置，启动前显式设置 `DSH_HOME`。

## 模型网络

默认「跟随系统」在每次新模型调用前读取系统静态 HTTP/HTTPS 配置：macOS 使用 `scutil --proxy`，Windows 使用 `WinHttpGetIEProxyConfigForCurrentUser`。关闭系统代理后后续调用直连。Windows 若勾选自动检测但未发现 PAC，则采用已配置的静态代理或直连；发现 PAC 或存在显式 PAC URL 时明确不支持。TUN 由操作系统处理，不扫描端口或猜测代理软件，应用直连不代表绕过 VPN。手动模式使用指定的 HTTP(S) 代理，直连模式不继承代理环境变量。代理故障、读取失败或不支持的系统设置均明确报错，不偷偷改成直连。 Linux 及其他平台请选择手动代理或直连。

回环地址始终直连；系统与手动模式均继承成功读取的系统例外，并接受额外的域名、子域、端口、IP、CIDR 或通配例外。是否适合走代理取决于 API 平台域名，而不是模型名称。代理软件的规则模式可以负责公网分流；受出口地区限制的平台可加入直连例外。

配置以 `modelProxyMode`、`modelProxyUrl`、`modelProxyBypass` 保存在当前 Harness home 的 overlay 配置中。旧的 URL 仅保留为手动模式候选，旧 `modelProxyProviders` 名单不再使用；首次默认跟随系统。保存采用串行原子写入。浏览器只显示无认证信息的代理地址；未编辑 URL 时保存或测试不提交该字段，保留已存凭据。测试只发一次无密钥 HEAD 请求，成功表示该目标连通，不代表所有模型生成成功，也不隐式保存。

每次调用捕获不可变路由，已开始的流、请求体与响应体保留原路由到结束，新调用使用最新配置。仅模型执行范围和定制模型卡片的网络操作参与选路；工具调用、MCP、数据库及系统设置不由该范围更改。路由实现不替换全局 fetch/WebSocket 或改写环境变量；共享的网络分发器和 Node HTTP 包装器在非模型范围委托原处理器。

便携 staging 为 Codex 选择 SSE，避免其会话级 WebSocket 缓存沿用旧代理；Bedrock 使用其原本支持的 Node HTTP/1 handler，使请求级代理接管生效。这些精确版本适配只施加在 staging，不编辑官方检出；模型、内容和超时时长不因此改变。

### 封包顺序

使用包含 Node-API 开发头文件的 Node 安装构建原生组件，不把精简的捆绑运行时当成开发安装。staging 按发行包名称重新编译 overlay，校验前端模块 ID 与包名一致，并在每次构建前恢复必要的临时入口。ASAR 重封包保留原解包文件的执行位，同时更新 Electron 的 ASAR 摘要。

路径清理和原生签名之后，[最终封存工具](scripts/finalize-portable-runtime.mjs) 对照已验证的原准备树审计全部文件。macOS 只接受精确路径替换或经过验证的签名区变化；Windows 只接受精确路径替换，**拒绝** dsh 内 PE/native 字节变化，并更新 Electron EXE 的唯一 `INTEGRITY/ELECTRONASAR` 资源（摘要为最终 ASAR headerString 的 SHA-256）。不接受成员或正文的任意变化。然后用官方 writer 写出最终库存、原版 validator 校验完整 ASAR，并由[最终 smoke](scripts/smoke-portable-final.mjs) 验证实际应用。原准备库存不被覆盖。Windows 构建在 NSIS 之后只允许明确来源的 `resources/elevate.exe` 增量，再复验并最后打 ZIP。Windows 最终封存与安装包需在 GitHub Actions 的 windows-latest x64 runner 上验证，不能用本机 macOS 结果代替。

### 边界与验证

自动读取目前只支持 macOS 的静态 HTTP/HTTPS 配置。PAC、自动发现、仅 SOCKS、按接口区分的系统代理及其他操作系统的自动读取均明确显示不支持；可选手动 HTTP(S) 或直连。预建 HTTP/2 会话、原始 CONNECT/Upgrade、自定义原生 Agent 或自带连接等不能安全接管的方式会拒绝，而不是绕过代理。提前缓存的第三方传输函数或显式自带 dispatcher 不属于任意代码强制代理机制；新的模型适配器必须补实际出网测试。

在 overlay 目录运行 `npm ci && npm run check`，覆盖策略、凭据脱敏、状态切换、实际本地 HTTP(S)/TLS/mTLS 代理、并发非模型隔离、UI 行为和 SDK/Loader 组合。SDK 与 Loader 测试需要已构建的官方开发依赖；缺少时明确跳过，可用 `DSH_PROXY_TEST_DEPENDENCIES` 指向已构建 staging 的依赖目录并核实零跳过。单元或本地 fixture 的通过不等于真实付费模型或当前安装 GUI 已验证。
