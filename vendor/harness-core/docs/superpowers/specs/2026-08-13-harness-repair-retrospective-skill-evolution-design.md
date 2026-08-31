# Harness 修复复盘与技能进化闭环设计

## 状态

- 日期：2026-08-13
- 阶段：个人版本地真实 Agent 的后续能力设计
- 前置：任务合同、隔离 worktree、确定性验证、独立 Reviewer、人工确认、本地 apply、重试与审计链路
- 本文结论：人工指出的问题是自动学习信号，不是逐条人工批准学习的开关。

## 1. 目标

Harness 应在每次修复、测试失败、Reviewer 打回或人工纠错后，自动形成可追溯的复盘和回归门禁，使下一次同一任务重试以及后续相似任务更早发现同类问题。

目标不是让模型自由改写提示词或业务规则，而是让 Harness 自动积累并执行可验证的“检查规则 + 证据要求 + 回归用例”。学习后的系统必须仍然受任务合同、允许路径、验证命令、Reviewer、外部写入授权和业务验收边界约束。

## 2. 明确不做

- 不自动修改业务代码、任务合同、允许路径或验收标准。
- 不自动授予 Git branch/commit/push、GitLab 写、云效写、部署或数据库写权限。
- 不将模型原始输出、业务数据、截图正文、凭证、URL 查询参数或完整报错保存为长期学习内容。
- 不由单次失败自动生成永久的 HIS 业务口径。
- 不在首版直接改写现有磁盘技能包；学习结果先由 Harness 自己的版本化规则库消费。
- 不把“代码通过”“真实联调通过”“业务验收通过”合并成一个状态。

## 3. 设计原则

1. **人工纠错即触发**：用户明确指出错误、Reviewer 给出 `changes_requested`、验证失败、运行时验收失败或交付后返工，均自动创建复盘事件；不再等待一次额外的“是否学习”确认。
2. **当前任务优先**：复盘得到的修复检查项立即注入同一 run 的下一次 attempt，且必须在重新进入 Reviewer 前通过。
3. **先试运行再稳定**：后续相似任务默认加载试运行规则；只有积累足够独立证据后，才自动升级为稳定技能规则。
4. **证据不可省略**：每条规则必须具有来源任务/attempt、失败类型、修复后验证或 Reviewer 证据哈希、适用范围和反例/排除条件。
5. **安全能力不可学习放宽**：学习只能增加检查、补充证据或限制执行，不能扩大可改路径、命令集合、外部写范围或写入授权。
6. **可撤销和可降级**：后续误报、反例、冲突或过期时，规则自动暂停、降级或失效；不让错误经验永久污染后续编码。
7. **最小通用化**：学习的适用范围以仓库、技术栈、模块/风险分类和稳定错误指纹定义，不从一次特定医院/患者/配置的事实推广成全局规则。

## 4. 闭环

```text
人工纠错 / 测试失败 / Reviewer 打回 / 运行时验收失败
  -> 复盘事件（脱敏、追加式）
  -> 根因分类与学习草案
  -> 当前任务修复门禁（立即生效）
  -> 下一次 worktree attempt
  -> 确定性验证 + 独立 Reviewer
  -> 相似任务试运行规则
  -> 多任务证据计数
  -> 自动稳定技能 / 自动暂停或降级
```

每个步骤都记录状态和证据摘要。任何一步证据不足都只能得到 `insufficient_evidence`，不会生成可执行规则。

## 5. 状态模型

### 5.1 复盘事件

`RetrospectiveEvent` 是一次明确纠错事实，来源为：

- `verification_failed`：合同验证命令失败；
- `review_changes_requested`：独立 Reviewer 拒绝；
- `human_correction`：用户在 run 或交付后明确指出实现、逻辑或验证错误；
- `runtime_acceptance_failed`：已提交/推送后的测试环境或真实运行时失败；
- `delivery_parity_failed`：分支、提交、远端 SHA 或回读结果不一致。

事件仅存：run/attempt 的内部 ID、任务键摘要、失败分类、受影响路径的规范化相对路径、证据哈希、脱敏原因摘要和创建时间。事件本身不等于“业务结论”。

### 5.2 学习规则

每个 `LearningRule` 都有以下状态：

```text
draft
  -> active_current_task
  -> trial
  -> stable
  -> suspended | retired
```

- `draft`：复盘信息不足或规则无法安全表达；仅保留审计，不能影响执行。
- `active_current_task`：来自当前 run 的纠错；下一次 attempt 必须加载。该规则只在该 run/合同哈希下有效。
- `trial`：当前任务修复通过后，向严格匹配的后续任务自动加载；规则只可增加检查/证据要求，不可自动改代码。
- `stable`：满足晋升条件后，默认应用于严格匹配的后续任务。
- `suspended`：出现误报、反例、范围冲突或证据失效；不再自动加载，等待重新验证。
- `retired`：规则已被替代、长期失效或被确认不正确；保留审计但绝不执行。

