# Harness 多入口宿主接入合同

Harness Core 不把 Codex App、Codex CLI 或 DeepSeek-Harness-Desktop 当成权限来源。
它们都是宿主（host）；宿主只负责承接 Agent 请求和返回结构化结果，真正的角色、
capability、Skill/MCP、worktree、验证、审核、人工确认和 mutation level 仍由 Harness
Core 决定。

## 四个入口

| host_id | 入口 | 默认 backend | 当前状态 |
| --- | --- | --- | --- |
| `terminal` | 终端运行 `tools/task_manager.py` | `host-bridge` | 已有 CLI；需显式选择后端执行 Agent |
| `codex-app` | Codex Desktop 附带 App Server | `codex-app-server` | 使用官方 stdio JSON-RPC 启动独立临时 thread；不接管当前窗口对话 |
| `codex-cli` | Codex CLI 调用 Harness 或本地 Agent | `codex-cli` | 兼容现有签名/超时/清理门禁，必须显式选择 |
| `deepseek-harness-desktop` | DeepSeek-Harness-Desktop | `host-bridge` | 使用同一 JSONL 合同；桌面 adapter 由宿主提供 |

发现入口：

```bash
python3 tools/harness_agent_bridge.py describe
```

只读 Manager 发现接口：`GET /api/agent-backends` 和
`tools/harness_agent_bridge.py describe` 的 `hosts` 字段。两者都不读取凭证、不访问
网络、不打开正式 Harness 数据库。

## 协商与执行边界

宿主首先提交 `his-agent-host-negotiation.v1`，示例：

```json
{
  "schema_version": "his-agent-host-negotiation.v1",
  "host_id": "deepseek-harness-desktop",
  "role": "worker",
  "required_capabilities": ["source.search", "verification.run-local"],
  "requested_mutation_level": "L0"
}
```

不满足角色、capability 或宿主自身 transport 上限时，返回确定性的拒绝码。即使宿主
声明支持更高 mutation level，也不能替代 Harness 的一次性授权、需求治理、人工确认
或数据库永久只读策略；例如 Harness 授权为 `L0` 时，请求 `L2` 必须返回
`host_mutation_not_authorized`。

宿主 adapter 收到 `his-agent-backend-request.v1` 后，只能返回已定义的 event/result
合同。不得把模型名、线程 ID、token、原始 provider payload 或凭证塞回 Harness。当前
bridge 已提供 `describe`、`validate-request` 和 `negotiate`，用于连接前合同验证；没有
注册 Host handler 时执行会 fail closed，不能把“已发现宿主”伪装成“已完成真实 Agent 运行”。

## Codex Desktop 的正式本地通道

选择 `--agent-backend codex-app-server` 时，Harness 固定启动本机
`codex app-server --stdio`，完成 `initialize`、`thread/start`、`turn/start` 的 JSON-RPC
序列。每次 Harness attempt 使用一个 ephemeral thread；App Server 的 thread ID、模型名、
provider payload 和凭证都不会进入 Harness 事件或证据。运行策略固定为 worktree 写入、
网络关闭和无额外可写根；Harness 自己的任务授权、worktree、验证、审核和人工 gate 仍然
在 App Server 之前生效。
Reviewer 响应不由 App Server 的 `outputSchema` 认定为可信：Harness 保留 schema 文件完整性
检查，并对返回 JSON 重新执行字段与 review-hash 验证后，才允许进入人工确认门。

这条通道是对本机已安装 runtime 的代码级接入，不等于操作或复用当前已经打开的 Codex
Desktop 对话；首次真实模型 smoke 需要在隔离 worktree 中单独执行。

## 宿主 Adapter 的最小实现

宿主侧可以复用 `app.host_adapter.HostAdapterSession`，它同时支持 JSONL 输入和进程内
嵌入。宿主只需要提供一个 `(request, sink) -> AgentBackendResult` handler：

```python
import json

from app.host_adapter import HostAdapterSession


def invoke_host_runtime(request, sink):
    # 由 Codex App 或 DeepSeek-Harness-Desktop 注入自己的 Agent 调用。
    # 这里只能返回 AgentBackendResult，不得返回 provider payload 或凭证。
    return host_runtime.execute(request, sink)


session = HostAdapterSession(invoke_host_runtime)
result_line = session.handle_json_line(json.dumps(request.to_dict()))
```

输入无效、handler 异常或返回非 `AgentBackendResult` 时，session 会返回固定的安全错误，
不会把异常原文、模型标识或宿主内部线程标识泄露给 Harness。宿主仍必须把 Harness 的
`authorized_mutation_level`、角色和 capability 当作不可升级的约束。

## Codex CLI 的特殊说明

本机 Codex CLI 的签名、可执行文件、子进程、协议、超时和清理门禁仍由原有
`CodexCliWorker` 负责。选择 `--agent-backend codex-cli` 后，原有 gate 继续生效；签名
无效时只阻断该后端，不影响 Harness Core、角色路由、MCP/Skill 发现、mock/replay 或
其他宿主的合同验证。
