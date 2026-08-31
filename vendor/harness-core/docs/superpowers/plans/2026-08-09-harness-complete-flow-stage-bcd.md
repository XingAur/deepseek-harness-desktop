# Harness 完整流程（阶段 B–D）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` to implement each numbered task in an isolated task/review pair. Do not use the formal Harness directory as an implementation workspace.

**Goal:** 在隔离源码工作树中完成 Harness 的完整本地优先流程：Manager 配置、受控 Provider 执行、真实模型单节点、学习候选审核、知识库优先回答、HIS 业务验收证据与统一 Manager UI。完成后再迁移到正式 Harness 做分层验收；不把本地测试误报为外部系统或 HIS 业务验收。

**Architecture:** 阶段 A 已提供 Manager 数据库、类型化 Provider Profile、AES-GCM 密文凭证、只读知识咨询和本地审计基础。阶段 B 在其上增加“一次性授权计划 + 审计”的唯一执行入口，并把云效、Git、GitLab、数据库只读操作和模型 smoke 收敛到 Provider Adapter。阶段 C 把失败运行持久化为可审核 candidate，记录可追溯的业务验收证据，并让知识库先检索再决定是否调用模型。阶段 D 只做正式目录的可逆迁移与分层实连验证；实际外部写入始终单次确认、审计并回读验证。

**Tech Stack:** Python 3、SQLite WAL（本地）、标准库 HTTP/subprocess、现有 `cryptography` AES-GCM、`unittest`、现有 `ThreadingHTTPServer` Manager。

## Completion Definition

- 代码完成：以下所有 B/C 任务的功能、单元测试、HTTP 合同测试、模拟 Provider 集成测试及独立审查全部通过。
- 部署完成：正式 Harness 迁移在明确文件清单、备份和回退方法确认后执行；当前正式目录不会被源码工作树覆盖。
- 运行验收完成：需要真实的测试 Profile、可用账号/网络和测试数据。云效/GitLab 写入、Git 推送/合并请求仅在用户逐次确认后执行；数据库没有写入验收，因为 Harness 永久不提供数据库写能力。
- HIS 业务验收完成：只有具备测试环境、场景、证据、实际操作者和明确结论时，`business_valid` 才能为 `true`。代码完成、Provider 连通和 smoke 成功均不能替代它。

## Global Constraints

- 只改隔离源码工作树；不改 `/Users/lym/WorkCode/ai/Harness`，不提交、不 push、不创建 PR、不写云效、不改 GitLab、不执行数据库 DDL/DML。
- 管理员凭证仅存 Manager 数据库密文；AES 主密钥仅由部署环境提供。页面、API、错误、审计和测试输出不得暴露明文、掩码尾号、完整连接串、认证头或私钥。
- 所有真实 Provider 调用必须经单次、短期、绑定参数哈希的授权令牌；令牌不可复用、不可由模型生成、不可在日志或 UI 回显。未授权时 fail closed 并留下脱敏审计。
- 数据库 Provider 永久只读：只允许单条 `SELECT` / `EXPLAIN SELECT` / `WITH … SELECT`，只允许只读账号，连接、语句、行数、时长和结果大小均限额。不得新增任何 DDL/DML、事务提交、存储过程、执行草案或绕过入口。
- 模型不能创建授权、改变 Provider 配置、执行外部写入、删除任何文件/记录或晋升知识。真实模型运行只开放固定单节点 smoke；正常 Agent 团队/DAG 继续冻结，直到独立的运行时验收完成。
- 知识库先检索已批准且未过期的证据；缺失时记录脱敏咨询与 candidate 建议，不能自动晋升或暗中调用模型。

## Task 1: 统一执行授权、审计和持久化合同（B1） — Complete

**Files:**
- Modify: `app/database.py`
- Modify: `app/manager_provider_repository.py`
- Create: `app/provider_action_authorization.py`
- Create: `tests/test_provider_action_authorization.py`
- Modify: `tests/test_database_governance.py`
- Modify: `tests/test_manager_provider_repository.py`

**Interfaces:**
- Produces `ProviderActionPlan`, `ProviderActionAuthorization`, `ProviderActionDecision` and `ProviderActionAuthorizer`.
- Adds Manager tables `manager_provider_action_plans`, `manager_learning_candidates`, `manager_business_acceptance_evidence`; all action audit rows retain only target aliases, parameter hash, authorization hash and redacted safe result summary.

