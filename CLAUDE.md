# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

DeepSeek Harness Desktop is a Tauri 2 + React + Rust desktop client for DeepSeek Harness (Windows x64, macOS Apple Silicon only). It does **not** re-implement DeepSeek Harness as a management dashboard — it bundles/downloads a signed "Managed Runtime" (Node 24 + DeepSeek Harness + pnpm), starts the official DeepSeek Harness Web UI on a random `127.0.0.1` port, and embeds it below the trusted local title bar. Desktop-specific behavior (desktop workbench layout, local projects, Profile management, plugin market) is delivered as ordinary DeepSeek Harness plugins composed into the official workbench. The Rust shell additionally hosts a local app launcher that runs projects built by sessions on loopback ports.

UI strings and many comments are written in Simplified Chinese; identifiers and APIs are English. Follow that convention.

## Commands

```bash
npm ci --legacy-peer-deps        # install (legacy-peer-deps is required)

npm run check                    # full CI gate, no Rust needed:
                                 #   root vitest + both workspace test suites
                                 #   + web build + both package builds
npm run test                     # root tests only (src/ and scripts/, jsdom)
npm run test:watch
npm run plugin:test              # @dsh/desktop-plugin tests only
npm run agent:test               # @dsh/agent-adapter tests only

# single test file
npx vitest run src/App.test.tsx
npm run test -w @dsh/desktop-plugin -- tests/advanced-shell.spec.ts

cargo test --manifest-path src-tauri/Cargo.toml --locked   # Rust tests
npm run installer:verify && npm run icon:verify            # installer asset checks

# end-to-end (needs a binary built with the e2e feature; fixed CDP port 9229)
npm run e2e:build                                      # tauri build --features e2e --no-bundle
DSH_E2E_APP_BINARY=<path-to-built-binary> npm run e2e  # wdio workbench/local-app specs
npm run e2e:installer:quick                            # installer suites: quick | full

npm run release:versions:check   # read-only consistency check of release/versions.json
npm run tauri dev                # full dev shell; needs Rust stable + native deps
                                 # + a reachable signed runtime manifest
                                 #   (DSH_DESKTOP_RUNTIME_MANIFEST_URL=...)
```

Rust side lives in `src-tauri/` (`cargo check` / `cargo test` there verify Rust changes without a full installer build). e2e specs live in `e2e/specs/` (workbench smoke, local app launch) plus installer suites (first-start provisioning, upgrade/uninstall).

### Runtime assembly & signing

```bash
npm run runtime:build -- --target=windows-x86_64 --version=0.1.0 --url=<release-url>
node scripts/sign-manifest.mjs <unsigned.json> <out.json>
```

Signing keys come from `DSH_DESKTOP_SIGNING_PRIVATE_KEY` / `DSH_DESKTOP_SIGNING_PUBLIC_KEY` (Ed25519 raw JWK). In-repo keys are dev-only; production keys live in GitHub Secrets and the matching public key is compiled into the Tauri shell via `DSH_DESKTOP_RELEASE_PUBLIC_KEY`. Never commit production private keys.

### Release versioning

`release/versions.json` is the single version source for the desktop app, managed runtime, DeepSeek Harness, Node, and pnpm. `npm run release:prepare -- --latest=<exact upstream version>` bumps it plus the derived version files — run it on a clean branch/checkout. The daily `upstream-sync` workflow does this automatically against npm's `@deepseek-ai/dsh` `latest` tag.

## Architecture

Three layers with a strict, deliberate trust boundary between them:

```
src/        React bootstrap/recovery UI — only shown while the runtime is not ready
src-tauri/  Rust desktop shell — runtime manager, agents, local apps, security boundaries
packages/   DeepSeek Harness plugins + agent adapters — run INSIDE the managed Node runtime
```

### 1. Bootstrap UI (`src/`)

State-machine UI driven by `runtime-reducer.ts`; `runtime-contract.ts` defines the phase/failure/event types shared with Rust (mirror of `src-tauri/src/runtime/model.rs`); `runtime-client.ts` is the Tauri IPC implementation. Includes the permanent local `TitleBar.tsx`, migration prompts, and theme sync (`theme-message.ts`). Local-shell actions for the embedded workbench go through the typed, action-whitelisted bridge contract (`bridge-contract.ts` / `workbench-bridge.ts`) — not raw Tauri IPC. Once the runtime is healthy, the managed loopback workbench is embedded in an iframe below the title bar; the iframe does **not** receive Tauri IPC.

### 2. Rust desktop shell (`src-tauri/src/`)

