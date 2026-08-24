# DeepSeek Harness Desktop 扩展平台架构

## 状态与边界

本文定义 DeepSeek Harness Desktop 接入 Codex、Claude、其他模型、Plugins、Skills 和 MCP 的稳定边界。当前版本已经落地模型/Agent 中心、系统安全存储凭证引用、Provider HTTP 适配器、Codex/Claude 的注入式 Agent 适配器、受管 Worker 协议、权限审批、扩展审核预览、MCP 客户端传输包装器和重启恢复状态机；真实账户、真实 CLI/SDK 进程接入仍需由宿主注入官方客户端或受管可执行文件后才能启用。

当前明确不是完成态的部分（本期没有实现真实账户运行时）：仓库没有携带 Codex 或 Claude 官方 SDK 依赖，不自动读取用户已有登录态；桌面 Worker 默认只运行确定性的 mock 适配器；扩展安装仍要求固定来源与用户确认，尚未提供任意远程扩展市场；MCP OAuth 只生成受校验的回调和凭证写入口，不在测试中发起真实令牌交换。UI 会把这些能力标为预览或不可用，不把测试适配器伪装成真实 Provider。

架构先约束能力、权限、数据和失败边界，再逐项实现，避免把第三方凭证、任意代码执行或不兼容插件直接放进可信桌面壳。

## 分层结构

```text
Desktop Shell
  ├─ Profile / 用户授权 / 更新 / 原生窗口
  ├─ Governance Plane
  │    ├─ 权限决策、审计、凭证引用、版本兼容与回滚
  │    └─ 扩展启停、隔离、健康检查和故障熔断
  └─ Managed Runtime
       ├─ Provider Adapter
       │    ├─ API Provider（Codex、Claude、其他模型 API）
       │    └─ CLI Worker（受管 Codex CLI、Claude CLI 等独立进程）
       └─ Extension Plane
            ├─ Plugins
            ├─ Skills
            └─ MCP Clients / Servers
```

- **Desktop Shell**：只负责窗口、生命周期、更新、Profile、系统凭证引用和明确的本机授权，不直接执行模型或第三方扩展代码。
- **Managed Runtime**：继续承载 Agent、会话、工具编排和 Web UI；Runtime 更新使用签名清单、版本化目录、健康检查及 last-known-good 回滚。
- **Provider Adapter**：把模型或 CLI 差异归一为稳定协议，至少覆盖能力发现、会话生命周期、流式事件、取消、限流、错误分类和用量元数据。
- **Extension Plane**：承载 Plugins、Skills 和 MCP；扩展不获得桌面壳的通用 IPC，只能调用授予的类型化能力。
- **Governance Plane**：统一做权限、审计、凭证、兼容、隔离、熔断与回滚，任何 Provider 或扩展都不能绕过。

## Provider 接口

Provider 以声明式清单注册，不在业务代码中散落模型判断。每个 Adapter 必须声明：

- `id`、显示名、Adapter 版本、兼容的 Runtime 协议范围；
- 运行方式：`API Provider` 或 `CLI Worker`；
- 支持的能力，例如对话、工具调用、图像输入、结构化输出、取消和用量回传；
- 所需凭证引用、网络主机白名单、文件系统与进程权限；
- 健康检查、超时、错误分类、重试上限和可回滚版本。

API Provider 通过受管网络客户端访问明确允许的 HTTPS 主机。CLI Worker 必须是版本固定的独立子进程，使用最小环境变量、独立工作目录、受管终止和日志脱敏；不能继承整个桌面进程环境。Codex、Claude 和其他模型都遵循同一 Adapter 合同，不能获得特殊的隐式高权限路径。

## Plugins、Skills 与 MCP

- **Plugins** 扩展运行界面或 Runtime 能力，必须有清单、来源、完整性哈希、兼容范围、启停开关和独立故障边界。
- **Skills** 是可审阅的任务说明与配套资源，默认不等同于代码执行权限；Skill 触发的工具调用仍需通过当前 Profile 的权限策略。
- **MCP** 连接按服务器逐一登记，区分本地进程和远端 HTTPS；工具、资源和提示词能力必须可见、可撤销并进入审计。

禁止“安装即全权限”。首次启用和权限扩张必须向用户显示扩展身份、来源、请求能力、数据范围和风险。禁用扩展后立即停止新调用；卸载只移除扩展自身的版本化文件，不自动删除用户项目或共享数据。

## Profile、数据与凭证

每个 Profile 独立保存 Provider 选择、扩展启用状态、权限决策、MCP 配置和会话引用。跨 Profile 共享必须由用户显式选择，不能通过默认路径或缓存偶然泄漏。

凭证只存放在操作系统安全存储或等价密钥服务中；Profile 中保存不含秘密的凭证引用。任何日志、诊断、审计事件和崩溃信息都必须脱敏，禁止回显 token、认证头、完整环境变量或私钥。

桌面应用、Runtime、Provider Adapter 和扩展均使用版本化目录。升级采用“准备 → 完整性验证 → 健康检查 → 原子切换”；失败恢复 last-known-good。更新和回滚不得迁移、重建或删除 Profile、Workspace、项目、会话、上传文件和用户扩展数据。

## 权限与审计模型

权限至少区分网络主机、工作区读写、任意文件选择、子进程、剪贴板、通知和外部应用打开。决策粒度为 Profile + 扩展 + 能力 + 资源范围，并支持单次允许、持续允许和拒绝。

审计记录包含时间、Profile、调用方、能力、目标范围、用户决策、结果和错误类别，但不记录凭证或完整敏感内容。高风险动作必须可追溯到具体 Provider、Plugin、Skill 或 MCP 工具。

## 分阶段落地

1. 固化 Adapter 清单与能力协议，先实现一个只读 mock Provider 和权限/审计闭环。
2. 接入首个 API Provider，验证流式、取消、凭证引用、网络白名单和限流。
3. 接入受管 CLI Worker，验证进程隔离、工作目录、终止和日志脱敏。
4. 为 Plugins、Skills、MCP 增加统一清单、兼容检查、启停和故障隔离。
5. 经过真实端到端测试后，再考虑可信来源目录或扩展发现能力；不默认提供任意远程代码安装。

每一阶段都必须先有行为测试、安全测试、迁移/回滚测试和 Profile 隔离测试，再开放给普通用户。
