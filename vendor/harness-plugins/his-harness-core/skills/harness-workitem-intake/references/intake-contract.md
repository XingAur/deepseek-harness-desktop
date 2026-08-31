# Harness Work Item Intake v1 (plugin-owned)

## 输入

- `source`：工作项 URL 或编号。
- `history_root`：永久历史根目录。
- `run_id`：可选；默认使用本地时间 `YYYYMMDD-HHMMSS`。
- `provider_evidence_dir`：只接受 `$yunxiao-workitem-read` 生成的只读、
  已脱敏证据目录。
- 程序内 adapter：与 `provider_evidence_dir` 二选一，只能接收规范化的
  `source` 和临时 `output_dir`。

本协议不接收或转发任何 provider 认证参数。

## 平台路由

| 识别结果 | 证据适配器 | 历史 provider |
| --- | --- | --- |
| `devops.aliyun.com` URL 或 `DFHIS-*` | `$yunxiao-workitem-read` | `YUNXIAO` |
| 其他 | 无 | 明确报错 |

未来增加 TAPD 时，应新增 `tapd-workitem-evidence`，输出相同的
`requirement-evidence.v2` 协议；接入层不得直接依赖 TAPD 私有返回结构。

## 证据目录校验

归档前和复制后都必须验证：

- 目录本身及任一路径组件都不是符号链接；
- `requirement_evidence.v2.json` 和 `.md` 均存在；
- contract、只读 mode、`policy.allowed_actions=["read"]` 正确；
- evidence 内容哈希、下载文件大小和 SHA-256 正确；
- evidence provider 和 requested ticket 与 intake 输入一致。

## 永久记录

成功调用历史适配器后，创建：

```text
<task>/runs/<run-id>/intake/request.json
```

记录只包含：

- provider、ticket_id、run_id；
- 规范化为 `DFHIS-编号` 的 source（原 URL 不进入证据包）；
- adapter_skill；
- decision_gate、completeness；
- intake_status、next_action、mutation_allowed、readonly_discovery_allowed、requested_at。

不得记录 provider 认证信息、请求头或可还原敏感信息的错误文本。

## 状态

| decision_gate | intake_status | 允许动作 |
| --- | --- | --- |
| `ready_for_analysis` | `accepted` | 只读分析；后续仍需独立修改门禁 |
| `needs_requirement_confirmation` | `accepted_for_readonly_discovery` | 只读代码侦查；禁止 patch、提交、推送和外部写入 |
| `fetch_failed` 或未知值 | `blocked`/失败 | 说明缺口，禁止实现 |

`needs_requirement_confirmation` 必须向 `harness-history` 追加 `analysis=pending` 阶段事件，明确待执行只读侦查；其他未通过门禁追加 `analysis=blocked`。任何后续修改决定仍要求 `ready_for_analysis` 和完整的修改合同。

## 外部写入边界

评论、状态流转、附件上传、字段修改等外部写入不属于本协议，也不存在兼容写入口。
