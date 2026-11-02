# DSH Desktop Documentation

[中文文档](README.md)

This repository maintains exactly one product: **the official DeepSeek Harness desktop app (`apps/desktop`) plus a local overlay plugin**. Windows, tray, installer, updates, and plugin management belong to the official implementation; this repository owns what the overlay adds and the packaging that bundles official sources with the overlay.

- New to the project: start with the root [`README.en.md`](../README.en.md)
- Want to use it: read the [user guide](user-guide.en.md)
- Want to change code: read [official shell and overlay](official-architecture.md) first, then the [architecture notes](architecture.en.md) and [overlay plugin development](plugin-development.en.md)

## User documentation

| Document | Covers |
|---|---|
| [User guide](user-guide.en.md) | Installing the official app and portable builds, enabling mobile connection, configuring the relay, model test/fetch/selection, Codex/xAI sign-in, MCP, model network proxy, Skills |
| [FAQ](faq.en.md) | Unsigned builds and SmartScreen, opening on macOS, manual updates, `DSH_HOME` isolation, changing the official baseline, troubleshooting the overlay |
| [Why this desktop build](why-desktop.en.md) | The positioning and cost of "official shell + local overlay", and what it deliberately does not do |
| [Migrating from 2.0.x](migration-from-2.0.en.md) | Where the old shell's capabilities went, what happens to data, and the migration steps |
| [Privacy policy](../PRIVACY.md) | Local data, third-party requests, and what the relay can see |

## Developer documentation

| Document | Covers |
|---|---|
| [Official shell and overlay](official-architecture.md) | Conclusions: repository structure, hard dependency-direction rules, feature ownership. **Read this first** |
| [Architecture notes](architecture.en.md) | How the overlay is injected, Host/Client contracts, profile repair, official version locking, portable packaging pipeline |
| [Overlay plugin development](plugin-development.en.md) | `dsh.bundle.patch`, `dsh.client.inject`, Host services, Client slots, tests, and prohibitions |
| [Plugin ecosystem and this repository](plugin-ecosystem.en.md) | The official plugin manager owns official bundles; the overlay is a local patch layer and is not published |
| [Relay server](../relay-server/README.md) | Deploying `relay-server/`, protocol essentials, and security boundaries |
| [Contributing](../CONTRIBUTING.en.md) | Commit and review conventions |

## Conventions

- `docs/official-architecture.md` holds the conclusions. Other pages cite it instead of restating the arguments.
- `docs/*.en.md` are semantic equivalents of the Chinese pages; keep both sides in sync.
- The authoritative source for build, sync, and packaging commands is `scripts/` plus the root `package.json` `scripts` field. Documentation explains them; it never adds semantics.
- These pages do not describe features that are not merged into the repository. Before writing "supported", confirm the implementation exists.

## Migration and history

This repository has moved to an official-first architecture: the shell, installer, and updates belong to the official implementation, and this repository keeps only the overlay plus portable packaging. See [migrating from 2.0.x](migration-from-2.0.en.md) for where the old capabilities and data went; older implementations exist only in git history, are unmaintained, and should not be followed from old documentation.
