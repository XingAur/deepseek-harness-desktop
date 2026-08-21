# Runtime preparation on first launch

Date: 2026-08-21
Status: approved design, awaiting user review

## Goal

Make the Windows full installer finish without launching the Desktop application, a Runtime process, or a browser. The full package continues to embed the signed Runtime payload, but verification, extraction, probing, activation, and receipt publication move into the visible first-launch Generation lifecycle.

## User experience

The installer only copies the Desktop shell, bundled Runtime archive and manifest, writes registration data, and creates shortcuts. It does not invoke `--install-bundled-runtime` or perform any Runtime work.

On application launch, the existing preparation surface appears immediately. Runtime selection follows this order:

1. Reuse a locally provisioned Runtime when its signed manifest, active payload, target, Desktop version, and final receipt match.
2. If no compatible provisioned Runtime exists and signed bundled resources are available, prepare from the bundled archive without network access.
3. If bundled resources are missing, corrupt, or fail integrity checks, fetch the signed online Runtime as a repair fallback.
4. Probe the prepared candidate with `--no-open`, activate it only after readiness succeeds, publish the final receipt, and launch the workbench.

Second and later launches reuse the verified active Runtime and remain on the existing fast-start path.

## Progress contract

Bundled extraction emits real completed and total work. The visible message uses `正在解压内置组件 N%`, where the percentage is derived from extracted archive content, never elapsed time.

The percentage must:

- begin at zero before extraction;
- remain within 0–100;
- never decrease;
- reach and visibly emit 100 before the phase changes to verification;
- stop at the last real value if extraction fails.

After extraction reaches 100%, the UI changes to `正在验证组件`, then candidate probing and activation. Existing runtime progress events remain the single frontend contract; no second provisioning window is introduced.

## Architecture

### Installer boundary

Remove the full-installer post-install `ExecWait` hook. The full Tauri configuration retains only deterministic Runtime resource mappings. The online installer remains free of bundled Runtime resources.

Remove the obsolete `--install-bundled-runtime` application mode and its process-exit wrapper so an installer or external caller cannot accidentally reintroduce hidden Runtime provisioning. Reusable provisioning behavior moves behind normal startup services rather than a command-line mode.

### Runtime source selection

Generation startup owns a source selector with three candidates:

- `Local`: verified active Runtime plus matching final receipt;
- `Bundled`: signed manifest and archive beneath the fixed application resource directory;
- `Online`: signed manifest and archive from the production GitHub Release policy.

The selector prefers Local, then Bundled, then Online. Bundled resources are optional so the same code supports both installer types. Online remains the source for later compatible updates and repair when the embedded payload is unusable.

Selection does not bypass the existing candidate lifecycle. Bundled and online candidates use the same staging, health probe, activation, receipt, rollback, and diagnostic contracts.

### Extraction progress

Archive staging accepts a progress observer. ZIP extraction calculates the total uncompressed content declared by validated entries and increments completed work after bytes are written. Other supported archive formats use the equivalent validated entry sizes. Directory entries do not inflate completed work. Zero-byte archives emit a deterministic 100 only after successful extraction.

The staging layer reports numeric progress without UI strings. Generation maps bundled staging progress to Runtime events and the Chinese preparation message. Online download progress remains separate from extraction progress.

## Failure recovery

Bundled manifest absence, signature failure, hash mismatch, unsafe archive content, or extraction failure triggers one online repair attempt. The startup page states that the embedded component is unavailable and the application is repairing online.

Before downloading, compare the online signed manifest identity with the failed bundled identity. If it resolves to the same payload hash that already failed content or probe validation, do not download and retry it indefinitely. Surface a retryable failure with diagnostic export instead. A different signed online payload may proceed.

Transient Windows activation locks continue using the existing bounded retries for operating-system errors 5, 32, and 33. Failure never replaces a last-known-good Runtime or Profile. Candidate activation remains transactional and writes the final receipt only after readiness.

When neither bundled nor online preparation succeeds, the existing problem surface offers retry and diagnostic export. Diagnostics record the selected source, failure phase, and last extraction percentage using safe relative identifiers; secrets and sensitive absolute paths are redacted.

All managed candidate and active Runtime launches include exactly one `--no-open`. No failure or retry path opens an external browser.

## Testing

Automated coverage must include:

- full installer contract contains Runtime resources but no post-install Runtime execution hook;
- application argument parser rejects the removed bundled-install mode;
- local compatible Runtime keeps the network-free fast path;
- full-package first launch selects bundled Runtime without an HTTP request;
- online installer without bundled resources selects the signed online source;
- corrupt bundled archive falls back online once;
- identical failed online payload is not downloaded repeatedly;
- extraction progress is real, monotonic, bounded, and emits 100 before verification;
- candidate failure preserves the active pointer, receipt, and Profile last-known-good state;
- every managed Runtime probe receives `--no-open` exactly once;
- frontend renders the percentage and transitions between extraction, verification, and startup phases.

Run the complete frontend, plugin, Rust, installer-template, icon, and build gates. Build a new Windows full installer and report its size, SHA-256, and Authenticode status. The user performs the final packaged install, first-launch, second-launch, and browser-observation black-box check.

## Acceptance criteria

- Windows installation completes without Runtime processing or a browser window.
- Full-package first launch prepares the embedded Runtime offline with visible real progress through 100%.
- The Runtime candidate probe does not open Chrome or another browser.
- A successful first launch produces a matching final receipt; the next launch uses fast start without extraction.
- Invalid embedded resources fall back to a different signed online payload or present a bounded actionable failure.
- Online and full installers share one Generation, activation, rollback, and diagnostic lifecycle.
