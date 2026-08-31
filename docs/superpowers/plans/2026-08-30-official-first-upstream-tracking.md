# Official-First Upstream Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the official DeepSeek Harness repository the continuously tracked source baseline, keep the managed Runtime on an exact verifiable distribution, expose the complete official runtime closure, and isolate the immature HIS Harness behind an explicit experimental switch.

**Architecture:** `release/versions.json` records separate immutable source and npm Runtime coordinates. A scheduled workflow observes the official GitHub repository and npm registry, prepares a tested update branch, and opens a review pull request without merging or publishing. Runtime assembly derives its official dependency closure from the installed CLI manifest, while the desktop renders the official conversation directly and exposes HIS Harness only through a non-persistent experiment opt-in.

**Tech Stack:** Node.js 24, TypeScript 6, Vitest 4, React 18, GitHub Actions, Tauri 2, official `@deepseek-ai/dsh` packages.

## Global Constraints

- Official source repository is exactly `https://github.com/deepseek-ai/deepseek-harness.git`.
- Initial source coordinate is `dsh-v0.1.2-alpha.1` at `cd5ef8148158c3a752a658978873241fdf8e2bbc`.
- Managed Runtime remains on exact npm `0.1.1-rc.2` until a newer exact package is registry-verifiable.
- Scheduled automation may update only `automation/deepseek-harness-upstream` and its pull request; it must not merge, tag, publish, or write the default branch directly.
- Existing Profile, Workspace, session, Runtime fallback, credential, project, cache, and HIS Harness data must not be deleted, reset, migrated, or reinitialized.
- Existing uncommitted user changes must be preserved; no bulk formatting or unrelated refactoring is allowed.
- No local Git commit, push, tag, Release, deployment, or external-system write is authorized during implementation.

## File Structure

- `release/versions.json`: single machine-readable source for desktop, Runtime, toolchain, official npm coordinate, and official source provenance.
- `scripts/release-versions.mjs`: validates the exact source/distribution schema and consistency.
- `scripts/prepare-upstream-release.mjs`: resolves official Git tags and npm metadata, classifies source-only versus distributable upgrades, and performs atomic local preparation.
- `.github/workflows/upstream-watch.yml`: scheduled observer and tested upgrade-PR producer.
- `.github/workflows/upstream-sync.yml`: manual-only release recovery/publishing workflow after an upgrade PR is reviewed and merged.
- `scripts/runtime-capabilities.mjs`: derives and validates the full official direct runtime closure plus feature ownership.
- `packages/dsh-plugin-desktop/src/client/AdvancedFrame.tsx`: renders the official conversation without an HIS wrapper.
- `packages/dsh-plugin-desktop/src/client/model-agent/ModelAgentCenter.tsx`: contains the explicit, session-local HIS experiment switch.
- `THIRD_PARTY_NOTICES.md` and `docs/upstream-policy.md`: human-readable license, provenance, and upgrade policy.

---

### Task 1: Separate Official Source and Runtime Distribution Coordinates

**Files:**
- Modify: `release/versions.json`
- Modify: `scripts/release-versions.mjs`
- Modify: `scripts/release-versions.test.ts`

**Interfaces:**
- Consumes: current release version object and existing consistency checks.
- Produces: `dshUpstream: { repository: string; tag: string; commit: string }` in schema version 2.

- [ ] **Step 1: Write the failing schema tests**

Add `dshUpstream` to the accepted fixture and add rejection cases for a non-official repository, mutable tag, and non-40-character commit. Require `schemaVersion: 2`.

```ts
dshUpstream: {
  repository: 'https://github.com/deepseek-ai/deepseek-harness.git',
  tag: 'dsh-v0.1.2-alpha.1',
  commit: 'cd5ef8148158c3a752a658978873241fdf8e2bbc',
}
```

- [ ] **Step 2: Verify RED**

Run: `npm test -- scripts/release-versions.test.ts`

Expected: FAIL because schema version 2 and `dshUpstream` are not accepted.

