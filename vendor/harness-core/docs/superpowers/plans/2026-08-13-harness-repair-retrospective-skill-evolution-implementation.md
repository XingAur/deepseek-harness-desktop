# Harness 修复复盘与技能进化闭环实施计划

> **目标：** 将每一次验证失败、Reviewer 驳回或人工纠正，自动转化为仅本地生效、可审计、可撤销的“修复学习规则”。规则立即约束同一任务的下一次重试；经跨任务证据后自动稳定化。它不会自动改写业务规则、权限、远端或共享技能池。

## 0. 实施范围与硬约束

- 本计划只实现 P0 离线闭环；不调用真实 Codex、不改 GitLab/云效、不执行 commit、push、建分支或远端评论。
- 所有代码必须在独立 Git worktree 内完成。原仓、业务仓、数据库、云效和 GitLab 仅在未来获得独立授权时才可写入。
- 学习状态机固定为：`draft -> active_current_task -> trial -> stable -> suspended | retired`。
- `active_current_task` 只匹配当前 `run_id`，下一次同任务 retry 立即可用；`trial` 仅允许严格匹配；`stable` 至少需要 3 个不同 task key 的通过观察、至少一个不同 workspace，并且无反例。
- 医保、收费、退费、结算、金额、远端写入、字段覆盖为高风险标签：只允许增加证据检查或评审关注点；不得自动推导、更改或覆盖业务语义、金额规则、权限、任务允许路径或远端授权。
- 对同一纠正来源的重复采集必须幂等；任何反例都立即将关联规则降为 `suspended`，不能等待下一次人工审核。
- 原有 `manager_learning_candidates` 继续服务于“知识发布候选”；本方案的修复规则在本地自动演进，两者不得互相自动晋升或写入对方表。

## 1. 基线与验证入口

在实施 worktree 内先运行：

```bash
python -m unittest \
  tests.test_local_agent_contract \
  tests.test_local_agent_repository \
  tests.test_local_agent_runner \
  tests.test_local_agent_review \
  tests.test_local_agent_cli \
  tests.test_database_governance \
  tests.test_learning_loop
```

记录基线失败项；后续只能把本次新增失败视为阻塞，不能把既有环境失败包装为功能通过。每个任务完成后都执行对应专项测试及 `git diff --check`。

---

## 2. Task 1：实现纯规则模型与确定性匹配

**Files**

- Create: `app/repair_learning.py`
- Create: `tests/test_repair_learning.py`

**接口与行为**

新增不依赖数据库、子进程或模型的纯函数模块：

```python
RULE_SCHEMA_VERSION = "his-repair-learning-rule.v1"

class RetrospectiveSourceKind(StrEnum): ...
class RootCauseKind(StrEnum): ...
class LearningRuleState(StrEnum): ...
class RuleObservationOutcome(StrEnum): ...

@dataclass(frozen=True)
class TaskLearningContext: ...

@dataclass(frozen=True)
class LearningRule: ...

def derive_task_learning_context(task: LocalAgentTask, *, run_id: int) -> TaskLearningContext: ...
def build_current_task_rule(... ) -> LearningRule: ...
def match_rules(context: TaskLearningContext, rules: Sequence[LearningRule]) -> tuple[MatchedLearningRule, ...]: ...
def validate_rule_payload(payload: Mapping[str, object]) -> dict[str, object]: ...
def canonical_rule_bytes(payload: Mapping[str, object]) -> bytes: ...
def rule_key(payload: Mapping[str, object]) -> str: ...
```

`TaskLearningContext` 只含可安全持久化、可判等的条件：run/task key、仓库种类、允许路径前缀、验证命令指纹、高风险标签和失败来源；不保存 prompt、模型输出、密钥、全量 diff 或人工自由文本。仓库种类仅由合同内受控的目录/验证命令标记确定为 `python`、`node`、`gradle` 或 `unknown`；`unknown` 不允许跨任务匹配。

规则动作是有限白名单：`verification_replay`、`reviewer_focus`、`path_coverage`。校验拒绝自定义命令、文件系统路径逃逸、空匹配条件、超长文本和未知动作。规则 key 由 canonical JSON 的 SHA-256 产生。

**实施步骤**

1. 先在 `tests/test_repair_learning.py` 写失败用例：同一语义输入产生同一 rule key、未知动作被拒绝、`unknown` 仓库不跨任务匹配、高风险动作只能是检查类。
2. 写失败用例：`active_current_task` 只匹配相同 run，`trial/stable` 需要仓库种类、路径前缀、验证指纹和高风险标签完全兼容。
3. 实现最小枚举、不可变数据类、canonical JSON 和严格 payload validator，使上述测试转绿。
4. 增加反例：自由文本含换行、敏感字段名、超长摘要、`../` 路径和 shell 控制符必须被拒绝或规范化为不持久化。
5. 运行 `python -m unittest tests.test_repair_learning`。

