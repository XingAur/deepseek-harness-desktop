---
name: harness-flux-lite
description: Use for Harness-internal Flux-Lite learning, replay, conflict demotion, and bounded reviewer experience capture.
---

# Harness Flux-Lite

这是 Harness 内部能力声明，不是外部 Provider，也不是可直接调用的 MCP
server。它只允许在 Harness 已经完成角色、任务上下文、attempt 和证据门禁后运行。

- `harness.flux-lite.replay`：只读回放已有 attempt，不能创造新事实。
- `harness.flux-lite.learn`：根据已审查的 reviewer opinion 生成候选经验；候选必须继续经过人工或治理门禁，不能自动发布。
- 冲突、上下文漂移、预算耗尽和高风险场景必须 fail closed。
- 不向模型提示词注入凭证、完整敏感 payload 或未封存外部响应。

当前实现入口：`app/flux_lite_learning.py`、`app/flux_lite_repository.py`、
`app/flux_lite_service.py`。该 Skill 的 `execution_kind` 固定为 `internal`，
不得交给外部 CapabilityRuntime 执行。
