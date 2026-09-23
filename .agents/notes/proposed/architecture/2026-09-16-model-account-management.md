# Model account management: multi-account library with hot switching

Status: historical proposal — account management was implemented for v2.0.36; consult the current code and tests for final behavior.

English | [中文](2026-09-16-model-account-management.zh.md)

- Proposed: 2026-09-16
- Reviewed commit: `5587652752`
- Owner surface: `dsh-plugin-desktop/` (the Beta variant workspace was retired in `9b1aede2ef`; there is no second package to stage changes in)
- Constraint honored: the pinned `deepseek-harness/` submodule is never edited; every mechanism below uses desktop-owned seams that already exist.

## 1. Goal

Users sign in to OAuth model providers (xAI, OpenAI Codex) through the tray. Today a provider has exactly one live credential, and a new sign-in overwrites it. This proposal adds per-provider account management: keep several signed-in accounts per provider, switch the active one without restarting, and manage the library from both the tray (quick switching) and the Desktop settings page (full management).

## 2. Established facts this design relies on

These were verified against the reviewed commit; the design breaks if any of them changes.

1. **A model request re-reads its credential every time.** pi-ai's `Models.stream()` → `applyAuth()` → `getAuth()` → `readCredential(providerId)` reads the credential store fresh per request (`pi-ai/dist/models.js`), and `llm-pi-ai` builds its credential store once but reads through `ctx` per call (`deepseek-harness/packages/llm/llm-pi-ai/src/index.ts:194`). **Switching the stored record therefore takes effect on the next request — no restart, no cache to flush.**
2. **OAuth token refresh is a read-decide-replace under the credential file lock.** pi-ai runs refresh inside `credentials.modify()`; the local provider serializes it with reads (`credentials-local`). A record swap performed through the same `modifyRecord` path cannot interleave with a refresh.
3. **Credential records are opaque payloads under scoped keys.** `CredentialRecord` is `{ kind: 'api-key', … } | { kind: 'grant', payload: unknown }`; the `payload` of a grant is pi-ai's own `OAuthCredential` (`{ type: 'oauth', refresh, access, expires, …extras }`), stored verbatim. Key segments must match `/^[a-z][a-z0-9-]*$/` (`credentials/src/index.ts`).
4. **The sign-in relay already exists and is provider-agnostic.** `DesktopModelSigninService` (`dsh-plugin-desktop/src/desktop-model-signin.ts`) lists flows under the `llm-pi-ai` scope from `ctx.authorization` and runs them with native dialogs. It commits into the fixed slot `llm-pi-ai/<providerId>`.
5. **External edits to `.credentials.yaml` hot-publish**; an entry deleted on disk disappears from memory without a restart (`credentials-local` watcher tests).

## 3. Data model

One **active slot** per provider plus a desktop-owned **account library**:

| Record key | Owner | Meaning |
| --- | --- | --- |
| `llm-pi-ai/<providerId>` (unchanged) | llm-pi-ai | The credential the next request uses. Everything upstream keeps reading this one slot. |
| `dsh-desktop/<providerId>-acct-<n>` | Desktop | A saved (not active) account. `payload` is the same grant record, stored verbatim. |

- Account identity: the slot id (`xai-acct-1`, `xai-acct-2`, …). The next `n` is max-existing + 1; gaps are reusable.
- Display label: derived, never stored — `payload.accountId` when the grant carries one (ChatGPT/Codex grants do), otherwise `账号 N` / `Account N`. The label is best-effort by design; the seam treats the payload as opaque and desktop must not grow a second grant parser.
- Accounts are per DSH home, so the library is per profile — switching profiles switches libraries. This follows existing credential behavior and is documented, not changed.

## 4. Component

One new Host service beside the existing relay, mounted in `main.ts` at the same `hostCtx` block:

```
DesktopModelAccountService
  flows()        → providers = authorization flows under scope llm-pi-ai (same source as the sign-in tray)
  list(provider) → { active?, saved[] } — metadata only, never payloads
  add(provider)  → snapshot → run the existing sign-in relay → shelve new grant, restore snapshot
  switch(provider, id) → swap saved slot ⇄ active slot (two modifyRecord calls)
  remove(provider, id) → deleteRecord(saved slot)
  logout(provider)      → deleteRecord(active slot)
```

Reviewed decisions (2026-09-16): **logout is in scope for v1**, and after a switch or logout the product **prompts a restart** (immediate orderly relaunch or continue). The restart is belt-and-braces, not a correctness requirement — fact 2.1 above means the next request already picks up the new account; the prompt exists because the user asked for it and long-lived in-flight requests finish on the old account.

It reads `ctx.get('credentials')` / `ctx.get('authorization')` lazily, exactly like `DesktopModelSigninService`; a profile without those services offers an empty management list instead of failing.

### 4.1 add(provider) — the only subtle flow

The upstream sign-in always commits into the active slot, so "keep the old account" is implemented as snapshot → sign-in → shelve-and-restore:

1. `before = readRecord('llm-pi-ai/<id>')` (may be absent).
2. Run the existing sign-in relay (`authorization.begin` through the current native-dialog interaction). Cancel/failure touches nothing.
3. On `authorized`: read the new grant now sitting in the active slot; write it to the next free `dsh-desktop/<id>-acct-<n>` slot; if `before` existed, restore it into the active slot. Result: the old account stays active and the new one joins the library — the semantics the requirement names.
4. First-ever sign-in (no `before`): the new account is simultaneously active and the library's first entry.

### 4.2 switch(provider, id)

