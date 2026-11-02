# 插件生态与本仓库的关系

## 官方生态是主体

DSH 的插件生态由官方定义：官方 plugin manager（以及 `dsh plugin` 命令）管理当前 profile 里的 bundle、依赖和 patch。任何面向所有人的插件，都应该作为普通 DSH 插件发布，走官方的安装、升级与组合路径。

本仓库不提供第二个插件市场，也不替代官方 plugin manager。

## overlay 是本地补丁层

`overlay/` 是**这一个产品**的一部分，不是给别人装的插件：

| 维度 | 官方插件 | 本仓库 overlay |
|---|---|---|
| 谁安装 | 用户通过官方 plugin manager / `dsh plugin` | 由便携包构建自动注入，或开发时 `file:` 安装 |
| 谁维护 | 各插件作者，独立发版 | 本仓库，跟随官方基线一起打包 |
| 版本 | 自己的 SemVer | 打包时对齐官方版本号，没有独立发版 |
| 分发 | npm / 官方市场 / 任意来源 | 只随 `deepseek-harness-overlay-*` 产物分发 |
| 兼容承诺 | 面向公众的兼容语义 | 只承诺与 `official-lock.json` 锁定的版本组合 |
| 失败影响 | 该插件不可用 | 对应入口降级，官方桌面照常启动 |

overlay 的 `package.json` 是 `private: true`，仓库不发 npm 包，也不在插件市场登记。它在打包时被改名成 `@deepseek-ai/dsh-overlay-custom` 并塞进官方 runtime，本质上是"这个便携包自带的一个 bundle"。

## 边界怎么划

能力应该放哪，按这个顺序判断：

1. **官方已有**：不要重写。发现缺陷提给上游，而不是在 overlay 里绕开。
2. **能做成普通 DSH 插件、对所有人都有用**：做成官方插件，走官方生态发布。
3. **只有在桌面应用里、只有配合本地中继/本机凭据才有意义**：放进 overlay。
4. **必须改官方源码才能实现**：先停下来。要么上游提出需求，要么放弃——不允许在 `deepseek-harness/` 里打补丁。

overlay 目前承载的就是第 3 类：手机连接、模型卡片工具、Codex/xAI 登录入口、MCP 卡片、模型网络代理、Skills 展示。

## 为什么不把 overlay 做成通用插件

- **它依赖具体版本的官方内部服务**（`webServer`、`connection` 的调用约定、模型配置的行结构）。这种耦合适合"同一版本一起发布"，不适合"用户随便装"。
- **它写入了官方配置**（模型勾选写 profile 的 `cordis.patch.yml`）。通用插件不该猜别的 profile 结构。
- **它带一个服务端**（`relay-server/`）。这是有部署成本的产品决策，不是零配置插件能承担的东西。

如果其中某个能力后来具备了通用性，正确做法是把它抽成普通插件或推动上游实现，而不是把 overlay 包装成"生态标准"。

## 对官方插件的态度

- overlay **不修改**、不劫持、不覆盖其它 bundle 与 patch 行；profile 修补只清理历史 overlay 行。
- 用户可以在同一个 profile 里继续安装官方插件，overlay 不占用它们的插槽 id 或配置键。
- overlay 的配置全部在 `overlay-custom.yaml`，不与官方 `settings.yaml` 争用命名空间。

## 兼容性与废弃

- 只对 `official-lock.json` 指定的官方版本组合做验证。换基线时按[常见问题](faq.md#怎么换官方基线)重跑构建与 smoke。
- 官方服务或插槽改名后，overlay 的对应入口会先失效。修复方式是改 overlay 适配新接口，而不是锁死旧版本或改官方源码。
- 某个能力被官方实现后，overlay 里的对应实现应当删除，避免两套逻辑同时生效。
