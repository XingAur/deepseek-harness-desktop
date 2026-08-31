# Visual Evidence First Gate Implementation Plan

> **For agentic workers:** execute this plan test-first. A host may not claim image support until an integration test proves it returns structured facts from an archived local image.

**Goal:** Prevent a screenshot-bearing high-risk HIS task from entering technical discovery or code mutation until an explicitly capable local host has extracted the screenshot's visible facts.

**Architecture:** Yunxiao retrieval and visual interpretation remain separate. Harness Core stores the local archived image and owns the fail-closed gate; a host visual adapter receives only bounded title/description plus the local image path through `his-visual-evidence.v1`, then returns evidence facts. The Core never selects a model, launches Codex, or uploads an image implicitly.

## Global Constraints

- Required facts: `error_text`, `menu`, `action`, `business_scene`; `target_module` is optional.
- Never infer visual facts from ticket background, comments, filename, or code search.
- Never send Yunxiao/HIS screenshots to an external provider without explicit, scoped authorization.
- A missing, malformed, or unavailable visual adapter keeps the task blocked before project selection and code modification.

### Task 1: Core visual evidence contract and gate

**Files:** `app/visual_evidence_protocol.py`, `app/visual_evidence.py`, `tests/test_visual_evidence_protocol.py`, `tests/test_visual_evidence.py`

- [x] Define the portable `his-visual-evidence.v1` request/response contract.
- [x] Bind every response fact to one archived local image and reject incomplete or malformed facts.
- [x] Add `VisualEvidenceHostSession` and `HostVisualEvidenceAnalyzer`; failure leaves the visual gate closed.

### Task 2: Role → capability → Skill registration

**Files:** `skills/harness-visual-evidence/SKILL.md`, `config/role_capability_skill_matrix.json`, `app/dynamic_planning.py`, `tests/test_role_capability_skill_registry.py`

- [x] Register `visual.extract` as Harness-internal, L0, non-external-executable.
- [x] Give it only to `product_analyst`, before technical search or a mutation-capable role can run.

### Task 3: Concrete host implementations

- [ ] Codex App: implement `his-visual-evidence.v1` only after the available Desktop/App-Server image-input API is verified in the installed runtime.
- [ ] Codex CLI: implement the same contract only if the selected CLI/runtime accepts local image input; otherwise report `visual_evidence_adapter_unavailable`.
- [ ] DeepSeek-Harness-Desktop: implement the same contract through its own host bridge and prove it passes the archived image as an image input, not merely a text path.
- [ ] Terminal: use a user-selected local visual engine or an explicitly authorized remote visual provider; terminal must otherwise remain blocked.

**Acceptance for each host:** replay the archived DFHIS-32190 screenshot and prove it returns the screenshot-visible error, menu, action and business scene before any project selection happens.

## Verified state

The Core protocol, evidence gate and role route are implemented and covered by local tests. No current default host is declared visually capable yet. That is intentional: declaring an adapter before it actually reads an image would recreate the original false-analysis problem.
