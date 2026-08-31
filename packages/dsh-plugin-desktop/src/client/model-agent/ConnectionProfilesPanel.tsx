import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { ExtensionReviewDialog, type ExtensionReviewItem } from '../extensions/ExtensionReviewDialog'
import {
  createConnectionDraft,
  defaultDatabasePort,
  editConnectionDraft,
  normalizeConnectionProfile,
  prepareConnectionDraft,
  validateConnectionDraft,
  type DatabaseType,
  type ConnectionDraft,
  type ConnectionKind,
  type ConnectionProfile,
  type ConnectionTestResult,
  type ConnectionTransport,
} from './connection-model'
import { messageOf } from './state'

export interface ConnectionProfilesPanelProps {
  bridge: DesktopBridgeLike
  managedMcp: ExtensionReviewItem[]
  onProfilesChange?(profiles: ConnectionProfile[]): void
}

export function ConnectionProfilesPanel({ bridge, managedMcp, onProfilesChange }: ConnectionProfilesPanelProps) {
  const [profiles, setProfiles] = useState<ConnectionProfile[]>([])
  const [editing, setEditing] = useState<ConnectionDraft | null>(null)
  const [reviewing, setReviewing] = useState<ExtensionReviewItem | null>(null)
  const [deleting, setDeleting] = useState<ConnectionProfile | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const replaceProfiles = useCallback((next: ConnectionProfile[]) => {
    setProfiles(next)
    onProfilesChange?.(next)
  }, [onProfilesChange])

  const refresh = useCallback(async () => {
    try {
      const reply = await bridge.requestV2<Array<Partial<ConnectionProfile> & Pick<ConnectionProfile, 'profileId' | 'kind' | 'displayName'>>>('harness.connection.list', undefined, {})
      replaceProfiles(reply.map(normalizeConnectionProfile))
      setError(null)
    } catch (cause) {
      setError(messageOf(cause))
    }
  }, [bridge, replaceProfiles])

  useEffect(() => { void refresh() }, [refresh])

  const save = async (draft: ConnectionDraft) => {
    if (bridge.mode === 'preview') return
    draft = prepareConnectionDraft(draft)
    const validation = validateConnectionDraft(draft)
    if (validation.length > 0) { setError(validation[0]); return }
    setBusyId(draft.profileId ?? 'new')
    try {
      const reply = await bridge.requestV2<Partial<ConnectionProfile> & Pick<ConnectionProfile, 'profileId' | 'kind' | 'displayName'>>('harness.connection.save', undefined, draft)
      const saved = normalizeConnectionProfile(reply)
      replaceProfiles([...profiles.filter((item) => item.profileId !== saved.profileId), saved])
      setEditing(null)
      setError(null)
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusyId(null)
    }
  }

  const updateEnabled = async (profile: ConnectionProfile) => {
    await save({ ...editConnectionDraft(profile), enabled: !profile.enabled })
  }

  const test = async (profile: ConnectionProfile) => {
    if (bridge.mode === 'preview') return
    setBusyId(profile.profileId)
    try {
      const result = await bridge.requestV2<ConnectionTestResult>('harness.connection.test', undefined, { profileId: profile.profileId })
      replaceProfiles(profiles.map((item) => item.profileId === profile.profileId ? { ...item, latestTest: result } : item))
      setError(null)
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusyId(null)
    }
  }

  const remove = async () => {
    if (deleting === null || bridge.mode === 'preview') return
    setBusyId(deleting.profileId)
    try {
      await bridge.requestV2('harness.connection.delete', undefined, { profileId: deleting.profileId })
      replaceProfiles(profiles.filter((item) => item.profileId !== deleting.profileId))
      setDeleting(null)
      setError(null)
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="dshConnectionCenter">
      <div className="dshCapabilitySectionHeader">
        <div><strong>MCP 与连接</strong><span>插件提供的 MCP 与自定义连接统一展示，并保留各自的管理边界。</span></div>
        <button type="button" onClick={() => setEditing(createConnectionDraft('mcp', 'stdio'))}>新增连接</button>
      </div>
      {bridge.mode === 'preview' && <div className="dshPluginMarketPreview" role="note">本地只读预览只演示交互，不读取或修改正式连接配置。</div>}
      {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}
      <div className="dshCapabilityRows">
        {managedMcp.map((extension) => (
          <div className="dshCapabilityRow" key={`managed-${extension.extensionId}`}>
            <div className="dshCapabilityRowMain"><strong>{extension.displayName}</strong><span>MCP · 插件管理 · {sourceLabel(extension.sourceKind)} · 版本未知</span></div>
            <span className="dshCapabilityState">{extension.status === 'enabled' ? '已启用' : '已停用'}</span>
            <div className="dshCapabilityActions"><button type="button" onClick={() => setReviewing(extension)}>查看审核</button></div>
          </div>
        ))}
        {profiles.map((profile) => (
          <div className="dshCapabilityRow dshConnectionRow" key={profile.profileId}>
            <div className="dshCapabilityRowMain">
              <strong>{profile.displayName}</strong>
              <span>{kindLabel(profile.kind)} · {transportLabel(profile.transport)} · {profile.source === 'legacy' ? '兼容配置' : '用户自定义'} · {profile.readOnly ? '只读' : '可写'}</span>
              <small>{connectionSummary(profile)}</small>
            </div>
            <span className="dshCapabilityState">{profile.enabled ? '已启用' : '已停用'}</span>
            <div className="dshCapabilityActions">
              <button type="button" aria-label={`测试 ${profile.displayName}`} disabled={busyId !== null || bridge.mode === 'preview'} onClick={() => void test(profile)}>测试</button>
              <button type="button" disabled={busyId !== null || bridge.mode === 'preview'} onClick={() => setEditing(editConnectionDraft(profile))}>编辑</button>
              <button type="button" disabled={busyId !== null || bridge.mode === 'preview'} onClick={() => void updateEnabled(profile)}>{profile.enabled ? '停用' : '启用'}</button>
              <button type="button" disabled={busyId !== null || bridge.mode === 'preview'} onClick={() => setDeleting(profile)}>删除</button>
            </div>
            <TestLayers result={profile.latestTest} />
          </div>
        ))}
        {managedMcp.length === 0 && profiles.length === 0 && <div className="dshModelAgentEmpty"><strong>暂无 MCP 或连接</strong><span>安装带 MCP 的插件，或新增一个自定义连接。</span></div>}
      </div>
      {editing !== null && <ConnectionEditor bridge={bridge} draft={editing} busy={busyId !== null} onChange={setEditing} onClose={() => setEditing(null)} onSave={save} onTest={(profileId) => {
        const profile = profiles.find((item) => item.profileId === profileId)
        return profile === undefined ? Promise.resolve() : test(profile)
      }} />}
      {deleting !== null && <DeleteDialog profile={deleting} busy={busyId !== null} onClose={() => setDeleting(null)} onDelete={remove} />}
      {reviewing !== null && <ExtensionReviewDialog extension={reviewing} onClose={() => setReviewing(null)} />}
    </div>
  )
}

function ConnectionEditor(props: { bridge: DesktopBridgeLike; draft: ConnectionDraft; busy: boolean; onChange(draft: ConnectionDraft): void; onClose(): void; onSave(draft: ConnectionDraft): Promise<void>; onTest(profileId: string): Promise<void> }) {
  const { draft } = props
  const [secret, setSecret] = useState('')
  const [credentialBusy, setCredentialBusy] = useState(false)
  const title = draft.profileId === undefined ? '新增连接' : `编辑 ${draft.displayName}`
  const errors = useMemo(() => validateConnectionDraft(draft), [draft])
  const patch = (value: Partial<ConnectionDraft>) => props.onChange({ ...draft, ...value })
  const scheme = connectionScheme(draft)
  const changeScheme = (value: ConnectionScheme) => {
    setSecret('')
    props.onChange(createSchemeDraft(value))
  }
  const changeDatabaseType = (databaseType: DatabaseType) => patch({ databaseType, port: defaultDatabasePort(databaseType) })
  const save = async () => {
    if (props.bridge.mode === 'preview') return
    setCredentialBusy(true)
    try {
      let credentialId = draft.credentialId
      if (secret !== '') {
        const reply = await props.bridge.requestV2<{ credentialId: string }>('credential.put', undefined, {
          ...(credentialId === undefined ? {} : { credentialId }), secret,
        })
        credentialId = reply.credentialId
      }
      setSecret('')
      await props.onSave(prepareConnectionDraft({ ...draft, ...(credentialId === undefined ? {} : { credentialId }) }))
    } finally {
      setSecret('')
      setCredentialBusy(false)
    }
  }
  return (
    <div className="dshModelAgentDialogBackdrop" role="presentation">
      <section className="dshModelAgentDialog dshConnectionEditor" role="dialog" aria-modal="true" aria-label={title}>
        <header><div><h3>{title}</h3><p>按连接类型只展示需要填写的内容。</p></div><button type="button" aria-label="关闭连接编辑器" onClick={props.onClose}>×</button></header>
        <div className="dshConnectionEditorBody">
          {props.bridge.mode === 'preview' && <div className="dshPluginMarketPreview" role="note">只读预览可以填写表单，但不会保存到正式桌面。</div>}
          <div className="dshConnectionEditorGrid">
          <Field label="连接方案"><select aria-label="连接方案" value={scheme} onChange={(event) => changeScheme(event.target.value as ConnectionScheme)}><option value="mcp">自定义 MCP Server</option><option value="http-api">HTTP API</option><option value="yunxiao">云效</option><option value="gitlab">GitLab</option><option value="database">数据库</option></select></Field>
          <Field label="连接名称"><input aria-label="连接名称" value={draft.displayName} onChange={(event) => patch({ displayName: event.target.value })} placeholder="给这个连接起个名字" /></Field>
          {scheme === 'yunxiao' && <>
            <Field wide label="云效服务地址"><input aria-label="云效服务地址" value={draft.endpoint} onChange={(event) => patch({ endpoint: event.target.value })} /></Field>
            <SecretField wide label="云效个人令牌" value={secret} configured={draft.credentialId !== undefined} onChange={setSecret} />
          </>}
          {scheme === 'gitlab' && <>
            <Field wide label="GitLab 地址"><input aria-label="GitLab 地址" value={draft.endpoint} onChange={(event) => patch({ endpoint: event.target.value })} /></Field>
            <SecretField wide label="GitLab 个人访问令牌" value={secret} configured={draft.credentialId !== undefined} onChange={setSecret} />
          </>}
          {scheme === 'database' && <>
            <Field label="数据库类型"><select aria-label="数据库类型" value={draft.databaseType} onChange={(event) => changeDatabaseType(event.target.value as DatabaseType)}><option value="postgresql">PostgreSQL</option><option value="mysql">MySQL</option><option value="sqlserver">SQL Server</option><option value="oracle">Oracle</option></select></Field>
            <Field label="主机"><input aria-label="主机" value={draft.host ?? ''} onChange={(event) => patch({ host: event.target.value })} /></Field>
            <Field label="端口"><input aria-label="端口" type="number" min={1} max={65535} value={draft.port ?? ''} onChange={(event) => patch({ port: Number(event.target.value) })} /></Field>
            <Field label="数据库名称"><input aria-label="数据库名称" value={draft.databaseName ?? ''} onChange={(event) => patch({ databaseName: event.target.value })} /></Field>
            <Field label="用户名"><input aria-label="用户名" autoComplete="username" value={draft.username ?? ''} onChange={(event) => patch({ username: event.target.value })} /></Field>
            <SecretField label="密码" value={secret} configured={draft.credentialId !== undefined} onChange={setSecret} />
            <Field label="编码"><select aria-label="编码" value={draft.encoding ?? 'UTF-8'} onChange={(event) => patch({ encoding: event.target.value })}><option value="UTF-8">UTF-8</option><option value="GBK">GBK</option><option value="LATIN1">LATIN1</option></select></Field>
            <Field wide label="连接测试查询语句"><input aria-label="连接测试查询语句" value={draft.testQuery ?? 'SELECT 1'} onChange={(event) => patch({ testQuery: event.target.value })} /></Field>
            <div className="dshConnectionComputed"><span>连接地址</span><code>{prepareConnectionDraft(draft).endpoint}</code></div>
          </>}
          {scheme === 'mcp' && <>
            <Field label="传输方式"><select aria-label="传输方式" value={draft.transport} onChange={(event) => patch({ transport: event.target.value as ConnectionTransport, workingDirectoryPolicy: event.target.value === 'stdio' ? 'workspace' : 'none', endpoint: '', command: '' })}><option value="stdio">stdio</option><option value="http">HTTP</option><option value="sse">SSE</option></select></Field>
            {draft.transport === 'stdio'
              ? <Field wide label="命令"><input aria-label="命令" value={draft.command} onChange={(event) => patch({ command: event.target.value })} placeholder="node /path/to/server.js" /></Field>
              : <Field wide label="服务地址"><input aria-label="服务地址" value={draft.endpoint} onChange={(event) => patch({ endpoint: event.target.value })} placeholder="https://example.com/mcp" /></Field>}
            <details className="dshConnectionAdvanced"><summary>高级设置</summary><div>
              {draft.transport === 'stdio' && <><Field label="参数"><input aria-label="参数" value={draft.args.join(' ')} onChange={(event) => patch({ args: splitWords(event.target.value) })} /></Field><Field label="环境变量名称"><input aria-label="环境变量名称" value={draft.environmentKeys.join(', ')} onChange={(event) => patch({ environmentKeys: splitNames(event.target.value) })} /></Field><Field label="工作目录"><select aria-label="工作目录" value={draft.workingDirectoryPolicy} onChange={(event) => patch({ workingDirectoryPolicy: event.target.value as ConnectionDraft['workingDirectoryPolicy'] })}><option value="workspace">当前工作区</option><option value="inherit">继承宿主</option><option value="none">不指定</option></select></Field></>}
              {draft.transport !== 'stdio' && <SecretField label="访问令牌（可选）" value={secret} configured={draft.credentialId !== undefined} onChange={setSecret} />}
            </div></details>
          </>}
          {scheme === 'http-api' && <>
            <Field wide label="服务地址"><input aria-label="服务地址" value={draft.endpoint} onChange={(event) => patch({ endpoint: event.target.value })} placeholder="https://example.com" /></Field>
            <SecretField wide label="访问令牌（可选）" value={secret} configured={draft.credentialId !== undefined} onChange={setSecret} />
            <details className="dshConnectionAdvanced"><summary>高级设置</summary><div><Field label="健康检查路径"><input aria-label="健康检查路径" value={draft.healthPath} onChange={(event) => patch({ healthPath: event.target.value })} placeholder="/health" /></Field></div></details>
          </>}
            <div className="dshConnectionPolicy"><label className="dshConnectionCheck"><input type="checkbox" checked={draft.enabled} onChange={(event) => patch({ enabled: event.target.checked })} />启用此连接</label><label className="dshConnectionCheck"><input type="checkbox" checked={draft.readOnly || scheme === 'database'} disabled={scheme === 'database' || scheme === 'yunxiao' || scheme === 'gitlab'} onChange={(event) => patch({ readOnly: event.target.checked })} />只读访问</label></div>
          </div>
          {errors.length > 0 && draft.displayName !== '' && <div className="dshModelAgentError" role="alert">{errors[0]}</div>}
          <p className="dshModelAgentDialogHint">密码和 Token 直接写入系统安全凭证库；连接 Profile 只保存凭证引用，不保存或回显秘密。数据库目前只有 PostgreSQL 接入 Harness 只读执行，其余类型先提供配置与网络探测。</p>
        </div>
        <footer><button type="button" onClick={props.onClose}>取消</button><button type="button" disabled={draft.profileId === undefined || props.busy || props.bridge.mode === 'preview'} title={draft.profileId === undefined ? '保存后可测试' : undefined} onClick={() => draft.profileId !== undefined && void props.onTest(draft.profileId)}>测试连接</button><button type="button" className="dshModelAgentPrimary" disabled={props.busy || credentialBusy || errors.length > 0 || props.bridge.mode === 'preview'} onClick={() => void save()}>{props.bridge.mode === 'preview' ? '正式桌面可保存' : '保存连接'}</button></footer>
      </section>
    </div>
  )
}

function DeleteDialog({ profile, busy, onClose, onDelete }: { profile: ConnectionProfile; busy: boolean; onClose(): void; onDelete(): Promise<void> }) {
  return <div className="dshModelAgentDialogBackdrop" role="presentation"><section className="dshModelAgentDialog" role="alertdialog" aria-modal="true" aria-label={`删除 ${profile.displayName}`}><header><h3>删除连接</h3><button type="button" onClick={onClose}>关闭</button></header><p className="dshModelAgentDialogHint">确认删除“{profile.displayName}”？这只删除该连接配置，不删除安全凭证。</p><footer><button type="button" onClick={onClose}>取消</button><button type="button" disabled={busy} onClick={() => void onDelete()}>确认删除</button></footer></section></div>
}

function TestLayers({ result }: { result: ConnectionTestResult }) {
  if (result.layers.length === 0) return <small>{result.summary}</small>
  return <div className="dshConnectionTest" role="status"><strong>{result.summary}</strong><div>{result.layers.map((layer) => <span data-state={layer.state} key={layer.id}><strong>{layer.label}</strong>{layer.message}</span>)}</div></div>
}

function Field({ label, children, wide = false }: { label: string; children: ReactNode; wide?: boolean }) { return <label className={`dshModelAgentField${wide ? ' is-wide' : ''}`}>{label}{children}</label> }
function SecretField({ label, value, configured, onChange, wide = false }: { label: string; value: string; configured: boolean; onChange(value: string): void; wide?: boolean }) { return <Field label={`${label}${configured ? ' · 已安全保存' : ''}`} wide={wide}><input aria-label={label} type="password" autoComplete="new-password" spellCheck={false} value={value} placeholder={configured ? '留空则保持现有凭证' : '输入后保存到系统安全凭证库'} onChange={(event) => onChange(event.target.value)} /></Field> }
function splitWords(value: string) { return value.trim() === '' ? [] : value.trim().split(/\s+/).slice(0, 32) }
function splitNames(value: string) { return value.trim() === '' ? [] : value.split(',').map((item) => item.trim()).filter(Boolean).slice(0, 32) }
function sourceLabel(source: string) { return source === 'official' ? '官方' : source === 'preview' ? '预览样例' : source === 'local' ? '本地' : '已安装插件' }
function kindLabel(kind: ConnectionKind) { return kind === 'mcp' ? 'MCP' : kind === 'http-api' ? 'HTTP API' : '数据库' }
function transportLabel(transport: ConnectionTransport) { return transport === 'stdio' ? 'stdio' : transport === 'sse' ? 'SSE' : transport === 'database' ? '数据库协议' : 'HTTP' }
type ConnectionScheme = 'mcp' | 'http-api' | 'yunxiao' | 'gitlab' | 'database'
function connectionScheme(draft: ConnectionDraft): ConnectionScheme {
  if (draft.templateId === 'yunxiao') return 'yunxiao'
  if (draft.templateId === 'gitlab') return 'gitlab'
  if (draft.kind === 'database') return 'database'
  return draft.kind
}
function createSchemeDraft(scheme: ConnectionScheme): ConnectionDraft {
  if (scheme === 'yunxiao') return { ...createConnectionDraft('mcp', 'http'), templateId: 'yunxiao', providerId: 'yunxiao', displayName: '云效需求读取', endpoint: 'https://openapi-rdc.aliyuncs.com', readOnly: true }
  if (scheme === 'gitlab') return { ...createConnectionDraft('mcp', 'http'), templateId: 'gitlab', providerId: 'gitlab', displayName: 'GitLab 代码读取', endpoint: 'https://gitlab.com', readOnly: true }
  if (scheme === 'database') return createConnectionDraft('database', 'database')
  return createConnectionDraft(scheme, scheme === 'mcp' ? 'stdio' : 'http')
}
function connectionSummary(profile: ConnectionProfile) {
  if (profile.kind === 'database') return `${profile.databaseType ?? 'database'} · ${profile.host ?? profile.endpoint}${profile.databaseName ? ` / ${profile.databaseName}` : ''}`
  return profile.transport === 'stdio' ? profile.command || '未填写命令' : profile.endpoint || '未填写地址'
}
