# Harness 基础代码证据与完整审核能力设计

## 目标

让个人用户只需输入问题或需求，Harness 就能自动完成与代码有关的只读取证、完整变更采集、确定性验证和结构化审核；用户不需要预先选择 Git、源码、验证或 Reviewer 能力。

本设计补齐的是通用基础能力，不替代已经完成的本地真实 Agent 修改闭环。最终个人使用流程为：

```text
输入问题或需求
  -> 自动意图路由
  -> 自动选择最小只读证据能力
  -> 冻结代码证据
  -> 必要时在隔离环境验证
  -> 必要时由只读 Reviewer 审核
  -> 仅需求修改流程进入 Worker
  -> 再次冻结最终 diff、验证、审核
  -> 人工确认
  -> 本地应用
```

## 已确认的现状缺口

- 正式能力清单只有 `git.inspect`、`git.apply-local`、`git.commit-local` 等能力，没有正式 `git.diff`。
- 底层 `repo.diff.read` 只返回文件数量和增删行数，不返回完整补丁，不能判断逐行正确性。
- `git.inspect` 能返回 HEAD、分支和状态路径，但不能提供源码正文、全文搜索、逐行历史或完整审核证据。
- Local Agent 内部已经能生成 `final.diff`、`final.patch`、verification、manifest 和 review 工件，但这些能力被绑定在 Agent run 内，普通咨询和已有工作区审核无法复用。
- 当前通用需求能力路由不会自动准备源码、diff、历史、验证和 Reviewer 证据。
- Manager 只能展示有限状态，尚不能证明一次代码审核使用了完整、未变化且可重验的证据。

因此，现有状态只能称为“专用 Agent 闭环已具备部分完整证据”，不能称为“Harness 普遍具备代码审核能力”。

## 方案选择

### 采用：不可变证据工件流水线

所有读取能力先生成受控、可哈希、可重开的证据工件，再由验证器和 Reviewer 消费。能力返回值只包含受控摘要和工件引用，不把任意大段 Git 输出直接塞进 Manager JSON。

优点：

- 可以覆盖大 diff、未跟踪文件、二进制和多文件变更，不依赖响应截断。
- Reviewer 审核的是冻结工件，不是随后可能变化的工作区。
- 每个下游步骤都能按路径、大小、SHA-256 和仓库身份重验。
- 敏感内容、超限、工作区竞态或证据缺失会阻断审核，而不是静默漏掉。
- 可以复用 Local Agent 已验证的 no-follow、工件哈希、Reviewer 和验证边界。

### 不采用：让 `repo.diff.read` 直接返回完整字符串

这会把大小限制、截断、秘密泄漏和工作区竞态交给普通 JSON 响应处理，无法证明 Reviewer 看到的是完整补丁。

### 不采用：所有代码问题都启动 Local Agent

普通解释、源码查询和历史定位不应该创建可写 Worker。只读问题应停在最小证据层；只有明确需要修改且路由为 task/mutation 时才进入 Worker。

## 能力基线

### 1. `git.inspect`

保留现有能力，作为仓库身份和状态入口。它必须返回受控的 repository root、HEAD、branch、operation markers、tracked/staged/untracked/renamed 路径清单和远端名称，但不返回凭证或 remote URL。

### 2. `source.read`

读取一个或多个明确相对路径的源码文件，固定约束：

- 仓库和路径必须来自已经验证的 repository scope。
- 使用目录 FD 和 no-follow 打开；拒绝符号链接、特殊文件、`.git`、敏感路径和越界路径。
- 对每个文件保存相对路径、mode、size、SHA-256、编码和有界内容工件。
- 读取前后复核文件身份；变化时整次证据失败。
- 发现 secret、credential、token 或不允许的二进制内容时阻断，不以脱敏后内容冒充完整源码。

### 3. `source.search`

在批准的仓库 scope 内执行受控全文搜索：

- 只接受 pattern、固定大小限制和可选路径前缀，不接受任意 shell/命令参数。
- 使用固定 `/usr/bin/rg` 身份或等价的内置扫描器；参数数组启动，禁止 shell、配置文件和环境注入。
- 返回匹配文件、行号、受控上下文和文件 SHA-256；按文件和总结果设硬上限。
- 结果超限时标记 `incomplete` 并阻断依赖“全仓无其他引用”的结论，不能静默截断后继续批准。

### 4. `git.diff`

这是本轮首要门禁。它必须生成完整、可复验的 diff bundle：

- HEAD、index 和 worktree 三层状态。
- tracked、staged、unstaged、untracked、added、deleted、renamed、mode change、binary 和 gitlink 分类。
- 规范 `--binary --full-index` 补丁；未跟踪文件用受控 no-index 补丁纳入。
- `git diff --check` 结果。
- 每个文件的 before/after mode、size、SHA-256、增删行数和内容分类。
- bundle manifest、完整 patch、按文件分片和总 SHA-256。
- 采集前后复核 HEAD、index、worktree 路径集合和文件身份；任一变化则废弃整个 bundle。

