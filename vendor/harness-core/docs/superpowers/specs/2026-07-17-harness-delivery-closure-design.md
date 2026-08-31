# HIS Harness Delivery Closure 受控交付闭环设计

## 状态

- 日期：2026-07-17
- 前置版本：HIS Harness v0.64 个人本地企业级核心
- 阶段：v0.65 Git 交付闭环已实现并通过离线企业门禁验收
- 优先级：高于 HarnessManager 正式产品设计、团队分发和服务器部署

实现边界：本阶段只落地 Git 交付闭环。云效评论、附件、状态、负责人和迭代写入仍
保持冻结，不能由 Git 交付确认隐式授权。

v0.65 采用保守冲突策略：所有 cherry-pick 冲突都 abort 并恢复，交由用户处理。
没有冲突但原始 patch 文本因行号或基线上下文不同而变化时，只有文件集合完全一致、
没有白名单外改动且稳定 patch-id 一致才按 `patch_id_equivalent` 放行；无法证明
等价时统一按 `unresolved_semantic_difference` 阻断。

## 1. 结论与目标

v0.64 已完成“需求证据 -> 受控改码 -> 专项验证 -> 本地应用 -> 人工验收登记”的个人本地研发闭环，但尚未形成 Git 和云效外部交付闭环。本阶段补齐以下能力：

```text
release 源码工作区修改
-> 用户在 release 工作区完成运行时验收
-> 原源码目录创建需求分支
-> 精确提交
-> 可选推送需求分支
-> 原源码目录切换 RC 并 cherry-pick
-> 用户在 RC 完成二次运行时验收
-> 可选推送 RC
-> 可选云效评论
-> 可选云效状态/负责人/迭代流转
-> 回读验证、审计和失败恢复
```

完成后，HarnessManager 只需要消费稳定的交付状态和审计产物，不需要在界面开发阶段重新定义 Git、云效或恢复规则。

## 2. 核心原则

1. `release_2.15.3_250515` 是需求开发和第一次本地测试的源码工作分支，允许存在未提交的当前需求改动；禁止直接在 release 上创建需求 commit，也禁止把带需求改动的 release 推送远端。
2. 用户在 release 源码工作区完成第一次运行时验收后，Harness 才能在原源码目录基于当前 release HEAD 创建 `feature-DFHIS-<id>` 或 `hotfix-DFHIS-<id>`，未提交需求改动随分支切换进入任务分支。
3. 需求 commit、需求分支 push、RC 切换、cherry-pick、RC 二次测试和 RC push 全部在原业务源码目录执行。临时 worktree 只用于改码试验和自动验证，不创建交付分支、不生成交付 commit、不执行 cherry-pick 或 push。
4. 默认不推送、不合入 RC、不写云效。远端 Git 和云效写动作必须出现在可审查交付计划中，并由用户显式确认该计划。
5. `RC_2.16.1_250514` 在原源码目录使用一个或多个精确需求 commit 的 `cherry-pick` 集成，不使用需求分支 merge。cherry-pick 后必须完成第二次本地运行时测试，测试通过后才允许推送远端 RC。
6. 不执行 `git add .`、`git commit -a`、匿名 stash、破坏性 reset 或无法审计的工作区清理。
7. Harness 只能自动移动已由 journal、patch hash 和文件 hash 证明归属的改动；无法区分归属时必须阻断。
8. Git 和云效外部写入不是跨系统原子事务。每个成功步骤必须形成 checkpoint，失败后从 checkpoint 恢复，不伪装成全量回滚成功。

## 3. 可配置交付规则

默认个人规则如下，后续通过项目/Profile 配置覆盖，不在执行器内硬编码票据特例：

```json
{
  "base_branch": "release_2.15.3_250515",
  "requirement_branch_template": "feature-DFHIS-{id}",
  "bug_branch_template": "hotfix-DFHIS-{id}",
  "requirement_commit_template": "feat: DFHIS-{id}-{url} 《{title}》",
  "bug_commit_template": "fix: DFHIS-{id}-{url} 《{title}》",
  "integration_branch": "RC_2.16.1_250514",
  "integration_method": "cherry-pick",
  "push_feature_default": false,
  "push_integration_default": false,
  "yunxiao_comment_default": false,
  "yunxiao_transition_default": false
}
```

配置解析必须沿用现有 ResolvedConfig 和 system hard guards。运行快照保存最终规则、来源层和 SHA-256；中途修改配置不改变已确认交付计划。

## 4. 交付模式

### 4.1 本地开发模式

