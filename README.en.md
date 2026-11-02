# DSH Desktop

[中文](README.md)

DSH Desktop is a local build of the official [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) desktop app (`apps/desktop`) with a Cordis overlay maintained in this repository. The Electron shell, accounts, scheduler, terminal, Office preview, plugin management, sessions/workspaces, installer, and updater all come from the official implementation. This repository owns only `overlay/`, the portable-build scripts, and the phone relay server.

- Official baseline: `0.2.1-rc.5`, commit `b830ec950faba0a46dda564be3e2d854afed94f1` (see [official-lock.json](official-lock.json))
- Platforms: Windows x64 and macOS ARM64
- Architecture: [docs/official-architecture.md](docs/official-architecture.md)
- License: MIT

## Downloads

Get the latest build from [GitHub Releases](https://github.com/XingAur/deepseek-harness-desktop/releases). Releases are marked **prerelease**.

| Platform | File | Format |
| --- | --- | --- |
| Windows x64 | `deepseek-harness-overlay-<build>-win-x64-unsigned-manual.exe` | NSIS installer |
| Windows x64 | `deepseek-harness-overlay-<build>-win-x64-unsigned-manual.zip` | Portable ZIP |
| macOS ARM64 | `deepseek-harness-overlay-<build>-mac-arm64-adhoc-manual.dmg` | DMG |
| macOS ARM64 | `deepseek-harness-overlay-<build>-mac-arm64-adhoc-manual.zip` | Portable ZIP |

Every artifact ships with a `.sha256` sidecar and a `.build.json` record. `<build>` is the locked official version plus a two-digit iteration, for example `0.2.1-rc.5_01`.

The artifacts are not commercially code-signed:

- Windows may show a SmartScreen warning on first launch. Choose **More info → Run anyway**.
- macOS builds are ad-hoc signed and not notarized. On first launch, right-click the app and choose **Open**, or allow it under **System Settings → Privacy & Security**.
- Updates are manual: download a new build and reinstall or replace the old one. There is no automatic-update feed.

## What the overlay adds

The official desktop already provides account sign-in, the scheduler, the terminal, Office preview, plugin management, sessions and workspaces, the Web/CLI runtime, the installer, and updates. The overlay fills gaps only:

- **Phone remote control**: a "Mobile" pairing panel in the sidebar plus the cloud relay in [`relay-server/`](relay-server/). The desktop opens an outbound tunnel, so the phone reaches the local Web UI over HTTPS without exposing the machine. Pairing is opt-in per session and supports workspace-grouped sessions, creating or continuing a session, sending messages, stopping a task, switching model/preset, and answering approvals or questions. A QR code and a pairing link are provided.
- **Model tools**: **Test connection**, **Fetch model list**, and **Models shown in picker** on each provider card. Fetched models are written back to the card and saved; a restart applies the selection. Failures show the upstream error.
- **Codex / xAI OAuth sign-in**: starts the official OAuth flow from the provider card. If the official login API is unavailable after an upgrade, the action is disabled without blocking app startup.
- **MCP card manager**: add, edit, and test MCP servers as cards in Settings, with built-in templates. Configuration lives in `overlay-custom.yaml` and is mounted by the overlay on the next start.
- **Model network proxy**: model requests can follow the system proxy, use a manual HTTP(S) proxy, or connect directly, with bypass rules and a connectivity test. MCP and other tools are unaffected.
- **Skills catalog**: a Settings section that lists the skills available to the current profile.
- **Version and update notice**: an "Updates" sidebar entry with a red dot checks the official releases and this repository's releases, shows both version numbers (`official version_iteration`) and release notes, and links the matching release pages. It only notifies; it never installs anything.

### Relay configuration

Enter the relay origin in the "Mobile" panel. It is stored as `remoteRelayOrigin` in `$DSH_HOME/overlay-custom.yaml`; `DSH_OVERLAY_RELAY_ORIGIN` overrides it. Only an `https://` origin is accepted (loopback `http://127.0.0.1` is allowed for local development). See [`relay-server/`](relay-server/) for deployment and protocol details.

## Quick start

### Installed build or portable build

1. Download the file for your platform from Releases. On Windows, run the `.exe` installer or extract the `.zip`. On macOS, open the `.dmg` and drag the app to Applications, or extract the `.zip`.
2. Allow the app through the system security prompt described above on first launch.
3. Application data defaults to `~/.dsh-official`. The build neither reads nor writes `~/.dsh`, so it can coexist with an official DeepSeek Harness installation.
4. To update, download a new build and reinstall or replace the portable directory the same way. Releases are prerelease and updates are manual.

### Verify a download

```bash
sha256sum -c <artifact>.sha256                 # Linux / macOS
Get-FileHash <artifact> -Algorithm SHA256      # Windows PowerShell; compare with .sha256
```

`.build.json` records the build version, official commit, overlay digest, and `update: manual`.

## Development

Prerequisites: Node.js `^22.19.0` or `>=24.0.0`, Git, Bash, and pnpm for the official checkout. The overlay uses npm. Windows portable builds require Windows x64 and PowerShell 7; macOS portable builds require macOS ARM64.

```bash
# 1. Clone the official source into deepseek-harness/ (gitignored, standalone clone)
bash scripts/bootstrap.sh

# 2. Fast-forward to the locked tag, then install dependencies and build the official artifacts
DSH_OFFICIAL_REF=dsh-v0.2.1-rc.5 bash scripts/update-official.sh

# 3. Start the official desktop with an isolated DSH_HOME (~/.dsh-official-dev)
bash scripts/start-official.sh

# 4. After the first launch creates the profile, install the overlay into it
DSH_HOME=$HOME/.dsh-official-dev bash scripts/install-overlay.sh

# 5. Overlay unit tests
cd overlay && npm ci && npm run check

# 6. Full check (requires the official checkout to match official-lock.json and be clean)
cd .. && bash scripts/check.sh

# 7. Build portable packages
pwsh -NoProfile -File scripts/build-portable-windows-x64.ps1   # Windows x64
bash scripts/build-portable-macos-arm64.sh                     # macOS ARM64
```

The root `package.json` exposes matching npm aliases: `official:bootstrap`, `official:sync`, `official:start`, `official:status`, `overlay:install`, `check`, `build:win`, and `build:mac`. For example, `npm run check` runs `bash scripts/check.sh`.

`deepseek-harness/` is a gitignored checkout of the official source and **must not be modified**. To move the baseline, fast-forward to an official tag and update `official-lock.json`. Do not patch, cherry-pick, or commit local features into that tree. `bash scripts/check.sh` verifies the checkout's HEAD, version, and clean state.

Data directories:

- Development: `DSH_HOME=~/.dsh-official-dev` (set by `scripts/start-official.sh`)
- Portable build: `~/.dsh-official` by default
- The user's everyday `~/.dsh` is never used, migrated, or overwritten. `install-overlay.sh` falls back to the DSH default directory when `DSH_HOME` is unset, so always pass it explicitly as shown above.

`scripts/install-overlay.sh` prefers the official `dsh plugin` command to add `overlay/` to the target profile. If the profile is managed by the Electron plugin manager, the script tells you to add `file:<repo>/overlay` there instead.

## Relationship to upstream

This is not a DeepSeek-owned repository. "Official" here means the upstream [deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) `apps/desktop` implementation. We pin one official commit and only fast-forward it; the official source tree is never modified. The overlay uses public Cordis Host services and Client slots, is injected into a temporary staging tree during packaging, and leaves the official desktop usable when those services are missing.

Report defects in official features upstream. This repository accepts changes to the overlay, build scripts, relay, and documentation only. Upstream syncs happen as needed; there is no promise to follow every release.

## License

MIT. See [LICENSE](LICENSE).
