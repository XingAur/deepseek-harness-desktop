# Official-First DeepSeek Harness Integration Design

## Status

This design was requested on 2026-08-30 after reviewing the official `deepseek-ai/deepseek-harness` repository and the current desktop checkout. It supersedes the architectural direction in `docs/superpowers/plans/2026-08-28-harness-core-desktop-integration.md` and `docs/superpowers/plans/2026-08-29-harness-functional-completion.md` where those plans make the local Python HIS Harness the default or sole governance core. The old plans remain historical records and are not edited.

## Goal

Make the official DeepSeek Harness release the primary and default agent runtime of DeepSeek Harness Desktop, expose the complete supported official runtime surface, and retain the local Python HIS Harness only as an explicitly enabled experimental capability until it has independent maturity evidence.

## Current Evidence

- The desktop release source pins `@deepseek-ai/dsh` to `0.1.1-rc.2` in `release/versions.json`.
- The official repository tag inspected on 2026-08-30 is `dsh-v0.1.2-alpha.1` at commit `cd5ef8148158c3a752a658978873241fdf8e2bbc`.
- The official npm registry still exposes `0.1.1-rc.2` as the installable `@deepseek-ai/dsh` release. A Git tag or source manifest is not evidence that the matching npm package closure has been published.
- The official CLI package owns the supported dependency closure for the Web application, plan and goal modes, background jobs, Skills, MCP, subagents, workflows, scheduling, webhooks, hooks, filesystem and shell tools, sessions, approvals, questions, and related client surfaces.
- The desktop plugin currently wraps every official conversation in `HarnessChatSurface`, so the experimental HIS flow is presented as a peer of ordinary chat instead of an isolated preview.
- Runtime capability validation currently checks only the API provider, Skill, and MCP packages. It does not prove that the complete direct dependency closure declared by the official CLI package is installed and version-compatible.
- The checkout contains unrelated, uncommitted desktop, Tauri, and vendored HIS Harness changes. This integration must preserve them and must not reset, overwrite, or reformat their files unnecessarily.

## Architecture Decision

### Official Runtime Ownership

`@deepseek-ai/dsh` is the source of truth for agent execution, model selection, session lifecycle, tool orchestration, plans, goals, jobs, Skills, MCP, subagents, workflows, approvals, questions, trajectories, settings, and the Web workbench. The managed Runtime installs one exact published version and launches the official `dsh web` profile with the desktop patch layered after the official base and Web bundles.

The desktop repository does not vendor the complete official monorepo. The published CLI package already declares the supported release closure, while copying the repository would create a second package graph and a second update process. Official source remains the reference for compatibility analysis. A specific upstream file may be copied only when a released package cannot provide a required behavior, the copied file's provenance is recorded, its license notice is preserved, and a removal condition is documented.

The project tracks two explicit upstream coordinates:

- the latest official source tag and immutable commit from `https://github.com/deepseek-ai/deepseek-harness.git`;
- the exact installable npm version used by the managed Runtime.

The source coordinate may move ahead of the Runtime coordinate. That state is reported as “upstream source ahead / distribution pending”, not hidden and not converted into a fabricated npm release. A scheduled watcher checks the official repository and registry at least daily, prepares a tested update branch, and opens or refreshes a reviewable pull request. It never auto-merges, rewrites the default branch, tags, publishes, or activates an unverified Runtime. Default-branch commits without an official tag remain compatibility signals; tagged or published coordinates are the adoption boundary.

Private packages and directories under the official repository's experimental area are not part of the default completeness target. They may be evaluated later behind a separate preview decision, but they must not be represented as supported official features.

### Desktop Plugin Ownership

The desktop plugin owns only capabilities that require a native desktop host:

- Tauri window, tray, title bar, navigation, and lifecycle integration;
- managed Runtime download, signature verification, health checks, upgrade, rollback, and diagnostics;
- Profile isolation and OS credential references;
- local project discovery and the bounded local application launcher;
- native directory/file selection and typed desktop bridge operations;
- desktop-specific layout additions that use documented official slots without replacing official services.

The plugin must not replace the official conversation, model-selection, settings, Skills, MCP, plan, goal, subagent, workflow, or trajectory surfaces with parallel implementations. Existing custom Provider, Agent, plugin-center, and connection pages are retained only where they add native host behavior not available from the official workbench; duplicate surfaces are removed or redirected to the official owner.

### HIS Harness Maturity Boundary

