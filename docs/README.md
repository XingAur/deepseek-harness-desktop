# DSH Desktop 文档

[English documentation](README.en.md)

本仓库只维护一个产品：**官方 DeepSeek Harness 桌面应用（`apps/desktop`）+ 本地 overlay 插件**。窗口、托盘、安装器、更新、插件管理都由官方实现负责；本仓库负责 overlay 增加的能力，以及把官方源码和 overlay 打成便携包。

- 第一次接触项目：根目录 [`README.md`](../README.md)
- 想先用起来：[用户指南](user-guide.md)
- 想改代码：先读[官方壳与 overlay 架构](official-architecture.md)，再读[架构说明](architecture.md)和[overlay 插件开发](plugin-development.md)

## 用户文档

| 文档 | 内容 |
|---|---|
| [用户指南](user-guide.md) | 安装官方应用与便携包、开启手机连接、配置中继、模型测试/拉取/勾选、Codex/xAI 登录、MCP、模型网络代理、Skills |
| [常见问题](faq.md) | 未签名包与 SmartScreen、macOS 打开方式、手动更新、`DSH_HOME` 隔离、换官方基线、overlay 排查 |
| [为什么做这个桌面版](why-desktop.md) | 官方壳 + 本地 overlay 的定位、代价，以及刻意不做的事 |
| [从 2.0.x 迁移](migration-from-2.0.md) | 旧自写壳的能力去了哪、数据怎么处理、迁移步骤 |
| [隐私政策](../PRIVACY.zh.md) | 本地数据、第三方请求与中继可见范围 |

## 开发者文档

| 文档 | 内容 |
|---|---|
| [官方壳与 overlay 架构](official-architecture.md) | 结论性说明：仓库结构、依赖方向硬规则、功能归属。**先读这篇** |
| [架构说明](architecture.md) | overlay 的注入方式、Host/Client 契约、profile 修补、官方版本锁定、便携包流水线 |
| [overlay 插件开发](plugin-development.md) | `dsh.bundle.patch`、`dsh.client.inject`、Host 服务、Client 插槽、测试与禁止项 |
| [插件生态与本仓库的关系](plugin-ecosystem.md) | 官方 plugin manager 管官方 bundle；overlay 只是本地补丁层，不发布 |
| [中继服务](../relay-server/README.md) | `relay-server/` 的部署、协议要点与安全边界 |
| [参与贡献](../CONTRIBUTING.md) | 提交与评审约定 |

## 目录约定

- `docs/official-architecture.md` 是结论文档。其它页面引用它的结论，不重复论证。
- `docs/*.en.md` 是对应中文页面的英文等义版本，两边同步修改。
- 构建、同步、打包命令的权威来源是 `scripts/` 与根 `package.json` 的 `scripts` 字段；文档只做说明，不新增语义。
- 文档不描述尚未合并进仓库的功能。写「支持」之前，先确认代码里存在对应实现。

## 迁移与历史

本仓库已改为官方优先架构：壳、安装器、更新都由官方实现负责，本仓库只保留 overlay 与便携包构建。旧版自带壳的功能去向与数据迁移见[从 2.0.x 迁移](migration-from-2.0.md)；更早的实现只在 git 历史里，不再维护，也不要按旧文档操作。