- [ ] **Step 3: Implement the exact provenance schema**

Validate the repository constant, `dsh-v<SemVer>` tag, lowercase 40-character commit, and exact nested keys. Return a cloned nested object so callers cannot mutate the validated value through shared references.

- [ ] **Step 4: Update the tracked source coordinate**

Set `release/versions.json` to schema version 2 with the exact initial source coordinate. Keep `dshVersion` at `0.1.1-rc.2`.

- [ ] **Step 5: Verify GREEN**

Run: `npm test -- scripts/release-versions.test.ts`

Expected: all release-version tests pass.

### Task 2: Observe GitHub Source and Prepare Reviewable Upgrades

**Files:**
- Modify: `scripts/prepare-upstream-release.mjs`
- Modify: `scripts/prepare-upstream-release.test.ts`
- Create: `.github/workflows/upstream-watch.yml`
- Modify: `.github/workflows/upstream-sync.yml`
- Modify: `scripts/workflow-contract.test.ts`

**Interfaces:**
- Consumes: `git ls-remote --tags` output and official npm latest JSON.
- Produces: `fetchLatestDshSource()`, `parseDshTagRefs(output)`, and preparation results with `action: 'noop' | 'source-update' | 'upgrade'`.

- [ ] **Step 1: Write failing source-observation tests**

Cover lightweight and annotated tags, SemVer ordering (`rc.10` after `rc.2`), malformed refs, a source-only update where npm is unchanged, and a distributable upgrade where npm advances. Assert that source-only preparation changes only `release/versions.json` semantically and does not bump desktop or Runtime versions.

```ts
expect(parseDshTagRefs([
  'cd5ef8148158c3a752a658978873241fdf8e2bbc\trefs/tags/dsh-v0.1.2-alpha.1',
].join('\n'))).toEqual({
  tag: 'dsh-v0.1.2-alpha.1',
  version: '0.1.2-alpha.1',
  commit: 'cd5ef8148158c3a752a658978873241fdf8e2bbc',
})
```

- [ ] **Step 2: Verify RED**

Run: `npm test -- scripts/prepare-upstream-release.test.ts`

Expected: FAIL because source parsing and source-only actions do not exist.

- [ ] **Step 3: Implement bounded official-source resolution**

Use `git ls-remote --tags https://github.com/deepseek-ai/deepseek-harness.git 'refs/tags/dsh-v*'`. Parse only 40-character object IDs and exact `dsh-v<SemVer>` refs, prefer peeled annotated-tag commits, and never interpolate a repository supplied by network data. Keep the existing 10-second npm timeout and bounded errors.

- [ ] **Step 4: Implement atomic source-only and distribution upgrades**

For `source-update`, replace only `dshUpstream` and keep desktop, Runtime, and npm versions unchanged. For `upgrade`, update source provenance, bump desktop/Runtime patches, and change the exact npm version. Preserve the existing restore-on-failure behavior.

- [ ] **Step 5: Write the scheduled PR workflow contract test**

Require a four-hour schedule, read-only default permissions, a write-scoped PR job, branch `automation/deepseek-harness-upstream`, targeted/full checks before push, `gh pr create` or `gh pr edit`, and absence of auto-merge, tag creation, Release publication, or direct default-branch pushes. Require the existing release workflow to be `workflow_dispatch` only.

- [ ] **Step 6: Verify workflow RED**

Run: `npm test -- scripts/workflow-contract.test.ts`

Expected: FAIL because the watch workflow does not exist and the release workflow still has a schedule.

- [ ] **Step 7: Implement the scheduled watcher and manual release boundary**

The watcher checks out the default branch, installs exact dependencies, runs upstream preparation, exits cleanly for `noop`, runs `npm run release:versions:check` and `npm run check`, commits only the allowlisted prepared files to the dedicated branch, and creates or refreshes one PR. Change `upstream-sync.yml` to manual dispatch only; retain its recovery and publication logic for explicit post-merge execution.

