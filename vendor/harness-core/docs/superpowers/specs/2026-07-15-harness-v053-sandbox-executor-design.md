# HIS Harness v0.53 Sandbox Executor Design

## 目标

在 v0.52 不可变 context envelope 上增加固定 executor adapter、短期一次性 capability lease 和进程级失败隔离，证明节点可以通过统一协议被真实调用并返回结构化结果。v0.53 仍只处理脱敏 fixture，不调用真实模型、业务仓库工具、数据库、Git 或外部系统。

## 非目标

- 不接受任意 executable、shell command、Python module 或环境变量。
- 不读取真实 HIS 源码，不创建 worktree，不应用 patch。
- 不把 sandbox fixture 结果晋升为 current 契约。
- 不批准人工闸口，不连接真实 PG，不执行远端写入。

## Capability Lease

Lease 绑定以下不可变事实：

- context id 和 envelope hash；
- schedule checkpoint hash；
- adapter kind=`sandbox_fixture_worker`；
- 允许 capability 集合；
- policy hash、签发时间、过期时间；
- `max_uses=1` 和持久化 use count。

签发条件：context/current checkpoint 有效、节点仍为 `running_simulated`、context 权限裁决全部通过、capability 与 context 请求完全一致、TTL 在 1 至 300 秒内。Lease 不包含 token、密码、DSN、cookie 或任何外部凭证。

## 固定 Adapter

`SandboxFixtureExecutor` 只能使用当前 Harness 自带的 `tools/fixture_node_worker.py`，通过 `subprocess.run([sys.executable, fixed_worker])` 调用：

- `shell=False`；
- cwd 固定为已验证 fixture root；
- 不继承父进程环境，仅传 UTF-8 最小环境；
- stdin/stdout 使用版本化 JSON 协议；
- timeout 上限 5 秒；
- 不把 stderr 原文写入数据库或报告；
- worker 只转换 stdin JSON，不打开业务文件、不访问网络。

## Worker 协议

输入包含 context hash、lease capability、节点输出契约和已验证 fixture payload。输出：

```json
{
  "schema_version": "1.0-sandbox-node-result",
  "status": "success",
  "contract_content": {},
  "usage": {
    "input_tokens": 0,
    "output_tokens": 0
  }
}
```

fixture 可显式指定 `worker_behavior=success|failure|sleep|protocol_error`，只用于自检失败隔离。`sleep` 受 adapter timeout 硬终止。

## 执行顺序

1. 验证 lease、context hash、checkpoint、节点状态和 fixture root。
2. 计算 fixture digest 和 execution key，先检查幂等结果。
3. 原子消费 lease；消费后无论成功、失败或 timeout 都不能再次用于其他输入。
4. 调用固定 worker，解析并验证结构化结果。
5. 执行 token/时间预算、凭证字段和候选契约大小检查。
6. 只保存 `sandbox_fixture_contract_candidate` 和审计结果，不改变 schedule/registry。

## 状态

- `succeeded_sandbox_fixture`
- `failed_adapter`
- `blocked_adapter_timeout`
- `blocked_adapter_protocol`
- `blocked_adapter_budget`
- `blocked_lease_expired`
- `blocked_lease_consumed`
- `blocked_stale_context`

## 显式命令

- `issue-fixture-capability-lease`
- `show-fixture-capability-lease`
- `execute-sandbox-fixture-node`

所有命令都明确含 fixture/sandbox，不提供通用 `execute-agent` 命令。

## 下一阶段

v0.54 可增加 deterministic mock-agent adapter 和节点级 trace/metrics，验证多节点交接和并行控制；真实模型 adapter、真实 worktree 工具和业务项目权限仍需独立设计与人工确认后才能开放。
