import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import type { CapabilitySectionProps } from './capability-locales.ts'
import { MCP_TEMPLATES, templateInitialValues, templateRequirementsMet, templateText, type McpTemplate, type McpTemplateBuild } from './mcp-templates.ts'

type McpTransport = 'stdio' | 'streamable-http'
interface McpServerView {
  readonly id: string; readonly serverName: string; readonly transport: McpTransport
  readonly command: string | null; readonly args: readonly string[]; readonly envKeys: readonly string[]
  readonly cwd: string | null; readonly url: string | null; readonly headerKeys: readonly string[]; readonly disabled: boolean
}
interface McpDraft {
  id: string; serverName: string; transport: McpTransport; command: string; argsText: string
  envText: string; envKeys: readonly string[]; cwd: string; url: string; headersText: string
  headerKeys: readonly string[]; disabled: boolean; dirty?: boolean
}
interface ProbeView { readonly ok: boolean; readonly toolCount?: number; readonly serverInfoName?: string; readonly detail?: string }
type ProbeState = { readonly kind: 'testing' | 'ok' | 'failed'; readonly result?: ProbeView }
type Translate = CapabilitySectionProps['t']

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: 'same-origin', redirect: 'error', ...init,
    headers: { accept: 'application/json', ...(init?.body === undefined ? {} : { 'content-type': 'application/json' }), ...init?.headers },
  })
  const value = await response.json().catch(() => undefined) as T | undefined
  if (!response.ok || value === undefined) throw new Error(`HTTP ${String(response.status)}`)
  return value
}

