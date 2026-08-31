# HIS AI Harness

HIS AI Harness 是一个面向 HIS 需求研发的专家团 Workflow 原型。

当前代码版本由根目录 `VERSION` 文件唯一维护；Core、发布包和 CI 均从该文件读取，历史版本制品可通过显式版本参数重建。

### 当前验证基线（2026-08-30）

使用统一入口执行验证：

```bash
./scripts/verify.sh unit
./scripts/verify.sh offline
```

当前 `compile`、`replay`、`secret` 短门禁已通过；完整离线门禁的 `unit` 阶段仍有超时/失败，未达到发布准入。这里的离线门禁只证明本地技术链路，不代表真实模型、运行时、HIS 业务验收或发布许可，必须继续保持：`business_valid=false`、`runtime_verified=false`、`promotion_enabled=false`。

### Enterprise MCP control plane status（Phase 0 + Phase 1A/1B runtime + Phase 1C authority + Phase 1D primary）

当前已交付外部 I/O 资产清单、稳定指纹和“禁止新增未分类直连”的架构门禁，可通过
`./scripts/verify.sh architecture` 验证。Phase 1A 已实现严格 MCP Capability Registry、
输入/结果合同、Gateway、单次调用限制、结果大小与敏感信息拦截，以及接入既有
`CapabilityService` 的兼容适配层。Phase 1B runtime 已增加哈希钉死、无 shell、单进程单调用的
stdio transport，以及独立于主库的 `mcp.sqlite`：Evidence 按请求和内容哈希幂等保存，Audit
使用只追加触发器和 SHA-256 链，重启后重新验哈希再恢复。离线 fixture 已通过
Runtime → Gateway → stdio → 合同校验 → 持久化的真实子进程链路。

Phase 1D 已启用云效 `workitem.read`、GitLab `gitlab.read`、PostgreSQL `database.inspect` 三个
L1 只读 MCP 描述符，并将 Manager 默认执行路由切换为持久 MCP Runtime。插件版本、MCP
入口点、依赖和 SHA-256 已冻结；MCP 失败会 fail closed，绝不静默回退到 Provider、浏览器、
直连客户端或另一份 token。旧 Provider 直连路径继续标记为 `compatibility_quarantine` 债务，
只有调用方显式选择 `provider_rollback` 才可使用，不能由环境状态或 MCP 错误自动触发。
离线合同、Server、路由和安全门禁已通过；真实 GitLab 连通性仍缺少已配置个人只读 token。
已配置 PostgreSQL 目标的 schema-only MCP smoke 当前返回可重试的
`DATABASE_NETWORK_UNREACHABLE`，未生成证据，因此真实 PostgreSQL 目录读取尚未验收通过，
不得描述为生产实连；该结果不授权 Provider、直连驱动或其他凭证回退。

这里的职责边界固定为：**Skill = 说明书、约束和工作方法；MCP = 外部系统连接、执行和证据回执**。
本地源码读取、Git、本地构建与测试仍属于受控 Worker sandbox，不强行包装为 MCP。
其余 Provider-to-MCP 逐能力迁移、有限重试、Token Governor 和 Supervisor 恢复编排仍属于
后续阶段，当前不得提前宣称具备。`ChangeContextPack` 已按下一节的明确边界交付。当前
Gateway 只向上返回紧凑 `CapabilityResult + evidence_ref`，完整 envelope 按需从 `mcp.sqlite`
恢复，为后续减少重复上下文和 token 消耗提供基础；尚不能宣称已经实现跨任务经验自动压缩。

Phase 1C 已进一步固定“技术权限”与“Harness 治理”的边界：云效/GitLab/GitHub 的只读调用
是否成功由对应个人 token 的权限决定，数据库只读调用由只读 endpoint/credential 的权限决定；
Harness 不再为已注册只读动作制造第二个人工确认。Harness 仍必须绑定 Profile、目标、请求人、
精确参数和一次性计划，并在执行 MCP 前原子消费计划和写审计；endpoint 与凭证只由对应 MCP
Server 在连接边界内解析，Harness 不接收或转发 secret。

数据库修改和删除默认绝对禁止。只有用户明确要求并授权精确对象、操作、条件和影响范围后，
才允许进入独立数据库变更设计；删除、`DROP`、`TRUNCATE` 还必须单独绑定破坏性范围。当前
`ACTION_DESCRIPTORS` 不登记数据库 DML/DDL/删除动作，`database.change` 仍不可外部执行，
因此本阶段没有任何数据库写入口。

### Enterprise ChangeContextPack（schema v73）

每条代码修改路径现在都必须先形成不可变、可追溯的 `ChangeContextPack`，通过 gate 后才能创建
`SinglePassChangeContract` 或进入 worker。四层固定为：`ProjectGraph`（项目、仓库、服务与依赖）、
`ChangeScope`（目标、范围内外、允许路径与验收）、`CodeGraph`（入口、调用链、接口、异常路径与测试）、
`DataGraph`（表、字段、主外键、索引、实体映射和读写路径）。前三层始终必需；只有确定性的适用性规则
证明不涉及数据契约或持久化时，`DataGraph` 才能标记为 `not_applicable`，不确定时保守要求当前证据。

云效、GitLab、PostgreSQL 外部证据只允许由 MCP 连接器读取，Skill 只说明如何选择能力和解释结果。
PostgreSQL 仅开放 catalog 读取；Harness 内的旧直连适配器已永久 fail closed，不提供 SQL 文本、业务行、
Provider、浏览器、驱动或备用 token 回退。数据库写、删除、DDL、迁移和权限能力仍未注册。

Pack、Layer、适用性决策、gate、事件和投影指标使用内容哈希与 append-only 元数据；完整证据保留在独立
artifact/MCP evidence store。相同上下文可复用，需求修正或人工否决必须创建显式 superseding 版本；
中断的同一 collection 可恢复，变化后的 collection 必须先淘汰旧快照。稳定阻断码区分 incomplete、stale、
conflict、source unavailable、hash mismatch、projection budget 和 version mismatch。

worker 只接收原始有界需求文本和角色投影，不复制完整源码、MCP envelope 或治理大对象。Tier 0 上限
2 KiB、Tier 1 上限 12 KiB；确定性 110 KiB fixture 要求至少减少 80%。pack ID、投影哈希和四层哈希
同时绑定 scope confirmation、worktree/full-stack/multi-service manifest，并在 workspace access 与最终
apply 前重新校验。正式 `his-harness-core` 插件版本为
`0.3.1+codex.20260830-change-context-pack`，身份和审阅源码哈希以冻结 inventory 为准。

`./scripts/verify.sh unit|offline|manager-static|architecture` 会在导入 Harness 前强制创建新的
`/private/tmp/his-harness-verify.*` 控制库和知识目录，禁止测试默认写入正式 `data/harness.sqlite`。
离线回归只证明技术合同；真实数据库 MCP、HIS 业务、运行时与发布状态仍必须分别验收，不能据此把
`business_valid`、`runtime_verified` 或 `promotion_enabled` 提升为 true。

## 命令运行环境

Harness 的 CLI 会优先自动切换到项目自己的 `.venv`。因此可直接使用
`python3 tools/harness_doctor.py`、`python3 tools/pg_evidence.py`、
`python3 harnesses/his_requirement_workflow.py` 或 `python3 tools/self_check.py`；
不会因为系统 Python 缺少、但 Harness `.venv` 已安装的依赖而误报环境故障。若排查
启动器本身，可临时设置 `HARNESS_DISABLE_VENV_REEXEC=1`，该开关只用于诊断。

## 可靠性闭环（2026-08）

所有入口先做运行前诊断，并在默认控制目录不可写时切换到私有临时控制库；该降级只允许只读分析，原目录和用户产物不会被覆盖或删除。页面 `/runs` 使用后台 Job 和轮询详情，任务异常会落为 `failed`/`interrupted`，带失败阶段、可重试标记和恢复动作。

受控 worktree 使用全局随机运行 key，冲突只阻断、不自动清理。验证状态固定区分 `passed`、`baseline_failed`、`not_run`、`tool_missing`、`failed`、`side_effect_failed`；只有 `passed` 能打开改码/合入门禁。GitHub/TAPD/Jira 未注册真实 adapter 时明确显示 `unsupported`，不会把通用输入误报成已接入。完整边界见 [`docs/reliability-boundaries.md`](docs/reliability-boundaries.md)。

## v0.66 插件能力治理

Harness 现在只负责治理、编排、门禁、审计和兼容路由，不再同时维护云效、Git、
GitLab、数据库和知识库的 provider 实现。默认 `routing_mode` 为 `enforce`：
每次调用先匹配 capability contract，缺失、禁用、越权或插件未安装时 fail closed，
不会静默回到权限更高的旧实现。

### 四个插件的责任

| 插件 | 状态 | 责任 |
| --- | --- | --- |
| `his-harness-core` | `canonical` | HIS/DFHIS 编排、工作项接入、任务历史和真实改码前的需求合理性、合规性、完整性、可修改性与一次修改合同门禁 |
| `yunxiao` | `canonical` | 云效工作项详情、评论、内联文件和附件的只读证据；写能力只声明边界，当前禁用 |
| `his-engineering` | `canonical` | 本地 Git 检查/应用/受控交付、GitLab MCP 只读连接、数据库 MCP 目录证据和数据库变更计划；普通改码不写远端，外部写入仍走独立受控交付计划，MCP 失败不回退 Provider |
| `his-knowledge` | `canonical` | 有作用域和证据等级的检索、客服式问答、候选记忆、独立审核与显式推广；不承诺“什么都能答” |

表中的 `canonical` 表示当前正式插件目录中的权威能力实现。当前
`/Users/lym/plugins` 下的四个正式插件已经安装，并由
`config/plugin_inventory.json` 中记录的 SHA-256 清单校验；任一插件缺失、
能力清单漂移或来源文件变化时，`enforce` 路由会按设计 fail closed。

Harness 不持有 provider 凭证，也不绕过插件直接执行外部操作。任务历史回答“这次
发生了什么”，知识库回答“哪些结论可以复用”，两者独立保存、不能互相替代。
正式启动会校验插件根目录、版本、能力清单、入口和全部声明依赖的 SHA-256；任一
来源变化都拒绝启动。兼容适配器不会静默使用仓库 staging 目录，只有离线测试和
`self_check.py` 会在自身进程内显式启用仓库随附的冻结插件副本。

### 云效需求长期档案

云效读取仍归 `yunxiao/workitem.read` 的只读能力；长期目录、附件清单、哈希和同档更新
是 Harness 的本地档案层，不是新的云效写能力。指定档案根目录后，每个编号固定写入
`<root>/DFHIS-<编号>/`；`requirement.md` 是唯一持续更新的需求文档，人工补充区不会被
后续同步覆盖。正文、评论、普通附件和内联资源分别落到 `yunxiao/`，运行报告落到 `runs/`。

```bash
python3 harnesses/his_requirement_workflow.py \
  --demand-file /absolute/path/to/requirement.txt \
  --yunxiao-read \
  --yunxiao-url 'https://devops.aliyun.com/projex/req/DFHIS-31861' \
  --yunxiao-archive-root '/Users/lym/WorkCode/ai/云效-Harness' \
  --yunxiao-archive-change-note '补充：诊室字段必须来自排班' \
  --execution-mode readonly
```

归档模式只发 GET 请求，并在同一次执行中复用 `yunxiao/snapshot.json`，不会再重复读取云效。
默认单文件上限为 100 MiB；超过时会在 `yunxiao/manifest.json` 中记录 `too_large` 并将档案标为
`partial`，绝不假称全量成功。确实需要无上限下载时，显式加
`--yunxiao-archive-no-size-limit`；Harness 不解压、执行或预览下载的附件。

### 统一角色路由与任务意图

当前执行链要求先固定任务的 `background`、`goal`、`scenarios` 和
`desired_outcome`，再按唯一注册表把角色、tool、capability、canonical Skill/MCP
和 mutation level 串起来。完整路由与验证边界见
[`docs/role-capability-skill-matrix.md`](docs/role-capability-skill-matrix.md)。
`his-code-evidence` 是本地代码证据的 canonical 只读 Skill；Flux-Lite、auto-repair
和角色路由是 Harness 内部能力，不能被外部 ProviderRuntime 当成可执行 capability。

### 改码前必须先理解（v1 理解证据门禁）

云效需求进入 Harness 后，不能因为文字看起来完整就直接改代码。所有会产生本地工程
改动的模式（含 `auto-local`）必须先生成 `requirement_understanding.json/.md`，分别证明：

1. 当前业务背景/痛点、用户使用场景，以及目标和不改动的边界；
2. 实际目标项目、页面或接口入口、调用/数据链路和相邻影响；
3. 有源码依据的允许改动路径，以及现有测试基座、专项命令和人工验收入口。

任一项缺失时，Harness 只会给出下一步只读调查动作（补读云效正文/评论/附件、定位代码
入口和调用链、检查测试基座或向业务方澄清）；不得调用 patch、worktree、预提交验证或
核心闭环执行器。`requirement_governance=observe` 仍会保留治理报告，但不能绕过该门禁。
`auto-local` 也不再跳过项目上下文扫描。

### 截图报错的强制链路闭环（v1）

医保、退费、收费、结算或外部调用的截图报错，不能由“医生申请退费”等背景文字替代
真实执行路径。Harness 会自动要求当前任务的截图观察和用户纠正进入闭环；如果宿主没有
导出对话证据，或云效截图已失效，门禁也会自动运行并 fail closed，而不是要求业务人员
手工做代码调查。已导入的确认代码锚点会自动带入只读源码检索，并生成 `error_chain_closure.json/.md`。
以下六段必须全部有本地
证据：**截图报错文本 -> 菜单/路由 -> 点击事件 -> 前端接口 -> 后端 Controller 分支 ->
外部医保调用**。

任一段缺失，任何改码模式都会 fail closed；只读分析仍会明确列出缺少的源码段，不能把
候选根因或助手的早期猜测当作结论。用户已确认的事实只约束需求含义，仍不替代源码和
运行时返回证据。核心不会抓取任意桌面聊天历史；Codex App、Codex CLI、DeepSeek 桌面等
宿主需要只导出当前任务明确选定的对话/附件。`--conversation-evidence-file` 是这些宿主或
自动化入口传入该本地包的接口，不用于要求业务人员手工补做代码调查。

### 多入口与可替换 Agent 后端

Harness Core 与执行宿主已经分层：终端、Codex App、Codex CLI 和
DeepSeek-Harness-Desktop 都可以作为入口，统一经过同一套角色/capability/验证/审计链。
Codex CLI 和 Codex App Server 都只是可选 adapter，不是 Core 的硬依赖；默认后端为
provider-neutral 的 `host-bridge`，注册表见 [`config/agent_backends.json`](config/agent_backends.json)，协议和
宿主发现入口见 [`docs/role-capability-skill-matrix.md`](docs/role-capability-skill-matrix.md)
及 [`tools/harness_agent_bridge.py`](tools/harness_agent_bridge.py)。

终端若明确要使用本机 Codex CLI，给 `local-agent run/retry/auto-repair` 增加
`--agent-backend codex-cli`；若要使用桌面端随附的官方 App Server，则显式使用
`--agent-backend codex-app-server`。两者都不会在导入 Harness Core 时自动启动或校验。
App Server 会启动独立的临时 thread，不会接管当前 Codex Desktop 窗口的对话；它固定为
worktree 写入、网络关闭、无 provider 标识回写。DeepSeek-Harness-Desktop 仍通过同一
Host Bridge 合同接入，需要它自身提供 handler。
在当前 Desktop runtime 中，Reviewer 的 schema 完整性在 Harness 本地校验并复核哈希；
不会向 App Server 传会导致终态错误的 `outputSchema` 参数。

### `question` 与 `task`

- Manager 的**自动意图路由**入口为 `/routing`。普通问题优先查询知识库；
  需求相关问题进入完整需求流程，分类后会立即执行对应下游，不要求用户预选知识页或需求页。
  服务端签发非敏感会话 cookie，空会话别名的连续提交仍保持需求模式在会话内粘滞；只有用户
  明确使用可选纠正控件时才允许切换。
- `question`：只调用 `his-knowledge` 的只读检索与回答。证据过期、冲突或缺少当前
  云效/数据库/运行时事实时，必须返回需要补充的证据，不能启动改码或假装全知。
- `task`：工作项只读接入后，先完成业务背景/场景/目标边界与项目入口/调用链的理解取证，
  再执行需求治理和一次修改合同；只有需求合理、合规、完整、范围明确且专项验证可执行，
  才进入本地 Git 变更。审核可修时继续修复并重审，
  不能修时说明具体原因并停止。
- 普通咨询的云效状态为 `not_applicable`；没有云效工作项的需求记录为 `unlinked` 并
  继续需求流程。Provider 缺失或失败不得把需求降级为普通咨询。分类结果不是外部写授权，
  云效写仍需明确动作；Git/RC/GitLab 由一次明确的交付计划绑定动作、目标和计划哈希；数据库
  默认禁止修改和删除，只有用户明确授权精确变更范围后才可进入独立变更流程，当前无写执行器。

### 数据库与知识边界

数据库只有在静态代码和云效证据不足时才进入流程。可执行只读查询由 PostgreSQL MCP
连接器完成；Harness 只传递目标别名、目录操作和范围预算，不读取、注入或持久化 DSN、账号、
密码。旧参数 `--database-credentials-file` 已禁用，凭证只能由 MCP 连接器根据
`HARNESS_CREDENTIALS_FILE` 或连接器专用环境变量解析。数据库修改只允许
`--database-change-file` 生成 `database.change-plan`，当前没有真实变更执行器。

本机 152 开发环境的非敏感策略在
`config/pg_evidence_profiles.local.json`：它使用 `his_152` Profile 和
`schemas: ["*"]`。这表示 schema 是否可读完全以 `df_bi` 在 PostgreSQL 中的真实授权
为准；Harness 不再维护第二份 schema 白名单。该策略不含地址、账号或密码，实际连接仍只从
本机凭证文件读取。当前 `his_152` 显式绑定到既有的 `his_test` 只读凭证三项；该绑定只在
运行进程内生效，不复制密码、不改动凭证文件，若将来提供完整的 `pg_his_152_readonly_*`，
则它会优先于兼容绑定。

知识库启动时不会自动建库、导入 seed 或写持久化数据。当前本地知识库根目录为
`/Users/lym/WorkCode/ai/his-knowledge`，其中
`/Users/lym/WorkCode/ai/his-knowledge/vault` 可作为 Obsidian vault 打开，
`/Users/lym/WorkCode/ai/his-knowledge/knowledge.sqlite` 由 `his-knowledge`
插件作为结构化索引维护。新环境要先显式导入：

```bash
python3 /Users/lym/plugins/his-knowledge/scripts/import_seed.py \
  --home /Users/lym/WorkCode/ai/his-knowledge \
  --seed /Users/lym/plugins/his-knowledge/assets/seed_knowledge.json
```

`knowledge_home` 必须是绝对路径；未导入时 `question` 会返回 `unsupported`，不会
把空库伪装成已知答案。任务审计仍只进入 Harness history。只有显式提供
`--knowledge-candidate-file` 的结构化、可复用内容才可创建 pending candidate；
普通 run 报告不会自动变成知识，candidate 也不会自动审核或推广。

Obsidian 只负责人可读 Markdown 和人工整理，不直接作为唯一事实源。建议每条笔记
使用 frontmatter 标注 `hospital`、`module`、`source_ticket`、`evidence_level`、
`valid_until` 和 `knowledge_id`；Harness 检索时以 SQLite 索引和证据状态为准，
再回链到 Obsidian 笔记。这样可以做到“像客服一样回答”，但只在有证据、未过期、
无冲突的范围内回答；证据不足时必须返回需要补充的云效、代码、数据库或人工验收
证据。

### 权限等级

| 等级 | 含义 | 当前示例 |
| --- | --- | --- |
| `L0` | 本地预览或纯计算，不读取凭证 | 需求治理、知识问答、Git 状态检查、数据库变更计划 |
| `L1` | 只读证据，可使用只读凭证 | 云效读取、GitLab 读取、数据库读取 |
| `L2` | 有精确范围的本地持久化 | 本地应用 patch、知识候选/审核/推广 |
| `L3` | 受控本地交付 | 本地 commit；必须显式进入独立交付流程 |
| `L4` | 外部系统写入 | 云效写仍禁用；Git/RC 仅按不可变交付计划执行；GitLab 写入需结构化计划和 Provider 核验回执 |
| `L5` | 数据库或生产级变更 | 数据库真实写入，当前 `disabled` |

全局 `external_writes_default=false` 阻断任意、未绑定目标的 L4/L5 调用；持有 token、PAT、
数据库账号或仅提出目标都不会自动提升权限。已由用户明确要求并写入不可变 Git delivery
计划的非 force Git/RC 操作，以及计划内 GitLab Provider 写入，是独立的窄范围交付链路：
它们仍必须通过精确目标绑定、远端回读和恢复态保护，不能扩展为通用外部写权限。

### 安装

正式插件目录是 `/Users/lym/plugins/<plugin-name>`。当前四个正式插件已安装。
需要重新安装或迁移时，该动作会修改正式本地目录，因此不属于 Harness 自动流程，
必须由用户明确执行。以下命令只适用于四个目标目录均不存在、且
`HIS_PLUGIN_SOURCE_ROOT` 指向已审核的四插件源码根目录时；任一目标已存在就停止，
不覆盖、不合并：

```bash
(
  set -eu
  HIS_PLUGIN_SOURCE_ROOT='/absolute/path/to/verified/plugins'
  test -f "$HIS_PLUGIN_SOURCE_ROOT/his-harness-core/.codex-plugin/plugin.json"
  test -f "$HIS_PLUGIN_SOURCE_ROOT/yunxiao/.codex-plugin/plugin.json"
  test -f "$HIS_PLUGIN_SOURCE_ROOT/his-engineering/.codex-plugin/plugin.json"
  test -f "$HIS_PLUGIN_SOURCE_ROOT/his-knowledge/.codex-plugin/plugin.json"
  test ! -e /Users/lym/plugins/his-harness-core
  test ! -e /Users/lym/plugins/yunxiao
  test ! -e /Users/lym/plugins/his-engineering
  test ! -e /Users/lym/plugins/his-knowledge
  mkdir -p /Users/lym/plugins
  cp -R "$HIS_PLUGIN_SOURCE_ROOT/his-harness-core" /Users/lym/plugins/
  cp -R "$HIS_PLUGIN_SOURCE_ROOT/yunxiao" /Users/lym/plugins/
  cp -R "$HIS_PLUGIN_SOURCE_ROOT/his-engineering" /Users/lym/plugins/
  cp -R "$HIS_PLUGIN_SOURCE_ROOT/his-knowledge" /Users/lym/plugins/
)
```

插件 marketplace 的安装策略是 `AVAILABLE`、认证策略是 `ON_USE`；安装文件不等于
授权凭证或外部写入。当前版本不会自动安装、覆盖、升级或删除正式插件。

### 验证与回退

从 Harness 根目录运行：

```bash
python3 -m unittest tests.test_plugin_inventory tests.test_plugin_documentation
python3 tools/plugin_replay_suite.py \
  --manifest fixtures/replay/plugin_migration_v1.json \
  --output-dir /tmp/his_plugin_migration_replay
python3 tools/self_check.py \
  --mode mock \
  --retain-output \
  --output-dir /tmp/his_plugin_self_check
```

插件回放必须 12/12 通过且外部调用、外部写入、凭证暴露、L4 请求和知识推广均为
0。需要临时回退时，只把 `config/capabilities.json` 的 `routing_mode` 从
`enforce` 改为 `legacy`；不得删除 run 数据、Git delivery 事务、`knowledge.sqlite`、
插件或 marketplace。旧 Skill 与旧 CLI 当前均为 `compatibility`，只做代理或
fail-closed，不维护第二套实现。本版本不得删除；只能在下一版本提出删除计划，且
必须先提供实际使用证据并取得用户确认。

离线单测、回放和 mock self-check 只证明技术合同：
`business_valid=false`、`runtime_verified=false`、`promotion_enabled=false`。
它们不等于真实云效、GitLab、数据库、HIS 页面或生产业务验收。

下方 v0.65 及更早章节保留为历史与兼容说明；若其中的旧默认行为、云效写入口或
provider 路径与本节冲突，以 v0.66 capability manifest 和本节边界为准。

### Manager 多 Provider 静态能力边界（阶段 A 历史快照）

阶段 A 时，Manager 仅静态发现并展示 Yunxiao、Git/GitLab、数据库与
`his-knowledge` 的静态 capability contract。`his-engineering` 是 canonical Git Provider
能力来源，但 `his-git-local`/`git.inspect` 当时只显示状态和记录 handoff，
不直接运行 Git。该历史阶段的 OS sandbox executor 均未登记，未登记时执行 blocked；
contract 为 `enabled` 也不等于执行授权。

