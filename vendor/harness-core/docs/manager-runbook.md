# Harness Manager 运维手册

本手册用于部署和操作阶段 B/C 的 Manager 受控流程。它不授权正式迁移、真实网络调用、
外部写入或 HIS 业务验收；这些动作仍需在目标阶段获得明确授权。

## 能力边界

| 层级 | 能证明什么 | 不能据此声称什么 |
| --- | --- | --- |
| code-ready | 代码和合同已存在 | 已配置、已测试或已连通 |
| configured | Profile 完整且凭证状态为 configured | 凭证可用或 Provider 已连接 |
| locally-tested | 临时 Manager DB、fake adapter、临时知识目录测试通过 | 真实云效、GitLab、模型或 HIS 已验证 |
| externally-verified | 指定测试目标的单个动作已执行并回读 | 其他 Provider 或其他写动作也可用 |
| business-accepted | 指定 HIS 环境、操作者、数据和场景证据经审核接受 | 生产环境或未覆盖场景已通过 |

数据库当前能力只包含结构读取、受限单条只读查询和人工 SQL 草案，不提供 DDL/DML、事务、
存储过程、草案执行、通用 SQL 或任务队列入口。数据库修改和删除默认绝对禁止；只有用户明确授权
精确对象、操作、条件和影响范围后才可进入独立变更设计，删除、`DROP`、`TRUNCATE` 还必须
单独绑定破坏性范围。当前没有数据库写 executor。

## 统一意图与角色路由

受控任务在进入 Worker、Reviewer、MCP 或 Provider 前，必须收集并固定四项意图：
`background`、`goal`、`scenarios`、`desired_outcome`。随后由
`config/role_capability_skill_matrix.json` 将 `ROLE_CATALOG` 中的角色、允许工具、
capability/provider 和 canonical Skill/MCP 做一对一解析。

缺少任一项时返回 `task_context_incomplete:*`，不执行外部动作；旧动态规划调用仍可
生成兼容性计划，但会带 `task_context_incomplete` warning。`his-code-evidence`
负责只读源码、搜索、diff、history、本地验证和 review；`harness-flux-lite`、
`harness-auto-repair` 和角色路由是 Harness 内部能力，不能交给外部 CapabilityRuntime。
完整登记与验证命令见 [`docs/role-capability-skill-matrix.md`](role-capability-skill-matrix.md)。

## 本地单 Agent CLI 闭环

这组命令是 Manager 之外的窄入口，只串联本地任务合同、隔离 worktree、Codex Worker、
确定性验证、只读 Reviewer 和一次性人工确认。它不会新增 HTTP/UI，也不会 commit、push、
创建 MR、写云效、部署或访问/修改业务数据库。`REAL_MODEL_RUNTIME_FROZEN=True` 继续约束
原有 DAG；只有显式 `local-agent run --allow-real-agent` 才能进入这条单次本地链路。

### Agent 后端与宿主选择

Harness Core 不要求 Codex CLI 才能启动。默认后端是 `host-bridge`；终端、Codex App、
Codex CLI 和 DeepSeek-Harness-Desktop 都通过同一份宿主/后端合同接入。当前注册表和
只读发现入口为：

```bash
python tools/harness_agent_bridge.py describe
```

如需让这条 local-agent 链路使用本机 Codex CLI 或 Codex Desktop 随附的 App Server，必须明确绑定：

```bash
python tools/task_manager.py local-agent run \
  --agent-backend codex-cli \
  --db-path "$HARNESS_DB_PATH" \
  --knowledge-home "$HIS_KNOWLEDGE_HOME" \
  --contract /private/tmp/his-local-agent-task.json \
  --worktree-root "$worktree_root" \
  --allow-real-agent \
  --authorization-id local-run-20260811-001
```

`codex-cli` 仍受原有可执行文件签名、协议、超时和清理门禁约束；签名失败只阻断该后端。
`codex-app-server` 固定使用 `codex app-server --stdio`、ephemeral thread、worktree 写入和
网络关闭；它同样是可选 backend，不会在默认路径启动。可将上述命令中的 backend 替换为
`codex-app-server`，在隔离临时 worktree 内执行真实 smoke。
其 Reviewer 返回值始终作为不可信 JSON 重新经过 Harness 的 schema、字段与 review-hash
验证；当前 Desktop runtime 不接收 `outputSchema`，因此不会把该参数下传给 App Server。
`host-bridge` 没有宿主 handler 时会返回 `worker_backend_unavailable` 并 fail closed，
不会偷偷启动其他模型或提升权限。后端选择会写入运行事件，retry 不能用另一个显式后端
替换原 run；正式桌面宿主接入前，`describe`/`validate-request`/`negotiate` 只代表合同
已准备，不代表真实 Agent 已运行。