**验收**

- 规则永远不能携带可执行命令或扩大合同 `allowed_paths`。
- 模块导入没有 DB、网络、模型或工作区副作用。

---

## 3. Task 2：增加可迁移、幂等的本地学习存储

**Files**

- Modify: `app/database.py`
- Create: `app/repair_learning_repository.py`
- Create: `tests/test_repair_learning_repository.py`
- Modify: `tests/test_database_governance.py`

**数据模型**

将 `HARNESS_SCHEMA_VERSION` 从 70 升至 71；只允许迁移来源 `{0, 69, 70, 71}`。新增三个表及相应索引：

```text
repair_retrospectives
  id, source_key UNIQUE, run_id, attempt_id, source_kind, root_cause_kind,
  safe_summary_json, task_context_json, created_at

repair_learning_rules
  id, rule_key UNIQUE, rule_json, state, origin_retrospective_id,
  active_run_id NULL, verified_task_count, distinct_workspace_count,
  counterexample_count, state_version, created_at, updated_at, suspended_at NULL

repair_learning_observations
  id, rule_id, run_id, attempt_id, task_key, workspace_fingerprint,
  outcome, evidence_json, observed_at,
  UNIQUE(rule_id, run_id, attempt_id, outcome)
```

保留已有的数据库备份、schema 版本与 PRAGMA 保护。仓库构造器接收显式 `connection_factory`；生产路径由 local-agent 的同一数据库连接工厂注入，测试使用临时 SQLite，不允许悄悄回落到全局 `DB_PATH`。

**实施步骤**

1. 在 `tests/test_database_governance.py` 先将 schema 期望更新为 71，并断言三张表、必要的唯一索引、70→71 迁移及未来版本 fail-closed 行为。
2. 在 `tests/test_repair_learning_repository.py` 写失败用例：相同 `source_key` 重放只产生一条 retrospective；相同 rule key 只产生一条 rule；观察记录幂等。
3. 写失败用例：以 `state_version` 做 compare-and-swap 的并发/旧版本更新被拒绝；反例记录会原子地把规则置为 `suspended`。
4. 在 `app/database.py` 增加 DDL、索引及迁移白名单；确保新建库与 70 版库都得到相同结构。
5. 实现 repository 的最小方法：`record_retrospective`、`upsert_rule`、`list_matchable_rules`、`record_observation`、`advance_rule_state`、`suspend_rule`、`snapshot_for_run`。所有读取返回脱敏结构。
6. 运行 `python -m unittest tests.test_database_governance tests.test_repair_learning_repository`。

**验收**

- 所有写入是本地 SQLite、参数化 SQL、事务内执行。
- 规则稳定化无法因重复重试、重复事件或同一 task key 被虚增计数。
- 反例使规则停用，后续 `list_matchable_rules` 不再返回它。

---

## 4. Task 3：实现复盘采集、自动状态推进与审计产物

**Files**

- Create: `app/repair_learning_service.py`
- Create: `tests/test_repair_learning_service.py`
- Modify: `app/local_agent_repository.py`
- Modify: `tests/test_local_agent_repository.py`

**接口与行为**

新增 `RepairLearningService`，只接收已结构化的本地事件，提供：

```python
def matched_checks_for_attempt(task: LocalAgentTask, *, run_id: int) -> tuple[MatchedLearningRule, ...]: ...
def record_verification_failure(... ) -> RepairLearningRecord: ...
def record_reviewer_changes_requested(... ) -> RepairLearningRecord: ...
def record_human_correction(... ) -> RepairLearningRecord: ...
def record_successful_observation(... ) -> tuple[LearningRule, ...]: ...
def record_counterexample(... ) -> tuple[LearningRule, ...]: ...
```

P0 根因不是由模型臆测：验证失败固定映射 `verification_gap`，Reviewer `changes_requested` 固定映射 `review_gap`，人工纠正由 CLI 要求显式枚举 `root_cause_kind`。自由文本只经 `safe_summary` 限长、单行、敏感字段过滤后写入 JSON；同时生成一个只包含结构化内容的 `his-repair-retrospective.v1` artifact，不写原始 prompt、diff 或密钥。

在 `LocalAgentRunRepository` 增加最小的、只读写 learning 同库连接工厂入口（例如 `open_learning_connection()`），以及将待确认 run 失效为 `changes_requested` 的受控方法。该方法必须同时使未使用 confirmation 失效，并产生本地审计事件；不得伪造 `locally_applied` 可回滚。

