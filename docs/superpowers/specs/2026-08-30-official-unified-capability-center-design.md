# Official Unified Capability Center Design

## Status

Approved direction on 2026-08-30. This document records the implementation contract for the plugin market, Skills, MCP, external connections, diagnostics, and their official-style presentation. Code implementation starts only after this written specification is reviewed.

## Goal

Give users one coherent, official-looking control plane where they can:

- browse the real plugin catalog instead of a six-item preview;
- understand and manage installed Skills;
- see plugin-provided and user-defined MCP servers together;
- add, edit, enable, disable, delete, and test custom connections;
- choose a connection type instead of being restricted to Yunxiao and GitLab;
- distinguish configuration validity, network reachability, protocol readiness, authentication state, and permission state;
- diagnose why an executor, Skill, MCP server, or connection is unavailable.

The official DeepSeek Harness Runtime remains the owner of official plugins, Skills, MCP execution, and permission semantics. The desktop layer supplies native-safe catalog access, custom connection profiles, credential references, testing, and diagnostics without creating a competing execution model.

## Confirmed Problems

### Plugin catalog

The packaged snapshot contains 1,946 entries, but the direct browser preview currently uses six hard-coded examples. That preview behavior is misleading and must be removed. Managed desktop mode already reads the packaged snapshot through the Rust bridge.

### Skills and MCP

The current pages only render a small inventory card with enable/disable and review actions. They do not communicate enough information about source, status, version, permissions, tools, compatibility, or failure reason. Plugin-provided MCP servers and maintainable connection profiles are split into separate tabs even though users understand both as ways to connect tools and data.

### Connections

The current frontend, bridge validator, workbench mapper, and Rust command layer restrict profiles to:

- kind: `mcp` or `database`;
- provider: `yunxiao`, `gitlab`, or `generic`.

This is not a genuinely custom connection system. Yunxiao and GitLab must become optional templates rather than protocol-level enums.

### Connection testing

The current test action performs a TCP reachability check only. The UI calls the action “校验配置”, then describes a successful result in a way that can be mistaken for complete readiness. Testing must report separate layers and must never promote a network-only result to authentication, protocol, or business validity.

### Diagnostics

The current capability page passes an empty Provider list to diagnostics and only summarizes approval flags. It cannot explain whether the official Runtime, an executor, a Skill, an MCP server, or an external connection is actually usable.

## Information Architecture

### Official plugin section

Keep one official `插件` settings section with three tabs:

1. `插件配置`
2. `插件列表`
3. `市场`

The market remains an official plugin tab. No second “插件市场” settings section may be introduced.

### Intelligent capability section

Reduce the current five tabs to four:

1. `执行器`
2. `技能`
3. `MCP 与连接`
4. `诊断`

`连接器` is removed as a separate tab. Database and HTTP connections appear in `MCP 与连接` with clear type and source labels.

## Plugin Market Contract

### Data source

- Managed desktop mode continues to page and search the packaged `plugin-catalog/plugins.json` snapshot through the typed Rust bridge.
- Direct browser preview loads the same generated snapshot as a read-only static asset or equivalent build-generated module.
- The six-item `PREVIEW_CATALOG` constant is removed.
- Preview mode must remain unable to install, upgrade, enable, disable, or delete real plugins.
- If the snapshot cannot be loaded, show an official-style error state with retry. Do not silently fall back to six examples.

### Interaction

- Initial page size remains bounded and paginated.
- Search covers ID, display name, category, Chinese description, and English description.
- Category counts refer to the complete snapshot, not the current page.
- Loading, empty, error, install-running, installed, and install-failed states remain explicit.
- Third-party code risk remains visible before an install action.

## Skills Contract

The Skills page uses the official extension inventory as its source of truth. Each row shows:

- display name and stable identifier;
- source: official, installed plugin, or local/custom when supported;
- enabled state;
- version or “版本未知” without inventing a version;
- compatibility or inventory error;
- review action;
- enable/disable action where the source permits it.

The detail dialog shows available source, integrity, compatibility, permission, and update facts. Missing facts are labeled “未提供” rather than hidden or fabricated.

## MCP And Connection Contract

### Unified list

One list combines these records without erasing their ownership:

- plugin-provided MCP servers from the official extension inventory;
- user-defined MCP connections;
- HTTP API connections;
- database connections;
- existing Yunxiao, GitLab, generic MCP, and database profiles through a compatibility adapter.

Every list item includes:

- name;
- type and transport;
- source label;
- enabled state;
- latest test summary;
- edit capability;
- contextual actions.

Plugin-provided entries are source-managed and cannot have their endpoint or command rewritten by the desktop page. User-defined entries can be edited. Existing legacy rows remain readable and usable; this work does not delete or rewrite them automatically.

### Add connection flow

`新增连接` opens an official-style modal. The first field is a user-controlled connection type:

- `MCP Server`
  - `stdio`
  - `HTTP`
  - `SSE`
- `HTTP API`
- `数据库`

