import { useCallback, useEffect, useMemo, useState } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { CredentialDialog } from './CredentialDialog'
import { AgentCard } from './AgentCard'
import { DiagnosticsPanel } from './DiagnosticsPanel'
import { ProviderCard } from './ProviderCard'
import { AgentWorkbench } from '../agent-workbench'
import { ExtensionCenter } from '../extensions/ExtensionCenter'
import { deriveProviderState, messageOf, type ProviderDiagnostic, type ProviderMetadata, type ProviderState } from './state'

type CenterTab = 'models' | 'agents' | 'extensions' | 'diagnostics'
interface CliStatus { path?: string; version?: string; diagnostics?: Array<{ code: string; message: string }> }
interface Extension { extensionId: string; extensionKind: string; displayName: string; sourceKind: string; status: string; updatedAt: string }
interface Capability { id: string; displayName: string; mutating: boolean; approvalRequired: boolean }
interface CredentialResult { credentialId: string; status: 'configured' | 'not-configured' }

export interface ModelAgentCenterProps { bridge: DesktopBridgeLike; workspaceId?: string }

export function ModelAgentCenter({ bridge, workspaceId }: ModelAgentCenterProps) {
  const [tab, setTab] = useState<CenterTab>('models')
  const [providers, setProviders] = useState<ProviderMetadata[]>([])
  const [cli, setCli] = useState<Record<string, CliStatus>>({})
  const [capabilities, setCapabilities] = useState<Capability[]>([])
  const [extensions, setExtensions] = useState<Extension[]>([])
  const [errors, setErrors] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [credentialProvider, setCredentialProvider] = useState<ProviderMetadata | null>(null)

  const load = useCallback(async () => {
    setBusy(true)
    setErrors([])
    try {
      const [providerReply, capabilityReply, extensionReply] = await Promise.all([
        bridge.requestV2<ProviderMetadata[]>('provider.metadata.list'),
        bridge.requestV2<Capability[]>('capability.inventory'),
        bridge.requestV2<Extension[]>('extension.inventory'),
      ])
      setProviders(providerReply)
      setCapabilities(capabilityReply)
      setExtensions(extensionReply)
      const statuses = await Promise.all(providerReply.map(async (provider) => {
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

  const tabs: Array<[CenterTab, string]> = [['models', 'API 模型'], ['agents', 'Agents'], ['extensions', 'Extensions'], ['diagnostics', 'Diagnostics']]
  return (
    <section className="dshModelAgentCenter" aria-busy={busy || undefined}>
      <header className="dshModelAgentCenterHeader"><div><p className="dshModelAgentEyebrow">MODEL & AGENT CENTER</p><h2>模型与 Agent</h2><p>统一管理 API 模型、CLI Agent、扩展和运行诊断。</p></div><button type="button" disabled={busy} onClick={() => void load()}>刷新</button></header>
      <nav className="dshModelAgentTabs" role="tablist" aria-label="模型与 Agent 中心页签">
        {tabs.map(([value, label]) => <button type="button" role="tab" aria-selected={tab === value} key={value} onClick={() => setTab(value)}>{label}</button>)}
      </nav>
      {tab === 'models' && <div className="dshModelAgentGrid">{providerRows.length === 0 ? <p className="dshModelAgentMuted">暂无可用 API Provider。</p> : providerRows.map(({ provider, state }) => <ProviderCard key={provider.providerId} provider={provider} state={state} onConfigure={() => setCredentialProvider(provider)} onTest={() => void testCredential(provider)} />)}</div>}
      {tab === 'agents' && <><div className="dshModelAgentGrid">{providerRows.length === 0 ? <p className="dshModelAgentMuted">暂无可用 Agent。</p> : providerRows.map(({ provider, state }) => <AgentCard key={provider.providerId} provider={provider} state={state} cliStatus={cli[provider.providerId]} />)}</div><AgentWorkbench bridge={bridge} workspaceId={workspaceId} providerOptions={visibleProviders.map((provider) => ({ id: provider.providerId, label: provider.displayName }))} /></>}
      {tab === 'extensions' && <ExtensionCenter bridge={bridge} extensions={extensions} onChange={(updated) => setExtensions((current) => current.map((extension) => extension.extensionId === updated.extensionId ? updated : extension))} />}
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