- [ ] **Step 8: Verify GREEN**

Run: `npm test -- scripts/prepare-upstream-release.test.ts scripts/workflow-contract.test.ts`

Expected: both suites pass.

### Task 3: Prove the Complete Official Runtime Dependency Closure

**Files:**
- Modify: `scripts/runtime-capabilities.mjs`
- Modify: `scripts/runtime-capabilities.test.ts`
- Modify: `scripts/runtime-build-capabilities.mjs`

**Interfaces:**
- Consumes: installed `@deepseek-ai/dsh/package.json` and its direct `dependencies` map.
- Produces: capability report schema 2 with `officialClosure: { digest: string; packages: ClosureRecord[] }` and `featureGroups`.

- [ ] **Step 1: Write failing closure tests**

Add an official CLI fixture with direct official and third-party dependencies. Assert every direct dependency is reported, all official packages equal `dshVersion`, missing and path-escaping dependencies fail, the digest is stable regardless of manifest key order, and feature groups include plan/goal, jobs/scheduling, Skill, MCP, subagent, workflow, approval/questions, filesystem/shell, Web tools, hooks/webhooks, sessions/settings, providers, and official Web UI.

- [ ] **Step 2: Verify RED**

Run: `npm test -- scripts/runtime-capabilities.test.ts`

Expected: FAIL because schema 1 has no full closure, digest, or feature groups.

- [ ] **Step 3: Implement manifest-derived closure inspection**

Read the already path-validated CLI manifest, sort all direct dependencies by package name, locate each inside the staged Runtime, record bounded package metadata, require official package versions to equal `dshVersion`, and reject missing, malformed, symlinked, or escaped paths. Hash canonical sorted records with SHA-256.

- [ ] **Step 4: Implement feature ownership diagnostics**

Build availability from dependency ownership predicates instead of a three-item optional list. Keep base/Web/desktop bundle ordering exact and keep provider connectivity separate from package availability.

- [ ] **Step 5: Verify GREEN**

Run: `npm test -- scripts/runtime-capabilities.test.ts scripts/runtime-build-capabilities.test.ts`

Expected: closure and assembled-Runtime tests pass.

### Task 4: Make Official Conversation Default and HIS Harness Explicitly Experimental

**Files:**
- Modify: `packages/dsh-plugin-desktop/src/client/AdvancedFrame.tsx`
- Modify: `packages/dsh-plugin-desktop/src/client/model-agent/ModelAgentCenter.tsx`
- Modify: `packages/dsh-plugin-desktop/tests/advanced-frame.spec.tsx`
- Modify: `packages/dsh-plugin-desktop/tests/model-agent-center.spec.tsx`

**Interfaces:**
- Consumes: official `conversation` slot and existing `HarnessTaskPanel`.
- Produces: direct official conversation rendering plus a session-local `HIS Harness（实验）` opt-in.

- [ ] **Step 1: Write the failing default-conversation test**

Assert a normal `AdvancedFrame` renders the official conversation node and does not render a “开始 Harness 任务” button or `Harness 任务` region.

- [ ] **Step 2: Write the failing experiment opt-in test**

Assert the model/agent center initially shows a warning but no Harness task panel, then renders `HarnessTaskPanel` only after the user clicks `启用本次会话的 HIS Harness（实验）`; navigating away or remounting resets the switch.

- [ ] **Step 3: Verify RED**

Run: `npm run plugin:test -- --run tests/advanced-frame.spec.tsx tests/model-agent-center.spec.tsx`

Expected: FAIL because the main conversation is wrapped and no experiment tab exists.

- [ ] **Step 4: Restore official workbench ownership**

Remove the `HarnessChatSurface` import and render `renderSlot('conversation', {})` directly when desktop overlay pages are closed. Do not delete the retained experimental component or its data paths.

- [ ] **Step 5: Add the non-persistent maturity gate**