Yunxiao and GitLab are optional templates that prefill labels and safe endpoint defaults. Choosing a template never prevents the user from choosing `自定义`.

Common fields:

- display name;
- stable profile ID generated by the host unless editing;
- enabled state;
- optional credential reference selected from secure credential storage;
- read-only policy where applicable.

Type-specific fields:

- MCP stdio: executable/command reference, bounded arguments, bounded environment-variable names, working-directory policy;
- MCP HTTP/SSE: HTTPS endpoint, with loopback HTTP allowed under the existing MCP safety rule;
- HTTP API: HTTPS base URL and optional read-only health path;
- database: connection URL without embedded username or password; credentials remain references.

Secrets, tokens, passwords, authorization headers, and environment-secret values must never cross the desktop bridge in a profile payload.

### Edit, enable, disable, and delete

- Editing reuses the add-connection modal and preserves the profile ID.
- Enable/disable is reversible and does not delete configuration.
- Delete requires a confirmation that names the exact profile.
- A profile referenced by an active Harness task cannot be deleted; the UI reports the conflict.
- Preview mode performs in-memory interaction only or disables writes with a clear explanation. It must never touch formal profile data.

### Compatibility storage

Do not destructively migrate `harness_connection_profiles`.

- Keep the existing table and actions readable for current Yunxiao, GitLab, generic MCP, and database profiles.
- Store the generalized model in a new versioned table or an equally additive store owned by the desktop host.
- Read both stores into one UI model.
- Do not copy, delete, reset, or rewrite existing rows automatically.
- A legacy profile becomes a generalized profile only after the user explicitly edits and saves that exact profile, and the original row remains recoverable until compatibility verification is complete.

## Connection Test Model

A test result contains five independent layers:

1. `配置` — required fields, URL/command shape, credential-reference shape.
2. `网络` — endpoint or process reachability.
3. `协议` — MCP initialize and `tools/list`, HTTP response contract, or an available database protocol probe.
4. `认证` — verified, failed, not configured, or not tested.
5. `权限` — requested tool/data scope and whether approval is required.

Support matrix:

- MCP HTTP/SSE/stdio: configuration, transport, MCP handshake, tool discovery, and declared permission summary.
- HTTP API: configuration, network, and optional read-only health request. Authentication is reported only when the configured probe actually validates it.
- Database: configuration and network reachability by default. Driver-level authentication or a read-only query is reported only when an installed connector supplies that probe.
- Legacy profiles: preserve current network test but label unsupported layers `未验证`.

No layer may infer success from another layer. A TCP success is not an MCP success, credential success, database query success, or business-valid result.

## Diagnostics Contract

Diagnostics becomes a real status page with four groups:

- official Runtime and dependency-closure readiness;
- executor installation, login, credential, and compatibility state;
- Skill and MCP inventory, enablement, compatibility, and permission state;
- connection configuration, reachability, protocol, authentication, and permission state.

The summary uses counts only when backed by loaded data. The page must not hard-code an empty Provider list or claim “available” because a file exists.

Each failed item provides:

- concise failure label;
- the failed layer;
- safe, redacted explanation;
- the next relevant action, such as `重新测试`, `配置凭证`, or `查看权限`.

## Official Visual Fidelity Contract

The visual target is the official DeepSeek Harness settings surface captured at:

`/Users/lym/.codex/visualizations/2026/08/30/01a05094-96cd-7851-9148-c4d923b7bd86/official-04-plugin-config.png`

This is a hard acceptance contract, not loose inspiration.

### Structure and geometry

- Keep the official settings dialog frame, left navigation width, content width, title alignment, top action placement, internal scrolling, and close control.
- Content remains within the official approximately 760px settings content region.
- Use the official compact header, explanatory copy, underline tabs, disclosure/list rows, and modal patterns.
- Do not introduce dashboard hero blocks, oversized metrics, dense admin tables, competing sidebars, or a second nested settings shell.

### Typography

- Reuse the official font family and inherited shell typography.
- Match official title, tab, row-title, helper-copy, metadata, and button hierarchy.
- Do not use uppercase decorative eyebrow copy unless it already exists in the matched official component.
- Chinese copy must fit without vertical letter wrapping or collapsed buttons.

### Color and surfaces

- Use existing `--dsw-*` tokens for backgrounds, labels, borders, interactive states, focus, success, warning, and error.
- Do not add a custom brand palette, gradients, glass effects, shadows, or colored dashboard cards.
- Light and dark themes must both inherit official tokens.

### Controls

- Buttons, inputs, selects, tabs, toggles, status pills, list rows, and dialogs must match official height, radius, border, padding, hover, disabled, and focus-visible treatment.
- Use the official icon library already present in the workbench. Do not use emoji, text-symbol icons, handcrafted SVG, CSS art, or placeholder images.
- Destructive actions remain visually secondary until their confirmation step.

### Density and states

- Prefer official disclosure rows and compact lists over large isolated cards.
- Provide loading, empty, error, disabled, testing, success, failure, and permission-required states.
- At 1280x720, settings navigation, close control, page title, primary action, and active tab remain visible without horizontal scrolling.
- At narrower supported widths, content reflows to one column without clipped labels, overlapped controls, or vertical button text.