1. Write RED tests covering: a plan binds `scope/provider/profile/action/target/parameter_hash`; execute before confirm fails; confirm creates one-use authorization with expiry; changed parameters, wrong actor, reuse or expiry fail; secret-shaped public input cannot create an audit record.
2. Version the schema without removing existing tables/migrations. Add immutable plan/audit timestamps, a state check (`planned`, `confirmed`, `consumed`, `expired`, `rejected`) and indexed expiry/profile lookups. Preserve Manager database backup/migration protection.
3. Implement canonical JSON hashing and strict redaction in `provider_action_authorization.py`. The authorizer must be injected with a clock for deterministic expiry tests and must never accept an authorization ID supplied by a model/provider response.
4. Extend `ManagerProviderRepository` with create/get/confirm/consume action-plan methods and transactionally record every attempted execution. `consume` must atomically mark the authorization consumed before calling an adapter, preventing retry races.
5. Run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_database_governance tests.test_manager_provider_repository tests.test_provider_action_authorization` and `git diff --check`.

## Task 2: Provider Adapter boundary and connection health (B2) — Complete

**Files:**
- Create: `app/provider_execution.py`
- Modify: `app/provider_connection_tests.py`
- Modify: `app/provider_readonly_smoke.py`
- Modify: `app/provider_capability_status.py`
- Create: `tests/test_provider_execution.py`
- Modify: `tests/test_provider_connection_tests.py`
- Modify: `tests/test_provider_readonly_smoke.py`

**Interfaces:**
- Produces `ProviderAdapter` protocol and `ProviderExecutionService.execute(authorization, request)`.
- Consumes decrypted credentials only inside an adapter after the authorizer has consumed a valid authorization.

1. Write RED tests using fake adapters: no adapter receives credentials or network permission until a matching authorization is consumed; blocked, succeeded and failed attempts produce the same redacted audit shape; adapter output over the configured size is rejected.
2. Define provider action descriptors with exact risk (`read`, `local_mutation`, `remote_write`, `model_smoke`), maximum timeout/result bytes, required credential fields and read-back verifier. Keep capability status descriptive rather than claiming an action is available merely because a Profile exists.
3. Refactor the current connection-test and readonly-smoke modules to create plans and show `awaiting_confirmation` / `blocked` instead of pretending to test a connection. They must delegate actual execution only to `ProviderExecutionService`; remove any separate audit write path.
4. Add test-only fake adapter injection. Production adapters must have no retries for writes, no shell interpolation, no credentials in exceptions, and a default deny for unknown actions.
5. Run the focused adapter, connection, readonly smoke and capability tests.

## Task 3: 云效 Provider（读取、评论、负责人/状态变更）（B3） — Complete

**Files:**
- Create: `app/providers/yunxiao.py`
- Modify: `app/yunxiao_read.py`
- Modify: `app/yunxiao_transaction.py`
- Create: `tests/test_yunxiao_provider.py`
- Modify: `tests/test_yunxiao_read.py`

**Interfaces:**
- Actions: `workitem.read`, `workitem.comments.read`, `workitem.comment.write`, `workitem.owner.update`, `workitem.status.update`.
- Requires the typed `yunxiao` Profile and encrypted `pat`; organization/project/work item identifiers are aliases/validated identifiers, never URLs from untrusted input.

1. Write fake-HTTP RED tests for work-item/comments reads, correct URL construction, bounded JSON response, response redaction, and no use of legacy environment credentials when a Manager Profile is selected.
2. Move credential resolution behind the adapter; retain legacy read helpers only as explicit compatibility adapters that cannot execute through Manager routes. Do not migrate or read current environment tokens during this task.
3. Implement read actions using constrained `GET` requests and a fixed base URL/allowlist. Each response records source, request-id (if safe), content hash and bounded redacted summary.
4. Implement write actions as `remote_write`: render the final comment/status/owner diff in a plan; require the Task 1 confirmation; execute exactly once; immediately GET/read back and mark `verified`, `unverified` or `failed`. Comments must remain business-oriented and must not disclose code paths, tokens or raw payloads.
5. Reject all other work-item mutation, iteration, attachment, service-change and close actions by policy. Test unchanged external state on missing/invalid authorization and exact read-back behavior with fakes.
6. Run Yunxiao focused tests without making a network request.

## Task 4: Git 与 GitLab Provider（B4）

**Files:**
- Create: `app/providers/git.py`
- Create: `app/providers/gitlab.py`
- Create: `app/repository_scope.py`
- Create: `tests/test_git_provider.py`
- Create: `tests/test_gitlab_provider.py`
- Create: `tests/test_repository_scope.py`
- Modify: `tests/test_git_capabilities.py`

**Interfaces:**
- Git actions: `repo.status.read`, `repo.log.read`, `repo.diff.read`, `branch.create`, `commit.create`, `remote.fetch`.
- GitLab actions: `project.read`, `merge_request.read`, `merge_request.comment.write`, `merge_request.create`.

1. Write RED tests with a temporary local Git repository and fake GitLab transport. A repository scope must resolve an existing configured local path, reject symlinks escaping it and never use shell command strings.
2. Implement Git as argument-list `subprocess.run`, fixed locale, timeout, no credential helpers in output, byte limits and redacted error output. Status/log/diff are `read`; branch/commit are `local_mutation`; fetch is a separately confirmed network action.
3. Implement branch creation and local commit only through authorization plans, checking clean/allowed path/branch policy, creating no force branch and no amend. Return commit SHA only after `git rev-parse` verification. Never implement reset, clean, checkout discard, rebase, force push or deletion.
4. Implement GitLab constrained reads and the two remote write actions through fake-tested HTTP adapter calls plus mandatory read-back. Do not implement merge, delete, approval override, project settings, variables, webhooks or protected branch changes.
5. Add complete tests for command injection, path escape, write confirmation, duplicate authorization and output redaction. Run the focused Git/GitLab suite.

## Task 5: 数据库只读 Provider（B5）

**Files:**
- Create: `app/providers/database_readonly.py`
- Modify: `app/database_read_policy.py`
- Create: `tests/test_database_readonly_provider.py`
- Modify: `tests/test_database_read_policy.py`

**Interfaces:**
- Actions: `database.schema.read`, `database.query.read`, `database.view_sql.draft`.
- `database.view_sql.draft` returns text only and has no executor/action-plan conversion.

1. Write RED tests using a temporary SQLite fixture plus a fake DB-API driver. Assert a configured Profile needs `readonly_policy=required`; all DDL/DML/transaction/pragma/attach/multi-statement/procedure syntax is rejected before a connection opens.
2. Define a narrow driver factory that only accepts supported explicit dialects, validates the typed profile fields, obtains the password in process only after authorization, and sets connection/read-only options appropriate to each driver. Do not add a generic SQL execution escape hatch.
3. Enforce AST/token policy plus server-side statement timeout, row limit, column/response-size limits and a result redactor. Store only SQL hash, target alias, count/timing and safe schema/result summary in audit; raw result content is only returned to the local caller within the response limit.
4. Add schema metadata reads and SQL draft generator with a visible `manual_execution_required` result. Draft SQL cannot flow into `query.read` automatically.
5. Run database policy/provider tests and assert the test driver never observed a mutation statement.

## Task 6: 受控密文执行解析器（B6）

**Files:**
- Modify: `app/manager_provider_repository.py`
- Modify: `app/provider_execution.py`
- Modify: `tests/test_manager_provider_repository.py`
- Modify: `tests/test_provider_execution.py`
- Create: `tests/test_authorized_credential_resolution.py`

**Interfaces:**
- Only a `ProviderExecutionContext` created after a successfully consumed authorization may resolve a Profile credential.
- Configuration/listing/preflight/UI routes continue to expose only `configured` status and must never decrypt.

1. Write RED tests that a successfully consumed plan can resolve only its own typed credential field through the common execution service; before confirmation, after expiry/reuse, with another Profile, or from a listing/preflight path, decryption is impossible.
2. Add a narrowly scoped Manager credential resolver that authenticates the encrypted record AAD against the selected Profile and field and returns plaintext only in memory to the existing `ProviderExecutionContext`. Missing master key, absent/tampered ciphertext, unsupported field and decrypt failures fail closed with a redacted stable reason.
3. Make `ProviderExecutionService` use this resolver as its production default only after `ProviderActionAuthorizer.consume` succeeds; preserve dependency injection for fake adapters. No HTTP/API handler or model/provider response may call the resolver directly.
4. Verify plaintext cannot enter an execution result, audit record, exception, repr, HTML/JSON response or test diagnostic; resolvers must not cache plaintext across calls.
5. Run resolver, Provider execution, Manager repository and preflight tests; this task is mandatory before any Task 11 staged real connection test.

## Task 7: 真实模型单节点 smoke（C1）

**Files:**
- Modify: `app/model_provider_runtime.py`
- Modify: `app/model_worker_smoke.py`
- Modify: `app/manager_model_smoke_preflight.py`
- Create: `app/providers/model_smoke.py`
- Create: `tests/test_manager_model_smoke_execution.py`
- Modify: `tests/test_model_provider_runtime.py`
- Modify: `tests/test_model_worker_smoke_readiness.py`

**Interfaces:**
- Action: `model.single_node.smoke` only; fixed prompt/response marker remains `SMOKE_OK`.
- Normal runtime modes (`openai`, `real`, `anthropic`, `claude`, `zhipu`) and the model DAG remain frozen.

1. Write RED tests proving a Manager model Profile with encrypted key can make exactly one fake transport call only after a consumed authorization, without a credential file or legacy profile JSON. Test timeout, endpoint allowlist, redacted failure and marker validation.
2. Add a Manager-backed profile resolver for `ControlledModelProviderRuntime`; retain file-based resolver only for non-Manager legacy tests and prohibit it from Manager HTTP routes.
3. Route the smoke request through the common Provider execution boundary. Prompt, model, endpoint host, timeout and max tokens are fixed/allowlisted from typed Profile fields; no tool invocation, file access, callback URL or user-provided prompt is accepted.
4. Record only safe evidence (profile alias, endpoint host, model alias, result marker, request/response hashes, usage and timing). The model never receives authorization ID or other Provider credentials.
5. Add a Manager UI readiness state that distinguishes `configuration_missing`, `awaiting_confirmation`, `smoke_passed`, `smoke_failed` and `dag_still_frozen`; run focused model tests.

## Task 8: 自动学习 candidate 审核与知识库优先链路（C2）

**Files:**
- Modify: `app/learning_loop.py`
- Modify: `app/knowledge_consultation.py`
- Modify: `app/knowledge_index.py`
- Create: `app/learning_candidate_repository.py`
- Create: `tests/test_learning_candidate_repository.py`
- Modify: `tests/test_learning_loop.py`
- Modify: `tests/test_knowledge_consultation.py`
- Modify: `tests/test_knowledge_index.py`

**Interfaces:**
- Candidate states: `candidate`, `approved`, `rejected`, `promoted`, `expired`.
- Outcomes from every failed controlled run: `eval.sample`, `contract_plugin.draft`, `rule_pack.draft`, `knowledge.candidate`; no automatic promotion.

1. Write RED tests that a failed audit/run creates an idempotent candidate set in the Manager database with redacted evidence references; no candidate path, model response or secret is persisted.
2. Replace local JSON candidate persistence for Manager runs with repository persistence. Keep the existing file function as an explicit offline export compatibility tool, not the operational source of truth.
3. Implement reviewer-only approve/reject/promote transactions. Promotion to Obsidian-compatible Markdown is permitted only for an approved `knowledge.candidate` with nonempty safe evidence and explicit reviewer identity; eval/rule/contract drafts remain non-executable artifacts until separately activated.
4. Update consultation flow: retrieve approved, unexpired knowledge first; when sufficient, answer with citations and `model_used=false`; when insufficient, store a redacted consultation then return a candidate recommendation. Do not implicitly call a model in either branch.
5. Add Obsidian index manifest/status, atomic Markdown writes and duplicate content hash suppression. Tests use temporary knowledge homes only; no real Vault is changed.
6. Run learning/knowledge focused tests.

## Task 9: HIS 业务验收证据与 Manager UI 完整面板（C3）

**Files:**
- Modify: `app/business_acceptance.py`
- Create: `app/business_acceptance_repository.py`
- Modify: `app/core_status.py`
- Modify: `app/server.py`
- Create: `tests/test_business_acceptance_repository.py`
- Modify: `tests/test_business_acceptance.py`
- Modify: `tests/test_server_core_status_api.py`
- Modify: `tests/test_manager_readiness_card.py`

**Interfaces:**
- Produces explicit environment/test-data aliases, scenario evidence, technical result, reviewer decision and `business_valid` state.
- Manager pages: `/providers`, `/actions`, `/knowledge`, `/learning-candidates`, `/business-acceptance`; JSON equivalents are localhost/CSRF/origin guarded where mutating.

1. Write RED tests that no checkbox, smoke result or offline test can set `business_valid=true`; only a complete evidence record with environment, operator alias, test-data alias, scenario expected/actual/evidence, runtime verification and explicit acceptance may do so.
2. Persist versioned business evidence with immutable creation record and append-only reviewer decision. Redact free text before persistence and reject secret-shaped content. An evidence record is not an external state mutation.
3. Extend Manager UI to create plans, display exact diff/risks, confirm a plan, show execution/read-back audit, review candidates and record acceptance evidence. Do not add generic “run command” or raw SQL execution controls.
4. Preserve Host/Origin/CSRF checks on every POST; add tests for unauthenticated cross-origin/invalid-CSRF rejection and for non-disclosure of sensitive fields in HTML/JSON.
5. Update the home/readiness card to list exactly what is code-ready, configured, locally tested, externally verified and business accepted. No status label may collapse these levels.
6. Run focused UI/API/business tests.

## Task 10: 完整流程回归、独立审核与交付资料（C4）

**Files:**
- Create: `tests/test_complete_manager_flow.py`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-09-manager-provider-configuration-design.md`
- Create: `docs/manager-runbook.md`

