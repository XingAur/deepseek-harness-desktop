# DSH Desktop (personal fork)

A **personally customized** Electron desktop client built on
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness): it wraps the
Harness local web UI, Host services, and plugin system into a native desktop
app, and layers remote-control and model tooling on top.

- Upstream chain: anywhere-labs/deepseek-harness-desktop → LiuYuMiao-M/dsh-desktop
  → this repository (XingAur/deepseek-harness-desktop); evolves independently
- Personal use first; no official support is offered. Installers are built and
  published by this repository's GitHub Actions
- [中文](README.md) · English

## Downloads

Grab them from [GitHub Releases](https://github.com/XingAur/deepseek-harness-desktop/releases/latest):

| Platform | File | Notes |
| --- | --- | --- |
| Windows x64 | `DSH-Desktop-<version>-x64-Setup.exe` / `-Portable.zip` | NSIS installer / portable zip |
| macOS | `DSH.Desktop-<version>-universal.dmg` | Unsigned universal DMG; allow it in System Settings → Privacy & Security on first open |

Watch the Releases page for new versions.

## Customizations over upstream

### Cloud-relay remote control
- Self-hosted relay server ([`relay-server/`](relay-server/)); the phone reaches
  the desktop over HTTPS through the relay, no public exposure of the machine
- Sidebar "Mobile" entry with a three-state icon: black (off) → amber (waiting
  for a remote connection) → green (remote connected)
- Opt-in per app run: remote control stays off at startup until enabled in the
  pairing window
- Pairing QR code + copyable link; the phone page mirrors the desktop sidebar:
  workspace-grouped sessions with matching titles, 5-second polling sync,
  create/message/stop tasks, and a read-only approval mirror
- Scan with the system camera or a browser; in-app browsers (e.g. WeChat) may
  render raw-IP links blank

### Model tooling
- "Test connection" / "Fetch model list" actions on every provider card
  (DeepSeek and OpenAI-compatible custom routes)
- Fetched models fill the card's native model list directly; probe failures
  surface the upstream error verbatim and retry transient faults
- A standalone "Model diagnostics" tray window

### Stability fixes
- False "failed to load" for agent presets in sealed (asar) installs
- Whole-chat outages caused by package-inventory resolution during request
  preparation
- Probe credential reads failing silently

## Remote-control quick start

1. Deploy the relay server: see [`relay-server/README.md`](relay-server/README.md)
   (Node + systemd + Caddy, bare-IP certificates supported)
2. Configure `~/.dsh/settings.yaml`:
   ```yaml
   dsh-desktop:
     remoteRelayOrigin: https://<your-relay-origin>
   ```
3. Restart the app → sidebar "Mobile" → "Enable remote control" → scan

## Development

```bash
corepack yarn install          # Node 22+, yarn 4.18 via corepack
corepack yarn workspaces foreach run build
corepack yarn workspace dsh-plugin-desktop test
```

- A single stable variant (`dsh-plugin-desktop`), currently 2.0.x; the upstream
  runtime is pinned as vendored tarballs (see `vendor/dsh-runtime/`)
- Gates: `yarn check` / the individual `check:*` scripts; releases: the GitHub
  Actions Release workflow (Windows + macOS)
- Local packaging scripts live in `dsh-plugin-desktop/package.json`
  (`dist:win`, `dist:mac-smoke`, …)

## Relationship to upstream

The upstream runtime runs pinned and unmodified; customizations live in the
desktop shell plugin and its companion services. This repository does not
promise to track upstream releases — syncs are manual and on demand.

## License

MIT (see [LICENSE](LICENSE)).