Two `modifyRecord` calls under the file lock: saved slot → active slot, then previous active → saved slot (a swap). The next request resolves auth from the new record; in-flight requests finish on the old account. A saved account whose access token expired is fine — the first request on it refreshes via the normal path, using its `refresh` token.

## 5. Surfaces

### 5.1 Tray (quick switching)

One new tray item in the `tools` group, 「模型账号管理」/ "Model accounts", rendered per provider like the existing sign-in submenu: a radio item per account (checked = active), plus 添加账号… / 移除 / 退出当前账号. `registerTrayItem` already supports lazy `submenu()` resolution with `radio` items and `refresh()` — no runtime changes. A successful switch or logout raises one native dialog: 已切换账号，建议重启后生效 with [立即重启] [稍后]; 立即重启 calls the existing `runtime.requestRestart()` orderly-relaunch path.

### 5.2 Desktop settings page (full management)

Follows the existing loopback-API pattern (`desktop-settings-route.ts` + `desktop-settings-contract.ts` + a client section component):

| Endpoint | Semantics |
| --- | --- |
| `GET /api/desktop/accounts` | Providers × accounts view: active + saved, labels and ids only. **Never returns record payloads or tokens.** |
| `POST /api/desktop/accounts/switch` | `{ provider, accountId }` — response marks `restartRecommended: true` |
| `POST /api/desktop/accounts/remove` | `{ provider, accountId }` |
| `POST /api/desktop/accounts/logout` | `{ provider }` — deletes the active credential; response marks `restartRecommended: true` |
| `POST /api/desktop/accounts/add` | `{ provider }` — starts the same native sign-in flow (dialogs appear over the app window); the page polls `GET` until the flow settles |

Security posture is identical to the existing settings API: loopback-hostname enforcement, same-origin fetch with credentials, the 16 KiB body cap, the shared error shape. The list endpoint intentionally returns the same metadata `pi-ai`'s `CredentialInfo` exposes (provider id + type) plus the derived label — no secrets cross the loopback boundary.

Native dialogs for the web-triggered sign-in are the v1 trade-off (see open question 2).

## 6. Edge cases and known limitations

| Case | Behavior |
| --- | --- |
| Refresh during add | Between snapshot and restore, a request may refresh the old account (refresh-token rotation). The restored snapshot can then hold a rotated-out token. Impact: that account fails on next use; recovery: re-add it. Accepted for v1 and surfaced in the docs; blocking refreshes mid-sign-in would require upstream changes. |
| Crash between the two switch writes | Active slot already points at the new account, the saved slot still holds a stale copy of the previous one. No account is lost; worst case is a duplicated/outdated library entry the user can remove. |
| Concurrent sign-in and switch | Both go through `modifyRecord`'s exclusion; the file lock serializes them. |
| Provider row in Models page | Switching never touches `llm-pi-ai.providers.<id>` settings; the route row is stable and only the credential behind it changes. |
| Environment/`apiKeyEnv` providers | Out of scope — the library manages stored OAuth grants, exactly the flows the sign-in tray already lists. |
| Ambiguous account identity | Labels are best-effort; slot ids are the identity. Two sign-ins of the same provider account produce two library entries; removing one is harmless. |

## 7. Non-goals

- No upstream submodule changes; no second grant parser in Desktop.
- No concurrent use of two accounts for one provider — one active slot per provider is an upstream constraint and also the product semantics ("switching", not "pooling").
- No web-rendered OAuth interaction in v1 (no in-page method selection or code entry); the native dialog flow is reused.
- No secrets in any renderer-facing response, log, or tray label.

## 8. Test plan

- **Service unit tests** (`dsh-plugin-desktop/tests/`): add onto empty library / onto existing account; add cancelled and add failed leave every record untouched; `ALREADY_IN_FLIGHT` surfaces; switch swaps and next-read sees the new payload; switch with a missing slot fails loud; remove deletes only its slot; logout deletes the active slot only; label derivation with and without `accountId`; concurrent `modifyRecord` contention during a simulated refresh.
- **Route tests**: non-loopback rejection, body-cap rejection, unknown provider/slot ids, response shape contains no `payload`/token fields even when a grant carries extras; switch/logout responses set `restartRecommended`.
- **Tray tests**: radio state mirrors `list()`, `refresh()` fires after mutations, switch/logout raise the restart dialog and its confirm path invokes `requestRestart`.
- **Client tests**: the settings section copy goes through the typed locale dictionaries (locale-owned copy rule), polling settles on both success and failure.
- **Gates**: `yarn workspace dsh-plugin-desktop typecheck && test`, then the repository `corepack yarn check` before landing. (The beta staging step and `check:desktop-variants` retired with the Beta variant in `9b1aede2ef`.)

## 9. Suggested PR split

1. `DesktopModelAccountService` + storage helpers + unit tests (no UI).
2. Loopback routes + contract types + route tests.
3. Tray item + settings-page section + locales + client tests.

## 10. Open questions for review

Resolved at review (2026-09-16): logout ships in v1; switch and logout prompt a restart instead of relying on hot-switch alone.

Still open:

1. **After adding an account, which one is active?** This design keeps the previous account active and only shelves the new one (matching the request "保留原来的账号，增加新的账号"). The alternative — add means add-and-switch — is a one-line change in `add()`.
2. **Web-triggered add reuses native dialogs in v1.** A fully web-native flow (choose browser/device login in the page, show the device code in the page, answer select prompts through the loopback API) is feasible but needs a pending-prompt channel; deferred unless wanted in v1.
