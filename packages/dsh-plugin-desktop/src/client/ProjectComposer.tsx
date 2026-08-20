import { useEffect, useState } from 'react'
import type { DesktopBridgeLike } from './desktop-bridge'
import type { ProfileListResult } from './ProfileSelector'
import type { ProjectController } from './project-controller'
import type { ProjectCardModel, ProjectDraft, ProjectPermissionMode } from './project-model'

export interface ProjectComposerProps {
  bridge: DesktopBridgeLike
  controller: ProjectController
  selected?: ProjectCardModel
  disabled?: boolean
  onClearSelection?(): void
  onComplete(): void
}

export function ProjectComposer({ bridge, controller, selected, disabled = false, onClearSelection, onComplete }: ProjectComposerProps) {
  const [profiles, setProfiles] = useState<ProfileListResult | null>(null)
  const [idea, setIdea] = useState('')
  const [path, setPath] = useState('')
  const [permissionMode, setPermissionMode] = useState<ProjectPermissionMode>('workspace-write')
  const [createDirectory, setCreateDirectory] = useState(false)
  const [draft, setDraft] = useState<ProjectDraft | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [modifyPrompt, setModifyPrompt] = useState('')

  useEffect(() => {
    let disposed = false
    void bridge.request<ProfileListResult>('profile.list')
      .then((next) => { if (!disposed) setProfiles(next) })
      .catch((cause) => { if (!disposed) setError(messageOf(cause)) })
    return () => { disposed = true }
  }, [bridge])

  useEffect(() => {
    setModifyPrompt('')
    setDraft(null)
    setError(null)
  }, [selected?.id])

  const currentProfileId = profiles?.pendingProfileId ?? profiles?.selectedProfileId ?? ''
  const currentProfile = profiles?.profiles.find((profile) => profile.id === currentProfileId)

  const preview = () => {
    setError(null)
    try {
      setDraft(controller.prepare({ idea, path, profileId: currentProfileId, permissionMode, createDirectory }))
    } catch (cause) {
      setError(messageOf(cause))
    }
  }

  const confirm = async () => {
    if (draft === null) return
    setBusy(true)
    setError(null)
    try {
      await controller.confirm(draft)
      onComplete()
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(false)
    }
  }

  const modify = async () => {
    if (selected === undefined) return
    setBusy(true)
    setError(null)
    try {
      await controller.modify(selected.id, modifyPrompt)
      onComplete()
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(false)
    }
  }

  if (selected !== undefined) {
    return (
      <div className="dshDesktopProjectComposer dshDesktopProjectModifyComposer" aria-busy={busy || undefined}>
        <header>
          <div><small>已选择项目</small><strong>正在修改 {selected.title}</strong></div>
          {onClearSelection !== undefined && <button type="button" aria-label="取消选择项目" disabled={busy || disabled} onClick={onClearSelection}>×</button>}
        </header>
        <label>
          修改需求
          <textarea
            autoFocus
            aria-label="修改需求"
            value={modifyPrompt}
            disabled={busy || disabled}
            placeholder="描述要继续修改、修复或优化的内容"
            onChange={(event) => setModifyPrompt(event.target.value)}
            onKeyDown={(event) => {
              if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
                event.preventDefault(); void modify()
              }
            }}
          />
        </label>
        {error !== null && <p role="alert">{error}</p>}
        <div className="dshDesktopProjectComposerActions">
          <span>{selected.path}</span>
          <button type="button" disabled={busy || disabled || modifyPrompt.trim().length === 0} onClick={() => void modify()}>{busy ? '正在发送…' : '发送修改'}</button>
        </div>
      </div>
    )
  }

  if (draft !== null) {
    return (
      <div className="dshDesktopProjectConfirm" aria-busy={busy || disabled || undefined}>
        <h3>确认构建范围</h3>
        <dl>
          <div><dt>需求</dt><dd>{draft.idea}</dd></div>
          <div><dt>路径</dt><dd>{draft.normalizedPath}{draft.createDirectory ? '（将新建）' : ''}</dd></div>
          <div><dt>Profile</dt><dd>{currentProfile?.name ?? draft.profileId}</dd></div>
          <div><dt>权限</dt><dd>{draft.permissionMode === 'read-only' ? '只读' : '工作区可写'}</dd></div>
          <div><dt>命令类别</dt><dd>{draft.commandCategories.join(' · ')}</dd></div>
        </dl>
        {error !== null && <p role="alert">{error}</p>}
        <div className="dshDesktopProjectComposerActions">
          <button type="button" disabled={busy || disabled} onClick={() => setDraft(null)}>返回修改</button>
          <button type="button" disabled={busy || disabled} onClick={() => void confirm()}>{busy ? '正在创建…' : '确认并开始构建'}</button>
        </div>
      </div>
    )
  }

  return (
    <div className="dshDesktopProjectComposer">
      <label>项目需求<textarea aria-label="项目需求" value={idea} disabled={disabled} placeholder="例如：做一个支持离线同步的记账应用" onChange={(event) => setIdea(event.target.value)} /></label>
      <div className="dshDesktopProjectComposerRow">
        <label>项目路径<input aria-label="项目路径" value={path} disabled={disabled} placeholder="C:\\Users\\你\\Projects\\ledger" onChange={(event) => setPath(event.target.value)} /></label>
        <label>当前 Profile<select aria-label="构建 Profile" value={currentProfileId} disabled><option value={currentProfileId}>{currentProfile?.name ?? '正在读取…'}</option></select></label>
        <label>权限模式<select aria-label="构建权限模式" value={permissionMode} disabled={disabled} onChange={(event) => setPermissionMode(event.target.value as ProjectPermissionMode)}><option value="workspace-write">工作区可写</option><option value="read-only">只读</option></select></label>
      </div>
      <label className="dshDesktopProjectCreateDirectory"><input type="checkbox" checked={createDirectory} disabled={disabled} onChange={(event) => setCreateDirectory(event.target.checked)} />目录尚不存在，需要创建</label>
      <small>如需更换 Profile，请先使用页面上方的 Profile 选择器。</small>
      {error !== null && <p role="alert">{error}</p>}
      <div className="dshDesktopProjectComposerActions"><button type="button" disabled={disabled || currentProfileId === ''} onClick={preview}>检查并预览</button></div>
    </div>
  )
}

function messageOf(cause: unknown) {
  return cause instanceof Error ? cause.message : '无法准备本地项目'
}