用户只要求分析、改码或验证时，沿用 v0.64 的临时 worktree、专项验证和本地应用能力。验证通过的 patch 回写原业务目录后，用户在 `release_2.15.3_250515` 源码工作区启动项目完成第一次运行时测试。

此阶段不自动创建需求 commit，不推送 release 或其他分支，也不执行 RC 和云效动作。临时 worktree 验证结束后必须清理，不能把临时 worktree 中的分支或 commit 当成交付结果。

### 4.2 完整交付模式

用户明确要求“创建分支”“提交”或“按完整交付流程处理”时，进入 Delivery Closure。patch 先进入原业务目录的 release 工作区供用户第一次测试；测试通过后再按第 6 节在原源码目录创建任务分支并提交。如果 patch 已经位于其他分支的工作区，则必须先完成改动归属和起点检查，不能直接提交。

用户在 release 源码工作区确认测试通过后，Harness 才进入完整交付模式：在原源码目录创建任务分支并提交。commit 不等同于 push 授权；需求分支 push、RC cherry-pick、RC push、云效评论和状态流转分别保留为交付计划中的独立动作。

## 5. 工作区状态分类

执行前读取当前分支、HEAD、index、worktree、未跟踪文件、submodule 状态和目标文件 hash，并归类为：

1. `clean`：工作区干净，允许开始新的 release 开发或执行已提交需求的 RC 集成。
2. `task_owned_exact`：全部改动与当前 Harness `final.diff`、local-apply journal 和允许路径完全一致。
3. `mixed_separable`：当前任务 patch 可由 journal 精确识别，其他文件或其他 hunk 与任务 patch 不重叠。
4. `ambiguous_overlap`：同一文件或同一 hunk 混有无法证明归属的修改。
5. `unsafe_repository_state`：merge/rebase/cherry-pick 进行中、未解决冲突、submodule 漂移、Git 元数据异常或基线缺失。

只有前三种状态允许自动继续。后两种状态输出阻断原因、文件/hunk 范围和恢复建议，不执行 stash、switch、commit 或 push。

## 6. Release 源码工作区到需求分支

### 6.1 全部属于当前任务

当前分支为 `release_2.15.3_250515`、第一次运行时验收通过且改动为 `task_owned_exact` 时：

1. 保存安全快照和事务 journal。
2. 验证当前 release HEAD 与第一次运行时验收绑定的 branch/HEAD 一致，当前 patch hash 与验收 patch 一致。
3. 在原业务目录执行 `git switch -c <task_branch>`；未提交需求改动随工作区进入任务分支。
4. 验证任务分支起点等于记录的 release HEAD，release 分支引用本身没有新增 commit。
5. 在原业务目录执行 `git diff --check`、专项验证和 precommit。
6. 使用显式允许路径暂存并创建需求 commit。
7. 任一步骤失败时恢复原 release 分支、HEAD、index、worktree 和未跟踪文件；无法精确恢复时标记 `recovery_required`。

迁移和 commit 完成后，原业务目录位于任务分支，工作区不存在当前需求的未提交遗留。`release_2.15.3_250515` 仍指向需求开始时的原 commit，没有产生需求 commit，也没有被推送。

### 6.2 当前任务与其他改动可分离

状态为 `mixed_separable` 时：

1. 当前任务 patch 由 Harness journal 和 patch hash 提取。
2. 其他改动写入命名的 Harness Safety Shelf。
3. Safety Shelf 校验通过后，受控清理原分支的已保存改动。
4. 按 6.1 在原源码目录将已完成第一次测试的当前任务迁移到需求/缺陷分支并提交。
5. 完整交付流程结束后，按计划切回启动事务前的 release 分支和 HEAD，并恢复 Safety Shelf 中的其他改动；如果没有其他改动，则正常流程结束时原源码目录保留在已完成二次测试的 RC 分支。
6. 恢复后逐文件校验 hash、index/unstaged 身份和未跟踪文件；不一致时标记 `recovery_required`，保留全部证据，不删除 shelf。

如果其他改动与当前任务在同一 hunk 重叠，状态必须升级为 `ambiguous_overlap`，禁止自动拆分。

## 7. Harness Safety Shelf

Safety Shelf 不使用匿名 `git stash`。它位于 Git 元数据目录下的 `his-harness/delivery/<transaction_id>/`，至少包含：

- 启动分支和 HEAD。
- index patch、worktree patch和二进制 patch。
- 未跟踪文件清单、受控副本和 SHA-256。
- 当前任务 patch、其他改动 patch及各自允许路径。
- 所有目标文件前置 hash。
- 恢复顺序、检查命令和状态机 journal。
- 创建时间、任务 ID、run ID、配置 hash和确认计划 hash。