宿主实现可复用 `app.host_adapter.HostAdapterSession`：宿主注入自己的 `(request, sink) ->
AgentBackendResult` handler，JSONL 与进程内调用共用同一套验证和安全错误边界。没有真实
handler 时，`host-bridge` 只会返回 `worker_backend_unavailable`。

首次操作必须使用一次性临时目录，不得指向 `data/harness.sqlite`、正式 Manager 数据库或
正式知识库：

```bash
control_root=$(mktemp -d /private/tmp/his_harness_stage_f_control_XXXXXX)
worktree_root=$(mktemp -d /private/tmp/his_harness_stage_f_cli_XXXXXX)
chmod 700 "$control_root" "$worktree_root"
export HARNESS_DB_PATH="$control_root/harness.sqlite"
export HIS_KNOWLEDGE_HOME="$control_root/knowledge"

python tools/task_manager.py local-agent run \
  --db-path "$HARNESS_DB_PATH" \
  --knowledge-home "$HIS_KNOWLEDGE_HOME" \
  --contract /private/tmp/his-local-agent-task.json \
  --worktree-root "$worktree_root" \
  --allow-real-agent \
  --authorization-id local-run-20260811-001
```

合同可从 `tests/fixtures/local_agent_task.json` 复制；必须把 `__PROJECT_PATH__` 和
`__PYTHON__` 替换为本次临时 Git fixture 的真实绝对路径。合同只接受 argv 数组形式的
验证命令，不接受 shell 字符串。合同必须是 owner-only（`chmod 600`）的绝对、规范、
现存普通文件；相对路径、符号链接、硬链接和读取期间被替换都会在创建控制库前拒绝。
源仓 HEAD 必须仍等于合同装载时的 HEAD，且 staged、unstaged、untracked 均为空，否则
不会执行授权前置检查、创建 run/worktree 或启动 Worker。`run` 的安全 JSON 会给出
`run_id` 和状态：

- `failed_workspace`、`interrupted`、`failed_worker`、`failed_verification`、
  `changes_requested`：修复对应原因后可执行 `retry`，最多三次 attempt；
- 需要显式自动闭环时，可使用 `auto-repair`，必须传入 `--max-rounds`，最多执行剩余预算内
  的两轮；它只重复隔离 worktree → Worker → 验证 → Reviewer，不会 apply、commit、push
  或部署。Reviewer 通过后仍停在 `awaiting_human_confirmation`；高风险任务会在首轮前暂停；
- `awaiting_human_confirmation`：验证和 Reviewer 均通过，但原仓尚未修改；
- `locally_applied`：一次性确认后 patch 已应用到原本地仓库，HEAD 仍不变化；
- 其他终态或不满足恢复条件时，`retry` 会稳定拒绝，不能绕过状态机。

查询和恢复命令：

```bash
python tools/task_manager.py local-agent status \
  --db-path "$HARNESS_DB_PATH" --run-id 1

python tools/task_manager.py local-agent retry \
  --db-path "$HARNESS_DB_PATH" --worktree-root "$worktree_root" --run-id 1

python tools/task_manager.py local-agent auto-repair \
  --db-path "$HARNESS_DB_PATH" --worktree-root "$worktree_root" \
  --run-id 1 --max-rounds 2
```

`status` 只读取现有运行事实，不初始化、迁移或更新控制库。控制库必须是私有目录中
owner-only 的单链接普通文件；每次命令都会按 inode/link count 重验，默认/正式库及其
硬链接别名均在任何 SQLite open、schema 或 run 操作前拒绝。CLI 命令全生命周期持有私有
控制目录锁；SQLite 只连接从锚定 FD 反序列化的 `:memory:` 镜像，不再按用户路径重新打开。
写命令提交时先序列化到同目录 `0600` 独占临时文件，`fsync` 后原子替换锚定 leaf；崩溃前
旧镜像保持完整，leaf/父目录身份变化则停止且不删除、恢复或覆盖未知数据库。`status` 的
镜像连接为 query-only 且不持久化。确认分两步；签发命令只在当次 JSON 输出中
显示一次 token，`confirm-apply` **仅接受 stdin 中一行 token**，没有 `--token` 参数：

