import { useEffect, useState } from 'react'
import type { DesktopBridgeLike } from './desktop-bridge'
import type { ProfileListResult } from './profile-model'
import type { ProjectController } from './project-controller'
import type { ProjectCardModel, ProjectDraft } from './project-model'

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
  const permissionReadonly = profiles?.profiles.find((profile) => profile.id === currentProfileId)?.permissionMode === 'read-only'

  const preview = async () => {
    setBusy(true)
    setError(null)
    try {
      setDraft(await controller.prepare({ idea, profileId: currentProfileId }))
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(false)
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
        <div className="dshDesktopProjectComposerBar">
          <span className="dshDesktopProjectComposerContext">
            <ComposerEditIcon />
            <small>已选择项目</small>
            <strong>正在修改 {selected.title}</strong>
          </span>
          {onClearSelection !== undefined && (
            <button type="button" className="dshDesktopProjectComposerClear" aria-label="取消选择项目" disabled={busy || disabled} onClick={onClearSelection}>×</button>
          )}
        </div>
        <textarea
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
        {error !== null && <p role="alert">{error}</p>}
        <div className="dshDesktopProjectComposerBar dshDesktopProjectComposerFooter">
          <span className="dshDesktopProjectComposerHint" title={selected.path}>{selected.path}</span>
          <button
            type="button"
            className="dshDesktopProjectComposerSend"
            data-busy={busy || undefined}
            disabled={busy || disabled || modifyPrompt.trim().length === 0}
            onClick={() => void modify()}
          >
            <ComposerSendIcon />
            <span className="dshDesktopSrOnly">{busy ? '正在发送…' : '发送修改'}</span>
          </button>
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
          <div><dt>项目名称</dt><dd>{draft.proposedName}</dd></div>
          <div><dt>保存位置</dt><dd>{draft.normalizedPath}</dd></div>
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
    <div className="dshDesktopProjectComposer" aria-busy={busy || undefined}>
      <div className="dshDesktopProjectComposerBar">
        <span className="dshDesktopProjectComposerContext">
          <ComposerFolderIcon />
          <strong>新建本地项目</strong>
        </span>
        <span className="dshDesktopProjectComposerMode">{permissionReadonly ? '只读' : '工作区可写'}</span>
      </div>
      <textarea
        aria-label="项目需求"
        value={idea}
        disabled={disabled || busy}
        placeholder="描述你想构建的本地项目，例如：一个支持离线同步的记账应用"
        onChange={(event) => setIdea(event.target.value)}
        onKeyDown={(event) => {
          if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
            event.preventDefault(); void preview()
          }
        }}
      />
      {error !== null && <p role="alert">{error}</p>}
      <div className="dshDesktopProjectComposerBar dshDesktopProjectComposerFooter">
        <span className="dshDesktopProjectComposerHint">项目与应用数据都保存在本机</span>
        <button
          type="button"
          className="dshDesktopProjectComposerSend"
          data-busy={busy || undefined}
          disabled={disabled || busy || currentProfileId === '' || idea.trim() === ''}
          onClick={() => void preview()}
        >
          <ComposerSendIcon />
          <span className="dshDesktopSrOnly">{busy ? '正在准备…' : '检查并预览'}</span>
        </button>
      </div>
    </div>
  )
}

function ComposerFolderIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" />
    </svg>
  )
}

function ComposerEditIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none">
      <path d="m14.5 5.5 4 4L8.5 19.5H4.5v-4L14.5 5.5Z" />
    </svg>
  )
}

function ComposerSendIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none">
      <path d="M12 19V5m0 0-6 6m6-6 6 6" />
    </svg>
  )
}

function messageOf(cause: unknown) {
  return cause instanceof Error ? cause.message : '无法准备本地项目'
}
