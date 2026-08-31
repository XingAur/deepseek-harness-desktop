# HIS Harness v0.61 Retention Governance Implementation Plan

## Tasks

### 1. Retention plan

- [x] Add deterministic preview for keep-days and keep-recent-runs union semantics.
- [x] Protect task, change, audit, running and invalid-timestamp runs.

### 2. Archive and prune

- [x] Require exact plan hash confirmation and reject drift.
- [x] Create verified full backup before deletion, delete dependent rows transactionally and compact the primary DB.
- [x] Reuse v0.59 restore for recovery drill.

### 3. CLI and verification

- [x] Add preview/apply commands without automatic scheduling.
- [x] Test wrong confirmation, successful prune, protected rows, drift rejection and restore.
- [x] Run full isolated regression.