该历史静态展示不读取凭证、不连接外部系统、不执行 Provider 代码，也不验证真实业务环境。
状态展示不构成显式授权，也不等于真实连接测试。
即使 manifest 被错误配置，`workitem.write`、`git.push`、`gitlab.write` 与
`database.change` 仍保持 disabled。本段只保留阶段 A 的实施背景；当前执行状态
以下方“阶段 B/C 完整受控流程”为准。

### Manager Provider 配置中心阶段 A（历史快照）

Provider Profile 的无密钥配置与 AES-GCM **加密凭证**现已保存到 **Manager 数据库**；
旧 `provider_profiles.json` 只作为可恢复的首次导入源，不再接收页面新增配置。加密
主密钥 `HARNESS_MANAGER_CREDENTIAL_MASTER_KEY` 只由 Manager 的服务部署环境提供，
不会写入 Manager 数据库，也不把 Provider 配置存放到个人 macOS 凭证设施。页面和
API 只返回凭证是否已配置，从不回显密钥值、尾号或派生请求头。

当前采用 **SQLite 本地**优先策略，继续使用 Harness 自身的 `data/harness.sqlite`；
**PostgreSQL 团队部署**是后续中心化 Manager 的目标，须先完成备份、导出、导入、
校验、角色权限和回滚演练。本阶段不迁移或删除现有 SQLite 历史数据，Manager UI
通过 Repository 合同访问配置域，后续切换团队数据库不需要重做 Provider 表单。

安全边界保持固定：**数据库永久只读**，Harness 只允许元数据/视图/表结构查询、
只读 SQL 和修改 SQL 草案；草案只能交给用户在 Harness 之外人工执行，不存在数据库
写执行器、执行 API 或写任务队列。知识咨询先检索已验证且未过期的知识，命中后直接
返回；未命中只标记需要后续处理，本阶段不自动升级到模型、不自动把咨询推广为正式
知识。当时的模型页面只提供无网络、无凭证读取的 smoke 前置检查；
后续 B/C 受控单节点 smoke 不改变常规真实模型 runtime 仍冻结的边界。

在该历史快照中，阶段 A 没有路由执行云效动作、远端 Git/GitLab 动作、
模型网络请求或业务数据库写入，也没有登记这些 Provider executor。配置保存不构成连接、
读取、提交、推送、评论、状态流转或其他外部动作授权。详细合同见
[Manager Provider 配置设计](docs/superpowers/specs/2026-08-09-manager-provider-configuration-design.md)
与 [阶段 A 实施计划](docs/superpowers/plans/2026-08-09-manager-provider-config-stage-a.md)。

### Manager 阶段 B/C 完整受控流程

阶段 B/C 在阶段 A 的配置中心上增加了一次性动作授权、统一 Provider 执行边界、
失败学习候选审核、知识库优先检索和 HIS 业务验收证据。配置完成只说明
`configured`；本地 fake 测试、外部连通验证和业务验收必须分层显示，互不推导。
当前只有已注册动作可进入受控 executor，**正常 Agent DAG 仍冻结**。任何
**真实调用**都必须同时具备所需技术凭证，并通过一次性计划的 Profile、目标、请求人和参数
校验；已注册只读动作无需 Harness 人工确认，非只读动作仍需一次性明确授权。结果必须单独
完成**外部验收**，本地 fake 结果不能代替这些条件。**外部写动作默认禁用**；
若某一已注册动作经专项验证和明确开放，仍必须逐次展示差异、确认、执行一次并回读。
数据库修改和删除默认绝对禁止；只有用户明确要求精确变更范围后才允许单独设计。
当前不存在数据库写 executor。

Manager `/routing` 的需求入口默认使用不读取模型凭证、不联网的本地确定性分析完成只读
12 阶段治理账本，并明确返回 `technical_only=true`、`real_model_used=false`、
`business_valid=false`。该入口用于保证自动分流后的需求流程可以落地运行，但不代表真实
模型质量、代码修改或业务验收；实际改码仍由单独授权的本地 Agent Worker → Reviewer →
人工确认 → 本地应用链路承担。

### 完整本地代码证据与审核（v0.70）

Manager 现在把 `git.diff`、`source.read`、`source.search`、`git.history`、
`verification.run-local` 和 `code.review-local` 登记为一组只读证据与审核能力。普通知识问题
不会触碰 Git；代码位置或调用链问题自动选择源码搜索/读取，历史问题自动增加 Git 历史；
代码审查和需求询问强制先冻结完整 diff，再在私有快照中验证并由固定只读 Reviewer 审核。
缺少仓库配置、验证命令、完整证据或批准结论时 fail closed，不会降级成“只看文件清单”。
多仓需求逐仓冻结并最终整体重验，任一仓变化都会阻断整个证据集。

正式服务通过 `HARNESS_CODE_EVIDENCE_PROJECTS_JSON` 提供受控仓库白名单。每个 alias 只包含
绝对 `path`、`allowed_paths` 和 argv 数组形式的 `verification_commands`；用户不选择能力，
Harness 根据消息自动规划。真实只读 Reviewer 还必须由服务进程显式配置
`HARNESS_CODE_EVIDENCE_REVIEWER_ENABLED=1`；未启用时完整 diff 和本地验证仍可生成，但审核
在模型调用前 fail closed。启用后，提交代码审核或需求消息会把冻结、脱敏且有界的证据交给
Codex Reviewer，并在结果中如实记录 `external_calls=true`，不会把模型调用冒充纯本地操作。
`/code-evidence` 与 `/api/manager/code-evidence` 只展示证据状态、
哈希、变更路径、验证和审核结论；大型工件由有界 artifact API 分块读取。证据根目录由
`HARNESS_CODE_EVIDENCE_ROOT` 指定，必须是 owner-only 的本地目录。以上能力不 commit、push、
写云效、连接外部 Provider 或修改业务数据库；明确修改需求仍须继续走 Worker → Reviewer →
人工一次性确认 → 本地应用。

GitLab 远端代码证据第一阶段复用 Manager 受控 GitLab Provider，补充了 repository file、
commit metadata、commit diff、compare、merge-request commits、merge-request diffs 和 pipeline
jobs 的只读动作。动作必须绑定一次性计划、固定 HTTPS host、项目和精确 ref/path/SHA；完整
源码和 diff 只作为当前调用的临时响应，持久审计只保存哈希、大小、条数和调度事实。
GitLab 写入仍保持 disabled。把这些临时响应密封为 owner-only evidence bundle 并自动送入
现有 Reviewer，是下一阶段工作；第一阶段不能据此声称“远端 GitLab 自动审核闭环已完成”。

| 能力边界 | 当前允许 | 永久或当前禁止 |
| --- | --- | --- |
| Profile 与凭证 | Manager 数据库保存类型化 Profile 和 AES-GCM 密文 | 页面、API、审计和日志回显明文或掩码尾号 |
| Provider 读取 | 一次性计划可执行受控只读动作；技术权限由 token、只读 endpoint/credential 或本地权限决定 | 计划缺失/过期、请求人/目标/参数变化、计划复用或技术权限不足 |
| 外部/本地写入 | 外部写默认禁用；专项开放的已注册动作逐次展示差异、一次性确认、执行一次并回读 | 未开放动作、批量静默写入、重试写入、模型自行授权 |
| 数据库 | 单条受限只读查询、结构读取和人工 SQL 草案 | 默认绝对禁止修改/删除；当前无 DML/DDL/删除 action 或 executor，未来也必须先有用户精确范围授权 |
| 模型 | 固定 prompt 的单节点 smoke；显式部署开关下的只读代码 Reviewer（每次由审核/需求消息触发并记录外部调用） | 未配置开关时调用、模型自行授权、常规模型工具调用和真实模型 DAG |
| 学习与知识 | 失败运行生成候选，审核通过后人工推广，先检索再回答 | 自动晋升、暗中调用模型、候选直接作为正式知识 |
| HIS 验收 | 完整环境、操作者、测试数据、场景证据和明确审核可形成结论 | 用离线测试、smoke 或勾选框替代业务验收 |

部署时必须由服务端环境提供 `HARNESS_MANAGER_CREDENTIAL_MASTER_KEY`；该 AES 主密钥
不通过 UI 输入，也不能与密文一起存储或写入日志。Manager 的标准 UI 操作顺序是
`/providers` 配置 → `/actions` 生成计划（只读免人工确认，非只读逐次确认）→ 查看执行/回读审计 →
`/learning-candidates` 审核候选 → `/knowledge` 检索知识 →
`/business-acceptance` 记录技术和业务证据。

正式迁移不是覆盖复制：先比较源/目标哈希和目标独有文件，确认可恢复备份，再在
**暂存副本**做逐文件**三方合并**；暂存副本使用独立临时 Manager DB 和知识目录完成
fake 回归后才能切换启动路径。出现异常时按已确认备份和旧启动路径**回滚**，不得迁移、
checkpoint、删除或用新库掩盖旧 Manager 数据。完整步骤见
[Manager 运维手册](docs/manager-runbook.md)。

## HarnessManager v0.67 Core/Plugin Compatibility

HarnessManager 的 Stage 1 只读兼容层已接入 Core v0.66 的冻结插件清单、`enforce`
路由和能力状态；历史 `/api/system` 保持兼容，并新增只读
`/api/v1/system/health`、`/api/v1/plugins`、`/api/v1/capabilities`。

```text
Core/plugin compatibility verified = true
business_valid = false
runtime_verified = false
promotion_enabled = false
external_writes_default = false
```

该兼容结论只覆盖本地技术合同，不构成真实 HIS 业务或运行时验收。Stage 1 不读取、
保存或维护任何凭证；云效、GitLab、数据库凭证维护及连接测试/运行时一致性仍是已
批准的 Stage 3 需求。

当真实 DB 存在 WAL/SHM 时，Core 状态会保守返回 `unknown/metadata_only/not_run`，不
打开 SQLite，也不声称健康或新鲜度。Stage 1 的普通 UI 首屏会加载旧任务列表：
`/api/tasks` 会走 `TaskManager().list_tasks()` 的普通 SQLite 连接，曾使 WAL/SHM
元数据改变；因此正式验收必须显式启用 `HARNESS_MANAGER_READ_ONLY_SMOKE=1`。该模式只加载
health/system 状态，前端不自动访问 legacy task/job 接口，后端也以 403 阻断这些路由；
日常 Manager 运行不启用该模式。

## v0.65 原源码 Git 交付闭环

v0.65 在 v0.64 本地研发闭环之后增加受控 Git 交付。普通需求分析、worktree
改码、专项验证和本地原仓库应用仍按原流程执行；只有显式创建交付事务并完成两次
真实运行时验收，才会进入分支、commit、RC 集成和可选远端推送。

核心流程：

```text
release 原源码中的已验证 patch
-> 不可变交付计划
-> release 页面验收
-> 第一次确认
-> 原源码创建任务分支并精确 commit
-> 按计划可选推送任务分支
-> 按计划在原源码同步 RC 并 cherry-pick
-> 提交增量 parity 审计
-> RC 页面二次验收
-> 第二次确认
-> 按计划可选推送 RC
```

默认规则来自 `config/rule_packs/dfhis.default.json`：

- 开发分支：`release_2.15.3_250515`
- 需求分支：`feature-DFHIS-<id>`
- 缺陷分支：`hotfix-DFHIS-<id>`
- 集成分支：`RC_2.16.1_250514`
- 需求提交：`feat: <id>-<url> 《<title>》`
- 缺陷提交：`fix: <id>-<url> 《<title>》`
- 任务分支 push、RC 集成和 RC push 默认全部关闭，只有它们已进入不可变计划并取得对应确认时才执行。

交付分支、commit、cherry-pick 和 push 只在原业务源码目录执行；传给
`--project-path` 的仓库根目录必须直接包含 `.git` 目录。linked worktree 的 `.git`
是文件，会被 `delivery_project_linked_worktree` 硬阻断，临时 worktree 不会生成
交付 commit。混合但可分离的本地改动由 Harness Safety Shelf 保存并按 index、
worktree、未跟踪文件和 SHA-256 逐项恢复；同文件 patch 漂移、未解决 Git 操作、
远端分叉或无法证明的恢复会阻断。

重复执行同一计划会复用同一交付事务；策略、远端动作、验证命令、白名单路径或
patch 任一变化都会生成不同事务，旧确认不会被新计划复用。RC 历史中即使存在等价
patch，也必须证明当前最终文件状态仍包含完整任务增量；多 commit 只存在一部分时
直接阻断。RC 集成检查点先写 repository-local journal，再更新数据库；两步之间
中断时可通过 journal、parity hash 和 RC HEAD 自动协调。

创建交付计划：

```bash
python3 tools/delivery.py prepare \
  --entity-kind requirement \
  --entity-id DFHIS-31557 \
  --title '挂号处理界面证件类型需要默认成身份证。' \
  --url 'https://devops.aliyun.com/projex/req/DFHIS-31557#' \
  --project-path /path/to/business-repo \
  --diff-file /path/to/final.diff \
  --allowed-path src/changed-file.js \
  --verify-command 'targeted verification command' \
  --output-dir /tmp/his_harness_DFHIS-31557_delivery
```

如果本次计划确实需要后续推送任务分支、集成本地 RC、最终推送 RC，分别在
`prepare` 时显式增加 `--push-feature --integrate-rc --push-rc`。没有出现在计划中
的动作不能在后续确认时临时开启。

两段运行时验收和两次交付执行确认：

```bash
python3 tools/delivery.py accept-release \
  --transaction-id <id> \
  --summary 'release 页面验证通过'

python3 tools/delivery.py first-confirmation \
  --transaction-id <id> \
  --confirm

python3 tools/delivery.py accept-rc \
  --transaction-id <id> \
  --summary 'RC 页面二次验证通过'

python3 tools/delivery.py second-confirmation \
  --transaction-id <id> \
  --confirm
```

用户不需要记忆 plan hash；CLI 从持久化事务中读取并复核。第一次确认会执行计划
中的本地 commit、任务分支推送和隔离 RC 集成；RC 验收通过后，第二次确认会重新校验
RC HEAD、工作区、parity、验收记录和远端前置引用，再执行计划中的 RC 推送和 GitLab
动作。两次确认是同一不可变计划的执行门槛，不是再次向用户索要单项 Git 权限；冲突默认
`cherry-pick --abort` 并恢复集成前 HEAD。若计划声明 GitLab 动作，只有 Provider 的
写入读回核验返回 `verified_applied` 回执才收口为 `completed`。
使用模型猜测冲突语义。RC 集成失败但恢复完整时，可重复执行第一次确认；Harness
会复用已审计的任务 commit，不重复创建分支或 commit，再从 RC 集成检查点继续。

专项验证命令属于不可变交付计划的一部分，会完整展示在
`delivery_plan.md`。`git push`、包发布、镜像推送、远端上传和常见外部写入命令
不能伪装成专项验证绕过交付确认。RC 与需求 commit 的原始 patch 文本因行号或基线
上下文不同而不一致时，只有文件集合完全一致、没有白名单外改动且稳定 patch-id
一致才按 `patch_id_equivalent` 放行；无法证明等价时仍按
`unresolved_semantic_difference` 阻断。任何 cherry-pick 冲突都 abort、恢复 RC
集成前 HEAD 并交由用户处理，不自动解决。

Task Manager 工作台会只读展示交付事务和下一步，关联任务状态同步为
`waiting_release_runtime_acceptance`、`release_runtime_accepted`、
`task_commit_created`、`waiting_rc_runtime_acceptance`、
`rc_runtime_accepted` 或 `completed`。云效评论、附件和状态流转仍未接入该 Git
闭环，不能由 Git 确认隐式授权。

v0.65 的自动测试只使用临时 Git 仓库和本地 bare remote，不读取凭证、不访问真实
Git 远端、不写云效、不调用模型、不访问业务 PG。

## v0.58-v0.64 企业级核心稳定化边界

当前优先建设本地需求到 patch 的准确性、事务安全、失败恢复、审计和回滚。常规真实模型与 Agent 团队 DAG 仍不执行；唯一可执行的模型路径是 Manager 中受控、逐次确认的固定单节点 smoke。

- 默认运行模式为本地 `mock`；`openai`、`anthropic`、`real`、`claude`、`zhipu` 的常规运行会在读取凭证前以 `real_model_runtime_frozen` 阻断。旧的 `run-model-provider-smoke` CLI 已永久改为 blocked，不能作为真实模型入口；固定提示、单次、无重试、无 DAG 的 smoke 只能通过 Manager Profile、B1 计划确认、B2 执行边界和 B6 已消费授权执行。
- 每次核心闭环在进入 worktree 前生成前端、后端、数据库、配置四层 `change_ownership_matrix`。评论只能作为线索，不能单独证明后端或数据库已完成。
- 验证后的本地原仓库应用使用确定性事务 journal、目标文件前后哈希、后置 `git diff --check` 和失败反向恢复；重复应用保持幂等。
- Task Manager 可保存不可变 diff 和目标文件后置哈希。实际本地回滚必须显式执行 `rollback-apply` 并输入精确 `ROLLBACK:<change_id>`；目标漂移时拒绝修改。
- 启动时会把超过 24 小时仍为 `running` 的本地运行收敛为 `interrupted` 并保存审计产物；未完成的本地应用 journal 会在下次同类应用前恢复或标记 `recovery_required`。
- SQLite 使用版本化 migration、外键、WAL 和 busy timeout；备份与恢复必须校验 SHA-256、完整性和精确 `RESTORE:<sha256>` 确认。
- 数据保留默认只预览。实际清理使用“最近 N 天”和“最近 N 次”的并集，并保护 Task Manager、修改历史、云效审计和运行中记录；精确确认后先完整备份再事务删除。
- 固定脱敏 replay 包含 10 个真实需求场景及负例，覆盖默认值、参数传递、页面状态、后端契约、前后端归属、稳定排序和医保/收费人工阻断。
- 统一离线企业门禁串行执行编译、全量单测、mock self-check、真实需求 replay 和高置信密钥扫描；子进程不会继承凭证环境变量，也不会使用持久化数据库。
- 可复现发布包只包含显式白名单中的源码、测试、fixture、示例配置、文档和 CI；不包含 `data/`、个人配置、运行记录、快照、worktree 或缓存。
- 需求确认中的既往样本规则已迁移到版本化 `config/contract_plugins` 数据包；核心校准器只负责通用优先级、合并和阻断，不再堆叠 DFHIS 票据特例。
- Harness worktree 使用旁路生命周期标记。启动时只读识别近期、超时、脏、未登记和孤立标记；清理默认仅预览，必须满足 Harness 归属、项目白名单、超过 24 小时、Git 登记且干净，并提供精确 `CLEANUP:<plan_hash>`。
- 业务 PG 仅在用户明确要求查询时走独立只读适配器；创建分支、提交、推送、部署、云效/TAPD 写入继续关闭。

个人本地企业级核心已于 2026-07-16 完成验收，详见 `docs/enterprise/HARNESS_ENTERPRISE_CORE_ACCEPTANCE_2026-07-16.md`。最终离线企业门禁连续 20/20 轮通过，每轮执行 272 个单元测试和 10 条真实需求 replay；`business_valid`、`runtime_verified` 和 `promotion_enabled` 仍为 `false`。

旧版 v0.57 `run-model-provider-smoke` CLI 已退役为 blocked 兼容诊断，不能读取 credentials 文件或触发网络。它不解冻常规真实模型入口，也不解冻 Agent 团队 DAG；真实固定 smoke 仅可走 Manager 受控动作。

### 离线企业门禁

```bash
python3 tools/enterprise_gate.py \
  --output-dir /tmp/his_harness_enterprise_gate
```

门禁通过只表示 `technical_valid=true`。`business_valid`、`runtime_verified` 和 `promotion_enabled` 仍固定为 `false`，不能替代真实 HIS 页面、接口和人工业务验收。

### 数据库治理

```bash
python3 tools/database_admin.py status --database /path/to/harness.sqlite
python3 tools/database_admin.py backup --database /path/to/harness.sqlite --reason manual
python3 tools/database_admin.py retention-preview --database /path/to/harness.sqlite --keep-days 30 --keep-recent-runs 200
```

`retention-preview` 不修改数据库。实际 `retention-apply` 必须使用预览产生的精确 `PRUNE:<plan_hash>`；清理前生成的完整归档不会被自动删除。

### 可复现本地发布包

```bash
python3 tools/build_release_bundle.py \
  --version 0.65.0 \
  --output-dir /tmp/his_harness_release
```

该命令只在本地生成归档、manifest 和 SHA-256 文件，不提交、不上传、不部署。

### Worktree 安全恢复

```bash
python3 tools/cleanup_worktrees.py \
  --worktree-dir /tmp/his_harness_worktrees \
  --project-path /path/to/business-repo \
  --max-age-hours 24
```

默认只输出候选项和精确确认码。需要清理时重新执行同一命令并追加 `--apply --confirm 'CLEANUP:<plan_hash>'`。无生命周期标记、未达到超时、存在改动、Git 未登记或项目不在白名单时均不会删除；该工具不使用 `rm -rf` 或强制 worktree 删除作为兜底。

0.5 版目标不是“跑通一个聊天流程”，而是形成可审查闭环：

```text
真实需求输入 -> 专家团产出报告 -> 独立 Evaluator 审核 -> 不合格自动返工 -> 最终可审查报告
```

第一版仍不自动修改业务代码、不自动提交、不自动发布。

0.6 版新增只读工程分析能力：

```text
真实需求输入 -> 只读项目扫描 -> 工程证据包 -> 专家团产出报告 -> 独立 Evaluator 审核 -> 不合格自动返工 -> 最终可审查报告
```

本阶段只允许 `read` 动作，`test/write/git/ci/deploy` 默认关闭。

0.7 版新增受控本地改码能力：

```text
真实需求输入 -> 只读项目扫描 -> 专家团报告 -> Evaluator 审核 -> 独立 Git worktree -> 白名单 patch -> 验证命令 -> 可审查 diff
```

默认仍是 `readonly`。只有显式传入 `--execution-mode worktree` 才会创建临时 Git worktree 并尝试应用 patch。

0.7.1 版新增已提交 diff 审查能力：

```text
已提交修复 -> 提交 diff 证据 -> 专家团审查 -> Evaluator 审核 -> 独立 Git worktree 验证 -> 可进入测试/人工代码审查结论
```

显式传入 `--execution-mode review-worktree` 时，Harness 只审查已有提交，不生成 patch、不 `git apply`。

0.7.2 版增强 `review-worktree` 的验证基线策略：

```text
已提交修复 -> base/head 双 worktree -> 同一验证命令两边执行 -> 错误指纹对比 -> 区分历史基线与本次回归
```

如果 base/head 都失败且错误指纹一致，Harness 会标记为 `baseline_existing` warning，不阻断当前提交；如果 head 新失败、错误指纹变化或执行环境异常，仍会阻断。

0.7.3 版新增验证命令副作用检测：

```text
验证命令执行前后 -> 记录 git status/diff -> 发现临时 worktree 被修改 -> warning 或阻断
```

在 `review-worktree` 中，只有 base worktree 被验证命令修改会标记为 `baseline_side_effect` warning；head worktree 被验证命令修改会标记为 `head_side_effect_failed` 并阻断当前提交。在 `worktree` patch 模式中，任何验证命令修改临时 worktree 都会让当前 attempt 失败，避免把格式化或构建产物副作用混入最终 diff。

0.7.4 版新增云效只读证据和业务澄清闸口：

```text
云效/手工需求 -> 只读工程证据 -> 业务澄清闸口 -> 证据充分才进入 worktree patch -> 验证副作用检测 -> 可审查追加 diff
```

`--yunxiao-read` 默认只读取工作项详情、评论、附件列表和文件信息摘要，不写评论、不改状态、不改负责人。用户明确要求“不看评论”时，追加 `--yunxiao-ignore-comments`；该开关不会请求评论接口，也不会把历史评论送入需求判断。DFHIS-31195 这类“不限时”需求在生成 patch 前必须确认含义、复现、期望、实际结果或附件证据；证据不足时状态为 `blocked_needs_clarification`，不会强行改代码。

0.8.0 版新增云效事务 dry-run 计划层：

```text
运行结果 -> 云效事务建议 -> 策略校验 -> dry-run 审计记录 -> 报告展示
```

`--yunxiao-transaction-mode dry-run` 只生成云效事务建议、策略校验结果和审计记录，不读取 `aliyun_devops_write_pat`，不调用云效评论、状态流转、负责人流转或关闭接口。

0.8.1 版补齐云效全事务 dry-run 计划层：

```text
运行结果 -> 评论/截图/迭代/负责人/状态/服务变更/产物建议 -> 策略校验 -> dry-run 审计记录 -> 报告展示
```

本阶段仍不读取写 token、不调用云效写接口，只把后续要开放的云效动作先纳入可审查计划。

0.8.2 版新增云效受控写入执行层：

```text
运行结果 -> 云效事务建议 -> 策略校验 -> 双开关确认 -> fake/real transport -> 审计和报告
```

默认仍不写云效。只有 `--yunxiao-transaction-mode write`、`--yunxiao-write-confirm WRITE:<entity_kind>:<entity_id>`、策略允许、写凭证存在时，才会进入真实写入执行器。高风险动作还必须传 `--yunxiao-human-confirmed`。

