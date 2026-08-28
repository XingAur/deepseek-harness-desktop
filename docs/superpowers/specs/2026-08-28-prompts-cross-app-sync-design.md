# Prompts 跨应用同步设计文档

- 日期：2026-08-28
- 状态：已确认（设计阶段）
- 主题：预设库式提示词管理——一份 Markdown 提示词跨 Claude / Codex / DSH 同步，带回填保护
- 参考：cc-switch（farion1231/cc-switch）Prompts 能力的移植与改造

## 1. 背景与目标

用户在多个 AI CLI（Claude Code、Codex）与本应用内置的 DeepSeek Harness 之间重复维护
全局提示词文件（`CLAUDE.md` / `AGENTS.md`）。本设计提供统一预设库：

1. **共享预设池**：所有预设存一处；一个预设可同时激活到多个目标。
2. **每应用单激活**：每个目标同一时刻最多一个激活预设；激活 = 写入对应 live 文件。
3. **回填保护**：切换/编辑前先把 live 文件的外部修改回填进预设库，外部编辑不丢。
4. **统一入口**：侧边栏新增「扩展中心」overlay 面板，「提示词」为第一个 tab；
   后续 MCP / Skills / 用量等 cc-switch 系功能以新 tab 陆续填充。
5. **编辑器**：textarea + marked 实时预览（V1 不引入重型编辑器）。

### 明确不做（YAGNI，本期）

- Deep Link 导入（已明确推迟；用面板内手动粘贴 JSON 导入替代，见 §6）
- 连通性测试、预设模板库（属 MCP 子项目范围）
- 项目级提示词（仅全局级文件）
- 每应用独立预设库（已选共享池）
- CodeMirror 等重型编辑器、语法高亮
- WebDAV / 云同步、SQL 导出导入
- 定时文件监听（同步均为操作触发，不后台 watch）

## 2. 方案选型

| 方案 | 说明 | 结论 |
| --- | --- | --- |
| **A. Rust `prompts/` 模块 + SQLite SSOT（选定）** | 预设与激活映射存 SQLite；目标写入器负责 live 文件原子写 | ✅ 与 `agent_store` 先例一致；回填/切换多步写有事务保障；与 cc-switch 存储模型同构 |
| B. 文件系统 SSOT（预设目录 + 索引 JSON） | 预设存散装 `.md`，索引存映射 | ❌ 索引与文件可漂移；跨文件无事务，回填中途失败会留不一致 |
| C. 插件侧存储 + 通用文件读写命令 | 插件存数据，Rust 暴露通用文件 I/O | ❌ 通用文件写命令削弱安全边界；存储生命周期挂在 Profile 下层级错误 |

与 cc-switch 的差异：cc-switch 预设按应用隔离、无跨应用复用；本设计为共享池 +
多目标激活，数据模型多一层激活映射，并因此引入多目标回填冲突语义（§5）。

## 3. 架构与数据模型

### 3.1 模块布局（`src-tauri/src/prompts/`，顶层模块，与 `agents/`、`profile/` 平级）

```text
prompts/
  mod.rs        模块根 + PromptTarget 枚举（Claude | Codex | Dsh）
  model.rs      PromptPreset、TargetStatus、冲突与导入相关类型
  store.rs      SQLite 存取（仿 agent_store：open via AppPaths、读写连接、Mutex 串行）
  migrations.rs 表结构迁移（仿 agent_store/migrations.rs 的 schema_version 模式）
  service.rs    业务操作：CRUD、激活/停用、回填、导入（核心逻辑，事务内完成）
  targets.rs    三个目标的路径解析 + 原子写 + 安装检测
  backup.rs     写前备份环（每目标保留 10 份）
```

存储文件：`<AppPaths 数据目录>/prompts.db`（独立于 `agent_store` 的库，便于回滚）。
状态对象 `PromptsService` 在 `lib.rs` setup 中 `app.manage`，命令经 `commands.rs` 委派。

### 3.2 表结构

```sql
CREATE TABLE prompts (
  id         TEXT PRIMARY KEY,          -- uuid
  title      TEXT NOT NULL,
  content    TEXT NOT NULL,             -- UTF-8，≤ 24 KiB
  created_at INTEGER NOT NULL,          -- unix ms
  updated_at INTEGER NOT NULL
);

CREATE TABLE prompt_activations (
  target       TEXT PRIMARY KEY CHECK (target IN ('claude','codex','dsh')),
  preset_id    TEXT REFERENCES prompts(id) ON DELETE SET NULL, -- NULL = 未激活
  activated_at INTEGER NOT NULL
);
```

### 3.3 目标与 live 文件路径

