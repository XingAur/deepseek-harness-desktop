---
name: requirement-delivery-flow
description: Controlled, evidence-driven workflow for carrying a complex requirement from intake to delivery. Use when a requirement involves external system integration, multi-module codebase investigation, black-box API reverse engineering, or needs structured verification before handoff. Provides gate-based phase control, parallel sub-agent exploration strategy, Requirement Contract state machine, and honest evidence classification.
---

# Requirement Delivery Flow

Universal methodology for taking a complex requirement from intake to verified delivery. Not tied to any specific product, platform, or language. Use as a cross-cutting overlay on domain-specific skills (for example a requirement-archiver skill, a release-verification skill, or any workflow skill that owns one concrete domain).

## When to Apply

- Requirement touches multiple modules, services, or repositories.
- External/third-party API integration with incomplete documentation.
- The codebase is large and you need to locate relevant code before editing.
- You must deliver not just code but structured evidence and status updates.
- Any requirement where "guessing" the architecture could cause rework.

For single-file bug fixes or trivial changes, skip this skill — it adds overhead without value.

## Phase 1: Intake — Evidence Archival

Goal: capture the requirement's ground truth before any analysis.

1. **Archive raw materials**: requirement text, attachments, linked documents, screenshots. Store under `{workspace}/{requirement-id}/`.
2. **Extract binary attachments**: PDFs, Word docs, images — extract text with `pdfplumber`/`python-docx`/vision tools. Never analyze a requirement whose key evidence is locked in a binary you haven't read.
3. **Identify the authoritative source**: if the requirement references a parent spec, contract, or external standard, trace to the original and read it fully.
4. **Do not start coding**. Do not even start planning. Until raw materials are archived and read.

## Phase 2: Analysis — Parallel Codebase Investigation

Goal: understand existing code that relates to this requirement, using sub-agents to parallelize.

### Parallel Exploration Strategy

Dispatch `explore` sub-agents for independent investigation paths. Each sub-agent prompt must be **extremely specific**:

- ❌ Bad: "Look at how the plugin system works"
- ✅ Good: "Search `packages/plugin-host` for: extension-point registration (`extensionPoint` enum), plugin SDK method signatures (`Host.plugin.*`), iframe sandbox setup. Report: (1) how extension points map to plugins, (2) what SDK methods are available, (3) the parameter passing mechanism."

Rules:
- Dispatch 2-4 sub-agents in parallel when investigation areas are independent.
- Each sub-agent should cover one repo, one subsystem, or one concern.
- Sub-agents are **explore-only** — they read and analyze, never modify files.
- Main agent synthesizes sub-agent reports into decisions, not pass-through.

### Analysis Output

Produce a structured analysis document covering:
- Current state: what already exists that can be reused.
- Gap analysis: what's missing that must be built.
- Risk profile: external dependencies, crypto/format compatibility, network reachability, multi-repo coordination.
- **Blocking questions**: 1-3 specific multiple-choice questions whose answers change implementation. Do not ask open-ended questions.

## Runtime-Evidence-First Diagnosis for UI/Rendering Defects

When the reported symptom is "page blank", "white screen", "component won't load", or similar rendering failure, static code analysis alone is **insufficient** to identify root cause. Follow this protocol before writing any fix:

1. **Capture runtime errors before diagnosing.** Ask the user to open browser DevTools Console and share all red errors. If the user cannot provide them, scaffold a local E2E that captures console output. Do not propose a fix based solely on code reading — common blank-screen root causes (circular imports, missing modules, registration failures) are visible only at runtime.
2. **Lock the reproduction environment.** Confirm with the user: (a) which repository/sub-application renders the page, (b) which branch/tag exhibits the bug, (c) the exact URL path. Do not assume — wrong branch or wrong repo wastes an entire fix cycle.
3. **After a failed fix, go back to evidence — do not guess again.** If a fix is deployed and the user reports the problem persists: re-capture console errors from the current state, verify the deployment actually contains the fix (read config/package hashes), then base the next diagnosis on new runtime evidence.
4. **No more than one hypothesis without fresh runtime evidence.** If you've made one fix based on a hypothesis and it failed, the next step must produce new runtime evidence, not a new guess. Multi-agent code review does not substitute for runtime evidence.

Measured impact: one real session spent 8 days and 6 failed fix rounds because the agent kept diagnosing from static analysis instead of requesting browser console errors. The actual root cause (circular import) was identifiable only from the runtime module loading error.

## Phase 3: Clarification — Blocking Decision Gate

Goal: resolve all architecture-level unknowns before writing code.

Use `AskUserQuestion` or equivalent structured flow:
- Present 1-3 questions, each with a recommended option and impact description.
- **Listen for user-proposed alternatives** — users often know a simpler path (e.g., "use a dynamic script instead of modifying Java code"). Evaluate seriously before pushing back.
- Record every decision in a **decision ledger** with rationale.
- Update the Requirement Contract status from `needs_clarification` → `ready_to_build`.

### Requirement Contract (State Machine)

Track requirement status with a YAML frontmatter block:

```yaml
status: needs_clarification | ready_to_build | blocked | ready_to_verify | delivered
intent: "One sentence: the user outcome this requirement achieves."
decisions:
  - id: D1
    decision: "Concrete architecture/implementation choice"
    source: "user | agent | code-evidence"
blocking_questions:
  - id: Q1
    question: "Specific decision question"
    options:
      - label: "Recommended option"
        impact: "What this choice means for implementation"
    why_blocking: "Why implementation diverges without this answer"
```