0.8.3 版新增本机凭证文件和云效只读 smoke 验证：

```text
本机凭证文件 -> 凭证摘要/权限检查 -> DFHIS 工作项只读读取 -> smoke 报告
```

默认凭证文件为 `/Users/lym/WorkCode/ai/apiKey/credentials.json`，可用 `HARNESS_CREDENTIALS_FILE` 覆盖。只读 smoke 不写评论、不流转状态、不改负责人。
云效 OpenAPI 默认服务接入点为 `https://openapi-rdc.aliyuncs.com`，可用 `YUNXIAO_API_BASE_URL` 或 `ALIYUN_DEVOPS_BASE_URL` 覆盖；不要把网页登录域名当成接口域名。

0.8.4 版增强云效只读证据：

```text
云效 HTML 描述 -> 清洗纯文本 -> 内联图片/文件识别 -> 只读下载摘要 -> 专家团证据输入
```

`tools/yunxiao_read_check.py` 会输出清洗后的需求正文、内联 `fileIdentifier`、下载文件大小/sha256/content-type 和失败原因。主 Workflow 的 `--yunxiao-read` 会把清洗文本和内联文件证据加入专家团上下文，并把只读下载内容放到 `--output-dir/_yunxiao_evidence`，但仍不写云效。正文可读而内联截图失效时，检查结果为 `passed_with_warnings` 并继续分析；正文、评论或普通附件等可能承载业务规则的证据失败时才要求补充。

0.8.7 版新增需求验收矩阵和项目验证基座：

```text
云效/手工需求 -> 需求验收矩阵 -> 自动验证建议 -> 人工验收项 -> 反驳/纠偏闸口 -> 后续自动改码前置依据
```

`acceptance_matrix.json/md` 会区分需求验收、自动验证、人工验收和阻断项。Harness 会明确判断是否可进入开发、是否可自动改码、是否可自动提交、是否可真实云效流转。v0.8.7 本身不自动改业务代码、不自动提交、不真实流转云效状态；真实业务云效任务仍只允许读取和评论。

0.8.8 版新增技术自治和 worktree 合入能力：

```text
云效/手工需求 -> 自动项目选择 -> 字段来源判断 -> worktree 试错验证 -> final.diff 合入原业务目录 -> 清理临时 worktree
```

Harness 会根据代码上下文判断前端/后端边界、目标文件和代码风格；只有业务规则或字段来源无法证明时才阻断并要求人工确认。`worktree` 模式成功后会在原业务目录仍干净且 `git apply --check final.diff` 通过时合入最终 diff；仍不提交、不推送、不发布、不真实流转云效状态。临时 worktree 成功或失败后都会清理，只保留报告、日志和 diff 摘要。

0.8.9 版新增多项目 fullstack worktree：

```text
云效/手工需求 -> 技术自治判断全栈影响 -> 多项目 worktree -> 全部验证 -> 全部 apply-check -> 合入原业务目录
```

`fullstack-worktree` 当前用于 DFHIS-31270 这类“字段来源已证明、前端展示需补齐”的受控修复。字段已由实际 REST 返回或 `df-his-api` 证据证明时，Harness 只创建住院收费前端的临时 worktree；字段来源不能证明时会阻断，不会为了加一列擅自改后端或 BFF。全部通过后才合入原业务目录；仍不提交、不推送、不发布、不真实流转云效状态。

0.9.1 版新增提交前验证矩阵和审查包：

```text
当前本地 diff -> 临时 worktree 复现 diff -> BFF/前端验证 -> 验证矩阵 -> 代码审查包 -> 提交准备结论
```

`precommit-verify` 用于 v0.8.9 已合入原业务目录后的提交前检查。它不会重新生成 patch，也不会提交；DFHIS-31270 在 v0.9.1 收敛为只验证前端仓库的本地 diff，并在临时 worktree 中执行前端单文件 lint。后端字段来源以实际 REST 响应或 `df-his-api` 只读证据证明，不临时启用 `settings.gradle` 中已注释的 API 源码模块，也不要求 BFF GraphQL 改动。验证失败会生成阻断项，不会伪装成可提交。

0.9.5 版新增单需求真实开发试跑：

```text
一个真实云效需求 -> 云效只读证据 -> 技术自治判断 -> 验收矩阵 -> worktree 受控改码 -> 专项验证 -> 审查包 -> comment-only 交付评论
```

`single-demand-trial` 每次只处理一个需求。它允许在临时 worktree 中试错，验证通过后合入本地原业务目录，但仍不自动 commit、不 push、不发布、不真实流转云效状态、不改负责人、不调迭代、不关闭任务。真实云效只保留 comment-only；状态建议只进入 dry-run/fake 报告，等规则成熟后再逐步开放。

0.10 版新增 Task Manager 基座：

```text
需求/BUG任务 -> task_id/run_id -> 统一运行入口 -> 产物目录 -> 阶段状态 -> 后续 UI 可读取
```

`tools/task_manager.py` 会把一个云效需求或手工需求登记成 `harness_tasks`，每次 Harness 运行登记到 `harness_task_runs`。本版目标是把脚本能力整理成可被界面管理的任务系统基础：任务列表、任务详情、最新 run、产物路径、验证状态、是否可提交。云效仍默认只读，`task_manager.py run` 固定使用 `--yunxiao-transaction-mode off`，不会写评论、不会流转状态、不会改负责人、不会调迭代、不会关闭任务。

0.10.1 版新增行为验收门禁：

```text
当前 diff -> 行为断言 -> 旧逻辑保护检查 -> 空提示/重复提示/关闭动作误判检查 -> 提交前阻断或放行
```

`behavior_acceptance.json/md` 会把需求拆成“必须发生 / 禁止发生 / 必须保持”的行为断言。凡是改到 `$alert`、`$confirm`、`catch`、`loading`、`closeSettlementProgress`、`failActiveSettlementProgressStep`、收费/结算/医保/退费路径，Harness 会额外检查是否存在空提示、重复提示、兜底提示替换真实原因、关闭动作误入业务失败 catch 等问题。

这类行为门禁用于解决“代码能 lint，但交互流程不对”的问题。行为验收未通过时，只允许继续修改 patch，不允许自动提交、云效交付评论或状态流转。

0.10.2 版新增方法级交互测试与 UI 证据基座：

```text
当前 diff -> 行为测试计划 -> 方法级执行结果 -> UI 证据 manifest -> 提交/云效评论放权边界
```

交互敏感改动会生成 `behavior_test_plan.json/md`、`method_regression_result.json/md`、`ui_evidence_manifest.json/md`、`playwright_screenshot_index.md` 和 `interaction_evidence.json/md`。没有方法级交互测试通过时，precommit 不允许进入提交准备；方法级测试通过但缺少截图/视频/GIF/人工 UI 证据时，可以进入提交准备，但仍不允许云效交付评论。云效状态流转继续冻结。

0.10.3A 版新增显式方法级测试命令执行器：

```text
当前 diff -> 行为测试计划 -> --method-test-command -> method_test_runner -> 方法级执行结果 -> 交互证据门禁
```

`--method-test-command` 会在临时 worktree 中执行用户显式传入的命令。命令需要向 stdout 输出 JSON：`{"cases":[{"id":"METHOD-...","status":"pass","evidence":"..."}]}`。Harness 会把这些 `cases` 作为 v0.10.2 方法级证据使用，并额外生成 `method_test_runner.json/md`。本阶段仍不自动打开真实业务页面，也不自动生成 Playwright/Chrome 截图。

0.10.3B 版新增显式 UI 证据采集命令执行器：

```text
当前 diff -> --ui-capture-command -> HARNESS_UI_EVIDENCE_DIR -> ui_evidence_runner -> ui_evidence_manifest -> 云效评论放权边界
```

`--ui-capture-command` 会在临时 worktree 中执行用户显式传入的 UI 采集命令。命令可使用 Playwright、Chrome、人工记录脚本或其他本地工具，截图/视频/GIF/记录文件应写入环境变量 `HARNESS_UI_EVIDENCE_DIR` 指向的目录，并向 stdout 输出 JSON：`{"artifacts":[{"path":"progress_closed.png","kind":"screenshot","label":"进度详情已关闭"}],"assertions":[{"name":"dialog_count","status":"pass","evidence":"未出现重复弹框"}]}`。Harness 会把生成的文件路径自动加入 `ui_evidence_manifest`，并额外生成 `ui_evidence_runner.json/md`。

0.10.3C 版新增 Playwright/Chrome UI 采集模板：

```text
DFHIS 场景 -> tools/ui_capture_template.py -> playwright_capture.mjs + env.example + manual_acceptance_record.md -> --ui-capture-command
```

模板使用 `HIS_UI_BASE_URL`、`HIS_UI_ROUTE` 和 `HIS_UI_STORAGE_STATE` 描述本地前端地址、目标路由和登录态文件；截图、UI 状态 JSON 和断言结果仍通过 v0.10.3B 的 stdout JSON 协议进入 Harness。模板不保存密码、token、cookie 原文，也不自动猜测 HIS 登录流程。

0.10.4 版完成真实 DFHIS 单需求提交前样板：

```text
真实本地 diff -> 白名单含新增文件 -> 临时 worktree 复现 -> lint + 方法级命令 + UI 人工证据 -> 提交范围告警
```

本阶段使用 DFHIS-31465《【运城口腔】挂号窗口新增'科室'过滤条件》跑通样板。precommit 当前 diff 支持白名单内未跟踪新增文件；同仓库存在白名单外未提交改动时，目标验证仍可通过，但 `can_commit` 和 `can_yunxiao_comment` 会保持 `false`。行为门禁拆分需求上下文和代码 diff，避免普通排班过滤改动因页面标题含“收费”被误套结算弹框/进度条用例。

0.10.5 版新增 Task Manager 真实样板登记：

```text
已有 Harness output_dir -> register-run -> 登记型 run_id -> harness_task_runs -> task_manager_real_trial_record
```

`tools/task_manager.py register-run` 用于把 `tools/precommit_verify.py` 这类独立脚本已生成的产物目录登记进 Task Manager。它不会重新跑模型、不会改业务代码、不会提交、不会写云效，只读取产物中的 `precommit_manifest.json`、`verification_matrix.json` 和常见证据文件，创建可被 UI 或后续脚本索引的 `task_id/run_id/output_dir` 记录。DFHIS-31465 已登记为 `task_id=2`、`run_id=325`，登记记录位于 `/tmp/his_harness_DFHIS-31465_v0104_trial/task_manager_real_trial_record.md`。

0.10.6 版新增 Task Manager precommit 复跑入口：

```text
已有 task_id/task_key -> rerun-precommit -> PrecommitVerifier -> 新 output_dir -> 自动登记 task_run
```

`tools/task_manager.py rerun-precommit` 会复用 Task Manager 记录中的任务标题、需求文本、项目路径，或使用命令行显式传入的 `--project-path`、`--allowed-path`、`--verify-command`，重新执行提交前验证并把结果登记回同一个任务。它不提交、不推送、不发布、不写云效。

0.10.7 版新增登记幂等和 run 历史可比：

```text
同 task + 同 output_dir + 同 execution_mode -> register-run 不重复造 task_run
```

重复登记同一个已有产物目录时，Task Manager 会返回原 task_run/run_id，并刷新 `task_manager_real_trial_record`、`task_manager_run_history.json/md` 和最新 artifact 索引，避免一个真实样板被重复登记成多次运行。

0.10.8 版新增 UI 证据复用策略记录：

```text
ui_evidence_manifest/ui_evidence_runner -> ui_evidence_reuse_policy -> 后续复跑/人工验收边界
```

Task Manager 会为登记和复跑产物生成 `ui_evidence_reuse_policy.json/md`，明确同一任务下 UI 证据可复用的条件、不能跨需求复用的边界，以及 Playwright/Chrome/人工证据仍依赖页面启动方式、登录态和测试数据的残余风险。

0.10.9 版新增 Task Manager 只读看板导出：

```text
harness_tasks/harness_task_runs -> dashboard -> task_dashboard.json/md/html
```

`tools/task_manager.py dashboard` 会读取本地 Task Manager 数据库，导出任务、运行历史、最新产物索引、验证状态、是否可提交和 UI 证据状态。该命令只读，不改业务仓库、不提交、不推送、不发布、不写云效。

0.10.10 版新增 Task Manager 看板筛选和真实样板集导出：

```text
harness_tasks/harness_task_runs -> filters -> dashboard + real sample set
```

`tools/task_manager.py dashboard` 支持按 DFHIS 编号、Task Key、任务类型、任务状态、验证状态、UI 证据状态、是否可提交和真实样板筛选。导出时除 `task_dashboard.json/md/html` 外，还会生成 `task_sample_set.json/md`，用于沉淀可复跑、可索引的真实样板集合。该能力仍只读，不改业务仓库、不复跑任务、不提交、不推送、不发布、不写云效。

0.11 版新增 Task Manager 本地任务工作台：

```text
task_id/task_key -> task detail + run detail + artifact paths + copyable rerun command
```

`tools/task_manager.py workbench` 会读取单个 Task Manager 任务，导出 `task_workbench.json/md`，其中包含任务详情、run 详情、产物路径、`open` 产物目录命令和可复制的 `rerun-precommit` 命令。该命令只读，不复跑任务、不改业务仓库、不提交、不推送、不发布、不写云效。

0.12 版新增 Task Manager 只读本地 HTML 工作台入口：

```text
dashboard + sample set + workbench -> task_workspace.html
```

`tools/task_manager.py workspace` 会把 Task Manager dashboard、真实样板集和每个单任务 workbench 汇总到一个本地静态入口，导出 `task_workspace.json/html`、`task_dashboard.*`、`task_sample_set.*` 和 `workbenches/<task_key>/task_workbench.json/md`。该命令只读，只生成本地索引页面和可复制命令，不复跑任务、不改业务仓库、不提交、不推送、不发布、不写云效。

0.13 版新增 Task Manager 历史 run 对比和证据 warning：

```text
task runs -> latest/previous comparison -> stale evidence warnings
```

`tools/task_manager.py workbench` 会在单任务工作台中输出 `run_history_comparison` 和 `evidence_warnings`，用于对比最新 run 和上一条 run 的验证状态、UI 证据状态、产物数量，并标记最新 UI 证据缺失但历史有证据、关键 precommit 产物缺失、产物路径不存在等风险。`workspace` 入口会汇总每个任务的 warning 数量和 warning code。该能力只读，只提示风险，不自动复跑、不改业务仓库、不提交、不推送、不发布、不写云效。

0.13.1 版修复 precommit 大 diff 原文保留问题：

```text
git diff stdout -> full patch for apply-check -> truncated text only for logs
```

`tools/precommit_verify.py` 读取当前本地 diff 时会保留完整 patch 原文，避免超过日志截断长度后把 `...（日志已截断）...` 写入 `current_diff` 并导致临时 worktree `git apply --check` 报 `corrupt patch`。验证命令日志仍保持截断策略，避免报告过大。

0.14 版增强 Task Manager 只读 HTML 工作台 warning 汇总、筛选和搜索：

```text
workspace entries -> warning summary + filter metadata -> static HTML search/filter
```

`tools/task_manager.py workspace` 的数据版本升级为 `0.14-task-workspace`，新增 `warning_summary`、`filter_options`，并在每个 entry 中写入 `filter_data` 和 `search_text`。本地 `task_workspace.html` 会在页面顶部展示 warning 汇总，并支持按 warning code、DFHIS 编号、验证状态、UI 证据状态和关键词进行前端筛选。该页面仍是静态只读 HTML，不复跑任务、不改业务仓库、不提交、不推送、不发布、不写云效。

0.15 版新增需求理解确认卡：

```text
云效/手工需求 -> 用户补充规则优先级 -> 字段/参数和值域确认 -> 复杂需求拆分/阻断 -> 后续技术自治和验收矩阵
```

`requirement_calibration.json/md` 会在主 Workflow 中导出，并写入最终报告。它用于先固定“按谁的规则理解需求”：用户明确说“按照我说的来”时，用户补充规则优先于需求图或云效描述；识别到菜单/路由参数如 `paiBanMs` 时，会记录参数位置、值域和默认行为；医保、结算、收费、报表、对账、金额、基金、统筹、回写等复杂高风险需求会先输出拆分项和必须确认项，不允许直接自动改码。

0.16 版新增 Task Manager 确认卡索引：

```text
requirement_calibration.json/md -> single-task workbench summary/copy -> workspace HTML column/search/filter
```

`tools/task_manager.py workbench` 会把最新 run 的 `requirement_calibration.json/md` 加入产物索引，并在 `task_workbench.md` 中展示确认卡状态、参数名、来源优先级、warning、原文摘要和原始文件路径。`tools/task_manager.py workspace` 会把确认卡复制到 `workbenches/<task_key>/requirement_calibration.json/md`，在静态 HTML 中增加“需求理解确认卡”列，并支持按确认卡状态筛选和关键词搜索参数名、确认卡摘要。该能力仍然只读，不自动复跑、不改业务仓库、不提交、不推送、不发布、不写云效。

0.17A 版新增 Task Manager 修改历史与回滚 dry-run：

```text
task diff -> record-change -> harness_task_changes -> workbench/workspace 修改历史 -> rollback-plan dry-run
```

`tools/task_manager.py record-change` 可以把一次已确认的本地 diff 登记到同一个任务的修改历史账本，形成 `change_id/change_sequence/diff_path/diff_summary`。`tools/task_manager.py rollback-plan` 只生成回滚 dry-run 计划、反向 patch 预览和 `git apply --reverse --check` 命令，不直接执行反向 patch、不修改业务仓库、不提交、不推送、不写云效。`workbench` 会额外导出 `task_change_history.json/md`，`workspace` 会展示“修改历史”和“回滚 dry-run”状态，便于查看某个任务改了几次以及人工需要回退时应检查哪份 diff。

0.17B 版增强 Task Manager 只读 WebUI 详情入口：

```text
workspace entries -> task_details -> 任务详情 tabs -> run/确认卡/修改历史/证据预览/命令
```

`tools/task_manager.py workspace` 的数据版本升级为 `0.17B-task-workspace`，新增 `task_details`。本地 `task_workspace.html` 保留 warning 汇总、筛选和搜索，并新增“任务详情”区域，支持查看概览、Run 历史、需求理解确认卡、修改历史、回滚 dry-run、证据预览和可复制命令。证据预览只截取本地 Markdown/JSON/截图索引等既有产物，不自动打开业务页面、不复跑任务、不执行回滚、不写云效。

0.18 版增强 Task Manager 只读 WebUI 快照对比和导出索引：

```text
previous task_workspace.json -> current workspace export -> snapshot comparison + export index
```

`tools/task_manager.py workspace` 的数据版本升级为 `0.18-task-workspace`。当同一个 `--output-dir` 下已存在上一版 `task_workspace.json` 时，本次导出会生成 `snapshot_comparison`，对比任务数、run 数、warning 数、样板数、修改次数以及每个任务的关键状态变化。同时新增 `export_index`，集中列出 workspace、dashboard、sample set 和各任务 workbench 的导出文件。该能力仍然只读，只比较本地 JSON 摘要和列出本地文件，不复跑、不回滚、不打开业务页面、不写云效。

0.19 版增强 Task Manager 只读 WebUI 多快照浏览和证据趋势：

```text
workspace exports -> workspace_snapshots/* -> snapshot history + selectable comparisons + evidence trend
```

`tools/task_manager.py workspace` 的数据版本升级为 `0.19-task-workspace`。同一 `--output-dir` 每次导出都会把当前 `task_workspace.json` 归档到 `workspace_snapshots/<snapshot_id>/task_workspace.json`，并生成 `snapshot_history`、`evidence_trend`、多快照对比数据和静态 HTML 选择控件。页面支持选择任意两个已归档快照查看 run、warning、修改次数等摘要变化，并展示每个任务的 UI 证据、warning、验证状态、确认卡状态和修改次数趋势。该能力仍然只读，不读取远端、不复跑、不回滚、不执行命令、不写云效。

0.20 版增强 Task Manager 只读 WebUI 导航结构、历史快照详情和证据预览体验：

```text
workspace sections -> readonly navigation -> snapshot detail -> collapsible evidence preview
```

`tools/task_manager.py workspace` 的数据版本升级为 `0.20-task-workspace`，新增 `navigation` 和 `snapshot_detail`。本地 `task_workspace.html` 会增加顶部导航、页面分区锚点、历史快照详情区域，并把证据预览改为摘要加可展开明细，避免大段 Markdown/JSON 直接铺满页面。该能力仍然只读，只展示本地已归档 workspace 摘要和既有证据片段，不读取远端、不复跑、不回滚、不执行命令、不写云效。

0.21 版增强 Task Manager 只读 WebUI 视觉可读性、空态/错误态和离线审查包：

```text
workspace ui polish -> empty/error states -> offline review package -> static readonly HTML
```

`tools/task_manager.py workspace` 的数据版本升级为 `0.21-task-workspace`，新增 `ui_polish` 和 `offline_review`。本地 `task_workspace.html` 增加状态标签、大表横向滚动容器、空态/错误态说明和“离线审查包”区域；同时导出 `task_workspace_offline_review.json/md`，汇总应保留的 HTML、JSON、Markdown、workbench 和快照文件。该能力仍然只读，只说明和索引本地已有产物，不读取远端、不复跑、不回滚、不执行命令、不写云效。

0.22 版新增配置中心和规则包骨架：

```text
Rule Pack + Profile + Credential Store -> readonly config summary -> optional workspace configuration page
```

`app/harness_config.py` 新增 Rule Pack、Profile 和 Credential Store 只读摘要能力。默认规则包位于 `config/rule_packs/dfhis.default.json`，profile 示例位于 `config/profiles.example.json`；配置文件只保存 Git 规范、评论模板、状态流转规则、验证要求、风险规则、需求来源类型和凭证 key 引用，不保存真实 token。`tools/config_check.py` 可单独输出配置摘要；`tools/task_manager.py workspace --include-config-summary` 会显式把配置摘要加入本地 HTML 工作台，生成 `task_workspace_config_summary.json/md`。不传新参数时，workspace 仍输出 `0.21-task-workspace`，旧命令默认行为不变。

0.23 版新增需求来源 provider 只读归一化：

```text
Yunxiao/TAPD/manual/file local payload -> requirement_evidence.json/md -> normalized readonly schema
```

`app/requirement_provider.py` 新增本地归一化能力，把 Yunxiao、TAPD、manual、file 这类来源整理成统一的 `source_type/source_url/external_id/title/description_text/comments/attachments/images/status/assignee/fetched_at/warnings` 结构。`tools/requirement_provider_check.py` 可单独把本地 JSON、文本或手工参数导出为 `requirement_evidence.json/md`。本阶段不联网读取 TAPD、不改变现有 `--yunxiao-read` 路径、不写云效/TAPD、不保存真实 token；它只是后续 WebUI、需求拆分和多平台需求接入的只读证据底座。

0.24 版新增需求来源证据显式接入：

```text
requirement_evidence.json/md -> --requirement-evidence-file -> workflow report + Task Manager workbench/workspace
```

主 Workflow 新增显式 `--requirement-evidence-file` 参数，可把 v0.23 生成的本地需求来源证据加入需求上下文，并随运行产出 `requirement_evidence.json/md`。Task Manager 会在登记 run 时索引这两个文件，单任务 workbench 和只读 HTML workspace 会展示来源类型、外部 ID、标题、状态、负责人、附件/图片/评论数量、warning 和预览链接。该能力只读取本地产物；不传新参数时旧 workflow、默认 demo、Task Manager workspace 和云效只读路径仍保持原行为。

0.25 版新增配置预览和 Provider 模板草案：

```text
Rule Pack + Profile + Credential Store -> readonly configuration preview -> provider templates + rule preview
```

`app/harness_config.py` 新增 `0.25-configuration-preview`，把 Rule Pack、Profile 和 Credential Store 摘要转换成可分享的只读配置预览。Provider 模板覆盖 Yunxiao、TAPD、manual、file，并预留 Jira/GitHub Issue 等来源；模板只展示 credential key、状态和用途，不展示真实 token。`tools/config_check.py --include-preview` 会额外导出 `harness_config_preview.json/md`；`tools/task_manager.py workspace --include-config-preview` 会显式生成 `0.25-task-workspace`，增加“配置预览”区域和 `task_workspace_config_preview.json/md`。该能力不读取远端、不测试连通性、不写云效/TAPD、不 commit/push、不执行回滚或发布；不传新参数时默认 workspace 仍保持 v0.21。

0.26 版新增配置分享校验和本地覆盖策略：

```text
Rule Pack/Profile templates -> share-package validation -> local override strategy preview
```

`app/harness_config.py` 新增 `0.26-configuration-share-validation`，检查团队共享模板是否保持外部写入关闭、真实状态流转关闭、Git 自动动作关闭，并扫描明显的真实密钥字段和个人绝对路径。`tools/config_check.py --include-share-validation` 会额外导出 `harness_config_share_validation.json/md`；`tools/task_manager.py workspace --include-config-share-validation` 会显式生成 `0.26-task-workspace`，增加“配置分享校验”和“本地覆盖策略”区域。该能力只读，不会应用配置、不会写入 `~/.his-harness`、不会测试远端账号或项目路径；不传新参数时默认 workspace 仍保持 v0.21。