| 目标 | live 文件 | 安装判定（不满足则只读展示、跳过一切写入） |
| --- | --- | --- |
| Claude | `~/.claude/CLAUDE.md` | `~/.claude/` 目录已存在 |
| Codex | `~/.codex/AGENTS.md` | `~/.codex/` 目录已存在 |
| DSH | `<活动 Profile 的 data_root>/<待定>` | 存在活动 Profile |

**DSH 目标文件名待定**：实现计划第一步做 spike，确认受管 DeepSeek Harness 读取全局
提示词/记忆文件的约定路径（候选：`AGENTS.md` / `CLAUDE.md`）。常量收敛在
`targets.rs` 一处；若确认 harness 无全局文件机制，DSH 目标降级为不显示，其余设计不变。

### 3.4 大小上限

预设内容 ≤ **24 KiB**（bridge 帧上限 32 KiB，留协议余量）。超限保存返回明确错误；
`prompts.status` 对超限的 live 文件在状态中标记 `oversized`，不参与回填。

## 4. UI 设计（插件侧）

### 4.1 入口：扩展中心

- 新增第二个 `sidebar.footer.action` 注册（id `dsh-desktop-extension-center`，order 20）：
  星形图标 +「扩展中心」，与「本地项目」按钮并列；点击开合 overlay 面板，
  完全复用 `LocalProjectsPage` 的 overlay 模式（`AdvancedFrame` 注入新的面板 state）。
- 官方导航栏内部无插件可注入槽位，`sidebar.footer.action` 是唯一导航级入口。
- 面板内 tab 导航：「提示词」（本期）；「MCP」「Skills」「用量」占位（显示"即将推出"）。

### 4.2 提示词 tab 布局

```text
┌────────────────────────────────────────────────────────┐
│ 目标状态条: [Claude ● 已激活·预设A] [Codex ○ 未安装] [DSH ● ⚠外部修改] │
├───────────┬────────────────────────┬───────────────────┤
│ 预设列表   │ 编辑器（textarea）      │ 实时预览            │
│ · 标题     │                        │ （marked + DOMPurify│
│ · 更新时间 │                        │   净化后渲染）      │
│ · 激活徽标 │ [保存] [激活到…] [停用]  │                   │
│           │ [删除] [从文件导入]       │                   │
└───────────┴────────────────────────┴───────────────────┘
```

- 目标状态条：每目标显示 安装/未安装、当前激活预设；live 文件哈希与 DB 不一致时
  亮「⚠外部修改」徽标，点击打开该激活预设进编辑器（保存时经 §5 回填流程吸收外部
  修改，无需独立回填动作）；未安装目标按钮禁用。
- 新增依赖（插件包）：`marked` + `dompurify`（预览渲染，防 iframe 内注入）。
- 手动粘贴 JSON 导入：对话框内粘贴 cc-switch 格式或 `{title, content}` JSON，
  解析入库（不激活），作为 Deep Link 缺位的导入通道。

## 5. 同步语义（`service.rs`）

- **激活**（目标 X ← 预设 P，命令 `prompts.activate`）：
  ① 回填检查（见下）② 备份当前 live 文件 ③ temp 写 + 同卷 rename 原子替换为 P 内容
  ④ 写成功后才在 `prompt_activations` 落记录；任一步失败向上报错、不留半状态。
  同一目标原激活项自动被替换（单激活不变量）。
- **回填保护**（激活/编辑前触发，读 live 文件）：
  - 非空 + 该目标有激活项 + live 内容 ≠ DB 内容 → 外部修改生效：live 内容回填覆盖
    该预设的 `content` / `updated_at`；
  - 非空 + 无激活项 → 自动创建 `backup-{timestamp}` 备份预设（不入激活），再正常写入；
  - live 为空 / 读取失败 → 跳过回填，不阻塞主流程（失败仅记录）；
  - **多目标冲突（共享池特有）**：预设同时激活于 ≥2 个目标且各 live 内容互不一致时，
    不静默选边——`prompts.activate` / `prompts.save` 返回
    `{ kind: 'backfill-conflict', presetId, candidates: [{ target, content, updatedAt }] }`，
    UI 弹冲突对话框由用户选择以哪个为准（或放弃回填）；用户选择后以
    `prompts.save` 写入选定内容并重试原操作。单目标激活时静默回填。
- **编辑已激活预设**（`prompts.save`）：先回填 → 更新 DB → 自动重投影到所有激活
  该预设的目标（逐目标备份 + 原子写）。
- **停用**（`prompts.deactivate`）：清空对应 live 文件（写前备份），删除激活记录。
  对齐 cc-switch「禁用即清空」。