```bash
issue_json="$(python tools/task_manager.py local-agent issue-confirmation \
  --db-path "$HARNESS_DB_PATH" --worktree-root "$worktree_root" \
  --run-id 1 --requested-by local-user)"
printf '%s\n' "$issue_json"
confirmation_token="$(printf '%s' "$issue_json" | \
  python -c 'import json,sys; print(json.load(sys.stdin)["confirmation_token"])')"

printf '%s\n' "$confirmation_token" | \
python tools/task_manager.py local-agent confirm-apply \
  --db-path "$HARNESS_DB_PATH" --worktree-root "$worktree_root" \
  --run-id 1 --requested-by local-user
unset confirmation_token
```

令牌过期、复用、操作者不一致、合同/HEAD/patch/review/目录身份变化都会零应用拒绝。发生
`local_agent_apply_recovery_required` 时保留原控制库、worktree 和同一 token，先按状态
证据恢复；不要删目录、重建数据库或重新初始化掩盖现场。

### 人工发现问题后的复盘

当人工审核、联调或本地验证发现本次改动不正确时，可将**一行、UTF-8、≤4 KiB、调用者
拥有且仅 owner 可读写的普通文件**作为受控纠正摘要提交。文件必须是绝对路径，不得是
symbolic link 或 hard link；命令只读取一次并只持久化其 `sha256:<hash>`，绝不输出或保存
摘要原文、摘要文件路径、任意 JSON、shell 片段、路径列表或新的规则文本。

```bash
python tools/task_manager.py local-agent record-correction \
  --database "$HARNESS_DB_PATH" \
  --run-id 1 \
  --worktree-root "$worktree_root" \
  --root-cause-kind implementation_defect \
  --summary-file /private/tmp/his_harness_stage_f_control_XXXXXX/correction-summary.txt
```

根因只能是固定枚举：`verification_failure`、`review_gap`、`path_coverage_gap`、
`contract_mismatch` 或 `implementation_defect`。入口只允许当前 attempt 处于
`failed_verification`、`changes_requested` 或 `awaiting_human_confirmation`；其他状态
（包括 `locally_applied` 和任何 apply 错误终态）一律拒绝，必须新建 run，不能把本命令
当作 Git rollback 工具。对 `awaiting_human_confirmation`，Harness 在同一受控事务内持久化
纠正证据，并令已签发 confirmation 失效、将 run 转为 `changes_requested`；同一纠正来源
重放不会新增 retrospective 或 artifact。若该受控事务失败，原 run 不转换，随后可用相同
命令安全重放。

这只会为后续当前任务 retry 生成受控的本地验证/Reviewer 关注点：**不修改原仓、不
commit、不 push、不创建分支/MR，也不写 GitLab、云效、部署或业务数据库。**

### Flux-OPD-Lite 结构化经验

当前实现是工程编排层的 Flux-Lite：外部独立 Reviewer 或插件可以提交同一任务范围内的
结构化 `ReviewerOpinion`，Harness 只保存意见哈希、固定根因/动作枚举和冲突元数据。
至少两名独立 Reviewer 完全一致时形成 `trial` 候选，下一次相同 `repository_kind:task_key`
才可得到固定检查；单意见、冲突意见和高风险意见只进入 append-only 证据，不会进入提示词。
它不是 logits/梯度更新，不读取或保存 prompt、diff、凭证或用户摘要原文。

规则状态只能是 `draft`、`active_current_task`、`trial`、`stable`、`suspended` 或
`retired`。`active_current_task` 仅匹配创建它的 run；正常规则至少需要三个不同 task key、
两个不同 workspace 的成功观察才可晋升 `stable`。收费、金额、退费、结算、医保、对账等
高风险标签永远停在 `trial`，不能自动晋升。任何匹配后的反例会立即 `suspended`，后续
Worker 和 Reviewer 都不再注入该规则，直到人工另行调查；系统不提供自动恢复或自动晋升。

运行状态仍以 `local-agent status` 的 run、attempt、artifact 和 event 为准。纠正后的
`changes_requested` 可在原有 attempt 预算内通过 `retry` 重新进入隔离 worktree；它不回滚
已应用代码，也不删除审计、重建控制库或重用旧 confirmation。发生中断、控制库身份变化、
源仓/HEAD/目录身份变化或确认失效时，停止在当前状态，保留证据并按既有恢复流程处理。

### 自我学习与“不重复犯已知错误”硬门禁

Harness 的学习不是让模型自由修改提示词，也不是把错误原文直接交给模型。每次人工纠正
都会以 `offline_import` 复盘记录持久化任务范围、根因枚举和摘要哈希；下一次兼容任务在
Worker 启动前必须读取这条记录，并自动加入 `reinspect_requirement_and_call_chain`、根因
专项检查和 `replan_before_model_execution`。因此旧方案不能原样重放，Harness 必须生成
新的 `harness_decision`，递增 `plan_version` 并标记被 supersede 的旧版本；模型/Agent 只
能执行这个新决定，不能自行重规划或扩大范围。

