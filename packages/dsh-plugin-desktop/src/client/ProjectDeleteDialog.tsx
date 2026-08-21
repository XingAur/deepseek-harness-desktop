import { useState } from 'react'

export type ProjectDeleteScope = 'unregister' | 'recycle'

export interface ProjectDeleteTarget {
  id: string
  title: string
  path: string
  unavailable: boolean
}

export interface ProjectDeleteDialogProps {
  project: ProjectDeleteTarget
  onConfirm(scope: ProjectDeleteScope): Promise<void>
  onCancel(): void
}

export function ProjectDeleteDialog({ project, onConfirm, onCancel }: ProjectDeleteDialogProps) {
  const [scope, setScope] = useState<ProjectDeleteScope>('unregister')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const recycleConfirmed = scope === 'recycle' && confirmation === project.title

  const confirm = async () => {
    setBusy(true)
    setError(null)
    try {
      await onConfirm(scope)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '删除项目失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="dshDesktopProjectDialogBackdrop" onPointerDown={(event) => { if (event.target === event.currentTarget && !busy) onCancel() }}>
      <section
        className="dshDesktopProjectDeleteDialog"
        role="dialog"
        aria-modal="true"
        aria-label={`删除 ${project.title}`}
        onKeyDown={(event) => {
          if (event.key === 'Escape' && !busy) { event.preventDefault(); onCancel() }
        }}
      >
        <header>
          <div>
            <p>删除本地项目</p>
            <h2 id="dsh-project-delete-title">{project.title}</h2>
          </div>
          <button type="button" aria-label="关闭删除确认" disabled={busy} onClick={onCancel}>×</button>
        </header>
        <p className="dshDesktopProjectDeletePath" title={project.path}>{project.path}</p>
        <fieldset disabled={busy}>
          <label>
            <input aria-label="仅从列表移除" type="radio" name="delete-scope" checked={scope === 'unregister'} onChange={() => setScope('unregister')} />
            <span><strong>仅从列表移除</strong><small>保留磁盘中的项目目录，可稍后重新添加。</small></span>
          </label>
          <label data-disabled={project.unavailable || undefined}>
            <input aria-label="移到 Windows 回收站" type="radio" name="delete-scope" checked={scope === 'recycle'} disabled={project.unavailable} onChange={() => setScope('recycle')} />
            <span><strong>移到 Windows 回收站</strong><small>{project.unavailable ? '当前路径不可用，只能移除列表记录。' : '同时移除目录与应用数据，仍可从系统回收站恢复。'}</small></span>
          </label>
        </fieldset>
        {scope === 'recycle' && (
          <label className="dshDesktopProjectDeleteNameCheck">
            输入项目名称确认
            <input autoFocus value={confirmation} disabled={busy} onChange={(event) => setConfirmation(event.target.value)} placeholder={project.title} />
          </label>
        )}
        {error !== null && <p className="dshDesktopProjectDeleteError" role="alert">{error}</p>}
        <footer>
          <button type="button" disabled={busy} onClick={onCancel}>取消</button>
          <button
            type="button"
            className="dshDesktopProjectDeleteDanger"
            disabled={busy || (scope === 'recycle' && !recycleConfirmed)}
            onClick={() => void confirm()}
          >
            {scope === 'recycle' ? '移到回收站' : '确认移除'}
          </button>
        </footer>
      </section>
    </div>
  )
}
