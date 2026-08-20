<p align="center">
  <img src="src-tauri/icons/icon.png" width="88" alt="DeepSeek Harness Desktop 图标" />
</p>

<h1 align="center">DeepSeek Harness Desktop</h1>

<p align="center">
  让普通用户无需命令行，即可在 Windows 上安装并使用 DeepSeek Harness。
</p>

> [!IMPORTANT]
> 这是独立维护的社区项目，并非 DeepSeek 官方产品。首个公开预览版正在准备中；线上 Runtime 发布完成前，本仓库暂不提供公开安装包下载。

## 它能做什么

DeepSeek Harness Desktop 把官方 DeepSeek Harness Web 工作台放进原生桌面窗口，并负责普通用户不应该手动处理的本地环境工作：

- 自动准备并启动受管 Runtime，无需预装 Node.js、pnpm 或 DeepSeek Harness。
- 直接进入官方工作台，不另外复制一套 Agent、会话或工具界面。
- 使用当前 Profile 的官方 Workspace 数据展示“本地项目”。
- 管理 Runtime 的下载、签名验证、健康检查、升级、回滚和诊断。
- 支持浅色、深色和跟随系统主题，并让桌面标题栏与工作台保持一致。
- 提供系统托盘、应用更新、受限导航和固定用户数据目录。

## 安装与首次启动

当前交付重点是 **Windows x64 在线安装版**。

安装程序只安装体积较小的桌面壳。首次打开应用时，启动页会自动：

1. 获取固定版本的 Runtime 清单；
2. 下载 Runtime；
3. 校验 Ed25519 签名和 SHA-256；
4. 在隔离环境中启动并检查工作台是否真正可用；
5. 验证成功后进入 DeepSeek Harness 工作台。

整个过程都在应用启动页中完成，不会在安装器中额外弹出命令行或下载窗口。首次准备需要联网；失败时可以重试、修复或导出诊断文件。

目前 GitHub 上尚未发布项目所需的不可变 Runtime Release，因此暂不提供面向普通用户的下载按钮。Release 就绪并完成安装包端到端验证后，会在这里提供正式入口。

## 日常使用

### 本地项目

侧栏中的“本地项目”来自当前 Profile 的 Workspace 列表，不维护第二份容易失真的项目数据库。

- 单击卡片选中项目，并可在下方对话框快捷修改。
- 双击卡片启动项目。
- 右键可以修改名称、选择内置颜色或渐变封面、置顶或删除项目。
- 删除操作必须二次确认，并由用户选择只移除记录，还是把已登记的项目目录移到 Windows 回收站。
- 列表为空时，可以直接描述想做的项目；应用会先确认需求、绝对路径、Profile、权限和命令类别，再通过真实 DeepSeek Harness 会话开始构建。

### Profile

Profile 是一套相互隔离的工作环境。不同 Profile 可以拥有不同的工作区、会话、插件配置、权限和数据根。

大多数用户只需要默认 Profile。需要区分工作与个人项目、不同客户环境或不同权限时，才需要创建第二个 Profile。

切换 Profile 使用 pending / last-known-good 机制：新环境完全就绪后才正式生效；如果启动失败或上次切换被中断，应用会自动恢复到最后一次确认可用的环境。

### 恢复、诊断与更新

- Runtime 启动失败时，可以重试或执行修复下载。
- “查看详情”显示失败阶段和技术信息，主提示保持面向普通用户。
- “导出诊断”生成已经脱敏的 ZIP，便于定位网络、版本或进程问题。
- 应用本体更新和 Runtime 更新是两个独立的签名通道，互不混用。
- 应用更新检查失败不会阻止已经可用的工作台启动。

## 为什么后续启动更快

首次启动必须下载并验证 Runtime，因此耗时取决于网络和压缩包大小。

后续启动会优先检查本地的验证凭据和 Runtime 版本。只要版本一致、文件完整且健康检查通过，应用会直接启动本地 Runtime，不等待联网检查。只有版本不兼容、文件损坏或用户主动修复时，才会重新下载。

## 数据与安全

- Windows 用户数据固定保存在 `%LOCALAPPDATA%\ai.deepseek.harness.desktop`，不会跟随安装目录漂移。
- 默认卸载只移除应用，保留 Profile、Workspace 和会话数据。
- 如果卸载时选择删除本地数据，卸载器会先把固定数据目录原子移动到待清理位置，再由后台清理程序释放磁盘空间。
- Runtime 清单使用 Ed25519 签名，下载制品使用 SHA-256 校验；新版本通过完整健康检查后才提交，失败会回滚。
- 工作台只监听随机的 `127.0.0.1` 端口。
- 嵌入的工作台页面不持有不受限制的 Tauri IPC；本机操作只能经过类型化、动作白名单化的桥接接口。
- 顶层导航只允许受管本地页面；外部 HTTPS 地址验证后交给系统浏览器，其余协议默认拒绝。
- 诊断导出会清理常见令牌、认证头和敏感环境变量。

## 当前支持范围

| 平台 | 状态 |
| --- | --- |
| Windows x64 | 当前发布目标；在线安装、首次 Runtime 准备和后续快速启动已实现 |
| macOS Apple Silicon | 保留构建配置，当前 Windows 预览版不以它作为发布承诺 |
| Windows ARM64、macOS Intel、Linux | 当前不支持 |

首个公开版本还需要完成：发布不可变且已签名的 Windows Runtime、配置生产签名密钥、运行真实安装包端到端验证，并发布 GitHub Release。

