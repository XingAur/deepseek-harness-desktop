# HIS Harness v0.52 Controlled Node Runtime Design

## 目标

在 v0.51 持久化 dry-run 调度之上，增加一个受控节点运行时协议，用脱敏 fixture 验证上下文封装、工具权限裁决、节点输出和审计链。v0.52 不运行真实模型，不读取或修改真实 HIS 仓库，不查询 PG，也不执行 Git 或外部系统动作。

## 核心边界

- 普通 requirement workflow 保持不变，只有 Task Manager 显式命令可以准备或执行节点。
- 节点必须处于 `running_simulated`，且 schedule checkpoint 必须有效。
- 上下文 envelope 一经保存不可修改；执行前重新核对 envelope hash、checkpoint hash 和节点当前状态。
- fixture root 必须有 `.harness-fixture-root.json` 标记、不得位于 Git 仓库内，fixture 文件必须位于该 root 下。
- fixture executor 不执行 shell、模型、worktree、源码搜索、数据库或网络调用，只解析本地 JSON。
- 输出保存为 `fixture_contract_candidate`，不得登记为 v0.50 `current` 契约，不得推动 schedule 进入真实完成状态。
- 凭证字段、超大 payload、路径逃逸、权限拒绝和上下文漂移均硬阻断。

## 数据模型

### Context Envelope

`harness_dynamic_context_envelopes` 保存：

- schedule、plan、node 和 role 身份；
- schedule checkpoint hash；
- plan hash；
- 角色允许/禁止工具、预算和节点路径白名单；
- 当前上游契约的 artifact id、schema 和 content hash；
- 请求工具及逐项裁决；
- 规范化 envelope payload 和 SHA-256。

Envelope 只保存契约引用和 hash，不复制凭证、数据库连接或真实源码内容。

### Fixture Execution

`harness_dynamic_node_executions` 保存：

- context envelope id/hash；
- executor kind=`fixture_json`；
- fixture 文件相对路径和 SHA-256，不保存绝对业务路径；
- 状态、工具裁决、候选契约和候选契约 hash；
- 明确的 `fixture_only=true`、`business_valid=false`、`promotion_enabled=false`。

## 权限裁决

逐项按以下优先级处理：

1. 全局硬禁止：`external_write`、`git_push`、`deploy`、`database_execute`、`model_execute`、`shell_execute`、`worktree_edit`。
2. 角色 `forbidden_tools`。
3. 不在角色 `allowed_tools`。
4. 当前 fixture executor 不支持。
5. 仅 `read_artifacts` 同时通过角色和 executor 白名单时允许。

任意请求工具被拒绝，context 可以输出裁决结果，但 fixture 执行必须整体阻断。

## 显式命令

- `prepare-dynamic-node-context --schedule-id --node-id --requested-tool ... --output-dir ...`
- `execute-fixture-node --context-id --fixture-root ... --fixture-file ... --output-dir ...`
- `show-fixture-node-execution --execution-id --output-dir ...`

输出包含 JSON、Markdown 和 envelope/checkpoint 证据。命令名称明确标注 fixture，不提供容易误用为真实执行的通用 `execute-node` 命令。

## Fixture 文件契约

```json
{
  "schema_version": "1.0-fixture-node-input",
  "fixture_only": true,
  "context_hash": "sha256:...",
  "requested_tools": ["read_artifacts"],
  "contract_content": {
    "scope": "fixture example"
  }
}
```

输出 schema、producer 和 input artifact ids 均从不可变 envelope 推导，fixture 不能自行覆盖。

## 失败策略

- 不自动重试 fixture 执行。
- 失败也写审计 execution，但不写候选契约。
- 同一个 context hash + fixture digest 幂等复用，不重复生成执行记录。
- schedule/checkpoint/node 状态变化后，旧 envelope 标记为 stale 并拒绝执行。

## 下一阶段

v0.53 在本层稳定后增加受控 executor adapter 和 capability lease，但仍应先限制在 Harness 自身 fixture/sandbox；真实 HIS worktree、模型调用和本地原仓库应用需要独立策略闸口，不能由 fixture 成功自动放开。
