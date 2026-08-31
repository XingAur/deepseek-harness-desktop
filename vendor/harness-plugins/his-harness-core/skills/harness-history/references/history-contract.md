# Harness History v1 (plugin-owned)

## 目录合同

```text
HarnessHistory/
├── README.md
├── index.json
└── YUNXIAO/
    └── DFHIS-<编号>/
        ├── README.md
        ├── task.json
        ├── evidence/
        │   └── revisions/
        │       └── <YYYYMMDD-HHMMSS>/
        │           ├── requirement_evidence.v2.json
        │           ├── requirement_evidence.v2.md
        │           └── files/
        ├── runs/
        │   └── <YYYYMMDD-HHMMSS>/
        │       ├── run.json
        │       ├── run-state.json
        │       ├── STATUS.md
        │       ├── evidence-manifest.json
        │       ├── intake/request.json   # 一键入口批次必需
        │       ├── events/
        │       ├── interactions/
        │       ├── stage-records/
        │       ├── projects/
        │       ├── analysis/
        │       ├── decisions/
        │       ├── reviews/
        │       ├── changes/
        │       ├── verification/
        │       └── apply-back/
        │           └── <sequence>-delivery-reconciliation-<project>.json
        └── worktrees/
            └── <YYYYMMDD-HHMMSS>/
```

## 事实来源

- `evidence/revisions/` 保存当次读取到的原需求、父需求、评论、关系和附件，是需求事实。
- `runs/` 保存 Codex 当次处理过程，是处理事实。
- `worktrees/` 是可变代码工作区，不是历史真相；最终历史以基线提交、补丁、diff、评审和验证记录为准。
- `index.json` 是可重建索引，不是事实主存储。
- `projects/*.json` 的 `historical_commits` 只记录通过目标仓库 Git 历史精确命中的提交，不记录推测值。

## 不可覆盖规则

- 一个 `<ticket-id>/<run-id>` 只能创建一次。
- 证据目录、`run.json`、证据清单和项目映射使用独占创建。
- 同一需求再次读取或处理时创建新的 `run-id`。
- 评审、决策、验证和改动后续采用单调事件序号文件追加，不修改旧记录。

## 处理状态

`run.json.stages` 固定包含：

- `project_mapping`
- `analysis`
- `change_decision`
- `implementation`
- `codex_review`
- `verification`
- `apply_back`

阶段状态只是投影；能否修改必须由独立决策记录说明，评审是否通过必须由独立评审记录说明，不能以目录存在或命令成功代替。

`events/` 和各阶段目录中的记录只追加。`run-state.json` 与 `STATUS.md` 从事件重建，因此不是不可变事实文件。

## 待答交互与自动续跑

- `request-interaction` 追加 `interaction_request`，字段包括唯一 `interaction_id`、问题、可选项、`resume_stage` 和 `next_action`。同一 run 同时只能存在一个未恢复交互。
- `resolve-interaction` 追加 `interaction_resolution`，保存用户原始答复，并把投影状态变为 `resolved_resume_required`。
- 编排器看到 `resolved_resume_required` 后必须自动执行记录的下一动作；开始续跑时由 `resume-interaction` 追加 `interaction_resume`，不能再要求用户输入“继续”。
- `pending-interaction` 是每轮恢复入口：返回 `awaiting_user`、`resolved_resume_required` 或 `none`。`run-state.json.interaction` 同步保存该投影。
- 三类结构化记录均位于 `interactions/`，并与对应事件序号一一匹配；旧记录不可覆盖。

## 本地变更生命周期

1. `record-decision` 必须在需求门禁、项目映射和只读分析通过后执行。
   `can_change` 必须列出完整项目集合，并为每个项目保存精确相对文件白名单；
   不在该集合内的项目不能创建 worktree，不在白名单内的文件不能归档补丁。
   rename/copy 必须同时校验源路径和目标路径，不能只校验目标路径。
2. `cannot_change` 保存原因、证据和 blockers，并将 implementation 标为
   `skipped`。
3. `can_change` 才能执行 `create-worktree`。worktree 固定为 detached HEAD，
   不隐式创建分支或提交，基线提交记录在 `changes/*-worktree-*.json`。
4. `archive-patch` 要求 worktree HEAD 仍等于基线、无未跟踪文件且
   `git diff --check` 通过；保存 binary/full-index patch、SHA-256 和文件清单。
   任一项目的新补丁都会使旧评审、旧验证和旧回写结论失效。
5. `record-review` 保存结构化 findings。存在未解决的 Critical 或 Important
   时禁止 `passed`；评审必须覆盖决策中的全部项目。失败且不能修复时必须
   保存原因，同一项目和 SHA-256 组成的补丁集合不能通过重新归档来绕过。
6. `record-verification` 保存实际命令、退出码和结果；只有 Codex 评审通过后
   才能验证。
7. `apply-back` 只回写原项目本地工作区。要求评审与验证通过、原项目 HEAD
   等于基线，并且工作区为空或已经完整包含同一补丁。任何未跟踪文件、
   非本任务改动、部分补丁或基线漂移都必须 `blocked`。真正应用前重新校验
   整份历史；应用后重新生成 full-index binary diff，必须与归档补丁逐字节
   一致，否则尝试安全反向撤销并记录 `blocked`。新增文件通过临时 Git index
   纳入精确快照，不污染原项目 index，也不会把本任务新增文件误判为无关文件；
   显式白名单内且以 `git add -f -N` 归档的 ignored 新文件同样纳入快照。
8. apply-back 不创建分支、不提交、不推送；这些动作需要独立授权。
9. `reconcile-delivery` 是只读一致性门禁。它在本地回写后把原仓库工作区、指定本地提交或已回读的 remote tracking ref 与归档的 binary/full-index patch 逐字节比较，并追加不可覆盖记录。目标包含其他需求、基线不在目标提交历史内、目标 ref 不可解析或补丁不一致时，记录 `blocked` 或 `mismatch`；不得称为已交付。该命令不会 fetch、提交、推送或创建远端对象。

## 结构化记录

- `decisions/*-change_decision.json`
- `interactions/*-interaction_request.json`
- `interactions/*-interaction_resolution.json`
- `interactions/*-interaction_resume.json`
- `changes/*-worktree-<project>.json`
- `changes/*-<project>.patch`
- `changes/*-patch.json`
- `reviews/*-codex_review.json`
- `verification/*-verification.json`
- `apply-back/*-apply-back-<project>.json`
- `apply-back/*-delivery-reconciliation-<project>.json`

每条记录携带事件序号和时间，事件同时更新对应阶段状态。补丁记录使用相对
路径，校验时必须重新计算 SHA-256。

## 安全

- 证据必须为 `readonly`，且 `policy.allowed_actions` 只能是 `["read"]`。
- 成功下载的文件必须位于证据包内，使用相对路径并匹配大小与 SHA-256。
- 证据包禁止符号链接。
- worktree 路径必须位于对应需求和批次的 `worktrees/` 下。
- 一键入口创建的批次将 `run.json.intake_policy` 标为 `required`，缺少
  `intake/request.json` 时校验失败；旧版和显式手工归档批次保留
  `legacy_optional` 兼容策略。
- 事件创建使用进程锁分配连续序号；校验时重新检查事件文件名、序号、类型、
  对应结构化记录和批次/证据/worktree 集合一致性。
- 早期导入批次如果在事件账本引入前已归档证据，只能追加带证据 JSON SHA-256
  的 `legacy-import.json`；不得伪造或改写历史事件。
