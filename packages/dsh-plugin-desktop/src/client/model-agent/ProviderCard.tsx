import type { ProviderMetadata, ProviderState } from './state'

export interface ProviderCardProps {
  provider: ProviderMetadata
  state: ProviderState
  onConfigure(): void
  onTest(): void
}

export function ProviderCard({ provider, state, onConfigure, onTest }: ProviderCardProps) {
  return (
    <article className="dshModelAgentCard">
      <div className="dshModelAgentCardHeader">
        <div><span className="dshModelAgentProviderMark">{provider.displayName.slice(0, 1)}</span><div><h3>{provider.displayName}</h3><small>{provider.adapterProtocol}</small></div></div>
        <span className={`dshModelAgentStatus dshModelAgentStatus-${state.kind}`}>{state.label}</span>
      </div>
      <p className="dshModelAgentCardText">{provider.cliCommand === 'codex' || provider.cliCommand === 'claude'
        ? '推荐直接用官方账号登录（在主界面 Agent 页完成），API Key 只是可选的另一种方式。'
        : provider.credentialSupported ? 'API 凭证由系统安全存储托管。' : '无需在桌面端配置 API 凭证。'}</p>
      <div className="dshModelAgentCardMeta"><span>Provider：{provider.providerId}</span><span>CLI：{provider.cliCommand}</span></div>
      <div className="dshModelAgentActions">
        {provider.credentialSupported && <button type="button" onClick={onConfigure}>{state.kind === 'not-configured' ? `配置 ${provider.displayName} API Key（可选）` : `更换 ${provider.displayName} API Key`}</button>}
        {provider.credentialSupported && state.kind !== 'not-configured' && <button type="button" onClick={onTest}>测试连接</button>}
      </div>
    </article>
  )
}
