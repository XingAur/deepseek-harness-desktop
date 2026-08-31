---
name: harness-auto-repair
description: Use for Harness-internal bounded local auto-repair loops with explicit budgets, review, and rollback boundaries.
---

# Harness Auto Repair

这是 Harness 内部能力声明，只能作用于用户明确授权的隔离本地 worktree。

- `harness.auto-repair.run`：按固定轮数、时间和 token 预算执行修复尝试。
- 每轮必须保留 attempt、patch、review 和验证证据；冲突或高风险业务立即停止。
- 自动修复不等于业务验收，不得自动 commit、push、部署、写云效或写正式数据库。
- 需要真实 Codex Worker/Reviewer 时，必须使用 Harness 固定角色边界和隔离 fixture。

当前实现入口：`app/local_agent_runner.py`、`app/repair_learning_service.py`、
`tools/task_manager.py auto-repair`。该 Skill 的 `execution_kind` 固定为
`internal`，不得交给外部 CapabilityRuntime 执行。
