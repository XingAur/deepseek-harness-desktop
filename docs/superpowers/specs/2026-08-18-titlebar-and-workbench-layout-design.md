# Titlebar Dragging and Workbench Layout Design

## Goal

Fix two desktop-shell integration problems without forking or duplicating the official DeepSeek Harness conversation UI:

1. The blank portion of the permanent 30px Tauri titlebar, including the centered title text, must move the native window when dragged and toggle maximization on double-click.
2. The managed workbench must start immediately below that titlebar, without the additional caption-sized blank row currently shown above the sidebar and conversation surface.

## Evidence and root causes

Tauri applies `data-tauri-drag-region` only to the element carrying the attribute. It does not automatically apply the behavior to descendants. The current titlebar relies on that attribute while also rendering child elements in the hit region, so the behavior is not reliable across the full visible blank area. Tauri's documented manual pattern uses primary-button `mousedown`, calling `startDragging()` for a single press and `toggleMaximize()` for a double press.

The trusted Tauri shell already owns a permanent 30px titlebar above the loopback iframe. The current desktop plugin additionally renders platform caption rows and applies 48px top padding to the sidebar, conversation and details surfaces. Those rules were adapted from the Electron reference application, where the web workbench itself owns the frameless native titlebar. In this Tauri architecture they reserve the titlebar twice, producing the visible empty row.

The reference screenshot shows an active conversation. The current screenshot shows the official no-workspace/no-session state. That state difference should not be hidden by fabricating a session-shaped empty page.

## Titlebar interaction design

`TitleBar` will use one explicit primary-button `mousedown` handler on the 30px header:

- Ignore events originating inside a traffic-light button.
- Ignore non-primary mouse buttons.
- When `event.detail === 2`, call `toggleMaximize()`.
- Otherwise call `startDragging()`.

The centered title has `pointer-events: none`, so its visible area resolves to the header handler. The traffic-light buttons retain their existing close, minimize and maximize actions. The declarative drag attribute may remain as harmless progressive support, but correctness and tests rely on the explicit handler.

## Workbench layout design

The permanent Tauri titlebar remains outside the sandboxed loopback iframe. Inside the iframe, `AdvancedFrame` will host only the product surfaces:

- Sidebar in column one.
- Official conversation surface in column two.
- Official details surface in column three when open.
- Shell overlay above all three columns.

It will not render macOS or Windows caption-row elements. Advanced-shell CSS will not add titlebar padding, extra grid rows, or caption drag regions. Sidebar, conversation and details content therefore begin at iframe coordinate `y = 0`, which is directly below the outer 30px titlebar.

The existing responsive sidebar sizing, details sizing, resize handles, and community-plugin entry remain. No fake conversation header, tabs, messages or composer will be introduced. The official empty state remains visible until the user selects a workspace or creates a session; an active session uses the unchanged upstream conversation UI and therefore matches the reference information architecture.

## Security boundary

Window movement continues through the trusted local React shell and its narrowly scoped Tauri command. The loopback iframe receives no Tauri IPC and cannot start window dragging or execute native window commands.

## Verification

Automated tests will prove:

- A primary single `mousedown` on blank titlebar space calls `startDragging()` once.
- A primary double `mousedown` calls `toggleMaximize()` instead of starting another drag.
- A traffic-light button does not invoke titlebar dragging.
- `AdvancedFrame` no longer renders either platform caption row.
- The injected advanced-shell stylesheet contains no platform titlebar padding or caption grid row.
- Existing sidebar, details, community market and Runtime tests remain green.

Manual Windows verification will prove:

- Dragging the blank 30px titlebar area, including across the centered title, moves a restored window.
- Double-clicking the same area toggles maximized/restored state.
- The sidebar and conversation surface start directly below the outer titlebar with no blank row.
- The official empty state still works, and an actual session renders the official active-conversation layout.
