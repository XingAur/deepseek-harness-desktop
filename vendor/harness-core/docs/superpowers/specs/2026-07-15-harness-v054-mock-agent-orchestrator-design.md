# HIS Harness v0.54 Deterministic Mock-Agent Orchestrator Design

## Goal

在不接入真实模型、业务仓库或业务工具的前提下，验证动态团队可以按 DAG 多波次运行、传递候选契约并记录可审计 trace/metrics。

## Runtime Boundary

- 只处理 `dry_run` schedule 中的 `running_simulated` 节点。
- 节点上下文必须由 v0.52 `ControlledNodeRuntime` 生成。
- 每次节点调用必须使用 v0.53 的一次性 capability lease 和固定 Python worker。
- mock-agent 输出完全由已签名 context envelope 确定，不读取业务文件，不调用模型、网络、shell、PG、Git 或外部系统。
- 成功结果仍是 `sandbox_fixture_contract_candidate`，只通过显式 `fixture_executor_success` 模拟事件推进 dry-run schedule；不登记为 current contract。

## Execution Model

1. 读取并校验 schedule checkpoint。
2. 收集当前全部 `running_simulated` 节点，形成一个 wave。
3. 为每个节点生成不可变 context、单次 lease 和 deterministic fixture。
4. 同 wave 节点按 `max_parallel` 分批并行调用固定 worker；全部调用结束后再推进调度器，避免中途 checkpoint 漂移。
5. 保存每个节点的 trace span、usage、耗时、候选 hash 和并发观测。
6. 成功节点写入显式模拟成功事件；失败节点写入对应 failure/timeout 事件并停止自动扩展。
7. 重复调用同一 schedule 返回已保存 run，不重复执行已完成节点。

## Persistence

- `harness_mock_agent_runs`：一条 schedule 只对应一个 mock-agent run，保存状态、波次数、节点数和聚合 metrics。
- `harness_mock_agent_traces`：每个节点一条 span，绑定 context、lease、execution、wave 和候选 hash。

## Failure Policy

- 任一节点失败时保留同 wave 其他节点结果和全部 trace，不自动补发 lease。
- 失败只影响 fixture run 和 dry-run 调度状态，不触发真实回滚或外部动作。
- human-only 节点保持 `paused_human`，run 返回 `paused_human`。

## Non-Goals

- 不接真实 LLM、多模型路由或独立智能体进程。
- 不读取或修改 HIS 源码，不创建 worktree，不应用原仓库 patch。
- 不查询真实 PG，不创建分支、提交或推送。
- 不执行云效、TAPD、GitHub、部署或发布写动作。
