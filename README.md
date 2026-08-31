<p align="center">
  <img src="src-tauri/icons/icon.png" width="88" alt="DeepSeek Harness Desktop 图标" />
</p>

<h1 align="center">DeepSeek Harness Desktop</h1>

<p align="center">
  让普通用户无需命令行，即可在 Windows 和 Apple Silicon Mac 上使用 DeepSeek Harness：描述想法、构建项目、直接运行做出来的应用。
</p>

> [!IMPORTANT]
> 这是独立维护的社区项目，并非 DeepSeek 官方产品。

官方 DeepSeek Harness 是本项目的上游基础仓库；具体更新边界见[上游跟踪与升级策略](docs/upstream-policy.md)，许可证归属见[第三方许可证声明](THIRD_PARTY_NOTICES.md)。

## 它能做什么

DeepSeek Harness Desktop 把官方 DeepSeek Harness Web 工作台放进原生桌面窗口，并负责普通用户不应该手动处理的本地环境工作：

- 自动准备并启动受管 Runtime（Node.js 24 + pnpm + 官方 DeepSeek Harness），无需预装任何依赖。
- 在原生窗口中呈现桌面版工作台布局：官方侧栏、会话区、详情面板三栏结构，侧栏与详情宽度可调，并带“本地项目”快捷入口。
- 用对话描述想做的项目，由真实 DeepSeek Harness 会话完成构建。
- 一键运行构建出来的本地应用：应用在本机安全运行、数据保存在项目目录里，随时返回工作台继续改进。
- 使用当前 Profile 的官方 Workspace 数据展示“本地项目”，不维护第二份容易失真的项目数据库。
- 通过 Profile 隔离不同的工作环境，支持创建、复制、编辑、删除和切换。
- 支持浅色、深色和跟随系统主题，桌面标题栏与工作台保持一致。
- 提供系统托盘、应用更新、受限导航和固定用户数据目录。
- 管理 Runtime 的下载、签名验证、健康检查、升级、回滚和诊断导出。
- 普通 Agent、会话和工具能力始终由官方 DeepSeek Harness 工作台提供；尚未成熟的 HIS Harness（实验）只在“模型与 Agent → 实验功能”中按本次会话显式启用。

## 安装与首次启动

