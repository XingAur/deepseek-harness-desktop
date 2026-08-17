# DeepSeek Harness Windows 客户端设计文档

- 日期：2026-08-17
- 状态：已确认（设计阶段）
- 项目代号：`dsh-desktop`

## 1. 背景与目标

DeepSeek Harness（`dsh`）是 DeepSeek AI 开发的开源 agent harness，官方使用方式为
`npx @deepseek-ai/dsh web`，启动后暴露 Web UI 于 `http://127.0.0.1:3080`。
该方式对非技术用户存在门槛（需安装 Node.js、使用命令行）。

本项目将其封装为 Windows 桌面客户端，实现：

1. **检测安装状态**：自动判断 dsh 运行时是否就绪
2. **一键安装**：客户端自带全部运行所需内容，安装客户端即完成一切
3. **启动/附加**：无服务时自动拉起；已有服务运行时（如用户此前用 npx 启动过）直接附加，不重复启动、不接管其生命周期
4. **插件市场可用**：Web UI 内置的「社区插件」在裸机上开箱即用（插件安装依赖 pnpm，由客户端捆绑提供）
5. **项目卡片与本地项目**：客户端主页保留项目卡片入口；项目由用户在 AI 会话中**指示 agent 生成**，落地在本地数据目录（默认 D 盘），点击卡片进入对应项目

### 目标用户

分发给**不懂技术的用户**：双击安装包 → 双击图标 → 看到 Web UI。全程无需命令行、
无需安装 Node.js、首次运行无网络依赖。

### 明确不做（YAGNI）

- 开机自启动
- 完整设置界面（仅托盘菜单 + 原生对话框）
- 多语言（中文界面）
- macOS / Linux 支持
- 客户端自建插件市场 UI（复用 Web UI 内置「社区插件」）
- 捆绑 git（不支持 `github:` 直装插件，详见 §6.3）
- 业务项目本体（如外贸工作台）——属使用期 agent 生成产物，非本客户端交付物（见 §7.5）

## 2. 方案选型

| 方案 | 说明 | 结论 |
| --- | --- | --- |
| **1. 全离线内置（选定）** | 构建时将 dsh 及全部依赖打进 Electron 安装包 | ✅ 零失败点，无网络/镜像依赖 |
| 2. 薄壳联网安装 | 首次运行从 npmmirror 下载 dsh | ❌ 首次运行强依赖网络，目标用户易卡死 |
| 3. 依赖系统 Node | 要求用户自装 Node.js | ❌ 与目标用户矛盾 |

技术栈：**Electron + TypeScript + electron-builder（NSIS）+ vitest**。

选 Electron 的决定性理由：dsh 是 Node 包，Electron 主进程自带完整 Node 运行时，
通过 `ELECTRON_RUN_AS_NODE` 直接运行 dsh，用户机器无需任何预装。

## 3. 架构

### 3.1 组件（主进程）

| 组件 | 职责 | 关键接口 |
| --- | --- | --- |
| `index.ts` | 入口：单实例锁、窗口/托盘装配 | — |
| `service-manager.ts` | 核心：「AI 会话」dsh 进程生命周期 + start 型项目子进程管理 | `ensureSession()` / `ensureProject(name)` / `shutdownAll()` |
| `port-probe.ts` | 探测指定端口：TCP 连通 + HTTP 特征校验 | `probe(port): Promise<'dsh' \| 'foreign' \| 'none'>`；判定规则：TCP 拒绝/超时 → `none`；TCP 通且 `GET /` 返回 200 + `text/html` → `dsh`；TCP 通但不满足前者 → `foreign` |
| `projects.ts` | 扫描 `<数据根>/projects/*/project.json`，维护项目列表 | `list(): Project[]` |
| `static-server.ts` | 内置静态文件服务（随机高位端口），加载静态型项目 | `serve(dir): Promise<number>` |
| `dsh-resolver.ts` | 版本目录解析（用户区 > 内置） | `resolve(): { binPath, version, source }` |
| `updater.ts` | 检查/下载/解压 dsh 新版本 | `check()` / `upgrade()` |
| `pnpm-runtime.ts` | 构建 dsh 子进程环境：捆绑 pnpm 的 shim、`DSH_HOME`、npmmirror 源 | `buildChildEnv(): NodeJS.ProcessEnv` |
| `tray.ts` | 托盘：打开主页 / 检查更新 / 退出 | — |

