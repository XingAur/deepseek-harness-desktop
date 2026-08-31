# Design QA — 官方样式统一能力中心

## Comparison target

- Source visual truth: `/Users/lym/.codex/visualizations/2026/08/30/01a05094-96cd-7851-9148-c4d923b7bd86/official-04-plugin-config.png`
- User database-form references: `/var/folders/hl/v1y33b895r525l60lqgfp8n80000gn/T/codex-clipboard-68e0b70c-653c-4176-bfb2-272179b735fc.png` and `/var/folders/hl/v1y33b895r525l60lqgfp8n80000gn/T/codex-clipboard-fe8cba3b-84d6-4505-b37e-9c6cda082df5.png`
- Rendered implementation: `/Users/lym/WorkCode/ai/deepseek-harness-desktop/design-qa-implementation.png`
- Same-input comparison: the first user reference and rendered implementation were inspected together at original resolution; the user reference governs field structure only, while the official screenshot governs visual language.
- Preview URL: `http://127.0.0.1:4176/?dsh-desktop-mode=advanced&dsh-desktop-platform=darwin&dsh-desktop-parent-origin=http%3A%2F%2F127.0.0.1%3A4176&dsh-desktop-generation-id=ui-audit-current&dsh-desktop-session-id=ui-audit-current`
- State: light theme, settings dialog, `智能体能力 -> MCP 与连接`, isolated read-only preview profile.
- Viewport: rendered implementation is 1280 x 720 at density 1; the user reference remains at its original 1290 x 1578 resolution.

## Findings

- No actionable P0, P1, or P2 visual difference remains.
- The implementation intentionally contains more rows and diagnostics than the official plugin configuration reference, so the content area scrolls while the official dialog frame, navigation, header, and close controls remain fixed.

## Required fidelity surfaces

- Typography: reuses the official shell font stack, title hierarchy, compact supporting text, tab labels, row titles, and metadata sizing.
- Geometry: matches the official 800 px settings dialog, 188 px navigation column, 760 px content region, title alignment, tab placement, compact vertical rhythm, 8-10 px radii, and 1 px dividers.
- Tokens: new capability-center styles use the existing `--dsw-*` surface, border, text, focus, selected-navigation, disabled, and semantic-status tokens. No competing palette, gradient, glass treatment, or decorative shadow was introduced.
- Assets: preserves the official DeepSeek shell branding and existing icon components. No placeholder illustration, emoji, CSS art, or handcrafted SVG was added.
- Information architecture: `插件` remains the single plugin control plane; `智能体能力` contains only execution, skills, MCP/connections, and diagnostics. Official `模型` and `Agent 预设` ownership remains unchanged.
- Interaction: capability tabs, add/edit form, type/template/transport selectors, labeled fields, per-connection actions, and layered diagnostics use semantic controls. Preview mutations are disabled and explicitly described as read-only.
- Responsive resilience: long metadata does not collapse action labels, diagnostic layers span the available width, and the settings content scrolls within the official frame.

## Browser verification

- Opened `设置 -> 插件 -> 市场`; verified the complete catalog reports 1946 community plugins rather than six samples.
- Searched `project memory`; verified five matching results including `00080000/dsh-project-memory`.
- Opened `智能体能力`; verified exactly four tabs: `执行器`, `技能`, `MCP 与连接`, and `诊断`; the conflicting `连接器` tab is absent.
- Verified installed-skill search, the new-skill form, the isolated folder-import command path, and the skill-market route; the skill category exposes 82 community entries instead of six fixed samples.
- Verified plugin-managed MCP and custom MCP/HTTP API/database connections render in one list.
- Opened the add-connection dialog and verified MCP Server, HTTP API, database, custom/Yunxiao/GitLab templates, and stdio/HTTP/SSE transport choices. Yunxiao and GitLab show only their service address and personal-token fields; database shows type, host, port, database, username, password, encoding, computed address, and test query.
- Verified the connection dialog uses an independently scrolling form body with a fixed, non-overlapping action footer.
- Opened `查看审核`; verified the optimized summary, basic-information, security/compatibility, warning, close, and return surfaces.
- Verified diagnostics independently report runtime, executor, skills/MCP, capabilities, and every loaded connection without inferring success for untested layers.
- Reloaded directly into a temporary isolated preview profile; no formal profile, plugin installation, credential, or connection mutation was performed.

## Comparison history

### Pass 1 — ownership and completeness

- P1 fixed: removed the separate `连接器` ownership surface and merged connectors into `MCP 与连接`.
- P1 fixed: replaced the six-item preview market with the full 1946-item catalog, search, categories, counts, and pagination.
- P1 fixed: separated official model/preset ownership from executable agent capabilities.

### Pass 2 — connection model and diagnostics

- Added user-defined MCP/HTTP API/database profiles, templates, edit/delete/toggle/test actions, and additive v2 persistence while retaining legacy profiles.
- Added configuration/network/protocol/auth/permission diagnostic layers with explicit `未测试` and failure semantics.
- Added partial-load isolation so one failed inventory request does not hide healthy capabilities.

### Pass 3 — official visual polish

- Replaced nested card-heavy presentation with compact official rows and sections.
- Aligned content width, navigation, tabs, radii, dividers, density, status presentation, and dialog behavior with the official screenshot.
- Corrected diagnostic wrapping and connection action compression at the target viewport.
- Recompared source and implementation at identical 1280 x 720 viewports in one side-by-side input; no actionable P0/P1/P2 finding remains.

### Pass 4 — skill lifecycle and focused credentials

- Added installed-skill search, create, directory import, market navigation, and preview-safe disabled mutation states.
- Reduced Yunxiao and GitLab forms to endpoint plus personal token, with secrets stored through the system credential vault and only credential references persisted in profiles.
- Replaced the generic database form with structured database fields and an explicit PostgreSQL execution boundary for the current Harness runtime.
- Reworked the review and connection dialogs around the official shell tokens and fixed the connection footer/content overlap found during browser QA.
- Recompared the user-provided database-form reference and the current implementation together; field hierarchy is complete and the product styling remains aligned with the official Harness shell instead of copying the reference product's purple branding.

## Final result

passed
