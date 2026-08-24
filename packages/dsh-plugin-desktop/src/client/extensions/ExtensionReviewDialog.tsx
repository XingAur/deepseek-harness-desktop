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
  return (
    <div className="dshModelAgentDialogBackdrop" role="presentation">
      <section className="dshModelAgentDialog" role="dialog" aria-modal="true" aria-label="扩展审核">
        <header><div><p className="dshModelAgentEyebrow">EXTENSION REVIEW</p><h3>{extension.displayName}</h3></div><button type="button" onClick={onClose}>关闭</button></header>
        <dl className="dshExtensionReviewFacts">
          <Fact label="ID">{extension.extensionId}</Fact>
          <Fact label="类型">{extension.extensionKind}</Fact>
          <Fact label="来源">{extension.sourceKind}</Fact>
          <Fact label="状态">{extension.status}</Fact>
          <Fact label="最后更新">{extension.updatedAt}</Fact>
        </dl>
        <div className="dshModelAgentWarning" role="note">启用前必须完成固定来源、版本完整性、协议兼容和权限审核。这里不会修改 ~/.codex、~/.claude 或外部仓库。</div>
        <button type="button" className="dshModelAgentPrimary" onClick={onClose}>返回扩展列表</button>
      </section>
    </div>
  )
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return <div><dt>{label}</dt><dd>{children}</dd></div>
}