0.27 版新增配置导入草案和示例文件生成：

```text
config summary/share validation -> user-selected draft dir -> profiles/rule pack/credentials example + readonly workspace guide
```

`app/harness_config.py` 新增 `0.27-configuration-import-draft`，可基于当前 Rule Pack、Profile 和 Credential Store 摘要生成 secret-free 导入草案。`tools/config_check.py --include-import-draft --draft-output-dir <dir>` 会在用户显式选择的目录生成 `profiles.draft.json`、`rule_pack.draft.json`、`credentials.example.json`、`IMPORT_GUIDE.md` 和 `config_import_manifest.json`；默认不覆盖同名文件，只有显式传 `--overwrite-drafts` 才覆盖。`tools/task_manager.py workspace --include-config-import-draft --draft-output-dir <dir>` 会显式生成 `0.27-task-workspace`，增加“配置导入草案”区域和 `task_workspace_config_import_draft.json/md`。该能力只生成草案和人工复制命令，不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号、不读取或写入云效/TAPD；不传新参数时默认 workspace 仍保持 v0.21。

0.28 版新增配置导入草案回读校验和只读表单预览：

```text
user-selected draft dir -> JSON/readback validation -> readonly form preview + import-before risk prompts
```

`app/harness_config.py` 新增 `0.28-configuration-import-review`，回读 v0.27 生成的 `profiles.draft.json`、`rule_pack.draft.json`、`credentials.example.json`、`IMPORT_GUIDE.md` 和 `config_import_manifest.json`，检查 JSON 结构、明显密钥泄漏、占位路径、个人路径、硬保护开关、Git 自动动作和真实状态流转开关。`tools/config_check.py --review-import-draft --draft-input-dir <dir>` 会导出 `harness_config_import_review.json/md`；`tools/task_manager.py workspace --include-config-import-review --draft-input-dir <dir>` 会显式生成 `0.28-task-workspace`，增加“配置导入回读校验”“只读表单预览”和“导入前风险提示”区域。该能力只读取用户选择目录中的草案文件，不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号、不读取或写入云效/TAPD；不传新参数时默认 workspace 仍保持 v0.21。

0.29 版新增配置差异对比、多 Profile 切换预览和团队模板索引：

```text
one or two draft dirs -> template index -> profile switch preview + config diff summary + team template file index
```

`app/harness_config.py` 新增 `0.29-configuration-template-index`，可索引一个或两个 v0.27 草案目录，展示每个 profile 的 provider、credential key、路径状态和人工确认边界，并对前两个草案目录做只读差异摘要：profile 增删、provider 变化、credential key 增删、提交评论模板、硬保护、Git 权限、状态流转和路径状态变化。`tools/config_check.py --include-template-index --draft-input-dir <dir> [--compare-draft-input-dir <dir>]` 会导出 `harness_config_template_index.json/md`；`tools/task_manager.py workspace --include-config-template-index --draft-input-dir <dir>` 会显式生成 `0.29-task-workspace`，增加“配置模板索引”“多 Profile 切换预览”和“配置差异对比”区域。该能力只读，不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号、不读取或写入云效/TAPD；不传新参数时默认 workspace 仍保持 v0.21。

0.30 版新增只读配置向导页面：

```text
config summary + preview + share validation + draft readback + template index -> readonly configuration wizard
```

`app/harness_config.py` 新增 `0.30-configuration-wizard`，把配置摘要、Provider 模板、分享校验、生成草案、回读校验、对比模板和人工复制前确认串成一个只读向导。`tools/config_check.py --include-config-wizard --draft-input-dir <dir> [--compare-draft-input-dir <dir>]` 会导出 `harness_config_wizard.json/md`；`tools/task_manager.py workspace --include-config-wizard --draft-input-dir <dir>` 会显式生成 `0.30-task-workspace`，增加“配置向导”区域和复制命令。该能力只聚合本地只读结果，不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号、不读取或写入云效/TAPD；不传新参数时默认 workspace 仍保持 v0.21。

0.31 版增强配置向导可读性：

```text
readonly configuration wizard -> step filters + blocker summary + copy-command affordance
```

`0.31-configuration-wizard` 在 v0.30 向导基础上新增 `ui_readability`，包含步骤状态筛选项、阻断摘要、空态说明和命令复制 target；`tools/task_manager.py workspace --include-config-wizard --draft-input-dir <dir>` 会显式生成 `0.31-task-workspace`，在 HTML 中增加向导步骤搜索、状态筛选、阻断筛选、阻断摘要和复制按钮。筛选和复制只在本地静态 HTML 中辅助阅读，不会执行命令、不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号。

0.32 版新增配置审查包索引：

```text
readonly configuration wizard -> offline configuration review package index
```

`tools/task_manager.py workspace --include-config-wizard --draft-input-dir <dir>` 会显式生成 `0.32-task-workspace`，新增 `0.32-configuration-review-package-index`，把配置摘要、配置预览、分享校验、草案/回读、模板索引、配置向导、复跑命令和人工确认项汇成一个只读审查包索引。该索引只列本地文件、入口和复制命令，不执行命令、不应用配置、不写入 `~/.his-harness`、不保存真实 token、不测试远端账号。

0.33 版增强配置审查包可读性：

```text
configuration review package index -> filters + confirmation groups + handoff summary
```

`0.33-configuration-review-package-index` 新增 `ui_readability`，包含文件状态筛选项、待确认分组、未确认必填项统计和交接摘要。`tools/task_manager.py workspace --include-config-wizard --draft-input-dir <dir>` 会显式生成 `0.33-task-workspace`，HTML 中新增审查包文件搜索、文件状态筛选、交接摘要和待确认分组；这些能力只服务离线阅读，不执行命令、不应用配置、不写真实配置目录。

0.34 版新增只读分层配置解析器：

```text
v0.33 Rule Pack/Profile/Experts
-> legacy compatibility adapter
-> explicit team/project/personal/run layers
-> merge policy + hard guards
-> immutable ResolvedConfig + provenance
```

`tools/config_check.py --include-resolved-config` 才会生成 `harness_resolved_config.json/md`。不传该参数时仍输出原 v0.22-v0.33 配置摘要和审查结果，不改变 Harness 运行模式。v0.34 不写 `~/.his-harness`、不应用配置、不读取默认个人覆盖目录、不测试远端账号、不修改业务代码、不执行 Git 或需求平台写入。

```bash
python3 tools/config_check.py \
  --profile-key team-share-example \
  --include-resolved-config \
  --run-override-json '{"orchestration":{"mode":"dynamic_plan"}}' \
  --output-dir /tmp/his_harness_v034_config \
  --json
```

可选的 `--team-config`、`--project-config`、`--personal-config` 文件必须符合 `config/schemas/harness_config_layer.v1.json`；各层只在用户显式传入时读取。

0.35 版新增核心需求闭环试跑：

```text
需求校准确认卡
-> RequirementContract（规则、默认行为、证据、白名单、专项验证）
-> EngineeringHandoff
-> 受控 Git worktree patch
-> 专项验证 + 独立 diff 审查
-> 人工代码审查 / 真实业务验收
```

`core-closure-trial` 专门处理低风险基础需求。它在进入 worktree 前必须同时具备：`ready_for_development` 的需求校准、目标模块工程证据、明确白名单、可执行专项验证命令、可自动验收的规则，以及空值/未传/非法值的默认行为。worktree、专项验证和独立 diff 审查均通过后，默认会把 final diff 自动应用到本地原业务目录；不提交、不推送、不发布、不写云效/TAPD。收费、结算、医保、退费、金额、对账等高风险需求会直接阻断，不会用该模式自动试跑。

v0.38 新增 `auto-local` 作为日常低风险需求的快速入口。它会复用现有的项目定位、白名单和专项验证命令推导，并直接进入上述核心闭环；全部本地闸门通过后自动应用到原业务目录。条件不完整、需求歧义或命中高风险规则时，它会保留结构化证据并阻断，不会退回固定九步骤报告链，也不会猜测业务规则。每次运行都会保存请求模式与实际路线；不会创建分支、提交、推送、合并、发布或写云效/TAPD。

v0.39 新增前后端契约核验门禁：云效正文、评论、附件和用户补充规则都只作为需求证据，不替代源码证据。需求命中入参、接口、请求、排序、返回字段、服务端、后端、BFF 或 API 等跨层关键词时，Harness 会生成 `contract_verification`，同时核验客户端请求和 BFF/服务端/公共 API 源码；任一层缺失时阻断自动 patch，避免把“后端已改”的评论误当成已验证事实。纯样式、纯客户端默认值等不涉及服务契约的局部需求不受该门禁影响。

v0.40 用真实 DFHIS-31551 回放收紧了跨层排序契约：云效评论中的 `sortField`、`sortOrder` 会进入需求校准；源码核验要求接口名和全部明确参数出现在同一段客户端请求构造或服务端方法签名附近，不能因为同一个文件的其他配置出现同名字段就判定支持。当前客户端漏传独立 `sortOrder` 或服务端未接收两个参数时，核心闭环会在 worktree 前快速阻断。

v0.41 补齐需求来源优先级的执行语义：当用户通过 `harness-rules` 明确声明接口参数时，该规则会覆盖云效正文或评论中的冲突参数，契约核验只检查已解析的明确参数，不会把旧评论中的额外参数重新加回。DFHIS-31551 的当前协议为仅传 `sortField`，编码格式为 `字段A|排序方式,字段B|排序方式`。

v0.42 新增人工运行时验收登记：当当前本地源码不包含其他同事已部署的服务端改动、但用户已在真实环境完成验证时，可用 `task_manager.py record-manual-verification` 绑定原 `task_run_id` 或 `run_id`，生成独立 JSON/Markdown 验收证据。它不会篡改原源码门禁结论，也不会打开自动应用、提交、推送或云效/TAPD 写入。

v0.43 新增 `auto-local` 小需求快速路径：仅当调用方明确提供一个本地项目路径、1 至 3 个存在的前端白名单文件，且需求不涉及接口、入参、排序、服务端/API 或医保收费结算等高风险词时，才跳过耗时的全仓工程上下文扫描。需求校准、技术路径存在性、专项验证、受控 worktree、独立 diff 审查和本地应用门禁完全保留；任一条件不满足仍执行既有完整核心闭环。

v0.44 新增 `auto_local_performance_json`：每次 `auto-local` 运行会记录需求证据、需求校准、技术决策、工程扫描、验收矩阵和核心闭环的实际耗时。工程扫描会明确标记为 `skipped` 或 `completed`；未命中快车道时，`fast_local.blockers` 记录回退完整闭环的原因。该产物只用于本地性能追踪，不放宽任何改码或应用门禁。

v0.45 前的临时存储模式：日常 `his_requirement_workflow.py` 和 `tools/self_check.py` 默认使用进程临时 SQLite 与输出目录，命令结束后自动删除运行记录、报告和自测 fixture，不再长期占用 `data/harness.sqlite`、`runs/` 或 `self_check_runs/`。仅当明确传入 `--retain-output` 时，才保留本地任务历史和输出文件，供未来 Task Manager/WebUI 使用。

v0.45 已用 DFHIS-31528 完成真实低风险 `auto-local` 全闭环：Harness 只读读取云效证据，在受控 worktree 中完成页面缓存名最小修复，专项校验与独立 diff 审查后自动应用到本地业务目录；用户页面验收通过后，Task Manager 会登记独立人工运行时验收记录。`task_manager.py register-run` 对核心闭环输出优先复用 `run.json` 内且存在于当前数据库的原始 `run.id`，因此人工验收可追溯到真实改码 run；若显式 `--source-run-id` 与输出 ID 不一致，或输出没有原始 ID，都会直接拒绝。该登记不打开自动提交、远端 Git 或云效/TAPD 写操作。

v0.46 修正核心闭环最终报告的状态口径：`ready_for_manual_review` 代表本地改码、专项验证和独立 diff 审查已完成，报告会提示进入人工代码审查与业务验收；它不代表已提交、已推送、已发布或已写云效。

v0.47 新增可执行验收契约：排序、方案树和右侧列表需保持一致的需求，会要求本地 `ordering_relation` JSON fixture。fixture 固定校验同顺序号按源位置稳定排序、方案树父节点按最早子孙排班键排序、无顺序号保持相对顺序，以及树叶子与右侧排班去重顺序一致。缺少、无效或执行失败的契约会在创建 worktree 前阻断；通过的契约会把其 `verify_command` 强制加入目标业务仓库的专项验证，独立 diff 审查还会检查契约声明的实现证据。该能力不连接 PG、浏览器或外部需求系统，也不替代真实页面验收。

v0.48 新增独立 PostgreSQL 数据证据适配器。普通需求、云效读取、代码分析、worktree 改码和专项验证仍保持零数据库连接；只有用户明确要求“查数据库/查 PG/用数据库验证”，并显式运行 `tools/pg_evidence.py --mode execute` 时，才允许尝试连接已登记的 `test`/`development` 只读 Profile。适配器会从凭证 key 自动发现 Profile，在有限源码文本中寻找 schema/table 证据，候选不唯一时停止为 `needs_evidence`，不会全库盲扫或反复询问用户表名。

v0.49 新增显式 `dynamic-plan` 只读规划器。它用八个可解释维度把需求评为 `simple`、`medium`、`large` 或 `high_risk`，再按需选择产品、架构、前端/后端/数据库开发、独立审查、测试、验收、冲突仲裁和人工闸口角色，并生成可校验的子任务 DAG、路径锁、并行组和版本化交接契约。该能力默认关闭，只有直接运行 `tools/dynamic_plan.py --enable` 才生成规划；不会调用模型、修改代码、连接数据库或执行 Git/云效/TAPD/发布动作。

请求文件示例：

```json
{
  "requirement_id": "DFHIS-EXAMPLE",
  "title": "挂号页面默认值调整",
  "demand_text": "前端单页面默认值与档案管理保持一致。",
  "evidence_refs": ["user:instruction", "code:archive-defaults"],
  "signals": {
    "affected_layers": ["frontend"],
    "repository_count": 1,
    "estimated_file_count": 2,
    "dependency_mode": "none",
    "evidence_status": "complete",
    "verification_mode": "targeted",
    "rollback_mode": "single_patch",
    "allowed_paths": {
      "frontend": ["src/views/register.vue"]
    }
  }
}
```

显式生成只读动态计划：

```bash
python3 tools/dynamic_plan.py \
  --request-file /tmp/dynamic_planning_request.json \
  --output-dir /tmp/his_harness_dynamic_plan \
  --enable
```

产物为 `dynamic_plan.json`、`dynamic_plan.md` 和 `dynamic_plan_audit.json`。实现节点缺少 `allowed_paths` 时返回 `needs_evidence`；命中医保、收费、退费、结算、对账、金额舍入、政策校验、数据库迁移、外部写入或证据冲突时强制返回 `high_risk`，并进入人工确认状态。v0.49 只生成规划，不伪造实现、审查、测试或业务验收结果。

v0.50 将显式启用的 v0.49 计划增量登记进本地 Task Manager。登记会保存父任务、不可变计划快照、子任务、DAG 边、planned 契约占位和审计记录；同一任务下相同计划哈希重复登记时保持幂等。契约新版本会校验节点 schema、角色 producer 和当前上游 artifact，随后只把可达下游标记为 `stale`，不影响无关并行分支。计划存在环依赖，或契约内容包含凭证字段时会拒绝入库。

显式登记并导出只读恢复预览：

```bash
python3 tools/task_manager.py register-dynamic-plan \
  --plan-file /tmp/his_harness_dynamic_plan/dynamic_plan.json \
  --output-dir /tmp/his_harness_dynamic_plan_registry
```

查看已登记计划：

```bash
python3 tools/task_manager.py show-dynamic-plan \
  --plan-id <plan-id> \
  --output-dir /tmp/his_harness_dynamic_plan_registry
```

契约文件格式：

```json
{
  "schema_name": "RequirementContract",
  "schema_version": "1.0",
  "producer": "product_analyst",
  "input_artifact_ids": [],
  "content": {
    "scope": "fixture-only",
    "acceptance": ["contract registry"]
  }
}
```

显式登记契约版本：

```bash
python3 tools/task_manager.py record-dynamic-contract \
  --plan-id <plan-id> \
  --node-id requirement_analysis \
  --contract-file /tmp/requirement_contract.json \
  --output-dir /tmp/his_harness_dynamic_plan_registry
```

三个输出是 `dynamic_plan_registry.json`、`dynamic_plan_registry.md` 和 `dynamic_plan_recovery.json`。`completed_nodes`、`ready_nodes`、`stale_nodes`、`blocked_nodes` 和 `human_gate_nodes` 只表示可恢复位置；v0.50 不执行节点、不调用模型、不创建 worktree、不修改业务代码，也不执行 Git、云效、TAPD 或发布动作。

v0.51 在已登记计划上增加显式、持久化的 dry-run 调度控制面。它会读取 DAG、v0.50 当前契约和角色的 token/时间/重试/并行策略，保存独立 schedule、节点状态、模拟事件、决策审计和 SHA-256 checkpoint。所有状态均属于调度模拟；`succeeded_simulated` 不代表节点真实执行或需求完成。

启动 dry-run：

```bash
python3 tools/task_manager.py start-dynamic-schedule \
  --plan-id <plan-id> \
  --output-dir /tmp/his_harness_dynamic_schedule
```

提交一个模拟结果事件：

```json
{
  "event_id": "requirement-analysis-success-1",
  "node_id": "requirement_analysis",
  "outcome": "success",
  "elapsed_seconds": 12,
  "input_tokens": 800,
  "output_tokens": 300
}
```

```bash
python3 tools/task_manager.py advance-dynamic-schedule \
  --schedule-id <schedule-id> \
  --event-file /tmp/dynamic_schedule_event.json \
  --output-dir /tmp/his_harness_dynamic_schedule
```

失败后进入 `retry_wait` 时，不传事件文件执行一次显式 retry tick：

```bash
python3 tools/task_manager.py advance-dynamic-schedule \
  --schedule-id <schedule-id> \
  --output-dir /tmp/his_harness_dynamic_schedule
```

查看 schedule：

```bash
python3 tools/task_manager.py show-dynamic-schedule \
  --schedule-id <schedule-id> \
  --output-dir /tmp/his_harness_dynamic_schedule
```

产物是 `dynamic_schedule.json`、`dynamic_schedule.md` 和 `dynamic_schedule_checkpoint.json`。失败/超时受角色 `max_retries` 约束，token 或时间超限进入 `blocked_budget`，重试耗尽进入 `blocked_retry_exhausted`，v0.50 契约过期进入 `blocked_stale`，人工闸口只能停在 `paused_human`。重复 event ID 幂等；checkpoint payload、hash 或当前节点状态不一致时拒绝继续推进，不自动恢复或执行真实节点。

v0.52 在 `running_simulated` 节点上增加受控的 fixture-only runtime。先生成绑定 schedule checkpoint、plan hash、角色权限、路径白名单和上游契约引用的不可变 context envelope，再由显式命令解析一个脱敏 fixture JSON。它不会调用模型或任何节点工具；成功结果保存为 `fixture_contract_candidate`，不会登记为 `current` 契约，也不会改变 schedule 状态。

准备节点上下文：

```bash
python3 tools/task_manager.py prepare-dynamic-node-context \
  --schedule-id <schedule-id> \
  --node-id requirement_analysis \
  --requested-tool read_artifacts \
  --output-dir /tmp/his_harness_node_runtime
```

fixture root 必须包含标记文件 `.harness-fixture-root.json`：

```json
{
  "schema_version": "1.0",
  "fixture_only": true
}
```

fixture 输入示例：

```json
{
  "schema_version": "1.0-fixture-node-input",
  "fixture_only": true,
  "context_hash": "sha256:<dynamic_node_context 中的 envelope_hash>",
  "requested_tools": ["read_artifacts"],
  "contract_content": {
    "scope": "脱敏 fixture 示例"
  }
}
```

执行并查看 fixture 结果：

```bash
python3 tools/task_manager.py execute-fixture-node \
  --context-id <context-id> \
  --fixture-root /tmp/his_fixture_root \
  --fixture-file /tmp/his_fixture_root/requirement.json \
  --output-dir /tmp/his_harness_node_runtime

python3 tools/task_manager.py show-fixture-node-execution \
  --execution-id <execution-id> \
  --output-dir /tmp/his_harness_node_runtime
```

当前 fixture executor 只裁决并允许角色已授权的 `read_artifacts`；`worktree_edit`、shell、模型、数据库执行、Git push、部署和外部写入均由全局硬保护拒绝。fixture root 位于 Git 仓库内、文件路径逃逸、权限拒绝、凭证字段、envelope/checkpoint 漂移或 hash 不一致时均阻断。输出为 `dynamic_node_context.*`、`fixture_node_execution.*` 和 `fixture_contract_candidate.json`，全部明确 `business_valid=false`、`promotion_enabled=false`。

v0.53 增加固定进程 executor adapter 和一次性 capability lease。Lease 与 v0.52 context hash、schedule checkpoint、节点、adapter 和 capability 绑定，最长 300 秒且 `max_uses=1`；失败、超时后不会自动补发。adapter 只能调用 Harness 自带的 `tools/fixture_node_worker.py`，不接受任意 command、worker path 或 env，使用 `shell=False`、固定 fixture cwd 和最小 UTF-8 环境。

签发并查看 lease：

```bash
python3 tools/task_manager.py issue-fixture-capability-lease \
  --context-id <context-id> \
  --capability read_artifacts \
  --ttl-seconds 60 \
  --output-dir /tmp/his_harness_executor_runtime

python3 tools/task_manager.py show-fixture-capability-lease \
  --lease-id <lease-id> \
  --output-dir /tmp/his_harness_executor_runtime
```

调用固定 worker：

```bash
python3 tools/task_manager.py execute-sandbox-fixture-node \
  --lease-id <lease-id> \
  --fixture-root /tmp/his_fixture_root \
  --fixture-file /tmp/his_fixture_root/requirement.json \
  --timeout-seconds 2 \
  --output-dir /tmp/his_harness_executor_runtime
```

worker 使用版本化 stdin/stdout JSON 协议，stdout 非 JSON、schema 错误、非零退出、超过 5 秒硬上限、角色 token/时间预算超限都会形成结构化阻断；stderr 原文和父进程环境不会写入数据库或产物。成功只生成 `sandbox_fixture_contract_candidate`，业务契约仍保持 `planned`，schedule 节点仍保持 `running_simulated`。

v0.54 在现有 dry-run scheduler、context envelope 和一次性 lease 之上增加 deterministic mock-agent 编排。它会自动执行当前 `running_simulated` wave，同 wave 节点按 `--max-parallel` 并行调用固定 fixture worker；全部节点结束后才写入显式模拟事件并推进 checkpoint。后续节点只接收上游候选契约的 artifact id/schema/hash，不读取真实业务文件。

先准备独立的非 Git fixture 目录及 marker：

```bash
mkdir -p /tmp/his_mock_agent_fixtures
printf '{"schema_version":"1.0","fixture_only":true}\n' \
  > /tmp/his_mock_agent_fixtures/.harness-fixture-root.json
```

执行并查看一个 deterministic mock-agent fixture schedule：

```bash
python3 tools/task_manager.py run-mock-agent-fixture-schedule \
  --schedule-id <schedule-id> \
  --fixture-root /tmp/his_mock_agent_fixtures \
  --max-parallel 2 \
  --output-dir /tmp/his_harness_mock_agent_runtime

python3 tools/task_manager.py show-mock-agent-fixture-run \
  --run-id <run-id> \
  --output-dir /tmp/his_harness_mock_agent_runtime
```

产物包含 `mock_agent_fixture_run.json/md` 和 `mock_agent_fixture_traces.json`。每个 trace 绑定 wave、context、lease、execution、usage、耗时、候选 hash 和 observed concurrency；失败保留同 wave 其他节点证据，但不自动补发 lease 或重试。成功只把 dry-run 节点推进到 `succeeded_simulated`，registry contract 仍保持 `planned`，所有结论固定 `fixture_only=true`、`business_valid=false`、`promotion_enabled=false`。

v0.55 增加 provider-neutral 离线模型调用边界。它从 v0.52 不可变 context 生成绑定 checkpoint、角色、上游 artifact、输出契约和预算的结构化请求，只允许 `mock`/`replay`。响应必须通过 provider-neutral envelope、token 用量、输出契约、producer、evidence refs、凭证字段和 fixture 边界校验；成功只保存 `fixture_model_candidate`，不会推进 schedule 或晋升 current contract。

录制 deterministic mock cassette：

```bash
python3 tools/task_manager.py run-model-fixture-node \
  --schedule-id <schedule-id> \
  --node-id requirement_analysis \
  --fixture-root /tmp/his_mock_agent_fixtures \
  --mode mock \
  --record-cassette \
  --output-dir /tmp/his_harness_model_invocation_runtime
```

