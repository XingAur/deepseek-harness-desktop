# 本地应用启动器设计文档

- 日期：2026-08-21
- 状态：已确认（设计阶段）
- 主题：本地项目的「双击运行」——把 agent 构建的项目作为可操作、可留存的本地应用启动

## 1. 背景与目标

「本地项目」页目前的双击行为是打开绑定项目目录的 DeepSeek Harness 会话（继续开发）。
用户期望的双击行为是**启动项目本身**：例如构建出的记账应用能作为一个本地应用打开、
操作，并且数据跨重启保留。

本设计在现有 Tauri 壳 + 受管 Runtime + 桌面插件三层结构上新增「本地应用启动器」：

1. **双击 = 启动应用**：项目带有效启动清单时双击即启动；无清单时智能回退为打开会话（现状行为）。
2. **主窗口内呈现**：应用以互斥表面嵌在主窗口中（与工作台切换，类似浏览器标签）。
3. **数据留存**：应用数据写项目目录内（默认 `data/`），随项目备份/删除。
4. **后台保持运行**：切回工作台不杀进程；卡片显示「运行中」；可手动停止；再双击瞬时切回。
5. **收录范围收敛**：本地项目页不再显示 Profile 内全部 Workspace，只显示
   ① 位于 `文档\DeepSeek Harness\Projects` 下的工作区 ② 元数据带 `localApp` 标记的工作区。

### 明确不做（YAGNI）

- exe / 原生应用启动（仅 `web` 与 `static` 两类）
- 多应用分屏、多窗口
- 清单可视化编辑器
- 应用开机自启 / 后台守护重启
- 主会话中 agent 建到约定目录之外的自动识别（靠「收录已有项目」按钮手动补录）

## 2. 方案选型

| 方案 | 说明 | 结论 |
| --- | --- | --- |
| **A. 项目内启动清单（选定）** | agent 构建收尾时在项目根写 `dsh-app.json`；启动器严格校验后受管启动 | ✅ 声明式、可静态校验、随代码演进 |
| B. 约定扫描 | 扫 `package.json` 脚本猜启动方式 | ❌ 区分不了「应用」与「库/脚本」，误启动风险高 |
| C. 元数据登记制 | 构建后把启动方式登记进项目元数据 | ❌ 与目录分离易丢配置；登记内容仍需扫描产物 |

关键取舍（已与用户确认）：主窗口内切换视图（非独立窗口/浏览器）；数据存项目目录内
（非独立数据目录）；进程后台保持运行（非随视图停止）；收录采用目录约定 + 收录按钮。

## 3. 启动清单 `dsh-app.json`

项目根目录，由 agent 在构建收尾时写入（构建提示词强制要求）：

```json
{
  "schemaVersion": 1,
  "type": "web",
  "start": ["pnpm", "run", "start"],
  "portEnv": "PORT",
  "healthPath": "/",
  "dataDir": "data"
}
```

- `type`：`web`（受管 Node 起服务）或 `static`（`"staticDir": "dist"`，由 Rust 内置回环
  静态服务器托管，不 spawn 进程）。
- 端口不写死：桌面端预留回环端口并通过 `portEnv` 指定的环境变量注入，应用必须从该变量读端口。
- `dataDir` 默认 `data`；构建提示词约定应用数据一律写入该目录。
- `start` 为相对受管 Runtime 的命令别名（`node` / `pnpm`），由启动器解析为受管树内绝对路径。

## 4. Rust `apps` 模块（新增 `src-tauri/src/apps/`）

| 组件 | 职责 |
| --- | --- |
| `manifest.rs` | 清单解析与校验：字段类型/枚举全量校验；`start` 首项仅允许 `node`/`pnpm` 别名并解析到受管 Runtime 树内绝对路径（同 `PluginCommandService` 验证 `DSH_DESKTOP_DSH_BIN` 的思路）；参数逐项非空字符串；`dataDir`/`staticDir` 禁止路径逃逸（不得包含 `..`、不得为绝对路径） |
| `runner.rs` | `reserve_loopback_port()`（复用 `runtime/process.rs`）→ spawn：无 shell、固定 argv、cwd 锁项目目录、最小环境（系统必需项 + 指向受管树的 `PATH` + 端口变量注入）、`CREATE_NO_WINDOW`、stdout/stderr 管道到应用日志文件（`<数据根>/logs/apps/<workspaceId>.log`）→ 60s 健康检查（`GET http://127.0.0.1:<port><healthPath>`）→ 通过后注册运行态。`app.launch` 幂等：项目已在册时直接返回既有 `origin` 并再次发 `launched` 事件（切回视图），不重复 spawn |
| `registry.rs` | 运行态注册表 `workspaceId → { pid, port, origin, logPath, startedAt }`；单项目单实例；全局并发上限 5；进程意外退出时摘除并发事件 |
| `static_server.rs` | `static` 类型的内置回环静态文件服务（随机预留端口，路径规范化防穿越） |

