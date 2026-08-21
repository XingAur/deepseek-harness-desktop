<p align="center">
  <img src="src-tauri/icons/icon.png" width="88" alt="DeepSeek Harness Desktop 图标" />
</p>

<h1 align="center">DeepSeek Harness Desktop</h1>

<p align="center">
  让普通用户无需命令行，即可在 Windows 上安装并使用 DeepSeek Harness。
</p>

> [!IMPORTANT]
> 这是独立维护的社区项目，并非 DeepSeek 官方产品。

## 它能做什么

DeepSeek Harness Desktop 把官方 DeepSeek Harness Web 工作台放进原生桌面窗口，并负责普通用户不应该手动处理的本地环境工作：

- 自动准备并启动受管 Runtime（Node.js 24 + pnpm + 官方 DeepSeek Harness），无需预装任何依赖。
- 在原生窗口中呈现桌面版工作台布局：官方侧栏、会话区、详情面板三栏结构，侧栏与详情宽度可调，并带“本地项目”快捷入口。
- 使用当前 Profile 的官方 Workspace 数据展示“本地项目”，不维护第二份容易失真的项目数据库。
- 通过 Profile 隔离不同的工作环境，支持创建、复制、编辑、删除和切换。
- 支持浅色、深色和跟随系统主题，桌面标题栏与工作台保持一致。
- 提供系统托盘、应用更新、受限导航和固定用户数据目录。
- 管理 Runtime 的下载、签名验证、健康检查、升级、回滚和诊断导出。

## 安装与首次启动

当前交付重点是 **Windows x64 完整安装包**。

安装程序同时携带桌面应用和已签名的受管 Runtime。首次打开应用时，启动页会自动：

1. 读取并验证内置 Runtime 清单；
2. 解压内置 Runtime；
3. 校验 Ed25519 签名和 SHA-256；
4. 在隔离环境中启动并检查工作台是否真正可用；
5. 验证成功后进入 DeepSeek Harness 工作台。

整个过程都在应用启动页中完成，不会在安装器中额外弹出命令行或下载窗口。正常的首次准备不依赖网络；如果内置 Runtime 损坏、版本需要升级或用户主动修复，应用才会从不可变 Runtime Release 下载并验证替代版本。失败时可以重试、修复或导出诊断文件。

目前 GitHub 上尚未发布项目所需的不可变 Runtime Release，因此暂不提供面向普通用户的下载按钮。Release 就绪并完成安装包端到端验证后，会在这里提供正式入口。

## 日常使用

### 工作台与本地项目

应用启动后直接进入官方工作台的桌面布局。侧栏中的“本地项目”来自当前 Profile 的 Workspace 列表：

- 单击卡片选中项目，并可在下方对话框快捷修改；
- 双击卡片启动项目；
- 右键可以修改名称、选择内置颜色或渐变封面、置顶或删除项目；
- 删除操作必须二次确认，并由用户选择只移除记录，还是把已登记的项目目录移到 Windows 回收站；
- 列表为空时，可以直接描述想做的项目；应用会先确认需求、绝对路径、Profile、权限和命令类别，再通过真实 DeepSeek Harness 会话开始构建。

### Profile

Profile 是一套相互隔离的工作环境。不同 Profile 拥有各自的工作区、会话、插件配置、权限和数据根。

大多数用户只需要默认 Profile。需要区分工作与个人项目、不同客户环境或不同权限时，才需要创建第二个 Profile。Profile 管理入口位于工作台设置中的 Profiles 区，支持创建、复制、编辑、删除和切换。

切换 Profile 使用 pending / last-known-good 机制：新环境完全就绪后才正式生效；如果启动失败或上次切换被中断，应用会自动恢复到最后一次确认可用的环境。

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
- 应用本体更新和 Runtime 更新是两个独立的签名通道，互不混用；
- 应用更新可以立即安装、在退出应用时安装，或暂时跳过；更新检查失败不会阻止已经可用的工作台启动。

## 为什么后续启动更快

首次启动需要验证并解压内置 Runtime，因此耗时主要取决于压缩包大小、磁盘性能和安全软件扫描。

后续启动会优先检查本地的验证凭据和 Runtime 版本。只要版本一致、文件完整且健康检查通过，应用会直接启动本地 Runtime，不等待联网检查。只有版本不兼容、文件损坏或用户主动修复时，才会重新下载。

## 数据与安全