Git 执行必须关闭 hooks、pager、external diff、textconv、fsmonitor、replace objects、attributes filter、用户/系统 Git 配置和网络。不得修改 worktree、index、HEAD、branch、refs 或 common `.git`。

敏感路径或内容、特殊文件、无法安全表达的 submodule/gitlink、文件/总量超限均返回稳定 blocker。不得省略后继续声明“完整审核”。

### 5. `git.history`

提供受控 `log`、`show` 和 `blame` 证据：

- ref 只能是验证过的 SHA 或固定 HEAD 相对范围，禁止任意 rev expression。
- path 必须经过相同 repository scope 和 no-follow 约束。
- log 返回有界 commit metadata；show/blame 生成哈希工件。
- 历史补丁使用与 `git.diff` 相同的敏感内容和大小门禁。
- 禁止签名 helper、textconv、pager、external diff、hooks 和网络。

### 6. `verification.run-local`

确定性验证不在用户原工作区直接执行：

- 从冻结 HEAD 和 diff bundle 建立一次性隔离 workspace。
- 只接受任务合同或项目配置中已经验证的 argv 数组，不接受 shell 字符串。
- 固定最小环境，默认无网络，设置进程组、超时、输出上限和完整清理。
- 运行前应用并重验同一个 diff bundle；运行后检测源码、index、HEAD、common `.git` 和允许范围之外副作用。
- 保存命令标识、return code、stdout/stderr 安全摘要、输出 SHA-256、开始/结束时间和 workspace 复核结果。
- 测试产生的缓存只能位于明确临时目录；任何无法解释的源码变化都使验证失败。

### 7. `code.review-local`

这是编排能力，不直接读取正在变化的仓库：

- 必须消费同一个 immutable evidence bundle 中的合同、源码、完整 diff、历史证据和 verification receipt。
- 输入工件逐个 no-follow 重开并核对 path、owner、kind、size、SHA-256 和 bundle seal。
- Reviewer 使用固定 read-only 角色；输出必须符合严格 schema。
- 路由自动选择审核能力，但真实 Reviewer 还必须有部署级显式启用；未启用时在模型启动前阻断。
- 真实 Reviewer 的结果必须记录 `external_calls=true`，不能把 Codex 模型调用描述为纯本地操作。
- findings path 必须属于 durable changed paths，行号必须可映射到冻结 diff。
- 缺失工件、incomplete 搜索、敏感阻断、diff 竞态、验证未运行或失败时，不能产生 approved。
- review verdict、findings、review hash 和 evidence bundle hash 绑定，并追加审计事件。

### 8. `git.apply-local`

保留既有一次性人工确认和本地应用事务。它只能消费已经 approved 且再次复核未变化的 final patch；不能因为新增只读能力而降低授权、确认、恢复或源仓保护边界。

## 内部基础组件

### Evidence Bundle Store

新增独立于目标仓库的受控证据目录。它不作为用户可选能力暴露，负责：

- 原子写入 manifest、source、search、diff、history、verification 和 review 工件。
- 目录 FD、`O_NOFOLLOW`、owner/mode/link-count 检查和同目录原子 rename。
- 工件类型、相对路径、大小和 SHA-256 精确登记。
- 只追加状态和 seal；sealed bundle 不允许替换或补写。
- 过期清理由后续明确的数据保留策略处理，本轮不自动删除历史证据。

### Evidence Completeness Gate

任何审核结论前统一检查：

- repository identity、HEAD、index/worktree snapshot 是否一致。
- required capabilities 是否全部成功。
- 搜索/diff 是否完整且没有超限、敏感或不支持项。
- verification receipt 是否绑定同一 bundle。
- Reviewer 是否读取同一 sealed bundle。

结果只有 `complete` 或稳定 blocker 集合，不使用“部分证据也大概可以批准”的降级路径。

## 持久化与 schema

为普通咨询和已有工作区审核新增独立证据记录，不能伪造 Local Agent run：

- `code_evidence_bundles`：bundle identity、conversation/task、repository identity、HEAD、状态、seal 和时间。
- `code_evidence_artifacts`：类型、相对路径、SHA-256、大小和 owner bundle。
- `code_evidence_events`：append-only 状态和 blocker 事件。
- `code_reviews`：bundle-bound structured verdict、review hash 和状态。

schema 从 v69 additive 升至 v70。迁移只能先在显式临时数据库完成 v69->v70、并发、污染、备份和恢复测试。正式数据库迁移必须作为独立安装计划列出准确文件清单、数据库备份、失败自动回退和计划哈希；实现阶段不得直接打开正式数据库。

## 自动路由

用户不选择能力，意图服务根据问题自动构造证据计划：

- 一般知识问题：知识检索，不访问仓库。
- “这个方法在哪里/怎么调用”：`git.inspect -> source.search -> source.read`。
- “为什么这样改/谁改的”：再加 `git.history`。
- “这些改动对不对/有没有多余改动”：`git.diff -> source.read/search -> verification.run-local -> code.review-local`。
- 需求询问但未要求修改：完成代码证据和审核，停止在 Worker 前。
- 明确需求修改：先取证，再进入现有 Worker、verification、Reviewer、人工确认、apply 完整闭环。

