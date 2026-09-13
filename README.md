# DSH Desktop(个人定制版)

基于 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 的 Electron 桌面客户端**个人定制版**:
把 Harness 的本地 Web UI、Host 服务和插件系统封装成原生桌面应用,并叠加自用的远程控制与模型工具。

- 上游链:[anywhere-labs/dsh-desktop](https://github.com/anywhere-labs/dsh-desktop) → 本仓库 [LiuYuMiao-M/dsh-desktop](https://github.com/LiuYuMiao-M/dsh-desktop),独立演进
- 定位:自用为主,不提供任何官方支持承诺;安装包由本仓库 GitHub Actions 构建发布
- 中文 · [English](README.en.md)

## 下载

从 [GitHub Releases](https://github.com/LiuYuMiao-M/dsh-desktop/releases/latest) 获取:

| 平台 | 文件 | 说明 |
| --- | --- | --- |
| Windows x64 | `DSH-Desktop-<版本>-x64-Setup.exe` / `-Portable.zip` | NSIS 安装包 / 便携版 |
| macOS | `DSH.Desktop-<版本>-universal.dmg` | 未签名通用 DMG,首次打开需在「系统设置 → 隐私与安全性」允许 |

新版本请关注 Releases 页。

## 相对上游的定制内容

### 云中继远程控制
- 自有中继服务器方案([`relay-server/`](relay-server/)),手机经 HTTPS 中继访问桌面,无需公网暴露本机
- 侧栏「手机连接」入口,三态图标:黑(未开启)→ 黄(等待远程连接)→ 绿(远程已连接)
- 按次开启:应用启动后远程控制默认关闭,在配对窗口点「开启远程控制」才建隧道
- 配对二维码 + 链接复制;手机页与桌面侧栏同源:按工作区分组、会话标题一致、5 秒轮询同步,支持新建/发消息/停止任务与批准状态镜像
- 扫码请用系统相机或浏览器;微信等内置浏览器打开裸 IP 链接可能空白

### 模型工具
- 每个提供商卡片(DeepSeek 及 OpenAI 兼容的自定义路由)内置「测试连接 / 拉取模型列表」
- 拉取的模型直接回填卡片原生模型列表;测试失败透出上游错误原文并自动重试瞬时故障
- 托盘「模型诊断」窗口独立可用

### 稳定性修复
- 封闭安装(asar)环境下 Agent 预设解析误报「加载失败」
- 请求准备阶段因包清单解析失败导致的整段对话不可用
- 探针读取凭据文件失败被错误掩盖的问题

## 远程控制快速开始

1. 部署中继服务器:见 [`relay-server/README.md`](relay-server/README.md)(Node + systemd + Caddy,支持裸 IP 证书)
2. 在 `~/.dsh/settings.yaml` 配置:
   ```yaml
   dsh-desktop:
     remoteRelayOrigin: https://<你的中继地址>
   ```
3. 重启应用 → 侧栏「手机连接」→「开启远程控制」→ 手机扫码

## 开发

```bash
corepack yarn install          # Node 22+,yarn 4.18(corepack)
corepack yarn workspaces foreach run build
corepack yarn workspace dsh-plugin-desktop test
```

- 单一 stable 变体(`dsh-plugin-desktop`),当前版本 2.0.x;上游运行时以 vendored tarball 固定(见 `vendor/dsh-runtime/`)
- 门禁:`yarn check` / 各 `check:*`;发布:GitHub Actions 的 Release 工作流(Windows + macOS)
- 本地打包脚本见 `dsh-plugin-desktop/package.json`(`dist:win`、`dist:mac-smoke` 等)

## 与上游的关系

上游运行时原样固定运行,不做 API 改造;本仓库的定制集中在外壳插件层与配套服务。不承诺跟随上游版本节奏,按需手动同步。

## License

MIT(见 [LICENSE](LICENSE))。
