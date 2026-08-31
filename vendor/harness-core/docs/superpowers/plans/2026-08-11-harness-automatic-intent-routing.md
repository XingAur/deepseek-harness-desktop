# Harness Automatic Intent Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 自动把普通咨询路由到知识问答，把任何需求相关询问稳定路由到完整需求流程，并在 Manager 中提供可审计状态。

**Architecture:** 使用无副作用的确定性分类器、Manager DB 中的粘滞会话与追加式事件、统一路由服务，以及 Manager API/UI 集成。分类先于知识、云效或 Provider 选择；需求模式只能由用户显式纠正，不能因外部能力缺失而降级。

**Tech Stack:** Python 3.14 standard library, SQLite Manager repository, `unittest`, existing `BaseHTTPRequestHandler` Manager UI.

## Global Constraints

- 只修改隔离工作树，不修改 `/Users/lym/WorkCode/ai/Harness`。
- 所有测试在 import 前显式设置临时 `HARNESS_DB_PATH` 和 `HIS_KNOWLEDGE_HOME`；禁止打开、迁移或处置默认 `data/harness.sqlite`、WAL 和备份。
- Harness 自动选择模式；普通用户不需要逐次选择。
- 需求模式在会话内粘滞，只能被用户显式纠正，不能因问句、无云效、Provider 缺失或连接失败降级。
- 无云效关联的需求记录 `unlinked`/`not_applicable` 并继续需求流程。
- 需求询问未要求修改时仍走完整分析治理流程，但零修改、零外部写。
- 云效/Git/GitLab 外部写仍需一次性授权和人工确认；数据库永久只读。
- 路由持久化不得保存原始秘密或未经脱敏的输入。
- 不启用真实模型、真实 Provider、正常 Agent DAG 或正式迁移。

---

### Task 1: Pure Automatic Intent Classifier

**Files:**
- Create: `app/task_intent_router.py`
- Create: `tests/test_task_intent_router.py`

**Interfaces:**
- Produces: `IntentContext`, `IntentDecision`, `classify_task_intent(message, context, previous_mode=None, explicit_override=None)`.
- `IntentDecision` exposes `mode`, `reason_codes`, `confidence`, `sticky`, `linked_work_item`, `yunxiao_status`, `current_phase`, and `next_route`.

- [ ] **Step 1: Write failing classifier contract tests**

  Cover general questions, explicit work items, requirement questions without Yunxiao, sticky task sessions, provider failures, question punctuation, ambiguous inputs, and explicit correction. Assert that provider/Yunxiao failure never changes `task` to `question`.

- [ ] **Step 2: Run RED**

  Run: `PYTHONDONTWRITEBYTECODE=1 /private/tmp/harness-stagea-crypto.EkwKkd/bin/python -m unittest -q tests.test_task_intent_router`

  Expected: import failure because `app.task_intent_router` does not exist.

- [ ] **Step 3: Implement the minimal pure classifier**

  Use validated structured fields first, bounded strong textual patterns second, and conservative `task` fallback for ambiguous task-like inputs. Do not call databases, models, providers, or the network.

- [ ] **Step 4: Run GREEN**

  Run the command from Step 2; expected all tests `OK`.

### Task 2: Sticky Manager Routing Repository

**Files:**
- Modify: `app/database.py`
- Create: `app/task_intent_repository.py`
- Create: `tests/test_task_intent_repository.py`
- Modify: `tests/test_database_governance.py`

**Interfaces:**
- Consumes: `IntentDecision` from Task 1.
- Produces: `TaskIntentRepository.get_session(conversation_key)`, `record_decision(...)`, and `list_recent_events(limit=100)`.

- [ ] **Step 1: Write failing repository and v68 migration tests**

  Assert current session persistence, task stickiness, explicit correction audit, redacted message summary/hash, append-only event triggers, and no raw Bearer/opaque secret storage.

- [ ] **Step 2: Run RED using an explicit temporary database**

  Run the repository and database-governance focused tests with a fresh `mktemp -d` path exported before Python starts. Expected failures are missing schema/repository only.

- [ ] **Step 3: Implement schema v68 and repository**

  Add `manager_task_intent_sessions` and `manager_task_intent_events`; event rows are append-only. Store stable reason codes, sanitized aliases, redacted summary, and SHA-256 only.

