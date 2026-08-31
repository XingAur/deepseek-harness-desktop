# Harness Five Gap Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Harness 当前五个缺口从“只读状态/合同可见”推进到“本地可维护、可测试、可审计、可逐步真实运行”的完整基础流程：真实模型 Worker 单节点 smoke、自动学习 candidate 闭环、真实业务验收证据、外部写动作受控执行、知识库/Obsidian 可问答，并让 Manager UI 从一开始承载这些能力。

**Architecture:** Harness 继续做编排、治理、审计和 UI；云效、Git/GitLab、数据库、知识库、模型 Worker 都作为独立 provider/capability 接入。所有高风险动作统一走 `dry-run -> review -> explicit confirmation -> execute -> audit`。Manager UI 不为每个能力重做页面，而是先建设通用的“连接配置、能力状态、执行计划、审核确认、证据归档、知识候选”框架，各 provider 只补自己的 schema 和适配器。

**Tech Stack:** Python 标准库、unittest、现有 Flask/HTML Manager、SQLite、本地文件知识库、Obsidian Markdown vault、现有 capability registry/runtime policy、后续可选 GitLab/Yunxiao/Database provider SDK 或 OpenAPI client。

## Global Constraints

- 默认本地化；不默认写云效、不 `git push`、不写 GitLab、不执行数据库变更、不部署。
- 真实模型 Worker 先只允许单节点 smoke；真实 Agent 团队/DAG 不在本计划前半段直接放开。
- 外部写入必须双开关：系统策略允许 + 用户对本次目标明确确认。
- 任何凭证只允许保存引用、别名或本地安全存储路径；不得进入 Git、日志、测试快照、知识库、Manager 明文页面。
- 业务验收必须和技术测试分离；技术测试通过不等于 HIS 页面、接口、数据库或生产验收通过。
- 自动学习只能生成 candidate/eval/rule draft/knowledge draft；正式 promote 必须人工审核。
- Manager UI 必须先做通用维护框架，避免后面每补一个能力就重新推一次 UI。
- 所有任务完成前必须有测试、diff 自审和状态页证据；不能用“代码已改”冒充“真实运行已通过”。

---

## Current Baseline

- [x] 五缺口 readiness 已经能在后端/Manager 基础卡片中表达。
- [x] 知识库本地 home 已落到 `/Users/lym/WorkCode/ai/his-knowledge`，可和 Obsidian vault 关联。
- [x] 学习闭环已有 candidate/review/promote 的 capability 边界，且自动 promote 禁用。
- [x] 外部写动作当前保持 disabled，并已有 dry-run 事务计划合同。
- [ ] 真实模型 Worker 仍冻结，缺真实 provider smoke。
- [ ] 业务验收仍未打通 HIS 测试环境、账号、测试数据和运行证据。
- [ ] 云效/GitLab/数据库连接维护与测试连接未做成 Manager 可维护能力。
- [ ] 外部写动作只能表达“禁用/计划”，还没有 sandbox/test-object 写入验收。
- [ ] 知识库还缺索引同步、检索问答、candidate 审核 UI 和引用式回答。

---

## Phase 0: Manager UI Foundation First

**Purpose:** 先把 Manager 做成稳定承载层，后续每完成一个 provider 只接 schema，不重做 UI。

**Files:**
- Modify: `app/server.py`
- Modify: `app/task_manager.py` or existing Manager rendering helpers
- Add/Modify tests: `tests/test_manager_readiness_card.py`, focused server/API tests

**Deliverables:**
- [ ] 新增通用页面区块：连接维护、能力状态、执行计划、审核确认、业务证据、知识候选。
- [ ] 每个区块先消费统一 JSON schema，不绑定某一个 provider 的特殊页面。
- [ ] 详情区支持 expandable JSON、状态 pill、阻塞原因、下一步动作。
- [ ] 增加“不允许真实写入”的明显状态提示和确认入口占位。

**Acceptance:**
- [ ] Manager 首页能看到五缺口总览和每项详情。
- [ ] 后续新增 Yunxiao/GitLab/DB/Knowledge provider 时，只需要新增后端 schema + 一小段渲染映射。
- [ ] 测试覆盖 HTML 中的关键状态、禁用提示、详情区 id。