function idFrom(name: string): string {
  const normalized = name.trim().replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 48)
  return `${normalized || 'mcp'}-${Date.now().toString(36)}`
}
function draftFromView(row: McpServerView): McpDraft {
  return { id: row.id, serverName: row.serverName, transport: row.transport, command: row.command ?? '', argsText: row.args.join('\n'), envText: '', envKeys: row.envKeys, cwd: row.cwd ?? '', url: row.url ?? '', headersText: '', headerKeys: row.headerKeys, disabled: row.disabled }
}
function emptyDraft(): McpDraft {
  const name = `mcp-${Date.now().toString(36).slice(-4)}`
  return { id: idFrom(name), serverName: name, transport: 'stdio', command: '', argsText: '', envText: '', envKeys: [], cwd: '', url: '', headersText: '', headerKeys: [], disabled: false, dirty: true }
}
function parsePairs(text: string): Record<string, string> | undefined {
  const entries = text.split('\n').map(line => line.trim()).filter(Boolean).map(line => {
    const split = line.indexOf('=')
    if (split <= 0) throw new Error('invalid pair')
    return [line.slice(0, split).trim(), line.slice(split + 1)] as const
  })
  return entries.length === 0 ? undefined : Object.fromEntries(entries)
}
function rowPayload(row: McpDraft): Record<string, unknown> {
  return {
    id: row.id, serverName: row.serverName.trim(), transport: row.transport, disabled: row.disabled,
    ...(row.transport === 'stdio' ? {
      command: row.command.trim(), args: row.argsText.split('\n').map(value => value.trim()).filter(Boolean),
      ...(row.cwd.trim() ? { cwd: row.cwd.trim() } : {}), ...(row.envText.trim() ? { env: parsePairs(row.envText) } : {}),
    } : { url: row.url.trim(), ...(row.headersText.trim() ? { headers: parsePairs(row.headersText) } : {}) }),
  }
}
function importedDrafts(value: unknown): McpDraft[] {
  const source = value && typeof value === 'object' && !Array.isArray(value) && 'mcpServers' in value
    ? (value as { mcpServers?: unknown }).mcpServers : value
  const entries: Array<[string, Record<string, unknown>]> = Array.isArray(source)
    ? source.map((row, index) => [typeof row?.serverName === 'string' ? row.serverName : typeof row?.name === 'string' ? row.name : `mcp-${String(index + 1)}`, row]).filter((entry): entry is [string, Record<string, unknown>] => entry[1] !== null && typeof entry[1] === 'object')
    : source !== null && typeof source === 'object'
      ? Object.entries(source).filter((entry): entry is [string, Record<string, unknown>] => entry[1] !== null && typeof entry[1] === 'object') : []
  return entries.map(([name, raw]) => {
    const url = typeof raw.url === 'string' ? raw.url : ''
    const env = raw.env && typeof raw.env === 'object' ? raw.env as Record<string, unknown> : {}
    const headers = raw.headers && typeof raw.headers === 'object' ? raw.headers as Record<string, unknown> : {}
    const pairs = (record: Record<string, unknown>) => Object.entries(record)
      .filter((entry): entry is [string, string] => typeof entry[1] === 'string' && entry[1] !== '__REDACTED__')
      .map(([key, item]) => `${key}=${item}`).join('\n')
    const redactedKeys = (record: Record<string, unknown>) => Object.entries(record).filter(([, item]) => item === '__REDACTED__').map(([key]) => key)
    return {
      id: idFrom(name), serverName: name.replace(/[^A-Za-z0-9_-]+/g, '-').slice(0, 32), transport: url ? 'streamable-http' : 'stdio',
      command: typeof raw.command === 'string' ? raw.command : '', argsText: Array.isArray(raw.args) ? raw.args.filter((item): item is string => typeof item === 'string').join('\n') : '',
      envText: pairs(env), envKeys: redactedKeys(env), cwd: typeof raw.cwd === 'string' ? raw.cwd : '', url,
      headersText: pairs(headers), headerKeys: redactedKeys(headers), disabled: raw.disabled === true, dirty: true,
    }
  })
}
function uniqueName(base: string, rows: readonly McpDraft[]): string {
  const names = new Set(rows.map(row => row.serverName))
  if (!names.has(base)) return base
  for (let index = 2; index < 100; index += 1) if (!names.has(`${base}-${String(index)}`)) return `${base}-${String(index)}`
  return `${base}-${Date.now().toString(36).slice(-4)}`
}
function fromTemplate(template: McpTemplate, values: Readonly<Record<string, string>>, rows: readonly McpDraft[]): McpDraft {
  const name = uniqueName(template.serverName, rows)
  const built = template.build(values)
  return built.transport === 'stdio'
    ? { id: idFrom(name), serverName: name, transport: 'stdio', command: built.command, argsText: built.args.join('\n'), envText: Object.entries(built.env).map(([key, value]) => `${key}=${value}`).join('\n'), envKeys: [], cwd: '', url: '', headersText: '', headerKeys: [], disabled: false, dirty: true }
    : { id: idFrom(name), serverName: name, transport: 'streamable-http', command: '', argsText: '', envText: '', envKeys: [], cwd: '', url: built.url, headersText: Object.entries(built.headers).map(([key, value]) => `${key}=${value}`).join('\n'), headerKeys: [], disabled: false, dirty: true }
}
function safeMapKeys(text: string, stored: readonly string[]): string[] {
  try { return [...new Set([...stored, ...Object.keys(parsePairs(text) ?? {})])].sort() } catch { return [...stored] }
}
function exportConfig(row: McpDraft): Record<string, unknown> {
  return row.transport === 'stdio'
    ? { command: row.command, args: row.argsText.split('\n').filter(Boolean), ...(row.cwd ? { cwd: row.cwd } : {}), env: Object.fromEntries(safeMapKeys(row.envText, row.envKeys).map(key => [key, '__REDACTED__'])), ...(row.disabled ? { disabled: true } : {}) }
    : { url: row.url, headers: Object.fromEntries(safeMapKeys(row.headersText, row.headerKeys).map(key => [key, '__REDACTED__'])), ...(row.disabled ? { disabled: true } : {}) }
}
function downloadJson(name: string, rows: readonly McpDraft[]): void {
  const body = JSON.stringify({ mcpServers: Object.fromEntries(rows.map(row => [row.serverName, exportConfig(row)])) }, null, 2)
  const url = URL.createObjectURL(new Blob([`${body}\n`], { type: 'application/json' }))
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click(); URL.revokeObjectURL(url)
}
function cardSummary(row: McpDraft): string {
  if (row.transport === 'streamable-http') return row.url || '—'
  const first = row.argsText.split('\n').filter(Boolean).slice(0, 2).join(' ')
  return `${row.command}${first ? ` ${first}` : ''}` || '—'
}

