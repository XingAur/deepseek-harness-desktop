# User Guide

This guide covers the official DeepSeek Harness desktop app plus the local overlay. Windows, tray, model list, sessions, terminal, and plugin management come from the official app; "Mobile", "Skills", "MCP", and the extra model-card buttons come from the overlay.

## Installation

### Portable build (recommended)

Download the file for your platform from [GitHub Releases](https://github.com/LiuYuMiao-M/dsh-desktop/releases/latest):

| Platform | File | Use |
|---|---|---|
| Windows x64 | `deepseek-harness-overlay-<version>-win-x64-unsigned-manual.exe` (NSIS installer) | Run the installer |
| Windows x64 | `...-win-x64-unsigned-manual.zip` | Extract anywhere and run `DeepSeek Harness.exe` |
| macOS arm64 | `...-mac-arm64-adhoc-manual.dmg` | Open and drag the app to Applications |
| macOS arm64 | `...-mac-arm64-adhoc-manual.zip` | Extract and run the `.app` |

Only two architectures exist: Windows x64 and macOS ARM64. There is no Intel macOS build and no Linux build. Builds are unsigned (ad-hoc signed on macOS); see the [FAQ](faq.en.md) for the first-launch prompts and how to allow them.

Optional: the sibling `.sha256` verifies the download, and `.build.json` records the official commit, official version, build version, and overlay hash for that artifact.

```powershell
Get-FileHash .\deepseek-harness-overlay-<version>-win-x64-unsigned-manual.exe -Algorithm SHA256
Get-Content .\deepseek-harness-overlay-<version>-win-x64-unsigned-manual.exe.sha256
```

### Running the official desktop from source

Requires Node.js `^22.19.0` or `>=24.0.0`, pnpm via `corepack`, Git, and an environment with `bash` (Git Bash or WSL on Windows).

```bash
bash scripts/bootstrap.sh
DSH_OFFICIAL_REF=dsh-v0.2.1-rc.5 bash scripts/update-official.sh
bash scripts/start-official.sh
bash scripts/install-overlay.sh
```

`start-official.sh` runs the official desktop with `DSH_HOME=~/.dsh-official-dev` and writes `official-desktop.log` there. `install-overlay.sh` installs the overlay into that profile via `file:`. If the profile is managed by the official Electron app the script refuses to write; add `file:<absolute repo path>/overlay` through the official plugin manager instead.

## Where data lives

- The official app uses `~/.dsh` by default.
- The portable build defaults to `~/.dsh-official` when `DSH_HOME` is unset, so it never touches your usual `~/.dsh`.
- Development scripts use `~/.dsh-official-dev`.

To move it, set `DSH_HOME` before launch:

```powershell
$env:DSH_HOME = "D:\dsh-data"; & "C:\Path\DeepSeek Harness.exe"
```

```bash
DSH_HOME=~/dsh-data bash scripts/start-official.sh
```

Overlay configuration lives in `<DSH_HOME>/overlay-custom.yaml`; the official `settings.yaml` is left alone.

## Mobile connection

Prerequisite: a relay origin (next section).

1. Click the phone icon at the bottom of the sidebar.
2. On first use the dialog asks for the relay origin; enter `https://your-relay.example` and click "Save and connect".
3. The dialog shows a pairing QR code and link. Scan it with the phone camera or open the link in a browser.
4. Icon colour shows state: green = remote connected, amber = waiting for a remote connection, default = off.

Notes:

- Remote control is off after every app restart; click the entry again to open the tunnel and pair again.
- The link carries a one-shot session parameter; it stops working after the app restarts.
- The phone page can list sessions, send messages, stop tasks, approve tool calls, and answer questions. The desktop can answer at the same time; whichever answers first wins.

## Configuring the relay

The relay is a separately deployed Node service; see [`relay-server/README.md`](../relay-server/README.md). Minimal steps:

```bash
# on the server
cd /opt/dsh-relay
npm install --omit=dev
node main.mjs --host 127.0.0.1 --port 8787
curl https://relay.example.com/healthz   # → dsh-desktop relay
```

The Caddy reverse proxy and systemd unit are in that README. Requirements: Node ≥ 22 and TLS.

Validation rules the overlay enforces:

- Enter an origin only, e.g. `https://relay.example.com`. Paths, query strings, or embedded credentials are rejected.
- Public origins must be `https://`. Only `localhost`, `127.0.0.1`, and `[::1]` may use `http://`, for local debugging.

Two ways to configure it:

- Enter it in the mobile dialog; it is stored as `remoteRelayOrigin` in `<DSH_HOME>/overlay-custom.yaml` and remembered.
- Override with an environment variable, which wins:

```powershell
$env:DSH_OVERLAY_RELAY_ORIGIN = "https://relay.example.com"
```

Security boundary: the relay sees forwarded plaintext HTTP. Deploy it only on a server you control, and always over TLS.

## Models: test, fetch, select

Settings → Models → any provider card (DeepSeek and custom OpenAI-compatible routes):

- **Test connection** requests the provider's model list and shows the HTTP status plus the upstream response on failure; transient failures are retried.
- **Fetch model list / Refresh latest models** pulls the provider's currently available model IDs into the candidate list below.
- **Tick + Save shown models** decides which models appear in the model picker. A restart is required before the picker changes.

Selections are written to the `llm-pi-ai` row of the profile's `cordis.patch.yml` and merged with the same section in `settings.yaml` when read. If the model list endpoint is unavailable (for example after an official upgrade), you only lose the live list; existing models keep working.

## Codex / xAI sign-in

Codex and xAI provider cards have a "Sign in (OAuth)" action:

1. Clicking it starts the official authorization flow and shows "Waiting for browser…".
2. Sign in in the browser ("Open sign-in page" reopens it if needed).
3. If an authorization code is required, paste it and click "Submit".
4. On success the card shows "Signed in" and the live model list becomes available.

Credentials are stored in `<DSH_HOME>/.credentials.yaml` (`llm-pi-ai/openai-codex`, `llm-pi-ai/xai`); the overlay only reads them. If the official authorization service is missing or renamed, the button reports that the sign-in interface is unavailable after the current upstream upgrade.

## MCP management

Settings → MCP:

- Each MCP is a card: test connection, edit, enable/disable, delete, export one JSON.
- "Common MCPs" offers templates: database, Yunxiao, GitLab, Playwright, Filesystem, Memory, generic HTTP.
- "Custom MCP" takes transport (local command `stdio` / remote `streamable-http`), command and arguments, environment, working directory, URL, and headers.
- "Import JSON" accepts Claude Desktop's `{"mcpServers": {...}}`, a single server, or an array of servers.
- Stored secret values in environment and headers are never echoed back; leaving a field blank keeps the stored value.
- "Export all" omits stored tokens, passwords, and header values.

Configuration is stored under `mcpServers` in `<DSH_HOME>/overlay-custom.yaml`. **Restart the app** after saving so the new MCP servers are mounted into the current Host composition. Arguments may reference the same card's `KEY=value` environment entries as `${VAR}`, which keeps passwords out of the command line.

## Model network proxy

Settings → Models → "Model network" at the bottom. It applies to model requests only, never to MCP or other tools, and needs no provider list.

- **System**: use the detected system HTTP/HTTPS proxy and bypass rules. Unsupported or unreadable system settings never silently become direct.
- **Manual**: an `http://` or `https://` proxy URL, optionally with credentials. No fallback to direct when the proxy is unavailable.
- **Direct**: ignore the system proxy and proxy environment variables.

"Test connection" tests the current input and **does not save**; only after "Save" do new model requests use the new route, while in-flight requests keep their route. Loopback addresses always connect directly. Stored proxy credentials are never echoed; leave the URL unchanged to keep them.

If an upstream rejects your region (for example OpenAI reporting an unsupported country), check the effective route and exit region here, then sign in again.

## Skills

Settings → Skills lists the skills the current official composition has discovered, with source, model-invocable and user-invocable flags, and when to use them. Typing `/` in the session input also selects a skill directly.

If the page says the composition has no skill catalog, the running build lacks the official skills service; the page then only informs and offers no management.

## Updates

This project updates **manually**; there is no auto-update. The sidebar "Updates" entry checks two sources and shows a red dot when something is available:

1. **Official releases** (`deepseek-ai/deepseek-harness`, tags `dsh-v*`): the official desktop app published a newer version.
2. **This repository's releases** (tags `official-v<buildVersion>`): a new portable build was published here.

Open the entry to see the current version, the latest official version, the latest build here, the release notes for each, and links to the release pages. The version format is `<locked official version>_<two-digit iteration>`, for example `0.2.1-rc.5_01` and `0.2.1-rc.5_02`; after the baseline moves to `0.2.2-...` the next build becomes `0.2.2-..._01`.

The notice only informs; it never downloads or installs:

1. Official update available: download the official desktop from the official releases page, or wait for this repository to rebuild against the new baseline.
2. New build available: download the package for your platform. On Windows, quit and install the new `.exe` (or replace the ZIP directory); on macOS, open the new DMG and replace the app.
3. Data stays in `DSH_HOME`; restart afterwards.

Why no automatic install: the official installer is signed and publisher-checked, and this repository's unsigned portable builds cannot use the official updater. Installing an official update would replace the overlay build, so the overlay only notifies.

Skipping several patch releases is usually fine, but when the official baseline changes (the part before the underscore) read the release notes first.

## When something breaks

Start with the troubleshooting section of the [FAQ](faq.en.md), especially whether `DSH_HOME` points at the directory you expect and whether the profile really contains the overlay row.
