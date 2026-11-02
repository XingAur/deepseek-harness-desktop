# overlay 插件开发

`overlay/` 是本仓库唯一的产品代码。它是一个**普通的 Cordis Host/Client bundle**，挂在官方桌面 profile 上，不修改官方源码，也不引入 Electron。

## 一个包，两个面

```text
overlay/
├─ package.json         # dsh.bundle.patch / dsh.client.inject / exports
├─ cordis.patch.yml     # 向 profile 追加 overlay 行
├─ src/index.ts         # Host 面入口 → lib/index.js（ESM）
├─ src/client/index.ts  # Client 面入口 → lib/client.js（CJS）
├─ public/              # 手机页静态资源（mobile.html/js/css）
└─ scripts/             # 打包与检查
```

关键字段：

```json
{
  "exports": { ".": "./lib/index.js", "./client": "./lib/client.js" },
  "dsh": {
    "bundle": { "patch": "./cordis.patch.yml" },
    "client": { "inject": ["@deepseek-ai/dsh-client-ui-sidebar", "..."], "platform": "web" }
  }
}
```

- `dsh.bundle.patch` 让官方 loader 在组合 profile 时读到你的 patch。
- `dsh.client.inject` 列出你要扩展的官方 Client 包；`platform: "web"` 表示这是浏览器侧 bundle。
- `cordis.patch.yml` 只追加 overlay 自己的一行，不碰别人的行。

## Host 面

最小骨架：

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

规则：

- **必需服务**写进顶层 `inject`：`webServer`、`connection`。它们缺失时 overlay 不启动，这是可接受的降级。
- **可选服务**用 `ctx.get('name')` 探测，不要写进 `inject`：例如 `authorization`（缺失时 OAuth 显示不可用）、`skills` 与 `agentPresets`（缺失时技能页只提示）。这样官方改名不会让整个 overlay 起不来。
- **路由**都用 `ctx.effect(...)` 包一层，返回的 dispose 由 Cordis 在卸载时调用。`kind` 只用 `exact` 或 `prefix`。
- **写操作**必须同时做两道检查：`connection.requestRejection()` 判会话，`sameOriginMutatingRequest()` 判同源；读操作只做前者。
- **事件**：`ctx.on('llm/stream', (options, next) => manager.stream(next))` 用于接管模型请求路由；`approval/request`、`user-questions/request` 用 `{ prepend: true }` 挂载，手机端可以先答，桌面仍可正常回答。
- **不要**缓存跨 generation 的对象；不要从 `process.argv` 或 `ctx.baseUrl` 推断 profile。

## Client 面

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

现有插槽见[架构说明](architecture.md#overlay-如何被加载)。要点：

- 先 `slots.inject(槽名, …)` 再 `slots.register`，让注册随插槽可用性一起生效。
- 文案走 `ctx.locale.register` 的命名空间，中英各一份；不要在组件里硬编码中文。
- 样式用注入 `<style>` 的方式（参考 `src/client/overlay-styles.ts`），使用 `--dsw-*` 主题变量，不要假设具体配色。
- 所有请求都是同源相对路径（`credentials: 'same-origin'`），不要跨域打官方接口。

## 打包约定

```bash
cd overlay
npm run bundle:host    # esbuild → lib/index.js，platform=node，format=esm，packages=external
npm run bundle:client  # esbuild → lib/client.js，format=cjs
npm run check
```

client bundle 带一段 banner：

```js
window.__ModuleLoader__.load({ id: "@deepseek-ai/dsh-overlay-custom", factory: (require) => { ... } })
```

`id` 必须等于 `package.json` 的 `name`。便携包构建会校验这一点，不一致直接失败——因为它在 staging 里把包改名为 `@deepseek-ai/dsh-overlay-custom`。

`lib/` 是 gitignored 的构建产物，改完源码必须重新 bundle，否则加载的还是旧代码。

## 数据与状态

- home 用 `resolveDshHome()` 自己解析（`DSH_HOME` 优先），不要 import 官方 home-paths 包。
- 自己的状态写 `<DSH_HOME>/overlay-custom.yaml`（`writeOverlayConfig`，0600）。
- **不要**写官方 `settings.yaml`、`.credentials.yaml` 或会话数据库。唯一例外是模型勾选需要影响官方 Loader，写入 profile 的 `cordis.patch.yml`。
- 密钥不落日志：`overlay-custom.yaml` 可以存凭据，但返回给前端的视图必须脱敏（参考 `projectOverlayMcpServer`、`modelProxyConfigView`）。

## 测试

```bash
node --experimental-strip-types --test overlay/scripts/check.ts
cd overlay && npm run check    # check.ts + scripts/*.test.ts
```

`scripts/check.ts` 是"契约测试"，除了断言纯函数行为，还做静态守卫：

- 每个 Host 路由路径在源码里存在，Client 注册的插槽 id 存在；
- 手机页调用的每个 `/api/desktop/mobile/*` 路由都在 Host 侧注册过；
- client bundle 与 host bundle 已构建，`exports` 指向正确；
- overlay 源码不出现 `deepseek-harness/` 路径、不 import `electron`、不 import 官方 authorization 包；
- 打包脚本里保留关键适配（例如图片预检的放开）。

新增功能时同步加断言。仅仅"跑得起来"不算验证——`scripts/check.ts` 是 CI 唯一会跑的东西。

## 禁止事项

1. 不 import 官方源码树路径（`deepseek-harness/**`），只用 `@deepseek-ai/*` 公开包与类型。
2. 不 import `electron`，不调用 `require('electron')`。overlay 是普通 Web Host/Client 插件。
3. 不让官方桌面依赖 overlay：官方服务缺失只能降级单个入口。
4. 不改官方 `apps/desktop` / `packages/**`；需要打包期适配时写进 `scripts/prepare-portable-stage.mjs`，只作用于 staging。
5. 不劫持或删除 profile 里不属于自己的 bundle 与 patch 行（profile 修补只清理历史 overlay 行）。
6. 不把构建机绝对路径、软链或开发期 `file:` 依赖带进产物。

## 提交前检查

- [ ] `npm run bundle:host && npm run bundle:client` 已重跑
- [ ] `npm run check` 通过，新增行为有对应断言
- [ ] 新路由做了 `requestRejection` + 同源检查
- [ ] 新文案中英都在 locale 命名空间里
- [ ] 新状态只写 `overlay-custom.yaml`
- [ ] 在真实官方桌面上手动验证过一次（`bash scripts/start-official.sh`）
