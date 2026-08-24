import type { ProviderMetadata, ProviderState } from './state'

export interface AgentCardProps {
  provider: ProviderMetadata
  state: ProviderState
  cliStatus?: { path?: string; version?: string; diagnostics?: Array<{ code: string; message: string }> }
}

export function AgentCard({ provider, state, cliStatus }: AgentCardProps) {
  const path = cliStatus?.path ?? '等待检测'
  return (
    <article className="dshModelAgentCard">
      <div className="dshModelAgentCardHeader">
        <div><span className="dshModelAgentAgentMark">A</span><div><h3>{provider.displayName} Agent</h3><small>{provider.providerId}:default</small></div></div>
        <span className={`dshModelAgentStatus dshModelAgentStatus-${state.kind}`}>{state.label}</span>
      </div>
      <dl className="dshModelAgentDetails"><div><dt>适配器</dt><dd>{provider.adapterProtocol}</dd></div><div><dt>CLI 路径</dt><dd title={path}>{path}</dd></div><div><dt>版本</dt><dd>{cliStatus?.version ?? '未检测'}</dd></div></dl>
      {cliStatus?.diagnostics?.[0] !== undefined && <p className="dshModelAgentCardHint">{cliStatus.diagnostics[0].message}</p>}
    </article>
  )
}
