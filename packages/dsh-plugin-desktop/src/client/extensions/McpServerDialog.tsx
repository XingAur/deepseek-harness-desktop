export interface McpServerReview {
  serverId: string
  displayName: string
  transport: 'stdio' | 'http' | 'sse'
  endpoint?: string
  command?: string
  tools: Array<{ name: string; effect: 'read' | 'write' | 'external' }>
  requestedPermissions: string[]
  oauthIssuer?: string
}

interface McpServerDialogProps {
  server: McpServerReview
  onClose(): void
  onEnable(): void
}

export function McpServerDialog({ server, onClose, onEnable }: McpServerDialogProps) {
  return (
    <div className="dshModelAgentDialogBackdrop" role="presentation">
      <section className="dshModelAgentDialog dshMcpServerDialog" role="dialog" aria-modal="true" aria-label="MCP 服务审核">
        <header><div><p className="dshModelAgentEyebrow">MCP SERVER REVIEW</p><h3>{server.displayName}</h3></div><button type="button" onClick={onClose}>关闭</button></header>
        <dl className="dshExtensionReviewFacts">
          <div><dt>服务 ID</dt><dd>{server.serverId}</dd></div>
          <div><dt>传输</dt><dd>{server.transport}</dd></div>
          <div><dt>入口</dt><dd>{server.endpoint ?? server.command ?? '未声明'}</dd></div>
          <div><dt>OAuth issuer</dt><dd>{server.oauthIssuer ?? '不使用'}</dd></div>
        </dl>
        <div className="dshMcpServerSection"><strong>工具清单</strong><ul>{server.tools.length === 0 ? <li>尚未发现工具</li> : server.tools.map((tool) => <li key={tool.name}><code>{tool.name}</code><span>{tool.effect}</span></li>)}</ul></div>
        <div className="dshMcpServerSection"><strong>请求能力</strong><div className="dshMcpServerTags">{server.requestedPermissions.length === 0 ? <span>无额外能力</span> : server.requestedPermissions.map((permission) => <span key={permission}>{permission}</span>)}</div></div>
        <div className="dshModelAgentWarning" role="note">MCP 只获得这里声明并经用户批准的服务、工具和范围；不会继承 Agent 的文件、网络或进程权限，也不会读取外部配置中的密钥。</div>
        <footer><button type="button" onClick={onClose}>取消</button><button type="button" className="dshModelAgentPrimary" onClick={onEnable}>启用 MCP 服务</button></footer>
      </section>
    </div>
  )
}
