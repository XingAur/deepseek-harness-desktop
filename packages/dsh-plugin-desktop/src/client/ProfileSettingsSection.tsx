import { useCallback, useEffect, useState } from 'react'
import type { DesktopBridgeLike } from './desktop-bridge'
import type { ProfileListResult, ProfileSummary } from './ProfileSelector'

type PermissionMode = 'read-only' | 'workspace-write'

interface ProfileEditorState {
  mode: 'create' | 'update'
  profileId?: string
  expectedRevision?: number
  name: string
  dataRoot: string
  permissionMode: PermissionMode
}

export interface ProfileSettingsSectionProps {
  bridge: DesktopBridgeLike
}

export function ProfileSettingsSection({ bridge }: ProfileSettingsSectionProps) {
  const [snapshot, setSnapshot] = useState<ProfileListResult | null>(null)
  const [editor, setEditor] = useState<ProfileEditorState | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    const next = await bridge.request<ProfileListResult>('profile.list')
    setSnapshot(next)
  }, [bridge])

  useEffect(() => {
    let disposed = false
    void bridge.request<ProfileListResult>('profile.list')
      .then((next) => { if (!disposed) setSnapshot(next) })
      .catch((cause) => { if (!disposed) setError(messageOf(cause)) })
    return () => { disposed = true }
  }, [bridge])

  const mutate = async (action: Parameters<DesktopBridgeLike['request']>[0], payload: Record<string, unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await bridge.request(action, payload)
      await load()
      setEditor(null)
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    if (editor === null) return
    const draft = { name: editor.name.trim(), dataRoot: editor.dataRoot.trim(), permissionMode: editor.permissionMode }
    if (editor.mode === 'create') {
      await mutate('profile.create', { draft })
    } else {
      await mutate('profile.update', {
        profileId: editor.profileId,
        expectedRevision: editor.expectedRevision,
        patch: draft,
      })
    }
  }

  const duplicate = (profile: ProfileSummary) => mutate('profile.duplicate', {
    profileId: profile.id,
    draft: {
      name: `${profile.name} 副本`,
      dataRoot: `${profile.dataRoot ?? ''}-copy`,
      permissionMode: profile.permissionMode ?? 'workspace-write',
    },
  })

  return (
    <section className="dshDesktopProfileSettings" aria-busy={busy || snapshot === null || undefined}>
      <header>
        <div><h2>Profiles</h2><p>隔离工作区数据、权限和 Runtime 上下文。</p></div>
        <button type="button" disabled={busy} onClick={() => setEditor(emptyEditor())}>新建 Profile</button>
      </header>
      {error !== null && <div className="dshDesktopProfileSettingsError" role="alert">{error}</div>}

      {editor !== null && (
        <div className="dshDesktopProfileEditor">
          <label>名称<input aria-label="名称" value={editor.name} disabled={busy} onChange={(event) => setEditor({ ...editor, name: event.target.value })} /></label>
          <label>数据目录<input aria-label="数据目录" value={editor.dataRoot} disabled={busy} onChange={(event) => setEditor({ ...editor, dataRoot: event.target.value })} /></label>
          <label>权限模式<select aria-label="权限模式" value={editor.permissionMode} disabled={busy} onChange={(event) => setEditor({ ...editor, permissionMode: event.target.value as PermissionMode })}><option value="workspace-write">工作区可写</option><option value="read-only">只读</option></select></label>
          <div><button type="button" disabled={busy || editor.name.trim() === '' || editor.dataRoot.trim() === ''} onClick={() => void save()}>保存</button><button type="button" disabled={busy} onClick={() => setEditor(null)}>取消</button></div>
        </div>
      )}

      <div className="dshDesktopProfileSettingsList">
        {snapshot?.profiles.map((profile) => {
          const protectedProfile = isProtected(profile.id, snapshot)
          return (
            <article key={profile.id}>
              <div className="dshDesktopProfileSettingsTitle">
                <strong>{profile.name}</strong>
                <span>{statusCopy(profile, snapshot)}</span>
              </div>
              <p>{profile.dataRoot ?? '未设置数据目录'}</p>
              <small>{profile.permissionMode === 'read-only' ? '只读' : '工作区可写'} · revision {profile.revision}{profile.runtimeVersion ? ` · Runtime v${profile.runtimeVersion}` : ''}</small>
              <div className="dshDesktopProfileSettingsActions">
                <button type="button" disabled={busy} aria-label={`编辑 ${profile.name}`} onClick={() => setEditor(editorFor(profile))}>编辑</button>
                <button type="button" disabled={busy} aria-label={`复制 ${profile.name}`} onClick={() => void duplicate(profile)}>复制</button>
                <button type="button" disabled={busy || protectedProfile} title={protectedProfile ? '当前、待激活或上次可用 Profile 不能删除' : undefined} aria-label={`删除 ${profile.name}`} onClick={() => void mutate('profile.delete', { profileId: profile.id })}>删除</button>
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}

function emptyEditor(): ProfileEditorState {
  return { mode: 'create', name: '', dataRoot: '', permissionMode: 'workspace-write' }
}

function editorFor(profile: ProfileSummary): ProfileEditorState {
  return {
    mode: 'update', profileId: profile.id, expectedRevision: profile.revision,
    name: profile.name, dataRoot: profile.dataRoot ?? '', permissionMode: profile.permissionMode ?? 'workspace-write',
  }
}

function isProtected(id: string, snapshot: ProfileListResult) {
  return id === snapshot.selectedProfileId || id === snapshot.pendingProfileId || id === snapshot.lastKnownGoodProfileId
}

function statusCopy(profile: ProfileSummary, snapshot: ProfileListResult) {
  if (profile.id === snapshot.pendingProfileId) return '待激活'
  if (profile.id === snapshot.selectedProfileId) return '当前'
  if (profile.id === snapshot.lastKnownGoodProfileId) return '上次可用'
  if (profile.status === 'invalid') return '需要修复'
  return '可用'
}

function messageOf(cause: unknown) {
  return cause instanceof Error ? cause.message : 'Profile 操作失败'
}
