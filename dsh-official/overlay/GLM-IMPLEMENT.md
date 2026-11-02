# GLM 5.3 施工说明：卡片 OAuth + 刷新模型 + 勾选显示

只改 `dsh-official/overlay/` 和必要时 `dsh-official/scripts/`、`dsh-official/README.md`。
**禁止修改** `dsh-official/deepseek-harness/**`、仓库根 `deepseek-harness/**`、`dsh-plugin-desktop/**`。

2.0.x pin 必须保持 `82a5fd61a7`（`overlay/scripts/check.ts` 已断言）。

官方桌面数据目录用 `DSH_HOME=$HOME/.dsh-official-dev`，禁止写用户正在用的 `~/.dsh`。

---

## 产品行为（设置 → 模型 → 某张提供商卡片）

官方 `settings.models.provider-card` 按 `settingsNs` 分发。`llm-pi-ai` 家族每张卡（含 `openai-codex`、`xai`、以及用户已加的 `zai`）都会渲染 overlay extras。

在 extras 里做三块，**不要去改官方 API Key 输入框源码**（做不到还不破坏同步）：

1. **OAuth 按钮**（仅 `openai-codex`、`xai`）
   - 文案：登录（OAuth）/ 等待浏览器 / 打开登录页 / 取消 / 已登录
   - 调 overlay 已有 Host：`POST /api/desktop/oauth/begin` `{provider}`
   - 轮询 `GET /api/desktop/oauth/status?provider=`
   - `url` 用 `<a target=_blank>`；若 `need-prompt` 显示输入框 + `POST /api/desktop/oauth/answer`
   - `POST /api/desktop/oauth/cancel`
   - Host 用 `ctx.get('authorization')` **可选**；没有该服务则 `phase: unavailable`，**不得让桌面起不来**
   - 凭据写入当前 `DSH_HOME` 的官方凭据库（`llm-pi-ai/openai-codex`、`llm-pi-ai/xai`）。**禁止从 ~/.dsh 拷贝 token**

2. **刷新获取最新模型**
   - 按钮已有「拉取模型列表」→ `POST /api/desktop/models/probe`
   - 补强：OAuth 成功后，用**当前 home 的凭据**拉线上列表（不要只信 pi-ai 内置 JSON）
   - Codex / xAI 若官方 `ctx.llm.discoverModels('llm-pi-ai', { provider })` 只返回目录、没有新模型：overlay 用已保存的 OAuth 自己打提供商 list 接口；失败则降级为目录 + 已有配置，**禁止 throw 到 Host 启动**
   - 结果先显示为勾选列表，**不要**一刷新就覆盖用户勾选

3. **勾选哪些模型显示**
   - 刷新结果 = 候选；勾选 = 写入该路由的 `models: [{id}]`
   - 写入必须走官方 settings 用户层（若 `ctx.get('settings')` 能写 `llm-pi-ai.providers.<id>.models` 就用它）
   - 写不了再退化为只改 **当前 DSH_HOME** 的 `profiles/desktop/cordis.patch.yml` 里 `id: llm-pi-ai` 的 `config.providers.<id>.models`（这是用户 profile，不是官网源码）
   - 未勾选的不得出现在官方模型选择器
   - 默认：刷新后预勾选「当前配置已有的 id」；若配置为空则预勾选全部候选
   - 保存后提示用户：可能要新开会话才出现在选择器

DeepSeek 卡片（`settingsNs === llm-deepseek`）只保留测试/拉取，不做 ChatGPT OAuth。

---

## 同步官网硬规则（写代码时每条都要满足）

- 零 `from '@deepseek-ai/dsh-authorization'`、零 import 官方 checkout 路径
- Host `inject` 保持 `webServer` + `connection`；authorization / llm / settings 全部 `ctx.get` 探测
- Client 只用已声明槽 `settings.models.provider-card`（`ctx.slots.inject`）；槽没了 extras 不出现，桌面仍能开
- 不把 overlay 加进根 Yarn workspace
- 不改 `cordis.patch.yml` 去 disable 官方插件
- `scripts/check.ts` 必须继续：pin、无 electron、无官方树 import、oauth 路由存在、authorization 可选

---

## 建议改的文件（均在 overlay）

| 文件 | 做什么 |
|---|---|
| `src/oauth.ts` | 已有 begin/status/answer/cancel；保持 duck-type |
| `src/oauth-providers.ts` | 白名单 `openai-codex`、`xai` |
| `src/models.ts` / `src/model-core.ts` | 刷新时带 OAuth 拉线上 id；失败降级 |
| `src/model-selection.ts`（新建） | 读/写勾选；优先 settings，否则 patch.yml |
| `src/client/ProviderCardExtras.tsx` | OAuth + 刷新 + checkbox + 保存显示 |
| `scripts/check.ts` | 勾选持久化、不碰官方树、不碰 ~/.dsh 的断言 |
| `README.md`（上级 `dsh-official/README.md`） | 同步规则已有则只补「勾选显示」一行 |

不要新建 Electron 窗口。不要拷贝 2.0.x 的 `modelProxy*`（本任务不做代理）。

---

## 验收

```bash
cd dsh-official/overlay && node --experimental-strip-types --test scripts/check.ts
```

人工（`DSH_HOME=$HOME/.dsh-official-dev` 的 Harness Dev）：

1. 设置 → 模型 → openai-codex 卡片有「登录（OAuth）」
2. 登录完成后「拉取」出现列表，可勾选，保存后新会话选择器只有勾选的
3. xai 同样
4. 关掉 overlay 或 authorization 缺失时，官方桌面仍能开，DeepSeek 账号不受影响

完成后用中文短汇报：改了哪些 overlay 文件、测了什么、**确认没改** `dsh-official/deepseek-harness/`。
