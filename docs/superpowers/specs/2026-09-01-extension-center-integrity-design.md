# Extension Center Integrity Design

## Goal

Repair the extension-center data-integrity and release-path issues discovered on `main`, then publish the changes as desktop `0.1.42` with Runtime `0.1.19-preview`.

## Scope

This work changes four bounded areas: MCP projection ownership, Skill installation transactions, usage-stat scanning and failure redaction, and release-version consistency. It does not add a new MCP product surface, a Skill marketplace, or cloud usage reporting.

## MCP ownership and reconciliation

The SQLite MCP store will retain a per-target projection record after a successful write. A record contains the MCP definition identity, target, server name, and the canonical payload fingerprint last materialized into that target's config.

During sync, the service compares the selected definition set with the projection records for that target. It may create or update an entry selected for the target. It may remove an entry only when the projection record proves this application owns it and the current target payload still matches the stored fingerprint. A missing selection removes its owned projection. A changed external payload is retained and reported as a conflict rather than overwritten or deleted.

Deleting an MCP definition follows the same rule: only owned, matching projections are removed. An identically named entry in another target, or an externally modified owned entry, is never removed silently. The UI refreshes projection state after edits so removing a target is propagated when the user presses sync.

## Skill installation transaction

Installation validates every archive entry, target root, and duplicate skill name before changing a target. Each skill is extracted and copied into a target-local staging directory. Commit replaces destinations with same-filesystem renames and stores prior destinations in an operation-local backup directory.

If any commit fails, the service removes newly installed destinations and restores each prior destination already replaced. Successful completion deletes staging and transaction backups. This compensating transaction spans all selected targets: the result is either all requested skills installed at every selected target, or the prior filesystem state is restored.

## Usage-stat resource and privacy bounds

Directory discovery and JSONL parsing run in `spawn_blocking`, keeping the Tauri async command responsive. The service applies explicit per-file, total-byte, file-count, line-size, and aggregate-model limits. Input outside a limit is skipped with a short reason.

Failure data crossing the bridge contains no absolute path. The backend returns a stable category and a root-relative label where safe; otherwise it uses a generic session-file label. The panel displays only this sanitized value.

## Release and verification

`release/versions.json` advances the desktop version to `0.1.42` and Runtime version to `0.1.19-preview`. Version checks must validate the embedded desktop-plugin release identity in the Runtime archive/manifest before a desktop release is tagged. A new tag is required because GitHub release assets are immutable: `desktop-v0.1.41` must not be rebuilt or overwritten.

## Acceptance criteria

- Deleting or unsyncing one MCP target cannot remove same-named user configuration in another target.
- An externally changed managed MCP entry is preserved and reported rather than silently replaced or removed.
- A forced Skill-install failure leaves every selected target as it was before installation.
- Usage collection does not block the async command executor and UI failure text contains no local absolute path.
- Version validation rejects a Runtime bundle whose desktop-plugin identity differs from the shell expectation.
- Targeted Rust and frontend tests pass, followed by release/version checks before `desktop-v0.1.42` is created.

## Non-goals

- Automatically resolving MCP conflicts or merging server payloads.
- Migrating third-party Skills or MCP configuration outside the supported target roots.
- Retrying failed GitHub release uploads for an already published tag.