**Commit:**
```bash
git add app/server.py app/task_manager.py tests/test_manager_readiness_card.py
git commit -m "feat(manager): add five-gap operating console"
```

---

## Phase 1: Real Model Worker Smoke

**Purpose:** 不是直接打开真实 Agent 团队，而是先证明“真实模型 provider 可以被安全调用一次”。

**Files:**
- Modify/Add: `app/model_worker_smoke.py`
- Modify: `app/runtime_policy.py`
- Modify: `app/core_status.py`
- Add tests: `tests/test_model_worker_smoke.py`, `tests/test_core_status.py`

**Implementation:**
- [ ] 增加 model provider profile：provider 名称、model 名称、credential reference、timeout、budget cap、network allowlist。
- [ ] 增加 smoke runner：固定最小 prompt、单次调用、超时、可取消、结构化结果。
- [ ] 增加双开关：
  - `REAL_MODEL_RUNTIME_FROZEN` 控制真实 DAG。
  - `REAL_MODEL_SMOKE_ALLOWED` 只控制单节点 smoke。
- [ ] 审计日志只记录 provider、model、耗时、token 估算、结果摘要、错误类型；不记录 secret。
- [ ] Manager UI 显示：未配置、待授权、可 smoke、smoke 通过、失败原因。

**Acceptance:**
- [ ] 默认状态不读取凭证、不联网。
- [ ] 未授权时 smoke 必须 blocked。
- [ ] 授权后只运行单节点 smoke，不启动真实 Agent 团队。
- [ ] smoke 成功后 readiness 从 `frozen/not_run` 变为 `smoke_passed`，但真实 DAG 仍不自动打开。

**Verification:**
```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_model_worker_smoke tests.test_core_status -v
```

**Commit:**
```bash
git add app/model_worker_smoke.py app/runtime_policy.py app/core_status.py tests/test_model_worker_smoke.py tests/test_core_status.py
git commit -m "feat(model): enable guarded real worker smoke"
```

---

## Phase 2: Learning Loop Candidate Pipeline

**Purpose:** 把失败样本自动沉淀下来，但不自动变成正式规则或正式知识。

**Files:**
- Modify/Add: `app/learning_loop.py`
- Modify/Add: `app/eval_candidates.py`
- Modify: `app/core_status.py`
- Modify: Manager UI candidate section
- Tests: `tests/test_learning_loop.py`, `tests/test_eval_candidates.py`

**Implementation:**
- [ ] 定义 candidate 类型：`eval_sample`、`contract_plugin_draft`、`rule_pack_draft`、`knowledge_candidate`。
- [ ] 失败运行后自动生成 candidate 文件/SQLite 记录：来源、失败命令、输入摘要、期望、实际、脱敏证据、建议归属。
- [ ] 增加 review 状态：`new -> reviewing -> accepted/rejected -> promoted`。
- [ ] 增加 replay/eval 验证入口：candidate 被接受前必须可重放或可解释。
- [ ] Manager UI 支持查看 candidate、证据、diff、审核动作。

**Acceptance:**
- [ ] 失败样本能自动沉淀为 candidate。
- [ ] candidate 默认不会影响正式规则、正式知识、正式 eval。
- [ ] promote 必须人工审核，并记录审核人/时间/原因。
- [ ] 含 secret 的 candidate 被拒绝或脱敏。

**Verification:**
```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_learning_loop tests.test_eval_candidates -v
```

**Commit:**
```bash
git add app/learning_loop.py app/eval_candidates.py app/core_status.py tests/test_learning_loop.py tests/test_eval_candidates.py
git commit -m "feat(loop): persist failed samples as reviewable candidates"
```

---

## Phase 3: Business Acceptance Evidence

**Purpose:** 让 Harness 能记录真实 HIS 验收证据，但不把离线测试通过冒充业务验收通过。

**Files:**
- Modify: `app/business_acceptance.py`
- Modify: `app/core_status.py`
- Modify: Manager UI business evidence section
- Tests: `tests/test_business_acceptance.py`, enterprise gate related tests