Rules:
- Never write code while status is `needs_clarification`.
- Any decision change that affects behavior, data, or workflow = new contract revision. Invalidate prior evidence.
- Keep the contract compact — details go in the analysis document below it.

## Phase 4: Implementation — Iterative Build

Goal: produce working code with incremental verification.

### Before Writing Code

1. Write an implementation plan (plan mode or markdown) listing every deliverable file, its purpose, and dependencies.
2. Verify key technical assumptions independently before committing to them:
   - Crypto/format compatibility: test with `curl` or a script before writing production code.
   - API reachability: probe the endpoint before assuming it works.
   - Parameter injection: if the target framework has quirks (e.g., form-encoded params auto-injected as variables), verify with a minimal test first.

### During Implementation

- Track progress with `TodoList` — one item per deliverable.
- Mark each item done only when the deliverable is verified, not when the code is written.
- If you hit a wall on one deliverable, don't block all progress — continue with independent deliverables and return to the blocker.

### Honest Scoping

- If a step requires an environment you don't have (production network, specific hardware, deployed release), **say so explicitly**. Mark it as "requires deployment-environment verification" rather than claiming success.
- Never dress up a failed test as a passed one.

## Phase 5: Delivery — Structured Evidence and Handoff

Goal: deliver complete evidence, not just code.

### Evidence Classification

Classify every piece of evidence into one of these types:

| Type | Description | Example |
|------|-------------|---------|
| `build` | Compilation/package success | `npm run build` exit 0, CI pipeline green |
| `runtime` | Endpoint/script executes correctly | API returns expected response |
| `screenshot` | Visual proof of behavior | UI shows correct data |
| `business` | Business semantics confirmed by domain expert | SME confirms field meaning |
| `database` | Read-only query confirms data state | `SELECT` returns expected rows |
| `integration` | External system interaction verified | Third-party API returns valid response |

Each evidence item must have: what was tested, what was the result, when it was captured. Mark stale evidence as `superseded` after any material code change.

### Delivery Closure

1. **Comment**: write a structured completion comment on the requirement ticket.
2. **Attachments**: upload deliverable files (code, SQL, configs) as attachments, not inline text.
3. **Status transition**: update the ticket status (e.g., → "ready for testing") and assign to the next owner.
4. **Reference data**: cache frequently-queried IDs (user IDs, org IDs, project IDs) in a `contacts.md` or similar file within the skill pack to avoid redundant API calls in future sessions.

## Black-Box System Integration Patterns

When integrating with a system that has poor or no documentation:

### 1. Source Code as Truth
- Official docs for forked/customized systems are often wrong. Read the actual source code (JS bundles, decompiled classes, source repos).
- Extract API paths from minified JS: `grep -oP '"/api/[^"]*"' bundle.js`

### 2. Full HTTP Capture
- Never assume how authentication works. Use `curl -v` or HTTP proxy to capture the complete request/response cycle.
- Tokens may come from response **headers**, not response body. Check both.
- Login flows may return dynamic tokens — don't try to compute them with formulas.

### 3. Incremental Verification
- Test each layer independently: crypto → signing → HTTP transport → business logic.
- If one layer fails, isolate it before blaming the whole chain.
- Keep test scripts — they become regression checks.

### 4. Framework Quirks
- Dynamic script engines (Groovy, MagicAPI, etc.) often inject parameters as variables, not via `request.body`.
- POST format matters: `application/json` vs `application/x-www-form-urlencoded` can change behavior entirely.
- Fork behavior: if a system is forked from open source, assume the fork changed things. Verify against the fork, not the original.

## Common Pitfalls

| Pitfall | Prevention |
|---------|-----------|
| Assuming auth token mechanism | Capture full HTTP exchange with `-v`; check headers AND body |
| Confusing ID systems (memberId vs userId) | Build a mapping table; verify which ID the target API expects before using it |
| Browser automation depending on OS permissions | Prefer API/curl solutions; treat browser automation as last resort |
| Claiming "tested" when environment can't reach target | Explicitly state "requires deployment-environment verification" |
| Asking open-ended clarification questions | Always provide specific multiple-choice options with recommended answer |
| Sub-agent prompts too vague | List exact search patterns, file paths, and specific questions |
| Ignoring user's proposed simpler alternative | Evaluate the user's suggestion seriously — they know the system |
| Single giant verification at the end | Verify incrementally at each technical milestone |
| Stale evidence after code change | Any material edit invalidates prior evidence; re-capture before delivery |
| Diagnosing UI blank/white-screen from static code only | Capture browser console errors before proposing any fix; static analysis generates hypotheses, not root causes |
| Fixing the wrong branch or wrong sub-application | Confirm exact branch + repo + URL path with the user before creating a worktree |
| Guessing a new root cause after a failed fix | Re-capture runtime evidence (console errors, deployed package check) before next hypothesis; no guessing loops |
| Treating "passed with non-blocking limitation: no runtime verification" as safe to release | When the limitation is missing runtime/UI verification, treat as blocking — do not push until the gap is closed |

## Delivery vs. Final Acceptance

Keep these separate:
- **Delivered**: code written, evidence captured, ticket transitioned to testing. The agent's job is done.
- **Accepted**: business confirms the requirement works in production after release. This happens later and may depend on deployment.

Do not block delivery waiting for final acceptance unless the user explicitly asks for production verification. Update the ticket status honestly and state "delivered, final acceptance pending release validation" when appropriate.