渲染层：纯静态 HTML/JS 的主页（项目卡片）、闪屏（状态文案）与错误页，无前端框架。
preload 仅暴露最小的状态查询/重试 IPC。

### 3.2 状态机

```text
打开客户端 → 主页（projects.list()）

点击「AI 会话」→ ensureSession()：
  CHECKING（探测 3080）
    ├─ 'dsh'    → ATTACHED：直接加载
    ├─ 'none'   → STARTING：spawn dsh web → 就绪 → READY
    └─ 'foreign'→ ERROR_PORT_CONFLICT

点击项目卡片 → ensureProject(name)：
  ├─ 静态型（无 start）→ static-server.serve(项目目录) → 加载 <随机端口>/<entry>
  └─ start 型          → spawn <start 命令>（cwd=项目目录，env=buildChildEnv()）
                          → 等 start 命令声明的端口就绪（project.json.port）→ READY
  start 失败/进程退出 → ERROR_CRASHED（该卡片错误态）
```

### 3.3 进程所有权规则（关键不变量）

- **只有客户端自己 spawn 的进程**（dsh 或 start 型项目），在客户端退出时被终止（`tree-kill` 整进程组）
- **附加模式**（探测到外部已有 dsh，如用户用 npx 启动的实例）：退出客户端**不杀**该进程
- 子进程意外退出且属启动模式 → 状态机转 ERROR_CRASHED

### 3.4 子进程运行方式

dsh（AI 会话）：

```ts
spawn(process.execPath, [binPath, 'web'], {
  env: buildChildEnv(),                 // 见 pnpm-runtime.ts
  stdio: ['ignore', 'pipe', 'pipe'],   // stdout/stderr → 日志文件
  windowsHide: true,
})
```

start 型项目：以 `buildChildEnv()`（PATH 含捆绑 pnpm/node 能力）执行
`project.json` 的 `start` 命令，`cwd` 为项目目录，stdout/stderr 记入
`<数据根>\logs\projects\<name>.log`。

### 3.5 单实例

`app.requestSingleInstanceLock()`：重复启动仅唤起既有窗口，`second-instance` 事件里
`win.show()` + 聚焦。

## 4. dsh 版本解析

优先级（`dsh-resolver.ts`）：

1. `<数据根>\runtime\dsh\<version>\`（升级安装的用户区版本，取 semver 最大）
2. 应用内置 `<安装目录>\resources\dsh\`（构建时由 `scripts/fetch-dsh.mjs` 下载并展开）

用户区版本目录损坏/被删 → 自动回退内置版（天然回滚机制，内置版只读）。
当前生效版本与来源持久化于 `<数据根>\settings.json`。数据根定义见 §7.1。

## 5. 升级流程

入口：托盘菜单「检查更新」。

1. `GET https://registry.npmmirror.com/@deepseek-ai/dsh/latest`（失败回退 `registry.npmjs.org`）
2. 与当前运行版本比对；有新版 → 原生确认对话框（当前 x → 新版 y）
3. 下载 `dist.tarball` 至临时目录，解压到 `<数据根>\runtime\dsh\<新版本>\`
4. 重启 dsh：**启动模式**下杀旧子进程 → 以新版本拉起 → 窗口自动刷新；
   **附加模式**下不杀外部进程，提示用户「关闭当前外部 dsh 服务后重试」，客户端随后以启动模式用新版拉起
5. 任意一步失败：对话框提示错误，**当前版本不受影响**

## 6. 插件市场支持

**形态**：复用 dsh Web UI 内置的「社区插件」页面，客户端不做自己的市场 UI；
职责是让该功能在**无 Node、无 pnpm 的裸机**上可用。

### 6.1 机制背景（来自官方文档调研）

- dsh 插件是声明了 `dsh.bundle` 的 **npm 包**；用户侧安装命令
  `dsh plugin --profile <name> add <pkg>` **内部转发给 pnpm**，在
  `$DSH_HOME/profiles/<name>` 下管理依赖
- 插件发现渠道：GitHub topic [`dsh-plugin`](https://github.com/topics/dsh-plugin)（README 官方约定）
- `github:owner/repo` 直装需要 git 且触发 pnpm ≥10 的 `allowBuilds` 审批——开发者用法，不面向本客户端的目标用户

### 6.2 客户端提供的三件事（`pnpm-runtime.ts`）

1. **捆绑 pnpm**：构建时随 dsh 一起下载 pnpm（npm 包形态）到 `resources/runtime/pnpm/`；
   运行时在 `<数据根>\bin\` 生成 `pnpm.cmd`，内容为
   `"<process.execPath>" <捆绑 pnpm 的 js 入口> %*` 并设 `ELECTRON_RUN_AS_NODE=1`，
   该目录被**注入到 dsh 子进程 PATH 最前面**——dsh 转发 pnpm 时即命中捆绑版
2. **固定数据目录**：`DSH_HOME=<数据根>\dsh-home`，
   profiles 与已装插件跨应用升级保留
3. **国内源**：子进程注入 `npm_config_registry=https://registry.npmmirror.com`
   （用户可覆盖：若自身环境已有该变量则不覆盖）