**Implementation:**
- [ ] 定义业务验收证据 schema：环境、账号别名、测试数据别名、操作步骤、预期结果、实际结果、截图/日志哈希、验收结论、验收人。
- [ ] Manager UI 增加手工录入/导入证据入口。
- [ ] readiness 明确区分：
  - `technical_valid`
  - `runtime_verified`
  - `business_valid`
  - `production_verified`
- [ ] enterprise gate 只有在证据完整且结论通过时，才允许 `business_valid=true`。

**Acceptance:**
- [ ] 只有本地测试通过时，业务验收仍显示 `not_verified`。
- [ ] 缺 HIS 测试环境/账号/数据/证据时，显示明确 missing reason。
- [ ] 录入完整测试证据后，Manager 能展示“业务验收通过/未通过/缺证据”。
- [ ] 不支持自动判定的场景，必须明确提示“需要人工或运行时确认”。

**Verification:**
```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_business_acceptance tests.test_core_status -v
```

**Commit:**
```bash
git add app/business_acceptance.py app/core_status.py tests/test_business_acceptance.py tests/test_core_status.py
git commit -m "feat(acceptance): track runtime business evidence"
```

---

## Phase 4: External Provider Maintenance and Controlled Writes

**Purpose:** 支持云效评论/负责人修改、Git/GitLab 提交推送、数据库查询/视图/变更，但必须先完成连接维护、测试连接、dry-run、确认、审计。

**Files:**
- Add/Modify: `app/provider_profiles.py`
- Add/Modify: `app/external_transactions.py`
- Add/Modify: provider adapters under existing capability/provider structure
- Modify: Manager UI connection and transaction sections
- Tests: `tests/test_provider_profiles.py`, `tests/test_external_transactions.py`, related capability permission tests

**Provider Scope:**
- [ ] Yunxiao provider:
  - token reference maintenance
  - organization/project/workitem config
  - read test
  - comment dry-run
  - assignee/status change dry-run
  - sandbox/test-object write acceptance
- [ ] Git/GitLab provider:
  - repo/profile maintenance
  - current branch/status read
  - commit plan
  - push dry-run
  - protected branch block
  - optional test remote write acceptance
- [ ] Database provider:
  - connection profile maintenance
  - read-only test connection
  - normal DB connection profile and test DB profile必须分开
  - query/view dry-run
  - write/change plan
  - production write block unless separately confirmed

**Execution Contract:**
- [ ] 每个写动作必须生成 transaction plan：目标、动作、payload、diff/SQL/commit 信息、风险、回滚方式、幂等 key。
- [ ] 执行前必须校验：
  - provider connected
  - capability allowed
  - target is test/sandbox or user explicitly confirmed real target
  - dry-run result exists and未过期
  - secrets redacted
- [ ] 执行后写 audit：谁确认、确认文本、执行结果、外部对象 id、失败原因。

**Acceptance:**
- [ ] 默认仍不能写云效、不能 push、不能改库。
- [ ] Manager 能维护 token/profile，但不显示 secret 原文。
- [ ] 测试连接结果和正常连接配置一致：同一 profile 同一 driver/host/database/schema，只是权限和环境标记明确。
- [ ] sandbox/test-object 写入能通过受控链路完成。
- [ ] 真实目标写入必须停下等用户确认，不能无人值守自动执行。

**Verification:**
```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_provider_profiles tests.test_external_transactions tests.test_capability_permissions -v
```

**Commit:**
```bash
git add app/provider_profiles.py app/external_transactions.py tests/test_provider_profiles.py tests/test_external_transactions.py tests/test_capability_permissions.py
git commit -m "feat(providers): add managed external transaction profiles"
```

---

## Phase 5: Knowledge Base and Obsidian Integration

**Purpose:** 做成本地强知识库：可沉淀、可检索、可引用回答、可人工审核；像客服一样答问题，但必须基于已有知识和来源。

**Files:**
- Modify/Add: knowledge provider modules
- Modify/Add: `app/knowledge_index.py`
- Modify: Manager UI knowledge search/candidate section
- Tests: `tests/test_knowledge_capabilities.py`, `tests/test_knowledge_index.py`

