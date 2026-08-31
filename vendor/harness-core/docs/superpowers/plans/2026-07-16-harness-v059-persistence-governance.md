# Harness v0.59 Persistence Governance Implementation Plan

## Constraints

- Use isolated temporary SQLite files for all tests.
- Do not open or mutate the existing persistent Harness database.
- Do not add retention deletion until verified backup and restore exist.

## Tasks

### 1. Connection and schema versioning

- [x] Add failing tests for foreign keys, busy timeout, WAL and monotonic `user_version`.
- [x] Add a migration ledger and reject future schema versions.
- [x] Preserve all existing schema creation and compatibility columns.

### 2. Backup and restore

- [x] Add failing tests for consistent backup, SHA-256, integrity check and exact restore confirmation.
- [x] Back up a pre-existing non-empty database before its first v0.59 migration.
- [x] Add explicit local-only status/backup/restore CLI commands.

### 3. Verification

- [x] Run persistence-focused tests.
- [x] Run all unit tests with an isolated database.
- [x] Run mock self-check with isolated storage.
- [x] Document exact results and remaining retention/replay gaps.
