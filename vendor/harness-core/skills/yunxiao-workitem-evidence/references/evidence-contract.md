# Requirement Evidence v2

`requirement-evidence.v2` 是需求来源 Skill 与 Harness 之间的只读证据协议。

## 决策门禁

| `decision_gate.state` | 含义 | 后续动作 |
| --- | --- | --- |
| `ready_for_analysis` | 当前项、父级、关系、评论和附件采集完整 | 可以进入代码定位和需求分析 |
| `needs_requirement_confirmation` | 原始需求、关系、评论或附件存在缺口或冲突 | 只允许继续补证和只读定位 |
| `fetch_failed` | 当前工作项本身无法读取 | 停止处理并报告失败 |

## 来源角色

- `requested`：用户输入的当前需求或缺陷。
- `parent`：沿 `parentId`、`idPath` 或 `PARENT` 关系追溯的原始工作项。
- `related`：`SUB`、`ASSOCIATED`、`DEPEND_ON`、`DEPENDED_BY` 等一跳关系工作项。

`lineage` 按“根工作项到当前工作项”的顺序保存。`root_work_item_id` 指向最上层已成功读取的父级。

## 完整性

完整性只描述采集事实，不替代业务判断。出现下列情况时至少标记为 `partial`：

- 父工作项无法读取或父关系冲突、循环。
- 当前项或父级评论、附件读取失败。
- 任一关系类型读取失败。
- 已要求下载但附件或内联图片下载失败。

## 安全

- `mode` 必须为 `readonly`。
- `policy.allowed_actions` 必须严格等于 `["read"]`。
- 请求日志不得记录请求头、令牌值或带签名参数的临时下载 URL。
- 附件必须保存 SHA-256；临时 URL 仅保留去除 query 和 fragment 后的地址。
- `local_path` 必须相对于证据包根目录，禁止写入临时目录绝对路径，确保整包可搬迁和长期归档。
- `integrity.evidence_sha256` 覆盖除 `integrity` 自身外的整份证据，校验器必须拒绝内容与摘要不一致的文件。
- 每次采集使用新的空目录；固定 JSON/Markdown 文件、旧附件和符号链接均不得覆盖或复用。