function DraftEditor({ row, t, update }: { row: McpDraft; t: Translate; update: (patch: Partial<McpDraft>) => void }): ReactNode {
  return <div className="dshOverlayFormGrid dshOverlayMcpEditor">
    <label><span>{t('serverName')}</span><input className="dshOverlayInput" value={row.serverName} onChange={event => { update({ serverName: event.target.value, dirty: true }) }} /></label>
    <label><span>{t('transport')}</span><select className="dshOverlayInput" value={row.transport} onChange={event => { update({ transport: event.target.value as McpTransport, dirty: true }) }}><option value="stdio">{t('stdio')}</option><option value="streamable-http">{t('http')}</option></select></label>
    {row.transport === 'stdio' ? <>
      <label><span>{t('command')}</span><input className="dshOverlayInput" value={row.command} onChange={event => { update({ command: event.target.value, dirty: true }) }} placeholder="npx" /></label>
      <label><span>{t('workingDirectory')}</span><input className="dshOverlayInput" value={row.cwd} onChange={event => { update({ cwd: event.target.value, dirty: true }) }} /></label>
      <label className="dshOverlayFormWide"><span>{t('arguments')}</span><textarea className="dshOverlayTextarea" value={row.argsText} onChange={event => { update({ argsText: event.target.value, dirty: true }) }} /></label>
      <label className="dshOverlayFormWide"><span>{t('environment')}</span><textarea className="dshOverlayTextarea" value={row.envText} onChange={event => { update({ envText: event.target.value, dirty: true }) }} placeholder={row.envKeys.join('\n')} />{row.envKeys.length ? <small>{t('secretHint')} {row.envKeys.join(', ')}</small> : null}</label>
    </> : <>
      <label className="dshOverlayFormWide"><span>{t('url')}</span><input className="dshOverlayInput" value={row.url} onChange={event => { update({ url: event.target.value, dirty: true }) }} placeholder="https://host.example/mcp" /></label>
      <label className="dshOverlayFormWide"><span>{t('headers')}</span><textarea className="dshOverlayTextarea" value={row.headersText} onChange={event => { update({ headersText: event.target.value, dirty: true }) }} placeholder={row.headerKeys.join('\n')} />{row.headerKeys.length ? <small>{t('secretHint')} {row.headerKeys.join(', ')}</small> : null}</label>
    </>}
  </div>
}

function McpCard({ row, t, probe, expanded, update, remove, test, toggleExpanded }: {
  row: McpDraft; t: Translate; probe?: ProbeState; expanded: boolean
  update: (patch: Partial<McpDraft>) => void; remove: () => void; test: () => void; toggleExpanded: () => void
}): ReactNode {
  const stateLabel = probe?.kind === 'testing' ? t('testing') : probe?.kind === 'ok' ? t('testOk') : probe?.kind === 'failed' ? t('testFailed') : row.dirty ? t('pendingRestart') : t('connectionUnknown')
  return <article className="dshOverlayMcpCard" data-state={probe?.kind ?? (row.dirty ? 'pending' : 'unknown')}>
    <div className="dshOverlayMcpHead">
      <span className="dshOverlayMcpIdentity"><i className="dshOverlayStatusDot" /><span><strong>{row.serverName || t('customServer')}</strong><small>{row.transport === 'stdio' ? t('stdio') : t('http')} · {stateLabel}</small></span></span>
      <label className="dshOverlayToggle"><input type="checkbox" checked={!row.disabled} onChange={event => { update({ disabled: !event.target.checked, dirty: true }) }} />{row.disabled ? t('disabled') : t('enabled')}</label>
    </div>
    <p className="dshOverlayMcpSummary">{cardSummary(row)}</p>
    <div className="dshOverlayMcpChips">{safeMapKeys(row.envText, row.envKeys).map(key => <span key={`e-${key}`}>ENV · {key}</span>)}{safeMapKeys(row.headersText, row.headerKeys).map(key => <span key={`h-${key}`}>HEADER · {key}</span>)}</div>
    {expanded ? <DraftEditor row={row} t={t} update={update} /> : null}
    <div className="dshOverlayMcpActions">
      <button className="dshOverlayBtnSecondary" type="button" disabled={probe?.kind === 'testing'} onClick={test}>{probe?.kind === 'testing' ? t('testing') : t('test')}</button>
      <button className="dshOverlayBtnSecondary" type="button" onClick={toggleExpanded}>{expanded ? t('done') : t('edit')}</button>
      <button className="dshOverlayBtnSecondary" type="button" onClick={() => { downloadJson(`${row.serverName || 'mcp'}.json`, [row]) }}>{t('exportJson')}</button>
      <button className="dshOverlayBtnSecondary dshOverlayDanger" type="button" onClick={remove}>{t('delete')}</button>
      {probe?.result ? <span className={probe.kind === 'ok' ? 'dshOverlaySaved' : 'dshOverlayAlert'}>{probe.kind === 'ok' && probe.result.toolCount !== undefined ? `${String(probe.result.toolCount)} ${t('toolCount')}` : probe.result.detail ?? ''}</span> : null}
    </div>
  </article>
}

