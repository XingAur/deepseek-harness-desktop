# DeepSeek Harness Desktop

面向 Windows x64 与 macOS Apple Silicon 的 DeepSeek Harness 桌面客户端。

本项目使用 Tauri 2 + React + Rust 重写。目标不是给 DeepSeek Harness（DSH）再套一层管理后台，而是直接承载官方 DeepSeek Harness Web UI，并通过普通 DeepSeek Harness 插件组合桌面布局与社区插件市场。普通用户不需要预装 Node.js、pnpm 或 DeepSeek Harness。

> 社区项目，并非 DeepSeek 官方产品。DeepSeek 是 DeepSeek AI 的商标。

## 首版能力

- Windows x64 NSIS 与 macOS Apple Silicon DMG 两个平台配置。
- 首启联网获取受管 Runtime，展示下载、校验、激活和启动进度。
- Ed25519 清单签名、SHA-256 制品校验、断点续传、安全解压和版本回滚。
- Runtime 启动失败可重试、修复、取消下载并导出诊断 ZIP。
- 成功启动后直接进入官方 DeepSeek Harness 工作台；桌面插件复用官方 sidebar、conversation、details 与 overlay slots。
- 一级“社区插件”入口，支持签名精选目录内插件的一键安装、更新、卸载、取消和实时日志。
- 插件写操作仍由官方 `dsh plugin --profile desktop ...` 执行，不在桌面端另造包管理语义。
- Runtime 更新失败时继续使用已验证的现有版本；新版本只有健康检查通过后才完成切换。

首版明确不支持 Linux、macOS Intel、旧版数据迁移、应用商店分发、TUI、Headless 和完全离线安装。

## 架构

```text
Tauri 本地首启页
       │ 4 个受限 command
       ▼
Rust Runtime Manager
签名校验 / 下载 / 解压 / 激活 / 回滚 / 诊断
       │
       ▼
Managed Node 24 + DeepSeek Harness 0.1.0-rc.7 + pnpm
       │ 127.0.0.1 随机端口
       ▼
官方 DeepSeek Harness Web UI + @dsh/desktop-plugin
官方工作台 slots + 社区插件市场
```

Tauri 本地可信壳始终负责窗口标题栏与 Runtime 生命周期状态；Runtime 就绪后，官方 DeepSeek Harness Web UI 会嵌入受限的 loopback iframe。iframe 内的普通 Web 页面不持有通用 Tauri IPC，也不能任意执行本机命令。

## 社区插件安全边界

GitHub 的 [`dsh-plugin` Topic](https://github.com/topics/dsh-plugin) 只作为候选发现来源，不会自动变成可执行目录。应用只展示经人工核对并签名的精选清单：

- 校验 Ed25519 目录签名并保留 last-known-good 缓存；
- 校验目标平台与 DeepSeek Harness semver 范围；
- 只允许目录中固定的包名、版本和 GitHub HTTPS 仓库；
- 安装前明确提示第三方插件权限风险；
- 同一时间只允许一个插件写操作；
- 命令使用固定 Node/DeepSeek Harness 入口、参数数组和 `shell: false`；
- 写接口要求精确的 `127.0.0.1:<port>` 同源 POST。

首个精选条目是已核对 npm manifest、GitHub 仓库和 provenance 的 `dsh-find-plugin@0.3.6`。精选不代表插件无风险，用户仍应在安装前查看来源。

## 开发

前端与桌面插件检查不要求 Rust：

```bash
cd deepseek-harness-desktop
npm ci --legacy-peer-deps
npm run check
```

`npm run check` 会执行 React/Vitest 测试、插件测试、Web 构建和 Desktop 插件构建。

运行 Tauri 开发壳需要 Rust stable 与对应系统的原生构建依赖：

```bash
npm run tauri dev
```

开发壳还需要一个可用的签名 Runtime 清单。可以设置：

```text
DSH_DESKTOP_RUNTIME_MANIFEST_URL=https://example.com/runtime-{target}.json
```

也可以先使用下面的脚本在当前平台准备清单。

## Runtime 组装与签名

```bash
npm run runtime:build -- \
  --target=windows-x86_64 \
  --version=0.1.0 \
  --url=https://example.com/dsh-runtime-windows-x86_64.zip

node scripts/sign-manifest.mjs \
  runtime-build/windows-x86_64/manifest-windows-x86_64.unsigned.json \
  runtime/manifests/runtime-windows-x86_64.json
```

macOS 目标为 `darwin-aarch64`，制品扩展名为 `.tar.gz`。签名脚本从环境变量读取 Ed25519 raw JWK 坐标：

- `DSH_DESKTOP_SIGNING_PUBLIC_KEY`
- `DSH_DESKTOP_SIGNING_PRIVATE_KEY`

仓库内公私钥只用于开发测试。正式 tag 流水线要求 GitHub Secrets 提供生产密钥，并通过 `DSH_DESKTOP_RELEASE_PUBLIC_KEY` 将对应公钥编译进 Tauri 壳。生产私钥不得写入仓库或安装包。

## 发布

`.github/workflows/desktop.yml` 在两套原生 runner 上分别完成：

1. TypeScript、React 与插件检查；
2. 精选插件目录签名；
3. 对应平台 Managed Runtime 组装与清单签名；
4. Windows x64 NSIS 或 macOS arm64 DMG 构建；
5. tag 构建汇总为 GitHub draft release。

macOS 正式分发还需要配置 Apple Developer 签名与 notarization secrets。当前版本不发布 Microsoft Store 或 Mac App Store 包。

## 代码目录

- `src/`：Tauri 首启、恢复和诊断 React UI。
- `src-tauri/`：Rust Runtime Manager 与最小 Tauri 权限面。
- `packages/dsh-plugin-desktop/`：DeepSeek Harness Host/Client 插件、桌面布局和社区市场。
- `runtime/`：精选目录及发布时写入的签名 Runtime 清单。
- `scripts/`：Runtime 组装、canonical JSON 和 Ed25519 签名脚本。

## 参考与致谢

桌面工作台组合方式参考并适配自 [`anywhere-labs/deepseek-harness-desktop`](https://github.com/anywhere-labs/deepseek-harness-desktop)，遵循其 MIT License；完整说明见 `packages/dsh-plugin-desktop/THIRD_PARTY_NOTICES.md`。

核心 Agent、会话、工具、Web UI 与插件体系来自 [`deepseek-ai/deepseek-harness`](https://github.com/deepseek-ai/deepseek-harness)。