- **删除预设**（`prompts.delete`）：仅当无任何目标激活时允许；有激活项返回明确错误，
  要求先停用。
- **首启导入**：面板首次打开时拉 `prompts.status`；预设池为空且任一目标 live 文件
  非空 → 弹导入对话框（逐目标列出、可勾选），`prompts.import` 导入为预设并自动
  激活到对应目标。池非空不自动触发；「从文件导入」按钮可随时手动触发同一对话框。

## 6. Bridge 契约（v2 动作，双份契约同步）

新增动作（命名沿用既有小写点分风格，映射到 `commands.rs` 新命令，全部登记进
`renderer_commands!`）：

| 动作 | Tauri 命令 | 载荷 → 返回 |
| --- | --- | --- |
| `prompts.list` | `prompts_list` | → `PresetSummary[]`（含 `activatedTargets`） |
| `prompts.get` | `prompts_get` | `{ presetId }` → `PromptPreset` |
| `prompts.save` | `prompts_save` | `{ presetId?, title, content }` → `{ preset }` 或冲突载荷 |
| `prompts.delete` | `prompts_delete` | `{ presetId }` → `ok` |
| `prompts.activate` | `prompts_activate` | `{ presetId, target }` → `{ status }` 或冲突载荷 |
| `prompts.deactivate` | `prompts_deactivate` | `{ target }` → `{ status }` |
| `prompts.status` | `prompts_status` | → `TargetStatus[]`：`{ target, installed, liveFileExists, activePresetId, liveContentSha256, matchesActivePreset, oversized }` |
| `prompts.import` | `prompts_import` | `{ targets: Array<'claude' \| 'codex' \| 'dsh'> }` → `{ imported: PresetSummary[] }` |

契约改动落三处：`src/bridge-contract.ts`、
`packages/dsh-plugin-desktop/src/client/bridge-contract.ts`（动作联合 +
`bridgeCommandByActionV2` + `versionedPayloadKeys`），及对应测试。

## 7. 安全边界与错误处理

- 渲染器**不可传任何路径**：8 个命令全部无路径参数；目标路径由 Rust 从 home 目录 /
  活动 Profile（复用 `profile/` 仓储）推导。capability 文件不动（仍 `core:default`）。
- 破坏性写三重保障：备份 → temp 写 + 同卷 rename 原子替换 → 成功后落激活记录。
- 备份位置：`<AppPaths 数据目录>/prompt-backups/<target>/<timestamp>-<sha256 前 8>.md`，
  每目标保留最近 10 份，超出轮转删除。
- 未安装目标跳过写入，**绝不替用户创建** `~/.claude/`、`~/.codex/` 等目录。
- 不涉及子进程、环境变量、网络；store 操作经 Mutex 串行化（与现有模块一致）。
- JSON 粘贴导入仅接受 `{title, content}` 或 cc-switch prompts 数组形状，其余拒绝；
  导入内容同样受 24 KiB 上限约束。

## 8. 测试策略

- **Rust**（内联 `#[cfg(test)]`，临时目录充当假 home，仿 `agents/discovery.rs` 风格）：
  - store：CRUD、迁移升级、激活映射约束（单激活、外键置空）；
  - service 回填矩阵：有激活 + 外部改 → 回填；无激活 + 非空 → 建备份预设；
    空/读取失败 → 跳过；多目标分歧 → 冲突载荷检出；
  - 激活/停用全链路：备份生成与 10 份轮转、未安装跳过、写失败不落激活、
    原子替换后旧内容可从备份恢复；
  - targets：路径推导、安装判定、超限标记。
- **插件**（`npm run plugin:test`）：扩展中心按钮开合、tab 切换与占位、
  提示词 tab 列表/激活/停用/保存重投影（mock bridge）、预览渲染（含脚本净化）、
  冲突对话框分支、首启导入对话框、粘贴 JSON 导入校验。
- **bridge 契约**：`bridge-contract.test.ts` / `workbench-bridge.test.ts` 补 8 个动作的
  payload 键校验、32 KiB 帧上限、secret 形状洗涤断言。
- **验收线**：`npm run check` 全绿；v1 不新增 e2e 套件（unit + 契约测试已覆盖，
  e2e 留作后续迭代）。

## 9. 与后续子项目的衔接

- 「扩展中心」面板与 tab 骨架本期落地；MCP / Skills / 用量以新 tab 逐个填充，
  各自独立走 设计 → 计划 → 实现 循环。
- 若 MCP 子项目重启，其 SQLite SSOT 可复用本模块的 store/migrations 模式与
  `targets.rs` 的目标探测结论。
