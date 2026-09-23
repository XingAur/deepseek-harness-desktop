# 模型账号管理：多账号库与热切换

状态：历史提案——账号管理已在 v2.0.36 落地；最终行为请以当前代码和测试为准。

[English](2026-09-16-model-account-management.md) | 中文

- 提案日期：2026-09-16
- 评审基线 commit：`5587652752`
- 落地面：`dsh-plugin-desktop/`（Beta 变体 workspace 已在 `9b1aede2ef` 退役，不存在需要先行验证的第二包）
- 约束：钉死的 `deepseek-harness/` submodule 不做任何修改；下述全部机制只使用已经存在的 Desktop 侧接缝。

## 1. 目标

用户通过托盘登录 OAuth 模型提供方（xAI、OpenAI Codex）。当前每个 provider 只有一个生效凭据，重新登录会直接覆盖。本提案增加按 provider 的账号管理：同一 provider 保留多个已登录账号、不重启即可切换生效账号，并提供托盘（快速切换）与设置页（完整管理）两个入口。

## 2. 设计依赖的既定事实

以下事实已对照评审基线验证；任何一条变化都会推翻本设计。

1. **每次模型请求都会重新读取凭据。** pi-ai 的 `Models.stream()` → `applyAuth()` → `getAuth()` → `readCredential(providerId)` 每次请求都重新读凭据存储（`pi-ai/dist/models.js`）；`llm-pi-ai` 的凭据 store 只构建一次，但每次调用都透过 `ctx` 读取（`deepseek-harness/packages/llm/llm-pi-ai/src/index.ts:194`）。**因此替换存储的记录在下一条请求即刻生效——无需重启、没有需要刷新的缓存。**
2. **OAuth token 刷新是凭据文件锁下的 read-decide-replace。** pi-ai 在 `credentials.modify()` 内执行刷新；本地 provider 用同一把锁将其与读写串行化（`credentials-local`）。走同一 `modifyRecord` 路径的记录对调不会与刷新交错。
3. **凭据记录是带 scope 键的不透明 payload。** `CredentialRecord` 为 `{ kind: 'api-key', … } | { kind: 'grant', payload: unknown }`；grant 的 `payload` 就是 pi-ai 自己的 `OAuthCredential`（`{ type: 'oauth', refresh, access, expires, …附加字段 }`），原样存储。键段必须匹配 `/^[a-z][a-z0-9-]*$/`（`credentials/src/index.ts`）。
4. **登录中继已存在且与 provider 无关。** `DesktopModelSigninService`（`dsh-plugin-desktop/src/desktop-model-signin.ts`）从 `ctx.authorization` 列出 `llm-pi-ai` scope 下的 flow，并用原生对话框运行它们。它把结果提交进固定槽位 `llm-pi-ai/<providerId>`。
5. **`.credentials.yaml` 的外部编辑会热发布**；磁盘上删除的条目无需重启即从内存消失（`credentials-local` watcher 测试）。

## 3. 数据模型

每个 provider 一个**生效槽位**，加一个 Desktop 自有的**账号库**：

| 记录键 | 归属 | 含义 |
| --- | --- | --- |
| `llm-pi-ai/<providerId>`（不变） | llm-pi-ai | 下一条请求使用的凭据。上游所有读取仍然只看这一个槽位。 |
| `dsh-desktop/<providerId>-acct-<n>` | Desktop | 已保存（非生效）账号。`payload` 是同一个 grant 记录，原样存放。 |

- 账号身份：槽位 id（`xai-acct-1`、`xai-acct-2`、…）。下一个 `n` 取现有最大值 + 1；空号可复用。
- 显示标签：派生而非存储——grant 携带 `payload.accountId` 时用它（ChatGPT/Codex 的 grant 有），否则用 `账号 N` / `Account N`。标签尽力而为：seam 把 payload 视为不透明，Desktop 不得长出自己的第二套 grant 解析器。
- 账号按 DSH home 隔离，账号库因此按 profile 隔离——切换 profile 就是切换账号库。这是现有凭据行为的自然延续，写入文档而不是改变它。