### 6.3 明确限制

- 仅支持 npm 源（npmmirror）可安装的插件；`github:` 直装会因缺少 git 失败，
  错误信息由 dsh/pnpm 原样呈现，客户端不拦截美化
- Web UI 市场列表若请求 GitHub API，国内可能加载慢或失败，客户端不做代理劫持

## 7. 项目卡片与本地项目

**定位**：客户端只保留**项目卡片入口**；项目本体由用户在 AI 会话中指示 agent 生成，
是独立的本地应用（非 dsh 插件）。客户端对项目目录**只读扫描 + 按需启动**。

### 7.1 数据根（统一目录）

首次运行创建数据根，默认 `D:\DeepSeekHarness\`，无 D 盘时回退 `C:\DeepSeekHarness\`
（实际路径持久化于 `settings.json`）：

```text
<数据根>\
├─ projects\<项目名>\       # agent 生成的项目与项目数据
├─ dsh-home\                # DSH_HOME：profiles、插件
├─ runtime\dsh\<版本>\      # 升级安装的 dsh 用户区版本
├─ bin\                     # pnpm.cmd 等 shim
├─ logs\                    # 主进程 / dsh / 项目日志
└─ settings.json
```

### 7.2 项目定义与生成方式

项目 = `<数据根>\projects\<name>\` 目录 + `project.json` 清单：

```json
{
  "name": "外贸工作台", "icon": "briefcase", "desc": "订单/客户/工厂管理",
  "entry": "index.html",          // 静态入口；start 型项目为页面路径
  "start": "node server.js",      // 可选；存在即为 start 型项目
  "port": 8801                    // start 型必填：声明其监听端口
}
```

生成链路：用户把 `<数据根>\projects\` 添加为 AI 会话的工作区 → 对 agent 下达
「生成一个 XX 工作台」→ agent 写入项目文件与 `project.json` → 客户端主页刷新即出现卡片。
**客户端不生成、不校验项目内容**，只按清单启动。

### 7.3 运行模型

| 类型 | 判定 | 启动方式 |
| --- | --- | --- |
| 静态型 | 无 `start` | `static-server.ts` 以随机高位端口 serve 项目目录，加载 `entry` |
| start 型 | 有 `start` | 以 `buildChildEnv()` 执行 `start`（cwd=项目目录），轮询 `port` 就绪后加载 `http://127.0.0.1:<port>/<entry>` |

- 重复点击同一项目 = 幂等（已运行直接加载）
- start 型进程意外退出 → 仅该卡片转错误态，不影响其他
- 「AI 会话」卡片仍走 §3.2 的探测/附加/启动逻辑（3080）

### 7.4 主页

打开客户端 → 原生主页 `home.html`：顶部「**AI 会话**」固定卡 + 项目卡片列表
（名称/图标/描述，`project.json` 无效或缺失的目录跳过）。窗口工具栏常驻
「⟵ 项目」返回按钮；返回时各运行中项目与 dsh 进程**保持运行**。