失败后的 `retry` 和显式 `auto-repair` 都走同一条新方案链；`auto-repair` 在剩余 attempt
预算内继续重试，遇到高风险任务仍暂停到人工闸口。已记录的同类问题会被确定性地再次拦截，
未知问题仍必须经过首次证据发现和验证，不能宣称所有未来错误都能自动预知。

关键审计信号是：`repair_learning_checks_matched`、`harness_decision_issued`、
`attempt_<n>.harness-decision.json`。若学习存储损坏、规则不匹配或新决定无法校验，Harness
在 Worker 启动前 fail closed，不允许模型拿旧方案继续执行。

当前验收范围与 P0/P1/P2 状态只以
[`README.md` 的“当前验收口径（唯一状态源）`](../README.md#当前验收口径唯一状态源)
为准。当前自动化测试仍主要使用 fake Worker/Reviewer。2026-08-14 已在无 remote 的一次性
Git fixture 中实际运行 bundled Codex、独立审核并确认应用成功，因此本机“真实单 Agent
fixture 闭环”已通过；Flux-Lite 和 `auto-repair` 目前仍只有离线 fake Worker/Reviewer
回归证据，不等于真实模型连续稳定运行或 HIS 业务验收。

本轮重新执行真实入口前置检查时，当前 bundled Codex `0.149.0-alpha.4.3` 的
`codesign --verify --strict` 失败，Harness 在 executable preflight 阶段以
`worker_executable_invalid` 阻断；没有绕过签名校验，也没有对真实业务仓启动模型。
因此当前证据仍是“历史一次 fixture 成功 + 本轮当前可执行文件被安全门禁阻断”，不能写成
本轮真实 Worker/Reviewer 已通过。正式控制库 v72 也只完成临时库迁移演练，未写入正式
`data/harness.sqlite`；正式迁移必须另行确认备份、恢复路径和维护窗口。

## 部署 AES 主密钥

部署负责人必须在 Manager 服务启动前，通过服务端机密管理或进程环境提供
`HARNESS_MANAGER_CREDENTIAL_MASTER_KEY`。该值是 AES-GCM 部署主密钥，不是 Provider
凭证，禁止通过 Manager UI、聊天、源码、JSON、日志或数据库传递。

操作要求：

1. 在目标部署环境的机密管理设施中生成并保存符合实现要求的主密钥。
2. 仅向 Manager 服务进程注入 `HARNESS_MANAGER_CREDENTIAL_MASTER_KEY`，不要在终端回显。
3. 启动后只检查“加密服务可用/凭证 configured”状态，不读取或展示密钥值。
4. 缺失、错误或轮换失败时停止凭证执行；先按备份和密钥轮换方案恢复，不覆盖密文。

## UI 操作序列

1. 启动前配置本机允许审查的仓库，不把路径或验证 argv 交给模型决定：

   ```bash
   export HARNESS_CODE_EVIDENCE_ROOT=/absolute/private/path/code-evidence
   export HARNESS_CODE_EVIDENCE_REVIEWER_ENABLED=1
   export HARNESS_CODE_EVIDENCE_PROJECTS_JSON='{
     "harness": {
       "path": "/absolute/path/to/Harness",
       "allowed_paths": ["."],
       "verification_commands": [["/absolute/path/to/python", "-m", "unittest", "-q", "tests.test_target"]]
     }
   }'
   ```

   `path` 必须是独立本地 Git 仓库；alias 需稳定且不含敏感信息。验证命令只接受受控 Python
   `unittest` argv，禁止 shell 字符串。多仓任务可以配置多个 alias；Harness 默认对所有配置仓
   执行，消息精确包含 alias 时只选择命中的仓库。Reviewer 开关只能由服务部署环境设置；
   关闭或缺失时不得调用模型，代码审核在 Reviewer 前阻断。开启后，每次审核/需求消息是本次
   只读模型调用的明确触发，结果必须显示 `external_calls=true`；它不授权任何仓库或外部写入。
2. 打开 `/routing` 使用**自动意图路由**。普通问题优先查询知识库，需求相关问题进入
   完整需求流程；分类后立即执行所选下游，不要求用户预选 `/knowledge` 或 `/runs`。
   空会话别名由服务端安全 cookie 保持连续，需求模式在会话内粘滞，仅在系统判断错误时
   由用户使用可选的显式纠正。普通咨询的云效状态为 `not_applicable`，无云效关联的需求
   记录 `unlinked` 后继续流程。Manager 默认需求下游使用本地确定性分析完成只读 12 阶段，
   返回 `technical_only=true`、`real_model_used=false`、`business_valid=false`；这只证明治理
   流程已走完，不冒充真实模型分析或 HIS 业务验收。需要实际改码时，继续使用上方受控
   代码位置/调用链问题会自动调用 source search/read，历史问题增加 git history；代码审查和
   需求询问强制执行完整 diff、隔离验证和 Reviewer。打开 `/code-evidence` 查看证据哈希、
   changed paths、验证、Reviewer 和多仓证据集；没有完整证据时流程阻断。需要实际改码时继续
   `local-agent` Worker → Reviewer → 人工确认 → 本地应用链路。路由事件不代表外部写授权；
   数据库修改和删除默认绝对禁止，当前没有写 executor。
3. 打开 `/providers`，维护类型化 Profile 的非密钥字段，并通过密码表单保存 Provider
   凭证。页面只应显示 `configured`，配置保存不发起连接。
4. 打开 `/actions`，选择 Profile 和已注册动作，生成绑定目标、参数哈希、风险和最终差异
   的计划。
5. 已注册只读动作不需要 Harness 人工确认，其技术权限由 token、只读 endpoint/credential
   或本地权限决定；非只读动作核对计划后做当次**一次性确认**。计划/授权过期、参数或操作者
   变化时应重新生成计划，不能复用计划或令牌。
6. 执行一次并在 `/actions` 查看脱敏审计和写动作回读结果。没有确认时不得执行外部写入。
7. 打开 `/learning-candidates`，对失败运行产生的 candidate 明确 approve 或 reject；
   approved 不等于 promoted。
8. 仅对安全、证据完整的 `knowledge.candidate` 人工推广，然后在 `/knowledge` 先检索再
   咨询。命中正式知识时 `model_used=false`。
9. 在 `/business-acceptance` 记录技术结果、环境/操作者/测试数据别名、预期/实际/证据及
   审核决定。只有完整运行时证据和明确接受才能令 `business_valid=true`。

## 正式迁移：比较、暂存和三方合并

正式 Harness 是独立目标，不能被源码工作树整体覆盖。迁移前先暂停：由负责人确认精确
目标、时间窗口、备份位置和回滚方法；未经确认不创建备份、不复制文件、不切换进程。

确认后按以下顺序执行：

1. 只读比较源和目标文件哈希，列出目标独有文件、同名分歧文件和计划新增文件，同时检查
   可用空间。不得读取、迁移或初始化现有 Manager 数据库来完成代码比较。
2. 公布精确合并清单、时间戳备份路径和恢复命令，验证备份可读且与目标对应。
3. 从目标创建可恢复的**暂存副本**；以“旧正式目标、隔离源码、暂存结果”逐文件做
   **三方合并**。保留目标独有配置和运行数据，不做批量删除或全目录覆盖。
4. 暂存副本启动前显式设置临时 `HARNESS_DB_PATH` 和临时 `HIS_KNOWLEDGE_HOME`，并注入
   独立测试主密钥。不得让暂存进程打开正式 Manager DB、WAL/SHM 或实际知识库。
5. 在暂存副本运行完整 fake 回归。通过仅证明本地合同；同时确认现有正式服务没有中断。
6. 只有在负责人再次确认后才切换正式启动路径。现有 Manager 数据库迁移必须另立计划，
   且先完成备份、校验和恢复演练。

## 回滚

切换前记录旧启动命令、旧源码路径、配置来源和服务状态。若新路径启动、健康检查或专项
验证失败：停止新路径，切回旧启动路径，按已公布的恢复命令还原本次变更的明确文件，
然后验证旧服务状态。不得删除、checkpoint、重建、清空或用新数据库掩盖原 Manager DB、
WAL/SHM、备份和知识目录。

## 分层外部验证

正式迁移完成后仍按“云效读取 → Git 本地读取 → 数据库只读技术查询 → 固定模型单节点
smoke → GitLab 读取”分别记录 `passed`、`failed` 或 `not_verified`。一个结果不能推导
另一个。云效/Git/GitLab 的每个写动作必须重新生成计划、展示最终差异、等待一次性确认、
执行一次并回读。数据库修改和删除默认绝对禁止，当前没有写 executor；未来只有用户明确授权
精确对象、操作、条件和影响范围后，才允许另建受控变更流程。
