# Migrating from the 2.0.x custom shell

In November 2026 this repository moved to an **official-first architecture**. The self-written Electron shell (`dsh-plugin-desktop`, 2.0.x) is gone; windows, tray, installer, updates, accounts, scheduling, terminal, Office and plugin management come from the official `apps/desktop`. This repository keeps only the overlay plugin and the portable packaging scripts.

## Where each capability went

| Old shell capability | Now |
|---|---|
| Cloud relay remote control and pairing page | Kept in the overlay (`relay-server/`, `/api/desktop/remote/*`, `/mobile`) |
| Model "test connection / fetch models / show selection" | Kept in the overlay |
| Codex / xAI OAuth sign-in | Kept in the overlay |
| MCP card management | Kept in the overlay |
| Model network proxy | Kept in the overlay |
| Scheduler, accounts, terminal, Office, plugin manager | Provided by the official app; the overlay does not duplicate them |
| Compatibility/extended/enhanced frames, tray mode switching, profile picker, setup wizard, custom installer and updater | Removed; the official shell owns these |

## Data

- The official desktop still uses `$DSH_HOME` (normally `~/.dsh`), so sessions, settings and credentials left by the old shell remain readable by the official app.
- Portable builds from this repository default to `~/.dsh-official`, separate from `~/.dsh`. Set `DSH_HOME` explicitly if you want to share one home.
- Never run both products against the same `DSH_HOME` at the same time, and do not copy credential files between them by hand.

## Steps

1. Quit and uninstall the old DSH Desktop (2.0.x).
2. Install the official DeepSeek Harness desktop, or use a portable build from this repository's Releases (`deepseek-harness-overlay-<version>-win-x64-unsigned-manual.zip` / `-mac-arm64-adhoc-manual.dmg`).
3. To keep remote control on the official app, deploy the relay per the [relay guide](../relay-server/README.md), set `remoteRelayOrigin` in the overlay config, then enable "Phone connection" from the sidebar.
4. Portable builds already bundle the overlay. For source development, run `bash scripts/install-overlay.sh` against a separate `DSH_HOME`.

## No rollback support

This repository no longer ships 2.0.x updates or installers. Old versions remain reachable through git history and past releases, but they are unmaintained and receive no fixes.