## 4. 组件

在 `main.ts` 现有 `hostCtx` 块里，与现有登录中继并列挂载一个新的 Host 服务：

```
DesktopModelAccountService
  flows()        → providers = authorization flows under scope llm-pi-ai（与登录托盘同源）
  list(provider) → { active?, saved[] } — 只含元数据，绝不含 payload
  add(provider)  → 快照 → 复用现有登录中继 → 收起新 grant，还原快照
  switch(provider, id) → 库存槽 ⇄ 生效槽对调（两次 modifyRecord）
  remove(provider, id) → deleteRecord(库存槽)
  logout(provider)      → deleteRecord(生效槽)
```

评审决策（2026-09-16）：**登出现在纳入 v1**；切换或登出后产品**提示重启**（立即有序重启或继续使用）。重启是双保险而非正确性要求——上文事实 1 已保证下一条请求即刻使用新账号；提示存在是因为用户明确要求，且长时在途请求会继续用旧账号收尾。

它像 `DesktopModelSigninService` 一样惰性读取 `ctx.get('credentials')` / `ctx.get('authorization')`；没有这些服务的 profile 只呈现空管理列表，不报错。

### 4.1 add(provider) — 唯一需要小心的流程

上游登录永远把结果提交进生效槽位，所以「保留旧账号」用 快照 → 登录 → 收起并还原 实现：

1. `before = readRecord('llm-pi-ai/<id>')`（可能不存在）。
2. 运行现有登录中继（经当前原生对话框 interaction 的 `authorization.begin`）。取消或失败不动任何记录。
3. `authorized` 后：读取此时坐在生效槽位上的新 grant，写入下一个空闲的 `dsh-desktop/<id>-acct-<n>`；若 `before` 存在，把它还原回生效槽位。结果：旧账号保持生效，新账号入库——正是需求描述的语义。
4. 首次登录（无 `before`）：新账号同时是生效账号和账号库第一条。

### 4.2 switch(provider, id)

文件锁下两次 `modifyRecord`：库存槽 → 生效槽，再原生效 → 库存槽（对调）。下一条请求从新记录解析凭据；进行中的请求用旧账号自然收尾。库存账号的 access token 过期没有关系——切过去后第一条请求走常规刷新路径，用它的 `refresh` token 续期。

## 5. 界面

### 5.1 托盘（快速切换）

`tools` 组新增一个托盘项「模型账号管理」，按 provider 渲染，与现有登录子菜单同构：每个账号一个 radio 项（勾选 = 生效），另附 添加账号… / 移除 / 退出当前账号。`registerTrayItem` 已支持惰性 `submenu()`、`radio` 项和 `refresh()`——运行时无需改动。切换或登出成功后弹一次原生对话框：「已切换账号，建议重启后生效」，按钮 [立即重启] [稍后]；立即重启走现有 `runtime.requestRestart()` 有序重启路径。

### 5.2 设置页（完整管理）

沿用现有 loopback API 模式（`desktop-settings-route.ts` + `desktop-settings-contract.ts` + 一个 client section 组件）：

| 端点 | 语义 |
| --- | --- |
| `GET /api/desktop/accounts` | providers × accounts 视图：生效 + 库存，只有标签与 id。**绝不返回记录 payload 或 token。** |
| `POST /api/desktop/accounts/switch` | `{ provider, accountId }` — 响应标记 `restartRecommended: true` |
| `POST /api/desktop/accounts/remove` | `{ provider, accountId }` |
| `POST /api/desktop/accounts/logout` | `{ provider }` — 删除生效凭据；响应标记 `restartRecommended: true` |
| `POST /api/desktop/accounts/add` | `{ provider }` — 启动同一个原生登录流（对话框弹在应用窗口上方）；页面轮询 `GET` 直到流程结束 |