**Implementation:**
- [ ] 使用 `/Users/lym/WorkCode/ai/his-knowledge` 作为 knowledge home。
- [ ] 规划 Obsidian vault 目录：
  - `inbox/` 临时输入
  - `sources/` 原始来源摘要
  - `candidates/` 待审核知识
  - `accepted/` 正式知识
  - `rules/` 规则包
  - `evals/` 验证样本
  - `audit/` 审核记录
- [ ] 增加 Markdown/SQLite 索引同步。
- [ ] 增加检索问答接口：必须返回引用来源、置信度、缺口说明。
- [ ] 和 learning loop 打通：失败样本可以生成 knowledge candidate，但不能自动进入 `accepted/`。
- [ ] Manager UI 支持搜索、查看来源、candidate 审核、promote/reject。

**Acceptance:**
- [ ] Obsidian 中新增/修改 Markdown 后可被索引。
- [ ] 用户问 HIS/Harness 问题时，回答能附来源引用。
- [ ] 知识库没有内容时，系统明确说“不知道/缺资料”，并可生成 candidate。
- [ ] secret、token、生产敏感数据不会进入正式知识。

**Verification:**
```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_knowledge_capabilities tests.test_knowledge_index -v
```

**Commit:**
```bash
git add app/knowledge_index.py tests/test_knowledge_index.py
git commit -m "feat(knowledge): index obsidian-backed harness knowledge"
```

---

## Phase 6: End-to-End Real Task Drill

**Purpose:** 用一次真实任务验证完整流程，但默认不做真实外部写入。

**Scenario:**
1. 从云效读取一个需求/BUG。
2. Harness 检查云效内容是否完整、合理、合规。
3. 判断自己能不能改；不能改则输出原因和缺口。
4. 能改则生成本地执行计划。
5. 本地代码调整。
6. 本地测试和 diff 自审。
7. 生成云效评论草稿、Git 提交计划、数据库查询/视图计划。
8. 录入或等待 HIS 业务验收证据。
9. 失败样本沉淀为 candidate。
10. Manager UI 展示全链路状态。

**Acceptance:**
- [ ] 没有授权时，只生成云效评论草稿，不真实评论。
- [ ] 没有授权时，只生成 Git 提交/push 计划，不真实 push。
- [ ] 没有授权时，只生成数据库 SQL/视图 dry-run，不真实改库。
- [ ] 业务验收缺证据时，最终状态不能显示 complete。
- [ ] Manager UI 能看到每一步证据、阻塞项、下一步动作。

**Verification:**
```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover tests -v
```

**Commit:**
```bash
git add .
git commit -m "feat(harness): complete governed real-task workflow"
```

---

## Required Prerequisites Before Real Execution

- [ ] 模型 provider 凭证和预算上限。
- [ ] 云效 token reference、organization id、测试 workitem 或明确允许的真实 workitem。
- [ ] GitLab token/reference、测试 repo 或明确允许的真实 repo。
- [ ] 数据库测试连接信息、只读账号、必要时单独的写入测试库。
- [ ] HIS 测试环境地址、测试账号、测试数据、验收步骤。
- [ ] 明确的外部写入确认规则，例如“只允许对 DFHIS-xxxxx 评论一次”或“只允许推送当前 feature 分支”。

---

## Definition of Done

- [ ] 五缺口 readiness 中每一项都有 `state`、`prerequisites`、`capabilities`、`verification`、`next_actions`、`manager_ui`。
- [ ] Manager UI 能维护连接、查看状态、审核计划、录入证据、检索知识、处理 candidate。
- [ ] 真实模型只完成单节点 smoke；真实 Agent 团队需要后续单独 gate。
- [ ] 外部写入默认关闭；测试对象写入通过后，真实写入仍需逐次确认。
- [ ] 业务验收必须有 HIS 运行证据，不能由本地测试替代。
- [ ] 知识库能从 Obsidian/本地 Markdown 建索引，并用引用回答问题。
- [ ] 全量或覆盖性测试通过，且 diff 自审无 secret、无无关格式化、无隐式外部写入。
