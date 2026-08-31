# Harness 本地真实 Agent 闭环设计

## 目标

先让单个用户在一台 Mac 上稳定跑通一次真实改码闭环，再讨论团队化。首个版本必须完成：读取一次性任务合同、创建隔离 worktree、启动真实 Codex Agent、持续保存运行事实、执行确定性验证、启动独立只读 Reviewer、生成 Patch/Diff/报告、等待人工确认，并在确认后只应用到本地目标仓库。

本阶段不建设 SSO、RBAC、多租户、分布式队列、Worker 集群、配额计费或新的 Manager 页面。

## 当前基线

- 正式 Harness 位于 `/Users/lym/WorkCode/ai/Harness`，本阶段继续冻结，不直接修改或迁移。
- 实现只发生在现有 linked worktree：`/Users/lym/Documents/Codex/2026-07-27/xia/work/his-harness-plugin-migration-base/.worktrees/implementation/Harness`。
- 当前 schema 为 v68；正式 `data/harness.sqlite`、WAL、SHM 和备份不得打开、迁移、checkpoint、恢复或删除。
- 现有 `OfflineModelDagRuntime` 仅支持 mock/replay，`REAL_MODEL_RUNTIME_FROZEN=True` 继续约束普通真实 DAG。
- 已有 worktree、Patch、验证、审计和一次性授权组件可以复用，但不存在正式 `AgentRunner`。

## 方案选择

### 采用：受控 Codex CLI Worker

使用本机 `/Applications/ChatGPT.app/Contents/Resources/codex` 的非交互 `codex exec --json` 作为第一种真实 Worker。Harness 不重新实现模型工具循环，只负责运行合同、进程边界、状态持久化、验证、审核和人工确认。

固定约束：

- 只允许解析后的固定 Codex 可执行文件，不接受用户提供的可执行路径。
- 使用参数数组启动，禁止 `shell=True`。
- Agent cwd 只能是本次 Harness 创建并验证的 worktree。
- 使用 `--sandbox workspace-write`、`--approve-for-me`、`--ignore-user-config` 和 JSONL 事件输出。
- 禁止 `--dangerously-bypass-approvals-and-sandbox`。
- 首轮不允许 Agent 访问业务数据库，不向 Prompt 注入 Provider 凭证。
- Git push、云效写、提交、合并、部署和数据库修改始终不在 Agent 权限内。

### 暂不采用：自建 API Tool Loop

它需要重新实现工具协议、上下文管理、审批、恢复和代码执行，无法最快证明真实闭环。

### 暂不采用：独立容器 Worker 平台

它适合后续团队化，但会把首个可运行版本扩大为队列、容器、租户和服务治理项目。

## 一次性任务合同

本地 CLI 只接受 JSON 合同文件，不接受任意 shell 命令字符串。合同 `his-local-agent-task.v1` 包含：

- `task_key`：非敏感、稳定、格式受限的任务标识。
- `project_path`：本地 Git 仓库绝对路径。
- `request`：经过敏感信息检查的需求文本。
- `allowed_paths`：允许修改的相对路径前缀，不能为空。
- `verification_commands`：参数数组列表，例如 `["python", "-m", "unittest", "-q"]`；禁止 shell 元字符语义。
- `acceptance_criteria`：明确、非空、可审查的验收条件。
- `timeout_seconds`：有上限的单次 Worker 超时。

合同创建时绑定规范化 JSON 哈希、源仓库 root inode、`.git` inode 和初始 HEAD。任何字段、仓库身份或 HEAD 变化都要求创建新合同，不能复用旧确认。

## 组件边界

### `LocalAgentRunRepository`

只管理 Harness 自身的追加式运行事实：

- `agent_runs`：任务合同哈希、项目身份、当前状态和最终结论。
- `agent_attempts`：每次 Worker 尝试、进程状态、开始/结束时间和安全错误码。
- `agent_run_events`：单调序号的追加式事件；禁止 update/delete/replace。
- `agent_artifacts`：只保存安全摘要、文件相对路径、SHA-256、大小和类型，不把任意原始模型输出直接写入数据库。

schema 从 v68 升至 v69，只允许在显式临时数据库上验证。进程发现历史 `running` attempt 且没有存活 Worker 时，将其标记为 `interrupted`；恢复动作创建新 attempt，不伪装续接同一进程。

### `CodexCliWorker`

负责单个受控子进程生命周期：

- `start()`：固定 argv、最小环境、独立进程组、stdin Prompt、stdout JSONL、stderr 有界读取。
- `poll()`：产生 heartbeat 和解析后的安全事件。
- `terminate()`：超时、取消、输出超限或 Harness 异常时无条件清理整个进程组并回收子进程。
- `result()`：只返回退出码、安全状态、会话标识、安全摘要和计量字段。

JSONL 单行和累计输出均有硬上限。解析失败、未知事件、敏感内容或输出超限都 fail closed；原始 secret、Authorization、Bearer、Cookie 和环境变量不得进入数据库、日志、异常或最终报告。

### `LocalAgentRunner`

负责编排一次本地运行：