function TemplateConfigurator({ template, rows, t, close, add }: { template: McpTemplate; rows: readonly McpDraft[]; t: Translate; close: () => void; add: (row: McpDraft) => void }): ReactNode {
  const [values, setValues] = useState<Record<string, string>>(() => templateInitialValues(template))
  const valid = templateRequirementsMet(template, values)
  return <div className="dshOverlayTemplateConfig">
    <div className="dshOverlaySettingsHead"><span><h3>{templateText(template.name)}</h3><p className="dshOverlayHint">{templateText(template.description)}</p></span><button className="dshOverlayBtnSecondary" type="button" onClick={close}>{t('cancel')}</button></div>
    <div className="dshOverlayFormGrid">{template.fields.filter(field => field.visibleWhen?.(values) !== false).map(field => <label key={field.key}><span>{templateText(field.label)}{field.required ? ' *' : ''}</span>{field.options ? <select className="dshOverlayInput" value={values[field.key] ?? ''} onChange={event => { setValues(current => ({ ...current, [field.key]: event.target.value })) }}>{field.options.map(option => <option value={option.value} key={option.value}>{templateText(option.label)}</option>)}</select> : <input className="dshOverlayInput" type={field.secret ? 'password' : 'text'} value={values[field.key] ?? ''} placeholder={field.placeholder ? templateText(field.placeholder) : ''} onChange={event => { setValues(current => ({ ...current, [field.key]: event.target.value })) }} />}</label>)}</div>
    {!valid ? <p className="dshOverlayAlert">{t('requiredFields')}</p> : null}
    <button className="dshOverlayBtnPrimary" type="button" disabled={!valid} onClick={() => { add(fromTemplate(template, values, rows)); close() }}>{t('addNow')}</button>
  </div>
}