安全姿态与现有 settings API 完全一致：loopback 主机名校验、same-origin 凭据 fetch、16 KiB 请求体上限、共享错误形状。列表端点刻意只返回 `pi-ai` `CredentialInfo` 同级的元数据（provider id + type）加派生标签——任何密钥都不跨过 loopback 边界。

Web 触发的登录复用原生对话框是 v1 的取舍（见开放问题 2）。

## 6. 边界情况与已知限制

| 情况 | 行为 |
| --- | --- |
| 添加期间发生刷新 | 快照与还原之间，某条请求可能刷新了旧账号（refresh token 轮换）。还原的快照可能持有已被轮换掉的 token。影响：该账号下次使用失败；恢复：重新添加一次。v1 接受并写入文档；登录中途冻结刷新需要改上游。 |
| 两次切换写入之间崩溃 | 生效槽已指向新账号，库存槽仍留有旧账号的过期副本。不丢账号；最坏情况是用户可手动移除的重复/过期库条目。 |
| 登录与切换并发 | 两者都走 `modifyRecord` 的互斥；文件锁将其串行化。 |
| Models 页路由行 | 切换从不触碰 `llm-pi-ai.providers.<id>` 设置；路由行稳定，只有它背后的凭据变化。 |
| 环境/`apiKeyEnv` 类提供方 | 不在范围内——账号库只管理已存储的 OAuth grant，即登录托盘已列出的那些 flow。 |
| 账号身份歧义 | 标签尽力而为；身份以槽位 id 为准。同一 provider 账号登录两次会产生两条库存；移除其一无害。 |

## 7. 非目标

- 不改上游 submodule；Desktop 不出现第二套 grant 解析器。
- 不支持同一 provider 两账号并发混用——每 provider 一个生效槽位是上游约束，也是产品语义（「切换」而非「混用池」）。
- v1 不做纯 Web 端 OAuth 交互（页内选方式、页内输码）；复用原生对话框流程。
- 任何面向 renderer 的响应、日志、托盘标签中不出现密钥。

## 8. 测试计划

- **服务单测**（`dsh-plugin-desktop/tests/`）：向空库/已有账号添加；取消与失败不触碰任何记录；`ALREADY_IN_FLIGHT` 呈现；切换对调且下次读取看到新 payload；切换目标缺失时 fail loud；移除只删自己的槽位；登出只删生效槽位；有无 `accountId` 的标签派生；模拟刷新并发下的 `modifyRecord` 争用。
- **路由测试**：非 loopback 拒绝、请求体超限拒绝、未知 provider/槽位 id、即使 grant 带附加字段响应形状也不含 `payload`/token 字段；切换/登出响应置位 `restartRecommended`。
- **托盘测试**：radio 状态镜像 `list()`，变更后触发 `refresh()`；切换/登出弹出重启对话框且确认路径调用 `requestRestart`。
- **Client 测试**：设置页 section 文案走类型化 locale 字典（locale-owned copy 规则），轮询在成功与失败两条路径上都收敛。
- **门禁**：`yarn workspace dsh-plugin-desktop typecheck && test`，落库前跑仓库完整 `corepack yarn check`。（beta 先行步骤与 `check:desktop-variants` 已随 Beta 变体在 `9b1aede2ef` 退役。）

## 9. 建议 PR 切分

1. `DesktopModelAccountService` + 存储辅助 + 单测（无 UI）。
2. loopback 路由 + contract 类型 + 路由测试。
3. 托盘项 + 设置页 section + 文案 + client 测试。

## 10. 待评审的开放问题

评审已定（2026-09-16）：登出纳入 v1；切换与登出后提示重启，而非只依赖热切换。

仍然开放：

1. **添加账号后谁生效？** 本设计保持旧账号生效、只收起新账号（对应「保留原来的账号，增加新的账号」）。备选——添加即切换——只是 `add()` 里一行改动。
2. **v1 的 Web 端添加复用原生对话框。** 完全 Web 原生的流程（页内选浏览器/设备码登录、页内显示确认码、经 loopback API 回答 select prompt）可行，但需要一条 pending-prompt 通道；除非 v1 就要，否则延后。
