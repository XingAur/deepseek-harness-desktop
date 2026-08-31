# Enterprise Harness Phase 1C Provider Authority Implementation Plan

> **Execution contract:** implement task by task with `executing-plans`, use test-first changes, and run `verification-before-completion` before reporting completion.

**Goal:** 将云效、GitLab、GitHub、数据库和本地 Provider 的“技术访问权限”与 Harness 的“行为治理”明确解耦：只读动作不再要求额外人工确认令牌，但仍绑定 Profile、目标、调用参数、请求人和一次性审计计划；数据库修改或删除继续默认不可执行，未来只有用户对精确变更范围作出明确授权后才允许进入实现。

**Architecture:** 由独立的 authority policy 判定技术权限来源与是否需要 Harness 明确授权。Provider token、数据库只读地址/账号和本地文件权限决定技术调用能否成功；Harness 负责计划绑定、范围检查、一次性消费、凭证短期注入、结果审计和写操作授权。只读计划从 `planned` 原子转为 `consumed`，不生成确认 token；非只读动作继续走现有 `confirm -> consume`。数据库写动作不加入 `ACTION_DESCRIPTORS`，只增加不可回退的策略与验收断言。

**Tech Stack:** Python 标准库、SQLite 事务、现有 Provider adapters、`unittest`、Harness architecture gate。

## Boundaries

- 不访问真实云效、GitLab、GitHub 或数据库。
- 不读取、修改、复制或展示真实 PAT、密码、连接串和 endpoint。
- 不增加数据库 DML/DDL/delete/truncate/drop action、adapter 或 capability route。
- 不迁移、不删除、不重建 `harness.sqlite`、`mcp.sqlite` 或任何业务数据库。
- 远程写和本地变更继续要求现有一次性明确授权；本阶段不放宽外部写入规则。
- 现有只读 SQL 语法策略、PostgreSQL session read-only、statement timeout 和结果上限全部保留。
- 本仓库不是 Git 仓库；修改前使用哈希一致的本地备份，交付使用逐文件差异和测试证据。

## File Map

- Create: `app/provider_authority_policy.py` — 技术权限来源、Harness 授权要求和数据库精确范围策略。
- Modify: `app/manager_provider_repository.py` — 原子消费无人工确认的只读计划并写安全审计。
- Modify: `app/provider_action_authorization.py` — 仅允许已注册 `risk=read` 动作走免确认消费。
- Modify: `app/provider_execution.py` — 按 authority policy 选择只读执行 grant 或现有人工授权。
- Modify: `app/provider_connection_tests.py`, `app/provider_profiles.py`, `app/provider_readonly_smoke.py`, `app/server.py` — Manager 只读连接/smoke 计划不再声明需要人工确认，显式执行仍交给 ProviderExecutionService；当前 UI 的数据库边界改为默认禁止且仅接受未来精确范围授权。
- Create: `tests/test_provider_authority_policy.py` — 策略合同和数据库写删边界。
- Modify: `tests/test_provider_action_authorization.py` — 只读计划一次性、actor、参数和审计约束。
- Modify: `tests/test_provider_execution.py` — 免确认只读与非只读 fail-closed 执行顺序。
- Modify: `tests/test_database_readonly_provider.py` — 数据库只读 endpoint/凭证是技术权限来源，危险 SQL 继续前置阻断。
- Modify: `tests/test_provider_connection_tests.py`, `tests/test_provider_profiles.py`, `tests/test_provider_readonly_smoke.py`, `tests/test_server_core_status_api.py`, `tests/test_manager_readiness_card.py`, `tests/test_manager_provider_repository.py`, `tests/test_complete_manager_flow.py` — 冻结 Manager 计划、API、UI 与文档的免确认只读和数据库精确授权语义。
- Create: `tests/test_provider_authority_acceptance.py` — 云效/GitLab/数据库跨 Provider 验收及数据库无写 action。
- Modify: `scripts/verify.sh`, `tests/test_verify_entrypoint.py`, `config/external_io_boundaries.v1.json` — 将 Phase 1C 纳入 architecture gate，并只更新已审核 verify 入口的稳定源指纹。
- Modify: `README.md`, `CHANGELOG.md`, `docs/manager-runbook.md`, `docs/superpowers/specs/2026-08-09-manager-provider-configuration-design.md` — 写明当前能力、技术权限来源、默认禁止数据库变更及未开放边界。

## Task 1: Freeze the Authority Policy

