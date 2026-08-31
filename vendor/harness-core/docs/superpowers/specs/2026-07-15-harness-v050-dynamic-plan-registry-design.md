# HIS Harness v0.50 动态计划登记与契约生命周期设计

## 状态与边界

- 日期：2026-07-15
- 前置：v0.49 `dynamic-plan` 已完成
- 范围：Task Manager 增量登记、契约版本、stale 传播、只读恢复预览
- 禁止：模型调用、节点执行、worktree 并行、数据库业务查询、Git 或需求平台写入

## 存储模型

在现有 Task Manager 上新增独立表：

- `harness_dynamic_plans`：父任务下的不可变规划快照、内容哈希和替代关系。
- `harness_dynamic_subtasks`：计划节点、角色、状态、路径、并行组和输出契约。
- `harness_dynamic_edges`：节点依赖和交接 schema。
- `harness_contract_artifacts`：节点契约版本、内容哈希、输入产物、状态和替代关系。
- `harness_dynamic_audit_events`：登记、版本更新和 stale 传播审计。
- `harness_schema_meta`：增量 schema 版本。

旧表不删除、不重命名、不隐式双写。默认临时数据库行为保持不变。

## 登记语义

显式 `register-dynamic-plan` 接收 v0.49 `dynamic_plan.json`：

1. 校验 schema、只读边界、节点、边、角色和 handoff 一致性。
2. 拒绝未知节点、重复节点和有环依赖，保证登记对象仍是 DAG。
3. 解析或创建父 `harness_tasks` 记录。
4. 以规范化计划内容 SHA-256 作为幂等键。
5. 写入计划、子任务、边和 `planned` 契约占位记录。
6. 返回 plan ID、task ID、节点/边/契约数量和恢复预览。

同一 task 和相同哈希重复登记时返回原 plan，不新增记录。新计划不会覆盖旧计划；旧计划只记录 `superseded` 关系，历史仍可读取。

## 契约版本

显式 `record-dynamic-contract` 只能更新计划中已有节点，并校验：

- schema 与节点 `output_contract` 一致。
- producer 与节点角色一致。
- schema version 为支持版本。
- 输入 artifact ID 来自当前计划允许的上游节点。
- 内容必须为 JSON 对象，hash 由 Harness 计算，调用方不能伪造。
- 内容不得包含 password、token、api_key、secret、cookie、DSN 等凭证字段。

新版本采用单调递增版本号；旧版本标记 `superseded`。上游内容变化后，所有可达下游节点的最新契约标记 `stale`，但历史内容不删除。

## 恢复预览

恢复预览只计算：

- `completed_nodes`：最新契约状态为 `current`。
- `stale_nodes`：最新契约因上游变化过期。
- `ready_nodes`：自身未完成且所有前置节点均为 current。
- `blocked_nodes`：前置未完成、过期或等待人工闸口。

预览不执行节点、不创建 worktree、不修改业务仓库。高风险 `human_gate` 始终显示为人工动作。

## 验收

1. 计划登记幂等，旧 Task Manager 数据仍可读取。
2. 子任务、边、角色和 handoff 数量与 v0.49 计划一致。
3. 无效 schema、producer、输入 artifact、循环依赖或凭证字段不能入库。
4. 上游新版本能传递标记全部下游 stale，不影响无关并行分支。
5. 恢复预览稳定区分 completed/ready/stale/blocked。
6. 所有动作追加审计，不删除历史。
7. CLI 只有显式命令才写本地 Task Manager；无远端和业务仓库副作用。
