# Delivery Original Checkout Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Harness and the `his-harness` skill from creating delivery branches, commits, cherry-picks, or pushes in a linked worktree instead of the original business repository checkout.

**Architecture:** Add an early repository-shape guard to `inspect_repository()` so every Delivery Closure transaction rejects a Git root whose `.git` entry is not a directory. Update the skill contract to make `tools/delivery.py` the only delivery path and keep temporary worktrees limited to editing and verification.

**Tech Stack:** Python 3, Git CLI, `unittest`, Markdown skill instructions.

## Global Constraints

- Do not touch business repositories, real remotes, Yunxiao, or existing RC commits.
- Do not create a Harness commit or push; the Harness root is not a Git repository.
- Preserve temporary worktrees for local editing and verification only.
- Reject linked worktrees before branch switching, committing, cherry-picking, or pushing.
- Keep existing original-checkout Delivery Closure behavior and Safety Shelf behavior unchanged.

---

### Task 1: Linked Worktree Regression Test

**Files:**
- Modify: `tests/test_delivery_closure.py`

**Interfaces:**
- Consumes: `inspect_repository(request, policy)`.
- Produces: a regression assertion for blocker `delivery_project_linked_worktree`.

- [x] **Step 1: Write the failing test**

Create a real linked worktree from `GitRepositoryFixture`, point `DeliveryRequest.project_path` at it, and assert `unsafe_repository_state` plus `delivery_project_linked_worktree`.

- [x] **Step 2: Verify RED**

Run:

```bash
python3 -m unittest tests.test_delivery_closure.DeliveryPlanningTests.test_linked_worktree_is_blocked_for_delivery -v
```

Expected: FAIL because the linked worktree is currently accepted.

### Task 2: Original Checkout Guard

**Files:**
- Modify: `app/delivery_closure.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: resolved `project_path` after Git-root verification.
- Produces: blocker `delivery_project_linked_worktree` when `project_path/.git` is not a directory.

- [x] **Step 1: Implement the minimum guard**

Return `unsafe_repository_state` before reading or changing branch state when `.git` is a file or otherwise not a directory.

- [x] **Step 2: Verify GREEN**

Run the new test and the full delivery-closure test module.

- [x] **Step 3: Document the enforced boundary**

State that the delivery project path must be the original checkout with a `.git` directory and that a linked-worktree `.git` file is blocked.

### Task 3: Skill Delivery Contract

**Files:**
- Modify: `/Users/lym/.codex/skills/his-harness/SKILL.md`
- Review: `/Users/lym/.codex/skills/his-harness/agents/openai.yaml`

**Interfaces:**
- Consumes: the formal `tools/delivery.py` transaction workflow.
- Produces: one mandatory execution recipe for commit, feature push, RC cherry-pick, and RC push.

- [x] **Step 1: Replace permissive manual Git guidance**

Require the original business checkout and `tools/delivery.py`; forbid using a linked worktree or ad-hoc `git switch/commit/cherry-pick/push` as a workaround for dirty files.

- [x] **Step 2: Preserve authorization boundaries**

Keep commit, feature push, RC integration, and RC push separately governed by the immutable plan and its confirmations.

- [x] **Step 3: Validate and forward-test the skill**

Run `quick_validate.py` and a fresh-context pressure scenario where the original repo contains unrelated dirty files.

### Task 4: Completion Verification

**Files:**
- Verify only.

**Interfaces:**
- Produces: fresh evidence for regression, syntax, skill validation, and unchanged broader behavior.

- [x] **Step 1: Run targeted tests**

Run delivery closure and CLI tests.

- [x] **Step 2: Run broader Harness verification**

Run the relevant full unit suite and compile checks available in the repository.

- [x] **Step 3: Inspect final changes**

Review the exact modified files and confirm that no business repository or remote state was changed.