## 常见问题

<details>
<summary><strong>为什么安装包很小，第一次打开却需要下载？</strong></summary>

在线安装包只携带 Tauri 桌面壳。DeepSeek Harness、Node.js、pnpm 和桌面插件组成受管 Runtime，在首次打开应用时按平台下载。这样可以缩小安装包，也能独立升级和回滚 Runtime。

</details>

<details>
<summary><strong>第二次打开还会等很久吗？</strong></summary>

通常不会。健康且版本一致的本地 Runtime 会直接启动，不等待远程更新检查。版本变化、文件损坏或修复操作才会进入下载流程。

</details>

<details>
<summary><strong>为什么现在会提示网络不可用？</strong></summary>

当前预览安装包指向的 Runtime Release 尚未发布，对应 URL 会返回 404。代码推送和 Runtime 发布是两个步骤；必须先生成、签名并上传 Runtime 清单与压缩包，在线首次启动才能完成。

</details>

<details>
<summary><strong>卸载会删除我的项目吗？</strong></summary>

默认不会。只有在卸载时明确选择删除本地数据，应用才会清理自己的固定数据目录；外部项目目录仍受删除确认范围保护。

</details>

## 工作原理

```text
Tauri 2 可信桌面壳
  └─ Rust Runtime Manager
      ├─ 签名验证 / 下载 / 激活 / 回滚 / 诊断
      ├─ Generation 与 Profile 生命周期
      └─ Managed Node 24 + DeepSeek Harness + Desktop 插件
          └─ 127.0.0.1:<随机端口> 官方工作台 UI
```

桌面壳只负责原生窗口、受管 Runtime 和安全边界。Agent、会话、工具、模型与 Web UI 仍由 DeepSeek Harness 提供；桌面布局和本地项目入口通过普通 DeepSeek Harness 插件组合进去。

## 开发者指南

### 环境与质量检查

需要 Node.js、npm、Rust stable，以及 Windows 原生构建工具。安装依赖并运行完整前端门禁：

```powershell
npm ci --legacy-peer-deps
npm run check
```

运行 Rust 测试及安装器资源校验：

```powershell
cargo test --manifest-path src-tauri/Cargo.toml --locked
npm run installer:verify
npm run icon:verify
```

本地启动 Tauri 开发壳：

```powershell
npm run tauri dev
```

开发壳需要可访问且签名有效的 Runtime 清单。

### Runtime 组装与签名

```powershell
npm run runtime:build -- --target=windows-x86_64 --version=0.1.0-preview --url=https://example.com/dsh-runtime-windows-x86_64.zip

node scripts/sign-manifest.mjs `
  runtime-build/windows-x86_64/manifest-windows-x86_64.unsigned.json `
  runtime/manifests/runtime-windows-x86_64.json
```

签名脚本读取以下 Ed25519 raw JWK 环境变量：

- `DSH_DESKTOP_SIGNING_PUBLIC_KEY`
- `DSH_DESKTOP_SIGNING_PRIVATE_KEY`

仓库中的密钥只能用于开发测试。生产私钥必须保存在 GitHub Secrets 或等价的发布密钥服务中。

### Windows 构建

正式在线安装包必须把不可变 Runtime 清单地址编译进应用：

```powershell
$env:DSH_DESKTOP_RUNTIME_MANIFEST_URL = 'https://github.com/<owner>/<repo>/releases/download/runtime-v0.1.0-preview/runtime-windows-x86_64.json'
npm run tauri -- build --bundles nsis --target x86_64-pc-windows-msvc
```

输出位于：

```text
src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis/
```

### 项目结构

```text
src/                           Tauri 启动、恢复和诊断 React UI
src-tauri/                     Rust Runtime Manager、原生窗口与安全边界
packages/dsh-plugin-desktop/   工作台布局、本地项目和 Profile 界面
runtime/                       Runtime 格式与签名清单说明
scripts/                       构建、签名、验证和端到端测试工具
e2e/                           真实安装包与工作台测试
```

### 发布要求

公开发布前必须满足：

1. 创建不可变的 `runtime-v<version>` 预发布版本；
2. 上传签名后的 `runtime-windows-x86_64.json` 和对应 Runtime ZIP；
3. 配置 Runtime 与 Tauri updater 的生产签名密钥；
4. 构建真实 Windows x64 在线安装包；
5. 在干净 Windows 环境验证首次安装、首次启动、后续启动、兼容重装和卸载；
6. 所有门禁通过后再公开桌面 Release。

## 参考与致谢

- 核心 Agent、会话、工具、模型接口和 Web UI 来自 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)。
- 产品定位、插件化桌面组合和文档分层参考了 [anywhere-labs/deepseek-harness-desktop](https://github.com/anywhere-labs/deepseek-harness-desktop)。
- 用户优先的安装说明、运行架构、验证状态和安全表达参考了 [dataelement/dsh-desktop](https://github.com/dataelement/dsh-desktop)。

项目只借鉴公开设计与工程经验，功能说明均按本仓库当前实现重新编写。第三方代码和许可证说明见各包内的 notices 文件。

## 独立项目与商标说明

DeepSeek Harness Desktop 是独立社区项目，与 DeepSeek 不存在隶属、合作、授权或背书关系。“DeepSeek”及相关标识归其权利人所有，本项目仅为说明兼容对象而使用相关名称。

当前仓库尚未添加根级 `LICENSE` 文件；对外发布前应明确本项目自身许可证。DeepSeek Harness 及所有第三方依赖继续遵循各自许可证。