使用命令输出中的 cassette 相对路径进行回放，并查看审计记录：

```bash
python3 tools/task_manager.py run-model-fixture-node \
  --schedule-id <schedule-id> \
  --node-id requirement_analysis \
  --fixture-root /tmp/his_mock_agent_fixtures \
  --mode replay \
  --cassette-file /tmp/his_mock_agent_fixtures/model-cassettes/<request-hash>.json \
  --output-dir /tmp/his_harness_model_invocation_runtime

python3 tools/task_manager.py show-model-fixture-invocation \
  --invocation-id <invocation-id> \
  --output-dir /tmp/his_harness_model_invocation_runtime
```

产物包含 `model_fixture_invocation.json/md` 和 `model_fixture_events.json`。v0.55 的离线入口不调用 `get_llm_client()`，不读取本地 credentials 文件，也没有真实 provider 或网络分支；`openai`、`anthropic`、`real` 和未知模式会在准备调用前拒绝。

v0.56 把 v0.55 离线模型适配器接入多波次动态 DAG。每个节点可以按 adapter policy 选择 `mock` 或 `replay`；同 wave 在 `--max-parallel` 上限内并行，整波完成后才推进模拟 checkpoint。下游 context 只接收同一 schedule 中成功模型候选的 artifact id/schema/hash，不能读取其他历史 schedule 的候选。

默认 mock 运行并录制每个节点的 cassette：

```bash
python3 tools/task_manager.py run-model-fixture-schedule \
  --schedule-id <schedule-id> \
  --fixture-root /tmp/his_mock_agent_fixtures \
  --max-parallel 2 \
  --record-cassettes \
  --output-dir /tmp/his_harness_model_dag_runtime

python3 tools/task_manager.py show-model-fixture-schedule-run \
  --run-id <run-id> \
  --output-dir /tmp/his_harness_model_dag_runtime
```

节点级 policy 可使用设计文档中的 `1.0-offline-model-dag-adapters` JSON，并通过 `--adapter-file` 显式传入。policy 只接受已登记节点、`mock/replay`、boolean 录制开关和 fixture root 内相对 cassette 路径；`--adapter-file` 与 `--record-cassettes` 不能同时使用。产物为 `model_fixture_dag_run.json/md` 和 `model_fixture_dag_traces.json`，trace 记录 wave、context、invocation、mode/provider/model、usage、耗时、候选 hash、cassette 和并发观测。失败保留同 wave 证据但不自动重试。

模型 smoke 只能在 Manager 中先配置 typed Profile 和加密 API key，再创建并确认 `model.single_node.smoke` 计划后执行。请求固定为 `SMOKE_OK`，无 tool、file、callback 或用户 prompt；endpoint host/model/timeout/max tokens 全由 allowlisted Profile 决定。每次授权最多一次 dispatch、无重试，且 redirect 与环境代理均 fail-closed。审计只保留 profile alias、endpoint host、model alias、request/response hash、usage、时长和 marker，不记录 API key、Authorization header、原始 payload 或 response。

`tools/task_manager.py run-model-provider-smoke` 仅保留旧脚本兼容入口，始终返回 `legacy_model_provider_smoke_disabled`（exit 2）。即使传入完整 legacy flags、credentials 文件和 authorization ID，它也不会读取文件、构造 runtime 或发起网络请求。

历史 credentials-file 格式不再是 Manager 模型执行入口；模型 API key 仅由 Manager 加密存储并在已消费的执行上下文中短暂解析。以下为其他只读数据库 Profile 的示例：

```text
pg_<profile>_readonly_dsn
pg_<profile>_readonly_user
pg_<profile>_readonly_password
```

不含连接信息的策略示例位于 `config/pg_evidence_profiles.example.json`。请求文件示例：

```json
{
  "subject": "核对门诊挂号测试数据",
  "keywords": ["挂号", "科室", "顺序号"],
  "sql": "SELECT keshiid, shunxuhao FROM df_jj_menzhen.mz_guahaob WHERE riqi = %(registration_date)s",
  "parameters": {
    "registration_date": "2026-07-15"
  }
}
```

先生成零连接计划：

```bash
python3 tools/pg_evidence.py \
  --request-file /tmp/pg_evidence_request.json \
  --profile-policy config/pg_evidence_profiles.example.json \
  --credentials-file /path/to/credentials.json \
  --mode plan \
  --project-root /absolute/path/to/backend-project \
  --output-dir /tmp/his_harness_pg_plan
```

只有用户明确要求查询数据时，才把 `--mode plan` 改成 `--mode execute`。执行前必须同时通过 Profile 环境、凭证完整性、唯一候选和只读 SQL 守卫；连接 5 秒、单查询 10 秒、总预算 45 秒、元数据查询最多 3 次、结果最多 50 行、失败不重试。执行产物为 `pg_evidence_plan.json/md`、`pg_evidence_result.json/md` 和 `pg_evidence_audit.json`，不包含完整 SQL、DSN、账号、密码、参数原值或未脱敏敏感字段。真实连接需要本机可选安装 `psycopg`；驱动缺失会快速返回 `blocked`，不会回退到其他连接方式。

v0.36 已用 DFHIS-31557 在真实 `df-web-bui` 源码基线上完成一次 review-only 回放：云效只读证据、参数默认值规则、受控 worktree patch、专项静态校验、`node --check` 和独立 diff 审查均已通过，原业务目录未改动。该结果证明本地研发证据链，不替代页面登录态、参数配置、接口返回和人工业务验收。

以 `DFHIS-31465` 的 `paiBanMs` 为例，契约会固定三条规则：`1` 仅保留医生为空的排班、`2` 仅保留有医生的排班、空/不传/其他值保持原默认模式。独立 diff 审查还会检查这三条规则的实现信号、默认模式保护、白名单和专项验证结果。

真实项目复跑必须由调用者给出已确认的项目路径、白名单和项目内可执行的专项验证命令；下面仅是参数形状示例，`<专项命令>` 不能照抄为业务验证结论：

```bash
python3 harnesses/his_requirement_workflow.py \
  --title "DFHIS-31465 核心闭环试跑" \
  --demand "菜单/路由参数 paiBanMs：1 只过滤医生为空的排班；2 只过滤有医生的排班；空、不传或其他值保持当前默认模式。" \
  --mode openai \
  --execution-mode core-closure-trial \
  --project-path /absolute/path/to/df-web-guahaosf \
  --allowed-path src/pages/yeWuGn/guaHaoSf/index.vue \
  --allowed-path src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js \
  --verify-command '<项目内已确认可执行的专项命令>' \
  --worktree-dir /tmp/his_harness_worktrees \
  --output-dir /tmp/his_harness_core_closure
```

排序/方案树关联需求在核心闭环中必须显式传入脱敏契约文件，例如：

```bash
python3 harnesses/his_requirement_workflow.py \
  --title "DFHIS-31558 排班和科室树排序" \
  --demand "科室树和右侧排班按顺序号排序并保持一致。" \
  --mode openai \
  --execution-mode core-closure-trial \
  --project-path /absolute/path/to/df-web-guahaosf \
  --acceptance-contract-file fixtures/acceptance_contracts/dfhis-31558-ordering.json \
  --output-dir /tmp/his_harness_ordering_contract
```

日常低风险需求可改用 `auto-local`。不传白名单或专项命令时，Harness 会优先使用工程决策的推荐结果并执行完整核心闭环；若希望启用 v0.43 快速路径，则需显式传入一个项目路径和 1 至 3 个前端白名单文件。两种路径都必须通过同一套专项验证和独立 diff 审查，缺少可靠证据则阻断：

```bash
python3 harnesses/his_requirement_workflow.py \
  --title "DFHIS-31465" \
  --demand "菜单/路由参数 paiBanMs：1 只过滤医生为空的排班；2 只过滤有医生的排班；空、不传或其他值保持当前默认模式。" \
  --mode openai \
  --execution-mode auto-local \
  --project-path /absolute/path/to/df-web-guahaosf \
  --allowed-path src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js \
  --output-dir /tmp/his_harness_auto_local
```

输出包含 `core_requirement_contract`、`core_engineering_handoff`、`core_diff_review`（进入 worktree 后）和 `core_closure` 的 JSON/Markdown 产物。默认会把已验证的 diff 合入本地项目；传 `--review-only` 可显式保留 review-only 行为。两种模式都不提交、不推送、不发布、不写云效/TAPD。fixture 或 mock 试跑只能证明 Harness 工程链路，不能替代真实页面、登录态和业务数据验收。

## 当前能力

- 手工输入 HIS 需求描述。
- 自动按 9 步专家团 Workflow 执行。
- 每一步保存输入、输出、状态、耗时、tokens、attempt。
- 独立 Evaluator 审核阶段完整性、报告结构、高风险 HIS 逻辑、测试验收口径。
- 不合格时从问题步骤开始自动返工，默认最多 2 轮。
- 输出 Markdown/JSON/每步专家报告。
- 当前默认使用本地 mock/fixture/replay 完成工程链验证；真实模型入口已冻结。
- 可选接入真实项目路径，生成只读工程证据包、影响范围、风险等级、建议验证命令和人工确认项。
- 扫描器会从需求标题提取业务定位词，并展开常见前端命名别名，例如“优惠项目/不限时”会联想到 `youHui`、`youHuiLb`、有效期/生效/失效时间等线索。
- 优惠、减免、费用配置、核算类需求至少按中风险处理；叠加收费、结算、医保、报表、对账或核算上下文时升为高风险或关键风险。
- Evaluator 会检查专家报告是否引用工程证据；没有证据时不能给出确定代码文件结论。
- 已加入云效事务防护基座：实体建模、动作分级、权限策略、幂等键和审计表。当前默认不直接写云效事务。
- v0.7 可以在独立 Git worktree 中生成、校验、应用 unified diff，并运行用户显式传入的验证命令。
- v0.7 worktree 模式不修改原业务目录；v0.8.8 开始只有临时 worktree 验证成功且原目录干净时，才把 `final.diff` 合入原业务目录。两者都不提交、不推送、不发布、不写云效事务。
- v0.7.1 可以审查已提交 diff，把 `git diff --stat/name-only/diff` 注入工程证据包，并在独立 worktree 中运行 `git diff --check` 和显式验证命令。
- v0.7.2 的 `review-worktree` 会为 `review_base` 和 `review_commit` 各创建一个临时 detached worktree，同一条验证命令两边都跑，避免历史 lint 基线误判当前提交失败。
- v0.7.3 会记录验证命令执行前后的 `git status` 和 `git diff`，明确报告验证命令是否污染临时 worktree。
- v0.7.4 支持 `--yunxiao-read --yunxiao-url` 作为只读证据入口，并输出 `yunxiao_evidence.json`、`clarification_gate.json`、`patch_readiness.md`。
- v0.7.4 的 worktree patch 前置闸口会检查“不限时”含义、复现/期望/实际/附件证据、工程候选根因、白名单路径和显式验证命令；不足则阻断 patch。
- v0.8.0 支持云效事务 dry-run，输出 `yunxiao_transaction_plan.json`、`yunxiao_transaction_plan.md`，并在本地审计表中记录 dry-run 事件。
- v0.8.0 的 dry-run 报告会明确区分：建议动作、策略是否允许、阻断原因、真实写入状态未执行。
- v0.8.1 的 dry-run 计划覆盖评论、截图/附件、迭代调整、负责人调整、状态流转、服务变更和产物关联。
- v0.8.1 仍不读取 `aliyun_devops_write_pat`，不会因为令牌有写权限而真实写云效。
- v0.8.2 支持 `write` 模式和 `fake|real` transport；fake 只验证写入链路，real 才会调用云效 OpenAPI。
- v0.8.2 的真实写入仍必须双开关确认，并按策略字段映射执行；缺少字段映射时动作会被阻断。
- v0.8.3 支持本机凭证文件 `/Users/lym/WorkCode/ai/apiKey/credentials.json`，凭证读取顺序统一为环境变量、本机凭证文件、Keychain。
- v0.8.3 提供 `tools/yunxiao_read_check.py`，用于凭证检查和 DFHIS-31226/DFHIS-31216 云效只读 smoke。
- v0.8.4 会清洗云效 HTML 正文，结构化提取内联图片/文件 `fileIdentifier`，并在只读 smoke 输出下载摘要。
- v0.8.4 的主 Workflow 会优先把清洗后的云效正文和内联证据注入专家团，内联文件只读下载到 `--output-dir/_yunxiao_evidence`，避免 HTML/JSON 噪声污染模型输入。
- v0.8.7 会生成 `acceptance_matrix.json/md`，把需求验收、自动验证、人工验收、阻断项和反驳/纠偏建议结构化。
- v0.8.7 支持重复传入 `--project-path`，用于生成多个业务项目的项目类型和推荐验证命令；推荐命令默认不执行，只有 `--verify-command` 显式传入才运行。
- v0.8.7 会拦截“跳过测试直接流转”“自动关闭高风险任务”“无证据推断医保/收费/结算规则”等不合理指令，并输出不建议、原因和替代方案。
- v0.8.8 会输出 `technical_decision.json/md`、`project_selection.md`、`field_provenance.md`、`implementation_decision.md`。
- v0.8.8 在未传 `--project-path` 时会按 `--project-root` 自动选择 HIS 项目，默认项目根为 `/Users/lym/Desktop/dongFang/dfcode`。
- v0.8.8 的 `worktree` 成功后会把 `final.diff` 合入原业务目录；失败或成功都会清理临时 worktree 并执行 `git worktree prune`。
- v0.8.9 支持 `--execution-mode fullstack-worktree`，当前用于 DFHIS-31270 的前端展示受控修复；字段来源已由实际返回证明时只输出前端 `final_*.diff`，字段来源不足时阻断。
- v0.9.1 支持 `--execution-mode precommit-verify` 和 `tools/precommit_verify.py`，会输出 `verification_matrix.json/md`、`code_review.md`、`commit_ready_summary.md` 和 `precommit_manifest.json`。
- v0.9.5 支持 `--execution-mode single-demand-trial`，会输出 `single_demand_trial.json/md`、`verification_matrix.json/md`、`code_review.md`、`commit_ready_summary.md`，并继续冻结真实云效状态流转。
- v0.10 支持 `tools/task_manager.py create/list/show/run`，用于把单次脚本运行沉淀成任务记录。后续 UI 应优先读取 `harness_tasks` 和 `harness_task_runs`，而不是直接解析零散 `/tmp` 产物。
- v0.10.1 支持 `tools/behavior_check.py` 和 precommit 内置行为验收。对于提示框、关闭、loading、进度条、收费结算异常路径改动，Harness 会输出 `behavior_acceptance.json/md`，并把明显危险模式作为提交前阻断项。
- v0.10.2 支持 `tools/interaction_evidence_check.py` 和 precommit 内置交互证据门禁。交互敏感 diff 需要方法级用例结果覆盖 alert/confirm resolve、close/cancel、重复提示、结算收尾路径后，才允许进入提交准备；UI 证据 manifest 用于后续云效交付评论放权。
- v0.10.3A 支持 `--method-test-command`，可在 precommit 临时 worktree 中执行用户显式方法级测试命令，并把 stdout JSON cases 自动转成方法级交互证据。
- v0.10.3B 支持 `--ui-capture-command`，可在 precommit 临时 worktree 中执行用户显式 UI 采集命令，并把生成的截图/视频/GIF/人工记录自动加入 UI 证据 manifest。
- v0.10.3C 支持 `tools/ui_capture_template.py`，可生成 Playwright/Chrome capture 脚本、环境变量示例和人工验收记录模板，用于沉淀 HIS 登录态和页面采集规范。
- v0.10.4 支持白名单内未跟踪新增文件的 precommit 复现；支持将同仓库白名单外 dirty scope 降级为提交/云效评论限制；已用 DFHIS-31465 生成真实样板产物 `/tmp/his_harness_DFHIS-31465_v0104_trial`。
- v0.10.5 支持 `tools/task_manager.py register-run`，可把已有 Harness 产物目录登记为 Task Manager run，生成 `task_id/run_id/output_dir` 索引和 `task_manager_real_trial_record.json/md`。
- v0.10.6 支持 `tools/task_manager.py rerun-precommit`，可从任务记录或显式参数复跑提交前验证，并自动登记新的 task_run/output_dir。
- v0.10.7 支持同 task、同 output_dir、同 execution_mode 的 `register-run` 幂等登记，并生成 `task_manager_run_history.json/md` 方便比较历史 run。
- v0.10.8 支持 `ui_evidence_reuse_policy.json/md`，把 UI 证据复用条件、人工验收边界和 Playwright/Chrome 登录态风险固定到产物中。
- v0.10.9 支持 `tools/task_manager.py dashboard`，导出只读 `task_dashboard.json/md/html`，展示任务、run 历史、产物索引、验证状态和 UI 证据状态。
- v0.10.10 支持 dashboard 筛选和真实样板集导出，可按 DFHIS 编号、验证状态、UI 证据状态、可提交状态筛选，并输出 `task_sample_set.json/md`。
- v0.11 支持 `tools/task_manager.py workbench`，导出单任务只读工作台 `task_workbench.json/md`，展示任务详情、run 详情、产物路径和可复制复跑命令。
- v0.12 支持 `tools/task_manager.py workspace`，导出只读本地 HTML 工作台入口 `task_workspace.html`，并串联 dashboard、sample set 和各任务 workbench。
- v0.13 支持历史 run 对比和过期证据 warning，workbench 展示 latest/previous run 差异，workspace 汇总 warning 数量和 code。
- v0.13.1 修复 precommit 大 diff 被日志截断后用于 `git apply --check` 的问题；当前 diff 读取保留完整 patch，报告日志仍可截断。
- v0.14 支持只读 HTML 工作台 warning 汇总、warning code/DFHIS/验证状态/UI 证据状态筛选和关键词搜索；所有筛选均在静态页面本地完成，不触发复跑或云效写入。
- v0.15 支持 `requirement_calibration.json/md`，在技术自治和验收矩阵前先确认来源优先级、用户补充规则、字段/参数和值域；低置信度或高风险复杂需求不自动进入改码。
- v0.16 支持 Task Manager 索引 `requirement_calibration.json/md`，单任务 workbench 展示确认卡摘要，workspace 静态 HTML 展示确认卡列、复制确认卡文件，并支持确认卡状态筛选和参数关键词搜索。
- v0.17A 支持 Task Manager 修改历史账本和回滚 dry-run 计划，能登记多次 diff、显示修改次数、导出 `task_change_history.json/md`，并生成只读回滚检查命令。
- v0.17B 支持只读本地 WebUI 任务详情 tabs，集中查看概览、Run 历史、确认卡、修改历史、回滚 dry-run、证据预览和可复制命令。
- v0.18 支持只读 workspace 历史快照对比和导出索引，能比较两次导出的任务、run、warning、修改次数和确认卡状态变化。
- v0.19 支持只读 workspace 多快照浏览、任意两快照摘要对比和证据状态趋势，能查看 UI 证据、warning、验证状态、确认卡状态和修改次数变化。
- v0.20 支持只读 WebUI 顶部导航、概览/任务/快照/趋势/导出索引分区、历史快照详情和可展开证据预览。
- v0.21 支持只读 WebUI 空态/错误态说明、状态标签、大表滚动容器和离线审查包 `task_workspace_offline_review.json/md`。
- v0.22 支持 Rule Pack、Profile、Credential Store 只读配置摘要、`tools/config_check.py` 和显式 workspace 配置中心页；默认旧命令行为保持不变。
- v0.23 支持 Yunxiao/TAPD/manual/file 本地 payload 归一化为 `requirement_evidence.json/md`；默认旧 workflow、Task Manager 和云效读取命令不变。
- v0.24 支持通过显式 `--requirement-evidence-file` 把本地需求来源证据接入主 workflow、Task Manager 和只读 WebUI；默认不传时不生成新证据产物。
- v0.25 支持显式配置预览、Provider 模板草案和规则预览；默认旧 workspace 和旧 workflow 行为保持不变。
- v0.26 支持显式配置分享校验和本地覆盖策略预览；默认旧 workspace 和旧 workflow 行为保持不变。
- v0.27 支持在用户选择目录生成 secret-free 配置导入草案文件，并在只读 workspace 展示人工导入步骤；默认旧 workspace 和旧 workflow 行为保持不变。
- v0.28 支持回读用户选择目录里的配置导入草案，生成只读表单预览、导入前风险提示和人工确认项；默认旧 workspace 和旧 workflow 行为保持不变。
- v0.29 支持索引一个或两个配置草案目录，生成多 Profile 切换预览、配置差异对比和团队模板文件索引；默认旧 workspace 和旧 workflow 行为保持不变。

行为验收单独检查示例：

```bash
python3 tools/behavior_check.py \
  --project-path /Users/lym/Desktop/dongFang/dfcode/df-web-yewugymk \
  --allowed-path src/components/shouFeiJs/components/Dialog.vue \
  --entity-id DFHIS-31446 \
  --title "DFHIS-31446 三方支付超时提示关闭后结算进度详情未关闭" \
  --demand-text "点三方支付失败提示右上角关闭后，应继续自动退费并关闭结算进度详情，不得再出现空提示或重复提示。" \
  --output-dir /tmp/his_harness_DFHIS-31446_behavior
```

提交前验证会自动生成行为验收产物：

```bash
python3 tools/precommit_verify.py \
  --project-root /Users/lym/Desktop/dongFang/dfcode \
  --project-path /Users/lym/Desktop/dongFang/dfcode/df-web-yewugymk \
  --allowed-path src/components/shouFeiJs/components/Dialog.vue \
  --verify-command './node_modules/.bin/vue-cli-service lint --no-fix src/components/shouFeiJs/components/Dialog.vue' \
  --entity-id DFHIS-31446 \
  --title "DFHIS-31446 三方支付超时提示关闭后结算进度详情未关闭" \
  --demand-text "点三方支付失败提示右上角关闭后，应继续自动退费并关闭结算进度详情，不得再出现空提示或重复提示。" \
  --output-dir /tmp/his_harness_DFHIS-31446_precommit
```

方法级交互测试和 UI 证据单独检查示例：

```bash
python3 tools/interaction_evidence_check.py \
  --project-path /Users/lym/Desktop/dongFang/dfcode/df-web-yewugymk \
  --allowed-path src/components/shouFeiJs/components/Dialog.vue \
  --entity-id DFHIS-31446 \
  --title "DFHIS-31446 三方支付超时提示关闭后结算进度详情未关闭" \
  --demand-text "点三方支付失败提示右上角关闭后，应继续自动退费并关闭结算进度详情，不得再出现空提示或重复提示。" \
  --method-evidence-file /tmp/dfhis_31446_method_evidence.json \
  --ui-evidence-path /tmp/dfhis_31446_progress_closed.png \
  --output-dir /tmp/his_harness_DFHIS-31446_interaction
```

`precommit_verify.py` 也支持同样的证据参数：

```bash
python3 tools/precommit_verify.py \
  --project-root /Users/lym/Desktop/dongFang/dfcode \
  --project-path /Users/lym/Desktop/dongFang/dfcode/df-web-yewugymk \
  --allowed-path src/components/shouFeiJs/components/Dialog.vue \
  --verify-command './node_modules/.bin/vue-cli-service lint --no-fix src/components/shouFeiJs/components/Dialog.vue' \
  --entity-id DFHIS-31446 \
  --title "DFHIS-31446 三方支付超时提示关闭后结算进度详情未关闭" \
  --demand-text "点三方支付失败提示右上角关闭后，应继续自动退费并关闭结算进度详情，不得再出现空提示或重复提示。" \
  --method-evidence-file /tmp/dfhis_31446_method_evidence.json \
  --ui-evidence-path /tmp/dfhis_31446_progress_closed.png \
  --output-dir /tmp/his_harness_DFHIS-31446_precommit
```

`precommit_verify.py` 也可以直接执行方法级测试命令生成证据：

```bash
python3 tools/precommit_verify.py \
  --project-root /Users/lym/Desktop/dongFang/dfcode \
  --project-path /Users/lym/Desktop/dongFang/dfcode/df-web-yewugymk \
  --allowed-path src/components/shouFeiJs/components/Dialog.vue \
  --verify-command './node_modules/.bin/vue-cli-service lint --no-fix src/components/shouFeiJs/components/Dialog.vue' \
  --entity-id DFHIS-31446 \
  --title "DFHIS-31446 三方支付超时提示关闭后结算进度详情未关闭" \
  --demand-text "点三方支付失败提示右上角关闭后，应继续自动退费并关闭结算进度详情，不得再出现空提示或重复提示。" \
  --method-test-command 'node tests/dfhis-31446.method-test.mjs' \
  --ui-evidence-path /tmp/dfhis_31446_progress_closed.png \
  --output-dir /tmp/his_harness_DFHIS-31446_precommit
```

`precommit_verify.py` 也可以直接执行 UI 证据采集命令生成截图或状态记录：

