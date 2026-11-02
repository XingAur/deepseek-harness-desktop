# 从 2.0.x 自写壳迁移

本仓库在 2026-11 改为**官方优先架构**：不再维护自写的 Electron 壳（`dsh-plugin-desktop`，2.0.x），窗口、托盘、安装器、更新、账号、调度、终端、Office、插件管理全部交给官方 `apps/desktop`。本仓库只保留 overlay 插件与便携包构建。

## 能力去哪了

| 旧壳里的能力 | 现在 |
|---|---|
| 云中继手机控制、配对页 | overlay 保留（`relay-server/` + `/api/desktop/remote/*`、`/mobile`） |
| 模型「测试连接 / 拉取模型 / 勾选显示」 | overlay 保留 |
| Codex / xAI OAuth 登录 | overlay 保留 |
| MCP 卡片管理 | overlay 保留 |
| 模型网络代理 | overlay 保留 |
| 调度器、账号、终端、Office、插件管理 | 官方自带，overlay 不重复实现 |
| 兼容/扩展/增强窗框、托盘模式切换、profile 选择器、setup wizard、自有安装器与更新器 | 已删除，改用官方壳的窗口与安装器 |

## 数据

- 官方桌面默认仍使用 `$DSH_HOME`（通常是 `~/.dsh`），旧壳留下的会话、设置、凭据可以继续被官方桌面读取。
- 本仓库构建的便携包默认使用 `~/.dsh-official`，与 `~/.dsh` 隔离；如需共用，启动前显式设置 `DSH_HOME`。
- **不要同时运行两个版本写同一个 `DSH_HOME`**，也不要在两个版本之间手动拷贝凭据文件。

## 迁移步骤

1. 退出并卸载旧的 DSH Desktop（2.0.x）。
2. 安装官方 DeepSeek Harness 桌面版，或直接使用本仓库 Releases 里的便携包（`deepseek-harness-overlay-<版本>-win-x64-unsigned-manual.zip` / `-mac-arm64-adhoc-manual.dmg`）。
3. 如果使用官方桌面并需要中继：按 [中继部署](../relay-server/README.md) 配好服务端，在 overlay 配置里写入 `remoteRelayOrigin`，然后从侧栏「手机连接」开启。
4. 便携包已内置 overlay，无需手工安装；源码开发用 `bash scripts/install-overlay.sh` 挂到独立 `DSH_HOME`。

## 不支持的回退

本仓库不再提供 2.0.x 的更新与安装包。旧版本仍可从 git 历史与历史 Release 中获取，但不再维护，也不再接受问题修复。
