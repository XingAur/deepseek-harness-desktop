# Harness 启用配置填写说明

配置文件：`harness_activation.local.json`。它是“启用申请和配置输入”，不是直接保存 token 的文件；Harness 会在审核后把其中的模型信息转换为实际 Profile。

## 先只做真实模型时，最少填写这些

其余 `yunxiao`、`git`、`gitlab`、`database` 块先保持 `enabled: false` 即可。

| JSON 路径 | 填什么 | 示例/规则 |
| --- | --- | --- |
| `requested_by` | 本次配置负责人 | `lym` |
| `authorization_id` | 本次真实模型 smoke 的唯一授权号 | `2026-08-09-model-smoke-01`；每次新 smoke 用新值 |
| `environment` | 目标环境 | 建议先填 `test` 或 `uat`，不要填生产 |
| `model.profile_key` | 模型 Profile 的名字 | `company-deepseek-smoke` |
| `model.provider_kind` | 当前支持的协议类型 | 目前只填 `openai_compatible` |
| `model.allowed_endpoint_hosts[0]` | 模型 API 域名，不含 `https://`、路径或端口 | `api.deepseek.com` |
| `model.model_name` | 实际模型名 | 例如供应商提供的模型标识 |
| `model.credential_key_refs.api_key` | 本机凭证库中 API Key 的字段名 | `deepseek_api_key`，不是 Key 的值 |
| `model.credential_key_refs.base_url` | 本机凭证库中 base URL 的字段名 | `deepseek_base_url` |
| `model.credential_key_refs.model` | 本机凭证库中模型名的字段名 | `deepseek_model` |
| `model.timeout_seconds` | 单次请求超时 | 5–45；建议 20 |
| `model.max_output_tokens` | 固定 smoke 的最大输出 token | 建议 64 |
| `model.monthly_budget_limit_cny` | 本次接入的预算上限记录 | 填具体金额；当前作为审核字段，不替代供应商侧限额 |

## 凭证应该放在哪里

不要把 token 填进启用 JSON。把值保留在本机安全凭证文件或 Keychain；若使用本机文件，路径由 `credentials.credentials_file_path` 指定。该文件至少要有以下**字段名**对应的值：

```json
{
  "deepseek_api_key": "只在本机填写真实值",
  "deepseek_base_url": "https://供应商地址/v1",
  "deepseek_model": "供应商模型名"
}
```

上面的 `deepseek_*` 必须与 `model.credential_key_refs` 中填写的名称一致。不要把此文件内容发送到聊天。

## 其它 Provider 什么时候填

| 区块 | 什么时候启用 | 需要填写的核心信息 |
| --- | --- | --- |
| `yunxiao` | 要真实读取或写入工作项时 | 组织/项目、只读和写入凭证引用、测试工作项、允许动作 |
| `git` | 要检查、提交或推送测试仓库时 | 测试仓库绝对路径、非保护分支、允许动作 |
| `gitlab` | 要访问 GitLab 测试项目时 | GitLab 主机、测试项目、token 引用、允许动作 |
| `database` | 要连接测试库、查询或建测试视图时 | 测试库五项身份、只读/变更凭证引用、批准 SQL/测试对象、回滚方式 |
| `business_acceptance` | HIS 页面或接口真实验收时 | 环境、账号别名、测试数据、场景、证据位置 |
| `knowledge` | 要扩大知识库问答范围时 | 允许索引与禁止索引的绝对路径、知识审核人 |

`allowed_actions` 里的每个 `true` 都代表你允许该种动作进入后续审核。日常低风险本地需求使用 `auto-local` 时，Harness 会在需求契约确定后自动复用一次任务级范围授权，内部扫描、worktree、编译和本地回写不再逐动作询问；云效写、Git push、GitLab 写和数据库 change 仍会在实际执行前要求一次独立确认。

## 现在的推荐填写范围

1. 先复制模板为 `harness_activation.local.json`。
2. 只填写顶层字段和 `model` 区块。
3. 保持 `keep_general_model_runtime_frozen: true`、`keep_agent_team_dag_frozen: true`。
4. 其余 Provider 保持 `enabled: false`、所有写动作保持 `false`。
5. 填好后回复“JSON 已填”；Harness 先做脱敏结构检查，再由受控单节点 smoke 做一次真实连通验证。