1. 校验合同、仓库和一次性本地执行授权。
2. 使用已有 worktree 生命周期组件创建隔离 workspace。
3. 创建 run/attempt 后启动 `CodexCliWorker`。
4. Worker 结束后复核仓库身份、允许路径、Git 状态和 diff。
5. 依次执行合同中的参数数组验证命令。
6. 验证通过后启动第二个只读 Reviewer Worker。
7. Reviewer 必须返回结构化 `approved` 或 `changes_requested`；后者不得进入确认阶段。
8. 生成 diff、patch、验证报告、审核报告和 manifest。
9. 将 run 置为 `awaiting_human_confirmation`。
10. 人工使用一次性确认令牌后，调用既有本地安全应用事务；不 commit、不 push。

### `LocalAgentReviewer`

Reviewer 是独立 Codex 调用，cwd 为同一 worktree，但 sandbox 为 `read-only`。输入只包含任务合同、最终 diff、验证摘要和需要检查的风险边界。输出必须符合固定 JSON Schema；自由文本只作为有界、脱敏的说明字段。

## 状态机

允许状态：

```text
created
  -> workspace_ready
  -> worker_running
  -> verifying
  -> reviewing
  -> awaiting_human_confirmation
  -> locally_applied
```

失败分支：

```text
worker_running -> interrupted | failed_worker | cancelled
verifying      -> failed_verification
reviewing      -> changes_requested | failed_review
awaiting_human_confirmation -> confirmation_expired | locally_applied
```

状态只能按允许边迁移。终态不能重新写回 running。恢复只允许从 `interrupted`、`failed_worker`、`failed_verification` 或 `changes_requested` 创建新 attempt，并继续使用已验证的同一 worktree；合同、仓库身份或 HEAD 改变时必须新建 run。

## Prompt 合同

Worker Prompt 由 Harness 固定模板生成，只包含：任务说明、允许路径、验收标准、验证命令的显示形式和安全边界。明确要求：

- 先检查现状，再做最小修改。
- 不修改允许路径之外的文件。
- 不 commit、不 push、不访问云效、不修改数据库、不部署。
- 不读取或打印凭证。
- 完成后只报告改动、验证建议和残余风险。

用户不能直接替换 system/safety 部分，也不能注入额外工具或 callback。

## 本地纵向验收

### 第一层：真实 Worker 机制验收

使用 Harness 创建的临时 Git fixture：一个有明确失败测试的小型 Python 项目。真实 Codex Worker 必须修改唯一允许文件并使确定性测试通过；Reviewer 独立批准；Harness 生成完整 artifacts；人工确认后 Patch 应用到 fixture 原仓，且没有 commit/push/外部写。

这层证明真实模型、工具调用、worktree、验证、审核、确认和本地应用链路可运行，但不代表 HIS 业务验收。

### 第二层：真实低风险项目验收

第一层通过后，由用户指定一个低风险真实仓库和清晰任务。继续禁止远程交付，完成同样闭环并由用户核对实际结果。只有第二层通过，才能把“个人本地真实闭环”标记为完成。

## 错误处理与恢复

- Codex 不存在或版本不满足：创建 run 前阻断，不读取项目内容。
- 登录/网络/模型失败：attempt 记录稳定错误码，保留 workspace，允许人工重试。
- Harness 重启：历史 running attempt 变为 interrupted，新 attempt 可继续，不丢失 diff/artifact。
- Worker 越界改文件：立即阻断，Reviewer 和本地应用均不运行。
- 验证失败：保留安全摘要和 diff，允许新 attempt 修复。
- Reviewer changes requested：保留意见，创建新 attempt 后重新执行完整验证与审核。
- 人工确认过期或被复用：本地应用为零。

## 安全和数据边界

- 业务数据库永久只读；本阶段不生成或执行数据库变更动作。
- Harness 运行库只存受控状态和安全摘要。
- 默认正式 DB/WAL/SHM/backup 继续冻结；测试必须在进程 import 前注入临时 `HARNESS_DB_PATH`。
- 任何真实模型调用都必须由显式本地运行命令和一次性授权触发，不通过页面后台自动启动。
- 不将 `REAL_MODEL_RUNTIME_FROZEN` 全局改为 `False`；为本地单 Runner 建立独立、狭窄的 activation gate。
- 首版不支持并行 Worker；同一项目同时最多一个 active run。

## 验收门禁

代码级完成必须满足：

- 所有新状态机、仓储、进程清理、越界写入、敏感输出、超时、恢复和确认反例测试通过。
- fake Worker 测试覆盖所有失败路径，并先完成 RED→GREEN。
- 在明确授权后执行一次真实 Codex fixture 闭环，保存运行 ID、事件、diff、验证、review 和本地应用证据。
- 正式 Harness 和默认数据库元数据前后不变。

不得用单元测试、fake Worker 或模型 smoke 代替真实 fixture 闭环。

## 后续但不属于本阶段

- 多 Worker、持久化任务队列、依赖 DAG、自动重试策略和资源调度。
- SSO、RBAC、用户/团队/项目/仓库隔离。
- PostgreSQL、分布式锁、容器 Sandbox、集中式密钥服务。
- Manager 实时页面、WebSocket、团队审计看板。
- Git push、PR/MR、云效评论/状态、CI、部署和发布。
