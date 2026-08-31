# Harness v0.56 Offline Model DAG Design

## Goal

Connect the v0.55 provider-neutral offline model adapter to the v0.54 multi-wave dynamic DAG so role nodes can produce and hand off structured model fixture candidates under bounded parallel execution.

## Runtime

`OfflineModelDagRuntime` owns one persistent run per dry-run schedule. For each current wave it:

1. resolves a strict per-node adapter policy;
2. invokes v0.55 in `mock` or `replay` mode;
3. waits for the complete wave;
4. persists node traces;
5. advances only simulated scheduler state;
6. stops after any failed node without automatic retry.

Downstream context resolution may consume the latest successful `fixture_model_candidate` by artifact id, schema and content hash. It does not promote that candidate to a current registry contract.

## Adapter Policy

The optional adapter file is fixture-only JSON:

```json
{
  "schema_version": "1.0-offline-model-dag-adapters",
  "default": {"mode": "mock", "record_cassette": false},
  "nodes": {
    "requirement_analysis": {"mode": "replay", "cassette_file": "model-cassettes/example.json"}
  }
}
```

Only known node ids, `mock/replay`, booleans and root-relative cassette paths are accepted. Real providers, credentials, network configuration, arbitrary commands and environment injection are invalid.

## Persistence

- `harness_model_dag_runs`: schedule-level status, concurrency and summary.
- `harness_model_dag_traces`: wave, context, invocation, adapter, usage, timing, candidate and concurrency evidence.

## Safety Boundary

- All model calls delegate to the v0.55 offline runtime.
- All outputs remain `fixture_only=true`, `business_valid=false`, `promotion_enabled=false`.
- No HIS source tool, worktree, PostgreSQL, Git, browser or external system is called.
- The normal HIS requirement workflow does not import this module.
- Real provider integration remains a separately authorized future phase.
