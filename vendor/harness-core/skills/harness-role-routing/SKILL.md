---
name: harness-role-routing
description: Use for Harness-internal role, task-context, and evidence-gate routing.
---

# Harness Role Routing

这是 Harness 内部路由声明。它负责把任务意图、角色允许工具和已注册能力组成
只读路由合同，不执行 Provider、MCP、数据库、Git 或模型调用。

完整的背景、目标、场景和愿望是自动路由的前置条件；缺失任何一项都必须
`task_context_incomplete` 并停止。人工闸口没有自动工具，所有高风险语义仍须
由用户或业务负责人确认。