### 5.3 当前任务重试

`active_current_task` 规则在 `failed_verification`、`changes_requested` 或显式人工纠错后创建。下一次 attempt 的固定 Prompt 附带：

- 错误类别和脱敏摘要；
- 必须验证的回归断言；
- 规则适用的允许路径；
- 明确的禁止推断边界。

Worker 不能删除、修改或宣称绕过该规则。Harness 在验证结束后独立检查规则要求，未满足则保持 `failed_verification`，不得进入 Reviewer。

## 6. 根因分类

根因由固定枚举组合表示，而非由自由文本直接决定：

| 分类 | 含义 | 可自动形成的门禁 |
|---|---|---|
| `requirement_misread` | 需求/验收条件理解遗漏 | 验收条件覆盖清单、澄清阻断 |
| `call_chain_gap` | 调用方、共享 API、消费者或状态链遗漏 | 调用链/消费者扫描、编译矩阵 |
| `implementation_defect` | 分支、空值、顺序、异常或兼容实现缺陷 | 目标回归测试、diff 规则 |
| `verification_gap` | 已有验证未覆盖真实失败路径 | 新增确定性命令或运行时证据要求 |
| `review_gap` | Reviewer 上下文或检查项不足 | Reviewer 固定风险问题 |
| `environment_gap` | 依赖版本、配置、数据或发布版本不一致 | 环境前置检查和证据标签 |
| `delivery_gap` | 分支、commit、push、回读或云效字段不一致 | 交付计划回读与 parity 检查 |
| `business_ambiguous` | HIS 业务口径不唯一 | 停止并要求业务确认；禁止自动生成实现规则 |

高风险分类 `business_ambiguous`、医保/收费/结算/金额、远端写入和字段覆盖的学习规则只能增强阻断、检查或证据要求。它们不得自动选择业务语义、状态流转或远端写入内容。

## 7. 规则内容与匹配

规则使用版本化、结构化数据，不依赖模型自由文本：

```json
{
  "schema_version": "his-repair-learning-rule.v1",
  "rule_key": "sha256:<stable-key>",
  "state": "trial",
  "risk_class": "normal",
  "fingerprint": {
    "failure_kind": "call_chain_gap",
    "repository_kind": "gradle-java",
    "path_prefixes": ["mic-jj-guahao/"],
    "change_tags": ["shared_api", "rest_endpoint"]
  },
  "required_checks": [
    {"kind": "consumer_scan", "value": "shared_api_consumers"},
    {"kind": "verification", "value": "declared_compile_matrix"}
  ],
  "prohibitions": ["do_not_infer_business_contract"],
  "evidence_threshold": 3,
  "counterexample_threshold": 1
}
```

严格匹配需要同时满足风险级别、技术栈/仓库范围、路径范围和改动标签。没有足够信息时，规则不匹配；宁可少加载，不可错误扩展。

`required_checks` 只允许固定白名单，例如消费者扫描、路径覆盖、特定测试 ID、构建矩阵、diff 审核问题和交付回读。禁止把任意 shell、SQL、网络 URL 或模型指令保存为学习规则。

## 8. 自动晋升与降级

### 8.1 自动试运行

当前任务的修复 attempt 同时满足以下条件后，规则自动从 `active_current_task` 进入 `trial`：

1. 修复后确定性验证通过；
2. 独立 Reviewer 没有与该规则相关的阻断 finding；
3. 规则的证据/路径/合同哈希仍完整一致；
4. 没有敏感信息、业务口径猜测或外部写入授权被混入规则。

### 8.2 自动稳定

一个 `trial` 规则满足以下全部条件时自动进入 `stable`：

1. 至少 3 个不同 `task_key` 的独立任务命中；
2. 每次命中都有 Harness 验证和 Reviewer 通过的证据；
3. 至少一次命中来自与首个来源不同的 run/仓库工作区，避免同一重试重复计数；
4. 没有任何有效反例、误报或范围冲突；
5. 规则不是高风险业务语义规则。高风险规则长期保持 `trial`，仅作为增强门禁。

自动稳定仅改变 Harness 的本地规则状态；不会自动编辑共享技能文件、个人记忆、团队技能池或外部系统。

### 8.3 自动暂停、降级与退役

以下任一情况将规则立即 `suspended`：

- 规则导致无关路径被阻断或请求了合同外修改；
- 规则的预期与真实业务确认冲突；
- 相似任务验证失败或 Reviewer 证明规则不适用；
- 规则证据损坏、过期或无法绑定原始 run；
- 发现规则内容包含敏感信息或不安全命令形态。

`stable` 规则被暂停后，后续任务不再自动加载。只有新鲜、独立的更正证据才能产生新的 `draft`；旧规则不会被静默覆盖。连续过期或替代后标记 `retired`。

## 9. 与现有 Harness 组件的边界

### 9.1 Local Agent