Provider 缺失、Git 失败、Reviewer 失败或证据不完整只能形成 blocker，不能把 task 降级成普通问答，也不能跳过审核。

## 单仓与多仓

### 第一阶段：单仓原子证据

先完成一个 repository scope 内的完整证据、验证和审核。这是第一批安装门禁。

### 第二阶段：多仓一致性聚合

随后增加 evidence set：

- 每个仓库独立产生 sealed bundle。
- 聚合 manifest 记录仓库顺序、identity、HEAD、bundle hash 和跨仓关系。
- 全部子 bundle 完成后再次复核每个仓库；任何仓库变化使整个 evidence set 失效。
- 不伪称跨文件系统瞬时原子快照；采用“逐仓冻结 + 全集末次复核”的明确一致性合同。
- Reviewer 必须看到全部仓库 bundle，findings 标明 repository alias。

多仓聚合完成前，涉及两个及以上仓库的审核必须返回 `multi_repository_evidence_unavailable`，不得只审其中一个仓库。

## Manager 交互

Manager 只要求用户输入自然语言和必要的项目范围，不要求选择 Git 动作。页面显示：

- 自动识别的问题/需求模式。
- 本次自动选择的证据能力和进度。
- repository/HEAD/bundle hash。
- changed paths、diff check、verification 和 Reviewer verdict。
- blocker、人工确认和本地应用状态。

完整 diff 使用受控分片查看器按 bundle artifact 读取；默认不把大 diff 嵌入普通状态 API。页面不能提供绕过 completeness gate、Reviewer 或一次性确认的按钮。

## 错误处理

- 工作区在采集期间变化：废弃 bundle，稳定返回 `code_evidence_changed`。
- 敏感内容：不保存、不发送模型，返回 `code_evidence_sensitive`。
- 大小或条目超限：返回 `code_evidence_limit_exceeded`，不得截断后批准。
- Git 配置、filter、textconv、hook、symlink 或特殊文件不安全：采集前阻断。
- verification 超时/输出超限/副作用：终止进程组并记录稳定码，Reviewer 不得批准。
- Reviewer 无 schema-valid terminal：记录 failed_review，不进入确认。
- Harness 重启：只从 sealed bundle 和 append-only 状态恢复，不重新信任调用者传入的路径/hash。

## 验收门禁

### 单仓专项

- tracked/staged/unstaged/untracked/add/delete/rename/mode/binary 全矩阵。
- `--binary --full-index` 工件可在隔离仓库 `git apply --check`。
- `git diff --check`、文件统计、before/after SHA-256 与真实内容一致。
- hooks、pager、textconv、external diff、filter、fsmonitor、replace object 零执行。
- symlink、hardlink、目录替换、文件竞态、HEAD/index/worktree 竞态全部 fail closed。
- secret、敏感路径、超大文件、总量超限不泄漏且不产生 approved。
- source search 截断不得被当作完整无引用结论。
- verification 零源仓副作用；超时和顽固 child 被清理。
- Reviewer 缺任一工件、hash 不符、bundle 改变或 findings 越界均拒绝。

### 多仓专项

- 两个和三个仓库的 bundle 聚合、末次复核和 findings alias。
- 任一仓库中途变化使整个 evidence set 失效。
- 一个仓库缺失/超限/敏感时不得对其余仓库给整体 approved。

### 真实个人使用验收

使用无 remote 的临时多文件 Git fixture，至少包含 tracked 修改、新增、删除、rename、binary 和 untracked 文件。Harness 必须自动：

1. 判断为代码审核任务。
2. 生成完整 diff bundle。
3. 读取必要源码和历史。
4. 在隔离 workspace 执行固定验证。
5. 由只读 Reviewer 给出结构化结论。
6. 证明原仓 HEAD、index、worktree 在只读阶段没有变化。

随后再用两个无 remote 临时仓库完成多仓聚合审核。正式安装前运行最终全量回归并生成精确安装计划哈希。

## 实施顺序

1. Evidence Bundle Store 和 v70 临时数据库合同。
2. `git.diff` 完整工件及安全矩阵。
3. `source.read`、`source.search`、`git.history`。
4. `verification.run-local`。
5. `code.review-local` 和 completeness gate。
6. 自动意图/能力路由及 Manager 状态展示。
7. 单仓真实验收。
8. 多仓 evidence set 和真实验收。
9. 全量回归、独立复审、正式安装计划哈希。

## 非目标与不变边界

- 不自动 commit、push、建 PR/MR、写云效、部署或发布。
- 不开放业务数据库写入；数据库仍永久只读。
- 不把自动路由当作外部写或模型数据发送授权。
- 不为本轮引入团队 RBAC、多租户、Worker 集群或分布式队列。
- 不直接修改正式 Harness；完成实现、专项复审、真实验收和最终回归后，才生成需要用户精确确认的正式安装计划。
