# his-harness-core skills

本目录状态为 `canonical`，属于 `his-harness-core` 正式插件。根目录同名 Skill
只是 `compatibility` 代理，不能覆盖这里的治理和审计规则。

- `his-harness`：通用工程治理入口；支持无工作项的本地任务和非 HIS 项目，
  HIS/DFHIS 规则作为按需领域增强。
- `harness-workitem-intake`：接收 provider 只读证据并创建门禁批次。
- `harness-history`：保存任务、run、证据、补丁、审核和验证审计；不承担知识问答。
- `his-requirement-governance`：判断需求完整性、合理性、合规性和一次修改可行性。

云效、Git/GitLab、数据库和知识库均由独立插件技能提供，核心插件不包含它们的内部实现。
