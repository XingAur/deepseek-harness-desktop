# Design QA — Titlebar and Workbench Layout

- Source visual truth: `C:\Users\xingy\AppData\Local\Temp\codex-clipboard-d7474182-fbcc-423d-b25f-26ae2b33edc6.png`
- Before-fix implementation: `C:\Users\xingy\AppData\Local\Temp\codex-clipboard-35f4ad4e-b7c7-4763-9428-d9316946b748.png`
- After-fix implementation: `C:\Users\xingy\AppData\Local\Temp\dsh-layout-qa-1215x806.png`
- Normalized source: `C:\Users\xingy\AppData\Local\Temp\dsh-reference-qa-1215x806.png`
- Combined comparison: `C:\Users\xingy\AppData\Local\Temp\dsh-layout-qa-comparison.png`
- Viewport: 1215 × 806 CSS pixels, Windows desktop, dark theme
- Source pixels: 2654 × 1838; app frame cropped to 2430 × 1612 and downsampled from 2× to 1215 × 806
- Implementation pixels: 1215 × 806 at 1× capture density
- State: source is an active conversation; implementation is the official no-workspace state, as explicitly selected in design option A

## Full-view comparison evidence

The combined comparison shows that the sidebar and official conversation surface now start directly below the permanent 30px Tauri titlebar. The earlier full-width 48px blank caption row is absent. Sidebar branding, new-session control, workspace controls, footer settings, dark surfaces, borders and upstream empty-state composition remain intact.

The source uses Electron-owned traffic lights inside its sidebar while this implementation intentionally uses a permanent full-width trusted Tauri titlebar. The active-conversation content, tabs, session log and composer in the source cannot appear in the implementation's no-workspace state without fabricating a session; option A explicitly rejected that behavior.

No focused crop was needed because the normalized 1215 × 806 combined image keeps the titlebar, sidebar, divider, empty-state controls and reference conversation controls legible at one view.

## Required fidelity surfaces

- Fonts and typography: upstream DeepSeek Harness sidebar and conversation typography remains unchanged. The local 11px title uses the established trusted-shell style. The different large copy is state-driven rather than typography drift.
- Spacing and layout rhythm: the duplicated 48/58px top reservation is removed. The local default sidebar is approximately 40px wider than the normalized reference at this viewport; this is an existing resizable preference and does not disrupt the center surface.
- Colors and visual tokens: dark backgrounds, subtle borders, selected control fill and blue action accents remain consistent with the upstream workbench. The local titlebar is intentionally a separate trusted-shell surface.
- Image quality and asset fidelity: upstream DeepSeek Harness whale/wordmark assets and icons are rendered by the official client; no replacement raster, placeholder or handmade clone was introduced.
- Copy and content: implementation copy is the official no-workspace state. Reference conversation copy is not duplicated because no real session is active.

## Findings

No actionable P0, P1 or P2 visual mismatch remains within the approved option-A scope.

- [P3] Sidebar default width is slightly wider than the normalized reference.
  - Location: `.dshDesktopFrame` first grid column.
  - Evidence: approximately 320px in the implementation versus 280px in the reference source at the normalized viewport.
  - Impact: minor density difference only; the sidebar is user-resizable and the center remains usable.
  - Follow-up: consider aligning `SIDEBAR_DEFAULT` with the upstream reference in a separate preference migration, because changing it now would violate the approved requirement to preserve existing responsive sizing.

## Comparison history

1. Before fix — P1: the implementation reserved both the outer Tauri titlebar and an inner 48px Electron caption row, visibly pushing the sidebar and conversation downward.
2. Fix — removed platform caption DOM nodes and all 48/58px surface top padding; moved resize handles to `top: 0`.
3. Post-fix — normalized combined evidence shows product surfaces beginning immediately beneath the single trusted titlebar, with no residual blank row.

## Interaction evidence

- Dragging across the centered title moved the native window by 105 × 70 pixels.
- Physical double-click changed the window from 816 × 609 restored to 3456 × 1432 maximized.
- The loopback workbench returned HTTP 200 on Runtime `0.1.5-local` with `@dsh/desktop-plugin` `0.1.3`.

## Implementation checklist

- [x] Remove duplicate platform caption elements.
- [x] Remove iframe surface top padding.
- [x] Preserve official empty and active-session state semantics.
- [x] Verify native drag and double-click behavior.
- [x] Verify the corrected plugin in a managed Runtime.

## Follow-up polish

- Optionally align the initial sidebar width with the reference after deciding how to migrate existing user preferences.

final result: passed