```bash
python3 tools/precommit_verify.py \
  --project-root /Users/lym/Desktop/dongFang/dfcode \
  --project-path /Users/lym/Desktop/dongFang/dfcode/df-web-yewugymk \
  --allowed-path src/components/shouFeiJs/components/Dialog.vue \
  --verify-command './node_modules/.bin/vue-cli-service lint --no-fix src/components/shouFeiJs/components/Dialog.vue' \
  --entity-id DFHIS-31446 \
  --title "DFHIS-31446 三方支付超时提示关闭后结算进度详情未关闭" \
  --demand-text "点三方支付失败提示右上角关闭后，应继续自动退费并关闭结算进度详情，不得再出现空提示或重复提示。" \
  --method-test-command 'node tests/dfhis-31446.method-test.mjs' \
  --ui-capture-command 'node tests/dfhis-31446.playwright-capture.mjs' \
  --output-dir /tmp/his_harness_DFHIS-31446_precommit
```

生成 Playwright/Chrome UI 采集模板示例：

```bash
python3 tools/ui_capture_template.py \
  --output-dir /tmp/his_harness_DFHIS-31446_ui_template \
  --entity-id DFHIS-31446 \
  --title "DFHIS-31446 三方支付超时提示关闭后结算进度详情未关闭" \
  --route /menzhen/shoufei \
  --scenario-name "三方支付提示关闭后进度详情关闭"
```

生成后按 `playwright_capture.env.example` 设置 `HIS_UI_BASE_URL`、`HIS_UI_ROUTE`、`HIS_UI_STORAGE_STATE` 等变量，再把 `node /tmp/his_harness_DFHIS-31446_ui_template/playwright_capture.mjs` 作为 `--ui-capture-command` 传给 precommit。

方法级证据文件最小结构：

```json
{
  "cases": [
    {"id": "METHOD-ALERT-RESOLVE", "status": "pass", "evidence": "点击确定后继续关闭结算进度详情。"},
    {"id": "METHOD-ALERT-CLOSE", "status": "pass", "evidence": "close/cancel reject 不进入外层业务失败 catch。"},
    {"id": "METHOD-NO-REPEATED-ALERT", "status": "pass", "evidence": "未出现空提示、重复提示或泛化失败文案。"},
    {"id": "METHOD-SETTLEMENT-CLEANUP", "status": "pass", "evidence": "closeSettlementProgress/loading/return 收尾顺序保持。"}
  ]
}
```

Task Manager 创建任务示例：

```bash
python3 tools/task_manager.py create \
  --yunxiao-url "https://devops.aliyun.com/projex/bug/DFHIS-31305" \
  --title "DFHIS-31305 住院收费费用性质默认商业保险顺序调整无效" \
  --project-root /Users/lym/Desktop/dongFang/dfcode \
  --project-path /Users/lym/Desktop/dongFang/dfcode/df-web-zhuyuansf
```

Task Manager 只读运行示例：

```bash
HARNESS_CREDENTIALS_FILE=/Users/lym/WorkCode/ai/apiKey/credentials.json \
python3 tools/task_manager.py run \
  --yunxiao-url "https://devops.aliyun.com/projex/bug/DFHIS-31305" \
  --title "DFHIS-31305 住院收费费用性质默认商业保险顺序调整无效" \
  --mode openai \
  --execution-mode readonly \
  --project-root /Users/lym/Desktop/dongFang/dfcode
```

Task Manager 查看任务示例：

```bash
python3 tools/task_manager.py list
python3 tools/task_manager.py show --task-key bug-dfhis-31305
```

Task Manager 登记已有 precommit 产物示例：

```bash
python3 tools/task_manager.py register-run \
  --yunxiao-url "https://devops.aliyun.com/projex/req/DFHIS-31465" \
  --title "【运城口腔】挂号窗口新增'科室'过滤条件" \
  --entity-kind requirement \
  --entity-id DFHIS-31465 \
  --project-root /Users/lym/Desktop/dongFang/dfcode \
  --project-path /Users/lym/Desktop/dongFang/dfcode/df-web-guahaosf \
  --output-dir /tmp/his_harness_DFHIS-31465_v0104_trial \
  --execution-mode precommit-verify \
  --notes "v0.10.5 register v0.10.4 real precommit trial"
```

登记后可用以下命令复查索引：

```bash
python3 tools/task_manager.py show --task-key requirement-dfhis-31465
```

Task Manager 复跑 precommit 示例：

```bash
python3 tools/task_manager.py rerun-precommit \
  --task-key requirement-dfhis-31465 \
  --project-root /Users/lym/Desktop/dongFang/dfcode \
  --project-path /Users/lym/Desktop/dongFang/dfcode/df-web-guahaosf \
  --allowed-path src/pages/yeWuGn/guaHaoSf/index.vue \
  --allowed-path src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js \
  --verify-command './node_modules/.bin/vue-cli-service lint --no-fix src/pages/yeWuGn/guaHaoSf/index.vue src/pages/yeWuGn/guaHaoSf/js/paiBanDoctorFilter.js' \
  --demand "菜单路由参数 paiBanMs：1 只过滤医生为空的排班；2 只过滤有医生的排班；空、不传或其他值保持当前默认模式。" \
  --output-root /tmp/his_harness_tasks \
  --worktree-dir /tmp/his_harness_task_worktrees
```

复跑产物会自动登记回同一个任务，并生成：

- `task_manager_real_trial_record.json/md`
- `task_manager_run_history.json/md`
- `ui_evidence_reuse_policy.json/md`

Task Manager 只读看板导出示例：

```bash
python3 tools/task_manager.py dashboard \
  --limit 50 \
  --output-dir /tmp/his_harness_task_dashboard
```

Task Manager 按真实样板筛选导出示例：

```bash
python3 tools/task_manager.py dashboard \
  --entity-id DFHIS-31465 \
  --verification-status passed \
  --ui-evidence-status present \
  --sample-only \
  --output-dir /tmp/his_harness_task_dashboard_filtered
```

导出产物：

- `task_dashboard.json`
- `task_dashboard.md`
- `task_dashboard.html`
- `task_sample_set.json`
- `task_sample_set.md`

Task Manager 单任务工作台导出示例：

```bash
python3 tools/task_manager.py workbench \
  --task-key requirement-dfhis-31465 \
  --output-dir /tmp/his_harness_task_workbench_DFHIS-31465
```

导出产物：

- `task_workbench.json`
- `task_workbench.md`

v0.13 起，单任务工作台会额外包含：

- `run_history_comparison`
- `evidence_warnings`

v0.14 起，工作台入口会额外包含：

- 顶层 `warning_summary`
- 顶层 `filter_options`
- entry 级 `filter_data`
- entry 级 `search_text`
- 静态 HTML 搜索框和只读筛选控件

v0.15 起，主 Workflow 会额外导出：

- `requirement_calibration.json`
- `requirement_calibration.md`

该确认卡会先进入报告和专家团上下文，用于记录用户补充规则、云效证据优先级、字段/参数和值域、复杂需求拆分和必须确认项。

v0.16 起，Task Manager 会索引确认卡：

- 单任务 `task_workbench.json/md` 会包含 `requirement_calibration` 摘要。
- workspace 会复制每个任务的 `workbenches/<task_key>/requirement_calibration.json`。
- workspace 会复制每个任务的 `workbenches/<task_key>/requirement_calibration.md`。
- `task_workspace.html` 会展示确认卡列，并支持确认卡状态筛选和参数/摘要关键词搜索。

v0.17A 起，Task Manager 会记录修改历史：

```bash
python3 tools/task_manager.py record-change \
  --task-key requirement-dfhis-31465 \
  --project-path /Users/lym/Desktop/dongFang/dfcode/df-web-guahaosf \
  --allowed-path src/views/guahao/GuaHao.vue \
  --diff-path /tmp/his_harness_DFHIS-31465_precommit/final.diff \
  --verification-status passed \
  --notes "按菜单参数 paiBanMs 控制排班过滤模式"
```

生成回滚 dry-run 计划示例：

```bash
python3 tools/task_manager.py rollback-plan \
  --task-key requirement-dfhis-31465 \
  --target-change-sequence 1 \
  --output-dir /tmp/his_harness_DFHIS-31465_rollback_plan
```

`rollback-plan` 只输出 `rollback_plan.json/md`、`change_<n>_reverse.patch` 和人工可复制命令；Harness 不会自动执行这些命令。

Task Manager 只读本地 HTML 工作台入口导出示例：

```bash
python3 tools/task_manager.py workspace \
  --limit 50 \
  --output-dir /tmp/his_harness_task_workspace
```

显式展示配置中心摘要：

```bash
python3 tools/task_manager.py workspace \
  --limit 50 \
  --include-config-summary \
  --profile-key dfhis-local-example \
  --output-dir /tmp/his_harness_task_workspace_configured
```

单独检查 Rule Pack、Profile 和凭证状态：

```bash
python3 tools/config_check.py \
  --profile-key dfhis-local-example \
  --output-dir /tmp/his_harness_config_check
```

单独导出只读配置预览和 Provider 模板草案：

```bash
python3 tools/config_check.py \
  --profile-key dfhis-local-example \
  --include-preview \
  --output-dir /tmp/his_harness_config_preview
```

显式展示配置中心摘要和配置预览：

```bash
python3 tools/task_manager.py workspace \
  --limit 50 \
  --include-config-summary \
  --include-config-preview \
  --profile-key dfhis-local-example \
  --output-dir /tmp/his_harness_task_workspace_configured_preview
```

单独导出团队分享包校验和本地覆盖策略：

```bash
python3 tools/config_check.py \
  --profile-key dfhis-local-example \
  --include-share-validation \
  --output-dir /tmp/his_harness_config_share_validation
```

显式展示配置中心摘要、配置预览和分享校验：

```bash
python3 tools/task_manager.py workspace \
  --limit 50 \
  --include-config-share-validation \
  --profile-key dfhis-local-example \
  --output-dir /tmp/his_harness_task_workspace_configured_share
```

单独生成配置导入草案文件到用户选择目录：

```bash
python3 tools/config_check.py \
  --profile-key dfhis-local-example \
  --include-import-draft \
  --draft-output-dir /tmp/his_harness_config_import_drafts \
  --output-dir /tmp/his_harness_config_import_draft
```

单独回读配置导入草案并生成只读校验：

```bash
python3 tools/config_check.py \
  --profile-key dfhis-local-example \
  --review-import-draft \
  --draft-input-dir /tmp/his_harness_config_import_drafts \
  --output-dir /tmp/his_harness_config_import_review
```

单独生成配置模板索引和差异对比：

```bash
python3 tools/config_check.py \
  --profile-key dfhis-local-example \
  --include-template-index \
  --draft-input-dir /tmp/his_harness_config_import_drafts \
  --compare-draft-input-dir /tmp/his_harness_config_import_drafts_compare \
  --output-dir /tmp/his_harness_config_template_index
```

单独生成只读配置向导：

```bash
python3 tools/config_check.py \
  --profile-key dfhis-local-example \
  --include-config-wizard \
  --draft-input-dir /tmp/his_harness_config_import_drafts \
  --compare-draft-input-dir /tmp/his_harness_config_import_drafts_compare \
  --output-dir /tmp/his_harness_config_wizard
```

显式展示配置中心摘要、配置预览、分享校验、导入草案、回读校验和模板索引：

```bash
python3 tools/task_manager.py workspace \
  --limit 50 \
  --include-config-import-draft \
  --draft-output-dir /tmp/his_harness_workspace_import_drafts \
  --include-config-import-review \
  --draft-input-dir /tmp/his_harness_workspace_import_drafts \
  --include-config-template-index \
  --profile-key dfhis-local-example \
  --output-dir /tmp/his_harness_task_workspace_configured_template_index
```

显式展示只读配置向导：

```bash
python3 tools/task_manager.py workspace \
  --limit 50 \
  --include-config-wizard \
  --draft-input-dir /tmp/his_harness_config_import_drafts \
  --profile-key dfhis-local-example \
  --output-dir /tmp/his_harness_task_workspace_configured_wizard
```

单独归一化一个本地需求来源：

```bash
python3 tools/requirement_provider_check.py \
  --source-type manual \
  --external-id MANUAL-1 \
  --title "手工需求" \
  --description "本地手工需求只读归一化。" \
  --output-dir /tmp/his_harness_requirement_provider_check
```

把本地需求来源证据显式接入主 Workflow：

```bash
python3 harnesses/his_requirement_workflow.py \
  --demand "手工需求正文" \
  --title "需求来源证据接入样例" \
  --mode mock \
  --execution-mode readonly \
  --requirement-evidence-file /tmp/his_harness_requirement_provider_check/requirement_evidence.json \
  --output-dir /tmp/his_harness_requirement_evidence_workflow
```

导出产物：

- `task_workspace.json`
- `task_workspace.html`
- `task_workspace_export_index.json`
- `task_workspace_export_index.md`
- `task_workspace_snapshot_comparison.json`
- `task_workspace_snapshot_comparison.md`
- `task_workspace_snapshot_history.json`
- `task_workspace_snapshot_history.md`
- `task_workspace_evidence_trend.json`
- `task_workspace_evidence_trend.md`
- `task_workspace_offline_review.json`
- `task_workspace_offline_review.md`
- `task_workspace_config_summary.json`（显式 `--include-config-summary` 时）
- `task_workspace_config_summary.md`（显式 `--include-config-summary` 时）
- `task_dashboard.json`
- `task_dashboard.md`
- `task_dashboard.html`
- `task_sample_set.json`
- `task_sample_set.md`
- `workbenches/<task_key>/task_workbench.json`
- `workbenches/<task_key>/task_workbench.md`
- `workbenches/<task_key>/requirement_calibration.json`（存在确认卡时）
- `workbenches/<task_key>/requirement_calibration.md`（存在确认卡时）
- `workbenches/<task_key>/task_change_history.json`
- `workbenches/<task_key>/task_change_history.md`

v0.17B 起，`task_workspace.json` 会额外包含 `task_details`，`task_workspace.html` 会在任务表下方展示只读详情 tabs：

- 概览
- Run 历史
- 需求理解确认卡
- 修改历史
- 回滚 dry-run
- 证据预览
- 可复制命令

v0.18 起，如果同一输出目录下已有上一版 `task_workspace.json`，再次导出会额外生成历史快照对比：

- `task_workspace_snapshot_comparison.json`
- `task_workspace_snapshot_comparison.md`

同时每次导出都会生成导出索引：

- `task_workspace_export_index.json`
- `task_workspace_export_index.md`

v0.19 起，同一输出目录会额外保留多次 workspace 快照，并生成趋势文件：

- `workspace_snapshots/<snapshot_id>/task_workspace.json`
- `task_workspace_snapshot_history.json`
- `task_workspace_snapshot_history.md`
- `task_workspace_evidence_trend.json`
- `task_workspace_evidence_trend.md`

`task_workspace.html` 会增加“多快照浏览”和“证据状态趋势”区域；页面选择控件只切换已内嵌的本地摘要数据，不发起网络请求、不执行命令。

v0.20 起，`task_workspace.json` 会额外包含 `navigation` 和 `snapshot_detail`；`task_workspace.html` 会增加顶部导航、快照详情区域，证据预览使用摘要和可展开明细。所有页面交互仍只切换当前 HTML 内嵌数据，不发起网络请求、不执行命令。

v0.21 起，`task_workspace.json` 会额外包含 `ui_polish` 和 `offline_review`；`task_workspace.html` 会增加“离线审查包”、空态说明、错误态说明、状态标签和大表滚动容器；同时导出：

- `task_workspace_offline_review.json`
- `task_workspace_offline_review.md`

离线审查包只汇总本地静态产物清单和审查步骤，不自动压缩、不上传、不执行复跑或回滚命令。

v0.22 起，`--include-config-summary` 会让 `task_workspace.json` 临时升级为 `0.22-task-workspace`，并在 `task_workspace.html` 中增加“配置中心”区域，展示 Rule Pack、Profile、Provider 和 Credential Store 状态。该入口仍然只读，只显示密钥是否配置、来源和脱敏尾号，不显示完整 key、不写配置、不测试网络、不读取远端、不执行外部写入。不传该参数时，旧 workspace 命令仍保持 v0.21 输出结构。

v0.23 起，`tools/requirement_provider_check.py` 可把本地 Yunxiao/TAPD/manual/file payload 统一导出为 `0.23-requirement-evidence`。该工具只处理本地输入，不读取远端需求、不测试 token、不调用评论/状态/负责人/迭代/附件接口；输出会经过本地密钥脱敏。

v0.24 起，`harnesses/his_requirement_workflow.py --requirement-evidence-file <path>` 会把本地 `requirement_evidence.json/md` 显式接入主流程和报告输出。Task Manager 的 `run` 也支持同名参数；`register-run` 会自动索引已有 output_dir 里的 `requirement_evidence.json/md`。不传该参数时，默认 demo 和旧 workflow 不会生成 `requirement_evidence.*`。

v0.25 起，`tools/config_check.py --include-preview` 会额外输出 `harness_config_preview.json/md`；`tools/task_manager.py workspace --include-config-preview` 会把 `task_workspace.json` 显式升级为 `0.25-task-workspace`，并在 `task_workspace.html` 中增加“配置预览”区域。预览只展示 provider 模板、credential key 引用、评论模板、提交规范、状态流转和验证规则摘要；不会读取远端、不会保存真实 token、不会测试 provider 连通性。不传该参数时，旧 workspace 命令仍保持 v0.21 输出结构；只传 `--include-config-summary` 时仍保持 v0.22 配置摘要行为。

v0.26 起，`tools/config_check.py --include-share-validation` 会额外输出 `harness_config_share_validation.json/md`；`tools/task_manager.py workspace --include-config-share-validation` 会把 `task_workspace.json` 显式升级为 `0.26-task-workspace`，并在 `task_workspace.html` 中增加“配置分享校验”和“本地覆盖策略”区域。分享校验只读检查本地 Rule Pack/Profile 模板，不会应用配置、不会写入 `~/.his-harness`、不会测试远端账号权限。不传该参数时，旧 workspace 命令仍保持 v0.21 输出结构。

v0.27 起，`tools/config_check.py --include-import-draft --draft-output-dir <dir>` 会在用户选择目录生成 `profiles.draft.json`、`rule_pack.draft.json`、`credentials.example.json`、`IMPORT_GUIDE.md` 和 `config_import_manifest.json`。`tools/task_manager.py workspace --include-config-import-draft --draft-output-dir <dir>` 会把 `task_workspace.json` 显式升级为 `0.27-task-workspace`，并在 `task_workspace.html` 中增加“配置导入草案”区域。导入草案默认不覆盖同名文件；它只生成示例文件和人工复制命令，不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号权限。不传该参数时，旧 workspace 命令仍保持 v0.21 输出结构。

v0.28 起，`tools/config_check.py --review-import-draft --draft-input-dir <dir>` 会回读 v0.27 草案并输出 `harness_config_import_review.json/md`。`tools/task_manager.py workspace --include-config-import-review --draft-input-dir <dir>` 会把 `task_workspace.json` 显式升级为 `0.28-task-workspace`，并在 `task_workspace.html` 中增加“配置导入回读校验”“只读表单预览”和“导入前风险提示”区域。回读校验只读取草案目录，不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号权限。不传该参数时，旧 workspace 命令仍保持 v0.21 输出结构。

v0.29 起，`tools/config_check.py --include-template-index --draft-input-dir <dir> [--compare-draft-input-dir <dir>]` 会输出 `harness_config_template_index.json/md`。`tools/task_manager.py workspace --include-config-template-index --draft-input-dir <dir>` 会把 `task_workspace.json` 显式升级为 `0.29-task-workspace`，并在 `task_workspace.html` 中增加“配置模板索引”“多 Profile 切换预览”和“配置差异对比”区域。模板索引只读取草案目录，不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号权限。不传该参数时，旧 workspace 命令仍保持 v0.21 输出结构。

v0.30 起，`tools/config_check.py --include-config-wizard --draft-input-dir <dir> [--compare-draft-input-dir <dir>]` 会输出 `harness_config_wizard.json/md`。`tools/task_manager.py workspace --include-config-wizard --draft-input-dir <dir>` 会把 `task_workspace.json` 显式升级为 `0.30-task-workspace`，并在 `task_workspace.html` 中增加“配置向导”区域，串联选择来源、Provider 模板、分享校验、生成草案、回读校验、对比模板和人工复制前确认。配置向导只聚合本地只读结果，不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号权限。不传该参数时，旧 workspace 命令仍保持 v0.21 输出结构。

v0.31 起，配置向导输出升级为 `0.31-configuration-wizard`，workspace 显式升级为 `0.31-task-workspace`，并增加步骤筛选、阻断摘要、命令复制 target 和空态说明。该增强只改变只读 JSON/Markdown/HTML 的可读性，不会执行复制出的命令、不会应用配置、不会写入真实配置目录、不改变旧默认 workspace。

v0.32 起，`tools/task_manager.py workspace --include-config-wizard --draft-input-dir <dir>` 会把 `task_workspace.json` 显式升级为 `0.32-task-workspace`，并生成 `task_workspace_config_review_package.json/md`。审查包索引会列出配置相关导出文件、审查入口、复跑命令和人工确认项，便于团队成员只打开一个本地 HTML/Markdown 入口完成配置接入前检查；默认 workspace 不传新参数时仍保持 v0.21 输出结构。

v0.33 起，配置审查包索引升级为 `0.33-configuration-review-package-index`，workspace 显式升级为 `0.33-task-workspace`，并增加文件搜索、文件状态筛选、待确认分组和交接摘要。默认 workspace 不传新参数时仍保持 v0.21 输出结构。

DFHIS-31270 前端受控修复示例：

```bash
HARNESS_CREDENTIALS_FILE=/Users/lym/WorkCode/ai/apiKey/credentials.json \
python3 harnesses/his_requirement_workflow.py \
  --demand-file /tmp/dfhis_31270_demand.txt \
  --title "DFHIS-31270 v0.8.9 前端展示受控修复" \
  --mode openai \
  --yunxiao-read \
  --yunxiao-url "https://devops.aliyun.com/projex/req/DFHIS-31270" \
  --project-root /Users/lym/Desktop/dongFang/dfcode \
  --execution-mode fullstack-worktree \
  --worktree-dir /tmp/his_harness_v089_dfhis_31270_fullstack_worktrees \
  --output-dir /tmp/his_harness_v089_dfhis_31270_fullstack
```

DFHIS-31270 提交前验证示例：

```bash
python3 tools/precommit_verify.py \
  --project-root /Users/lym/Desktop/dongFang/dfcode \
  --worktree-dir /tmp/his_harness_v090_dfhis_31270_precommit_worktrees \
  --output-dir /tmp/his_harness_v090_dfhis_31270_precommit
```

也可以走主 Workflow：

```bash
HARNESS_CREDENTIALS_FILE=/Users/lym/WorkCode/ai/apiKey/credentials.json \
python3 harnesses/his_requirement_workflow.py \
  --demand-file /tmp/dfhis_31270_demand.txt \
  --title "DFHIS-31270 v0.9.1 提交前验证" \
  --mode openai \
  --yunxiao-read \
  --yunxiao-url "https://devops.aliyun.com/projex/req/DFHIS-31270" \
  --project-root /Users/lym/Desktop/dongFang/dfcode \
  --execution-mode precommit-verify \
  --worktree-dir /tmp/his_harness_v090_dfhis_31270_precommit_worktrees \
  --output-dir /tmp/his_harness_v090_dfhis_31270_precommit_workflow
```

单需求真实开发试跑示例：

```bash
HARNESS_CREDENTIALS_FILE=/Users/lym/WorkCode/ai/apiKey/credentials.json \
python3 harnesses/his_requirement_workflow.py \
  --demand-file /tmp/dfhis_xxxxx_demand.txt \
  --title "DFHIS-xxxxx 单需求真实开发试跑" \
  --mode openai \
  --yunxiao-read \
  --yunxiao-url "https://devops.aliyun.com/projex/req/DFHIS-xxxxx" \
  --project-root /Users/lym/Desktop/dongFang/dfcode \
  --execution-mode single-demand-trial \
  --worktree-dir /tmp/his_harness_single_demand_worktrees \
  --yunxiao-transaction-mode dry-run \
  --yunxiao-policy-config config/yunxiao.example.json \
  --yunxiao-policy-key his_comment_write_enabled \
  --yunxiao-entity-kind requirement \
  --yunxiao-entity-id DFHIS-xxxxx \
  --output-dir /tmp/his_harness_single_demand_dfhis_xxxxx
```

如需写真实交付评论，必须额外显式使用 `--yunxiao-transaction-mode write --yunxiao-write-scope comment-only --yunxiao-write-confirm WRITE:requirement:DFHIS-xxxxx`，并配置专用 `aliyun_devops_write_pat`。即使写评论成功，也不会真实流转状态。

## 历史真实模型配置（v0.58 当前冻结）

以下配置格式仅保留兼容说明。v0.58 会在读取这些凭证前阻断真实模型入口，不应执行真实 smoke 或真实模型 DAG。

支持 OpenAI-compatible Chat Completions：