- Windows 用户数据固定保存在 `%LOCALAPPDATA%\ai.deepseek.harness.desktop`，不会跟随安装目录漂移。
- 默认卸载只移除应用，保留 Profile、Workspace 和会话数据；卸载过程不阻塞等待大文件删除。
- 如果卸载时选择删除本地数据，卸载器会先把固定数据目录原子移动到待清理位置，再由后台清理程序释放磁盘空间。
- Runtime 清单使用 Ed25519 签名，下载制品使用 SHA-256 校验；新版本通过完整健康检查后才提交，失败会回滚。
- 工作台只监听随机的 `127.0.0.1` 端口。
- 嵌入的工作台页面不持有不受限制的 Tauri IPC；本机操作只能经过类型化、动作白名单化的桥接接口。
- 顶层导航只允许受管本地页面；外部 HTTPS 地址验证后交给系统浏览器，其余协议默认拒绝。
- 诊断导出会清理常见令牌、认证头和敏感环境变量。

## 当前支持范围

| 平台 | 状态 |
| --- | --- |
| Windows x64 | 当前发布目标；完整安装包、首次内置 Runtime 准备和后续快速启动已实现 |
| macOS Apple Silicon | 保留构建配置，当前 Windows 预览版不以它作为发布承诺 |
| Windows ARM64、macOS Intel、Linux | 当前不支持 |

首个公开版本还需要完成：发布不可变且已签名的 Windows Runtime、配置生产签名密钥、运行真实安装包端到端验证，并发布 GitHub Release。

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
<summary><strong>为什么现在会提示网络不可用？</strong></summary>

正常的首次启动会使用安装包内置 Runtime，不需要联网。只有内置或本地 Runtime 损坏、版本需要升级，或者用户主动修复时，应用才会访问不可变 Runtime Release；如果该 Release 尚未发布、网络受限或下载地址不可达，就会提示网络不可用。

</details>

<details>
<summary><strong>卸载会删除我的项目吗？</strong></summary>

默认不会。只有在卸载时明确选择删除本地数据，应用才会清理自己的固定数据目录；外部项目目录仍受删除确认范围保护。

</details>

## 工作原理

```text
Tauri 2 可信桌面壳（React 启动页 + 自定义标题栏）
  └─ Rust Runtime Manager
      ├─ 签名验证 / 下载 / 激活 / 回滚 / 诊断
      ├─ Generation 与 Profile 生命周期
      └─ Managed Node 24 + DeepSeek Harness + Desktop 插件
          └─ 127.0.0.1:<随机端口> 官方工作台 UI（桌面布局）
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
npm run runtime:build -- --target=windows-x86_64 --version=0.1.2-preview --url=https://example.com/dsh-runtime-windows-x86_64.zip

node scripts/sign-manifest.mjs \
  runtime-build/windows-x86_64/manifest-windows-x86_64.unsigned.json \
  runtime/manifests/runtime-windows-x86_64.json
```

签名脚本读取以下 Ed25519 raw JWK 环境变量：

- `DSH_DESKTOP_SIGNING_PUBLIC_KEY`
- `DSH_DESKTOP_SIGNING_PRIVATE_KEY`

仓库中的密钥只能用于开发测试。生产私钥必须保存在 GitHub Secrets 或等价的发布密钥服务中。

### Windows 构建

正式 Windows 完整安装包会嵌入已签名 Runtime，同时必须把不可变 Runtime 清单地址和对应公钥编译进应用，用于后续修复与升级：

```bash
export DSH_DESKTOP_RELEASE_PUBLIC_KEY='<Ed25519 raw public JWK x>'
export DSH_DESKTOP_RUNTIME_MANIFEST_URL='https://github.com/<owner>/<repo>/releases/download/runtime-v0.1.2-preview/runtime-{target}.json'
npm run installer:windows
```

输出位于：

```text
src-tauri/target/release/bundle/nsis/DeepSeek-Harness-v0.1.0-Windows-x64.exe
```

### 项目结构

```text
src/                           Tauri 启动、恢复和诊断 React UI
src-tauri/                     Rust Runtime Manager、原生窗口与安全边界
packages/dsh-plugin-desktop/   桌面布局、本地项目和 Profile 界面插件
runtime/                       Runtime 格式与签名清单说明
scripts/                       构建、签名、验证和端到端测试工具
e2e/                           真实安装包与工作台测试
```

## 独立项目与商标说明

DeepSeek Harness Desktop 是独立社区项目，与 DeepSeek 不存在隶属、合作、授权或背书关系。“DeepSeek”及相关标识归其权利人所有，本项目仅为说明兼容对象而使用相关名称。

当前仓库尚未添加根级 `LICENSE` 文件；对外发布前应明确本项目自身许可证。DeepSeek Harness 及所有第三方依赖继续遵循各自许可证。