- [ ] **Step 4: Run GREEN and strict ResourceWarning verification**

  Run focused tests normally and with `PYTHONWARNINGS=error::ResourceWarning`; expected `OK` with no warning output.

### Task 3: Routing Service and Workflow Gate

**Files:**
- Create: `app/task_intent_service.py`
- Modify: `app/knowledge_consultation.py`
- Modify: `app/task_capability_routing.py`
- Create: `tests/test_task_intent_service.py`
- Modify: `tests/test_knowledge_consultation.py`
- Create: `tests/test_task_capability_routing.py`

**Interfaces:**
- Consumes: classifier and repository.
- Produces: `TaskIntentService.route(...)` and a routing result that selects `knowledge` or `requirement_workflow` before capabilities are considered.

- [ ] **Step 1: Write failing integration tests**

  Assert knowledge consultation is used only for `question`; requirement questions return/enter `requirement_workflow`; missing Yunxiao and provider failures preserve task mode; inquiry-only requirements produce zero mutation actions; explicit external write confirmation rules and database read-only rules remain unchanged.

- [ ] **Step 2: Run RED**

  Run the three focused test modules with temporary DB/knowledge paths. Expected failures identify the absent service/gate.

- [ ] **Step 3: Implement route-before-provider integration**

  Read the sticky session before classification, persist the new decision, and reject capability routing that attempts to treat a `task` as a plain knowledge consultation. Do not execute a Provider as part of classification.

- [ ] **Step 4: Run GREEN and affected B1-C4 regression**

  Run focused routing/knowledge tests plus authorization, Provider execution, complete Manager flow, and database read-policy tests. Expected all assertions `OK`.

### Task 4: Manager Routing API, UI, and Documentation

**Files:**
- Modify: `app/server.py`
- Modify: `README.md`
- Modify: `docs/manager-runbook.md`
- Modify: `tests/test_server_core_status_api.py`
- Modify: `tests/test_complete_manager_flow.py`

**Interfaces:**
- Consumes: `TaskIntentService`.
- Produces: `GET /routing`, `GET /api/manager/routing`, and CSRF-protected `POST /routing/classify`.

- [ ] **Step 1: Write failing HTTP/UI contract tests**

  Assert UI shows mode, reason, linked work item, Yunxiao status, phase, next route, and an optional correction control. Assert no per-question required selector, no authorization token rendering, no raw prompt/secret echo, and no default database access.

- [ ] **Step 2: Run RED**

  Run non-socket renderer tests first, then the approved loopback server test only with a new temporary DB and knowledge home. Expected 404/missing rendering assertions.

- [ ] **Step 3: Implement minimal Manager API/UI**

  Add navigation and status rendering using existing HTML/layout patterns. Protect POST with the existing Host/Origin/CSRF guard. The correction field is optional and never required for ordinary routing.

- [ ] **Step 4: Update docs and run GREEN**

  Document the two automatic modes, sticky requirement invariant, Yunxiao skip/not-applicable behavior, and unchanged write/DB boundaries. Run focused UI/API tests and `git diff --check`.

### Task 5: Independent Final Gate

**Files:**
- Update: `.superpowers/sdd/progress.md`
- Create: `.superpowers/sdd/stage-e-automatic-intent-routing-review.md`

**Interfaces:**
- Consumes: Tasks 1-4 stable diff and test reports.
- Produces: independent `APPROVED` or `CHANGES_REQUIRED` verdict.

- [ ] **Step 1: Run an independent static and adversarial review**

  Review classification downgrade paths, sticky session correctness, secret storage/output, default DB isolation, CSRF, external-write boundaries, and DB read-only invariants.

- [ ] **Step 2: Run focused verification once on a new temporary environment**

  Run classifier, repository, service, knowledge, routing, Manager API/UI, authorization, Provider execution, database read-only and complete-flow suites. Do not contact real systems.

- [ ] **Step 3: Record the final verdict**

  Mark complete only after all Critical/Important findings are fixed and independently re-reviewed. Clearly state that local tests do not equal real HIS/Yunxiao/GitLab/business acceptance or formal migration.
