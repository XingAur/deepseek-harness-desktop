# Universal Demand Delivery Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven-development for every implementation task. This plan is executed inline because the Harness directory is not a Git repository and no Git delivery is authorized.

**Goal:** Make every HIS requirement run through one user-visible lifecycle: intake, evidence analysis, clarification, change-scope confirmation, implementation, verification, and business acceptance.

**Architecture:** Reuse the existing `RequirementWorkflowRunner` and `_TaskStageLedger`. Add an additive progress/confirmation model that translates technical stage events into business-facing status, next action, affected repositories/services, evidence level, and confirmation gates. Store pre-change and post-change snapshots as append-only artifacts; existing governance and mutation gates remain authoritative.

**Tech Stack:** Python 3 standard library, dataclasses, JSON, Markdown, `unittest`.

## Global Constraints

- No Yunxiao/Git/GitHub/GitLab writes, deployment, database mutation, plugin replacement, or credential exposure.
- Existing artifacts and stage ledger remain backward-compatible; new progress artifacts are additive.
- Technical evidence must remain distinct from runtime/business acceptance evidence.
- User confirmation is requested only for business ambiguity, pre-change scope, or final business acceptance—not for backend implementation details.

---

### Task 1: Add a user-facing progress snapshot model

**Files:**
- Create: `app/demand_progress.py`
- Create: `tests/test_demand_progress.py`

**Interfaces:**
- `build_demand_progress_snapshot(...) -> dict`
- `demand_progress_to_markdown(snapshot: Mapping[str, Any]) -> str`

The snapshot must expose `current_stage`, `stage_statuses`, `completed`, `next_action`, `affected_scope`, `evidence_level`, `confirmation`, `open_questions`, and `proposed_subtasks`. It must never expose secrets or raw provider payloads.

### Task 2: Persist progress at the two user decision gates

**Files:**
- Modify: `app/harness.py`
- Test: `tests/test_harness_capability_routing.py`

After governance/contract calculation, store a pre-change snapshot that clearly states whether the run is ready for scope confirmation or blocked. In `_finalize_task_result`, store a post-change snapshot that summarizes modification, verification, review, and final business acceptance. Add the new artifact kinds to `write_run_outputs`.

### Task 3: Show progress and confirmation in the main report

**Files:**
- Modify: `app/harness.py`
- Test: `tests/test_harness_capability_routing.py`

Render the pre-change and post-change snapshots near the report summary. Keep the existing detailed expert reports below them. The report must state what the user needs to confirm and what the Harness has already verified.

### Task 4: Verify without changing existing mutation gates

**Files:**
- Modify: `tests/test_demand_progress.py`
- Modify: `tests/test_harness_capability_routing.py`

Run focused progress, governance, and workflow tests. Confirm readonly and blocked runs never acquire a writable confirmation state, and confirm existing twelve-stage ledger assertions remain unchanged.