**实施步骤**

1. 写 service 测试：三种来源生成稳定 source key；重复调用没有重复 retrospective/rule；安全摘要不落盘原始敏感文本。
2. 写状态机测试：新规则为 `active_current_task`，同 run 失败重试可匹配；一次验证/审核通过后转 `trial`；3 个不同 task key、不同 workspace 的成功观察后才转 `stable`。
3. 写反例测试：任意 counterexample 立即 `suspended`；高风险规则即使满足 3 次观察仍保留 `trial`。
4. 实现 summary sanitizer、固定源映射、artifact 构造、repository 调用和状态机，令测试转绿。
5. 为 repository 增加 `invalidate_confirmation_for_correction` 的失败测试：只能处理 `awaiting_human_confirmation`，错误状态或非当前 attempt 必须 fail-closed。
6. 实现该 repository 方法，并运行 `python -m unittest tests.test_repair_learning_service tests.test_local_agent_repository`。

**验收**

- 学习由结构化证据驱动，不依赖“模型自评正确”。
- 人工纠正能立即阻止旧 confirmation 被继续 apply。
- 学习记录失败时，调用方能收到异常且不把 run 误标为已复盘。

---

## 5. Task 4：把学习规则接入 Worker、验证和独立 Reviewer

**Files**

- Modify: `app/local_agent_contract.py`
- Modify: `app/local_agent_runner.py`
- Modify: `app/local_agent_review.py`
- Modify: `tests/test_local_agent_contract.py`
- Modify: `tests/test_local_agent_runner.py`
- Modify: `tests/test_local_agent_review.py`

**接入点**

- `build_worker_prompt(task, *, workspace_path=None, learning_checks=())`：仅追加固定格式的“必须执行/验证/注意”检查项；不把历史人工原文、其它仓库路径或新允许路径传给 Worker。
- `LocalAgentReviewRunner.review(run_id, *, learning_focus=())`：只追加固定格式的 Reviewer 关注点，并维持既有 review JSON schema、hash 和 seal 校验。
- `LocalAgentRunner` 注入 `RepairLearningService`。每个 attempt 开始前解析匹配规则、写本地审计/artifact；验证失败和 Reviewer 驳回后采集 retrospective；Reviewer 通过后记录 success observation，再决定是否进入 `awaiting_human_confirmation`。

**实施步骤**

1. 在合同测试中写失败用例：learning checks 不在白名单、试图添加命令、或涉及非 allowed path 时，prompt builder 拒绝。
2. 在 Reviewer 测试中写失败用例：学习关注点可见但审查输出 schema/hash 不变；坏规则不会进入 reviewer prompt。
3. 在 runner 测试中写失败用例：验证失败生成一次 retrospective；Reviewer 驳回生成一次 retrospective；相同事件重放不重复生成。
4. 在 runner 测试中写失败用例：存在 `active_current_task` 规则时下一次 retry 的 Worker/Reviewer 都获得相同检查；未匹配、`suspended` 或 `unknown` 上下文规则绝不注入。
5. 在 runner 中以 `LocalAgentRunRepository` 的数据库工厂构造 service，或者只从构造器显式注入；禁止静默读取全局 production DB。
6. 实现最小 prompt/review 参数、attempt 事件、回溯记录和 success observation。保持当前状态转换：只有验证和 Reviewer 均成功且持久化成功，才能进 `awaiting_human_confirmation`。
7. 运行 `python -m unittest tests.test_local_agent_contract tests.test_local_agent_review tests.test_local_agent_runner`。

**验收**

- 任何学习服务异常都 fail-closed：不允许跳过审计直接进入待确认状态。
- 学习规则不能改变 worker 可修改范围、verification command 或 remote action policy。
- retry 仍使用现有 worktree quarantine/replay 流程；本次不新增自动多轮模型修复循环。

---

## 6. Task 5：提供人工纠正的受控 CLI 入口

**Files**

- Modify: `tools/task_manager.py`
- Modify: `app/local_agent_runner.py`
- Modify: `app/local_agent_repository.py`
- Modify: `tests/test_local_agent_cli.py`
- Modify: `tests/test_local_agent_runner.py`
- Modify: `docs/manager-runbook.md`

**命令合同**

增加 JSON-only 子命令：

```bash
python tools/task_manager.py local-agent record-correction \
  --database /private/tmp/local-agent.sqlite3 \
  --run-id 42 \
  --worktree-root /private/tmp/local-agent-worktrees \
  --root-cause-kind implementation_defect \
  --summary-file /private/tmp/correction-summary.txt
```

`summary-file` 必须是调用者拥有的、普通、非 symlink、UTF-8、≤4 KiB 的单行文件；读取后立即规范化，输出和数据库只保留脱敏摘要/哈希。命令不接受任意 JSON、shell 片段、路径列表或更改规则文本。

