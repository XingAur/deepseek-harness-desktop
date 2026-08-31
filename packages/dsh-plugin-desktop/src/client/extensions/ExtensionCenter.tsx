import { useState } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { McpServerDialog } from './McpServerDialog'
import { ExtensionReviewDialog, type ExtensionReviewItem } from './ExtensionReviewDialog'
import { PluginMarket } from './PluginMarket'

interface ExtensionCenterProps {
  bridge: DesktopBridgeLike
  extensions: ExtensionReviewItem[]
  emptyKind?: string
  showMarket?: boolean
  onChange?(extension: ExtensionReviewItem): void
}

export function ExtensionCenter({ bridge, extensions, emptyKind = '扩展', showMarket = true, onChange }: ExtensionCenterProps) {
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
      <div className="dshModelAgentEmpty"><strong>还没有安装{emptyKind}</strong><span>前往“设置 → 插件 → 市场”安装；插件可以携带技能、MCP、智能体和工具。</span></div>
      {showMarket && <PluginMarket bridge={bridge} />}
    </>
  )
  return <>
    {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}
    <div className="dshCapabilityRows">{extensions.map((extension) => <article className="dshCapabilityRow" key={extension.extensionId}><div className="dshCapabilityRowMain"><strong>{extension.displayName}</strong><span>{extension.extensionId}</span><small><span>{sourceLabel(extension.sourceKind)}</span><span>版本未知</span><span>兼容性未提供</span><span>权限未提供</span></small></div><span className="dshCapabilityState">{extension.status === 'enabled' ? '已启用' : '已停用'}</span><div className="dshCapabilityActions"><button type="button" onClick={() => setReviewing(extension)}>查看审核</button><button type="button" onClick={() => void toggle(extension)}>{extension.status === 'enabled' ? '停用' : '启用'}</button></div></article>)}</div>
    {showMarket && <PluginMarket bridge={bridge} />}
    {reviewing !== null && reviewing.extensionKind === 'mcp'
      ? <McpServerDialog server={{ serverId: reviewing.extensionId, displayName: reviewing.displayName, transport: 'stdio', tools: [], requestedPermissions: [] }} onClose={() => setReviewing(null)} onEnable={() => { setReviewing(null); void toggle(reviewing) }} />
      : reviewing !== null && <ExtensionReviewDialog extension={reviewing} onClose={() => setReviewing(null)} />}
  </>
}

function sourceLabel(source: string): string {
  if (source === 'official') return '官方'
  if (source === 'local' || source === 'custom') return '本地 / 自定义'
  if (source === 'preview') return '预览样例'
  return '已安装插件'
}