```bash
export OPENAI_API_KEY=你的 API Key
export OPENAI_BASE_URL=https://api.openai.com/v1
export OPENAI_MODEL=gpt-4.1-mini
```

DeepSeek v4-flash 也走 OpenAI-compatible 模式，例如：

```bash
export HARNESS_LLM_MODE=openai
export OPENAI_API_KEY=你的 DeepSeek API Key
export OPENAI_BASE_URL=https://api.deepseek.com/v1
export OPENAI_MODEL=deepseek-v4-flash
```

也可以把 OpenAI-compatible/DeepSeek 配置放入本机凭证文件 `/Users/lym/WorkCode/ai/apiKey/credentials.json`：

```json
{
  "openai_api_key": "你的 DeepSeek API Key",
  "openai_base_url": "https://api.deepseek.com/v1",
  "openai_model": "deepseek-v4-flash"
}
```

环境变量优先于凭证文件。凭证文件里的 token 只加载到当前进程，不写入报告、日志或数据库。

也支持智谱 GLM-5.1 Anthropic 兼容接口：

```bash
export HARNESS_LLM_MODE=anthropic
export ANTHROPIC_AUTH_TOKEN=你的智谱 API Key
export ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic
export ANTHROPIC_MODEL=glm-5.1
```

正式运行必须配置对应模式的 API Key；缺失时会失败，不会用 mock 冒充通过。不要把 API Key 写入项目文件或提交到仓库。

说明：Claude Code / GLM Coding Plan 的环境变量不会自动注入到 Harness，必须让当前 Python 进程能读取到上述环境变量。

如果你已经把 Anthropic 兼容配置写在 `~/.claude/settings.json` 的 `env` 里，可以显式加载：

```bash
python3 tools/self_check.py --mode anthropic --load-claude-settings
```

Harness 只会读取必要环境变量，不会把密钥写入报告或输出。

`--mode anthropic` 的正式自测默认要求 `ANTHROPIC_BASE_URL` 指向智谱 `open.bigmodel.cn`。如果 settings 里是其他 Anthropic 兼容网关，自测会失败，避免把非智谱模型误判为 GLM-5.1 验收通过。

调试其他 Anthropic 兼容网关或本地协议替身时，可以显式加：

```bash
python3 tools/self_check.py --mode anthropic --allow-non-zhipu-anthropic
```

这种结果会被标记为非正式业务有效，只能证明协议和 Harness 流程兼容。

## 跑真实需求

```bash
python3 harnesses/his_requirement_workflow.py \
  --demand-file demand.txt \
  --title "门诊收费需求" \
  --mode anthropic \
  --load-claude-settings \
  --output-dir runs
```

接入真实项目做只读工程分析：

```bash
python3 harnesses/his_requirement_workflow.py \
  --demand-file demand.txt \
  --title "门诊收费需求" \
  --mode anthropic \
  --load-claude-settings \
  --project-path /absolute/path/to/his-project \
  --output-dir runs
```

受控 worktree 改码：

```bash
python3 harnesses/his_requirement_workflow.py \
  --demand-file demand.txt \
  --title "DFHIS-31195 优惠项目界面不限时" \
  --mode anthropic \
  --load-claude-settings \
	  --project-path /absolute/path/to/his-project \
	  --execution-mode worktree \
  --allowed-path src/pages/feiYongGl/youHuiLb.vue \
  --allowed-path src/pages/feiYongGl/youHuiLb_xsby.vue \
  --allowed-path src/apis/feiYongGl/youHuiLb.js \
  --allowed-path src/router/feiYongGl/index.js \
  --output-dir runs
```

v0.8.8 后，如果不传 `--project-path`，Harness 会从 `--project-root` 自动选择项目；如果技术自治判断能确定目标文件，会自动补 patch 白名单和无副作用验证命令。

DFHIS-31270 示例：

```bash
python3 harnesses/his_requirement_workflow.py \
  --demand-file /tmp/dfhis_31270_demand.txt \
  --title "DFHIS-31270 住院收费结算收款预交金备注列" \
  --mode openai \
  --yunxiao-read \
  --yunxiao-url "https://devops.aliyun.com/projex/req/DFHIS-31270" \
  --project-root /Users/lym/Desktop/dongFang/dfcode \
  --execution-mode worktree \
  --max-edit-rounds 2 \
  --output-dir runs
```

默认只运行 `git diff --check`。如需额外验证，显式传入：

```bash
  --verify-command "npm run lint"
```

`--allowed-path` 一旦传入，就覆盖 evidence bundle 默认白名单。`--max-edit-rounds 2` 表示初次尝试失败后最多自动返工 2 轮。

DFHIS-31195 v0.7.4 受控追加修复示例：

```bash
python3 harnesses/his_requirement_workflow.py \
  --demand-file /tmp/dfhis_31195_demand.txt \
  --title "DFHIS-31195 核心不限时追加修复 v0.7.4" \
  --mode openai \
  --project-path /Users/lym/Desktop/dongFang/dfcode/df-web-zhushujugl \
  --execution-mode worktree \
  --worktree-dir /tmp/his_harness_v074_dfhis_patch_worktrees \
  --allowed-path src/pages/feiYongGl/youHuiLb.vue \
  --verify-command "./node_modules/.bin/vue-cli-service lint --no-fix src/pages/feiYongGl/youHuiLb.vue" \
  --max-edit-rounds 2 \
  --yunxiao-read \
  --yunxiao-url "https://devops.aliyun.com/projex/bug/DFHIS-31195" \
  --output-dir /tmp/dfhis_31195_deepseek_v074_patch
```

云效只读凭证读取顺序：

- `ALIYUN_DEVOPS_PAT` / `ALIYUN_DEVOPS_ORGANIZATION_ID`
- `aliyun_devops_pat` / `aliyun_devops_organization_id`
- `/Users/lym/WorkCode/ai/apiKey/credentials.json`，可用 `HARNESS_CREDENTIALS_FILE` 覆盖
- macOS Keychain 同名 generic password

云效接口默认请求 `https://openapi-rdc.aliyuncs.com`；如果需要切换私有化或其它接入点，可设置 `YUNXIAO_API_BASE_URL`。如果报告返回 `Forbidden.InvalidUser.UserNotInCurrentOrganization`，优先检查凭证文件里的组织 ID 是否和当前 PAT 所属组织一致，以及该 PAT 是否具备当前组织的工作项读取权限。

如果 token 缺失、云效详情读不到，或需求未说明“不限时”到底是有效时间清空、展示错误、查询错误还是保存规则错误，Harness 会生成阻断报告，不会进入 patch。即使令牌本身有写权限，v0.7.4 也只执行读取动作。

推荐的本机凭证文件：

```json
{
  "aliyun_devops_organization_id": "你的云效组织 ID",
  "aliyun_devops_project_id": "你的云效项目 ID，可选",
  "aliyun_devops_pat": "只读或读写个人令牌",
  "aliyun_devops_write_pat": "可选：专用写入个人令牌"
}
```

凭证文件应放在项目目录外，并限制权限：

```bash
mkdir -p /Users/lym/WorkCode/ai/apiKey
chmod 700 /Users/lym/WorkCode/ai/apiKey
chmod 600 /Users/lym/WorkCode/ai/apiKey/credentials.json
```

云效只读凭证和 smoke 验证：

```bash
python3 tools/yunxiao_read_check.py \
  --output-dir /tmp/his_harness_yunxiao_read_check
```

该命令默认读取 DFHIS-31226 和 DFHIS-31216，只生成 `yunxiao_read_check_report.md` 和 `yunxiao_read_check_result.json`，不写评论、不流转状态、不改负责人。
报告会额外展示清洗后的需求正文、内联图片/文件列表和下载摘要；下载文件只落在 smoke 输出目录下，不进入业务项目目录。

云效事务 dry-run：

```bash
python3 harnesses/his_requirement_workflow.py \
  --demand-file /tmp/dfhis_31195_demand.txt \
  --title "DFHIS-31195 云效事务 dry-run" \
  --mode mock \
  --project-path /Users/lym/Desktop/dongFang/dfcode/df-web-zhushujugl \
  --yunxiao-url "https://devops.aliyun.com/projex/bug/DFHIS-31195" \
  --yunxiao-transaction-mode dry-run \
  --yunxiao-entity-kind bug \
  --yunxiao-current-status "开发中" \
  --output-dir /tmp/dfhis_31195_v080_yunxiao_dry_run
```

如果要测试“策略允许但仍不真实执行”，可以使用示例策略：

```bash
python3 harnesses/his_requirement_workflow.py \
  --demand-file /tmp/dfhis_31195_demand.txt \
  --title "DFHIS-31195 云效事务 dry-run" \
  --mode mock \
  --yunxiao-url "https://devops.aliyun.com/projex/bug/DFHIS-31195" \
  --yunxiao-transaction-mode dry-run \
  --yunxiao-policy-config config/yunxiao.example.json \
  --yunxiao-policy-key his_dry_run_enabled \
  --yunxiao-entity-kind bug \
  --yunxiao-current-status "开发中" \
  --output-dir /tmp/dfhis_31195_v080_yunxiao_dry_run
```

v0.8.0 的 dry-run 行为：

- 不读取 `aliyun_devops_write_pat` 或 `ALIYUN_DEVOPS_WRITE_PAT`。
- 不调用云效评论、状态、负责人、关闭等写接口。
- 不会因为你换成读写个人令牌就自动获得写能力。
- 只在本地输出 `yunxiao_transaction_plan.json`、`yunxiao_transaction_plan.md` 和审计记录。
- 如果缺少 entity id 或无法推断实体类型，事务计划会失败，但主报告仍会生成。

凭证放置原则：

- 环境变量：只对当前 shell、进程或 CI 运行有效；除非你写进 shell profile，否则不会进入项目文件。
- 本机凭证文件：默认 `/Users/lym/WorkCode/ai/apiKey/credentials.json`，适合你当前需要集中查找和维护的场景；文件不进入项目目录。
- macOS Keychain：适合保存本机长期凭证，避免明文落盘。
- 配置文件：只放项目路径、动作开关、状态机和策略；不要写 token。
- 报告和数据库：只能记录凭证 key 名、策略、动作、状态和原因，不能记录 token 原文。
- v0.8.0 只预留后续写 token 名称 `aliyun_devops_write_pat` / `ALIYUN_DEVOPS_WRITE_PAT`，本阶段不读取、不使用。

v0.8.1 全事务 dry-run 示例：

```bash
python3 harnesses/his_requirement_workflow.py \
  --demand-file /tmp/dfhis_31195_demand.txt \
  --title "DFHIS-31195 云效全事务 dry-run" \
  --mode mock \
  --project-path /Users/lym/Desktop/dongFang/dfcode/df-web-zhushujugl \
  --yunxiao-url "https://devops.aliyun.com/projex/bug/DFHIS-31195" \
  --yunxiao-transaction-mode dry-run \
  --yunxiao-policy-config config/yunxiao.example.json \
  --yunxiao-policy-key his_dry_run_enabled \
  --yunxiao-entity-kind bug \
  --yunxiao-current-status "开发中" \
  --yunxiao-target-status "待人工审核" \
  --yunxiao-target-assignee "zhangsan" \
  --yunxiao-target-iteration "迭代-2026-06" \
  --yunxiao-screenshot /tmp/dfhis_31195.png \
  --yunxiao-service-change-file /tmp/dfhis_31195_service_change.json \
  --yunxiao-artifact diff=final.diff \
  --yunxiao-artifact test_report=self_check_report.md \
  --output-dir /tmp/dfhis_31195_v081_yunxiao_dry_run
```

v0.8.1 行为：

- `--yunxiao-screenshot` 只记录文件名、大小和 sha256，不上传文件；文件缺失时该动作 rejected，主报告继续生成。
- `--yunxiao-service-change-file` 只读取 JSON 摘要并生成服务变更建议，不写云效服务变更。
- `--yunxiao-target-status` 显式指定目标状态；未传时按运行结果推断，但 `all_passed` 不自动流转到完成。
- `--yunxiao-artifact type=value` 只生成产物关联建议，不上传、不绑定真实云效产物。
- 高风险需求默认阻断附件、负责人、状态、迭代、服务变更和关闭动作，必须人工确认。
- `decision/payload` 会记录 `runtime_mode=dry_run` 和 `real_write_status=not_executed`。

v0.8.2 fake write 自测示例：

```bash
python3 harnesses/his_requirement_workflow.py \
  --demand-file /tmp/dfhis_31195_demand.txt \
  --title "DFHIS-31195 云效 fake write 验证" \
  --mode mock \
  --yunxiao-url "https://devops.aliyun.com/projex/bug/DFHIS-31195" \
  --yunxiao-transaction-mode write \
  --yunxiao-write-transport fake \
  --yunxiao-write-confirm WRITE:bug:DFHIS-31195 \
  --yunxiao-policy-config config/yunxiao.example.json \
  --yunxiao-policy-key his_dry_run_enabled \
  --yunxiao-entity-kind bug \
  --yunxiao-current-status "开发中" \
  --yunxiao-target-status "待人工审核" \
  --yunxiao-target-assignee "zhangsan" \
  --yunxiao-target-iteration "迭代-2026-06" \
  --yunxiao-human-confirmed \
  --output-dir /tmp/dfhis_31195_v082_fake_write
```

v0.8.2 real write 边界：

- 默认不对生产 BUG 首跑全事务；先用专用测试 BUG/任务验证。
- 写 token 读取顺序：环境变量 `ALIYUN_DEVOPS_WRITE_PAT` / `aliyun_devops_write_pat`，本机凭证文件 `aliyun_devops_write_pat`，再到 Keychain；如果没有专用写 token，会按同样顺序兜底读取 `aliyun_devops_pat`，但仍必须满足 `write` 模式和 `WRITE:<entity_kind>:<entity_id>` 双开关确认。
- 组织 ID 读取顺序：环境变量、本机凭证文件、Keychain。
- 配置文件只放动作开关、状态机和字段映射，禁止放 token。
- 真实写入动作会写入 `yunxiao_audit_events`，包含 `runtime_mode`、`real_write_status`、`external_request_id`、`external_response` 和 `verification_status`。
- `close` 默认关闭；即使进入 write 模式也不会自动关闭高风险需求。
- `fake` transport 不代表真实云效已经写入，只用于验证 Harness 写入管道、幂等和审计。

v0.8.5/v0.8.6 comment-only real write：

- 首次真实云效写入只开放 `comment`，通过 `--yunxiao-write-scope comment-only` 固定边界。
- `assign`、`transition`、`update_iteration`、`upload_attachment`、`update_service_change`、`link_artifact`、`close` 即使传参也会被真实写入层阻断。
- real write 必须使用专用 `aliyun_devops_write_pat`；`fallback_read_pat` 只能用于 dry-run 或 fake transport。
- 写评论前会读取云效已有评论并查找幂等标记，存在则跳过，无法读取评论则阻断，避免重复评论。v0.9.4 起标记写入 HTML 注释，人工正文不再显示 `HIS-HARNESS-IDEMPOTENCY`。
- v0.8.6 会在事务计划中输出 `effective_write_status`，把 comment-only 下的预期阻断和真实失败区分开。
- 当前专用验证对象：`DFHIS-31239`。

```bash
python3 harnesses/his_requirement_workflow.py \
  --demand-file /tmp/dfhis_31239_demand.txt \
  --title "DFHIS-31239 v0.8.5 云效只写评论验证" \
  --mode anthropic \
  --load-claude-settings \
  --yunxiao-read \
  --yunxiao-url "https://devops.aliyun.com/projex/req/DFHIS-31239" \
  --yunxiao-transaction-mode write \
  --yunxiao-write-scope comment-only \
  --yunxiao-policy-config config/yunxiao.example.json \
  --yunxiao-policy-key his_comment_write_enabled \
  --yunxiao-entity-kind requirement \
  --yunxiao-entity-id DFHIS-31239 \
  --yunxiao-write-confirm WRITE:requirement:DFHIS-31239 \
  --yunxiao-write-transport real \
  --output-dir /tmp/his_harness_v085_yunxiao_comment_31239
```

v0.8.6 评论幂等复验：

```bash
python3 tools/yunxiao_read_check.py \
  --url "https://devops.aliyun.com/projex/req/DFHIS-31239" \
  --include-comments \
  --comment-marker "HIS-HARNESS-IDEMPOTENCY:<从 yunxiao_transaction_plan.json 取得的幂等键>" \
  --output-dir /tmp/his_harness_v086_yunxiao_comment_marker_31239
```

再次用相同输入运行 comment-only real write 时，预期评论动作为 `write_skipped_idempotent`，不会新增重复评论；云效状态和负责人不应变化。

v0.9.4 研发交付评论模板：

- 真实评论仍只允许 `comment-only`，不流转状态、不改负责人、不调整迭代、不上传附件、不关闭任务。
- 评论正文固定包含：需求、提交、分支、改动范围、改动说明、验证结果、测试建议。
- `commit`、`branch`、`changed_file` 可通过 `--yunxiao-artifact type=value` 显式传入；未传 `commit` 和 `changed_file` 时，Harness 会尽量从 `--project-path` 的 Git HEAD 或 review/precommit 产物只读推断。
- 分支未显式传入时，需求默认展示 `feature-DFHIS-xxxxx + RC_2.16.1_250514`，缺陷默认展示 `hotfix-DFHIS-xxxxx + RC_2.16.1_250514`。
- 视觉证据可通过 `--yunxiao-screenshot <path>`，或 `--yunxiao-artifact screenshot=<path>`、`video=<path>`、`gif=<path>` 传入；评论只展示文件名、大小和 sha256，不展示 token。
- 没有截图/视频/GIF 时，评论必须写明“未提供截图/视频/GIF”，并改用改动点、自动验证摘要和人工测试建议说明，不能写成“已验收通过”。
- 人工可见正文不再包含 Harness 边界说明和单独可见的幂等行；幂等标记以 HTML 注释 `<!-- HIS-HARNESS-IDEMPOTENCY:<key> -->` 写入评论源码，用于重复运行时跳过重复评论。
- 如果真实写入后无法回读隐藏幂等标记，Harness 会把本次动作标为 `verify_failed`，提示云效可能过滤了 HTML 注释，避免后续静默重复评论。

v0.9.4 状态流转规则基座：

- 规则草案见 `config/yunxiao_transition_rules.v094.json`，只用于 dry-run/fake 验证。
- `analysis_unclear` 建议“待澄清”；`developed_unverified` 建议“待测试”；`verification_failed` 建议“开发中”；高风险或缺少验收证据建议“待人工审核”；`all_passed` 不自动关闭。
- 真实状态流转、负责人、迭代、附件、服务变更和关闭仍不开放；`transition-fake` 只允许 fake transport。

示例：

```bash
python3 harnesses/his_requirement_workflow.py \
  --demand-file /tmp/dfhis_31270_demand.txt \
  --title "DFHIS-31270 云效研发交付评论 dry-run" \
  --mode mock \
  --project-path /Users/lym/Desktop/dongFang/dfcode/df-web-zhuyuansf \
  --yunxiao-url "https://devops.aliyun.com/projex/req/DFHIS-31270" \
  --yunxiao-transaction-mode dry-run \
  --yunxiao-policy-config config/yunxiao.example.json \
  --yunxiao-policy-key his_comment_write_enabled \
  --yunxiao-entity-kind requirement \
  --yunxiao-entity-id DFHIS-31270 \
  --yunxiao-artifact commit=043f7c3b \
  --yunxiao-artifact branch=feature-DFHIS-31270 \
  --yunxiao-artifact changed_file=src/pages/chuYuanYw/jieSuan/dialog/jieSuan.vue \
  --output-dir /tmp/his_harness_v092_yunxiao_comment_31270_dry_run
```

v0.8.6 transition fake：

- `--yunxiao-write-scope transition-fake` 只允许 `--yunxiao-write-transport fake`。
- 该范围只用于验证 `comment` + `transition` 的策略、状态机、字段映射、审计和报告链路。
- 真实云效状态流转、负责人、迭代、附件、服务变更和关闭仍不执行。

```bash
python3 harnesses/his_requirement_workflow.py \
  --demand-file /tmp/dfhis_31239_demand.txt \
  --title "DFHIS-31239 v0.8.6 状态流转 fake 验证" \
  --mode mock \
  --yunxiao-read \
  --yunxiao-url "https://devops.aliyun.com/projex/req/DFHIS-31239" \
  --yunxiao-transaction-mode write \
  --yunxiao-write-scope transition-fake \
  --yunxiao-policy-config config/yunxiao.example.json \
  --yunxiao-policy-key his_transition_fake_enabled \
  --yunxiao-entity-kind requirement \
  --yunxiao-entity-id DFHIS-31239 \
  --yunxiao-current-status 待开发 \
  --yunxiao-target-status 开发中 \
  --yunxiao-write-confirm WRITE:requirement:DFHIS-31239 \
  --yunxiao-write-transport fake \
  --output-dir /tmp/his_harness_v086_transition_fake_31239
```

v0.8.7 才考虑在专用测试任务上做真实状态流转；执行前必须确认云效实际状态路径和字段映射。

v0.8.7 需求验收矩阵：

```bash
python3 harnesses/his_requirement_workflow.py \
  --demand-file /tmp/dfhis_31239_demand.txt \
  --title "DFHIS-31239 v0.8.7 需求验收矩阵验证" \
  --mode anthropic \
  --load-claude-settings \
  --yunxiao-read \
  --yunxiao-url "https://devops.aliyun.com/projex/req/DFHIS-31239" \
  --execution-mode readonly \
  --project-path /path/to/frontend-project \
  --project-path /path/to/backend-project \
  --output-dir /tmp/his_harness_v087_dfhis_31239
```

输出目录会包含：

- `acceptance_matrix.json`
- `acceptance_matrix.md`
- `yunxiao_evidence.json`
- `report.md`
- `run.json`

本阶段推荐命令只作为建议写入矩阵，不自动执行；需要实际运行时必须显式加 `--verify-command`。

已提交 diff 审查：

```bash
python3 harnesses/his_requirement_workflow.py \
  --demand-file demand.txt \
  --title "DFHIS-31195 已提交修复审查" \
  --mode anthropic \
  --load-claude-settings \
  --project-path /absolute/path/to/his-project \
  --execution-mode review-worktree \
  --review-commit HEAD \
  --review-base HEAD^ \
  --worktree-dir /tmp/his_harness_review_worktrees \
  --allowed-path src/pages/feiYongGl/youHuiLb.vue \
  --verify-command "yarn lint" \
  --output-dir runs
```

`review-worktree` 要求原业务仓库没有未提交改动。如果传入 `--allowed-path`，提交改动文件必须全部在白名单内。它不会生成新补丁，只会输出 `review_manifest.json`、`review.diff`、`review_summary.md` 和验证日志。

验证命令默认做 base/head 对比，分类如下：

- `pass`：head 通过。
- `baseline_existing`：base/head 都失败且错误指纹一致，作为历史基线 warning，不阻断当前提交。
- `regression_failed`：base 通过但 head 失败，判定本次提交引入问题。
- `changed_failure`：base/head 都失败但错误指纹不同，保守判定需人工介入。
- `infra_failed`：依赖、命令、worktree 或执行环境异常。
- `baseline_side_effect`：只有 base worktree 被验证命令修改，作为历史基线副作用 warning，不阻断当前提交。
- `head_side_effect_failed`：head worktree 被验证命令修改，阻断当前提交，避免验证副作用污染结果。

历史基线失败不会被伪装成验证通过，报告会明确标为 warning。临时 worktree 不会复制依赖；如果原项目存在 `node_modules`，Harness 会在 base/head 两个临时 worktree 中创建指向原目录的同名符号链接并写入 manifest，便于执行 `yarn lint` 这类本地验证命令。

也可以参考 `config/projects.example.json` 建立本地 `config/projects.json`，然后用项目画像运行：

```bash
python3 harnesses/his_requirement_workflow.py \
  --demand-file demand.txt \
  --title "门诊收费需求" \
  --mode anthropic \
  --project-key his_local \
  --output-dir runs
```

项目画像只保存路径、目录、测试命令和敏感关键词；不要把 API Key、Jenkins 密码、生产发布凭证写入配置。

输出目录示例：

```text
runs/run_1/
  report.md
  run.json
  step_01_attempt_0_demand_analysis.md
  ...
```

## 自测自审

v0.58 离线企业核心验证：

```bash
python3 tools/self_check.py --mode mock
```

它会执行：

- Python 和项目文件预检。
- 真实模型入口冻结和 mock 不读取凭证校验。
- 3 类 HIS 真实风格样例：
  - 普通前端字段需求。
  - 后端流程调整需求。
  - 医保/结算/报表高风险需求。
