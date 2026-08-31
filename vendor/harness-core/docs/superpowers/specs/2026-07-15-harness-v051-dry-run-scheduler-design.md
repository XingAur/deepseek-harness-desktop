# HIS Harness v0.51 动态调度 Dry-run 控制面设计

## 状态与边界

- 日期：2026-07-15
- 前置：v0.49 动态计划、v0.50 Task Manager 计划与契约登记
- 范围：显式 dry-run 调度、节点状态机、角色预算裁决、失败重试、检查点和恢复预览
- 禁止：模型调用、节点工具执行、worktree、业务代码修改、PG 查询、Git、云效/TAPD、发布和部署

v0.51 的所有运行状态都必须带 `simulated` 或 `dry_run` 语义。`succeeded_simulated` 只表示调度状态机接受了测试事件，不等于存在真实节点产物、代码改动、验证结果或业务验收。

## 方案选择

采用本地持久化调度模拟器：

1. 纯内存预览成本最低，但无法验证进程中断后的恢复、事件幂等和历史审计。
2. 本地持久化模拟器能验证控制面，又不会执行模型和工具，是本阶段采用方案。
3. 真实多智能体执行器暂不开放；它需要额外完成上下文隔离、worktree 并发、工具权限、真实契约产出和人工闸口事务。

## 存储模型

在 v0.50 表结构上增量新增：

- `harness_dynamic_schedules`：一次显式 dry-run，保存 plan、状态、tick 和策略快照。
- `harness_dynamic_node_states`：每个节点在该 schedule 内的当前状态、attempt、预算和最近决策。
- `harness_dynamic_schedule_events`：输入事件和调度决策审计，按 event key 幂等。
- `harness_dynamic_checkpoints`：每次状态变化后的规范化快照、hash 和恢复位置。

旧计划、契约和普通 Task Manager 表不删除、不重命名。普通 requirement workflow 不创建 schedule。

## 状态机

节点状态限定为：

- `planned`：前置条件尚未满足。
- `ready`：前置节点已 current 或在本次模拟中 succeeded。
- `running_simulated`：调度器已模拟分派，但未执行任何模型或工具。
- `succeeded_simulated`：显式成功事件已被控制面接受。
- `retry_wait`：失败或超时后仍有重试预算，等待下一次显式 tick。
- `paused_human`：人工闸口已就绪，但 v0.51 不接受自动批准。
- `blocked_budget`：事件声明的 token 或时间消耗超过角色硬预算。
- `blocked_retry_exhausted`：失败或超时次数已经超过角色重试预算。
- `blocked_stale`：v0.50 当前契约为 stale，需要上游重新形成契约。
- `completed_from_contract`：v0.50 已存在 current 真实契约，调度器直接复用该事实。

schedule 状态为 `active`、`paused_human`、`blocked` 或 `completed_simulated`。只有全部非人工节点为 `succeeded_simulated/completed_from_contract` 且没有人工闸口时，才能成为 `completed_simulated`。

## 调度和事件

显式启动时：

1. 校验 plan 存在且不是 `blocked/needs_evidence`。
2. 从 v0.50 最新契约同步 `completed_from_contract/blocked_stale`。
3. 从 plan 中读取角色 `input_budget_tokens`、`output_budget_tokens`、`timeout_seconds`、`max_retries` 和 `parallel_allowed`。
4. 计算首批 ready 节点；人工节点变为 `paused_human`，其他节点模拟分派为 `running_simulated`。
5. 写入初始 checkpoint。

显式事件格式：

```json
{
  "event_id": "fixture-requirement-success-1",
  "node_id": "requirement_analysis",
  "outcome": "success",
  "elapsed_seconds": 12,
  "input_tokens": 800,
  "output_tokens": 300
}
```

`outcome` 只允许 `success`、`failure`、`timeout`。事件只能作用于 `running_simulated` 节点；event ID 重复时返回原快照，不重复增加 attempt。失败/超时未超过 `max_retries` 时进入 `retry_wait`；下一次无事件 tick 才重新模拟分派。任何预算超限直接进入 `blocked_budget`，不能通过重试绕过。

依赖满足后，同一批可并行节点可一起进入 `running_simulated`。v0.49 已用 DAG 边消除路径冲突；v0.51 仍复核角色 `parallel_allowed`，禁止把串行角色与其他节点同时分派。

## 检查点和恢复

每次 start、event 或 tick 后写 checkpoint：

- schedule ID、plan ID、tick。
- 每个节点的状态和 attempt。
- ready/running/paused/blocked/completed 节点集合。
- 最近事件 key。
- 规范化内容 SHA-256。

`show-dynamic-schedule` 读取数据库当前状态并核对最新 checkpoint hash。stored payload 或当前节点状态与 checkpoint 不一致时，`advance-dynamic-schedule` 必须拒绝继续推进，不能用新 checkpoint 掩盖异常。恢复只继续 dry-run 状态机，不创建真实角色、模型请求或工作目录。

## CLI 与产物

Task Manager 增加三个显式命令：

- `start-dynamic-schedule --plan-id ...`
- `advance-dynamic-schedule --schedule-id ... [--event-file ...]`
- `show-dynamic-schedule --schedule-id ...`

产物：

- `dynamic_schedule.json`
- `dynamic_schedule.md`
- `dynamic_schedule_checkpoint.json`

所有产物必须包含 `dry_run=true`、`execution_enabled=false` 和禁止边界。

## 验收

1. 普通 workflow 和 v0.50 注册命令不隐式启动 schedule。
2. 简单 DAG 能按前置关系推进，并明确标记 simulated 状态。
3. 并行分支可同时模拟分派，路径/角色串行边界仍生效。
4. 失败和超时严格受 `max_retries` 控制，重复 event ID 幂等。
5. token/时间预算超限进入 `blocked_budget`，不可自动放宽。
6. 高风险人工闸口停在 `paused_human`，不能自动通过。
7. checkpoint 可重复读取并通过 hash 校验。
8. mock self-check、专项测试和全量回归通过，输出不含凭证。
