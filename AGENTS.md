# DSH Desktop repository rules

This repository ships one product: the official DeepSeek Harness desktop app (`apps/desktop`) plus the Cordis overlay in `overlay/` and the portable-build scripts. The official source is an unmodified checkout pinned by `official-lock.json`.

Read these first:

- [docs/official-architecture.md](docs/official-architecture.md) — architecture, feature ownership, loading, and build flow.
- [official-lock.json](official-lock.json) — official repository, commit, and version (`0.2.1-rc.5`).
- [overlay/package.json](overlay/package.json) — Cordis bundle and client injection points.
- [.github/workflows/ci.yml](.github/workflows/ci.yml) and [.github/workflows/release-official-portable.yml](.github/workflows/release-official-portable.yml) — CI and release contracts.

## Hard boundaries

- `deepseek-harness/` is a gitignored, standalone clone of the official source. **Never edit, patch, or commit files in it.** Move the baseline only by fast-forwarding to an official tag and updating `official-lock.json`. `bash scripts/check.sh` enforces the locked HEAD, version, and clean tree.
- Do not reintroduce a self-written Electron shell, installer, updater, or copied runtime. Those responsibilities belong to the official implementation.
- The overlay is the only code this repository loads into the desktop. It may use only public Cordis Host services and Client slots. Do not import the official source tree, read official internal file paths, or import `electron`.
- The overlay must be optional. If an official service is missing or renamed, the related entry point shows as unavailable and the official desktop still starts.
- Packaging may inject files only into a one-off staging tree produced by `git archive`; never modify the official checkout in place.
- Keep data isolated: development uses `DSH_HOME=~/.dsh-official-dev`, the portable build defaults to `~/.dsh-official`, and the repository must not read or write the user's `~/.dsh`. Always pass `DSH_HOME` explicitly; `scripts/install-overlay.sh` falls back to the DSH default when it is unset.
- Do not add telemetry, content reporting, or implicit uploads. The relay forwards paired sessions only and does not persist session content.

## Feature ownership

The official desktop owns accounts, scheduler, terminal, Office preview, plugin management, sessions/workspaces, the Web/CLI runtime, the installer, and updates. Do not reimplement them in the overlay.

The overlay owns only what the official app lacks:

- Cloud-relay phone control and the pairing page (with `relay-server/`).
- Model **Test connection**, **Fetch model list**, and picker-visible model selection.
- Codex / xAI OAuth sign-in.
- MCP card management.
- Model network proxy.
- Skills catalog display.
- Version and update notice: compare the official baseline and this repository's portable builds, show both release notes, and link the matching release pages. Never install updates automatically.

Defects in official features belong upstream. Keep overlay changes within this list unless an architecture change is explicitly approved.

## Setup and commands

Requires Node.js `^22.19.0` or `>=24.0.0`, Git, Bash, and pnpm for the official checkout. Run commands from the repository root unless noted.

```bash
bash scripts/bootstrap.sh
DSH_OFFICIAL_REF=dsh-v0.2.1-rc.5 bash scripts/update-official.sh
bash scripts/start-official.sh
DSH_HOME=$HOME/.dsh-official-dev bash scripts/install-overlay.sh
cd overlay && npm ci && npm run check
cd .. && bash scripts/check.sh
pwsh -NoProfile -File scripts/build-portable-windows-x64.ps1   # Windows x64
bash scripts/build-portable-macos-arm64.sh                     # macOS ARM64
```

Root npm aliases: `official:bootstrap`, `official:sync`, `official:start`, `official:status`, `overlay:install`, `check`, `build:win`, `build:mac`. `npm run check` runs `bash scripts/check.sh`.

`bash scripts/start-official.sh` sets `DSH_HOME=~/.dsh-official-dev`. The portable build sets `~/.dsh-official` when `DSH_HOME` is unset. Do not add a fallback to `~/.dsh`.

## Verification

- Overlay-only changes: `cd overlay && npm ci && npm run check` (unit tests).
- Build-script or lock changes: `bash scripts/check.sh` (overlay checks, script syntax, portable-path guards, official checkout matches the lock and is clean).
- Packaging, ASAR, signing, or artifact-layout changes require a real portable build and smoke on Windows x64 or macOS ARM64. Linux CI passing is not artifact verification.
- CI covers the overlay and build scripts only. The release workflow performs the real platform builds.

## Documentation

- README, CONTRIBUTING, and PRIVACY are bilingual pairs. Keep both sides equivalent and update `README.i18n.yaml` / `PRIVACY.i18n.yaml` with the new Git blob hashes after editing.
- [docs/official-architecture.md](docs/official-architecture.md) is the architecture source of truth. Do not rewrite it as part of unrelated overlay work.
- Do not document or reference removed legacy components or installation paths. The current layout is `overlay/`, `scripts/`, `relay-server/`, `docs/`, `official-lock.json`, and `.github/workflows/`.

## Releases

- Releases are **prerelease**, **manual-update** builds. Windows x64 is unsigned; macOS ARM64 is ad-hoc signed and not notarized.
- The build version is `<official version>_<NN>` (for example `0.2.1-rc.5_01`; the default iteration is `01`, overridable with `DSH_PORTABLE_ITERATION` or `DSH_PORTABLE_BUILD_VERSION`). The release tag must be `official-v<build-version>`.
- Artifacts are `deepseek-harness-overlay-<build>-win-x64-unsigned-manual.{exe,zip}` and `deepseek-harness-overlay-<build>-mac-arm64-adhoc-manual.{dmg,zip}`, each with `.sha256` and `.build.json` sidecars.
- Do not add an automatic-update feed or publish a stable release from this workflow.

## Commits

- Use conventional commits, for example `fix(overlay): ...`, `build: ...`, or `docs: ...`.
- Keep an official-lock update in its own commit, separate from overlay behavior changes.
- Do not commit `deepseek-harness/`, `dist/`, `node_modules/`, logs, credentials, diagnostic archives, or build caches.
- Commit before large direction changes. Do not run `git push` or publish releases unless explicitly asked.
