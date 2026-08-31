---
name: his-code-evidence
description: Use when reading bounded local source, diff, history, or local verification evidence for a governed engineering task.
---

# HIS Code Evidence

这是 `his-engineering` 的 canonical 只读 Skill，用于把本地代码证据和角色路由
明确绑定。它覆盖：

- `git.diff`：读取当前白名单 worktree 的精确 diff；
- `source.read`、`source.search`：读取和搜索已批准路径；
- `git.history`：读取 bounded commit/blame/history 证据；
- `verification.run-local`：执行已批准的本地专项验证；
- `code.review-local`：对最新完整 diff 做只读审查。

本 Skill 不包含 patch、commit、push、远程写入、数据库写入或部署授权。
需要本地修改时转交 `his-git-local`；需要 commit 或交付时转交
`his-git-delivery`。命令和输出必须受 Harness 的路径、超时、结果大小和敏感
信息门禁约束，代码级证据不能冒充真实运行或生产结论。
