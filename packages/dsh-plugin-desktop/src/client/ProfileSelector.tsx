import { useEffect, useMemo, useState } from 'react'
import type { DesktopBridgeLike } from './desktop-bridge'
import { ProfileListbox } from './ProfileListbox'

export interface ProfileSummary {
  id: string
  name: string
  revision: number
  dataRoot?: string
  permissionMode?: 'read-only' | 'workspace-write'
  runtimeVersion?: string
  status?: 'active' | 'switching' | 'recovered' | 'invalid' | 'ready'
}

export interface ProfileListResult {
  selectedProfileId: string | null
  pendingProfileId: string | null
  lastKnownGoodProfileId: string | null
  profiles: ProfileSummary[]
}

export type ProfileStatus = NonNullable<ProfileSummary['status']>

export interface ProfileSelectorProps {
  bridge: DesktopBridgeLike
  onPendingChange?(pending: boolean): void
}

export function ProfileSelector({ bridge, onPendingChange }: ProfileSelectorProps) {
  const [result, setResult] = useState<ProfileListResult | null>(null)
  const [selected, setSelected] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let disposed = false
    void bridge.request<ProfileListResult>('profile.list').then((profiles) => {
      if (disposed) return
      setResult(profiles)
      setSelected(profiles.pendingProfileId ?? profiles.selectedProfileId ?? profiles.profiles[0]?.id ?? '')
      const switching = profiles.pendingProfileId !== null
      setPending(switching)
      onPendingChange?.(switching)
    }).catch((cause) => {
      if (!disposed) setError(messageOf(cause))
    })
    return () => { disposed = true }
  }, [bridge, onPendingChange])

  const active = useMemo(
    () => result?.profiles.find((profile) => profile.id === selected),
    [result, selected],
  )
  const activeStatus = active !== undefined && result !== null
    ? profileStatus(active, result)
    : null

  const switchProfile = async (profileId: string) => {
    const previous = result?.selectedProfileId ?? selected
    setSelected(profileId)
    setPending(true)
    setError(null)
    onPendingChange?.(true)
    try {
      await bridge.request('profile.switch', { profileId })
      // 成功后父壳层会移除当前 iframe；保持 locked，避免旧 Generation 再发操作。
    } catch (cause) {
      setSelected(previous)
      setPending(false)
      onPendingChange?.(false)
      setError(messageOf(cause))
    }
  }

  return (
    <div className="dshDesktopProfileSelector" aria-busy={pending || result === null || undefined}>
      <ProfileListbox
        profiles={result?.profiles ?? []}
        selectedId={selected}
        pending={pending || result === null}
        status={(profile) => result === null ? profile.status ?? 'ready' : profileStatus(profile, result)}
        onSelect={switchProfile}
      />
      <div className="dshDesktopProfileMeta">
        {pending
          ? <span>正在切换 Profile…</span>
          : <span>{active?.runtimeVersion ? `Runtime v${active.runtimeVersion}` : '当前项目上下文'}</span>}
        {activeStatus !== null && <small data-status={activeStatus}>{profileStatusCopy(activeStatus)}</small>}
      </div>
      {error !== null && <p className="dshDesktopProfileError" role="alert">{error}</p>}
    </div>
  )
}

function profileStatus(profile: ProfileSummary, result: ProfileListResult): ProfileStatus {
  if (profile.status !== undefined) return profile.status
  if (profile.id === result.pendingProfileId) return 'switching' as const
  if (profile.id === result.selectedProfileId) return 'active' as const
  if (profile.id === result.lastKnownGoodProfileId) return 'recovered' as const
  return 'ready' as const
}

function profileStatusCopy(status: ReturnType<typeof profileStatus>) {
  return ({ active: '已启用', switching: '切换中', recovered: '上次可用', invalid: '需要修复', ready: '可切换' })[status]
}

function messageOf(cause: unknown) {
  return cause instanceof Error ? cause.message : '无法切换 Profile'
}
