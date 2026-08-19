# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

DeepSeek Harness Desktop is a Tauri 2 + React + Rust desktop client for DeepSeek Harness (Windows x64, macOS Apple Silicon only). It does **not** re-implement DeepSeek Harness as a management dashboard — it downloads a signed "Managed Runtime" (Node 24 + DeepSeek Harness + pnpm), starts the official DeepSeek Harness Web UI on a random `127.0.0.1` port, and embeds it below the trusted local title bar. Desktop-specific behavior (layout, community plugin market) is delivered as ordinary DeepSeek Harness plugins bundled in the runtime.

UI strings and many comments are written in Simplified Chinese; identifiers and APIs are English. Follow that convention.

## Commands

```bash
npm ci --legacy-peer-deps        # install (legacy-peer-deps is required)

npm run check                    # full CI gate, no Rust needed:
                                 #   root vitest + plugin vitest + web build + plugin build
npm run test                     # root tests only (src/ and scripts/, jsdom)
npm run test:watch
npm run plugin:test              # @dsh/desktop-plugin tests only
npm run plugin:build

# single test file
npx vitest run src/App.test.tsx
npm run test -w @dsh/desktop-plugin -- tests/plugin-command.spec.ts

npm run tauri dev                # full dev shell; needs Rust stable + native deps
                                # + a reachable signed runtime manifest
                                #   (DSH_DESKTOP_RUNTIME_MANIFEST_URL=...)
```

Rust side lives in `src-tauri/` (`cargo check` / `cargo test` there verify Rust changes without a full installer build).

### Runtime assembly & signing

```bash
npm run runtime:build -- --target=windows-x86_64 --version=0.1.0 --url=<release-url>
node scripts/sign-manifest.mjs <unsigned.json> <out.json>   # or sign-catalog.mjs
```

Signing keys come from `DSH_DESKTOP_SIGNING_PRIVATE_KEY` / `DSH_DESKTOP_SIGNING_PUBLIC_KEY` (Ed25519 raw JWK). In-repo keys are dev-only; production keys live in GitHub Secrets and the matching public key is compiled into the Tauri shell via `DSH_DESKTOP_RELEASE_PUBLIC_KEY`. Never commit production private keys.

## Architecture

Three layers with a strict, deliberate trust boundary between them:

```
src/        React bootstrap/recovery UI   — only shown while runtime is not ready
src-tauri/  Rust Runtime Manager          — verify/download/activate/spawn/monitor
packages/dsh-plugin-desktop/              — DeepSeek Harness Host + Client plugins running INSIDE
                                           the managed Node runtime, not in Tauri
```

### 1. Bootstrap UI (`src/`)

State-machine UI driven by `runtime-reducer.ts`; `runtime-contract.ts` defines the phase/failure/event types shared with Rust (mirror of `src-tauri/src/runtime/model.rs`); `runtime-client.ts` is the Tauri IPC implementation. The trusted local shell exposes four Runtime commands and four narrowly scoped window-control commands (`src-tauri/src/commands.rs`). Once the runtime is healthy, the managed loopback workbench is embedded in an iframe below the permanent local title bar. The iframe does **not** receive Tauri IPC.

### 2. Rust Runtime Manager (`src-tauri/src/runtime/`)

`manager.rs` orchestrates the phases (checking → fetching-manifest → downloading → verifying → activating → starting → ready) as a cancellable background task, emitting progress events the React reducer consumes. Submodules: `manifest.rs` (Ed25519 signature verification), `download.rs` (resumable, SHA-256), `archive.rs` (safe extraction), `activation.rs` (versioned dirs + last-known-good rollback), `health.rs`, `process.rs` (spawn managed Node on a reserved loopback port), `diagnostics.rs` (diagnostic ZIP). Key invariant: a new runtime version only becomes active after its health check passes; on failure the previously verified version is kept.

### 3. Desktop plugin (`packages/dsh-plugin-desktop/`)

One npm workspace package exporting **two** DeepSeek Harness plugins:

- **Host plugin** (`src/index.ts` → `market-routes.ts`, `plugin-command.ts`, `catalog.ts`): injects `webServer` and registers `/api/desktop/community/*` HTTP routes inside the managed DeepSeek Harness process. Plugin writes execute the official CLI via `dsh plugin --profile desktop ...` with a fixed Node entry, argument array, and `shell: false`.
- **Client plugin** (`src/client/`): injects `slots/sessions/theme/workspaces`, activates only when the URL carries `dsh-desktop-mode=advanced` (appended by `window.rs` after health check), and composes the official workbench layout plus the market page.

Root `package.json` is an npm workspace (`packages/*`). Design docs and plans live in `docs/superpowers/`.

## Security model — hard constraints

These boundaries are the point of the design; do not weaken them casually:

- Only four Runtime commands and four window-control commands are exposed to the trusted local shell; the capability file (`src-tauri/capabilities/bootstrap.json`) grants `core:default` only.
- `window.rs` only accepts the exact managed `127.0.0.1:<port>` origin before it is passed to the workbench iframe.
- Community market: only plugins from the Ed25519-signed curated catalog (`runtime/catalog/community.json`, with last-known-good cache) can be installed; package name/version/GitHub HTTPS repo must match the catalog exactly; target platform and DeepSeek Harness semver range are checked.
- Write endpoints require a same-origin JSON POST from the exact `127.0.0.1:<port>` origin (`safeWrite` in `market-routes.ts`) and a two-step preview-token → execute flow; only one plugin write operation runs at a time.
- `PluginCommandService` validates that `DSH_DESKTOP_DSH_BIN` is the official runtime entry inside the managed tree.
