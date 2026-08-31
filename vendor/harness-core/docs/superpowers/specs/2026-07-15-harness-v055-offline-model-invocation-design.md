# Harness v0.55 Offline Model Invocation Design

## Goal

Add one provider-neutral, structured model invocation boundary that can be exercised end to end without credentials or network access. v0.55 proves only a single synthetic fixture node in `mock` and `replay` modes.

## Scope

- Build an immutable request envelope from a v0.52 controlled node context.
- Allow only `mock` and `replay` adapters.
- Validate a provider-neutral response envelope and structured contract output.
- Record request, response, usage, candidate hash, status, error and ordered audit events in SQLite.
- Optionally record a deterministic mock response as a cassette, then replay it only when the request hash matches.
- Export JSON and Markdown evidence through Task Manager and mock self-check.

## Safety Boundary

- The runtime must not call `get_llm_client`, load environment credentials, read the Harness credentials file, or open the network.
- Cassette roots must carry the existing fixture marker and must not be inside a Git repository.
- `openai`, `anthropic`, `real` and every unknown mode are rejected before invocation preparation.
- Requests, responses and cassettes containing credential-shaped fields are blocked.
- Results are fixture-only candidates with `business_valid=false` and `promotion_enabled=false`.
- The dry-run scheduler and current contract registry are not advanced or promoted.
- Normal HIS requirement workflows do not import or call this runtime.

## Contract

The request binds the schedule checkpoint, controlled context hash, node, role policy, upstream artifact references, output contract and token/timeout limits. Its canonical SHA-256 hash is the replay identity.

The response is provider-neutral and contains:

- schema version;
- provider and model labels;
- structured output;
- input/output token usage;
- finish reason.

The structured output must match the context's output contract and producer role, retain upstream evidence references, contain non-empty structured content, and explicitly remain fixture-only and not business-valid.

## Persistence and Idempotency

`harness_model_invocations` stores one invocation per context, mode, request and cassette digest. `harness_model_invocation_events` stores ordered lifecycle evidence. Repeating an identical call returns the same invocation without creating duplicate events.

## Deferred

- Real provider adapters, credential selection and network access.
- Multi-node model-backed DAG execution.
- Business source tools, worktrees, PostgreSQL, Git and external writes.
- Candidate promotion or automatic scheduler progression.
