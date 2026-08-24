import type { ProviderMetadata, ProviderState } from './state'

export interface DiagnosticsPanelProps {
  providers: Array<{ provider: ProviderMetadata; state: ProviderState }>
  capabilities: Array<{ id: string; displayName: string; mutating: boolean; approvalRequired: boolean }>
  errors: string[]
}

export function DiagnosticsPanel({ providers, capabilities, errors }: DiagnosticsPanelProps) {
  return (
    <div className="dshModelAgentDiagnostics">
      <div className="dshModelAgentSummary"><strong>{providers.length}</strong><span>个 Provider 已加载</span><strong>{capabilities.filter((item) => item.mutating).length}</strong><span>个变更能力需审批</span></div>
      {errors.map((error) => <div className="dshModelAgentError" role="alert" key={error}>{error}</div>)}
      <section><h3>Provider 状态</h3>{providers.length === 0 ? <p className="dshModelAgentMuted">暂无 Provider 诊断。</p> : providers.map(({ provider, state }) => <div className="dshModelAgentDiagnosticRow" key={provider.providerId}><span>{provider.displayName}</span><span>{state.label}</span></div>)}</section>
      <section><h3>能力与审批</h3>{capabilities.length === 0 ? <p className="dshModelAgentMuted">暂无能力清单。</p> : capabilities.map((item) => <div className="dshModelAgentDiagnosticRow" key={item.id}><span>{item.displayName}</span><span>{item.approvalRequired ? '需要审批' : item.mutating ? '受管执行' : '只读'}</span></div>)}</section>
    </div>
  )
}
