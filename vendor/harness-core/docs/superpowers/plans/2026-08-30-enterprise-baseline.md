# Enterprise Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish one reproducible, versioned, evidence-producing quality gate for the Harness before expanding real Agent, visual-host, remote-delivery, or team-runtime capabilities.

**Architecture:** Keep the existing offline enterprise-core gate as the first release gate, but make its interpreter, version, test classes, and release metadata explicit and consistent. Separate deterministic unit/self-check/replay checks from socket, real-host, external-provider, and business-acceptance checks; no stage may promote a technical result into business or production validity.

**Tech Stack:** Python 3, existing `.venv`, `unittest`, existing `tools/enterprise_gate.py`, GitHub Actions, JSON/Markdown evidence.

## Global Constraints

- No cloud, Yunxiao, GitLab, Git remote, production database, deployment, or external write is performed.
- Existing user data under `data/` is preserved; no reset, cleanup, migration, or deletion is performed.
- The default external-write policy remains disabled.
- Technical, runtime, business, and promotion statuses remain independent.
- The first increment does not change Agent behavior, provider contracts, or business rules.
- A failed or unavailable test stage is recorded as failed/not_run/tool_missing; it is never reported as passed.

---

### Task 1: Create a single version source and release metadata contract

**Files:**
- Create: `/Users/lym/WorkCode/ai/Harness/VERSION`
- Create: `/Users/lym/WorkCode/ai/Harness/app/version.py`
- Modify: `/Users/lym/WorkCode/ai/Harness/app/core_status.py`
- Modify: `/Users/lym/WorkCode/ai/Harness/tools/build_release_bundle.py`
- Test: `/Users/lym/WorkCode/ai/Harness/tests/test_version_contract.py`

**Interfaces:**
- `app.version.read_version() -> str` reads the UTF-8 `VERSION` file, strips whitespace, and rejects empty or non-semver-like values.
- `app.version.VERSION -> str` is the loaded version used by Core and release tooling.
- `build_release_bundle.py` defaults to `app.version.VERSION`; an explicitly requested historical version remains allowed and the manifest records both `version` and `source_version`.

- [x] **Step 1: Write the failing tests**

Add tests that assert `VERSION`, `app.version.VERSION`, and `app.core_status.CORE_VERSION` use the same value, and that a release manifest records the current source version while preserving an explicitly requested historical bundle version.

- [x] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_version_contract -v
```

Expected: FAIL because the version source and release contract do not yet exist.

- [x] **Step 3: Implement the minimal version contract**

Use one plain-text value in `VERSION`, load it from `app/version.py`, import it in `app/core_status.py`, and add `source_version` to release metadata. Make the CLI default to the loaded current version while preserving explicit historical bundle rebuilds.

- [x] **Step 4: Run the focused tests and verify they pass**

Run the same command. Expected: PASS with all version-contract cases green.

### Task 2: Add one supported test entrypoint with explicit interpreter and test classes

**Files:**
- Create: `/Users/lym/WorkCode/ai/Harness/scripts/verify.sh`
- Create: `/Users/lym/WorkCode/ai/Harness/tests/test_verify_entrypoint.py`
- Modify: `/Users/lym/WorkCode/ai/Harness/README.md`

**Interfaces:**
- `scripts/verify.sh unit` runs the full unit suite with `.venv/bin/python` and `PYTHONDONTWRITEBYTECODE=1`.
- `scripts/verify.sh offline` runs compile, unit, self-check, replay, and secret stages through `tools/enterprise_gate.py` using the same interpreter.
- `scripts/verify.sh manager-static` runs Manager tests that do not require a listening socket and requires `HIS_HARNESS_ROOT` to be set explicitly.
- Unsupported or unavailable stages exit non-zero and print the exact stage status.

- [x] **Step 1: Write the failing tests**

Add tests that inspect the entrypoint text and assert it resolves the project `.venv/bin/python`, sets `PYTHONDONTWRITEBYTECODE=1`, rejects unknown subcommands, and does not use system `python3` for the quality gate.

- [x] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_verify_entrypoint -v
```

Expected: FAIL because `scripts/verify.sh` does not yet exist.

