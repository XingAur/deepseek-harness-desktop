import { useState } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { PluginMarket } from '../extensions/PluginMarket'
import { ExtensionErrorBoundary } from './ExtensionErrorBoundary'
import { PromptsPanel } from './PromptsPanel'

type CenterTab = 'prompts' | 'plugins' | 'mcp' | 'skills' | 'usage'

const TABS: Array<[CenterTab, string]> = [
  ['prompts', '提示词'],
  ['plugins', '插件'],
  ['mcp', 'MCP'],
  ['skills', 'Skills'],
  ['usage', '用量'],
]

export function ExtensionCenterPanel(props: { bridge: DesktopBridgeLike }) {
  const [tab, setTab] = useState<CenterTab>('prompts')
  return (
    <section className="dshExtCenter" role="complementary" aria-label="扩展中心">
      <header className="dshExtCenterHeader">
        <div>
          <p className="dshModelAgentEyebrow">DESKTOP EXTENSIONS</p>
          <h2>扩展中心</h2>
          <p>跨应用管理提示词、插件、MCP、Skills 与用量。</p>
        </div>
      </header>
      <nav className="dshExtCenterTabs" role="tablist" aria-label="扩展中心页签">
        {TABS.map(([value, label]) => (
          <button key={value} type="button" role="tab" aria-selected={tab === value} onClick={() => setTab(value)}>{label}</button>
        ))}
      </nav>
      {tab === 'prompts' && <ExtensionErrorBoundary label="提示词"><PromptsPanel bridge={props.bridge} /></ExtensionErrorBoundary>}
      {tab === 'plugins' && <ExtensionErrorBoundary label="插件"><PluginMarket bridge={props.bridge} /></ExtensionErrorBoundary>}
      {tab === 'mcp' && <p className="dshExtCenterPlaceholder">即将推出</p>}
      {tab === 'skills' && <p className="dshExtCenterPlaceholder">即将推出</p>}
      {tab === 'usage' && <p className="dshExtCenterPlaceholder">即将推出</p>}
    </section>
  )
}
