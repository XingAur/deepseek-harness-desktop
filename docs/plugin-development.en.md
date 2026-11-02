# Overlay Plugin Development

`overlay/` is the only product code in this repository. It is an **ordinary Cordis host/client bundle** mounted into the official desktop profile. It does not modify official sources and it does not pull in Electron.

## One package, two faces

```text
overlay/
├─ package.json         # dsh.bundle.patch / dsh.client.inject / exports
├─ cordis.patch.yml     # appends the overlay row to the profile
├─ src/index.ts         # host entry → lib/index.js (ESM)
├─ src/client/index.ts  # client entry → lib/client.js (CJS)
├─ public/              # phone page assets (mobile.html/js/css)
└─ scripts/             # bundling and checks
```

Key fields:

```json
{
  "exports": { ".": "./lib/index.js", "./client": "./lib/client.js" },
  "dsh": {
    "bundle": { "patch": "./cordis.patch.yml" },
    "client": { "inject": ["@deepseek-ai/dsh-client-ui-sidebar", "..."], "platform": "web" }
  }
}
```

- `dsh.bundle.patch` makes the official loader read your patch while composing the profile.
- `dsh.client.inject` lists the official client packages you extend; `platform: "web"` marks this as a browser-side bundle.
- `cordis.patch.yml` appends only the overlay's own row and leaves everyone else's rows alone.

## Host face

Minimal skeleton:

```ts
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-connection'
import type {} from '@deepseek-ai/dsh-host-webserver'
import { readJsonBody, rejectUnauthorized, sameOriginMutatingRequest, writeJson } from './http.ts'

export const name = 'dsh-overlay-custom'
export const inject = ['webServer', 'connection']

export function applyFeature(ctx: Context): void {
  const reject = (req, res): boolean =>
    rejectUnauthorized(req, res, ctx.connection.requestRejection(req))

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/example/state',
    handler: (req, res) => {
      if (reject(req, res)) return
      if (req.method !== 'GET') {
        res.writeHead(405, { allow: 'GET', 'cache-control': 'no-store' }).end('method not allowed')
        return
      }
      writeJson(res, 200, { ok: true })
    },
  }), 'dsh-overlay-custom: example state')
}
```

Rules:

- **Required services** go in the top-level `inject`: `webServer` and `connection`. When they are missing the overlay does not start, and that degradation is acceptable.
- **Optional services** are probed with `ctx.get('name')` and never added to `inject`: `authorization` (absent → OAuth reports unavailable), `skills` and `agentPresets` (absent → the Skills page only informs). This keeps an upstream rename from taking the whole overlay down.
- **Routes** are always wrapped in `ctx.effect(...)` so the returned disposer runs on unmount. `kind` is either `exact` or `prefix`.
- **Mutations** must pass both guards: `connection.requestRejection()` for the session and `sameOriginMutatingRequest()` for same-origin. Reads only need the first.
- **Events**: `ctx.on('llm/stream', (options, next) => manager.stream(next))` takes over model request routing; `approval/request` and `user-questions/request` are mounted with `{ prepend: true }` so the phone may answer first while the desktop still can.
- **Never** cache objects across generations, and never infer the profile from `process.argv` or `ctx.baseUrl`.

## Client face

```ts
import type { Context as ClientContext } from '@deepseek-ai/cordis'

export const inject = ['slots', 'locale']

export function apply(ctx: ClientContext): void {
  ctx.effect(() => ctx.locale.register('overlay.example', { zh, en }), 'dsh-overlay-custom: locale')
  ctx.effect(() => ctx.slots.inject('settings.section', () => ctx.slots.register({
    name: 'settings.section',
    id: 'overlay-example',
    order: 96,
    label: () => ctx.locale.bind('overlay.example')('nav'),
    locale: 'overlay.example',
    inject: () => ({}) as never,
  }, ExampleSection as never)), 'dsh-overlay-custom: example settings')
}
```

Current slots are listed in the [architecture notes](architecture.en.md#how-the-overlay-is-loaded). Essentials:

- Call `slots.inject(name, …)` before `slots.register` so registration follows slot availability.
- Text goes through a `ctx.locale.register` namespace with both Chinese and English; never hardcode copy in a component.
- Style by injecting a `<style>` element (see `src/client/overlay-styles.ts`) and use `--dsw-*` theme variables instead of assuming a palette.
- Every request is a same-origin relative path (`credentials: 'same-origin'`). Never call official endpoints cross-origin.

## Bundling

```bash
cd overlay
npm run bundle:host    # esbuild → lib/index.js, platform=node, format=esm, packages=external
npm run bundle:client  # esbuild → lib/client.js, format=cjs
npm run check
```

The client bundle carries a banner:

```js
window.__ModuleLoader__.load({ id: "@deepseek-ai/dsh-overlay-custom", factory: (require) => { ... } })
```

`id` must equal `package.json` `name`. The portable build enforces this and fails otherwise, because staging renames the package to `@deepseek-ai/dsh-overlay-custom`.

`lib/` is a gitignored build artifact. Re-bundle after editing sources or the old code keeps loading.

## Data and state

- Resolve the home with `resolveDshHome()` (`DSH_HOME` first). Never import the official home-paths package.
- Write your own state to `<DSH_HOME>/overlay-custom.yaml` via `writeOverlayConfig` (0600).
- **Do not** write the official `settings.yaml`, `.credentials.yaml`, or session database. The one exception is model selection, which has to reach the official loader through the profile's `cordis.patch.yml`.
- Keep secrets out of logs: `overlay-custom.yaml` may hold credentials, but every view returned to the UI must be redacted (see `projectOverlayMcpServer`, `modelProxyConfigView`).

## Tests

```bash
node --experimental-strip-types --test overlay/scripts/check.ts
cd overlay && npm run check    # check.ts + scripts/*.test.ts
```

`scripts/check.ts` is the contract test. Besides pure-function behaviour it enforces static guards:

- every host route path and client slot id exists in the sources;
- every `/api/desktop/mobile/*` route the phone page calls is registered on the host;
- both bundles exist and `exports` points at them;
- overlay sources contain no `deepseek-harness/` path, import no `electron`, and import no official authorization package;
- packaging adaptations (for example the relaxed image preflight) stay present.

Add assertions with the feature. "It runs" is not verification — `scripts/check.ts` is the only thing CI runs.

## Prohibitions

1. Do not import the official source tree (`deepseek-harness/**`); use only public `@deepseek-ai/*` packages and types.
2. Do not import `electron` or `require('electron')`. The overlay is an ordinary web host/client plugin.
3. Do not make the official desktop depend on the overlay: a missing official service may only degrade a single entry point.
4. Do not edit official `apps/desktop` / `packages/**`. Packaging-time adaptation goes into `scripts/prepare-portable-stage.mjs` and applies to staging only.
5. Do not hijack or delete bundles and patch rows you do not own; profile repair removes legacy overlay rows only.
6. Never ship build-host absolute paths, symlinks, or development `file:` dependencies inside artifacts.

## Pre-commit checklist

- [ ] `npm run bundle:host && npm run bundle:client` re-run
- [ ] `npm run check` passes and new behaviour has assertions
- [ ] New routes use `requestRejection` plus the same-origin guard
- [ ] New copy exists in both locale dictionaries
- [ ] New state is written only to `overlay-custom.yaml`
- [ ] Manually verified once on the real official desktop (`bash scripts/start-official.sh`)