生命周期：`stop` 走 `terminate_tree`（复用现有实现）；`orderly_quit` 与 Runtime 重启
（含 Profile 切换换代会话）时回收全部应用进程；健康检查超时即杀进程树并返回带日志路径的错误。

## 5. 桥接与命令

- 桥接动作新增：`app.launch` / `app.stop` / `app.status`（`desktop-bridge.ts` 类型 +
  `bridge-contract.ts` 载荷校验与命令映射 + Rust 命令，全部带 `generationId` 校验，同现有形态）。
- 新增 Tauri 事件通道 `local-app-event`（`launched` / `stopped` / `exited`，载荷含
  `workspaceId` 与 `origin`）：`App.tsx` 监听后切换主窗口表面；插件侧在打开本地项目页时
  调一次 `app.status` 刷新角标（插件在 iframe 内收不到 Tauri 事件，主动查询 + 自身操作后本地更新）。
- `project.metadata.patch` 的 patch 结构扩展 `localApp?: boolean`，「收录已有项目」写此标记。

## 6. 主窗口应用视图（`src/App.tsx`）

- 渲染树增加互斥表面：工作台 iframe ↔ 应用视图 iframe。**切换用隐藏而非卸载**，工作台会话状态不丢。
- 应用视图 `src` 仅为 Rust 返回的精确 `http://127.0.0.1:<port>` 源（沿用 `window.rs` 的
  精确源校验模式），同样委托 `allow="clipboard-write"`。
- 应用视图顶部一条本地受信条：`正在运行：{项目名} · [返回工作台] [停止应用]`。
  返回不杀进程（后台保持运行）；停止才终止并回到工作台。

## 7. 插件侧 UI（`LocalProjectsPage` / `ProjectCard`）

- **收录过滤**：卡片列表 = 路径在 `文档\DeepSeek Harness\Projects` 下的工作区 ∪ 元数据带
  `localApp` 标记的工作区；页尾新增「收录已有项目」按钮（列出当前 Profile 其余工作区供选择，
  选中即打 `localApp` 标记）。
- **双击分支**：有有效清单 → `app.launch`；无 → 现状回退（`connectWorkspace` + 打开会话）。
- **角标**：`可运行`（清单有效）/ `运行中`（`app.status` 在册）。
- **右键菜单**：新增「打开会话继续开发」与「停止应用」（运行中时显示）。
- 单击选中 → 底部修改需求对话框的现有交互不变。

## 8. 数据与构建提示词

- `project-controller.ts` 的 `buildPrompt` 追加：收尾在项目根写 `dsh-app.json`（给出字段
  与示例）；服务端口从 `PORT` 环境变量读；业务数据一律写入 `data/`（清单 `dataDir` 可覆盖）。
- 删除确认对话框（`ProjectDeleteDialog`）文案补充：项目目录内的应用数据将随目录一并移入回收站。

## 9. 错误处理

| 场景 | 行为 |
| --- | --- |
| 清单缺失/不合法 | 双击回退打开会话；卡片无「可运行」角标 |
| spawn 失败 / 健康超时 | 桥返回明确错误（含日志路径），卡片可重试；进程树确保清理 |
| 运行中崩溃 | `exited` 事件；角标消失；若正处应用视图则自动返回工作台并提示 |
| 停止失败 | 重试一次 `terminate_tree`，仍失败写入诊断 |
| Runtime 重启 / 应用退出 | 全部应用进程随 `orderly_quit` 回收 |

## 10. 测试

- **Rust 单测**：清单校验（非法命令别名、参数含 shell 元字符、路径逃逸、类型枚举）、
  注册表生命周期与并发上限、静态服务器路径规范化、健康超时回滚、`orderly_quit` 全回收。
- **插件 vitest**：收录过滤、双击分支、角标渲染、右键新菜单项、`app.status` 刷新、
  「收录已有项目」流程。
- **`src/` vitest**：表面切换与 `local-app-event` 处理、精确源校验、隐藏不卸载。
- **e2e**：构建最小 `web` 清单项目 → 双击启动 → 断言应用视图出现与受信条 → 停止 → 断言进程回收。
