# Role → Capability → Skill/MCP 路由

`config/role_capability_skill_matrix.json` 是 Harness 当前唯一的角色路由登记表。
它把四类信息固定在同一条可审计链上：

`background + goal + scenarios + desired_outcome`
→ `ROLE_CATALOG` 角色
→ 角色允许的 tool
→ capability/provider
→ canonical Skill 或 MCP server
→ mutation level 与外部执行边界。

## 角色分工

- `product_analyst`：需求契约、范围和验收边界。
- `architect`：模块、接口、依赖、兼容和回滚契约。
- `developer`、`frontend_developer`、`backend_developer`、`report_specialist`：仅在路径白名单内改动并执行专项验证。
- `database_specialist`：只规划数据库证据，不执行数据库写入。
- `code_reviewer`、`high_risk_reviewer`：独立审查，不能审查自己的实现；高风险角色不能绕过人工闸口。
- `test_designer`、`test_executor`、`acceptance_agent`：分别设计、执行和汇总验证证据。
- `conflict_arbiter`：只根据证据处理冲突；`human_gate`：无自动工具，只等待人工确认。

注册表加载时会验证：

1. 每个角色的 `allowed_tools` 都存在且逐一有路由；
2. provider capability 存在于正式 manifest；
3. canonical Skill 文件实际存在；
4. `his-knowledge` 的 MCP Skill 实际声明了 `his-knowledge` server；
5. Flux-Lite、auto-repair 和角色路由是 `internal`，`external_executable=false`。

统一路由 API 在上下文不完整时返回 `task_context_incomplete:*`，不会先执行模型、
MCP、Git、数据库或外部写入。旧动态规划入口仍兼容没有结构化上下文的请求，但会在
计划中显式输出 warning；进入受控执行链前必须补齐四项上下文。

## 当前边界

`his-code-evidence` 已补为 `his-engineering` 的 canonical 只读 Skill，覆盖源码、搜索、
diff、history、本地验证和 review；patch、commit、push、数据库写入和部署仍由其他
独立 capability 控制。

`harness-flux-lite` 和 `harness-auto-repair` 是 Harness 内部声明，不是可交给
`CapabilityRuntime` 的 Provider，也不是数据库迁移或真实业务验收授权。它们只保留
结构化经验、预算、attempt、review 和验证证据；高风险或冲突场景继续 fail closed。

## 改码前理解门禁

这不是新增 Agent、MCP 或 Skill：`product_analyst` 先以云效正文、评论、附件和用户补充
固定业务背景、使用场景、目标和范围；`architect` 再通过 `his-code-evidence` 对实际项目、
入口、调用链、允许路径和相邻影响取证。Harness Core 汇总成 `requirement-understanding.v1`。

只有该证据包为 `ready_for_change`，`developer` 等改码角色才可能获得本地执行入口；缺口
只会返回只读调查动作。该前置门禁独立于既有 `requirement-governance.v1` 八项检查，因而
不会修改已注册的 role → capability → Skill/MCP 合同；`auto-local` 同样必须完成项目上下文
扫描，不能以快速路径绕过。

### 截图视觉取证

`visual.extract` 是 `harness-visual-evidence` 的内部 L0 能力，只分配给
`product_analyst`。它位于技术搜索、项目选择、调用链定位和改码之前，只接收已归档的
本地截图以及有界的需求标题/正文，返回截图中直接可见的错误文本、菜单、动作和业务场景。

它不是 MCP，不读取云效，不写云效，不选择模型，也不会隐式启动 Codex、CLI 或上传图片。
宿主只有实际实现并验证 `his-visual-evidence.v1` 后，才能声明视觉能力；未实现时高风险
截图需求必须保持 `visual_evidence_blocked`，而不是根据背景文字猜测代码路径。

### 云效档案层

`yunxiao/workitem.read` 仍是唯一的云效远端读取能力，mutation level 为 L1 / GET only。
`yunxiao-workitem-read` 现在是声明 `yunxiao` server 的 MCP Skill，Skill 只说明何时调用、
输入、停止条件、证据与 Token 策略；认证、网络读取、分页和脱敏由插件 MCP Server 承担。
Harness Core 的默认执行路由已是 `mcp / native`，descriptor、stdio transport、持久证据审计、
插件入口和源码哈希均已冻结。MCP 失败直接阻断，不自动调用旧 Provider；旧路径只允许调用方
显式选择 `provider_rollback`，并继续作为兼容债务审计。
`app.requirement_archive` 是该能力之后的本地 L2 档案服务：它只接受本次读取产生的受控
临时下载目录，重算 SHA-256 后落入固定 `DFHIS-<编号>` 目录，并更新同一份
`requirement.md`。它不属于 MCP provider，不读取写凭证，也不会评论、流转、上传或改写云效。

验证命令：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_role_capability_skill_registry \
  tests.test_task_context \
  tests.test_dynamic_planning
python3 tools/capability_check.py --config config/capabilities.json validate --json
```

## Agent 后端与多入口

角色、Skill、MCP 和业务 capability 仍由上面的矩阵统一治理；模型/Agent 的执行后端是
另一条可替换边界，不改变角色权限：

| 层 | 当前责任 | 可用入口/实现 |
| --- | --- | --- |
| Harness Core | 意图、角色、capability、worktree、验证、审核、审计和历史 | `app/`、`tools/task_manager.py` |
| Host Bridge | 给宿主传递受控 request/event/result，不认识具体模型、凭证或 MCP | `tools/harness_agent_bridge.py`、`app/agent_backend_protocol.py`、`app/host_adapter.py` |
| Codex CLI adapter | 本地进程执行；保留现有签名、超时、协议和清理门禁 | `codex-cli`，必须显式选择 |
| Codex App Server adapter | 桌面端随附的官方 stdio JSON-RPC 本地运行时；独立临时 thread、worktree 写入、网络关闭 | `codex-app-server`，必须显式选择 |
| DeepSeek-Harness-Desktop adapter | 宿主自行承接请求并注入 Host handler | 通过 `host-bridge` 接入，不把宿主 SDK 写进 Core |

`config/agent_backends.json` 的默认后端是 `host-bridge`，因此导入 Harness Core 不要求本机
存在 Codex CLI 或 App Server。终端执行需要本机 Codex 时显式使用
`--agent-backend codex-cli` 或 `--agent-backend codex-app-server`，也可以设置
`HARNESS_AGENT_BACKEND`；若不提供宿主 handler，`host-bridge` 会 fail closed，不会偷偷
启动模型、网络请求或读取凭证。

宿主可以先调用：

```bash
python3 tools/harness_agent_bridge.py describe
python3 tools/task_manager.py local-agent --help
```

只读 HTTP Manager 还提供 `/api/agent-backends`，用于 Codex App、桌面宿主或其他前端展示
当前注册表。它只返回后端描述、默认选择和能力边界，不返回凭证、路径中的秘密或模型响应。