- 本地 fixture HIS 项目只读扫描。
- Evidence bundle 生成与报告引用校验。
- 文件快照校验，确认 Harness 没有改动 fixture 项目。
- 云效事务策略校验，确认当前阶段只允许读取，评论、附件、负责人、状态、迭代、服务变更、关闭等写动作默认被阻断。
- 云效事务 dry-run 校验，确认 off 模式不生成计划、全事务计划能生成、策略关闭会阻断、策略开启也只标记未执行、高风险阻断敏感动作、重复计划按幂等键复用审计记录。
- Patch Readiness 校验，确认云效凭证缺失会失败、语义不足会阻断、语义和证据充分时才允许进入 worktree patch。
- Worktree 策略校验，确认临时 Git worktree、白名单 patch、非 Git 拦截和验证失败停止机制可用。
- Interaction Evidence 校验，确认交互敏感 diff 无方法级证据会阻断 precommit，有方法级测试和 UI 证据时才允许进入提交准备和云效评论准备，并确认 `--method-test-command` 可自动生成方法级证据、`--ui-capture-command` 可自动生成 UI 证据、Playwright capture 模板能描述登录态和 stdout 协议。
- Precommit 真实样板边界校验，确认白名单内未跟踪新增文件可复现到临时 worktree，白名单外 dirty scope 只限制提交/云效评论，不把目标验证误判失败，非交互排班过滤不会误套结算交互门禁。
- Review Worktree 策略校验，确认已提交 diff 读取、白名单审查、非 Git/dirty repo 拦截、diff-check 失败报告、base/head 验证基线对比和验证命令副作用检测可用。
- 动态计划登记校验，确认相同计划幂等、契约版本可记录、恢复预览可重复生成，并且不会执行 DAG 节点。
- 动态调度 dry-run 校验，确认失败/重试、模拟成功、事件幂等、checkpoint hash 和输出边界可重复执行。
- 自动 Evaluator 审核。
- 不合格自动返工。
- 输出 `self_check_report.md` 和 `self_check_result.json`。

也可以只运行内置离线流程：

```bash
python3 harnesses/his_requirement_workflow.py --demo
python3 tools/self_check.py --mode mock
```

mock 输出会明确标记 `business_valid=false`：只证明 Harness 工程链路，不替代真实业务判断。

## 本地 Web 后台

```bash
python3 run.py
```

打开：

```text
http://127.0.0.1:8765
```

说明：Web UI 现在是本地可靠性闭环入口：`/runs` 提交后转为后台 Job，详情页显示进度、验证状态、失败阶段和恢复动作。它仍是本地只读/受控执行界面，不代表真实 HIS 业务验收或外部系统写入已完成。

## 项目结构

```text
app/
  database.py      SQLite 表结构、兼容迁移、查询写入
  evaluator.py     独立自动审核器
  dynamic_plan_registry.py  v0.50 动态计划登记、契约版本、stale 传播和只读恢复预览
  dynamic_planning.py  v0.49 复杂度评分、动态组队、子任务 DAG 和交接契约
  dynamic_scheduler.py  v0.51 dry-run 调度、预算/重试状态机和 checkpoint
  node_runtime.py  v0.52 不可变节点上下文、工具权限裁决和 fixture-only executor
  executor_runtime.py  v0.53 一次性 capability lease、固定 worker adapter 和失败隔离
  mock_agent_runtime.py  v0.54 deterministic mock-agent DAG、候选交接和 trace/metrics
  model_invocation_runtime.py  v0.55 provider-neutral mock/replay、结构化输出和 cassette 审计
  model_dag_runtime.py  v0.56 多波次离线模型 DAG、节点 adapter policy 和并行 trace
  model_provider_runtime.py  v0.57 双开关真实 provider 单节点 smoke 与脱敏审计
  harness.py       Workflow 执行、自动返工、报告输出
  interaction_evidence.py  v0.10.2 方法级交互测试计划、执行结果和 UI 证据 manifest
  method_test_runner.py  v0.10.3A 显式方法级测试命令执行器
  ui_capture_template.py  v0.10.3C Playwright/Chrome UI 采集模板生成器
  ui_evidence_runner.py  v0.10.3B 显式 UI 证据采集命令执行器
  llm_client.py    mock / OpenAI-compatible / Anthropic-compatible 模型调用抽象
  project_context.py  只读工程扫描、证据包、风险分级和上下文压缩
	  review_executor.py  已提交 diff 审查、临时 worktree 验证和审查产物
	  server.py        标准库 Web 后台
	  technical_decision.py  技术自治项目选择、字段来源判断和实施决策
	  fullstack_executor.py  多项目 fullstack worktree、统一验证和合入
	  precommit_verifier.py  提交前验证矩阵、代码审查包和提交准备结论
	  worktree_executor.py  受控 Git worktree、patch 白名单、验证命令和改码返工
  clarification_gate.py  v0.7.4 业务澄清闸口和 patch readiness 判断
  yunxiao_read.py   云效工作项、附件和文件信息只读证据采集
  yunxiao_transaction.py  云效实体、动作分级、权限策略、事务审计和生命周期建议
config/
  dynamic_planning.example.json  v0.49 动态规划开关、阈值和硬保护示例
  projects.example.json        项目画像示例
  yunxiao.example.json         云效事务策略示例，不包含密钥
harnesses/
  his_requirement_workflow.py  CLI 入口
prompts/
  default_experts.json         专家团与步骤配置
	tools/
	  cleanup_worktrees.py       临时 worktree 清理工具
	  dynamic_plan.py             v0.49 显式只读动态规划工具
	  fixture_node_worker.py      v0.53 固定 sandbox fixture JSON worker
	  interaction_evidence_check.py  v0.10.2 交互证据单独检查工具
	  precommit_verify.py        当前本地 diff 提交前验证工具
	  pg_evidence.py             v0.48 显式只读 PostgreSQL 数据证据工具
	  single_demand_trial.py     单需求真实开发试跑审查包
	  self_check.py                0.5 天游标版自测自审入口
	  ui_capture_template.py       v0.10.3C Playwright/Chrome UI 采集模板生成工具
run.py                         Web 启动入口
```

## 当前边界

### Stage F 本地单 Agent CLI

`python tools/task_manager.py local-agent --help` 提供五个 JSON-only 命令：`run`、
`status`、`retry`、`issue-confirmation`、`confirm-apply`。`run` 必须显式指定
`/private/tmp` 下的非默认 `--db-path`、`--knowledge-home`、任务合同、私有 worktree root、
`--allow-real-agent` 和一次性 authorization id；不会回退到正式 `data/harness.sqlite`。
合同只接受绝对、owner-only、无链接的现存普通文件；控制库按 no-follow inode 身份和
单链接约束逐次重验，正式/default DB 的硬链接别名也会在任何 SQLite open/schema/write
前拒绝。CLI 不把裸路径交给 SQLite：只从锚定 FD 读取镜像到 `:memory:`，提交后在同一
锁定目录内以 owner-only 临时文件、`fsync` 和原子 rename 持久化；`status` 使用不持久化的
query-only 镜像连接。正式 Manager 的默认 path-based 数据库入口不受这条 CLI 专用能力影响。
源仓存在
任何 staged、unstaged 或 untracked 改动时，`run` 在前置检查和 Worker 前 fail closed。
通过验证和独立 Reviewer 后只进入 `awaiting_human_confirmation`，确认前不修改原仓；确认
后只把已封存 patch 应用到本地原仓，不 commit、不 push、不写云效、不部署，业务数据库
永久只读。确认 token 只能从 stdin 输入，不能放进 argv。精确命令、状态、重试、确认和
恢复边界见 `docs/manager-runbook.md`。

### 当前验收口径（唯一状态源）

Task 5 最终验收及 Task 6 离线集成已通过；2026-08-14 又完成一次真实 bundled Codex 的
无 remote 临时 Git fixture 验收。下表是 Harness 个人版的唯一当前状态口径；其他文档只
引用本表。不得把“代码已具备”或 fake fixture 的结果写成真实 Agent、GitLab/云效或业务
验收，也不得把一次真实 fixture 写成稳定的日常业务交付能力。

| 优先级 | 能力 | 当前状态与可证明范围 |
| --- | --- | --- |
| P0 | 修复复盘与规则学习闭环 | 已验收；仅代表临时 Git fixture + fake Worker/Reviewer 的离线规则、状态、审计与人工确认阻断闭环。它不代表真实 Codex 已稳定改码。 |
| P1 | 真实 Codex 临时无 remote fixture | 已验收；在一次无 remote 临时 Git fixture 中实际运行 bundled Codex，完成改文件 → 测试 → 独立 Reviewer → 人工确认 → 本地 apply。源仓未新增 commit，且只发生本地文件应用。它仅证明本机单次真实 Agent 闭环，不代表真实 HIS 业务或 GitLab/云效交付。 |
| P1 | Flux-OPD-Lite 结构化经验闭环 | `flux_lite_offline_verified`；v72 新增 append-only Reviewer 意见/候选经验表，支持多 Reviewer 一致性聚合、冲突降权、精确任务上下文匹配和 Worker/Reviewer 固定检查注入。单 Reviewer、冲突意见及医保/收费/退费/结算高风险意见只留证据，不自动晋升。 |
| P1 | 受预算的自动多轮修复与失败重规划 | `flux_lite_offline_verified`；显式 `LocalAgentRunner.auto_repair(run_id, max_rounds=...)` 复用现有三次尝试预算，每轮失败后由 Harness 递增 `plan_version`、重新注入根因检查，模型只能执行新决定；遇到通过只进入人工确认，高风险任务首轮前暂停。当前证据是 fake Worker/Reviewer 离线回归，不代表真实模型连续稳定性。 |
| P1 | 角色 → capability → Skill/MCP 统一路由 | 已实现并接入动态计划；14 个角色的允许工具均有唯一 capability/provider 与 canonical Skill/MCP 归属，四项任务意图缺失时受控执行链 fail closed。 |
| P2 | GitLab / 云效受控交付 | 未验收；本地规则学习、审核和确认都不授权创建分支、commit、push、MR、评论或任何远程写入。 |

#### 2026-08-18 数据库与 Git 能力闭环状态

这部分是当前可执行边界，不能把“已登记”当成“已对真实业务仓执行”：

| 能力 | 当前状态 | 已验证证据 | 明确未开放/未验证 |
| --- | --- | --- | --- |
| PostgreSQL 业务证据 | `real_readonly_verified` | 通过只读测试 Profile 连接 PostgreSQL，并成功读取元数据解析后的 `df_zhushuju.gy_shoufeixm`；连接使用 `default_transaction_read_only=on` 和语句超时 | 永久禁止 DML/DDL、事务写入、锁、过程和 COPY；未验证生产库 |
| Git 代码证据 | `manager_orchestrated_available` | `CodeEvidenceService` 的 diff、源码、搜索、历史、本地验证和审核链路专项测试通过 | 直接从插件运行任意 Git 命令会 fail-closed；没有仓库范围时必须阻断 |
| Git 状态/日志/差异 | `local_read_verified` | 临时仓只读 Git provider 测试通过 | 不代表真实 HIS 仓运行时结果 |
| reset/cherry-pick/merge | `local_fixture_execute_verified` | 临时 Git 仓已验证一次性授权、固定 argv、HEAD/分支/工作区预检、reset/cherry-pick/merge 执行和读回；冲突只阻断，不自动解决 | 仍未对真实 HIS 仓执行；必须先由 Harness 生成精确计划并单独确认 |
| remote pull/push 计划 | `plan_only_needs_remote_evidence` | 合法 push 计划可生成，并明确要求远端读回；force push 和不安全 ref 被拒绝 | 远端 pull/push 仍未执行；没有真实远端写入验收 |

离线规则和 Flux-Lite 候选通过后，只能向下一次本地 Worker/Reviewer 提供固定、不可执行的
检查关注点。它不会读取或保存提示词、diff、凭证或用户摘要原文，不会自行扩大改动范围，
不会把单次 Reviewer 结果当成共识，也不会成为外部写入授权。

Flux-Lite 这里是工程编排层对应物，不是论文意义上的 logits/梯度更新：Harness 保存的是
Reviewer 结构化意见的哈希证据、根因枚举、固定动作和冲突/权重元数据；只有至少两名独立
Reviewer 在同一任务范围内完全一致的 `trial` 候选，才允许作为固定检查注入。真实模型、
真实 HIS 业务质量、跨任务长期稳定性仍需单独验收。

当前离线回归使用 fake Worker/Reviewer，只证明本地合同和规则闭环，不能替代真实模型证据。
真实 bundled Codex 已在一次性无 remote fixture 中单独验收；登录/网络、重复运行稳定性、
自动修复，以及真实 HIS 页面、接口、数据和生产业务仍需分别验收。

- v0.56 model DAG 只编排 v0.55 离线适配器，候选交接严格限制在同一 schedule；它会推进 simulated schedule，但不会读取凭证、调用网络或晋升 current contract，也不代表真实多智能体已运行。
- v0.55 model invocation runtime 只允许 `mock/replay`，只处理非 Git fixture root 内的结构化 cassette。它不会读取模型凭证、调用网络、推进 schedule 或晋升契约；输出不代表真实模型质量、代码改动、测试或业务验收。
- v0.57 provider smoke 是独立的固定单节点连通性测试。只有用户逐次明确授权并同时开启凭证/网络双开关才会读取配置和发出一次请求；它不接入 v0.56 DAG、不执行工具、不重试，也不把连通性结果当作业务有效证据。
- v0.54 mock-agent 只根据已签名 context envelope 生成 deterministic fixture，并复用 v0.53 固定 worker；不接受任意 prompt、command、worker path 或 env。它可以推进 dry-run schedule，但不能晋升业务契约，也不代表真实智能体、代码修改、测试或业务验收完成。
- v0.53 executor 会启动一个真实本地 Python 子进程，但 executable 和 worker 文件均由 Harness 固定，不接受任意命令或环境；它只转换脱敏 fixture JSON。该进程调用不代表真实模型、业务源码工具或 worktree 已开放。
- v0.52 runtime 只有 Task Manager 三个显式命令可以调用；它只读取已登记计划和 dry-run checkpoint、解析标记目录内的脱敏 JSON，并保存 fixture 候选契约。不会运行真实智能体、模型、shell、源码工具、worktree、PG、Git 或外部系统，也不会把候选契约晋升为 current。
- v0.51 scheduler 只有 Task Manager 三个显式命令可以启动或推进；`running_simulated`/`succeeded_simulated` 均不执行真实智能体，不生成真实契约，不调用模型、工具、worktree、数据库查询或外部系统。
- v0.50 动态计划登记仅由 Task Manager 三个显式命令触发；它只写本地计划/契约历史并生成只读恢复预览，未接入普通 workflow。真实 DAG 调度、模型节点执行、独立上下文、worktree 并行和人工闸口执行仍未开放。
- v0.49 dynamic-plan 默认关闭且未接入普通 workflow；显式启用后也只生成评分、团队、DAG 和交接契约，不调用模型、不修改代码、不连接数据库、不执行任何外部写入。
- v0.48 PG 数据证据是独立显式能力；普通 workflow 不调用它，`plan` 不创建驱动或连接，只有用户明确要求并使用 `execute` 才可查询测试/开发 Profile。候选歧义、SQL 不安全、驱动缺失或超时均立即停止且不重试。
- 默认不写云效；只有显式 `write` 模式和双开关确认才进入写入执行器。
- 云效真实写入已具备代码层执行器，但本仓库默认自测只使用 fake transport，不对真实云效对象做 live write。
- v0.8.7 真实业务任务仍不允许真实状态流转、负责人流转、迭代调整或关闭任务；这些动作必须在后续专用测试任务上验证状态机后再开放。
- v0.8.2 写入凭证不进入项目文件、报告或数据库；只记录凭证来源摘要和动作审计。
- 不接钉钉真实消息。
- readonly 默认不创建 Git worktree、不修改业务代码、不执行测试命令。
- worktree 模式先修改 Harness 创建的临时 Git worktree；成功后在原业务目录仍干净且 apply check 通过时合入 final.diff。
- fullstack-worktree 模式会同时管理多个业务仓库的临时 worktree；全部项目验证和 apply-check 通过后才合入原业务目录。
- precommit-verify 模式只验证当前本地 diff，并生成提交前审查产物；不会修改业务 diff、不会提交、不会推送、不会真实流转云效。
- v0.10.2 只生成方法级交互测试计划、方法执行结果和 UI 证据 manifest；不会自动打开业务页面、不会自动验收真实收费/结算流程。
- v0.10.3A 只执行用户显式传入的 `--method-test-command` 并解析 stdout JSON；不会自动生成测试代码、不会自动打开真实业务页面、不会自动采集真实 UI 截图。
- v0.10.3B 只执行用户显式传入的 `--ui-capture-command` 并解析 stdout JSON；不会自动生成 Playwright 脚本、不会自动处理 HIS 登录态、不会自动判断真实业务验收通过。
- v0.10.3C 只生成 Playwright/Chrome capture 模板、env 示例和人工验收记录模板；不会保存真实 storageState、cookie、密码或 token，也不会替代人工准备测试账号和测试数据。
- v0.10.4 可以验证已有本地 diff 的目标范围；如果同仓库存在白名单外未提交改动，会阻止直接提交和云效交付评论，直到人工隔离或处理提交范围。
- v0.10.10 只读取 Task Manager 本地数据库和已有产物索引生成 dashboard/sample set；不会自动复跑验证、不会新增真实样板、不会打开业务页面。
- v0.11 只读取单个 Task Manager 任务并生成本地工作台；复跑命令仅供复制，不会自动执行。
- v0.12 只生成本地静态 HTML 工作台入口和索引文件；页面链接和复跑命令仅供人工打开或复制，不自动执行。
- v0.13 只根据 Task Manager 数据库和已登记产物推导 run 对比与 warning；warning 不会自动触发复跑、提交或云效动作。
- v0.13.1 只修复 precommit 包装层 patch 读取，不改变业务 diff、不放宽行为门禁、不自动补交互证据。
- v0.14 只增强本地静态 workspace 的可读性和前端筛选；搜索和筛选仅隐藏/显示已导出的表格行，不读取远端、不复跑命令、不写云效。
- v0.15 只校准需求理解和输出确认卡；它不读取额外私有页面、不自动拆云效子任务、不自动改码、不自动提交、不写云效。
- v0.16 只把已存在的确认卡纳入 Task Manager 只读索引和静态 HTML 展示；不会自动生成新确认卡、不会打开业务页面、不会复跑验证、不会写云效。
- v0.17A 只登记已知 diff 并生成回滚 dry-run 计划；不会自动执行回滚、不会修改业务仓库、不会提交、不会推送、不会写云效。
- v0.17B 只增强静态 HTML/JSON 工作台的详情展示和本地证据预览；不会发起浏览器自动化、不会自动复跑命令、不会执行回滚、不会读取或写入云效。
- v0.18 只比较本地 workspace JSON 快照并生成导出索引；不会重新验证产物、不会读取远端、不会自动复跑命令、不会执行回滚、不会写云效。
- v0.19 只归档和比较本地 workspace JSON 快照并展示证据趋势；不会读取远端、不会自动复跑命令、不会执行回滚、不会提交推送、不会写云效。
- v0.20 只增强静态 HTML 信息结构和本地快照摘要展示；不会读取远端、不会自动复跑命令、不会执行回滚、不会提交推送、不会写云效。
- v0.21 只增强静态 HTML 可读性、空态/错误态说明和离线审查包索引；不会读取远端、不会自动复跑命令、不会执行回滚、不会提交推送、不会写云效。
- v0.22 只新增 Rule Pack、Profile、Credential Store 只读摘要和显式配置页；不会保存真实 token、不会改变旧命令默认行为、不会自动写云效/TAPD、不会自动 commit/push、不会自动状态流转。
- v0.23 只新增本地需求来源归一化；不会联网读取 TAPD、不会改变旧 `--yunxiao-read` 路径、不会写外部系统、不会保存真实 token。
- v0.24 只在显式传入 `--requirement-evidence-file` 或已有 output_dir 存在 `requirement_evidence.json/md` 时展示需求来源证据；不会自动读取远端需求系统、不会写云效/TAPD、不会保存真实 token、不会改变旧命令默认输出。
- v0.25 只在显式传入 `--include-preview` 或 `--include-config-preview` 时生成配置预览和 provider 模板草案；不会读取远端、不会测试 provider 连通性、不会保存真实 token、不会写云效/TAPD、不会自动应用提交/评论/状态规则。
- v0.26 只在显式传入 `--include-share-validation` 或 `--include-config-share-validation` 时生成分享包校验和本地覆盖策略；不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会读取或写入远端。
- v0.27 只在显式传入 `--include-import-draft` 或 `--include-config-import-draft` 且提供 `--draft-output-dir` 时生成配置导入草案；草案只写入用户选择目录，默认不覆盖同名文件，不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号。
- v0.28 只在显式传入 `--review-import-draft` 或 `--include-config-import-review` 且提供 `--draft-input-dir` 时回读配置导入草案；回读只生成校验和表单预览，不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号。
- v0.29 只在显式传入 `--include-template-index` 或 `--include-config-template-index` 且提供 `--draft-input-dir` 时索引配置模板；索引只生成 profile 预览、差异摘要和文件列表，不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号。
- v0.30 只在显式传入 `--include-config-wizard` 且提供 `--draft-input-dir` 时生成配置向导；向导只聚合配置摘要、预览、分享校验、回读和模板索引，不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号。
- v0.31 只增强配置向导只读展示：步骤筛选、阻断摘要、命令复制 target 和空态说明；不会执行命令、不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号。
- v0.32 只在显式配置向导 workspace 中生成配置审查包索引；索引只汇总本地配置产物、复跑命令和人工确认项，不会执行命令、不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号。
- v0.33 只增强配置审查包只读展示：文件筛选、待确认分组和交接摘要；不会执行命令、不会应用配置、不会写入 `~/.his-harness`、不会保存真实 token、不会测试远端账号。
- v0.34 只新增显式分层配置解析、来源追踪和只读报告；默认命令不启用解析器，不写个人/团队配置，不测试远端账号，不执行外部写入。
- v0.35 新增 `core-closure-trial`：低风险基础需求必须先通过结构化需求契约和工程交接，才允许受控 worktree 改码；独立 diff 审查通过后才允许进入本地应用或人工代码审查和业务验收前。
- v0.37 本地优先自动应用：`core-closure-trial` 的 worktree、专项验证和独立 diff 审查均通过后，默认自动应用至本地原业务目录；`--review-only` 可显式禁用本地应用。不会创建分支、提交、推送、合并 RC、发布或写云效/TAPD。
- v0.38 自动本地路线：`auto-local` 将低风险日常需求直接路由到核心闭环，并记录实际路线；可推导的项目、白名单和专项验证会自动复用，证据不足、需求不清或高风险时会快速阻断，不执行固定九步骤报告链，也不会执行远端 Git 或外部系统写入。
- v0.36 已完成 DFHIS-31557 真实源码 review-only 回放：支持显式默认值规则、白名单源码上下文和不相交脏文件下的受控 worktree；Task Manager 可按 `core-closure-trial` 归档通过的独立 diff 审查并登记修改历史/只读回滚计划；原业务仓库不会因该回放被自动写入。
- single-demand-trial 模式一次只处理一个云效需求，会尝试 worktree 受控改码、验证和本地合入，但仍不自动提交、不推送、不发布；真实云效只允许 comment-only。
- worktree 成功或失败后默认删除临时目录并 prune；不会长期堆积 `/tmp/his_harness_worktrees`。
- v0.7.4 worktree 模式在证据不足时会阻断 patch；不会把候选根因当成业务结论。
- review-worktree 模式只审查已有提交并在 base/head 临时 Git worktree 中验证，不生成 patch、不修改原业务目录。
- 验证命令必须显式传入，Harness 不自动安装依赖、不自动猜测 build/test。
- 不自动编译、提交、发布。
- 不触发 Jenkins、K8s rollout 或远程 CI/CD。

当前优先级是核心闭环的人工审查和泛化验证：按明确授权应用已审查 diff，并用更多低风险需求验证“需求规则 -> 受控 patch -> 专项验证 -> 独立 diff 审查 -> 人工业务验收”的完整证据链。配置真实写入、团队分发、UI 产品化、部署、远端账号测试、云效/TAPD 写动作、自动回滚、状态流转、负责人、迭代、关闭和发布继续冻结。

## 云效事务设计

云效不是给 AI 一个 token 让它自由操作，而是按流程机做受控事务编排。

当前已经建好的基座：

- 实体：迭代、需求、缺陷、子任务、负责人、状态、评论、附件、分支、commit、MR、发布单。
- 动作：`read`、`comment`、`upload_attachment`、`assign`、`transition`、`update_iteration`、`update_service_change`、`create_task`、`link_artifact`、`close`。
- 默认权限：只开启 `read`，所有写动作关闭。
- 审计要求：写动作必须绑定 `run_id`、证据、原因、风险等级、幂等键和模型信息。
- 高风险规则：医保、结算、收费、报表、对账、核算、优惠/减免类需求不能自动关闭；附件、负责人、状态、迭代和服务变更需要人工闸口。
- 生命周期建议：分析不清楚建议“待澄清”，验证失败建议回到“开发中/待修复”，高风险建议“待人工审核”，全部通过只建议评论和产物关联，不自动完成或关闭。

v0.8.2 已实现受控写入执行层。默认仍不写；真实写入必须使用专用测试对象验证后，再按项目逐个动作开放。