允许状态：`failed_verification`、`changes_requested`、`awaiting_human_confirmation`。若处于待确认，先成功记录 correction，再原子失效 confirmation、转为 `changes_requested`，阻止旧 patch 被 apply。`locally_applied`、`locally_applied_error` 或其他状态必须拒绝，并要求新建 run；这不是 Git rollback 工具。

**实施步骤**

1. 先在 CLI 测试写失败用例：无 `--summary-file`、symlink、超限、非法 root cause、非允许状态或 run/worktree 不匹配均输出单个安全 JSON 错误且无状态变化。
2. 写成功用例：待确认 run 写入一条 retrospective，confirmation 失效，状态变为 `changes_requested`；重放同一命令幂等。
3. 在 runner 中实现窄接口 `record_human_correction(...)`：验证 run-binding，再调用 service，再请求 repository 转换状态。若状态转换失败，重复 source key 保证下次可安全重试。
4. 为 `tools/task_manager.py` 加参数解析、普通文件检查和 JSON response；不得打印 summary 原文、路径外内容或 traceback。
5. 在 `docs/manager-runbook.md` 增加“人工发现问题后的复盘”章节，明确该命令不修改原仓、不提交、不推送。
6. 运行 `python -m unittest tests.test_local_agent_cli tests.test_local_agent_runner tests.test_local_agent_repository`。

**验收**

- 人工发现问题后，不需要人工“批准学习”；系统自动在下一 retry 采用当前任务规则。
- 该入口不允许通过自由文本把任意指令注入 Worker/Reviewer。

---

## 7. Task 6：集成回放、文档口径与最终验证

**Files**

- Create: `tests/test_repair_learning_integration.py`
- Modify: `README.md`
- Modify: `docs/manager-runbook.md`
- Modify: `CHANGELOG.md`

**实施步骤**

1. 写端到端离线集成测试，使用临时 Git fixture 和 fake Worker/Reviewer：
   - Task A 的验证失败创建 `active_current_task` 规则；retry 显示规则注入。
   - 三个不同 task key、至少两个 workspace 的成功观察后规则自动到 `stable`。
   - 第四个 task 发生反例时规则立刻 `suspended`，第五个 task 不再注入。
   - 高风险金额/结算任务只能停留 `trial`，即使观察次数已满足。
   - 待确认状态执行 `record-correction` 后不能 `confirm-apply`。
2. README 增加“已验收 P0 / 未验收 P1-P2”状态源：P0 是离线规则闭环，P1 是真实 Codex 临时 fixture 验收，P2 是 GitLab/云效受控交付；绝不把计划描述成已通过。
3. manager runbook 补充观察、规则状态、人工纠正命令及恢复边界；解释“自动学习”与“自动远端写入”无关。
4. CHANGELOG 以实际版本号和日期记录本次功能，避免 README/CHANGELOG 口径漂移；未决定发布版本前用 `Unreleased`，不擅自声明 v0.71.0。
5. 运行完整相关套件：

   ```bash
   python -m unittest \
     tests.test_repair_learning \
     tests.test_repair_learning_repository \
     tests.test_repair_learning_service \
     tests.test_repair_learning_integration \
     tests.test_local_agent_contract \
     tests.test_local_agent_repository \
     tests.test_local_agent_runner \
     tests.test_local_agent_review \
     tests.test_local_agent_confirmation \
     tests.test_local_agent_cli \
     tests.test_database_governance \
     tests.test_learning_loop
   ```

6. 查看 `git diff --check`、`git diff --stat` 和逐文件 diff；确认没有未授权的 provider、Git、云效、模型或业务数据库改动。
7. 输出验收报告：通过/失败测试清单、离线证明边界、尚未做的真实 Codex fixture、GitLab/云效 P2、自动修复循环 P1 及恢复/撤销方法。

**验收**

- P0 只在上述全套离线测试通过时才可宣称完成。
- 真实 Agent、真实 GitLab 写入、真实云效写入和自动模型修复循环均明确保留为后续独立验收。

## 8. 完成定义与后续阶段

本计划完成时，Harness 具备“发现失败/人工纠正 → 自动生成本地约束 → 同任务立即使用 → 跨任务证据自动稳定 → 反例自动停用”的离线能力，并始终保留人对业务含义和外部写入的控制权。

后续 P1 必须单独设计并验收受预算、次数、停止条件约束的真实 Codex 修复循环；P2 必须把 GitLab/云效写动作纳入既有 provider action plan、人工确认、回读校验和最小提交策略，不能由本 P0 的学习规则越权触发。
