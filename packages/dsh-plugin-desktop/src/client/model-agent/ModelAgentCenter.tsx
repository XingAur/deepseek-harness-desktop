import { useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { AgentHome } from '../AgentHome'
import { DiagnosticsPanel } from './DiagnosticsPanel'
import { ConnectionProfilesPanel } from './ConnectionProfilesPanel'
import type { ExtensionReviewItem } from '../extensions/ExtensionReviewDialog'
import { SkillCenter } from './SkillCenter'
import { deriveProviderState, messageOf, type ProviderMetadata } from './state'
import { normalizeConnectionProfile, type ConnectionProfile } from './connection-model'

type CenterTab = 'runners' | 'skills' | 'connections' | 'diagnostics'
interface Capability { id: string; displayName: string; mutating: boolean; approvalRequired: boolean }

export interface ModelAgentCenterProps { bridge: DesktopBridgeLike; workspaceId?: string }

export function ModelAgentCenter({ bridge, workspaceId }: ModelAgentCenterProps) {
  const [tab, setTab] = useState<CenterTab>('runners')
  const [capabilities, setCapabilities] = useState<Capability[]>([])
  const [extensions, setExtensions] = useState<ExtensionReviewItem[]>([])
  const [providers, setProviders] = useState<ProviderMetadata[]>([])
  const [connections, setConnections] = useState<ConnectionProfile[]>([])
  const [runtime, setRuntime] = useState<{ state: string } | null>(null)
  const [errors, setErrors] = useState<string[]>([])
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setBusy(true)
    setErrors([])
    try {
      const [capabilityReply, extensionReply, providerReply, runtimeReply, connectionReply] = await Promise.allSettled([
        Promise.resolve().then(() => bridge.requestV2<Capability[]>('capability.inventory')),
        Promise.resolve().then(() => bridge.requestV2<ExtensionReviewItem[]>('extension.inventory')),
        Promise.resolve().then(() => bridge.requestV2<ProviderMetadata[]>('provider.metadata.list')),
        Promise.resolve().then(() => bridge.requestV2<{ state: string }>('harness.status')),
        Promise.resolve().then(() => bridge.requestV2<ConnectionProfile[]>('harness.connection.list')),
      ])
      const failures: string[] = []
      if (capabilityReply.status === 'fulfilled') setCapabilities(capabilityReply.value)
      else failures.push(messageOf(capabilityReply.reason))
      if (extensionReply.status === 'fulfilled') setExtensions(extensionReply.value)
      else failures.push(messageOf(extensionReply.reason))
      if (providerReply.status === 'fulfilled') setProviders(providerReply.value)
      else failures.push(messageOf(providerReply.reason))
      if (runtimeReply.status === 'fulfilled') setRuntime(runtimeReply.value)
      else failures.push(messageOf(runtimeReply.reason))
      if (connectionReply.status === 'fulfilled') setConnections(connectionReply.value.map(normalizeConnectionProfile))
      else failures.push(messageOf(connectionReply.reason))
      setErrors(failures)
    } catch (cause) {
      setErrors([messageOf(cause)])
    } finally {
      setBusy(false)
    }
  }, [bridge])

  useEffect(() => { void load() }, [load])

  const skills = useMemo(() => extensions.filter((item) => item.extensionKind === 'skill'), [extensions])
  const mcp = useMemo(() => extensions.filter((item) => item.extensionKind === 'mcp'), [extensions])
  const tabs: Array<[CenterTab, string]> = [['runners', '执行器'], ['skills', '技能'], ['connections', 'MCP 与连接'], ['diagnostics', '诊断']]
  return (
    <section className="dshModelAgentCenter" aria-busy={busy || undefined}>
      <header className="dshModelAgentCenterHeader"><div><h2>智能体能力</h2><p>模型由“模型”管理，角色提示由“Agent 预设”管理；这里管理执行器、技能、MCP 和外部连接。</p></div><button type="button" disabled={busy} onClick={() => void load()}>刷新</button></header>
      {bridge.mode === 'preview' && <div className="dshPluginMarketPreview" role="note">本地只读预览使用样例能力，不读取或修改正式应用配置。</div>}
      <nav className="dshModelAgentTabs" role="tablist" aria-label="智能体能力页签">
        {tabs.map(([value, label]) => <button type="button" role="tab" aria-selected={tab === value} key={value} onClick={() => setTab(value)}>{label}</button>)}
      </nav>
      {tab === 'runners' && <AgentHome bridge={bridge} workspaceId={workspaceId} embedded />}
      {tab === 'skills' && <SkillCenter bridge={bridge} items={skills} onChange={(updated) => upsertExtension(setExtensions, updated)} />}
      {tab === 'connections' && <ConnectionProfilesPanel bridge={bridge} managedMcp={mcp} onProfilesChange={setConnections} />}
      {tab === 'diagnostics' && <DiagnosticsPanel runtime={runtime} providers={providers.map((provider) => ({ provider, state: deriveProviderState(provider, null, {}) }))} capabilities={capabilities} extensions={extensions} connections={connections} errors={errors} />}
    </section>
  )
}

function upsertExtension(setter: Dispatch<SetStateAction<ExtensionReviewItem[]>>, updated: ExtensionReviewItem) {
  setter((current) => current.some((item) => item.extensionId === updated.extensionId)
    ? current.map((item) => item.extensionId === updated.extensionId ? updated : item)
    : [...current, updated])
}
