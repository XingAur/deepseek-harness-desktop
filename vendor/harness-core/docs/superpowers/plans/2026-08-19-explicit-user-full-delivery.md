# Explicit User Full Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a user explicitly requests delivery, Harness executes the complete approved delivery plan—task-branch push, RC integration and RC push, plus the specified GitLab write—without requiring a new capability-enablement decision at each checkpoint.

**Architecture:** Keep the default endpoint local-only. A delivery request that carries explicit user intent creates an immutable plan whose exact remote targets and actions are visible before execution. The existing one-use authorization, repository drift checks, read-back receipts, transaction journal and recovery state remain mandatory; they are execution safeguards, not repeated requests for policy permission.

**Tech Stack:** Python 3, unittest, existing provider execution runtime, bounded `/usr/bin/git` adapter, GitLab HTTP adapter, SQLite delivery journal.

## Global Constraints

- A completed code change never starts a remote write by itself; only a user instruction classified as `submit` / `push` / `deliver` creates a remote-action plan.
- One explicit delivery intent authorizes the plan's included task push, RC integration, RC push and declared GitLab API write; it does not authorize a different target, changed plan hash, force push, deletion, reset or arbitrary GitLab API call.
- Use only fixed argv Git operations; reject credential-bearing URLs, force push, dirty or drifted worktrees, unknown remote state and ambiguous refs.
- A network interruption after dispatch reports `unknown` and persists recovery evidence; it must not claim that a push was not applied.
- GitLab writes are bounded to existing structured actions (`merge_request.create` and `merge_request.comment.write`); a generic submit request must not invent a comment body or MR target.
- No live remote Git/GitLab call is made by tests. Tests use a disposable local bare Git remote and simulated GitLab transport.

---

### Task 1: Represent explicit full-delivery actions in the immutable plan

**Files:**
- Modify: `/Users/lym/plugins/his-engineering/scripts/delivery_closure.py`
- Modify: `/Users/lym/WorkCode/ai/Harness/tools/delivery.py`
- Test: `/Users/lym/WorkCode/ai/Harness/tests/test_delivery_closure.py`
- Test: `/Users/lym/WorkCode/ai/Harness/tests/test_delivery_cli.py`

**Interfaces:** `DeliveryRequest` accepts explicit action declarations. `build_delivery_plan()` records `push_feature`, `cherry_pick_integration`, `push_integration` and a validated optional GitLab action in `actions`; its hash changes when any one changes. `prepare` does not execute a remote write.

- [ ] **Step 1: Write failing tests**

```python
def test_explicit_delivery_plan_retains_all_requested_remote_actions(self):
    request = self.request(push_feature=True, cherry_pick_integration=True, push_integration=True)
    plan = build_delivery_plan(request, DeliveryPolicy(), self.snapshot)
    self.assertEqual(
        {"push_feature": True, "cherry_pick_integration": True, "push_integration": True},
        {name: plan["actions"][name] for name in ("push_feature", "cherry_pick_integration", "push_integration")},
    )
    self.assertTrue(plan["remote_actions_enabled"])
```

- [ ] **Step 2: Run the focused tests and confirm they fail because plans force remote actions off.**

Run: `.venv/bin/python -m unittest tests.test_delivery_closure.DeliveryPlanTests tests.test_delivery_cli.DeliveryCliTests -v`

- [ ] **Step 3: Implement request-to-plan action binding.** Preserve the default `False` values when no explicit delivery action was requested; include an immutable structured `gitlab_action` only when the caller supplied all fields required by the selected API action.

- [ ] **Step 4: Re-run the focused tests and confirm they pass.**

### Task 2: Add bounded one-use remote Git push execution

**Files:**
- Modify: `/Users/lym/WorkCode/ai/Harness/app/providers/git.py`
- Modify: `/Users/lym/WorkCode/ai/Harness/app/provider_execution.py`
- Modify: `/Users/lym/WorkCode/ai/Harness/app/provider_capability_status.py`
- Modify: `/Users/lym/plugins/his-engineering/capabilities.json`
- Test: `/Users/lym/WorkCode/ai/Harness/tests/test_git_provider.py`
- Test: `/Users/lym/WorkCode/ai/Harness/tests/test_git_provider_security.py`
- Test: `/Users/lym/WorkCode/ai/Harness/tests/test_provider_execution.py`

**Interfaces:** Register executable `remote.push` as a `remote_write` action. It receives exactly `repository_alias`, `remote_alias`, `source_ref`, `target_ref`, `expected_head_sha`, `expected_remote_sha`, `force=False`; execution requires a consumed one-use authorization and returns a redacted post-push receipt.

- [ ] **Step 1: Write failing disposable-remote tests.** Cover successful non-force task-branch push, rejected dirty/head/remote-ref drift, rejected force push, and dispatch interruption returning `unknown` with a recovery record.

- [ ] **Step 2: Run only those tests and confirm `remote.push` is not an executable descriptor.**

Run: `.venv/bin/python -m unittest tests.test_git_provider tests.test_git_provider_security tests.test_provider_execution -v`

- [ ] **Step 3: Implement fixed-argv remote push.** Before dispatch, validate the repository scope and HTTPS remote identity, read local head and remote target ref, compare both expected SHAs, and run only `git push --porcelain --no-verify <approved-url> <source-ref>:<target-ref>`. Record a non-sensitive dispatch identity before starting and read the remote ref back after success.

