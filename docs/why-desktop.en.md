# Why This Desktop Build Exists

## What the official desktop already solves

[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) ships its own desktop app (`apps/desktop`): Electron shell, windows and tray, installers, updates, profile and plugin management, and the Web UI. None of that needs to be rewritten.

So this repository no longer builds a shell: the official one already covers this ground, and the old hand-written shell is gone — see [migrating from 2.0.x](migration-from-2.0.en.md) for where its capabilities went.

## Why the repository still exists

The official desktop is a general product. A few things it does not cover, or covers awkwardly, are added by a **local overlay plugin** without touching official sources:

- **Mobile connection**: there is no cloud relay upstream. The overlay adds a channel where the desktop makes an outbound WebSocket to *your* relay and the phone talks to that relay over HTTPS, so the phone does not need to be on the same LAN.
- **Model card tools**: test a connection, fetch the live model list, and tick which models should appear in the model picker.
- **Codex / xAI sign-in**: run the official OAuth flow from the model card and use the live model list afterwards.
- **MCP management**: MCP servers as testable, enable/disable-able, importable/exportable cards instead of hand-edited YAML.
- **Model network proxy**: a system / manual / direct route applied to **model requests** only, leaving MCP and other tools alone.
- **Skills catalog**: list the skills the official composition has already discovered, so you can see what is callable.

These are "official does not have it, I use it every day" features, not a second product.

## Boundaries

| Official | Overlay |
|---|---|
| Windows, tray, installer, updates, plugin manager, account sign-in, task scheduling, terminal, Office preview, sessions and workspaces | Relay-based phone control and pairing page, model test/fetch/selection, Codex/xAI sign-in entry, MCP cards, model network proxy, Skills view |

The overlay attaches through Cordis `dsh.bundle.patch` and client slots and uses only public official services. When an official service is missing it degrades itself; the official desktop still starts.

## The costs, stated plainly

- **Unsigned / ad-hoc signed**: Windows builds trigger SmartScreen and macOS needs a manual allow. The repository has no code-signing certificate and does not intend to buy one.
- **Manual updates**: no official auto-update feed is wired in; new versions are downloaded and installed by hand.
- **Following upstream**: the overlay depends on public interfaces of official internals. When upstream refactors, an entry point may break first. The fix is to repair the overlay, never to patch the official tree.
- **Single maintainer**: this is a personal customization. No support SLA and no feature roadmap are promised.

## Deliberately out of scope

- Rewriting the official Web UI as native pages.
- Copying official sources and forking them long-term.
- Publishing the overlay to a plugin market or promising ABI compatibility to others (see [plugin ecosystem and this repository](plugin-ecosystem.en.md)).
- Writing features that look complete but are not implemented.

## Who it is for

- People who just want to install and use it: see the [user guide](user-guide.en.md).
- People who want to change the overlay: see the [architecture notes](architecture.en.md) and [overlay plugin development](plugin-development.en.md).
- People who want to know what actually changed versus official: see [official shell and overlay](official-architecture.md).