`LocalAgentRunner` 在每次可重试失败后创建复盘事件，并在下一次 attempt 前调用规则解析器。`LocalAgentTask` 仍保持不变、不可变；学习规则是 run 绑定的附加验证上下文，不修改合同哈希。

`LocalAgentReviewer` 继续独立、只读。它接收已匹配规则的脱敏摘要和待验证检查项，但不能创建、晋升、暂停或删除规则。

`LocalAgentConfirmationService` 不消费学习状态。仅当原有 final diff、review seal 和人工确认均有效时才可本地 apply。

### 9.2 Delivery Closure

交付阶段只消费已经稳定或试运行的 `delivery_gap` 规则，以增加 pre-push 的精确 SHA、远端分支和云效读回检查。规则不能将 push、云效评论、状态更新或字段覆盖变成自动动作；现有不可变交付计划和逐阶段确认继续生效。

### 9.3 现有 Learning Candidate

现有 `manager_learning_candidates` 保持 Provider/Manager 失败候选的独立用途和人工 promotion 约束。新的修复复盘规则库不复用其“审核后写入知识库”的状态，避免把执行学习和知识发布混在一起。

它们可以共享脱敏、哈希、审计和敏感文本检测基础设施，但不能互相自动晋升。

## 10. 数据、审计与隐私

新增本地表仅存结构化、脱敏数据：

- `repair_retrospectives`：复盘事件、来源 run/attempt、原因类别、摘要 hash、证据 hash、状态；
- `repair_learning_rules`：规则 JSON、状态、风险级别、版本、命中/反例计数、时间戳；
- `repair_learning_observations`：规则在当前/相似任务中被加载、通过、失败、暂停的追加式观测。

规则与事件必须满足：

- 原始模型响应、完整日志、凭证、Cookie、Authorization、SQL 数据行和业务正文不写入上述表；
- 文本先经 `sensitive_text` 检查与规范化，再保存有限摘要；
- 所有可变状态迁移携带前一版本/当前版本，使用 compare-and-swap 防止并发重复晋升；
- 规则关键字段使用规范 JSON SHA-256；
- 只允许本地数据库与受控 artifact 文件，首版不写入云效、GitLab、Git 或外部知识库。

## 11. 产物

每个发生纠错的 run 生成：

- `repair_retrospective.json`：机器可读、脱敏的根因和证据摘要；
- `repair_retrospective.md`：人可读的“错误原因、修复方式、遗漏门禁、回归结果、残余边界”；
- `repair_learning_rule.json`：当前任务/试运行规则的规范化内容与哈希；
- `repair_learning_observation.json`：本次加载及验证结论。

final manifest 只引用这些文件的相对路径、SHA-256 和状态，不嵌入原始内容。

## 12. 验收门禁

实现完成需满足：

1. 人工纠错、验证失败、Reviewer 打回分别自动产生单一、幂等的复盘事件；
2. 当前任务下一次 attempt 必须实际加载对应规则，缺失或规则不通过时不能进入 Reviewer；
3. 不匹配任务不加载规则，防止跨仓库/跨业务误推广；
4. 3 个独立成功任务满足条件后自动进入 `stable`；同一 run 重试不能刷计数；
5. 任何反例立即暂停 `trial`/`stable` 规则，后续任务不加载；
6. 所有高风险业务/交付规则均不能自动变更业务语义、允许路径、外部写权限或云效字段；
7. 敏感文本、超长文本、不安全命令和不可验证证据全部 fail closed；
8. 规则/事件数据库和 artifacts 在临时 Harness DB 验证，正式数据库不迁移、不覆盖；
9. local-agent 既有测试、学习候选既有测试及新增专项测试通过；
10. 后续真实 fixture 与低风险真实任务验收时，分别保留“技术通过”“运行时确认”“业务验收”状态，不能互相替代。

## 13. 分期

### P0：离线可验证复盘层

实现结构化复盘事件、规则状态机、当前 run 注入、试运行/暂停/自动稳定、临时数据库测试与 artifacts。不开启真实模型、Git 提交或远端写入。

### P1：真实本地 Agent 验收联动

在 P0 全绿后，用真实 Codex 临时 Git fixture 验证一次“Reviewer 打回 -> 自动复盘 -> 同一 worktree 重试 -> 规则通过 -> Reviewer 批准 -> 人工本地 apply”。然后才选择低风险真实仓库任务。

### P2：受控交付联动

在本地真实闭环连续稳定后，将 `delivery_gap` 规则接入交付计划和远端读回检查；commit、push 和云效写入的现有独立授权不放宽。

## 14. 预期效果

Harness 会随着真实纠错变得更擅长避免已经发生过的工程错误：遗漏消费者、验证覆盖不足、调用链不完整、Reviewer 关注点缺失和交付回读遗漏等。

它不会因为“学习”而变成可以自行决定医保业务口径、自动反复提交或自动向远端写入的 Agent。正确性来自复盘、确定性门禁、独立审核、证据和可撤销规则的组合，而不是模型自我确信。
