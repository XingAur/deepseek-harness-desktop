# his-knowledge skills

本目录状态为 `canonical`，属于 `his-knowledge` 正式插件。

- `his-knowledge-retrieve`：按医院、地区、模块、仓库和时间范围检索带来源的证据。
- `his-knowledge-answer`：提供客服式 `question` 回答；证据缺失、冲突或过期时明确
  返回需要补充的实时证据，不能承诺什么都能答。
- `his-knowledge-maintain`：只有用户明确要求记忆，或授权治理流程创建候选时才进入
  candidate；没有新需求时也可从已批准的本地文档、源码仓库和审计历史冷启动，
  但只生成候选。创建、独立审核和推广是三个分离的 L2 操作。

个人记忆默认关闭，且永远不能高于当前已验证证据。知识库不直接调用云效、Git、
数据库或网络，也不能把历史结论描述为当前生产事实。

插件同时提供独立的只读 MCP：`knowledge_search`、`knowledge_get`、
`knowledge_related`、`knowledge_health`。MCP 只读取已经发布的正式知识，不创建
SQLite、不写候选、不审核、不发布；知识写入仍必须由 `his-knowledge-maintain`
执行 candidate-first 治理。

HIS 知识类型除业务规则、代码路径、流程和故障处理外，还包括服务契约、数据字典、
集成拓扑和需求历史。服务、API、仓库、表和需求之间可通过 relation 建立直接证据图。
