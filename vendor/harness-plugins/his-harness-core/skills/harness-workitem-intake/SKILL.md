---
name: harness-workitem-intake
description: Use when a DFHIS work-item URL or ID must enter Harness before analysis or modification.
---

# Harness Work Item Intake

本技能只负责 provider-neutral 接入和门禁记录，不访问云效网络、不读取 provider 凭证，也不实现平台 API。

## 工作流

1. 接受一个工作项 URL 或 `DFHIS-编号`，拒绝附带凭证、额外文本或不支持的平台。
2. 云效来源先调用 `$yunxiao-workitem-read`，让 provider 在自己的边界内生成只读、已脱敏的证据目录。
3. 将证据目录通过 `--provider-evidence-dir` 交给 `scripts/intake.py`，再由 `$harness-history` 严格校验并归档证据和 intake 记录。
4. `ready_for_analysis` 允许进入只读分析；`needs_requirement_confirmation` 允许先做只读代码侦查、禁止修改，只有侦查后仍无法判定的缺口才向用户确认；读取失败必须停止并说明缺口。
5. 缺少证据目录时明确失败，不回退到旧云效实现或任何写能力。

## 参数合同

能力宿主调用 `process_intake` 时使用与下列命令行同名的规范参数：

```text
--source <work-item-url-or-id>
--provider-evidence-dir <sanitized-readonly-evidence-dir>
--output-dir <HarnessHistory-root>
```

`intake.py` 会拒绝符号链接、缺失合同文件、非只读策略、完整性错误以及
provider 或 ticket 不匹配。程序内可选 adapter 只接收规范化 `source` 和
临时 `output_dir`，不能接收或转发 provider 认证参数。

它只记录规范化编号、provider、run、门禁和完整性，不记录原始 URL 查询参数、请求头或敏感错误文本。

## 安全边界

- 云效证据只能来自 `$yunxiao-workitem-read`。
- 本技能不包含外部写入路由；评论、流转、上传、分支交付、数据库修改和部署均不在接入范围。
- 不支持的平台或读取失败必须 fail closed；不完整证据只允许只读侦查，不能进入 patch、提交、推送、云效写入或部署。
- 接入成功不代表代码已分析、修改、审核或验证。

字段、状态和持久化约束见 `references/intake-contract.md`。