Add an `实验功能` tab with explicit copy that the Python HIS Harness has not passed complete business/runtime validation and grants no external-write authority. Keep `enabled` in React component state only; render `HarnessTaskPanel` after opt-in.

- [ ] **Step 6: Verify GREEN**

Run: `npm run plugin:test -- --run tests/advanced-frame.spec.tsx tests/model-agent-center.spec.tsx tests/harness-task-panel.spec.tsx`

Expected: all targeted UI tests pass.

### Task 5: Record Upstream, License, and Operational Boundaries

**Files:**
- Create: `THIRD_PARTY_NOTICES.md`
- Create: `docs/upstream-policy.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: source and Runtime coordinates from `release/versions.json`.
- Produces: human-readable provenance and upgrade operations without relicensing user-owned code.

- [ ] **Step 1: Add a failing documentation contract**

Extend `scripts/product-copy.test.ts` to require the official repository, source/distribution split, scheduled PR branch, manual merge/release boundary, MIT attribution, and statement that the desktop repository's own license is not changed.

- [ ] **Step 2: Verify RED**

Run: `npm test -- scripts/product-copy.test.ts`

Expected: FAIL because the new policy and notice files are missing.

- [ ] **Step 3: Write exact policy and notice documents**

Document inspection commands, source-ahead/distribution-pending semantics, review gates, rollback/data preservation, and recovery when the scheduled workflow fails. Attribute official DeepSeek Harness under MIT and link its `THIRD_PARTY_NOTICES.md`; do not add a root project LICENSE.

- [ ] **Step 4: Update README ownership language**

State that the official workbench owns ordinary agent capabilities, the desktop owns native integration, and HIS Harness is an opt-in experiment.

- [ ] **Step 5: Verify GREEN**

Run: `npm test -- scripts/product-copy.test.ts`

Expected: documentation contracts pass.

### Task 6: Integrated Verification and Diff Audit

**Files:**
- Verify only; no new production files.

**Interfaces:**
- Consumes: all preceding tasks.
- Produces: fresh test/build evidence and a protected final diff inventory.

- [ ] **Step 1: Run focused tests**

Run: `npm test -- scripts/release-versions.test.ts scripts/prepare-upstream-release.test.ts scripts/runtime-capabilities.test.ts scripts/runtime-build-capabilities.test.ts scripts/product-copy.test.ts`

Run: `npm run plugin:test -- --run tests/advanced-frame.spec.tsx tests/model-agent-center.spec.tsx tests/harness-task-panel.spec.tsx`

Expected: all focused tests pass.

- [ ] **Step 2: Run full JavaScript gates**

Run: `npm run check`

Expected: agent build, root tests, agent tests, plugin tests, Web build, and plugin build all pass. Loopback tests may require the approved sandbox-external `npm test` execution.

- [ ] **Step 3: Run Rust tests**

Run: `cargo test --manifest-path src-tauri/Cargo.toml --locked`

Expected: all host/runtime activation tests pass, or a pre-existing platform/toolchain blocker is reported separately.

- [ ] **Step 4: Audit source and user-owned diffs**

Run: `git diff --check`

Run: `git status --short`

Run: `git diff -- release/versions.json scripts/release-versions.mjs scripts/prepare-upstream-release.mjs scripts/runtime-capabilities.mjs .github/workflows/upstream-watch.yml .github/workflows/upstream-sync.yml packages/dsh-plugin-desktop/src/client/AdvancedFrame.tsx packages/dsh-plugin-desktop/src/client/model-agent/ModelAgentCenter.tsx README.md THIRD_PARTY_NOTICES.md docs/upstream-policy.md`

Expected: no whitespace errors, no deletion/reset of user data, and no unrelated formatting changes.

- [ ] **Step 5: Handoff without external mutation**

Report completed behavior, exact verification commands/results, existing unrelated dirty files left untouched, source-versus-Runtime lag status, and remaining runtime/production uncertainty. Do not commit, push, tag, create a real PR, publish, or deploy.
