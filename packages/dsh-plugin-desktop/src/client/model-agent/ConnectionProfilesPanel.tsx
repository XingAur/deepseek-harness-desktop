import { useCallback, useEffect, useState } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { messageOf } from './state'

export type ConnectionKind = 'mcp' | 'database'

interface ConnectionProfile {
  profileId: string
  kind: ConnectionKind
  providerId: 'yunxiao' | 'gitlab' | 'generic'
  displayName: string
  endpoint: string
  readOnly: boolean
  enabled: boolean
  credentialId?: string
}

export function ConnectionProfilesPanel({ bridge, kind }: { bridge: DesktopBridgeLike; kind: ConnectionKind }) {
  const [profiles, setProfiles] = useState<ConnectionProfile[]>([])
  const [form, setForm] = useState({ profileId: '', providerId: kind === 'database' ? 'generic' : 'yunxiao', displayName: '', endpoint: '', credentialId: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [testedProfileId, setTestedProfileId] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setProfiles(await bridge.requestV2<ConnectionProfile[]>('harness.connection.list', undefined, { kind }))
    } catch (cause) {
      setError(messageOf(cause))
    }
  }, [bridge, kind])

  useEffect(() => { void refresh() }, [refresh])

  const save = async () => {
    if (form.displayName.trim() === '' || busy) return
    setBusy(true)
    setError(null)
    try {
      const profile = await bridge.requestV2<ConnectionProfile>('harness.connection.save', undefined, {
        ...(form.profileId.trim() === '' ? {} : { profileId: form.profileId.trim() }),
        kind,
        providerId: kind === 'database' ? 'generic' : form.providerId,
        displayName: form.displayName.trim(),
        endpoint: form.endpoint.trim(),
        readOnly: kind === 'database' ? true : true,
        enabled: true,
        ...(form.credentialId.trim() === '' ? {} : { credentialId: form.credentialId.trim() }),
      })
      setProfiles((current) => [...current.filter((item) => item.profileId !== profile.profileId), profile])
      setForm({ profileId: '', providerId: kind === 'database' ? 'generic' : 'yunxiao', displayName: '', endpoint: '', credentialId: '' })
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(false)
    }
  }

  const remove = async (profileId: string) => {
    setBusy(true)
    try {
      await bridge.requestV2('harness.connection.delete', undefined, { profileId })
      setProfiles((current) => current.filter((item) => item.profileId !== profileId))
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(false)
    }
  }

  const test = async (profileId: string) => {
    if (busy) return
    setBusy(true)
    setError(null)
    setTestedProfileId(null)
    try {
      await bridge.requestV2('harness.connection.test', undefined, { profileId })
      setTestedProfileId(profileId)
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="dshModelAgentGrid">
      <article className="dshModelAgentCard">
        <div className="dshModelAgentCardHeader"><div><div className="dshModelAgentEyebrow">{kind === 'database' ? 'DATABASE MAINTENANCE' : 'MCP CONNECTION MAINTENANCE'}</div><h3>{kind === 'database' ? '数据库维护' : 'MCP 连接维护'}</h3></div></div>
        <p className="dshModelAgentCardText">{kind === 'database' ? '数据库 profile 独立维护；Harness 任务只选择 profile，不保存密码，并默认只读。' : '维护云效、GitLab 等 MCP 连接的公共地址和凭证引用；敏感值只进入安全存储。'}</p>
        <div className="dshModelAgentFormGrid">
          <label className="dshModelAgentField">Profile ID（可选）<input aria-label="Profile ID" value={form.profileId} onChange={(event) => setForm({ ...form, profileId: event.target.value })} placeholder="his-db-readonly" /></label>
          {kind === 'mcp' && <label className="dshModelAgentField">MCP 归属<select aria-label="MCP 归属" value={form.providerId} onChange={(event) => setForm({ ...form, providerId: event.target.value as typeof form.providerId })}><option value="yunxiao">云效</option><option value="gitlab">GitLab</option><option value="generic">其他 MCP</option></select></label>}
          <label className="dshModelAgentField">显示名称<input aria-label="连接显示名称" value={form.displayName} onChange={(event) => setForm({ ...form, displayName: event.target.value })} placeholder={kind === 'database' ? 'HIS 只读库' : '云效 / GitLab'} /></label>
          <label className="dshModelAgentField">连接地址<input aria-label="连接地址" value={form.endpoint} onChange={(event) => setForm({ ...form, endpoint: event.target.value })} placeholder={kind === 'database' ? 'postgresql://host:5432/db' : 'https://gitlab.example.com 或 MCP 地址'} /></label>
          <label className="dshModelAgentField">凭证引用（可选）<input aria-label="凭证引用" value={form.credentialId} onChange={(event) => setForm({ ...form, credentialId: event.target.value })} placeholder="credential-1" /></label>
        </div>
        <p className="dshModelAgentMuted">当前策略：只读；实际连通性在 Harness 证据阶段探测，不在维护页执行外部写操作。</p>
        {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}
        <div className="dshModelAgentActions"><button type="button" disabled={busy || form.displayName.trim() === ''} onClick={() => void save()}>保存 profile</button><button type="button" disabled={busy} onClick={() => void refresh()}>刷新</button></div>
      </article>
      <article className="dshModelAgentCard">
        <h3>已维护的 {kind === 'database' ? '数据库' : 'MCP'} profile</h3>
        {profiles.length === 0 ? <p className="dshModelAgentMuted">暂无 profile。</p> : profiles.map((profile) => <div className="dshModelAgentListRow" key={profile.profileId}><div><strong>{profile.displayName}</strong><div className="dshModelAgentMuted">{profile.providerId} · {profile.profileId} · {profile.endpoint || '未填写地址'} · {profile.readOnly ? '只读' : '可写'}</div>{testedProfileId === profile.profileId && <div className="dshModelAgentSuccess" role="status">本地配置校验通过；实际连通性由 Harness 只读探测阶段执行。</div>}</div><div className="dshModelAgentActions"><button type="button" disabled={busy} onClick={() => void test(profile.profileId)}>校验配置</button><button type="button" disabled={busy} onClick={() => void remove(profile.profileId)}>删除</button></div></div>)}
      </article>
    </div>
  )
}
