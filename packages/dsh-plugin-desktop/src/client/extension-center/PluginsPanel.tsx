import { useCallback, useEffect, useState } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { messageOf } from '../model-agent/state'
import { ExtensionCenter } from '../extensions/ExtensionCenter'
import { PluginMarket } from '../extensions/PluginMarket'
import type { ExtensionReviewItem } from '../extensions/ExtensionReviewDialog'

/**
 * 「插件」页签内容：上方为已安装扩展管理，下方保持插件市场。
 *
 * 已装列表整体复用设置里的 ExtensionCenter（含 MCP 审核对话框、扩展审核与
 * 启用/停用动作）；本面板自持 inventory 状态，负责拉取、刷新，并在
 * ExtensionCenter 完成动作后重拉 inventory 以对齐宿主真实状态。
 */

function isExtensionList(value: unknown): value is ExtensionReviewItem[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'object' && item !== null
    && typeof (item as ExtensionReviewItem).extensionId === 'string'
    && typeof (item as ExtensionReviewItem).displayName === 'string'
    && typeof (item as ExtensionReviewItem).status === 'string')
}

export function PluginsPanel({ bridge }: { bridge: DesktopBridgeLike }) {
  const [extensions, setExtensions] = useState<ExtensionReviewItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setBusy(true)
    try {
      const inventory = await bridge.requestV2<ExtensionReviewItem[]>('extension.inventory')
      // 桥返回的清单形状异常时降级为错误状态，绝不让整个页签崩溃。
      if (!isExtensionList(inventory)) throw new Error('扩展清单响应异常，请刷新重试')
      setExtensions(inventory)
      setError(null)
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(false)
    }
  }, [bridge])

  useEffect(() => { void load() }, [load])

  const handleChange = useCallback((updated: ExtensionReviewItem) => {
    // 先局部更新保证响应，再重拉 inventory 对齐宿主真实状态。
    setExtensions((current) => current === null
      ? current
      : current.map((item) => item.extensionId === updated.extensionId ? updated : item))
    void load()
  }, [load])

  return (
    <section className="dshPluginsPanel" aria-label="已安装扩展">
      <header className="dshPluginsPanelHead">
        <div>
          <h3>已安装扩展</h3>
          <p>插件、技能和 MCP 在这里完成审核后启用；新插件来自下方市场。</p>
        </div>
        <button type="button" className="dshAgentGhostButton" disabled={busy} onClick={() => { void load() }}>
          {busy ? '加载中…' : '刷新'}
        </button>
      </header>
      {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}
      {extensions !== null
        ? <ExtensionCenter bridge={bridge} extensions={extensions} onChange={handleChange} />
        : error !== null
          ? <PluginMarket bridge={bridge} />
          : <p className="dshModelAgentMuted">正在加载已安装扩展…</p>}
    </section>
  )
}
