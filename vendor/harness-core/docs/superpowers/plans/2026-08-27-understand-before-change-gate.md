# Understand Before Change Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent every mutation-capable Harness route from changing code until business intent, usage scenario, project context, call-chain evidence, impact boundaries, and verification baseline are proven.

**Architecture:** Add an independent, versioned `requirement-understanding.v1` evaluator that consumes existing calibration, technical decision, ownership, acceptance, and evidence artifacts. Persist it before governance/execution and use it as an unconditional mutation gate while retaining existing governance schemas and capability contracts.

**Tech Stack:** Python 3 standard library, existing Harness SQLite artifact store, unittest.

## Global Constraints

- This is a local, read-only-before-change gate; it must not introduce Yunxiao writes, database writes, Git delivery, or model-inferred business facts.
- Preserve the existing `requirement-governance.v1` eight-check schema and capability route compatibility.
- A blocked result must state what to investigate next and must remain useful for readonly analysis.
- `auto-local` may not skip project-context evidence when it would otherwise lead to a mutation-capable run.

## Task 1: Define and test the pure understanding evaluator

**Files:**
- Create: `app/requirement_understanding.py`
- Create: `tests/test_requirement_understanding.py`

- [x] Add failing unit tests for a semantically calibrated requirement that lacks project/call-chain evidence; assert `can_modify` is false and the required discovery blockers are named.
- [x] Add failing unit tests for missing background/scenario/goal-boundary evidence; assert the result blocks without inventing facts.
- [x] Add a ready fixture with source evidence, selected project, entry/call-chain evidence, proven allowed path, ownership, verification and manual acceptance; assert `ready_for_change`.
- [x] Implement an immutable result/check model with JSON and Markdown renderers, stable blockers, evidence refs, and explicit next readonly actions.
- [x] Run `python3 -m unittest tests.test_requirement_understanding` and confirm all cases pass.

## Task 2: Integrate the gate before all mutation routes

**Files:**
- Modify: `app/harness.py`
- Modify: `tests/test_requirement_governance_integration.py`

- [x] Add a dedicated `understanding` stage after technical decision, ownership, evidence collection and acceptance construction.
- [x] Write failing integration coverage that verifies a blocked understanding result prevents `_run_worktree_execution` and core-closure execution even when `requirement_governance="observe"`.
- [x] Make every mutation-capable execution mode stop with an explicit understanding-gate reason before any local executor is called; readonly continues as analysis-only.
- [x] Remove the `auto-local` project-context-scan bypass for mutation-capable runs and cover it with a regression test.
- [x] Persist `requirement_understanding_json` and `requirement_understanding_markdown` with the run, including on blocked runs.
- [x] Run the focused governance integration suite and confirm legacy readonly behavior remains compatible.

## Task 3: Surface the evidence in reports and archived work-item runs

**Files:**
- Modify: `app/harness.py`
- Modify: `tests/test_harness_artifact_compaction.py`
- Modify: `README.md`
- Modify: `docs/role-capability-skill-matrix.md`

- [x] Add the two artifacts to output allowlists, stable file-name mapping, and the main Markdown report.
- [x] Add an artifact-output regression test for both files.
- [x] Document the mandatory ordering: Yunxiao evidence -> business understanding -> project/call-chain discovery -> scope/verification -> governed change.
- [x] State that the gate is a Harness orchestration responsibility; no new Agent, MCP, or skill is required.

## Task 4: Validate and self-review

**Files:**
- Verify: `tests/test_requirement_understanding.py`
- Verify: `tests/test_requirement_governance.py`
- Verify: `tests/test_requirement_governance_integration.py`
- Verify: `tests/test_harness_artifact_compaction.py`
- Verify: `tests/test_requirement_calibration.py`
- Verify: `tests/test_technical_decision.py`

- [x] Run the focused test command above.
- [x] Inspect the diff for accidental governance-schema, Yunxiao-write, or execution-boundary changes.
- [x] Report completed behavior, exact validation, remaining runtime boundaries, and one concrete acceptance scenario using a real Yunxiao work item without performing a remote write.
