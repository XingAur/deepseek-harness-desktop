import { useState } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { McpServerDialog } from './McpServerDialog'
import { ExtensionReviewDialog, type ExtensionReviewItem } from './ExtensionReviewDialog'
import { PluginMarket } from './PluginMarket'

interface ExtensionCenterProps {
  bridge: DesktopBridgeLike
  extensions: ExtensionReviewItem[]
  onChange?(extension: ExtensionReviewItem): void
}

export function ExtensionCenter({ bridge, extensions, onChange }: ExtensionCenterProps) {
  const [reviewing, setReviewing] = useState<ExtensionReviewItem | null>(null)
  const [error, setError] = useState<string | null>(null)
  const toggle = async (extension: ExtensionReviewItem) => {
    try {
      const action = extension.status === 'enabled' ? 'extension.disable' : 'extension.enable'
      const updated = await bridge.requestV2<ExtensionReviewItem>(action, undefined, { extensionId: extension.extensionId })
      onChange?.(updated)
      setError(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '扩展状态更新失败')
    }
  }
  if (extensions.length === 0) return (
    <>
      <div className="dshModelAgentEmpty"><strong>还没有安装扩展</strong><span>在下方插件市场安装社区插件；插件、技能和 MCP 在这里完成审核后启用。</span></div>
      <PluginMarket bridge={bridge} />
    </>
  )
  return <>
    {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}
    <div className="dshModelAgentGrid">{extensions.map((extension) => <article className="dshModelAgentCard" key={extension.extensionId}><div className="dshModelAgentCardHeader"><div><h3>{extension.displayName}</h3><small>{extension.extensionKind} · {extension.sourceKind}</small></div><span className="dshModelAgentStatus dshModelAgentStatus-available">{extension.status}</span></div><p className="dshModelAgentCardText">版本、完整性、协议和权限均由宿主管理；外部配置只读导入。</p><div className="dshModelAgentCardActions"><button type="button" onClick={() => setReviewing(extension)}>查看审核</button><button type="button" onClick={() => void toggle(extension)}>{extension.status === 'enabled' ? '停用' : '启用'}</button></div></article>)}</div>
    <PluginMarket bridge={bridge} />
    {reviewing !== null && reviewing.extensionKind === 'mcp'
      ? <McpServerDialog server={{ serverId: reviewing.extensionId, displayName: reviewing.displayName, transport: 'stdio', tools: [], requestedPermissions: [] }} onClose={() => setReviewing(null)} onEnable={() => { setReviewing(null); void toggle(reviewing) }} />
      : reviewing !== null && <ExtensionReviewDialog extension={reviewing} onClose={() => setReviewing(null)} />}
  </>
}