正式安装包统一发布在 [GitHub Releases](https://github.com/XingAur/deepseek-harness-desktop/releases)。请选择与系统对应的版本：

- Windows x64：下载 `.exe` 安装包；后续新版本可在应用内更新。
- macOS Apple Silicon：下载 `.dmg`，拖入“应用程序”完成安装；后续由应用提示新版本并打开可信下载页，再由用户手动替换旧应用。

Windows 应用内更新使用免费的 Tauri updater 密钥校验更新包完整性，不等同于付费的 Windows Authenticode 发行者证书。当前没有 Authenticode 证书，因此首次下载或安装时 Windows 仍可能显示“未知发布者”或 SmartScreen 提示；但安装后的应用内更新不会接受缺少正确 Tauri 签名的更新包。

macOS 包未使用 Apple Developer ID 签名、未经过 Apple 公证，因为当前项目没有付费 Apple Developer Program 证书。macOS 可能在第一次启动时显示开发者验证提示；请只从本仓库 Release 下载，随后在 Finder 中按住 Control 点按应用并选择“打开”，或在“系统设置 → 隐私与安全性”中确认打开。项目不会要求关闭 Gatekeeper 或执行移除隔离属性的命令。

安装程序同时携带桌面应用和已签名的受管 Runtime。首次打开应用时，启动页会自动：

1. 读取并验证内置 Runtime 清单；
2. 解压内置 Runtime；
3. 校验 Ed25519 签名和 SHA-256；
4. 在隔离环境中启动并检查工作台是否真正可用；
5. 验证成功后进入 DeepSeek Harness 工作台。

整个过程都在应用启动页中完成，不会在安装器中额外弹出命令行或下载窗口。正常的首次准备不依赖网络；如果内置 Runtime 损坏、版本需要升级或用户主动修复，应用才会从不可变 Runtime Release 下载并验证替代版本。失败时可以重试、修复或导出诊断文件。

安装与更新只替换桌面程序和版本化 Runtime，不迁移或清空 Profile、Workspace 和会话数据。升级前退出正在运行的应用即可；重要项目仍建议按日常习惯独立备份。

## 日常使用

### 工作台与本地项目

应用启动后直接进入官方工作台的桌面布局。侧栏中的“本地项目”来自当前 Profile 的 Workspace 列表：

- 单击卡片选中项目，并可在下方对话框快捷修改；
- 双击卡片启动项目：可运行的项目直接启动本地应用，其余项目打开会话继续构建；
- 卡片上的徽标说明项目状态：“可运行”表示已带有可启动的应用，“运行中”表示应用正在后台运行；
- 右键菜单提供打开会话继续开发、停止应用、修改名称、选择内置颜色或渐变封面、置顶和删除项目；
- 删除操作必须二次确认，并由用户选择只移除记录，还是把已登记的项目目录移到 Windows 回收站；
- 列表为空时，只需描述想做的项目；桌面端会在“文档\DeepSeek Harness\Projects”中自动创建安全且不重名的目录，并在后台使用当前 Profile 和工作区可写权限，再通过真实 DeepSeek Harness 会话开始构建；
- 已经存在于其他位置的项目，可以通过“收录已有项目”加入本地项目列表；收录只是登记标记，不会复制或移动项目文件。

### 运行本地应用

由 DeepSeek Harness 构建完成的项目，可以在应用内直接运行：

- 双击带“可运行”徽标的项目，应用会在本机回环地址启动，并通过健康检查确认真正可用；
- 运行的应用嵌入主窗口，顶部提示条显示“正在运行：<项目名>”，并提供“返回工作台”和“停止应用”；
- 返回工作台后应用继续在后台运行，工作台会话不受影响，随时可以再切回去；
- 应用的数据保存在项目自己的目录中，重启应用或电脑后数据仍在；
- 同一项目同时只保留一个运行实例，重复双击会直接回到已运行的实例。

### Profile

Profile 是一套相互隔离的工作环境。不同 Profile 拥有各自的工作区、会话、插件配置、权限和数据根。

大多数用户只需要默认 Profile。需要区分工作与个人项目、不同客户环境或不同权限时，才需要创建第二个 Profile。Profile 管理入口位于工作台设置中的 Profiles 区，支持创建、复制、编辑、删除和切换。

切换 Profile 使用 pending / last-known-good 机制：新环境完全就绪后才正式生效；如果启动失败或上次切换被中断，应用会自动恢复到最后一次确认可用的环境。

### 模型、Agent 与扩展（预览）

工作台设置中的“模型与 Agent”中心提供统一入口：

- Provider 凭证只通过 macOS Keychain 或 Windows Credential Manager 保存，数据库只保存凭证 ID 和状态；API Provider 支持 DeepSeek、OpenAI 兼容接口和 Anthropic 风格流式协议的适配边界；
- Agents 支持 Codex、Claude 的会话协议、任务提示、审批、事件时间线、取消和待复核状态。当前仓库不携带官方 Codex/Claude SDK，也不会复制已有登录凭证；真实账户接入需要宿主注入对应官方客户端或受管 CLI；
- 权限模式由用户选择“请求批准”“智能批准”或“完全访问权限”，完全访问会显示风险提示；应用重启后不会自动重放结果未知的外部操作；
- Plugins、Skills、MCP 使用清单、完整性、来源、能力和审核边界；MCP 支持受限的 stdio、HTTPS、SSE 传输包装器和 OAuth 回调校验，但扩展远程安装和真实 MCP 服务接入仍需固定来源与宿主配置。
- HIS Harness（实验）不进入普通对话主流程，启用前会明确提示其尚未完成完整业务与运行时验证，也不会因此获得 Git、云效、数据库或部署写权限。

因此，首次接入时请以界面显示的 Provider/CLI 状态和 Diagnostics 为准；看到“预览”“未配置”“待复核”时，不代表真实模型调用已经可用。

### 系统托盘

关闭窗口时应用默认驻留系统托盘。托盘菜单提供：

- 显示当前 Profile 与 Runtime 版本；
- 显示 / 隐藏窗口；
- 重启 DeepSeek Harness；
- 检查应用更新；
- 导出诊断；
- 打开用户数据目录；
- 退出。

### 恢复、诊断与更新

- Runtime 启动失败时，可以重试或执行修复下载；
- “查看详情”显示失败阶段和技术信息，主提示保持面向普通用户；
- “导出诊断”生成已经脱敏的 ZIP，便于定位网络、版本或进程问题；
- 应用本体更新和 Runtime 更新是两个独立通道，互不混用；
- Windows x64 使用 Tauri 签名校验的应用内更新，可以立即安装、在退出应用时安装，或暂时跳过；
- macOS Apple Silicon 只提示新版本并打开本仓库的 HTTPS 下载地址，不会在后台执行未签名 DMG；下载后请退出应用、打开 DMG、把新应用拖入“应用程序”并确认手动替换；
- 更新检查或打开下载页失败不会阻止已经可用的工作台启动，也不会删除用户数据。

GitHub Actions 每 4 小时分别检查官方 GitHub 源码 tag 和 npm `@deepseek-ai/dsh` 的 `latest` 精确版本。发现变化后只更新专用分支并创建或刷新升级 PR；必须经过人工审核、合并和显式发布，自动观察不会直接改默认分支、打标签或发布。源码领先但 npm 包尚未出现时会明确记录“发行包待发布”，不会把源码 tag 冒充为已安装 Runtime。

## 为什么后续启动更快

首次启动需要验证并解压内置 Runtime，因此耗时主要取决于压缩包大小、磁盘性能和安全软件扫描。

后续启动会优先检查本地的验证凭据和 Runtime 版本。只要版本一致、文件完整且健康检查通过，应用会直接启动本地 Runtime，不等待联网检查。只有版本不兼容、文件损坏或用户主动修复时，才会重新下载。

## 数据与安全

- Windows 用户数据固定保存在 `%LOCALAPPDATA%\ai.deepseek.harness.desktop`，不会跟随安装目录漂移。
- 默认卸载只移除应用，保留 Profile、Workspace 和会话数据；卸载过程不阻塞等待大文件删除。
- 如果卸载时选择删除本地数据，卸载器会先把固定数据目录原子移动到待清理位置，再由后台清理程序释放磁盘空间。
- Runtime 清单使用 Ed25519 签名，下载制品使用 SHA-256 校验；新版本通过完整健康检查后才提交，失败会回滚。
- 工作台和本地应用都只监听随机的 `127.0.0.1` 端口，不从局域网或互联网访问。
- 本地应用只能用受管 Runtime 内的 Node.js/pnpm 以固定参数启动，不允许任意命令；应用目录和清单经过校验，防止路径逃逸；同时运行的应用数量有上限。
- 嵌入的工作台页面不持有不受限制的 Tauri IPC；本机操作只能经过类型化、动作白名单化的桥接接口。
- 顶层导航只允许受管本地页面；外部 HTTPS 地址验证后交给系统浏览器，其余协议默认拒绝。
- 诊断导出会清理常见令牌、认证头和敏感环境变量。

## 当前支持范围

| 平台 | 状态 |
| --- | --- |
| Windows x64 | 完整安装包、内置受管 Runtime、Tauri 签名应用内更新 |
| macOS Apple Silicon | DMG、内置受管 Runtime、应用内更新提醒与手动替换；无 Developer ID 签名和公证 |
| Windows ARM64、macOS Intel、Linux | 当前不支持 |

## 常见问题

<details>
<summary><strong>为什么完整安装包较大，第一次打开仍要准备？</strong></summary>

完整安装包内置了 DeepSeek Harness、Node.js、pnpm 和桌面插件组成的受管 Runtime，因此体积会更大。首次打开时，应用仍需验证签名与哈希、解压文件并完成健康检查；这通常取决于磁盘和安全软件的扫描速度，而不是网络下载速度。

</details>

<details>
<summary><strong>第二次打开还会等很久吗？</strong></summary>

通常不会。健康且版本一致的本地 Runtime 会直接启动，不等待远程更新检查。只有版本变化、文件损坏或用户主动修复时，才可能进入在线下载流程。

</details>

<details>
<summary><strong>双击项目没有启动应用？</strong></summary>

只有构建完成并带有应用清单的项目才会显示“可运行”徽标并直接启动应用；其余项目双击后会打开会话继续构建。如果启动过程中健康检查没有通过，界面会提示失败并保留项目状态，可返回会话让 DeepSeek Harness 继续修复，或导出诊断反馈问题。

</details>

<details>
<summary><strong>为什么现在会提示网络不可用？</strong></summary>

正常的首次启动会使用安装包内置 Runtime，不需要联网。只有内置或本地 Runtime 损坏、版本需要升级，或者用户主动修复时，应用才会访问不可变 Runtime Release；如果网络受限、下载地址不可达或 Release 资产不完整，就会提示网络不可用且保留当前数据和可用版本。

</details>

<details>
<summary><strong>卸载会删除我的项目吗？</strong></summary>

默认不会。只有在卸载时明确选择删除本地数据，应用才会清理自己的固定数据目录；外部项目目录仍受删除确认范围保护。

</details>

## 工作原理

```text
Tauri 2 可信桌面壳（React 启动页 + 自定义标题栏）
  ├─ Rust Runtime Manager
  │    ├─ 签名验证 / 下载 / 激活 / 回滚 / 诊断
  │    ├─ Generation 与 Profile 生命周期
  │    └─ Managed Node 24 + DeepSeek Harness + Desktop 插件
  │        └─ 127.0.0.1:<随机端口> 官方工作台 UI（桌面布局）
  └─ 本地应用启动器
       ├─ 应用清单校验 / 健康检查 / 生命周期管理
       └─ 127.0.0.1:<随机端口> 项目构建出的本地应用
```

桌面壳只负责原生窗口、受管 Runtime 和安全边界。Agent、会话、工具、模型与 Web UI 仍由 DeepSeek Harness 提供；桌面布局、本地项目入口和 Profile 界面通过普通 DeepSeek Harness 插件组合进去。

## 开发者指南

### 环境与质量检查

需要 Node.js、npm、Rust stable，以及 Windows 原生构建工具。安装依赖并运行完整前端门禁：

```bash
npm ci --legacy-peer-deps
npm run check        # 根测试 + 插件测试 + Web 构建 + 插件构建
```

运行 Rust 测试及安装器资源校验：

```bash
cargo test --manifest-path src-tauri/Cargo.toml --locked
npm run installer:verify
npm run icon:verify
```

本地启动 Tauri 开发壳（需要可访问且签名有效的 Runtime 清单）：

```bash
DSH_DESKTOP_RUNTIME_MANIFEST_URL=<manifest-url> npm run tauri dev
```

### Runtime 组装与签名

```bash
npm run runtime:build -- --target=windows-x86_64 --version=<runtime-version> --url=https://github.com/XingAur/deepseek-harness-desktop/releases/download/runtime-v<runtime-version>/dsh-runtime-windows-x86_64.zip

node scripts/sign-manifest.mjs \
  runtime-build/windows-x86_64/manifest-windows-x86_64.unsigned.json \
  runtime/manifests/runtime-windows-x86_64.json
```

签名脚本读取以下 Ed25519 raw JWK 环境变量：

- `DSH_DESKTOP_SIGNING_PUBLIC_KEY`
- `DSH_DESKTOP_SIGNING_PRIVATE_KEY`

仓库中的密钥只能用于开发测试。生产私钥必须保存在 GitHub Secrets 或等价的发布密钥服务中。

### 自动上游同步与发布

`release/versions.json` 是桌面版、受管 Runtime、DeepSeek Harness、Node.js 和 pnpm 的统一版本源。只读一致性检查为：

```bash
npm run release:versions:check
```

以下准备命令会在确认上游版本更高后修改统一版本源及 5 个派生版本文件；请在独立分支或干净工作区中执行：

```bash
npm run release:prepare -- --latest=<已确认存在的精确版本>
```

`.github/workflows/upstream-watch.yml` 每 4 小时观察官方源码与 npm 分发，只向 `automation/deepseek-harness-upstream` 推送经过测试的允许清单文件并创建或刷新 PR。维护者人工审核并合并后，才可手动运行 `.github/workflows/upstream-sync.yml` 进入精确标签发布流程。正式发布需要在 GitHub Secrets 中配置 Runtime Ed25519 密钥和 Windows Tauri updater 私钥；macOS 构建不要求 Apple 证书，但必须继续明确标为未签名、未公证。发布工作流只补齐缺失资产，对同名不同内容的不可变资产会直接失败。完整规则见[上游跟踪与升级策略](docs/upstream-policy.md)。

需要配置的 GitHub Secrets：

- `DSH_DESKTOP_SIGNING_PUBLIC_KEY`、`DSH_DESKTOP_SIGNING_PRIVATE_KEY`：受管 Runtime 清单 Ed25519 密钥；
- `TAURI_UPDATER_PUBLIC_KEY`、`TAURI_SIGNING_PRIVATE_KEY`：Windows Tauri updater 公私钥；
- `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`：仅当 Windows updater 私钥设置了密码时需要；
- `GITHUB_TOKEN` 由 GitHub Actions 自动提供，不应另存个人访问令牌。

Runtime 与 Desktop Release 都先停留在草稿状态并校验精确资产集合；Runtime 归档先于其签名清单上传，以便中断后从不可变归档重建清单。Windows 构建还会在上传前使用编译进应用的公钥重新验证 updater 包和 `.sig`，公私钥错配会直接阻断发布。

当前仓库已实现并在本地验证发布状态机和构建契约；GitHub 托管 Windows/macOS runner、真实 Release 中断恢复、Windows 已安装旧版本升级以及 Apple Silicon 实机 Gatekeeper/DMG 覆盖仍需要推送后在真实环境完成验收，不能仅凭本地测试视为已验证。

未来接入 Codex、Claude、其他模型、Plugins、Skills 和 MCP 的职责及安全边界见 [扩展平台架构](docs/architecture/extension-platform.md)。该文档是架构基础，不代表这些 Provider 已经实现。

### Windows 构建

正式 Windows 完整安装包会嵌入已签名 Runtime，同时必须把不可变 Runtime 清单地址和对应公钥编译进应用，用于后续修复与升级：

```bash
export DSH_DESKTOP_RELEASE_PUBLIC_KEY='<Ed25519 raw public JWK x>'
export DSH_DESKTOP_RUNTIME_MANIFEST_URL='https://github.com/XingAur/deepseek-harness-desktop/releases/download/runtime-v<runtime-version>/runtime-{target}.json'
npm run installer:windows
```

输出位于：

```text
src-tauri/target/release/bundle/nsis/DeepSeek-Harness-v<desktop-version>-Windows-x64.exe
```

### 项目结构

```text
src/                           Tauri 启动、恢复和诊断 React UI
src-tauri/                     Rust Runtime Manager、原生窗口、安全边界与本地应用启动器
packages/dsh-plugin-desktop/   桌面布局、本地项目和 Profile 界面插件
runtime/                       Runtime 格式与签名清单说明
release/                       统一版本源
scripts/                       构建、签名、验证和端到端测试工具
e2e/                           真实安装包与工作台测试
docs/architecture/             扩展平台等架构文档
```

## 独立项目与商标说明

DeepSeek Harness Desktop 是独立社区项目，与 DeepSeek 不存在隶属、合作、授权或背书关系。“DeepSeek”及相关标识归其权利人所有，本项目仅为说明兼容对象而使用相关名称。

当前仓库尚未添加根级 `LICENSE` 文件；对外发布前应明确本项目自身许可证。DeepSeek Harness 及所有第三方依赖继续遵循各自许可证，详见[第三方许可证声明](THIRD_PARTY_NOTICES.md)。