`runtime/` orchestrates the runtime lifecycle (`manager.rs`: checking → fetching-manifest → downloading → verifying → activating → starting → ready) as a cancellable background task emitting progress events the React reducer consumes. Submodules: `manifest.rs` (Ed25519 signature verification), `download.rs` (resumable, SHA-256), `archive.rs` (safe extraction), `activation.rs` (versioned dirs + last-known-good rollback), `health.rs`, `process.rs` (spawn managed Node on a reserved loopback port), `diagnostics.rs` (redacted diagnostic ZIP). Key invariant: a new runtime version only becomes active after its health check passes; on failure the previously verified version is kept.

Around the runtime, the shell also provides (each a top-level module under `src/`):

- `agents/`, `agent/`, `agent_store/` — discovery and install recipes for external agent CLIs (Codex/Claude), permission modes with audit and recovery, persistent store with migrations.
- `apps/` — local app launcher: validates app manifests, starts built projects on random loopback ports with health checks, single-instance per project, bounded concurrency; only managed Node/pnpm with fixed arguments may launch them.
- `profile/`, `projects/`, `provisioning/`, `generation/` — Profile model/repository with pending → last-known-good switching, local project registry (safe auto-created locations, recycle-bin deletion), project provisioning coordination, generation lifecycle with breaker.
- `credentials/`, `extensions/`, `mcp/` — OS credential vault (Windows Credential Manager / macOS Keychain), extension manifests + import/install boundaries, MCP transport wrapper + OAuth callback validation.
- `plugin_market.rs` — community plugin market: serves the bundled catalog snapshot (`plugin-catalog/plugins.json`, generated from the fixed `awesome-dsh-plugin` source by `scripts/build-plugin-catalog.mjs`) and installs community plugins through the managed CLI (`dsh plugin --profile desktop add`). Install targets must be repo URLs registered in the catalog; subprocesses use a minimal environment whitelist (no secret-shaped vars) with bounded output and timeout; job state is pollable.
- `window.rs`, `navigation.rs`, `tray.rs`, `app_update/` — managed-origin window control, restricted top-level navigation (external HTTPS goes to the system browser), tray, updater with signature verification.

The renderer-visible command surface is enumerated in one place — the `renderer_commands!` macro in `lib.rs` — with tests asserting registration; commands live in `commands.rs`. The capability file (`src-tauri/capabilities/bootstrap.json`) grants `core:default` only.

### 3. Packages (`packages/`)

Root `package.json` is an npm workspace (`packages/*`). Design docs and plans live in `docs/superpowers/` and `docs/architecture/`.

- **`dsh-plugin-desktop`** (`@dsh/desktop-plugin`) — the Client plugin (`src/client/`) injects `slots/sessions/theme/workspaces` and activates only when the URL carries the desktop-mode query params appended by `window.rs` (`dsh-desktop-mode=advanced` plus generation/session ids). It composes the official workbench into the desktop three-column layout and adds the local projects page, Profile settings, extension center, and model/agent center. The package root `src/index.ts` is a stub shell plugin.
- **`dsh-agent-adapter`** (`@dsh/agent-adapter`) — agent protocol adapters (Codex app-server/CLI/SDK, Claude Agent SDK/CLI dev-mode, plus a mock for tests), model providers (Anthropic-style and OpenAI-compatible HTTP), MCP client/transports/OAuth, credential redaction, and error recovery. Ships the `dsh-agent-worker` bin.

## Security model — hard constraints

These boundaries are the point of the design; do not weaken them casually:

- The renderer command surface is centrally enumerated (`renderer_commands!` in `src-tauri/src/lib.rs`) and the capability file grants `core:default` only. The workbench iframe and launched local apps do not receive unrestricted Tauri IPC; local actions flow through the typed, action-whitelisted bridge.
- `window.rs` only accepts the exact managed `127.0.0.1:<port>` origin before it is passed to the workbench iframe.
- Community market: only plugins whose GitHub HTTPS repo URL matches the bundled catalog snapshot can be installed; installation always goes through the managed CLI with a fixed Node entry, fixed argument array, `shell: false`, a minimal environment whitelist, and bounded output/timeout.
- Local app launching validates app manifests against project directories (no path escape), binds only to random `127.0.0.1` ports, and caps concurrent apps.
- Top-level navigation allows only managed local pages; verified external HTTPS addresses open in the system browser; other protocols are rejected.
- Runtime manifests are Ed25519-signed and download artifacts are SHA-256-verified; a new version activates only after a full health check, with rollback otherwise. Diagnostic exports redact tokens, auth headers, and sensitive env vars.