### Fidelity verification

- Capture the official source and implementation at the same viewport and theme.
- Place both images in the same comparison input.
- Compare dialog crop, navigation width, title baseline, tab geometry, row height, spacing, borders, radii, typography, controls, and empty/error states.
- Fix every actionable P0, P1, and P2 mismatch before handoff.
- Update `design-qa.md` with the new source, implementation captures, comparison history, interaction checks, and remaining evidence limits.

## Error And Safety Handling

- Catalog failure does not crash the settings dialog.
- One bad Skill, MCP server, or connection does not hide the rest of the inventory.
- Tests are bounded by timeout and output-size limits.
- Error output is redacted and never includes secrets or full environment values.
- Remote MCP requires HTTPS except loopback HTTP under the existing rule.
- Stdio commands are treated as local code execution and require explicit source/permission review before enablement.
- Plugin installation remains an execution-risk action and uses the existing managed install path.
- This feature does not authorize Git push, deployment, Yunxiao write, database write, or any external mutation.

## Component Boundaries

- `PluginCatalogSource`: shared read-only catalog paging/searching contract for managed and preview modes.
- `SkillInventoryView`: renders official Skill inventory and review state.
- `UnifiedConnectionRegistry`: merges official/plugin MCP inventory, generalized custom profiles, and legacy profiles into view models.
- `ConnectionEditorDialog`: type-driven add/edit form with no persistence knowledge.
- `ConnectionTestPresenter`: renders the five-layer test result without inferring unsupported success.
- `CapabilityDiagnosticsView`: combines Runtime, executor, extension, and connection status.
- Typed bridge and Rust commands: validate, persist, test, and return bounded result objects.

These units must remain independently testable and must not be collapsed into `ModelAgentCenter.tsx` or `ConnectionProfilesPanel.tsx`.

## Test Strategy

Implementation follows red-green-refactor.

### Plugin market

- Failing test: preview reports the full snapshot count and does not contain a six-item constant.
- Search, category, pagination, loading, empty, malformed response, and retry tests.
- Managed install states remain covered.

### Information architecture

- Failing test: no separate `连接器` tab exists.
- `MCP 与连接` contains plugin MCP, custom MCP, HTTP API, database, and legacy rows.
- Official `模型` and `Agent 预设` ownership is not duplicated.

### Connection editor and bridge

- Add, edit, enable, disable, delete-confirmation, and conflict tests.
- Custom template and fully custom type tests.
- Exact payload allowlist tests and secret-rejection tests.
- Legacy compatibility tests with no automatic row rewrite.

### Connection tests

- MCP success, protocol mismatch, timeout, malformed response, and tool-discovery tests.
- HTTP network/health success and failure tests.
- Database network-only result explicitly leaves authentication and query layers unverified.
- No test may render a TCP success as complete readiness.

### Diagnostics and UI

- Real Provider and Runtime state replaces the current hard-coded empty input.
- Loading, partial failure, no-data, permission-required, and retry states.
- Keyboard access, focus-visible, labels, status announcements, and destructive confirmation.
- Light/dark theme and responsive checks.

### Verification gates

- Targeted frontend tests for every changed behavior.
- Bridge-contract and workbench-mapper tests.
- Rust unit tests for schema, validation, redaction, timeouts, and probes.
- Plugin typecheck/build and root Web tests/build.
- Relevant Rust command and Runtime tests.
- Browser flow verification against the local read-only preview.
- Same-input visual comparison against the official source image.
- Full related diff review that preserves all pre-existing user changes.

## Acceptance Criteria

- The local preview and managed desktop both show the same 1,946-entry catalog snapshot at the current checkpoint; pagination prevents loading all cards at once.
- No six-item preview catalog remains.
- `连接器` is merged into `MCP 与连接`.
- Users can choose MCP, HTTP API, or database and are not restricted to Yunxiao/GitLab.
- Yunxiao and GitLab remain convenient templates and existing profiles remain visible.
- Add, edit, enable, disable, delete, and test flows work through typed host actions.
- MCP tests perform a real bounded handshake and tool discovery when the transport is available.
- Test results keep configuration, network, protocol, authentication, and permission states separate.
- Skills and MCP entries show real source/status facts and do not invent missing metadata.
- Diagnostics uses real loaded data and gives a safe next action for failures.
- Existing plugin, Profile, credential, connection, Runtime, session, project, and Harness data is not reset or deleted.
- The final UI matches the official settings visual contract in both structure and component styling, with no actionable P0/P1/P2 visual mismatch.
- Local implementation performs no Git push, PR creation, release, deployment, or external-system write.

## Out Of Scope

- A new visual brand or design system.
- Replacing the official plugin, Skill, MCP, permission, or execution model.
- Automatic migration or deletion of legacy profiles.
- Storing raw secrets in profile records.
- Claiming database authentication or business validity from a network-only probe.
- Publishing, deploying, or modifying external systems.
