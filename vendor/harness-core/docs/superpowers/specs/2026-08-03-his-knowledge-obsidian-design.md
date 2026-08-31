# HIS Knowledge Obsidian Integration Design

## Goal

把 HIS Harness 的长期知识库放到 `/Users/lym/WorkCode/ai/his-knowledge`，同时支持 Obsidian 人工整理和 Harness 结构化检索。

## Directory Contract

```text
/Users/lym/WorkCode/ai/his-knowledge/
  vault/
    00-inbox/
    10-his-rules/
    20-hospital-cases/
    30-yunxiao-learnings/
    40-db-notes/
    90-review/
  seeds/
  exports/
  knowledge.sqlite
```

`vault/` 是 Obsidian 可打开目录，保存人可读 Markdown。`knowledge.sqlite` 是 `his-knowledge` 插件维护的结构化索引。两者可以互相引用，但 SQLite 的 evidence status、scope、freshness 和 conflict 结果是 Harness 自动回答的准入边界。

## Markdown Frontmatter

Obsidian 笔记建议使用以下 frontmatter：

```yaml
---
knowledge_id: dfhis-example-id
hospital: 东方医院
module: 门诊收费
source_ticket: DFHIS-00000
evidence_level: code_verified
valid_until: 2026-12-31
status: candidate
---
```

`status` 默认先进入 `candidate`，经独立审核后才能推广为正式知识。普通任务报告、聊天结论和失败记录不能自动晋升为正式知识。

## Capability Boundaries

- `knowledge.retrieve` 和 `knowledge.answer` 是 L0，只读检索，不读取凭证。
- `knowledge.candidate.create/review/promote` 是 L2，本地持久化，必须显式触发。
- 缺少证据、证据过期、证据冲突或问题需要真实云效/数据库/运行时事实时，回答必须返回需要补充的证据，不能伪装成全知客服。
- 知识库不替代 Harness task history；history 记录一次任务发生了什么，knowledge 记录可复用结论。

## Next Stages

1. 初始化 `/Users/lym/WorkCode/ai/his-knowledge` 并导入 seed。
2. 增加 Manager UI 的知识库状态卡，显示 home、SQLite 是否存在、seed 导入时间和 Obsidian vault 路径。
3. 增加 candidate review 页面，只允许人工审核后推广。
4. 后续自动学习 loop 只能创建 candidate，不能自动 promote。