Shelf 只有在恢复成功、用户确认或保留策略明确允许时才能清理。凭证文件、Git ignored secret 和白名单外超大文件默认不复制；发现这类文件时阻断自动暂存并提示具体风险，不输出内容。

## 8. 用户验收与 Commit 门禁

release 源码工作区中的 patch 通过静态检查不等于可以创建需求分支或提交。commit 前必须同时满足：

1. 核心闭环、diff review、专项验证和 precommit 通过。
2. 用户已在 release 源码工作区登记第一次真实运行时验收结果，且绑定当前 task run、release HEAD、patch hash和验证场景。
3. 当前 diff 与用户验收时 diff 一致；用户验收后发生文件漂移则验收失效。
4. 当前分支名称和起点符合交付规则，且不是 base/integration 分支。
5. 暂存内容只包含当前任务允许路径，staged diff hash 与待提交任务 patch一致。

执行器使用显式路径 `git add -- <paths>`，随后读取 `git diff --cached --binary` 做逐路径和 hash 复核。禁止空提交、混合提交和未经验证的新增文件。

commit 成功后必须确认：

- commit message 与配置模板完全一致。
- commit 的 parent 是已记录任务分支 HEAD。
- commit diff 与验收 patch一致。
- 工作区不存在当前任务遗留改动。
- unrelated shelf 仍完整，或已按计划恢复。

## 9. 需求分支推送与 RC Cherry-pick

### 9.1 交付计划确认

远端动作执行前生成不可变 `delivery_plan.json/md`，列出：

- 原源码 repo、remote、release、task branch、一个或多个 commit SHA和 integration branch。
- 是否推送需求分支。
- 是否在原源码目录切换 RC、cherry-pick、登记二次验收和推送 RC。
- 是否写云效评论、上传附件或执行状态/负责人/迭代流转。
- 每一步前置条件、验证命令、不可逆影响和失败恢复边界。

计划生成 SHA-256，但正常用户不需要记忆或手工输入确认码。Harness 根据当前事务状态只展示当前可执行的下一阶段、具体动作和不会执行的动作；用户在该计划上下文中回复“可以”“继续”“确认”等自然语言，或未来在 Web UI 点击确认，即形成绑定 `plan_hash` 的批准事件。

默认个人交互只有两个交付暂停点：

1. release 第一次测试通过后，Harness 展示“创建任务分支 -> commit -> 推送需求分支 -> 切换并同步 RC -> cherry-pick”的完整计划，并明确本阶段不会推送 RC。用户一次确认后执行到 `waiting_rc_runtime_acceptance`。
2. RC 第二次测试通过后，Harness 展示待推送 RC、commit 列表、远端变化和验证结果。用户一次确认后才推送 RC。

用户可用自然语言说明“只提交不推送”“暂不切 RC”等例外，不要求记忆固定口令。确认只授权当前展示的计划快照；branch、commit、diff、远端引用或云效状态变化后必须重新展示并确认。未列入计划的动作即使配置允许也不能执行。

### 9.2 需求分支推送

只有计划包含 `push_feature=true` 时才推送。推送前读取远端同名分支：

- 不存在：允许创建。
- 指向相同 commit：幂等跳过。
- 是本地 commit 的可证明祖先：按策略允许 fast-forward。
- 存在分叉或未知提交：阻断，不 force push。

### 9.3 RC 集成

只有计划包含 `cherry_pick_integration=true` 时，才在原业务源码目录执行 RC 集成：

1. 确认需求分支 commit 已创建，原源码工作区不存在当前需求的未提交遗留；有 Safety Shelf 时确认其完整可恢复。
2. 在原源码目录切换 `RC_2.16.1_250514`，获取并验证当前本地和远端 RC 引用。
3. 检查一个或多个需求 commit 尚未通过 patch-id 或交付审计集成，防止重复 cherry-pick。
4. 按交付计划记录的顺序执行 `git cherry-pick <sha1> [<sha2> ...]`。
5. cherry-pick 冲突时立即在原源码目录执行 `git cherry-pick --abort`，验证 RC 回到集成前 HEAD，停止自动集成并交由用户处理；当前个人模式不自动解决任何冲突。
6. cherry-pick 成功后先执行第 9.5 节的提交增量一致性审计，再运行 `git diff --check`、专项命令和集成 smoke；全部通过后进入 `waiting_rc_runtime_acceptance`。
7. 用户使用原源码目录完成 RC 第二次运行时测试并登记通过；RC HEAD、任务增量或工作区发生漂移时验收失效。
8. 只有计划包含 `push_integration=true`、一致性审计通过且第二次验收仍有效时才推送 RC；禁止 force push。
9. RC push 完成后验证远端 RC 指向预期提交，并再次核对远端提交增量 hash。没有其他 Safety Shelf 改动时，原源码目录保留在干净的 RC 分支。