The Python HIS Harness remains in the repository and its existing data is preserved. It is classified as experimental because its complete offline unit gate is not green and its business/runtime validity is explicitly false.

Default desktop builds and Profiles must not:

- wrap the official conversation with `HarnessChatSurface`;
- show HIS Harness as the primary task path;
- start the Python sidecar automatically;
- require the Python sidecar for official Runtime readiness;
- claim that its model, business, database, or release workflow is production-validated.

An explicit experimental build/profile flag may expose a separate “HIS Harness（实验）” entry. The entry must show its maturity and verification limitations before it can start a task. Enabling it does not grant Git push, Yunxiao write, database write, deployment, or other external mutation authority.

The first implementation keeps the vendored HIS source and existing local data intact. Normal official Runtime assembly no longer depends on rebuilding or synchronizing the HIS vendor tree. Removing the Python payload from distributable installers is a later size/packaging decision after the default-off boundary is verified; this phase does not delete it.

## Complete Official Release Contract

“Complete” means the entire supported runtime closure declared by the exact published `@deepseek-ai/dsh` package, not a hand-maintained list of three optional packages and not every private source file in the GitHub monorepo.

During Runtime assembly, a generated compatibility report must:

1. read the installed `@deepseek-ai/dsh/package.json`;
2. enumerate all direct `@deepseek-ai/*` runtime dependencies declared by that package;
3. resolve every package inside the staged Runtime without following package paths outside the Runtime root;
4. record package name, observed version, license, entrypoint presence, and compatibility status;
5. require the official base and Web bundles plus the desktop patch bundles in exact order;
6. map user-facing feature groups to their owning official packages so diagnostics can state which official capability is unavailable;
7. fail Runtime assembly when a declared official runtime dependency is missing, path-invalid, malformed, or incompatible;
8. store the report alongside the Runtime launcher and include its digest in the Runtime provenance metadata.

The feature-group map covers at least: model/provider, session and trajectory, plan and goal, jobs and scheduling, Skill, MCP, subagent, workflow, approval and user questions, filesystem and shell tools, web tools, hooks, webhook, settings, and official Web UI. The dependency-closure report remains authoritative when the upstream package adds another supported feature.

## Version and Upgrade Policy

The first source compatibility target is `dsh-v0.1.2-alpha.1` at `cd5ef8148158c3a752a658978873241fdf8e2bbc`. The first installable Runtime target remains `0.1.1-rc.2` until the official publisher exposes a verifiable `0.1.2-alpha.1` npm artifact or a separately reviewed source-build distribution contract is implemented. Because official DeepSeek Harness is still a developer preview with compatibility-breaking changes, the existing side-by-side Runtime generation, health check, last-known-good activation, and rollback flow remains mandatory.

An upstream version bump is accepted only after these gates pass against the exact package bytes:

- release-version consistency;
- official dependency-closure inspection;
- desktop Profile generation and bundle-order validation;
- Runtime session/control/Web UI identity contract;
- desktop plugin typecheck, unit tests, and build;
- root React tests and build;
- Rust tests that cover Runtime activation and data-preserving rollback.

An automated version check may prepare and push a dedicated update branch and create or refresh a pull request after its local gates pass. It must not merge the pull request, update the default branch directly, publish, tag, release, or replace the last-known-good Runtime. A human must review and merge the upgrade before the existing manual release workflow may publish it.

## Licensing and Provenance

The official repository and published packages are MIT-licensed. Consuming the published packages does not change the license of this desktop repository's own code. The integration must add a project-level third-party notice that identifies DeepSeek Harness, its exact version and source repository, preserves the DeepSeek MIT notice, and points to the packaged dependency notices.

The root repository currently has no declared project license. This design does not silently license the user's original desktop or HIS Harness code under MIT. Public distribution of that original code remains a separate owner decision. Any copied upstream source file must retain or reference its upstream MIT notice and be recorded in a machine-readable provenance file.

## Data and Security

No migration, reset, deletion, or reinitialization of Profile, Workspace, session, project, credential, cache, Runtime fallback, or HIS Harness data is allowed. The upgrade installs a new versioned Runtime, verifies it, and atomically switches only after readiness succeeds.

The official Runtime remains bound to loopback addresses. Desktop IPC remains typed and allowlisted. Official plugins, Skills, MCP servers, hooks, and workflows receive only the permissions granted by the active Profile and the official approval path. This integration does not authorize arbitrary remote plugin installation or any external-system write.