- [x] 先创建 `tests/test_provider_authority_policy.py`，断言：
  - 所有 `risk=read` 动作的 Harness 人工授权要求为 false；
  - 云效/GitLab/GitHub 只读技术权限来源是 personal token；
  - 数据库只读技术权限来源是 readonly endpoint/credential；
  - 数据库非只读要求用户明确授权和精确 scope；delete/truncate/drop 额外要求明确 destructive scope；
  - 非数据库写动作继续要求明确授权。
- [x] 运行测试并确认因模块不存在而 RED。
- [x] 最小实现 `provider_authority_policy(...)`，拒绝未知 provider/risk/action，不引用 Provider adapter，避免循环依赖。

## Task 2: Add Approval-Free but Governed Read Consumption

- [x] 先在 `tests/test_provider_action_authorization.py` 增加失败测试：
  - planned read 不经 `confirm` 可消费；
  - actor 必须等于 `requested_by`；
  - 参数哈希必须完全一致；
  - 成功后 state 为 `consumed`，再次消费失败；
  - audit 的 authorization hash 为空，reason 为 `credential_or_endpoint_authority`；
  - 非 read 调用该入口 fail closed。
- [x] 运行专项测试并确认 RED。
- [x] 在 Repository 增加单事务 `consume_read_action_plan`：重新验证 Profile 和数据库 canonical target，在适用状态下原子消费并追加审计。
- [x] 在 Authorizer 增加 `consume_read`：从固定 `ACTION_DESCRIPTORS` 验证 action/provider/risk 后调用 Repository；不接受模型生成的 approval/token。
- [x] 保留原 `confirm`/`consume` API，确保所有非只读动作和历史显式确认路径兼容。

## Task 3: Route Execution by Authority Without Weakening Credentials

- [x] 先在 `tests/test_provider_execution.py` 增加失败测试：
  - read + `authorization=None` 消费只读 grant 后才解析凭证和调用 adapter；
  - 缺失凭证或 token 权限不足仍由 resolver/adapter 返回失败，Harness 不伪造成功；
  - remote write + `authorization=None` 在 credential/adapter 前阻断；
  - read actor/parameter/target 不匹配仍在 credential/adapter 前阻断。
- [x] 运行专项测试并确认 RED。
- [x] 在 `ProviderExecutionService.execute` 中仅当 policy 声明不需要 Harness 授权且调用者未提供 authorization 时调用 `consume_read`；其他路径保持现有 `consume`。
- [x] 继续只在计划消费后创建临时 execution grant 和 credential resolver；finally 必须撤销 grant。

## Task 4: Prove the Database Boundary and Cross-Provider Semantics

- [x] 先创建 `tests/test_provider_authority_acceptance.py` 和数据库专项失败测试，覆盖云效、GitLab、数据库三类只读动作免确认执行。
- [x] 断言技术权限失败来自 credential/endpoint/adapter，且数据库危险 SQL 在 credential/driver 前拒绝。
- [x] 断言 `ACTION_DESCRIPTORS` 中不存在数据库修改、删除、DDL/DML action，`database.change` capability 仍不可外部执行。
- [x] 不增加任何真实网络 fixture；全部使用临时 SQLite 管理库和 fake adapters。

## Task 5: Gate, Document and Review

- [x] 将 policy、authority、acceptance 测试加入 `scripts/verify.sh architecture`，并由 `test_verify_entrypoint.py` 冻结。
- [x] README 明确区分：技术权限、Harness 治理、数据库修改授权、当前未开放能力。
- [x] CHANGELOG 记录 Phase 1C 增量，不宣称真实外部系统或业务数据库验收。
- [x] 运行 focused tests、完整 architecture gate、只读语法编译和相关 legacy Provider tests。
- [x] 对备份与当前文件做逐文件 diff，检查无凭证、无 endpoint、无数据库写 action、无非预期文件。
- [x] 将决策、验证结果和残余边界写入 HarnessHistory；因目标非 Git，结构化 patch/review 归档按真实限制记录为 blocked；未创建 branch、commit、push、MR 或外部评论。

## Acceptance Criteria

1. 注册的只读 Provider 动作在不提供 Harness 人工 authorization 时可以进入执行，但必须有一次性计划、精确 actor/参数/目标绑定和审计。
2. PAT、数据库 readonly endpoint/credential 或本地权限不足时，执行失败且不会被 Harness 绕过。
3. 所有非只读动作在缺少现有显式 authorization 时仍在解析凭证和调用 adapter 前阻断。
4. 数据库写、删除、DDL/DML 没有可执行 action/adapter；策略明确要求精确用户授权，删除要求明确 destructive scope。
5. 原数据库只读 SQL、session read-only、超时和结果边界测试继续通过。
6. architecture gate 通过，文档不把离线 fake adapter 验收写成真实云效、GitLab 或数据库验收。
