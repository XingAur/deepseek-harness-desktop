# HIS Harness v0.60 Real Replay Suite Implementation Plan

## Constraints

- No real model, network, credentials, business PG, Yunxiao write, Git remote or business repository mutation.
- Replay inputs are desensitized and immutable.
- A passing replay must never claim business or runtime validity.

## Tasks

### 1. Fixed replay manifest

- [x] Add 10 cases covering the required category counts.
- [x] Require source, ownership, paths, diff features, commands, negative case and manual boundary.

### 2. Deterministic runner

- [x] Replay requirement calibration and four-layer ownership.
- [x] Execute ordering and high-risk negative gates.
- [x] Emit truthful JSON/Markdown status without persistence or external calls.

### 3. Verification

- [x] Add unit and CLI tests, including tampered-expectation failure.
- [x] Run full isolated unit suite and mock self-check.
- [x] Run the replay suite repeatedly and record deterministic hashes.
