# FAQ

[ä¸­æ–‡](faq.md)

This page only answers questions that match the current code. The [user guide](user-guide.en.md) and [official shell and overlay](official-architecture.md) define the product scope.

## What is this, and how does it relate to official DeepSeek Harness?

This repository maintains **the official DeepSeek Harness desktop app (`apps/desktop`) plus a local overlay plugin**, and packages it as a portable build. Windows, installer, updates, and plugin management are official; the overlay adds a few capabilities official does not have (mobile connection, model-card tools, Codex/xAI sign-in, MCP cards, model network proxy, Skills view).

It is not a DeepSeek product and has no official endorsement. It is a personal customization with no support commitment.

## Can I still use the older build with its own shell?

This repository no longer maintains it and no longer ships updates. See [migrating from 2.0.x](migration-from-2.0.en.md) for where capabilities and data went; the old implementation survives only in git history and past releases. Following the old documentation will not match the code.

## Which platforms are supported?

- Windows x64: NSIS installer plus a portable ZIP.
- macOS ARM64: DMG plus ZIP, ad-hoc signed.
- No Intel macOS build and no Linux build.

## Do I need to install Node.js?

Not for the portable build; the runtime is bundled. Building or running the development flow needs Node.js `^22.19.0` or `>=24.0.0`, pnpm via `corepack`, Git, and `bash`; Windows packaging needs a Windows x64 host with PowerShell 7 (`pwsh`), and macOS packaging needs an Apple Silicon host.

## Windows says "Windows protected your PC". What now?

The build is not code-signed, so SmartScreen blocks it. Click "More info" â†?"Run anyway". This is normal for unsigned builds, not corruption; verify the download with the sibling `.sha256` if you want certainty.

## The macOS app will not open.

The build is ad-hoc signed and not notarized. Two options:

1. Right-click (or Control-click) the app in Applications â†?"Open" â†?confirm again.
2. Try opening it once, then use "Open Anyway" at the bottom of System Settings â†?Privacy & Security.

If it is still blocked, clear the quarantine attribute with `xattr -dr com.apple.quarantine <app path>` and open it again. Only do this when you trust the source.

## How do I update?

Manually; no auto-update feed is connected. Download the new build, quit the app on Windows and install over the old copy (or replace the extracted ZIP directory), or open the new DMG on macOS and drag the app into Applications. Data stays in `DSH_HOME`, so switching builds does not lose it.

## What is `DSH_HOME`, and will it touch my existing `~/.dsh`?

`DSH_HOME` is the DSH data directory: sessions, settings, credentials, profiles.

| Scenario | Default |
|---|---|
| Official app run directly | `~/.dsh` |
| Portable build (`DSH_HOME` unset) | `~/.dsh-official` |
| Development script `start-official.sh` | `~/.dsh-official-dev` |

The portable build never writes `~/.dsh` by default, so it can coexist with a daily-driver official Harness. Overlay configuration lives only in `<DSH_HOME>/overlay-custom.yaml` and never in the official `settings.yaml`. Setting `DSH_HOME` moves the official app and the overlay together.

## How do I configure the relay?

Deploy [`relay-server/`](../relay-server/README.md) first (Node â‰?22 plus TLS), then enter the origin in the app's mobile dialog, or override it with the `DSH_OVERLAY_RELAY_ORIGIN` environment variable. Only a bare origin is accepted: `https://` for public hosts, `http://` only for `localhost`, `127.0.0.1`, or `[::1]`.

The relay sees forwarded plaintext traffic, so deploy it only on a server you control.

## How do I change the official baseline?

Only to a **published official tag**, and always outside the official tree:

1. Confirm the target tag and commit, e.g. `dsh-v0.3.0`.
2. Update `repository`, `commit`, and `version` in `official-lock.json`.
3. Fast-forward the checkout and rebuild official artifacts:

```bash
DSH_OFFICIAL_REF=dsh-v0.3.0 bash scripts/update-official.sh
```

It requires a clean checkout on `master` with the official desktop not running, performs only `merge --ff-only`, then runs `pnpm install --frozen-lockfile` and `pnpm run build`.

4. Run the consistency check and overlay tests:

```bash
bash scripts/check.sh
```

5. Run one complete portable build plus smoke on Windows x64 and on macOS ARM64 (`scripts/build-portable-windows-x64.ps1`, `scripts/build-portable-macos-arm64.sh`).
6. If official releases changed a service or slot the overlay uses, fix the overlay. Never patch or cherry-pick inside `deepseek-harness/`.

## The overlay is installed but the UI is unchanged. How do I debug it?

In order:

1. **Is `DSH_HOME` right?** The overlay only reads and writes the active home. Portable builds use `~/.dsh-official`, development uses `~/.dsh-official-dev`; do not look at `~/.dsh`.
2. **Is the overlay in the profile?** Check `dsh.profile.bundles` in `<DSH_HOME>/profiles/desktop/package.json` (`@deepseek-ai/dsh-overlay-custom` in a portable build, `dsh-overlay-custom` in development) and the `overlay-custom` row in `cordis.patch.yml`.
3. **Reinstall it in development**: `bash scripts/install-overlay.sh`. If it reports that the desktop profile is managed by Electron, add `file:<absolute repo path>/overlay` through the official plugin manager.
4. **Did you rebuild `lib/`?** `overlay/lib/` is a gitignored build artifact; without it nothing loads:

```bash
cd overlay && npm run bundle:host && npm run bundle:client && npm run check
```

5. **Restart the app.** Bundles and slots are composed at startup only.
6. **Read the log.** For development, check `~/.dsh-official-dev/official-desktop.log`; a healthy start has `dsh-overlay-custom host mounted`. If that line is missing, the host face did not mount, usually because `webServer` or `connection` disappeared upstream.
7. **Only the UI missing while routes work?** An official client slot was renamed. Host routes (`/api/desktop/...`) and client slots fail independently.
8. **Does a portable profile still hold a legacy `file:` dependency?** On profile create/upgrade the packaged app removes legacy `file:` dependencies and symlinks automatically and writes `portable-overlay-state.json`. If that file exists but the UI still does not change, go back to step 2.

## The phone scans the code but the page will not load.

- The origin in the QR code/link must be reachable from the phone and have a valid certificate.
- Links stop working as soon as the app restarts; open the entry again to mint a new one.
- If the state says "waiting for remote connection" and the phone never connects, confirm the relay's `/healthz` works and that the desktop-to-relay WebSocket is not blocked by a corporate network.

## Why does model selection or sign-in require a restart?

Model selection has to affect the official loader's model list, so the overlay writes it into the profile's `cordis.patch.yml`. The official loader composes configuration at startup, hence a restart after saving.

## Where do I report problems?

Open a [GitHub issue](https://github.com/XingAur/deepseek-harness-desktop/issues/new/choose) with the OS and architecture, the artifact filename (including `buildVersion`), your `DSH_HOME` path, reproduction steps, and the relevant log excerpt. Redact anything credential-like first.
