# Readonly Discovery Unblock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven-development for every implementation task. No Git delivery is part of this plan because the current local Harness directory has no Git metadata.

**Goal:** Let a partial work-item continue into local, read-only code discovery while keeping every mutation path blocked until the requirement and change contract are complete.

**Architecture:** The provider-intake gate distinguishes `readonly_discovery_allowed` from `mutation_allowed`. Existing `TechnicalDecisionResult` receives a first HIS discovery profile for the stored `shangXiaWWsBz` classification and reports its UI, endpoint and storage evidence without producing a patch.

**Tech Stack:** Python 3 standard library and `unittest`; no network, provider write, credential access, database write, commit, push or deployment.

## Global Constraints

- Only source reading is allowed after partial ticket evidence.
- `needs_requirement_confirmation` must never authorize patch generation or any external write.
- The discovery output must call out stored values `0=上午`, `1=下午`, and preserve existing value `2=晚上` when the default is `全部`.

### Task 1: Separate readonly discovery from mutation intake

**Files:**

- Modify: `../plugins/his-harness-core/skills/harness-workitem-intake/scripts/intake.py`
- Test: `../plugins/his-harness-core/skills/harness-workitem-intake/tests/test_intake.py`
- Modify: `../plugins/his-harness-core/skills/harness-workitem-intake/SKILL.md`
- Modify: `../plugins/his-harness-core/skills/harness-workitem-intake/references/intake-contract.md`

- [x] Write a failing test that sends `needs_requirement_confirmation` evidence and expects `accepted_for_readonly_discovery`, `mutation_allowed=false`, `next_action=start_readonly_discovery`, and an `analysis=pending` event.
- [x] Run the targeted test and verify it fails because the old intake emits `blocked`.
- [x] Implement the smallest intake state split: only `ready_for_analysis` grants mutation; partial evidence grants discovery only; all other gates remain blocked.
- [x] Run the targeted intake test and verify it passes.

### Task 2: Add the first stored-classification discovery profile

**Files:**

- Modify: `app/technical_decision.py`
- Test: `tests/test_technical_decision.py`

- [x] Write a failing temporary-repository test for the DFHIS-32010 wording and synthetic frontend, BFF, service, entity and repository evidence.
- [x] Run the targeted test and verify it fails because the old decision cannot infer a target field.
- [x] Add the `挂号收费` candidate repositories and the `上午/下午` stored-filter profile with the `shangXiaWWsBz` aliases.
- [x] Report the frontend/BFF/service endpoint chain, stored-field evidence, `全部/上午/下午` options, and the preserved `晚上` default behavior; keep `can_patch=false`.
- [x] Run the targeted decision test and verify it passes.

### Task 3: Regression and live read-only replay

**Files:**

- Test: `tests/test_technical_decision.py`
- Test: `../plugins/his-harness-core/skills/harness-workitem-intake/tests/test_intake.py`

- [x] Run both complete focused test modules: 12 technical-decision tests and 17 intake tests passed; 13 governance integration tests also passed when their database was redirected to a temporary writable path.
- [x] Run the technical decision against the local DFHIS RC cache: it found `DO_MZ_GuaHao.shangXiaWWsBz`, the `getGuaHaoPageList` frontend/BFF/service chain, and the `全部/上午/下午` behavior. Remote freshness is not implied because the RC remote fetch was unavailable during this run.
- [x] Inspect the final changed source and document the remaining boundary: this increment discovers and plans; it does not modify HIS code, generate a patch, write Yunxiao, or perform Git delivery.