需求分支已经推送但 RC 集成失败时，不删除远端需求分支。事务停在 checkpoint，输出失败原因和可复跑计划。失败恢复完整且任务 commit 已登记时，重跑第一次确认必须幂等复用已有 commit，不重复创建任务分支或提交。

### 9.4 Cherry-pick 冲突处理

当前个人模式不自动解决任何 cherry-pick 冲突。发生冲突后必须立即执行
`git cherry-pick --abort`，确认 RC 恢复至集成前 HEAD，然后输出冲突报告交由用户
处理。报告包含冲突文件和 hunk、双方 commit、冲突原因及建议处理方向，但不修改
冲突内容、不执行 `git cherry-pick --continue`，也不声称已经解决。

### 9.5 Cherry-pick 后提交增量一致性审计

Harness 不能用“整个文件是否完全相同”判断 cherry-pick 是否正确，因为 RC 可能已经包含 release 没有的其他需求。审计必须同时构建：

- `expected_task_delta`：按计划顺序合并一个或多个需求 commit 相对其起点产生的文件、hunk、二进制 hash、增删和最终任务语义。
- `actual_rc_delta`：RC 集成前 HEAD 到 cherry-pick 后 HEAD 的实际文件、hunk、二进制 hash和增删。
- `rc_final_state`：cherry-pick 后当前任务涉及路径的最终内容和行为契约结果。

逐文件、逐 hunk 对比后，差异只能归入以下类型：

1. `exact_match`：实际任务增量与需求提交完全一致。
2. `already_present_equivalent`：RC 在集成前已存在相同代码或行为，导致实际新增内容少于需求提交；最终状态和契约一致。属于正常差异，但必须列出已存在的 commit/行和原因。
3. `patch_id_equivalent`：RC 与需求 commit 的文件集合完全一致、没有白名单外改动且稳定 patch-id 一致；原始 patch 文本仅因行号或基线上下文不同。属于正常差异，仍须执行专项验证并等待用户 RC 二次测试。
4. `unexpected_missing`：需求提交中的有效改动未进入 RC，且不能由已存在等价代码解释。
5. `unexpected_extra`：RC 新增了需求提交和允许路径之外的改动。
6. `unresolved_semantic_difference`：文本可以解释但业务行为存在两个以上合理结果，或无法证明最终状态等价。

`unexpected_missing`、`unexpected_extra` 和 `unresolved_semantic_difference` 都是阻断问题。Harness 必须说明具体文件、hunk、预期、实际和可能原因，不得推送 RC。

如果问题属于当前任务且修复方式唯一、低风险、未混入 RC 其他需求，Harness 可以自动修复，但修复必须回到原源码的任务分支形成新的任务 commit并完成专项验证；随后将该修复 commit 继续 cherry-pick 到 RC，重新执行完整一致性审计。禁止只在 RC 上偷偷改一份而让任务分支缺少修复。

如果差异属于高风险业务、RC 特有业务规则、冲突语义不明确或无法在任务分支形成统一修复，Harness 停止并交由用户决定。确认属于正常差异时，审计报告明确说明原因和证据，不把它伪装成 exact match。

审计产物至少包括 `cherry_pick_parity.json/md`、expected/actual patch hash、文件状态矩阵、差异分类、自动修复记录和 RC push blocker。

## 10. 云效交付事务

现有 `dry-run/fake/real` 事务骨架继续复用，但必须在 v0.64 企业门禁之上重新验收，不直接视为已开放能力。

### 10.1 评论

评论只能从当前真实产物生成：需求编号、分支、commit、改动文件、改动说明、自动验证、用户运行时验收、截图/证据和残余风险。不存在的提交、推送或测试不得写入评论。

真实评论要求：

- 专用写凭证，不复用只读 token。
- 当前用户批准事件绑定的 `delivery_plan` 已明确包含 comment 动作。
- 写入前按幂等 marker 回读检查。
- 写入后回读验证 marker 和评论摘要。
- 回读失败标记 `verify_failed`，不自动重写第二条评论。

### 10.2 状态、负责人、迭代和其他字段

不得硬编码中文显示名直接写入。执行顺序为：

