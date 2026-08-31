# HIS Harness 基础需求核心闭环设计

## 状态

- 日期：2026-07-11
- 阶段：v0.35 Core Closure
- 状态：用户已确认，待实施
- 优先级：高于 Web UI、配置中心真实写入、团队分发和部署

## 1. 结论与目标

Harness 当前的 worktree、路径白名单和验证执行器可以安全地承载改码，但固定九步专家报告不能证明需求理解、工程定位和代码修改之间存在可靠交接。本阶段的目标是让一个**低风险、单仓库、范围明确的基础需求**形成可复验闭环：

```text
需求证据 -> 需求契约 -> 工程定位交接 -> 受控改码 -> 独立 diff 审查 -> 专项验证 -> 交付结论
```

每一阶段都必须产出机器可读取的结构化产物；任何关键产物缺失、冲突、越界或验证失败都必须阻断后续阶段。通过静态检查不等于业务验收；最终交付结论必须分别说明自动验证和人工业务验收状态。

## 2. 范围与非目标

### 本阶段范围

1. 新增显式、默认关闭的 `core-closure-trial` 执行模式，不改变 legacy 固定工作流和既有 CLI 默认行为。
2. 建立 `RequirementContract`、`EngineeringHandoff`、`DiffReview`、`CoreClosureResult` 四类版本化结构化产物。
3. 将需求校准卡、工程证据、技术决策和验收矩阵收敛成硬闸门，而不是只作为提示词附件。
4. 使用独立审查阶段检查 final diff 是否满足契约、是否越过允许路径、是否保持默认行为和是否缺失验证证据。
5. 建立 DFHIS-31465 `paiBanMs` 回放基线，用已确认的参数语义验证需求理解、阻断规则和审查规则。
6. 对实现逻辑和回放基线补充标准库 `unittest`；保留 `tools/self_check.py` 作为补充集成检查。

### 明确不做

- 不实现 Web UI、配置目录选择、个人密钥存储、团队模板分发或部署。
- 不修改云效、TAPD、Git 远端、发布或业务项目代码。
- 不自动提交、推送、发布或执行回滚。
- 不将 mock 回放描述为真实业务项目的运行时验收。
- 不在本阶段实现大需求 DAG、并行多智能体或按模型/预算隔离的动态团队；这些能力必须建立在本闭环成功后。

## 3. 运行边界

`core-closure-trial` 只允许以下前提同时成立时进入 worktree：

1. 需求校准结果为 `ready_for_development`，且不存在未解决的来源冲突。
2. 技术决策能确定一个存在的主项目、至少一条允许修改路径和至少一条稳定验证命令。
3. 需求不命中医保、收费、退费、结算、对账、金额、政策校验、数据库迁移、外部真实写入等高风险规则。
4. 工程证据能支持目标页面或模块，不允许仅凭模型猜测文件位置。
5. 每条需求验收项都能归为自动验证项或明确标记为人工业务验收项。

不满足时输出 `blocked` 结果和可操作原因，不调用 LLM 生成 patch，不创建 worktree，不修改业务仓库。

## 4. 结构化契约

### 4.1 RequirementContract

`RequirementContract` 是改码前唯一有效的需求定义，字段包括：

- `schema_version`：`1.0-requirement-contract`
- `status`：`ready` 或 `blocked`
- `title`、`demand_digest`、`source_priority`
- `rules`：可验证业务规则，含 `id`、`statement`、`source`、`evidence_refs`
- `default_behavior`：未传参数、空值、非法值等兼容行为
- `allowed_paths`、`verify_commands`
- `automatic_acceptance`、`manual_acceptance`
- `blockers`、`warnings`、`evidence_refs`

DFHIS-31465 的基线规则必须精确表示：`paiBanMs=1` 只保留医生为空的排班；`paiBanMs=2` 只保留有医生的排班；空、未传和其他值保持当前默认模式。

### 4.2 EngineeringHandoff

工程定位阶段只交付已证明的工程事实：主项目、允许路径、证据 ID、命中的代码片段摘要、验证命令和不可修改边界。它不允许用“建议可能在某文件”替代事实。

### 4.3 DiffReview

独立审查不复用开发模型的自然语言结论。审查器根据契约和 `final.diff` 给出：

- 是否只有白名单路径被修改。
- 每条自动验收规则是否在 diff 中具有可解释的实现证据。
- 默认行为是否存在兼容保护证据。
- 是否存在缺失的验证命令、空 diff、删除文件、禁止路径或未覆盖规则。
- `pass`、`blocked` 或 `failed` 以及逐条发现。

审查器的 `pass` 只是“可以进入人工代码审查”，不是“业务已验收”。

### 4.4 CoreClosureResult

最终结果汇总各阶段状态、所有产物、worktree 结果、审查结果、验证矩阵和交付状态。只有下面条件同时成立时才可为 `ready_for_manual_review`：

- 契约和工程交接均为 `ready`。
- worktree 改码、`git apply --check`、`git diff --check` 和所有专项命令通过。
- 独立 diff 审查通过。
- 未出现白名单外改动、验证副作用或高风险升级。

`manual_business_acceptance_required` 始终保留，除非后续由用户明确提供运行时验收记录。

## 5. 执行编排

新模式不调用 legacy 九步专家串行流程。它按以下固定、可审计的最小链执行：

1. **Contract Gate**：从校准卡、技术决策和验收矩阵建立需求契约。
2. **Engineering Gate**：验证项目、路径、工程证据和验证命令，生成工程交接。
3. **Developer**：仅将契约、工程交接和必要证据传给现有 worktree 执行器。
4. **Independent Review**：只读取 final diff、契约和验证结果，拒绝自身实现的推断。
5. **Closure Gate**：综合所有阻断项，生成交付包。

现有 `single-demand-trial` 保持不变；`core-closure-trial` 是新的 opt-in 路径。初版仍使用现有 `WorktreeCodeExecutor`，但以 `apply_to_project=False` 为默认值，先只产生并验证 final diff。只有用户显式传入 `--apply-approved-diff` 才可沿用既有的本地合入能力，且不允许用于高风险需求。

## 6. 回放基线与验收

DFHIS-31465 是语义回放基线，不是虚构需求，也不是对业务仓库运行时的替代：

- 需求：菜单/路由参数 `paiBanMs` 控制医生为空或有医生排班过滤。
- 已知优先级：用户明确规则高于需求图中“科室过滤条件”描述。
- 已知默认行为：空、不传、非法值保持当前模式。
- 预期工程范围：`df-web-guahaosf` 的排班页面与过滤辅助模块；运行时实际路径仍必须由当轮工程证据确认。

自动化回放至少覆盖：用户规则覆盖来源冲突、参数三种分支、缺少验证命令阻断、高风险关键词阻断、越界 diff 阻断、默认行为保护、合法 diff 通过和 legacy 模式不变。真实业务仓库回放只在用户明确提供目标仓库与验证命令后执行，且输出与 fixture 回放分开标注。

## 7. 后续顺序

1. v0.35 Core Closure：本设计。
2. v0.36：在 v0.35 的结构化契约基础上实现简单/中等/高风险的动态团队组建。
3. v0.37：子任务 DAG、依赖、隔离上下文和独立验证。
4. v0.38：将经过验证的核心产物投射到配置中心和 Web UI。

只有 v0.35 回放和真实基础需求试跑均有明确证据后，才恢复 UI、团队分发和部署相关实施。
