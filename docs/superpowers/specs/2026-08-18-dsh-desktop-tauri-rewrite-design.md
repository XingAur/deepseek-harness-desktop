# DSH Desktop Tauri 重写设计

- 日期：2026-08-18
- 状态：已获产品方向批准
- 首发平台：Windows x64、macOS Apple Silicon（arm64）
- 参考实现：`anywhere-labs/deepseek-harness-desktop`、`hairyf/deepseek-harness-desktop`

## 1. 背景

现有项目是 Windows 专用 Electron 客户端，已经包含部分主进程能力，但渲染层、首启体验、运行时自愈、插件生态和跨平台边界不完整。本次不在旧实现上继续堆叠，而是按全新产品重写，不迁移旧数据。

产品外观与组合方式以 `anywhere-labs/deepseek-harness-desktop` README 展示的 DSH 工作台为基准：官方 DSH 会话、工作区、模式、轨迹、输入框、状态栏和设置保持主导。桌面应用只补充系统集成、运行时管理和社区插件市场，不再设计一套独立管理后台包裹官方界面。

## 2. 目标

首版必须实现：

1. 提供 Windows x64 与 macOS arm64 的直接安装包。
2. 使用 Tauri 2 + React + Rust 构建轻量桌面壳。
3. 首次启动可联网自动准备 Node/DSH 受管运行时，无需用户操作终端。
4. 运行成功后直接展示由 Desktop Profile 组合出的官方 DSH Web UI。
5. 在官方工作台中加入一级“社区插件”入口。
6. 支持经过筛选的社区插件一键安装、更新和卸载。
7. 提供运行进度、取消、故障恢复和诊断导出。
8. 桌面壳、DSH Runtime、社区插件采用彼此独立的更新通道。

## 3. 非目标

首版不包含：

- Linux 构建与验收；架构避免无必要的平台锁死，但 Linux 延后。
- macOS Intel 或 Universal 安装包。
- TUI 与 Headless 客户端；只在服务边界上保留未来扩展空间。
- 旧版本设置、项目、插件或数据目录迁移。
- Microsoft Store、Mac App Store 或其他应用商店发布。
- 完全离线安装包。
- 将 GitHub `dsh-plugin` Topic 中的所有仓库直接列为可安装插件。
- 对第三方插件提供完整沙箱隔离或安全担保。

## 4. 核心原则

### 4.1 官方界面优先

会话、工作区、输入、轨迹、模式、详情面板和设置继续由官方 DSH surface 渲染。桌面插件只重新组合已有 slot、补充标题栏留白与布局状态，并注册社区市场 surface。

### 4.2 壳芯分离

Tauri/Rust 负责操作系统能力与 DSH 进程生命周期；DSH Host 负责 Agent、会话、工具和插件语义；React 本地页面只负责首启、恢复和致命错误。

### 4.3 最小权限

官方 DSH Web UI 不获得通用 Tauri IPC。需要系统权限的行为只能通过明确的 Rust command 或受控 DSH Host 插件完成，不能从普通网页直接执行任意命令。

### 4.4 上游兼容优先

不 fork 或复制官方会话页面。Desktop 插件依赖稳定 slot 与 Host 插件接口，并通过版本锁定和契约测试控制上游变化。

## 5. 总体架构

