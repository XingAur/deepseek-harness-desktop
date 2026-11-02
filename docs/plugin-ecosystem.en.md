# Plugin Ecosystem and This Repository

## The official ecosystem is the main line

DSH's plugin ecosystem is defined upstream: the official plugin manager (and the `dsh plugin` command) manages the bundles, dependencies, and patches of the current profile. Anything meant for everyone should ship as an ordinary DSH plugin and go through the official install, upgrade, and composition path.

This repository provides no second plugin market and does not replace the official plugin manager.

## The overlay is a local patch layer

`overlay/` is part of **this one product**, not a plugin for others to install:

| Dimension | Official plugin | Overlay here |
|---|---|---|
| Who installs it | The user, via the official plugin manager or `dsh plugin` | Injected automatically by the portable build, or `file:` installed in development |
| Who maintains it | Each plugin author, released independently | This repository, packaged alongside the pinned official baseline |
| Version | Its own SemVer | Aligned with the official version at packaging time; no independent release |
| Distribution | npm / official market / any source | Only inside `deepseek-harness-overlay-*` artifacts |
| Compatibility promise | Public compatibility semantics | Only the combination pinned by `official-lock.json` |
| Failure impact | That plugin is unavailable | The entry point degrades; the official desktop still starts |

The overlay's `package.json` is `private: true`; nothing is published to npm or registered in a plugin market. During packaging it is renamed to `@deepseek-ai/dsh-overlay-custom` and baked into the official runtime — in effect, "one bundle this portable build ships with".

## Where the boundary sits

Decide where a capability belongs, in this order:

1. **Official already has it**: do not rewrite it. Report defects upstream instead of routing around them in the overlay.
2. **It works as an ordinary DSH plugin and helps everyone**: ship it as an official plugin through the official ecosystem.
3. **It only makes sense inside the desktop app, with a local relay or local credentials**: put it in the overlay.
4. **It requires editing official sources**: stop. Either raise an upstream request or drop it — patching `deepseek-harness/` is not allowed.

Everything the overlay carries today is category 3: mobile connection, model-card tools, Codex/xAI sign-in entry, MCP cards, model network proxy, Skills view.

## Why the overlay is not a general-purpose plugin

- **It depends on version-specific official internals** (the calling conventions of `webServer` and `connection`, the row structure of model configuration). That coupling suits "released together with one version", not "users install it freely".
- **It writes official configuration** (model selection goes into the profile's `cordis.patch.yml`). A general plugin should not guess another profile's structure.
- **It ships a server** (`relay-server/`). That is a deployment decision with operational cost, not something a zero-config plugin can absorb.

If one of these capabilities later becomes generally useful, the right move is to extract it as an ordinary plugin or push it upstream — not to dress the overlay up as an ecosystem standard.

## Stance toward official plugins

- The overlay does not modify, hijack, or override other bundles and patch rows; profile repair only removes legacy overlay rows.
- Users can keep installing official plugins into the same profile; the overlay does not squat on their slot ids or configuration keys.
- All overlay configuration lives in `overlay-custom.yaml` and never competes for the official `settings.yaml` namespace.

## Compatibility and deprecation

- Only the official version combination in `official-lock.json` is verified. When changing the baseline, re-run the build and smoke as described in the [FAQ](faq.en.md#how-do-i-change-the-official-baseline).
- When an official service or slot is renamed, the matching overlay entry point breaks first. Fix it by adapting the overlay, never by pinning an old version or patching official sources.
- When upstream implements one of these capabilities, delete the overlay's copy so two implementations never run at once.