1. Add a complete fake-provider scenario: configure profile and credential → create and confirm a read plan → execute and audit → create and review a candidate → retrieve knowledge → record technical/business evidence. The scenario must not use a real network, real Git remote, real HIS database or the actual knowledge home.
2. Add negative flows: expired confirmation, changed payload, forbidden database mutation, model DAG invocation, secret-shaped user input, external write without confirmation and candidate auto-promotion.
3. Run all tests directly related to changed modules; then run the full `tests` suite once, `python3 -m py_compile` for changed Python modules and `git diff --check`.
4. Have an independent reviewer inspect the diff and rerun the focused/full verification. Fix only confirmed issues, rerun affected tests and record exact evidence in the implementation ledger.
5. Update README/runbook with the boundary table, the Manager deployment key requirement, UI operation sequence, rollback method, and a statement that database writes are permanently unsupported.

## Task 11: 正式 Harness 迁移与分层验收（D — after B/C are code-complete)

**Files/locations:**
- Source: this isolated worktree
- Target (requires explicit target confirmation): `/Users/lym/WorkCode/ai/Harness`
- Target backup: a timestamped sibling backup path, exact name confirmed before creation

1. Before copying anything, compare source/target file hashes, list all target-only and divergent files, verify free space, create a recoverable backup, and publish the exact merge list plus rollback command. Because the target has no Git history and already diverges, never use wholesale overwrite or delete target files.
2. Apply only a reviewed, three-way/manual merge to a staged copy first. Start it on loopback with a separate temporary Manager database and test knowledge directory. Confirm the currently used target service/process has not been interrupted.
3. Run the Task 10 fake regression on the staged copy. Only after it passes can the target launch path be switched, with an explicit rollback procedure and no existing Manager database migration until backup is confirmed.
4. User configures Profile fields through Manager UI. The deployment owner supplies the server-side AES master key outside the UI; users never paste or retrieve that key. Credentials are entered only via password forms and verified by redacted status.
5. Conduct external verification in order: Yunxiao read → Git local read → database read-only technical query → model fixed single-node smoke → GitLab read. Each result is `passed`, `failed` or `not_verified`; do not infer one from another.
6. For each requested remote/local write (cloud comment/owner/status, branch, commit, fetch, GitLab comment/MR), present the one-time plan and wait for user confirmation at that moment. Execute once and read back. Database writes are excluded permanently.
7. HIS business acceptance is last: user supplies test environment, accounts/aliases, test data aliases and scenario evidence. Record verdicts but do not claim production validation unless the user explicitly supplies production evidence.

## Required Inputs Only at the External Verification Phase

No input is needed to implement and fake-test tasks B/C. Before Task 11 external verification, the deployment/test owner will need to provide through the Manager UI or the test runbook: provider base URLs/allowed hosts, profile aliases, read-only database account and test database endpoint, local repository scope(s), GitLab project aliases, model endpoint/model alias, a test work item/project alias, and HIS test scenarios/environment/account/test-data aliases. Actual write execution additionally needs a specific displayed plan confirmed by the user. Never paste credentials into chat, source files, plans or JSON fixtures.