```text
┌──────────────────── Tauri 2 Desktop ────────────────────┐
│                                                         │
│  Local React UI                                         │
│  首启 / 下载 / 修复 / 诊断                              │
│            │ 受限 Tauri commands                        │
│            ▼                                            │
│  Rust Runtime Manager                                   │
│  清单校验 / 下载 / 版本切换 / 进程树 / 健康检查 / 托盘   │
│            │ 启动                                       │
│            ▼                                            │
│  Managed Node + DSH Host (Desktop Profile)              │
│  ├─ 官方 Host 能力                                      │
│  ├─ Desktop Host Plugin                                 │
│  └─ Community Plugin Service                            │
│            │ 127.0.0.1 + 每次启动会话令牌               │
│            ▼                                            │
│  Official DSH Web UI + Desktop Client Plugin            │
│  官方 surfaces / Desktop root layout / 社区插件市场      │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 5.1 Tauri 应用壳

职责：

- 创建窗口、托盘和系统菜单。
- 选择 Windows/macOS 对应窗口效果。
- 在本地 React 页面与 DSH loopback 页面之间切换。
- 处理应用退出、隐藏、恢复和单实例。
- 使用 Tauri updater 检查桌面壳更新。

Tauri capability 只授予本地引导页面所需命令。导航到 loopback DSH 页面后，不暴露文件系统、shell 或通用进程 API。

### 5.2 Rust Runtime Manager

职责：

- 获取并验证平台运行时清单。
- 下载 Node/DSH/Desktop 插件制品，支持断点续传。
- 校验 HTTPS 来源、签名、SHA-256 与目标平台。
- 使用版本目录和原子 `current` 指针完成切换。
- 启动并管理完整 DSH 进程树。
- 执行健康检查、崩溃检测、有限自动重启和有序退出。
- 生成脱敏诊断包。

Rust 层不重新实现 DSH 的插件解析、profile 或依赖语义。

### 5.3 Desktop Host/Client 插件

Desktop 插件属于普通 DSH 插件组合，分为 Host 与 Client 两部分：

- Client：组合官方 `sidebar`、`conversation`、`details`、`shell.overlay` 等 surface；保存三栏宽度和折叠状态；注册“社区插件”入口与市场页面。
- Host：读取精选目录、校验安装坐标、调用官方 `dsh plugin`、流式传输 stdout/stderr、取消操作并刷新插件状态。
- Layout：参考 MIT 许可的 `anywhere-labs/dsh-plugin-desktop` 布局实现，保留版权与许可证声明；不复制或声称拥有第三方品牌资产。

## 6. 启动状态机

```text
Boot
  ├─ No runtime ──> Fetch manifest -> Download -> Verify -> Activate
  ├─ Runtime old ─> Check compatibility -> Stage update -> Activate
  └─ Runtime ready
          ↓
Start DSH -> Wait for health -> Open official UI -> Ready
    │              │
    └─ failed ─────┴─> Recovery
                         ├─ Retry
                         ├─ Repair runtime
                         └─ Export diagnostics
