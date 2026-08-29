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
        <p className="dshModelAgentCardText">{kind === 'database' ? '数据库独立维护；Harness 主聊天只选择已维护的数据库，不保存密码，并默认只读。' : '维护 Harness 实际使用的云效需求和 GitLab 代码能力；连接凭证只进入安全存储。'}</p>
        <div className="dshModelAgentFormGrid">
          {kind === 'mcp' && <label className="dshModelAgentField">业务能力<select aria-label="业务能力" value={form.providerId} onChange={(event) => setForm({ ...form, providerId: event.target.value as typeof form.providerId })}><option value="yunxiao">云效需求读取</option><option value="gitlab">GitLab 代码读取</option></select></label>}
          <label className="dshModelAgentField">连接名称<input aria-label="连接名称" value={form.displayName} onChange={(event) => setForm({ ...form, displayName: event.target.value })} placeholder={kind === 'database' ? 'HIS 只读库' : '研发云效需求 / GitLab 主库'} /></label>
          <label className="dshModelAgentField">{kind === 'database' ? '数据库连接地址' : form.providerId === 'yunxiao' ? '云效服务地址' : 'GitLab 服务地址'}<input aria-label={kind === 'database' ? '数据库连接地址' : form.providerId === 'yunxiao' ? '云效服务地址' : 'GitLab 服务地址'} value={form.endpoint} onChange={(event) => setForm({ ...form, endpoint: event.target.value })} placeholder={kind === 'database' ? 'postgresql://host:5432/db' : form.providerId === 'yunxiao' ? 'https://devops.aliyun.com' : 'https://gitlab.example.com'} /></label>
          <label className="dshModelAgentField">安全凭证（可选）<input aria-label="安全凭证" value={form.credentialId} onChange={(event) => setForm({ ...form, credentialId: event.target.value })} placeholder="从安全凭证维护中选择" /></label>
        </div>
        <p className="dshModelAgentMuted">当前策略：只读。保存的是业务连接配置，Harness 会在任务证据阶段按所选能力读取，不在维护页执行外部写操作。</p>
        {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}
        <div className="dshModelAgentActions"><button type="button" disabled={busy || form.displayName.trim() === ''} onClick={() => void save()}>保存 profile</button><button type="button" disabled={busy} onClick={() => void refresh()}>刷新</button></div>
      </article>
      <article className="dshModelAgentCard">
        <h3>已维护的 {kind === 'database' ? '数据库' : '业务连接'}</h3>
        {profiles.length === 0 ? <p className="dshModelAgentMuted">暂无已维护连接。</p> : profiles.map((profile) => <div className="dshModelAgentListRow" key={profile.profileId}><div><strong>{profile.displayName}</strong><div className="dshModelAgentMuted">{profile.providerId === 'yunxiao' ? '云效需求' : profile.providerId === 'gitlab' ? 'GitLab 代码' : kind === 'database' ? '数据库' : '未分类连接'} · {profile.endpoint || '未填写服务地址'} · {profile.readOnly ? '只读' : '可写'}</div>{testedProfileId === profile.profileId && <div className="dshModelAgentSuccess" role="status">本地配置校验通过；实际连通性由 Harness 只读探测阶段执行。</div>}</div><div className="dshModelAgentActions"><button type="button" disabled={busy} onClick={() => void test(profile.profileId)}>校验配置</button><button type="button" disabled={busy} onClick={() => void remove(profile.profileId)}>删除</button></div></div>)}
      </article>
    </div>
  )
}
