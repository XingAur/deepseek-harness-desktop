# Contributing

DSH Desktop is the official DeepSeek Harness desktop app (`apps/desktop`) plus this repository's Cordis overlay. The repository maintains only the overlay, the portable-build scripts, the phone relay, and documentation. The official source stays unmodified.

Before writing code, read [docs/official-architecture.md](docs/official-architecture.md). It defines the dependency direction, feature ownership, and packaging boundary in more detail than this page.

## What to contribute

- `overlay/`: desktop capabilities the official app does not provide, such as phone relay, model tools, OAuth, MCP, proxying, and the Skills catalog.
- `scripts/`: official-source sync, portable packaging, sealing, and verification.
- `relay-server/`: the phone relay service and its protocol.
- Documentation, translations, and issue reproductions.

The following belong to the official implementation and are not fixed or reimplemented here: account sign-in, scheduler, terminal, Office preview, plugin management, sessions/workspaces, the Web/CLI runtime, the installer, and updates. Please report those upstream.

## Development environment

You need Node.js `^22.19.0` or `>=24.0.0`, Git, Bash, and pnpm for the official checkout. The overlay uses npm on its own.

```bash
# Official source, first time
bash scripts/bootstrap.sh

# Fast-forward to the locked tag and build the official artifacts
DSH_OFFICIAL_REF=dsh-v0.2.1-rc.5 bash scripts/update-official.sh

# Start the official desktop with the development home (~/.dsh-official-dev)
bash scripts/start-official.sh

# After the first launch creates the profile, install the overlay
DSH_HOME=$HOME/.dsh-official-dev bash scripts/install-overlay.sh

# Overlay unit tests
cd overlay && npm ci && npm run check

# Full check: overlay + build scripts + official checkout matching the lock
cd .. && bash scripts/check.sh
```

Portable builds:

```bash
pwsh -NoProfile -File scripts/build-portable-windows-x64.ps1   # Windows x64
bash scripts/build-portable-macos-arm64.sh                     # macOS ARM64
```

The root `package.json` also exposes `npm run official:*`, `npm run overlay:install`, `npm run check`, `npm run build:win`, and `npm run build:mac`.

## Repository boundaries

- `deepseek-harness/` is a gitignored standalone clone of the official source and **must not be modified**. Move the baseline only by fast-forwarding to an official tag and updating `official-lock.json`. Do not patch, cherry-pick, or commit local features there.
- The overlay is the only code loaded into the desktop that this repository owns. It may use only public Cordis Host services and Client slots. It must not import the official source tree, read official internal paths, or use Electron APIs.
- The overlay must remain optional. When an official service is missing or renamed, the corresponding entry point shows as unavailable and the official desktop still starts.
- Packaging injects the overlay into a one-off staging tree created by `git archive`; it never edits the official source tree.
- Keep data directories isolated: development uses `DSH_HOME=~/.dsh-official-dev`, the portable build uses `~/.dsh-official`, and neither reads nor writes the user's `~/.dsh`. `install-overlay.sh` falls back to the DSH default when `DSH_HOME` is unset, so always pass it explicitly.
- Do not add telemetry, content reporting, or implicit uploads to the overlay or relay. The relay forwards paired sessions only and does not record session content.

## Commits and pull requests

- Use conventional commit messages, for example `fix(overlay): ...` or `docs: ...`.
- Before committing, run at least `cd overlay && npm ci && npm run check`. When changing build scripts or the lock file, also run `bash scripts/check.sh`.
- When changing packaging, ASAR repacking, signing, or artifact layout, run the matching build and smoke test on real Windows x64 / macOS ARM64. Passing unit tests on Linux does not mean an installer was verified.
- Keep documentation changes bilingual and update the matching `README.i18n.yaml` or `PRIVACY.i18n.yaml` hash record.
- Describe the change, its motivation, and how it was verified in the PR. CI covers only the overlay and build scripts; full artifact verification happens in the release workflow on real platforms.
- Do not commit `deepseek-harness/`, `dist/`, `node_modules/`, logs, credentials, diagnostic archives, or build caches.

## Code of conduct

Be kind and respectful, and stay on topic. The [Contributor Covenant](CODE_OF_CONDUCT.en.md) applies to all project spaces.
