# Precommit 范围告警策略

## 结论

目标 diff 验证通过，不等于可以直接提交整个仓库。只要同一业务仓库存在白名单外未提交改动，Harness 必须把目标验证结果和提交范围结论分开。

## 判定规则

- `overall_status=pass`：本需求白名单内 diff 已在临时 worktree 中复现并通过验证命令。
- `can_enter_test=人工代码审查通过后可进入测试`：目标改动可以进入人工审查和测试准备。
- `can_commit=false`：当前仓库还有白名单外 dirty scope，不能直接整体提交。
- `can_yunxiao_comment=false`：提交范围未隔离前，不生成云效交付评论，避免把无关改动一起声明交付。
- `can_yunxiao_transition=false`：云效真实状态流转继续冻结。

## 处理方式

1. 保留目标验证产物，不把范围告警误判为目标功能失败。
2. 提交前人工隔离本需求 diff，或先处理同仓库无关改动。
3. 重新运行 precommit 验证，确认白名单外 dirty scope 已清空。
4. 只有 `can_commit=true` 且人工代码审查通过后，才进入提交动作。
5. 云效评论、状态流转、负责人、迭代和关闭任务仍需独立放权。

## DFHIS-31465 样板

DFHIS-31465 的目标文件验证通过，但 `df-web-guahaosf` 同仓库存在白名单外未提交改动，因此样板结论保持：

- `overall_status=pass`
- `can_enter_test=人工代码审查通过后可进入测试`
- `can_commit=false`
- `can_yunxiao_comment=false`