1. 只读获取工作项当前状态、字段定义、可选值和允许流转。
2. 生成 before/after 和字段 ID 映射证据。
3. 使用 fake/replay 覆盖合法、非法、并发漂移和重复执行。
4. 用户确认的交付计划明确列出每个字段动作。
5. 写入前再次回读 current state；与计划不一致则阻断。
6. 写入后回读目标字段并登记结果。

关闭任务、生产发布和无法可靠回读的字段继续默认冻结，必须作为独立阶段验收。

## 11. 状态机与恢复

交付事务至少包含以下状态：

```text
planned
-> snapshotted
-> base_verified
-> patch_applied_to_release
-> waiting_release_runtime_acceptance
-> release_runtime_accepted
-> task_branch_ready
-> precommit_passed
-> committed
-> delivery_confirmed
-> feature_pushed (optional)
-> integration_cherry_picked (optional)
-> integration_parity_passed (optional)
-> waiting_rc_runtime_acceptance (optional)
-> rc_runtime_accepted (optional)
-> integration_pushed (optional)
-> yunxiao_commented (optional)
-> yunxiao_transitioned (optional)
-> completed
```

异常状态包括 `blocked`、`failed`、`interrupted` 和 `recovery_required`。进程重启后必须从 journal 和外部回读结果恢复，不能仅凭数据库中的上次状态继续写入。

每一步均记录输入 hash、前置状态、命令摘要、退出码、后置状态、远端请求 ID 和证据路径。日志必须脱敏，不保存 token、Authorization header 或完整敏感响应。

## 12. 验收标准

### 12.1 本地 Git fixture

至少覆盖：

1. release 源码工作区允许当前任务未提交改动，但不能在 release 创建需求 commit 或推送 release。
2. release 第一次测试通过后，在原源码目录创建任务分支并 commit，release 引用不变化。
3. release 上当前任务与其他文件改动可分离时，其他改动完整 shelf 和恢复。
4. 同一 hunk 混合改动时阻断。
5. 用户验收后 diff 漂移时禁止 commit。
6. staged diff 出现白名单外文件时禁止 commit。
7. 迁移任一步骤 failure injection 后恢复 branch、HEAD、index、worktree 和未跟踪文件。
8. 已存在任务分支的幂等复跑、错误起点和分叉检测。
9. 原源码目录 RC cherry-pick 成功、冲突 abort、验证失败、二次运行时验收、重复 patch-id 和进程中断恢复。
10. 所有默认路径均不执行 push。
11. 单个和多个 commit 的 expected/actual 增量完全一致时通过。
12. RC 已存在等价代码时输出 `already_present_equivalent` 和证据，不误报少改。
13. 多改、少改和无法证明的语义差异阻断 RC push；唯一低风险修复回到任务分支形成新 commit 后重新 cherry-pick 和审计。

### 12.2 Git 远端 fixture

使用本地 bare repository 覆盖首次推送、幂等推送、远端分叉、非快进阻断、RC 推送关闭和显式开启；不连接真实业务远端。

### 12.3 云效 fixture/fake/replay

覆盖评论幂等、回读失败、状态漂移、字段映射缺失、非法流转、重复执行、附件失败和凭证脱敏。真实云效写入必须在全部离线验收通过后，使用低风险专用测试工作项逐动作授权验证。

### 12.4 企业门禁

Delivery Closure 新增独立门禁，并重新运行 v0.64 全量企业门禁。默认测试不得访问真实 Git 远端、云效、模型、业务 PG 或部署环境。

## 13. 非目标与冻结项

- 本阶段不重新设计 HarnessManager 正式 UI；只定义未来 UI 消费的交付状态和动作契约。
- 不恢复真实模型或真实模型 DAG。
- 不自动查询业务 PG。
- 不自动部署、发布或关闭云效工作项。
- 不 force push、不改写远端历史、不自动删除远端分支。
- 不在临时 worktree 创建需求分支、交付 commit、RC cherry-pick 或执行 push。
- 不将代码级、自动化或 fake 验证描述为真实业务验收。

## 14. 对未来 Web UI 的稳定接口

未来产品界面只消费以下稳定对象：

- `delivery_policy_snapshot`
- `workspace_classification`
- `delivery_transaction`
- `safety_shelf`
- `runtime_acceptance`
- `commit_record`
- `delivery_plan`
- `remote_git_result`
- `yunxiao_transaction_result`
- `recovery_status`

页面负责展示、预览和收集精确确认，不复制 Git、云效状态机或恢复逻辑。这样后续增加团队配置、TAPD 或其他需求来源时，只替换 provider/policy，不推翻核心页面流程。
