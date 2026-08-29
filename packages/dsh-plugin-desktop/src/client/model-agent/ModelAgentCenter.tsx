import { useCallback, useEffect, useMemo, useState } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { CredentialDialog } from './CredentialDialog'
import { AgentHome } from '../AgentHome'
import { DiagnosticsPanel } from './DiagnosticsPanel'
import { ProviderCard } from './ProviderCard'
import { ConnectionProfilesPanel } from './ConnectionProfilesPanel'
import { deriveProviderState, messageOf, type ProviderDiagnostic, type ProviderMetadata, type ProviderState } from './state'

type CenterTab = 'models' | 'agents' | 'mcp' | 'database' | 'diagnostics'
interface CliStatus { path?: string; version?: string; diagnostics?: Array<{ code: string; message: string }> }
interface Capability { id: string; displayName: string; mutating: boolean; approvalRequired: boolean }
interface CredentialResult { credentialId: string; status: 'configured' | 'not-configured' }

export interface ModelAgentCenterProps { bridge: DesktopBridgeLike; workspaceId?: string }

export function ModelAgentCenter({ bridge, workspaceId }: ModelAgentCenterProps) {
  const [tab, setTab] = useState<CenterTab>('models')
  const [providers, setProviders] = useState<ProviderMetadata[]>([])
  const [cli, setCli] = useState<Record<string, CliStatus>>({})
  const [capabilities, setCapabilities] = useState<Capability[]>([])
  const [errors, setErrors] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [credentialProvider, setCredentialProvider] = useState<ProviderMetadata | null>(null)

  const load = useCallback(async () => {
    setBusy(true)
    setErrors([])
    try {
      const [providerReply, capabilityReply] = await Promise.all([
        bridge.requestV2<ProviderMetadata[]>('provider.metadata.list'),
        bridge.requestV2<Capability[]>('capability.inventory'),
      ])
      setProviders(providerReply)
      setCapabilities(capabilityReply)
      const statuses = await Promise.all(providerReply.map(async (provider) => {
        if (provider.kind === 'api') return [provider.providerId, {}] as const
        try {
          return [provider.providerId, await bridge.requestV2<CliStatus>('cli.path.status', undefined, { providerId: provider.providerId })] as const
        } catch (cause) {
          return [provider.providerId, { diagnostics: [{ code: 'network-error', message: messageOf(cause) }] }] as const
        }
      }))
      setCli(Object.fromEntries(statuses))
    } catch (cause) {
      setErrors([messageOf(cause)])
    } finally {
      setBusy(false)
    }
  }, [bridge])

  useEffect(() => { void load() }, [load])

  const visibleProviders = useMemo(() => providers.filter((provider) => !provider.developerOnly), [providers])
  const providerRows = useMemo(() => visibleProviders.map((provider) => {
    const diagnostic = diagnosticFor(cli[provider.providerId])
    return { provider, state: deriveProviderState(provider, diagnostic, {}) }
  }), [cli, visibleProviders])

  const testCredential = async (provider: ProviderMetadata) => {
    if (provider.credentialId === undefined) {
      setCredentialProvider(provider)
      return
    }
    try {
      await bridge.requestV2('credential.test', undefined, { credentialId: provider.credentialId })
      setErrors([])
    } catch (cause) {
      setErrors([messageOf(cause)])
    }
  }

  const onCredentialSaved = (result?: CredentialResult) => {
    if (result !== undefined && credentialProvider !== null) {
      setProviders((current) => current.map((provider) => provider.providerId === credentialProvider.providerId
        ? { ...provider, credentialId: result.credentialId, credentialStatus: result.status }
        : provider))
    }
    setCredentialProvider(null)
  }

  const tabs: Array<[CenterTab, string]> = [['models', 'API 模型'], ['agents', 'Agents'], ['mcp', 'MCP 连接维护'], ['database', '数据库维护'], ['diagnostics', 'Diagnostics']]
  return (
    <section className="dshModelAgentCenter" aria-busy={busy || undefined}>
      <header className="dshModelAgentCenterHeader"><div><p className="dshModelAgentEyebrow">MODEL & AGENT CENTER</p><h2>模型与 Agent</h2><p>统一管理 API 模型、CLI Agent、扩展和运行诊断。</p></div><button type="button" disabled={busy} onClick={() => void load()}>刷新</button></header>
      <nav className="dshModelAgentTabs" role="tablist" aria-label="模型与 Agent 中心页签">
        {tabs.map(([value, label]) => <button type="button" role="tab" aria-selected={tab === value} key={value} onClick={() => setTab(value)}>{label}</button>)}
      </nav>
      {tab === 'models' && <div className="dshModelAgentGrid">{providerRows.length === 0 ? <p className="dshModelAgentMuted">暂无可用 API Provider。</p> : providerRows.map(({ provider, state }) => <ProviderCard key={provider.providerId} provider={provider} state={state} onConfigure={() => setCredentialProvider(provider)} onTest={() => void testCredential(provider)} />)}</div>}
      {tab === 'agents' && <AgentHome bridge={bridge} workspaceId={workspaceId} />}
      {tab === 'mcp' && <ConnectionProfilesPanel bridge={bridge} kind="mcp" />}
      {tab === 'database' && <ConnectionProfilesPanel bridge={bridge} kind="database" />}
      {tab === 'diagnostics' && <DiagnosticsPanel providers={providerRows} capabilities={capabilities} errors={errors} />}
      {credentialProvider !== null && <CredentialDialog bridge={bridge} providerId={credentialProvider.providerId} providerName={credentialProvider.displayName} credentialId={credentialProvider.credentialId} onClose={onCredentialSaved} />}
    </section>
  )
}

function diagnosticFor(status: CliStatus | undefined): ProviderDiagnostic | null {
  const diagnostic = status?.diagnostics?.[0]
  if (diagnostic === undefined) return null
  if (diagnostic.code === 'not-found' || diagnostic.code === 'invalid-path' || diagnostic.code === 'non-executable') return { ...diagnostic, code: 'missing-cli' }
  if (diagnostic.code === 'version-too-old' || diagnostic.code === 'version-too-new' || diagnostic.code === 'unsupported-protocol') return diagnostic
  return { ...diagnostic, code: diagnostic.code === 'network-error' ? 'network-error' : diagnostic.code }
}