- [x] **Step 3: Implement the entrypoint**

The script must resolve its own project root, require an executable `.venv/bin/python`, use fixed argv arrays for the Python commands, and map `offline`, `unit`, and `manager-static` to the documented commands without mutating data.

- [x] **Step 4: Run focused and smoke verification**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_verify_entrypoint -v
./scripts/verify.sh unit
```

Expected: the entrypoint tests pass; the unit command reports the current complete suite result without substituting system-Python dependency errors.

### Task 3: Make the CI gate consume the single version and report test boundaries

**Files:**
- Modify: `/Users/lym/WorkCode/ai/Harness/.github/workflows/enterprise-core.yml`
- Modify: `/Users/lym/WorkCode/ai/Harness/tools/enterprise_gate.py`
- Create: `/Users/lym/WorkCode/ai/Harness/tools/syntax_check.py`
- Modify: `/Users/lym/WorkCode/ai/Harness/app/enterprise_gate.py`
- Test: `/Users/lym/WorkCode/ai/Harness/tests/test_enterprise_gate.py`

**Interfaces:**
- CI must use the repository version source and must not contain a hard-coded historical release version.
- The gate result records `interpreter`, `python_version`, `version`, `stage_timeout_seconds`, and whether a stage ended as `failed`, `timeout`, `not_run`, or `passed`.
- A timeout is a failed technical gate and cannot produce `technical_valid=true`.

- [x] **Step 1: Write the failing tests**

Add tests asserting that the workflow has no `--version 0.64.0` literal, the gate result contains interpreter/version metadata, and a timeout stage produces `status=failed` with an explicit timeout reason.

- [x] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_enterprise_gate -v
```

Expected: FAIL on the historical hard-coded version and missing result metadata.

- [x] **Step 3: Implement the smallest gate/reporting changes**

Read the version through `app.version`, pass it to the release bundle command from CI, replace write-producing `compileall` with the no-write `tools/syntax_check.py`, and add bounded metadata to the JSON result. Preserve all existing offline-only flags and the current false business/runtime/promotion statuses.

- [x] **Step 4: Run focused gate verification**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_enterprise_gate -v
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/enterprise_gate.py --stages compile,secret --output-dir /private/tmp/harness-baseline-gate
```

Expected: both tests and the two-stage gate pass, with version and interpreter metadata in the result.

### Task 4: Reconcile documentation and record the baseline truth

**Files:**
- Modify: `/Users/lym/WorkCode/ai/Harness/README.md`
- Modify: `/Users/lym/WorkCode/ai/Harness/HANDOFF.md`
- Modify: `/Users/lym/WorkCode/ai/Harness/CHANGELOG.md`
- Test: `/Users/lym/WorkCode/ai/Harness/tests/test_documentation_baseline.py`

**Interfaces:**
- Documentation names the single current version and distinguishes offline technical evidence, runtime verification, business acceptance, and promotion readiness.
- Documentation records the current full-gate baseline as failed/timeout until the complete `.venv` suite is green.
- Historical plans remain historical and are not presented as current completed capability.

- [x] **Step 1: Write the failing documentation assertions**

Add checks that current documentation references the version source, does not claim `0.64.0` as the current release, and includes the current gate boundary.

- [x] **Step 2: Run the focused assertions and verify they fail**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_documentation_baseline -v
```

- [x] **Step 3: Update the documented baseline**

Update only version/status statements, command entrypoints, and current acceptance boundaries. Do not rewrite historical implementation notes or claim real-model, remote, or business acceptance.

- [x] **Step 4: Run the complete first-increment verification**

Run:

```bash
./scripts/verify.sh offline
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_version_contract tests.test_verify_entrypoint tests.test_enterprise_gate tests.test_documentation_baseline -v
```

Expected: the focused contract tests pass; offline gate either passes completely or reports a precise failed/timeout stage with no false promotion status.

## Self-review checklist

- Version source is singular and every consumer is tested.
- No command relies on system Python implicitly.
- Socket-dependent tests are not silently mixed into deterministic unit tests.
- Gate timeouts are failures, not successes.
- No external calls, writes, database mutations, resets, or cleanup occur.
- Documentation reports current evidence rather than historical claims.
