import { useCallback, useEffect, useState } from 'react'
import type { DesktopBridgeLike } from './desktop-bridge'
import { ExtensionCenter } from './extensions/ExtensionCenter'

/**
 * 左侧「插件」全页面：已装扩展 + 插件市场（awesome-dsh-plugin 目录）。
 */
export interface PluginCenterPageProps {
  bridge: DesktopBridgeLike
  onClose(): void
}

interface Extension {
  extensionId: string
  extensionKind: string
  displayName: string
  sourceKind: string
  status: string
  updatedAt: string
}

export function PluginCenterPage({ bridge, onClose }: PluginCenterPageProps) {
  const [extensions, setExtensions] = useState<Extension[]>([])

  const load = useCallback(async () => {
    try {
      setExtensions(await bridge.requestV2<Extension[]>('extension.inventory'))
    } catch {
      // 已安装清单读取失败时市场仍可用，由 ExtensionCenter 呈现空态
      setExtensions([])
    }
  }, [bridge])

  useEffect(() => { void load() }, [load])

  return (
    <section className="dshPluginCenterPage" aria-label="插件">
      <header className="dshPluginCenterHead">
        <div>
          <h2>插件</h2>
          <p>浏览与安装社区插件；已安装的插件在这里审核、启用或停用。安装即运行第三方代码，装前请先看源码。</p>
        </div>
        <button type="button" className="dshAgentGhostButton" onClick={onClose}>关闭</button>
      </header>
      <ExtensionCenter bridge={bridge} extensions={extensions} onChange={(updated) => setExtensions((current) => current.map((extension) => extension.extensionId === updated.extensionId ? updated : extension))} />
    </section>
  )
}
