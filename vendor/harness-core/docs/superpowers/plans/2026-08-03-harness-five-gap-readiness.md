# Harness Five Gap Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把真实模型 Worker、自动学习闭环、真实业务验收、外部写动作和知识库五个缺口统一沉淀为 Manager 可读取的后端 readiness 合同。

**Architecture:** 先扩展 `app/core_status.py` 的只读 snapshot，新增 `readiness` 数据区，统一表达 `state`、`prerequisites`、`verification`、`next_actions` 和 `manager_ui`。后续 Manager UI 只消费该稳定合同，不需要每个能力完成一次就重做页面。

**Tech Stack:** Python 标准库、unittest、现有 capability registry、runtime policy、SQLite metadata-only health check。

## Global Constraints

- 不开启真实模型 DAG，不读取模型凭证，不发起网络调用。
- 不写云效、不 `git push`、不写 GitLab、不执行数据库真实变更。
- 业务验收只记录证据入口和状态，不把离线测试通过等同于 HIS 页面、接口、数据库或生产验收通过。
- 知识库自动学习只能沉淀 candidate；正式知识必须经过 review/promote。
- Manager UI 本阶段只预留统一数据合同，不做完整页面。

---

### Task 1: Core Readiness Snapshot

**Files:**
- Modify: `app/core_status.py`
- Test: `tests/test_core_status.py`

**Interfaces:**
- Consumes: `build_core_status_snapshot(...) -> dict`
- Produces: snapshot key `readiness: {"schema_version": "his-readiness.v1", "items": [...]}`.

- [ ] **Step 1: Write the failing test**

Add assertions that `build_core_status_snapshot()` returns five readiness items:

```python
readiness = snapshot["readiness"]
self.assertEqual("his-readiness.v1", readiness["schema_version"])
self.assertEqual(
    {
        "real_model_worker",
        "learning_loop",
        "business_acceptance",
        "external_writes",
        "knowledge_home",
    },
    {item["id"] for item in readiness["items"]},
)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_core_status.CoreStatusTests.test_snapshot_reports_verified_enforce_plugins_without_external_access -v
```

Expected: FAIL because `readiness` is missing.

- [ ] **Step 3: Write minimal implementation**

Extend `app/core_status.py` with helpers that derive five items from existing runtime policy, capability descriptors and `knowledge_home` filesystem metadata. Do not execute capabilities.

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_core_status -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/core_status.py tests/test_core_status.py docs/superpowers/plans/2026-08-03-harness-five-gap-readiness.md
git commit -m "feat(manager): expose five-gap readiness snapshot"
```

### Task 2: Readiness CLI/API Surface

**Files:**
- Modify: `tools/task_manager.py` or existing status CLI if present
- Modify: `app/server.py` only if a read-only JSON route already fits current server style
- Test: add focused unittest around JSON output without starting external services

**Interfaces:**
- Consumes: `build_core_status_snapshot(...)`
- Produces: stable JSON consumable by later Manager UI.

- [ ] Add a read-only command or route that returns the full core status snapshot.
- [ ] Assert it never reads credential values and never reports external calls.
- [ ] Run focused tests.
- [ ] Commit separately.

### Task 3: Learning Candidate Queue Contract

**Files:**
- Modify: knowledge plugin routing docs or Harness task output index
- Test: focused tests around failed sample -> candidate metadata only

**Interfaces:**
- Consumes: future failed run/eval/contract-plugin/rule-pack metadata.
- Produces: local candidate draft metadata; never calls promote.

- [ ] Add failing test for candidate-only learning outcome.
- [ ] Implement metadata-only candidate contract.
- [ ] Verify candidate create/review/promote remain explicit.
- [ ] Commit separately.

### Task 4: Business Acceptance Evidence Contract

**Files:**
- Modify: acceptance/gate output model
- Test: enterprise gate stays `business_valid=false` unless explicit business acceptance evidence is present.

**Interfaces:**
- Consumes: structured manual/runtime evidence.
- Produces: status usable by Manager UI: `not_verified`, `evidence_recorded`, `accepted`, `rejected`.

- [ ] Add failing test for no-overclaim boundary.
- [ ] Implement explicit evidence status contract.
- [ ] Verify offline gate remains technical-only.
- [ ] Commit separately.

### Task 5: External Write Transaction Readiness

**Files:**
- Modify: capability/status reporting only.
- Test: L4/L5 write capabilities remain disabled but have visible prerequisites and dry-run next action.

**Interfaces:**
- Consumes: capability registry descriptors.
- Produces: Manager-readable status for `workitem.write`, `git.push`, `gitlab.write`, `database.change`.

- [ ] Add failing test for disabled write capability reporting.
- [ ] Implement prerequisite and blocker reporting.
- [ ] Verify no write executor is invoked.
- [ ] Commit separately.
