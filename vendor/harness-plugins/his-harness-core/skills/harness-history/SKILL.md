---
name: harness-history
description: Use when a governed HIS Harness task needs durable task, run, evidence, patch, review, or verification history.
---

# Harness History

本技能保存 append-only 的任务审计历史：需求证据、run、待答交互、项目映射、决策、worktree、补丁、审核、验证和本地回写记录。它不承担知识问答；可复用知识由独立知识技能管理。

## 对话自动续跑

1. 同一任务每轮开始先执行 `pending-interaction`。返回 `awaiting_user` 时，把当前用户消息匹配为原问题的答复；清晰答复立即执行 `resolve-interaction`，不能另开新任务。
2. `resolve-interaction` 返回 `resolved_resume_required` 后，必须在同一轮按记录的 `resume_stage` 和 `next_action` 自动继续。开始继续前执行 `resume-interaction`，不得再等待用户说“继续”。
3. 如果上一轮答复已归档、但执行在续跑前中断，下一轮 `pending-interaction` 会再次返回 `resolved_resume_required`；应直接恢复，不重复提问。
4. 真正需要用户答复前，必须先执行 `request-interaction`，保存唯一问题、选项、恢复阶段和答复后的下一动作。一个 run 同时只允许一个待答交互。
5. 只有答复无法映射、将触发未授权外部写入，或高风险业务仍存在多个合理解释时，才继续保持 `awaiting_user`。

## 处理顺序

1. `archive-evidence` 创建不可覆盖的证据 revision 和 run。
2. `record-project` 保存每个实际仓库和源码证据。
3. `record-decision` 保存 `can_change` 或 `cannot_change`；允许修改时必须列全项目和精确文件白名单。
4. `create-worktree` 只创建任务目录内的 detached、无分支工作区。
5. `archive-patch` 保存最新完整 binary/full-index patch 和摘要。
6. `record-review` 保存结构化审核；未解决的重要问题不能通过。
7. `record-verification` 保存实际命令、退出码和结果。
8. `apply-back` 只在完整历史、最新审核和最新验证均通过时执行安全本地回写。
9. `reconcile-delivery` 在每个交付边界重新对比归档补丁：本地回写用 `WORKTREE`，本地提交用明确 commit/ref，远端交付只检查已回读的 remote tracking ref。它不会创建提交、拉取、推送或访问远端。
10. `validate-task` 在交付前重建并校验整个审计链。

所有命令由插件自带的 `scripts/history_manager.py` 提供。详细参数和记录结构见 `references/history-contract.md`。

## 边界

- 新补丁会使旧审核、验证和回写结论失效。
- 待答交互使用 `request-interaction → resolve-interaction → resume-interaction` 追加记录；用户清晰答复不是新的任务触发器。
- 原仓库基线、文件范围或补丁不一致时必须停止，不做部分回写。
- “本地已回写”“本地提交已核对”“远端已回读核对”是三个不同状态。没有相应的 `reconcile-delivery` 一致性记录时，不得把前一状态表述为后一状态。
- `events/` 和结构化记录是事实来源；投影文件可以重建。
- 任务历史与知识库职责分离：前者证明本次发生了什么，后者只保存可复用知识。
- 云效写操作、远端 Git 交付、数据库修改、发布和部署不属于本技能，均需独立能力和明确授权。
