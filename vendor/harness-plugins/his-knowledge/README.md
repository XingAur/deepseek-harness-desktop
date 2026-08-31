# HIS Knowledge

独立、local-first、证据优先的 HIS 知识插件。它与 Harness 分离：Harness 负责需求
治理和交付；本插件负责正式知识的检索、关系读取和受控维护。

## 知识模型

- 业务：`business_rule`、`workflow`、`support_boundary`、`troubleshooting`
- 工程：`code_path`、`service_contract`、`integration_topology`
- 数据：`data_dictionary`
- 历史：`requirement_history`
- 个人：`personal_memory`，默认关闭且权威最低

每条正式知识必须携带 authority、scope、有效期、source refs、版本和内容哈希。
冲突知识不直接形成答案；历史知识不能冒充当前运行时或生产事实。

## 入口

- Skill：检索、问答、候选创建、独立审核、发布。
- MCP（只读）：`knowledge_search`、`knowledge_get`、`knowledge_related`、
  `knowledge_health`。

MCP 使用 Python 标准库实现，不依赖外部向量服务，不联网，也不提供写工具。正式知识
默认位于 `HIS_KNOWLEDGE_HOME/knowledge.sqlite`；缺失时只返回 `absent`，不会自动
创建数据库。所有新增或更新必须走候选—审核—发布链路。