```

### 6.1 首次启动

1. 本地引导页显示当前步骤、下载体积、速度和可取消状态。
2. Runtime Manager 获取签名平台清单。
3. 仅下载 Windows x64 或 macOS arm64 对应制品。
4. 所有制品先进入 staging 目录，完整校验后再原子激活。
5. 启动 Desktop Profile，等待 HTTP 与 DSH 应用级健康检查同时通过。
6. 同一 WebView 导航到 loopback 官方 UI。

### 6.2 日常启动

已有兼容运行时直接启动，不阻塞等待非关键更新。应用进入 Ready 后在后台检查桌面壳、Runtime 与插件目录版本，只展示提示，不静默替换正在运行的核心。

### 6.3 Loopback 会话

- DSH 只绑定 `127.0.0.1` 的随机可用端口。
- 每次启动生成短期随机会话令牌。
- 首次导航完成令牌交换后使用 `HttpOnly`、`SameSite=Strict` 会话 cookie。
- 不把长期凭据写入 URL、日志或诊断包。
- 外部导航统一交给系统浏览器，非允许来源不能在主 WebView 中加载。

## 7. 受管运行时与数据目录

建议根目录：

- Windows：`%LOCALAPPDATA%\DeepSeekHarnessDesktop`
- macOS：`~/Library/Application Support/DeepSeekHarnessDesktop`

逻辑结构：

```text
app-data/
├─ config/
├─ runtime/
│  ├─ versions/<runtime-version>/
│  ├─ downloads/
│  └─ current.json
├─ dsh/
│  ├─ profile/
│  └─ data/
├─ catalog/
│  ├─ current.json
│  └─ signature.json
├─ logs/
└─ diagnostics/
```

配置与运行时分离。修复运行时不能删除用户会话/profile 数据，除非用户在二次确认后选择“重置全部数据”。

## 8. 社区插件市场

### 8.1 目录来源

GitHub Topic 仅用于发现候选。应用消费由项目维护者发布的精选 JSON 目录，目录通过 HTTPS 分发并使用内置公钥验证签名。网络失败时使用最后一次验证通过的缓存。

目录最小字段：

```json
{
  "schemaVersion": 1,
  "generatedAt": "ISO-8601",
  "plugins": [
    {
      "id": "publisher/plugin",
      "name": "Plugin name",
      "description": "Short description",
      "publisher": "Publisher",
      "repository": "https://github.com/...",
      "installSpec": "validated DSH install coordinate",
      "version": "1.2.3",
      "dshRange": ">=x.y.z",
      "platforms": ["windows-x64", "darwin-arm64"],
      "verified": true
    }
  ]
}
```

目录签名只证明条目由本项目精选和未被篡改，不代表第三方代码绝对安全。安装确认页必须展示发布者、仓库、版本、来源和第三方代码警告。

### 8.2 操作模型

安装、更新、卸载均通过 Desktop Host 插件调用官方 `dsh plugin`：

1. 前端发起结构化请求，不接受任意 shell 字符串。
2. Host 再次校验插件 ID、目录版本、兼容范围和安装坐标。
3. 同一时间只允许一个插件写操作。
4. stdout/stderr 以事件流回传市场页面。
5. 用户可取消；Host 终止完整子进程树。
6. 操作完成后重新读取官方插件状态，不能只相信命令退出码。
7. 失败时保留日志并提供重试、复制错误摘要和导出诊断。

首版更新由用户明确点击，不自动安装第三方插件更新。卸载前显示依赖影响和确认对话框；若官方 CLI 拒绝卸载，界面展示原始原因，不强删文件。

## 9. 界面设计

### 9.1 主工作台

主工作台保持参考项目结构：

- 左栏：品牌、窗口控制、新会话、工作区、会话列表、设置与社区插件入口。
- 中栏：会话标题、模式、对话/轨迹切换、消息与工具过程。
- 右侧/覆盖层：详情与临时面板，按官方 surface 行为显示。
- 底部：官方输入区、模型选择、权限模式和运行指标。

不会在其外部再增加永久导航栏、仪表盘或 iframe 边框。

### 9.2 桌面外观

- Windows：隐藏原生标题栏，使用 Tauri 窗口拖拽区、圆角、阴影与 Windows 11 Mica 渐进增强；不支持的系统回退为不透明背景。
- macOS：Overlay/隐藏标题栏，定位 traffic lights，侧栏使用可用的 vibrancy/window effect。
- Web surface：使用官方 DSH 主题 token，Desktop 插件只添加透明背景、标题栏留白、拖拽区与 resize handle 样式。

验收目标是 Web 内容、信息架构和交互高度一致。原生标题栏、系统字体、阴影、模糊和 WebView 渲染允许平台差异，不以跨平台逐像素相同为验收条件。

### 9.3 引导与恢复页

本地 React 页面使用与主工作台一致的深色 token，但保持任务单一：

- 首启页：当前步骤、总进度、下载详情、取消。
- 启动页：运行时版本、启动阶段、超时提示。
- 恢复页：错误摘要、重试、修复运行时、导出诊断。
- 致命错误页：安全退出和打开日志目录。

## 10. 更新策略

### 10.1 桌面壳更新

使用 Tauri updater 与签名更新制品。下载后由用户确认重启安装。未配置正式代码签名与更新签名密钥时，只生成开发/测试包，不宣称可安全自动更新。

### 10.2 Runtime 更新

Runtime 清单独立签名，并携带 DSH、Node、Desktop 插件和兼容矩阵。新版本先安装到独立目录，健康探测通过后切换；失败继续使用上一版本。

### 10.3 社区插件更新

市场比较官方已安装状态与精选目录版本，显示“可更新”。用户点击后调用官方 CLI。Runtime 更新不得隐式改写用户安装的第三方插件，只有兼容性阻断时提示处理。

## 11. 故障恢复与诊断

### 11.1 自动恢复

- DSH 启动失败最多进行有限次数重试，使用退避，避免无限重启。
- Runtime 更新启动失败自动回退上一已知可用版本。
- 精选目录更新失败继续使用最后一次验证通过的目录。
- 插件命令失败不直接修改官方插件状态文件。

### 11.2 诊断包

诊断包包含：应用/Runtime/DSH 版本、平台架构、健康检查、脱敏日志、最近一次插件操作结果和目录签名状态。

诊断包必须排除：API Key、会话正文、用户源码、环境变量完整值、会话令牌和长期凭据。导出前展示包含内容和保存位置。

## 12. 安全边界

- 所有远程清单和更新制品执行签名与哈希校验。
- 下载解包阻止路径穿越、符号链接逃逸和写出目标目录。
- 所有可执行文件路径来自验证后的运行时清单，不接受 renderer 提供的路径。
- 插件命令使用参数数组启动，不拼接 shell 命令。
- 主 UI 不启用任意外部网页导航或通用 Tauri IPC。
- 日志和错误信息经过凭据脱敏。
- 社区插件属于第三方代码，安装前明确提示其可获得 DSH 插件体系允许的能力。

## 13. 发布与签名

首发矩阵：

| 平台 | 架构 | 安装包 | 发布要求 |
|---|---|---|---|
| Windows | x64 | Tauri 支持的直接安装包 | 正式发布需 Windows 代码签名证书 |
| macOS | arm64 | `.dmg`/`.app` | 正式发布需 Developer ID 签名与 Apple notarization |

CI 在原生 runner 分别构建两套制品。签名材料只通过 CI Secret 注入，不进入仓库。首版不上架应用商店。

## 14. 测试策略

### 14.1 Rust

- 运行时清单签名、哈希、平台选择和版本比较单元测试。
- 下载中断/续传、staging、原子切换和回滚测试。
- 进程树启动、取消、超时、退出和崩溃恢复测试。
- 路径穿越、恶意归档和日志脱敏安全测试。

### 14.2 React/Desktop 插件

- 引导/恢复状态机组件测试。
- Desktop root slot 组合、三栏折叠、标题栏留白和主题 token 测试。
- 市场列表、兼容性、确认、流式日志和失败状态测试。

### 14.3 集成与端到端

- 使用可控假 Host 验证首启、取消、失败和修复流程。
- 使用固定 DSH 版本执行 Desktop 插件契约测试。
- Windows x64 与 macOS arm64 原生 CI 生成安装包并进行启动冒烟测试。
- 打包后验证首次启动、再次启动、Runtime 回滚和插件安装/更新/卸载。
- 以参考 README 截图为视觉基线做主要窗口尺寸人工/截图对比，但排除平台原生材质差异。

## 15. 首版验收标准

满足以下条件才可称为首版完成：

1. Windows x64 与 macOS arm64 均能从干净系统完成安装和首启自举。
2. 用户无需终端即可进入可用的官方 DSH 工作台。
3. 主工作台信息架构与参考项目一致，且官方会话流程可完整使用。
4. 精选目录签名失败时不会展示或安装未验证的新目录。
5. 至少一个测试插件可在两个平台完成安装、更新、卸载和取消操作。
6. Runtime 更新失败可回退，插件失败可诊断，不损坏可用运行时。
7. loopback 服务不监听外网接口，主 Web UI 无通用系统 IPC。
8. 两个平台安装包通过自动化测试与人工打包冒烟测试。
9. 正式公开发布前完成对应平台代码签名；没有证书时只交付明确标注的测试包。

## 16. 后续阶段

首版稳定后再评估：

- Linux 构建与兼容外观。
- TUI/Headless 启动入口。
- 更细粒度的插件权限声明与审核流水线。
- 应用商店渠道。
- 目录后台、自动化候选扫描和社区提交工作流。
