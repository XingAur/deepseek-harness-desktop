import type { ReactNode } from 'react'

export interface ExtensionReviewItem {
  extensionId: string
  extensionKind: string
  displayName: string
  sourceKind: string
  status: string
  updatedAt: string
}

interface ExtensionReviewDialogProps {
  extension: ExtensionReviewItem
  onClose(): void
}

export function ExtensionReviewDialog({ extension, onClose }: ExtensionReviewDialogProps) {
  const enabled = extension.status === 'enabled'
  return (
    <div className="dshModelAgentDialogBackdrop" role="presentation">
      <section className="dshModelAgentDialog dshExtensionReviewDialog" role="dialog" aria-modal="true" aria-labelledby="dsh-extension-review-title">
        <header><div><h3 id="dsh-extension-review-title">扩展审核详情</h3><p>{extension.displayName}</p></div><button type="button" aria-label="关闭审核详情" onClick={onClose}>×</button></header>
        <div className="dshExtensionReviewSummary"><div><strong>{extension.displayName}</strong><span>{extension.extensionId}</span></div><span className="dshCapabilityState" data-state={enabled ? 'enabled' : 'disabled'}>{enabled ? '已启用' : '已停用'}</span></div>
        <section className="dshExtensionReviewSection"><h4>基本信息</h4><dl className="dshExtensionReviewFacts">
          <Fact label="类型">{kindLabel(extension.extensionKind)}</Fact><Fact label="来源">{sourceLabel(extension.sourceKind)}</Fact><Fact label="最后更新">{formatUpdatedAt(extension.updatedAt)}</Fact>
        </dl></section>
        <section className="dshExtensionReviewSection"><h4>安全与兼容</h4><dl className="dshExtensionReviewFacts">
          <Fact label="版本">未提供</Fact><Fact label="完整性">未提供</Fact><Fact label="兼容性">未提供</Fact><Fact label="权限">未提供</Fact>
        </dl></section>
        <div className="dshModelAgentWarning" role="note"><strong>启用前检查</strong><span>确认来源、版本完整性、协议兼容与权限范围。审核本身不会修改本机配置或外部仓库。</span></div>
        <footer><button type="button" className="dshModelAgentPrimary" onClick={onClose}>返回扩展列表</button></footer>
      </section>
    </div>
  )
}

function kindLabel(value: string) { return value === 'skill' ? '技能' : value === 'mcp' ? 'MCP Server' : '插件' }
function sourceLabel(value: string) { return value === 'official' ? '官方' : value === 'local' || value === 'custom' ? '本地 / 自定义' : value === 'preview' ? '预览样例' : '已安装插件' }
function formatUpdatedAt(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '未提供' : date.toLocaleString('zh-CN', { hour12: false })
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return <div><dt>{label}</dt><dd>{children}</dd></div>
}
