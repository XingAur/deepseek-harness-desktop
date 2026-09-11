# DSH Desktop 远程控制中继 / Remote-Control Relay

DSH Desktop「远程控制」功能的云中继服务器:手机通过 HTTPS 访问本服务,服务把请求经一条由桌面端发起的出站 WebSocket 隧道转发回桌面,桌面再把请求交给本机 `127.0.0.1` 上的 WebServer。手机无需与桌面处于同一局域网。

```
手机浏览器 ──HTTPS──> 中继(本服务) ──WS 隧道──> 桌面主进程 ──HTTP──> 127.0.0.1 WebServer
```

## 部署

需要 Node.js ≥ 22。推荐部署在一台有域名和 TLS 的服务器上(Caddy 自动 HTTPS 最省事):

```bash
# 1. 上传 relay-server/ 目录到服务器,例如 /opt/dsh-relay
cd /opt/dsh-relay
npm install --omit=dev

# 2. 直接试跑(默认监听 127.0.0.1:8787)
node main.mjs --host 127.0.0.1 --port 8787
```

Caddy 反代(把 relay.example.com 换成你的域名):

```caddy
relay.example.com {
    reverse_proxy 127.0.0.1:8787
}
```

systemd 服务 `/etc/systemd/system/dsh-relay.service`:

```ini
[Unit]
Description=DSH Desktop remote-control relay
After=network-online.target

[Service]
WorkingDirectory=/opt/dsh-relay
ExecStart=/usr/bin/node main.mjs --host 127.0.0.1 --port 8787
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now dsh-relay
curl https://relay.example.com/healthz   # → dsh-desktop relay
```

## 启用桌面端

在 DSH Desktop 的 `settings.yaml`(DSH_HOME 下)加入并重启应用:

```yaml
dsh-desktop:
  remoteRelayOrigin: https://relay.example.com
```

托盘菜单 → 远程控制… → 扫码或复制链接到手机。

## 协议要点

- 桌面端连接 `wss://<origin>/host`,首帧 `{"t":"hello","v":1,"pair":"<10位id>","secret":"<43位密钥>"}` 认证;同一 pair 重复连接会顶替旧连接。
- 手机访问 `https://<origin>/r/<pair>/<path>`;请求/响应以 JSON 文本帧多路复用(body base64),WebSocket 升级以原始字节帧双向桥接。
- 响应中的相对 `Location: /x` 会被改写为 `/r/<pair>/x`,保证前缀路由下重定向正确。
- 限制:单 pair 最多 64 个并发请求、单请求体 8MB、空闲 pair 保活 10 分钟;桌面隧道断开时未完成请求立即以 503 收尾。

## 安全边界

- 配对密钥与浏览器会话 cookie 都不出现在中继日志里,但**中继可见转发流量明文**(它就是 HTTP 反代的角色)。请只部署在你自己控制的服务器上,并始终走 TLS。
- 桌面端重启后配对密钥与会话 token 全部轮换,旧链接立即失效。