- [ ] **Step 4: Re-run the remote Git suites and confirm they pass without a real network request.**

### Task 3: Complete transaction-based task push and RC integration/push

**Files:**
- Modify: `/Users/lym/plugins/his-engineering/scripts/delivery_closure.py`
- Modify: `/Users/lym/plugins/his-engineering/scripts/git_delivery.py`
- Modify: `/Users/lym/WorkCode/ai/Harness/tools/delivery.py`
- Test: `/Users/lym/WorkCode/ai/Harness/tests/test_delivery_closure.py`
- Test: `/Users/lym/WorkCode/ai/Harness/tests/test_delivery_cli.py`

**Interfaces:** The first delivery phase creates the audited task commit and pushes it only when `actions.push_feature` is true. The RC phase only cherry-picks and pushes when the corresponding immutable plan actions are true, the required runtime acceptance remains valid and every pre/post state matches its plan receipt.

- [ ] **Step 1: Write failing lifecycle tests** for task push, RC cherry-pick conflict stop/abort, valid RC push, push drift rejection, idempotent replay after a known receipt, and an interrupted push that becomes `recovery_required`.

- [ ] **Step 2: Run the lifecycle tests and confirm current `remote_delivery_disabled` assertions fail.**

Run: `.venv/bin/python -m unittest tests.test_delivery_closure tests.test_delivery_cli -v`

- [ ] **Step 3: Implement lifecycle checkpoints.** Reuse the plan hash and transaction journal; store a redacted result for each remote action. Do not remove a successfully pushed task branch if later RC work fails. On cherry-pick conflict, abort the temporary/RC operation and verify the original RC head before changing the transaction to a stopped recovery state.

- [ ] **Step 4: Re-run lifecycle tests and confirm all pass.**

### Task 4: Enable structured GitLab write actions in the same delivery authorization

**Files:**
- Modify: `/Users/lym/plugins/his-engineering/capabilities.json`
- Modify: `/Users/lym/WorkCode/ai/Harness/app/provider_capability_status.py`
- Modify: `/Users/lym/WorkCode/ai/Harness/app/provider_execution.py`
- Modify: `/Users/lym/plugins/his-engineering/scripts/delivery_closure.py`
- Test: `/Users/lym/WorkCode/ai/Harness/tests/test_git_provider_security.py`
- Test: `/Users/lym/WorkCode/ai/Harness/tests/test_provider_capability_status.py`

**Interfaces:** `gitlab.write` becomes executable only for a plan-declared `merge_request.create` or `merge_request.comment.write` action, with the configured host/project and its exact source/target/title or MR IID/body. The existing adapter's verification GET is required before reporting success.

- [ ] **Step 1: Write failing simulated-transport tests** proving plan-declared MR creation/comment succeeds with read-back, a missing or changed GitLab action is rejected before dispatch, and the token never appears in an outcome or audit record.

- [ ] **Step 2: Run the GitLab status/security tests and confirm capability remains disabled.**

Run: `.venv/bin/python -m unittest tests.test_provider_capability_status tests.test_git_provider_security -v`

- [ ] **Step 3: Bind the existing GitLab adapter actions to the delivery transaction.** Keep arbitrary API methods unavailable and preserve `gitlab.read` behavior.

- [ ] **Step 4: Re-run the focused GitLab tests and confirm they pass.**

### Task 5: Update the delivery contract and verify the complete fake matrix

**Files:**
- Modify: `/Users/lym/WorkCode/ai/Harness/README.md`
- Modify: `/Users/lym/plugins/his-engineering/skills/his-git-delivery/SKILL.md`
- Test: `/Users/lym/WorkCode/ai/Harness/tests/test_plugin_replay_suite.py`
- Test: `/Users/lym/WorkCode/ai/Harness/tests/test_harness_capability_routing.py`

**Interfaces:** Documentation and status distinguish `disabled`, `available with explicit user delivery plan`, and `automated delivery` (not implemented). Replay scenarios must remain readonly unless their fixture contains an explicit delivery plan.

- [ ] **Step 1: Write failing status/replay tests** that expect explicit delivery plans to expose the enabled capability without treating ordinary local changes as delivery authorization.

- [ ] **Step 2: Run the targeted replay/routing tests and confirm they fail against the old disabled-only status.**

Run: `.venv/bin/python -m unittest tests.test_plugin_replay_suite tests.test_harness_capability_routing -v`

- [ ] **Step 3: Update documentation and routing status.** State that no code completion automatically writes remote state, and a specific user delivery request creates one immutable action plan rather than four separate policy approvals.

- [ ] **Step 4: Run the complete targeted matrix and inspect every changed line.**

Run: `.venv/bin/python -m unittest tests.test_git_provider tests.test_git_provider_security tests.test_provider_execution tests.test_delivery_closure tests.test_delivery_cli tests.test_provider_capability_status tests.test_plugin_replay_suite tests.test_harness_capability_routing -v`

## Self-Review

- The plan covers every user-authorized delivery endpoint: task branch push, RC integration, RC push and structured GitLab API writes.
- Default local-only behavior remains unchanged; explicit delivery intent is the only transition into a remote-action plan.
- Each remote action is bounded to a recorded target, plan hash and receipt, so an explicit submission does not become a standing remote-write permission.
- No plan step enables force push, arbitrary GitLab writes, database writes or untested live remote calls.