## User Experience

On normal startup, users see the official DeepSeek Harness conversation and official feature surfaces without an HIS Harness toolbar wrapped around every session. Desktop additions remain available through native slots and pages. Diagnostics show:

- exact official DSH version and source commit/tag metadata when available;
- whether the complete dependency closure passed inspection;
- each user-facing official feature group and its owning package status;
- desktop plugin version and Runtime generation;
- HIS Harness as disabled/experimental, with its separate technical-validation status if explicitly enabled.

No screen may claim “all capabilities available” solely because package files exist. Package compatibility, Runtime readiness, configured credentials, and real provider connectivity are reported separately.

## Error Handling

- A missing or incompatible official dependency fails Runtime assembly before packaging.
- A new Runtime that fails identity, control API, Web UI, or session checks is not activated.
- Failure leaves the last-known-good Runtime and all user data untouched.
- An unavailable optional external provider is reported as unconfigured or unavailable; it does not make local official capabilities disappear.
- Experimental HIS Harness failures remain isolated from official chat and cannot block normal desktop startup.
- Diagnostics use bounded, redacted error codes and never include credentials, authorization headers, or complete environment values.

## Test Strategy

Implementation follows red-green-refactor for every behavior change.

1. Add failing tests that demonstrate the current capability report accepts an incomplete official dependency closure.
2. Implement closure inspection and verify missing, wrong-version, malformed, symlinked, and path-escaping packages fail.
3. Add failing Profile tests for the new official bundle contract and implement exact official-first ordering.
4. Add failing UI tests proving normal conversation rendering does not include the HIS Harness toolbar, then restore the official conversation as the default surface.
5. Add failing tests for the explicit HIS experimental flag, maturity label, and isolated sidecar start.
6. Add failing version/preparation fixtures for `0.1.2-alpha.1`, then update the pinned version through the existing transactional version source.
7. Assemble a real staged Runtime from the exact official package and run the Runtime session/control/Web UI contract.
8. Run affected plugin, root Web, release-version, installer-contract, and Rust Runtime tests.
9. Inspect the final diff against the pre-existing dirty-file inventory and report any overlapping user-owned changes.

## Delivery Phases

### Phase 1: Official Release Compatibility Foundation

Upgrade the exact DSH version, replace the three-item capability check with complete official dependency-closure validation, generate feature-group diagnostics, and verify the official Web Profile against a real staged Runtime.

### Phase 2: Official Workbench Ownership

Remove the default `HarnessChatSurface` wrapper, audit duplicate custom surfaces, and route users to official model, settings, Skills, MCP, plan, goal, subagent, workflow, and trajectory owners. Keep native-only desktop functions.

### Phase 3: Experimental HIS Isolation

Add the explicit experimental flag and maturity notice, isolate sidecar readiness from official Runtime readiness, and preserve existing HIS source and data without presenting it as complete.

### Phase 4: License, Provenance, and Upgrade Gates

Add third-party notices and machine-readable upstream provenance, document the new ownership model, make automated upstream preparation depend on the real compatibility gates, and replace direct scheduled release mutation with a scheduled review-PR workflow.

## Acceptance Criteria

- A normal desktop Profile boots the latest verified installable official Web workbench through the managed Runtime; at the design checkpoint this is npm `0.1.1-rc.2`, while source compatibility is tracked against `dsh-v0.1.2-alpha.1`.
- Runtime assembly proves the complete supported direct dependency closure of `@deepseek-ai/dsh` is present and compatible.
- Official plan, goal, jobs, Skills, MCP, subagent, workflow, approval/questions, trajectory, settings, filesystem/shell, and Web-tool groups appear in capability diagnostics with their owning packages.
- The main conversation renders the official conversation directly and does not include an HIS Harness task toolbar by default.
- HIS Harness can be exposed only by an explicit experimental switch and is clearly labeled as not business/runtime validated.
- Profile, Workspace, session, project, credential, Runtime fallback, and HIS Harness data are byte-preserved by the upgrade path.
- Existing desktop-native project, Profile, Runtime, updater, tray, navigation, and local-app behaviors continue to pass their targeted tests.
- The repository records official source/version/license provenance without changing the license of user-owned code.
- Local integration and verification perform no Git push, tag, Release, deployment, Yunxiao write, database write, or other external mutation. The repository-installed scheduled watcher is separately authorized to push only its dedicated update branch and open or refresh an upgrade pull request; it cannot merge or publish.