export function OverlayMcpSection({ t }: CapabilitySectionProps): ReactNode {
  const [rows, setRows] = useState<McpDraft[]>()
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())
  const [probes, setProbes] = useState<Readonly<Record<string, ProbeState>>>({})
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<'saved' | 'failed' | 'invalid' | 'duplicate'>()
  const [importText, setImportText] = useState('')
  const [template, setTemplate] = useState<McpTemplate>()
  const load = useCallback(() => { void jsonRequest<{ servers: McpServerView[] }>('/api/desktop/mcp').then(view => { setRows(view.servers.map(draftFromView)) }).catch(() => { setRows([]); setNote('failed') }) }, [])
  useEffect(load, [load])
  const duplicate = useMemo(() => rows !== undefined && new Set(rows.map(row => row.serverName.trim())).size !== rows.length, [rows])
  const update = (index: number, patch: Partial<McpDraft>) => { setRows(current => current?.map((row, rowIndex) => rowIndex === index ? { ...row, ...patch } : row)); setNote(undefined) }
  const save = () => {
    if (rows === undefined || duplicate) { setNote('duplicate'); return }
    setBusy(true); setNote(undefined)
    try {
      void jsonRequest('/api/desktop/mcp', { method: 'POST', body: JSON.stringify({ servers: rows.map(rowPayload) }) }).then(() => { setNote('saved'); load() }).catch(() => { setNote('failed') }).finally(() => { setBusy(false) })
    } catch { setNote('failed'); setBusy(false) }
  }
  const test = (row: McpDraft) => {
    setProbes(current => ({ ...current, [row.id]: { kind: 'testing' } }))
    let payload: Record<string, unknown>
    try { payload = rowPayload(row) } catch { setProbes(current => ({ ...current, [row.id]: { kind: 'failed', result: { ok: false, detail: t('saveFailed') } } })); return }
    void jsonRequest<ProbeView>('/api/desktop/mcp/test', { method: 'POST', body: JSON.stringify({ id: row.id, row: payload }) })
      .then(result => { setProbes(current => ({ ...current, [row.id]: { kind: result.ok ? 'ok' : 'failed', result } })) })
      .catch(() => { setProbes(current => ({ ...current, [row.id]: { kind: 'failed', result: { ok: false } } })) })
  }
  const importRows = () => {
    try {
      const parsed = importedDrafts(JSON.parse(importText)); if (!parsed.length) throw new Error('empty')
      setRows(current => [...(current ?? []), ...parsed]); setImportText(''); setNote(undefined)
      setExpanded(current => new Set([...current, ...parsed.map(row => row.id)]))
    } catch { setNote('invalid') }
  }
  return <div className="dshOverlaySettings">
    <header className="dshOverlaySettingsHero"><span className="dshOverlaySettingsHeroIcon">⌘</span><span><h2>{t('mcpTitle')}</h2><p>{t('mcpIntro')}</p></span></header>
    <section className="dshOverlaySettingsCard">
      <div className="dshOverlaySettingsHead"><h3>{t('templates')}</h3><div className="dshOverlayRow"><button className="dshOverlayBtnSecondary" type="button" disabled={!rows?.length} onClick={() => { if (rows?.length) downloadJson('dsh-mcp-servers.json', rows) }}>{t('exportAll')}</button><button className="dshOverlayBtnPrimary" type="button" onClick={() => { const row = emptyDraft(); setRows(current => [...(current ?? []), row]); setExpanded(current => new Set(current).add(row.id)) }}>＋ {t('addServer')}</button></div></div>
      <div className="dshOverlayTemplateGrid">{MCP_TEMPLATES.map(item => <button className="dshOverlayTemplateCard" type="button" key={item.id} onClick={() => {
        if (item.fields.length === 0) setRows(current => [...(current ?? []), fromTemplate(item, {}, current ?? [])])
        else setTemplate(item)
      }}><span className="dshOverlayTemplateIcon">{{ database: '▦', git: '⑂', cloud: '☁', globe: '◎', folder: '□', brain: '✦' }[item.icon]}</span><strong>{templateText(item.name)}</strong><small>{templateText(item.description)}</small><em>{item.fields.length ? t('configure') : t('addNow')}</em></button>)}</div>
      {template && rows ? <TemplateConfigurator key={template.id} template={template} rows={rows} t={t} close={() => { setTemplate(undefined) }} add={row => { setRows(current => [...(current ?? []), row]); setExpanded(current => new Set(current).add(row.id)) }} /> : null}
    </section>
    <section className="dshOverlaySettingsCard">
      <div className="dshOverlaySettingsHead"><h3>{t('mcpTitle')}{rows ? ` · ${String(rows.length)}` : ''}</h3></div>
      {rows === undefined ? <p className="dshOverlayHint">{t('mcpLoading')}</p> : null}
      {rows?.length === 0 ? <div className="dshOverlaySettingsEmpty">{t('mcpEmpty')}</div> : null}
      <div className="dshOverlayMcpList">{rows?.map((row, index) => <McpCard key={row.id} row={row} t={t} probe={probes[row.id]} expanded={expanded.has(row.id)} update={patch => { update(index, patch) }} test={() => { test(row) }} remove={() => { setRows(current => current?.filter((_, rowIndex) => rowIndex !== index)); setNote(undefined) }} toggleExpanded={() => { setExpanded(current => { const next = new Set(current); if (next.has(row.id)) next.delete(row.id); else next.add(row.id); return next }) }} />)}</div>
      <details className="dshOverlayImport"><summary>{t('importJson')}</summary><p className="dshOverlayHint">{t('importHint')}</p><textarea className="dshOverlayTextarea dshOverlayImportText" value={importText} onChange={event => { setImportText(event.target.value) }} placeholder={'{"mcpServers":{"example":{"command":"npx","args":["-y","server-package"]}}}'} /><button className="dshOverlayBtnSecondary" type="button" onClick={importRows}>{t('importJson')}</button></details>
      <p className="dshOverlayHint">{t('redactedExport')}</p>
      <div className="dshOverlaySettingsFooter"><button className="dshOverlayBtnPrimary" type="button" disabled={busy || rows === undefined} onClick={save}>{busy ? t('saving') : t('save')}</button>{note === 'saved' ? <span className="dshOverlaySaved">{t('savedRestart')}</span> : null}{note === 'failed' ? <span className="dshOverlayAlert">{t('saveFailed')}</span> : null}{note === 'invalid' ? <span className="dshOverlayAlert">{t('invalidJson')}</span> : null}{(note === 'duplicate' || duplicate) ? <span className="dshOverlayAlert">{t('duplicateName')}</span> : null}</div>
    </section>
  </div>
}
