# Harness v0.57 Controlled Model Provider Smoke

## Goal

在不改变 Harness 默认离线流程的前提下，验证一个已配置 OpenAI-compatible 模型 endpoint 的最小连通性，并留下不含密钥和模型原文的审计证据。

## Safety Contract

- 未同时开启凭证读取和网络调用双开关时，在读取 credentials 文件之前拒绝。
- 必须提供本次用户授权标识；授权标识只保存 SHA-256。
- Profile 不保存密钥，只保存凭证 key 别名和 endpoint 主机白名单。
- 只发送固定 `SMOKE_OK` 单节点提示，不接受任意 prompt、node 或工具输入。
- 每次授权最多一次 HTTP 请求，短超时，无重试；相同授权和请求在同一 SQLite 中幂等复用。
- 只保存 endpoint 主机、模型、凭证 key 名、usage、状态和哈希，不保存 API key、请求头或响应原文。
- transport、OpenAI-compatible protocol shape、fixed marker 分层记录，CLI 只在三层全部通过时返回 0。
- smoke 不接入动态 DAG，不执行 HIS 源码、worktree、PG、Git、云效/TAPD 或部署动作。

## Implementation

- `app/model_provider_runtime.py`: 双开关门禁、Profile 解析、固定请求、一次性 transport、脱敏与审计快照。
- `app/database.py`: `harness_model_provider_smokes` 与事件表。
- `config/model_providers.example.json`: 无密钥 DeepSeek OpenAI-compatible Profile。
- `tools/task_manager.py`: `run-model-provider-smoke` 与 `show-model-provider-smoke`。
- `tools/self_check.py`: fake transport 自检，严禁真实网络。
- `tests/test_model_provider_runtime.py`: 门禁、幂等、脱敏、失败、协议与 CLI 回归测试。

## Acceptance

- 专项测试、动态运行时测试、全量单测和 mock self-check 通过。
- 真实请求只在用户明确授权后执行一次。
- 真实 smoke 无论成功或失败均不得自动重试。
- 对输出目录和 SQLite 做密钥泄漏扫描。

## Status

实现、离线专项测试、动态运行时回归、全量单测和 mock self-check 已完成。2026-07-15 按用户授权执行了唯一一次真实请求：HTTPS/凭证/模型/OpenAI-compatible 响应解析链成功，返回 usage 22/16/38；模型响应未精确匹配固定 marker，因此严格状态为 `failed_protocol`。审计确认只有一个网络事件、无重试、无响应原文，API key/Authorization header 泄漏扫描为 0。v0.57.1 已离线补充分层状态、失败退出码、旧审计兼容推导，并依据输出 token 恰好达到原 16 上限的证据，将后续固定 smoke Profile 上限调整为 64、强化 exact marker 指令。最终验证为 provider 专项测试 9/9、全量单测 217/217、同一持久化 SQLite 连续两轮完整 mock self-check 138/138。复测仍需要新的逐次授权。