### 7.5 范围分解

本 spec 交付：数据根、项目扫描、静态/start 双运行模型、主页与返回导航。
**业务项目本体**（如外贸工作台的订单/客户/工厂功能）属使用期 agent 生成产物，
不在本 spec 范围；验收使用两个 fixture 项目（静态型 + start 型）验证全链路。

## 8. 错误处理

| 场景 | 行为 |
| --- | --- |
| 3080 被非 dsh 程序占用 | 「AI 会话」卡片错误态「端口被其他程序占用」+ 重试 |
| start 型项目启动失败/声明端口未就绪（30s）/进程退出 | 该项目卡片错误态 + 日志路径，不影响其他 |
| `project.json` 无效或缺失 | 扫描时跳过该目录，不生成卡片 |
| Web UI 加载超时（30s） | 错误页 + 重试 |
| 升级下载/解压失败 | 对话框提示，保持当前版本 |
| 用户区版本损坏 | 自动回退内置版，托盘气泡提示 |

日志：`<数据根>\logs\`，主进程、dsh、项目分文件、按天滚动、保留 7 天。

## 9. 测试策略

- **单元**（vitest）：`dsh-resolver` 优先级与回退、状态机转换表、`port-probe` 特征判定（mock HTTP）、`pnpm-runtime` 环境注入与 shim 生成、`projects` 扫描（有效/无效 `project.json`）、`static-server` 服务与端口分配
- **集成**：真实拉起 dsh（开发机具备 Node），验证 启动→就绪→附加→退出清理 全链路；
  另验证插件安装：`dsh plugin --profile t add <本地 fixture tarball>` 在捆绑 pnpm 下成功；
  fixture 静态项目与 fixture start 项目各走完 主页卡片 → 启动 → 进入 → 返回 → 退出清理
- **验收**：无 Node.js 的干净 Windows 环境，安装 → 双击 → 主页 → 进「AI 会话」与两个 fixture 项目 →「社区插件」页能列出并安装一个 npm 源插件（分发的硬指标）

## 10. 工程结构

```text
dsh-desktop/
├─ package.json              # electron、electron-builder、vitest、typescript
├─ electron-builder.yml      # nsis；resources/dsh 配 asarUnpack
├─ scripts/fetch-dsh.mjs     # 构建时：npm pack @deepseek-ai/dsh + pnpm → resources/（dsh、runtime/pnpm）
├─ src/main/
│  ├─ index.ts
│  ├─ service-manager.ts
│  ├─ port-probe.ts
│  ├─ projects.ts
│  ├─ static-server.ts
│  ├─ dsh-resolver.ts
│  ├─ updater.ts
│  ├─ pnpm-runtime.ts
│  └─ tray.ts
├─ src/preload.ts
├─ src/renderer/             # home.html / splash.html / error.html（纯静态）
└─ fixtures/projects/        # hello-static / hello-start 两个验收项目（见 §7.5）
```

交付物：`dsh-desktop Setup <ver>.exe`（NSIS，x64）。

## 11. 已知风险

- dsh 处于开发者预览，破坏性变更可能使内置旧版无法与新版 Web UI 数据兼容——升级由
  用户手动触发，且可回退，风险可控
- 3080 端口冲突概率低但存在；特征校验可区分 dsh 与他者，冲突时给出明确指引而非静默失败
- `dsh plugin` 对 pnpm 的调用方式（PATH 查找 vs 硬编码）以当前版本文档为准；若实现
  与假设不符，需要随 dsh 升级做适配（集成测试覆盖该链路，可及时发现）
- Web UI「社区插件」列表数据源在 GitHub，国内访问不稳定时列表可能为空，客户端不代理
- **安全边界**：start 型项目在本机执行任意命令——这是「让 agent 生成项目」模型的固有
  授信，与 dsh 工作区审批策略一致；卡片首次启动 start 型项目时弹一次性原生确认框
- agent 生成项目的质量不可控（清单字段缺失、端口未监听等）：客户端按 §8 容错，
  卡片错误态展示日志路径，不阻塞其他功能
- D 盘不存在或不可写时回退 C 盘；数据根不可写（极端情况）→ 错误页指引
