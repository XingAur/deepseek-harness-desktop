export interface AdoptCandidate {
  id: string
  title: string
  path: string
}

export interface AdoptProjectDialogProps {
  candidates: readonly AdoptCandidate[]
  busy: boolean
  // 收录失败的错误文案：留在对话框内就地提示，不关闭对话框、不落到页面级错误条。
  error?: string | null
  onAdopt(workspaceId: string): Promise<void>
  onClose(): void
}

// 收录已有项目对话框：列出当前 Profile 中尚未进入本地项目列表的工作区，点选即通过
// project.metadata.patch 打上 localApp 标记；样式沿用删除确认对话框的背板与面板模式。
export function AdoptProjectDialog({ candidates, busy, error, onAdopt, onClose }: AdoptProjectDialogProps) {
  return (
    <div
      className="dshDesktopProjectDialogBackdrop"
      onPointerDown={(event) => { if (event.target === event.currentTarget && !busy) onClose() }}
    >
      <section
        className="dshDesktopProjectDeleteDialog dshDesktopAdoptDialog"
        role="dialog"
        aria-modal="true"
        aria-label="收录已有项目"
        onKeyDown={(event) => { if (event.key === 'Escape' && !busy) { event.preventDefault(); onClose() } }}
      >
        <header>
          <div>
            <p>收录已有项目</p>
            <h2>把工作区加入本地项目</h2>
          </div>
          <button type="button" aria-label="关闭收录对话框" disabled={busy} onClick={onClose}>×</button>
        </header>
        {candidates.length === 0 ? (
          <p className="dshDesktopProjectDeletePath">当前 Profile 没有可收录的项目</p>
        ) : (
          <ul className="dshDesktopAdoptList">
            {candidates.map((candidate) => (
              <li key={candidate.id}>
                <button type="button" disabled={busy} onClick={() => void onAdopt(candidate.id)}>
                  <strong>{candidate.title}</strong>
                  <small title={candidate.path}>{candidate.path}</small>
                </button>
              </li>
            ))}
          </ul>
        )}
        {error != null && <p className="dshDesktopProjectDeleteError" role="alert">{error}</p>}
      </section>
    </div>
  )
}
