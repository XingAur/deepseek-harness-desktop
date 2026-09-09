/** Desktop-owned MCP servers settings section editing the launcher's MCP rows. */

import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Brain, Cloud, Database, Folder, GitBranch, Globe, KeyRound, Plug, Plus, RefreshCw, Terminal, Trash2, X } from 'lucide-react'
import type { InjectFace, PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import {
  MCP_TEMPLATES,
  templateById,
  templateInitialValues,
  templateRequirementsMet,
  templateText,
  type McpTemplate,
} from './desktop-mcp-templates.ts'
import type {
  DesktopMcpServerView,
  DesktopMcpTransport,
  DesktopMcpServerWrite,
  DesktopMcpStateView,
  DesktopMcpTestView,
  DesktopSettingsApi,
} from './desktop-settings-api.ts'
import type { DesktopMcpLocaleKey } from './desktop-mcp-locales.ts'

/** Registration-side business face for the Desktop MCP settings section. */
export interface DesktopMcpSectionInjected {
  readonly api: DesktopSettingsApi
}

/** Renderer-composed props for the MCP settings section entry. */
export type DesktopMcpSectionProps =
  PropsRuntime<'settings.section'>
  & PropsLocale<'desktop.mcp'>
  & InjectFace<DesktopMcpSectionInjected>

type Translate = DesktopMcpSectionProps['t']

const MCP_ID_PREFIX = 'desktop-mcp-'
const MAX_MCP_TEXT_LENGTH = 2048
const MCP_SERVER_NAME_PATTERN = /^[A-Za-z0-9_-]{1,32}$/u

interface SecretPair {
  readonly key: string
  readonly value: string
}

/**
 * Secret edits accumulated in the current edit session. Stored values are
 * never known to the renderer: chips list stored key names, a typed pair sets
 * or replaces a value, and a removed key is sent as a `null` patch entry.
 */
interface SecretEdits {
  readonly storedKeys: readonly string[]
  readonly pairs: readonly SecretPair[]
  readonly removed: readonly string[]
}

/** Editable projection of one MCP row; an empty id marks a new server. */
interface ServerDraft {
  readonly id: string
  readonly serverName: string
  readonly transport: DesktopMcpTransport
  readonly command: string
  readonly argsText: string
  readonly cwd: string
  readonly url: string
  readonly disabled: boolean
  readonly env: SecretEdits
  readonly headers: SecretEdits
}

interface ImportEntry {
  readonly serverName: string
  readonly transport: DesktopMcpTransport
  readonly command?: string
  readonly args?: readonly string[]
  readonly env?: Readonly<Record<string, string>>
  readonly cwd?: string
  readonly url?: string
  readonly headers?: Readonly<Record<string, string>>
}

const EMPTY_SECRETS: SecretEdits = Object.freeze({
  storedKeys: Object.freeze([]),
  pairs: Object.freeze([]),
  removed: Object.freeze([]),
})

function emptyDraft(): ServerDraft {
  return {
    id: '',
    serverName: '',
    transport: 'stdio',
    command: '',
    argsText: '',
    cwd: '',
    url: '',
    disabled: false,
    env: EMPTY_SECRETS,
    headers: EMPTY_SECRETS,
  }
}

const TEMPLATE_ICONS = { database: Database, git: GitBranch, cloud: Cloud, globe: Globe, folder: Folder, brain: Brain } as const

function secretsFromEntries(entries: Readonly<Record<string, string>>): SecretEdits {
  return {
    storedKeys: [],
    pairs: Object.entries(entries).map(([key, value]) => ({ key, value })),
    removed: [],
  }
}

/** Assemble a full draft from a template and its friendly field values. */
function templateToDraft(
  template: McpTemplate,
  values: Readonly<Record<string, string>>,
  base: Pick<ServerDraft, 'id' | 'serverName' | 'disabled'>,
): ServerDraft {
  const built = template.build(values)
  if (built.transport === 'stdio') {
    return {
      id: base.id,
      serverName: base.serverName,
      transport: 'stdio',
      command: built.command,
      argsText: built.args.join('\n'),
      cwd: '',
      url: '',
      disabled: base.disabled,
      env: secretsFromEntries(built.env),
      headers: EMPTY_SECRETS,
    }
  }
  return {
    id: base.id,
    serverName: base.serverName,
    transport: 'streamable-http',
    command: '',
    argsText: '',
    cwd: '',
    url: built.url,
    disabled: base.disabled,
    env: EMPTY_SECRETS,
    headers: secretsFromEntries(built.headers),
  }
}

function draftFromServer(server: DesktopMcpServerView): ServerDraft {
  return {
    id: server.id,
    serverName: server.serverName,
    transport: server.transport,
    command: server.command ?? '',
    argsText: server.args.join('\n'),
    cwd: server.cwd ?? '',
    url: server.url ?? '',
    disabled: server.disabled,
    env: { storedKeys: server.envKeys, pairs: [], removed: [] },
    headers: { storedKeys: server.headerKeys, pairs: [], removed: [] },
  }
}

/**
 * Rebuild a stored row's full non-secret fields so the replacing write keeps
 * them; without an explicit secret patch the stored values stay unchanged.
 */
function rowFromServer(server: DesktopMcpServerView): DesktopMcpServerWrite {
  return {
    id: server.id,
    serverName: server.serverName,
    transport: server.transport,
    ...(server.command !== null ? { command: server.command } : {}),
    ...(server.args.length > 0 ? { args: [...server.args] } : {}),
    ...(server.cwd !== null ? { cwd: server.cwd } : {}),
    ...(server.url !== null ? { url: server.url } : {}),
    disabled: server.disabled,
  }
}

/** Three-state patch: typed pairs set values, removed keys map to `null`. */
function secretPatch(edits: SecretEdits): Readonly<Record<string, string | null>> | undefined {
  if (edits.pairs.length === 0 && edits.removed.length === 0) return undefined
  const patch: Record<string, string | null> = {}
  for (const key of edits.removed) patch[key] = null
  for (const pair of edits.pairs) {
    if (pair.key.length > 0) patch[pair.key] = pair.value
  }
  return patch
}

function rowFromDraft(draft: ServerDraft): DesktopMcpServerWrite {
  const args = draft.argsText.split('\n').map(line => line.trim()).filter(line => line.length > 0)
  const env = secretPatch(draft.env)
  const headers = secretPatch(draft.headers)
  return {
    id: draft.id === '' ? `${MCP_ID_PREFIX}${draft.serverName.trim()}` : draft.id,
    serverName: draft.serverName.trim(),
    transport: draft.transport,
    ...(draft.transport === 'stdio' && draft.command.trim().length > 0 ? { command: draft.command.trim() } : {}),
    ...(draft.transport === 'stdio' && args.length > 0 ? { args } : {}),
    ...(draft.transport === 'stdio' && draft.cwd.trim().length > 0 ? { cwd: draft.cwd.trim() } : {}),
    ...(draft.transport === 'streamable-http' && draft.url.trim().length > 0 ? { url: draft.url.trim() } : {}),
    ...(env === undefined ? {} : { env }),
    ...(headers === undefined ? {} : { headers }),
    disabled: draft.disabled,
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function importString(record: Record<string, unknown>, key: string): string | undefined {
  const value = record[key]
  return typeof value === 'string' && value.length > 0 && value.length <= MAX_MCP_TEXT_LENGTH
    ? value
    : undefined
}

function importStringList(record: Record<string, unknown>, key: string): readonly string[] | undefined {
  const value = record[key]
  if (!Array.isArray(value)) return undefined
  const list: string[] = []
  for (const entry of value) {
    if (typeof entry !== 'string' || entry.length > MAX_MCP_TEXT_LENGTH) return undefined
    list.push(entry)
  }
  return list
}

function importStringRecord(value: unknown): Readonly<Record<string, string>> | undefined {
  if (!isRecord(value)) return undefined
  const record: Record<string, string> = {}
  for (const [key, entry] of Object.entries(value)) {
    if (key.length === 0 || typeof entry !== 'string' || entry.length > MAX_MCP_TEXT_LENGTH) return undefined
    record[key] = entry
  }
  return record
}

function importEntry(name: unknown, config: unknown): ImportEntry | undefined {
  if (typeof name !== 'string' || !MCP_SERVER_NAME_PATTERN.test(name) || !isRecord(config)) return undefined
  const url = importString(config, 'url')
  const command = importString(config, 'command')
  const transport: DesktopMcpTransport | undefined = url !== undefined
    ? 'streamable-http'
    : command !== undefined ? 'stdio' : undefined
  if (transport === undefined) return undefined
  const args = importStringList(config, 'args')
  const env = importStringRecord(config.env)
  const headers = importStringRecord(config.headers)
  const cwd = importString(config, 'cwd')
  return {
    serverName: name,
    transport,
    ...(transport === 'stdio' && command !== undefined ? { command } : {}),
    ...(transport === 'stdio' && args !== undefined ? { args } : {}),
    ...(transport === 'stdio' && cwd !== undefined ? { cwd } : {}),
    ...(transport === 'streamable-http' && url !== undefined ? { url } : {}),
    ...(env !== undefined ? { env } : {}),
    ...(headers !== undefined ? { headers } : {}),
  }
}

/** Accept Claude Desktop `{mcpServers:{...}}`/name maps or a JSON array. */
function parseMcpImport(text: string): readonly ImportEntry[] | undefined {
  let parsed: unknown
  try {
    parsed = JSON.parse(text) as unknown
  } catch {
    return undefined
  }
  const rows: [name: unknown, config: unknown][] = []
  if (Array.isArray(parsed)) {
    for (const item of parsed) {
      if (!isRecord(item)) return undefined
      rows.push([item.serverName ?? item.name, item])
    }
  } else if (isRecord(parsed)) {
    const map = isRecord(parsed.mcpServers) ? parsed.mcpServers : parsed
    for (const [name, config] of Object.entries(map)) rows.push([name, config])
  } else {
    return undefined
  }
  const entries: ImportEntry[] = []
  for (const [name, config] of rows) {
    const entry = importEntry(name, config)
    if (entry === undefined) return undefined
    entries.push(entry)
  }
  return entries
}

/** Imported values count as typed this session, so they travel as patches. */
function rowFromImport(entry: ImportEntry): DesktopMcpServerWrite {
  return {
    id: `${MCP_ID_PREFIX}${entry.serverName}`,
    serverName: entry.serverName,
    transport: entry.transport,
    ...(entry.command !== undefined ? { command: entry.command } : {}),
    ...(entry.args !== undefined ? { args: entry.args } : {}),
    ...(entry.cwd !== undefined ? { cwd: entry.cwd } : {}),
    ...(entry.url !== undefined ? { url: entry.url } : {}),
    ...(entry.env !== undefined ? { env: entry.env } : {}),
    ...(entry.headers !== undefined ? { headers: entry.headers } : {}),
    disabled: false,
  }
}

/** One stored key chip; its value is unknown and only the name can be removed. */
function StoredKeyChip({ name, disabled, t, onRemove }: {
  name: string
  disabled: boolean
  t: Translate
  onRemove: () => void
}) {
  return (
    <span className="dshDesktopSettingsToggleRow">
      <span className="dshDesktopSettingsToggleLabel">
        <KeyRound width={13} height={13} aria-hidden="true" />
        {name}
      </span>
      <button
        type="button"
        className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
        disabled={disabled}
        aria-label={`${t('removeValue')}: ${name}`}
        onClick={onRemove}
      >
        <X width={13} height={13} />
      </button>
    </span>
  )
}

/** Blind secret editor: stored key chips plus empty key/value pair inputs. */
function SecretFields({ label, hint, edits, disabled, t, onChange }: {
  label: string
  hint: string
  edits: SecretEdits
  disabled: boolean
  t: Translate
  onChange: (edits: SecretEdits) => void
}) {
  const liveKeys = edits.storedKeys.filter(key => !edits.removed.includes(key))
  const addPair = (): void => {
    onChange({ ...edits, pairs: [...edits.pairs, { key: '', value: '' }] })
  }
  const updatePair = (index: number, pair: SecretPair): void => {
    onChange({ ...edits, pairs: edits.pairs.map((current, at) => at === index ? pair : current) })
  }
  const removePair = (index: number): void => {
    onChange({ ...edits, pairs: edits.pairs.filter((_current, at) => at !== index) })
  }
  const removeStored = (key: string): void => {
    onChange({ ...edits, removed: edits.removed.includes(key) ? edits.removed : [...edits.removed, key] })
  }
  return (
    <div className="dshDesktopSettingsField">
      <span className="dshDesktopSettingsChoiceTitle">{label}</span>
      {liveKeys.map(key => (
        <StoredKeyChip key={key} name={key} disabled={disabled} t={t} onRemove={() => { removeStored(key) }} />
      ))}
      {edits.pairs.map((pair, index) => (
        <span key={index} className="dshDesktopSettingsToggleRow">
          <input
            className="dshDesktopSettingsInput"
            value={pair.key}
            placeholder={label}
            maxLength={MAX_MCP_TEXT_LENGTH}
            autoComplete="off"
            disabled={disabled}
            aria-label={label}
            onChange={event => { updatePair(index, { key: event.currentTarget.value, value: pair.value }) }}
          />
          <input
            className="dshDesktopSettingsInput"
            value={pair.value}
            placeholder='••••••'
            maxLength={MAX_MCP_TEXT_LENGTH}
            autoComplete="off"
            disabled={disabled}
            aria-label={label}
            onChange={event => { updatePair(index, { key: pair.key, value: event.currentTarget.value }) }}
          />
          <button
            type="button"
            className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
            disabled={disabled}
            aria-label={t('cancel')}
            onClick={() => { removePair(index) }}
          >
            <X width={13} height={13} />
          </button>
        </span>
      ))}
      <span className="dshDesktopSettingsHint">{hint}</span>
      <button
        type="button"
        className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
        disabled={disabled}
        onClick={addPair}
      >
        {t('addValue')}
      </button>
    </div>
  )
}

/** One server card: name, transport badge, enable toggle, edit, and delete. */
function ServerRow({ server, t, busy, pendingDelete, testing, testLine, onToggle, onEdit, onDeleteRequest, onDelete, onCancelDelete, onTest }: {
  server: DesktopMcpServerView
  t: Translate
  busy: boolean
  pendingDelete: boolean
  testing: boolean
  testLine: ReactNode
  onToggle: (server: DesktopMcpServerView) => void
  onEdit: (server: DesktopMcpServerView) => void
  onDeleteRequest: (id: string) => void
  onDelete: (server: DesktopMcpServerView) => void
  onCancelDelete: () => void
  onTest: (server: DesktopMcpServerView) => void
}) {
  const summary = server.transport === 'stdio'
    ? [server.command ?? '', ...server.args].filter(part => part.length > 0).join(' ')
    : server.url ?? ''
  return (
    <div className="dshDesktopSettingsRow" role="listitem">
      <span className="dshDesktopSettingsRowIcon" aria-hidden="true">
        {server.transport === 'stdio' ? <Terminal /> : <Globe />}
      </span>
      <span className="dshDesktopSettingsRowCopy">
        <span className="dshDesktopSettingsChoiceTitle">
          {server.serverName}
          <span className="dshDesktopSettingsBadge">
            {t(server.transport === 'stdio' ? 'transportStdio' : 'transportHttp')}
          </span>
          <span className="dshDesktopSettingsStatus" data-state={server.disabled ? 'off' : 'on'}>
            {t(server.disabled ? 'disabled' : 'enabled')}
          </span>
        </span>
        {summary.length > 0 && <span className="dshDesktopSettingsRowMeta">{summary}</span>}
        {testLine}
        {(server.transport === 'stdio' && server.envKeys.length > 0) && (
          <span className="dshDesktopSettingsChipRow">
            {server.envKeys.map(key => <span key={key} className="dshDesktopSettingsChip">{key}</span>)}
          </span>
        )}
        {(server.transport === 'streamable-http' && server.headerKeys.length > 0) && (
          <span className="dshDesktopSettingsChipRow">
            {server.headerKeys.map(key => <span key={key} className="dshDesktopSettingsChip">{key}</span>)}
          </span>
        )}
      </span>
      <span className="dshDesktopSettingsRowActions">
        {pendingDelete ? (
          <div className="dshDesktopSettingsDeleteConfirm" role="group" aria-label={t('deleteConfirm')}>
            <span className="dshDesktopSettingsDeleteWarning">{t('deleteConfirm')}</span>
            <span className="dshDesktopSettingsDeleteActions">
              <button
                type="button"
                className="dshDesktopSettingsButton dshDesktopSettingsButtonDanger"
                disabled={busy}
                onClick={() => { onDelete(server) }}
              >
                <Trash2 width={13} height={13} />
                {t('delete')}
              </button>
              <button
                type="button"
                className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
                disabled={busy}
                onClick={onCancelDelete}
              >
                {t('cancel')}
              </button>
            </span>
          </div>
        ) : (
          <>
            <button
              type="button"
              className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
              disabled={busy}
              onClick={() => { onTest(server) }}
            >
              {testing === true ? t('testing') : t('test')}
            </button>
            <button
              type="button"
              className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
              disabled={busy}
              onClick={() => { onToggle(server) }}
            >
              {t(server.disabled ? 'enable' : 'disable')}
            </button>
            <button
              type="button"
              className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
              disabled={busy}
              onClick={() => { onEdit(server) }}
            >
              {t('edit')}
            </button>
            <button
              type="button"
              className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
              disabled={busy}
              aria-label={t('delete')}
              onClick={() => { onDeleteRequest(server.id) }}
            >
              <Trash2 width={13} height={13} />
            </button>
          </>
        )}
      </span>
    </div>
  )
}

/** Render the Desktop MCP servers settings page. */
export function DesktopMcpSection({ t, api }: DesktopMcpSectionProps): ReactNode {
  const [view, setView] = useState<DesktopMcpStateView>()
  const [failed, setFailed] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [saveFailed, setSaveFailed] = useState(false)
  const [draft, setDraft] = useState<ServerDraft>()
  const [draftError, setDraftError] = useState<DesktopMcpLocaleKey>()
  const [pendingDeleteId, setPendingDeleteId] = useState<string>()
  const [importOpen, setImportOpen] = useState(false)
  const [importText, setImportText] = useState('')
  const [importStatus, setImportStatus] = useState<'invalid' | 'duplicate'>()
  const [chooserOpen, setChooserOpen] = useState(false)
  const [templateId, setTemplateId] = useState<string>()
  const [templateValues, setTemplateValues] = useState<Record<string, string>>({})
  const [testingId, setTestingId] = useState<string>()
  const [testResult, setTestResult] = useState<{ readonly key: string; readonly view: DesktopMcpTestView }>()

  const load = useCallback(async () => {
    setFailed(false)
    try {
      setView(await api.getMcp())
    } catch {
      setFailed(true)
    }
  }, [api])

  useEffect(() => { void load() }, [load, reloadKey])

  /** Replace the whole row list; the response refreshes the rendered rows. */
  const persist = useCallback(async (servers: readonly DesktopMcpServerWrite[]) => {
    setBusy(true)
    setSaveFailed(false)
    setSaved(false)
    try {
      const write = await api.putMcp(servers)
      setView({ servers: write.servers, restartRequired: write.restartScheduled })
      setSaved(write.restartScheduled)
      setDraft(undefined)
      setDraftError(undefined)
      setPendingDeleteId(undefined)
      setImportOpen(false)
      setImportText('')
      setImportStatus(undefined)
    } catch {
      setSaveFailed(true)
    } finally {
      setBusy(false)
    }
  }, [api])

  const updateDraft = (patch: Partial<ServerDraft>): void => {
    setDraft(current => current === undefined ? current : { ...current, ...patch })
  }
  const updateEnv = (edits: SecretEdits): void => {
    setDraft(current => current === undefined ? current : { ...current, env: edits })
  }
  const updateHeaders = (edits: SecretEdits): void => {
    setDraft(current => current === undefined ? current : { ...current, headers: edits })
  }

  const startEdit = (server: DesktopMcpServerView): void => {
    setPendingDeleteId(undefined)
    setDraftError(undefined)
    setTemplateId(undefined)
    setTemplateValues({})
    setDraft(draftFromServer(server))
  }

  const activeTemplate = templateId !== undefined ? templateById(templateId) : undefined

  const chooseTemplate = (template: McpTemplate): void => {
    const values = templateInitialValues(template)
    setTemplateId(template.id)
    setTemplateValues(values)
    setChooserOpen(false)
    setDraftError(undefined)
    setDraft(templateToDraft(template, values, { id: '', serverName: template.serverName, disabled: false }))
  }

  const startBlankDraft = (): void => {
    setTemplateId(undefined)
    setTemplateValues({})
    setChooserOpen(false)
    setDraftError(undefined)
    setDraft(emptyDraft())
  }

  const updateTemplateValue = (key: string, value: string): void => {
    if (templateId === undefined || draft === undefined) return
    const template = templateById(templateId)
    if (template === undefined) return
    const values = { ...templateValues, [key]: value }
    setTemplateValues(values)
    setDraft(templateToDraft(template, values, { id: draft.id, serverName: draft.serverName, disabled: draft.disabled }))
  }

  const closeDraft = (): void => {
    setDraft(undefined)
    setDraftError(undefined)
    setTemplateId(undefined)
    setTemplateValues({})
  }

  const runTest = (row: DesktopMcpServerWrite, id?: string): void => {
    const key = id ?? 'draft'
    setTestingId(key)
    setTestResult(undefined)
    void api.testMcp(row, id).then(view => {
      setTestResult({ key, view })
    }).catch(() => {
      setTestResult({ key, view: { ok: false, error: 'protocol', detail: 'probe unavailable' } })
    }).finally(() => {
      setTestingId(undefined)
    })
  }

  const testResultLine = (key: string): ReactNode => {
    if (testingId === key) return <span className="dshDesktopSettingsHint">{t('testing')}</span>
    if (testResult === undefined || testResult.key !== key) return undefined
    const { view } = testResult
    if (view.ok) {
      return (
        <span className="dshDesktopSettingsCallout" data-tone="success" role="status">
          <RefreshCw aria-hidden="true" />
          <span>
            {t('testOk')}
            {view.toolCount !== undefined ? ` · ${String(view.toolCount)} ${t('testToolsUnit')}` : ''}
            {view.serverInfoName !== undefined ? ` · ${view.serverInfoName}` : ''}
          </span>
        </span>
      )
    }
    return (
      <span className="dshDesktopSettingsCallout" data-tone="info" role="status">
        <X aria-hidden="true" />
        <span>
          {`${t('testFailed')}${view.detail !== undefined ? `: ${view.detail}` : ''}`}
        </span>
      </span>
    )
  }

  const submitDraft = (event: FormEvent): void => {
    event.preventDefault()
    if (draft === undefined || busy) return
    const name = draft.serverName.trim().length > 0
      ? draft.serverName.trim()
      : activeTemplate !== undefined
        ? activeTemplate.serverName
        : ''
    if (name.length === 0 || !MCP_SERVER_NAME_PATTERN.test(name)) {
      setDraftError('nameRequired')
      return
    }
    if ((view?.servers ?? []).some(server => server.serverName === name && server.id !== draft.id)) {
      setDraftError('duplicateName')
      return
    }
    if (draft.transport === 'stdio' && draft.command.trim().length === 0) {
      setDraftError('commandRequired')
      return
    }
    if (draft.transport === 'streamable-http' && draft.url.trim().length === 0) {
      setDraftError('urlRequired')
      return
    }
    setDraftError(undefined)
    const kept = (view?.servers ?? [])
      .filter(server => server.id !== draft.id)
      .map(rowFromServer)
    void persist([...kept, rowFromDraft({ ...draft, serverName: name })])
  }

  const toggleServer = (server: DesktopMcpServerView): void => {
    if (busy) return
    const kept = (view?.servers ?? [])
      .filter(candidate => candidate.id !== server.id)
      .map(rowFromServer)
    void persist([...kept, { ...rowFromServer(server), disabled: !server.disabled }])
  }

  const deleteServer = (server: DesktopMcpServerView): void => {
    if (busy) return
    void persist(
      (view?.servers ?? [])
        .filter(candidate => candidate.id !== server.id)
        .map(rowFromServer),
    )
  }

  const applyImport = (): void => {
    if (busy) return
    const entries = parseMcpImport(importText)
    if (entries === undefined) {
      setImportStatus('invalid')
      return
    }
    const existing = view?.servers ?? []
    const names = new Set(existing.map(server => server.serverName))
    const additions: DesktopMcpServerWrite[] = []
    let duplicate = false
    for (const entry of entries) {
      if (names.has(entry.serverName)) {
        duplicate = true
        continue
      }
      names.add(entry.serverName)
      additions.push(rowFromImport(entry))
    }
    setImportStatus(duplicate ? 'duplicate' : undefined)
    if (additions.length > 0) {
      void persist([...existing.map(rowFromServer), ...additions])
    }
  }

  return (
    <div className="dshDesktopSettings">
      <header className="dshDesktopSettingsHero">
        <span className="dshDesktopSettingsHeroIcon" aria-hidden="true"><Plug /></span>
        <span>
          <h2>{t('title')}</h2>
          <p>{t('description')}</p>
        </span>
      </header>

      {saved && (
        <p className="dshDesktopSettingsCallout" data-tone="success" role="status">
          <RefreshCw aria-hidden="true" />
          <span>{t('restartRequired')}</span>
        </p>
      )}
      {saveFailed && <p className="dshDesktopSettingsError" role="alert">{t('saveFailed')}</p>}

      <section className="dshDesktopSettingsCard" aria-label={t('title')}>
        {failed && view === undefined && (
          <div>
            <p className="dshDesktopSettingsError" role="alert">{t('unavailable')}</p>
            <button
              type="button"
              className="dshDesktopSettingsPrimary"
              onClick={() => { setReloadKey(key => key + 1) }}
            >
              <RefreshCw />
              {t('retry')}
            </button>
          </div>
        )}
        {!failed && view === undefined && <p className="dshDesktopSettingsHint">{t('loading')}</p>}
        {view !== undefined && (
          <>
            <div className="dshDesktopSettingsCardHead">
              <h3>{t('title')}{view.servers.length > 0 ? ` · ${String(view.servers.length)}` : ''}</h3>
              <span className="dshDesktopSettingsCardHeadActions">
                {!importOpen && (
                  <button
                    type="button"
                    className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
                    disabled={busy}
                    onClick={() => { setImportOpen(true); setImportStatus(undefined) }}
                  >
                    {t('import')}
                  </button>
                )}
                {draft === undefined && (
                  <button
                    type="button"
                    className="dshDesktopSettingsPrimary"
                    disabled={busy}
                    onClick={() => { setChooserOpen(true); setDraftError(undefined) }}
                  >
                    <Plus />
                    {t('addServer')}
                  </button>
                )}
              </span>
            </div>
            {view.servers.length === 0
              ? (
                <div className="dshDesktopSettingsEmpty">
                  <Plug aria-hidden="true" />
                  <span className="dshDesktopSettingsEmptyStrong">{t('empty')}</span>
                  <p className="dshDesktopSettingsEmptyBody">{t('description')}</p>
                </div>
              )
              : (
                <div className="dshDesktopSettingsList" role="list" aria-label={t('title')}>
                  {view.servers.map(server => (
                    <ServerRow
                      key={server.id}
                      server={server}
                      t={t}
                      busy={busy}
                      pendingDelete={pendingDeleteId === server.id}
                      testing={testingId === server.id}
                      testLine={testResultLine(server.id)}
                      onToggle={toggleServer}
                      onEdit={startEdit}
                      onDeleteRequest={setPendingDeleteId}
                      onDelete={deleteServer}
                      onCancelDelete={() => { setPendingDeleteId(undefined) }}
                      onTest={candidate => { runTest(rowFromServer(candidate), candidate.id) }}
                    />
                  ))}
                </div>
              )}
            {chooserOpen && draft === undefined && (
              <div>
                <p className="dshDesktopSettingsGroupIntro">{t('templateTitle')}</p>
                <div className="dshDesktopTemplateGrid" role="list" aria-label={t('templateTitle')}>
                  {MCP_TEMPLATES.map(template => {
                    const Icon = TEMPLATE_ICONS[template.icon]
                    return (
                      <button
                        key={template.id}
                        type="button"
                        className="dshDesktopTemplateCard"
                        onClick={() => { chooseTemplate(template) }}
                      >
                        <Icon aria-hidden="true" />
                        <span className="dshDesktopTemplateCardStrong">{templateText(template.name)}</span>
                        <span className="dshDesktopTemplateCardBody">{templateText(template.description)}</span>
                      </button>
                    )
                  })}
                  <button type="button" className="dshDesktopTemplateCard" onClick={startBlankDraft}>
                    <Terminal aria-hidden="true" />
                    <span className="dshDesktopTemplateCardStrong">{t('templateBlank')}</span>
                    <span className="dshDesktopTemplateCardBody">{t('templateAdvancedNote')}</span>
                  </button>
                </div>
              </div>
            )}
            {draft !== undefined && activeTemplate !== undefined && (
              <form className="dshDesktopSettingsPanel" onSubmit={submitDraft}>
                <p className="dshDesktopSettingsHint">{t('templateAdvancedNote')}</p>
                <div className="dshDesktopSettingsFormGrid">
                  <label className="dshDesktopSettingsField">
                    {t('serverName')}
                    <input
                      className="dshDesktopSettingsInput"
                      value={draft.serverName}
                      maxLength={32}
                      autoComplete="off"
                      disabled={busy}
                      onChange={event => { updateDraft({ serverName: event.currentTarget.value }) }}
                    />
                    <span className="dshDesktopSettingsHint">{t('nameOptionalHint')}</span>
                  </label>
                  {activeTemplate.fields.map(field => (
                    <label key={field.key} className="dshDesktopSettingsField">
                      {templateText(field.label)}{field.required === true ? ' *' : ''}
                      {field.options !== undefined ? (
                        <select
                          className="dshDesktopSettingsSelect"
                          value={templateValues[field.key] ?? ''}
                          disabled={busy}
                          onChange={event => { updateTemplateValue(field.key, event.currentTarget.value) }}
                        >
                          {field.options.map(option => (
                            <option key={option.value} value={option.value}>{templateText(option.label)}</option>
                          ))}
                        </select>
                      ) : (
                        <input
                          className="dshDesktopSettingsInput"
                          type={field.secret === true ? 'password' : 'text'}
                          value={templateValues[field.key] ?? ''}
                          placeholder={field.placeholder !== undefined ? templateText(field.placeholder) : undefined}
                          autoComplete="off"
                          disabled={busy}
                          onChange={event => { updateTemplateValue(field.key, event.currentTarget.value) }}
                        />
                      )}
                    </label>
                  ))}
                </div>
                {draftError !== undefined && <p className="dshDesktopSettingsError" role="alert">{t(draftError)}</p>}
                {testResultLine(draft.id === '' ? 'draft' : draft.id)}
                <div className="dshDesktopSettingsDeleteActions">
                  <button
                    type="button"
                    className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
                    disabled={busy}
                    onClick={() => { closeDraft(); setChooserOpen(true) }}
                  >
                    {t('templateTitle')}
                  </button>
                  <button
                    type="button"
                    className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
                    disabled={busy || testingId === (draft.id === '' ? 'draft' : draft.id)}
                    onClick={() => { runTest(rowFromDraft(draft), draft.id === '' ? undefined : draft.id) }}
                  >
                    {testingId === (draft.id === '' ? 'draft' : draft.id) ? t('testing') : t('test')}
                  </button>
                  <button
                    type="button"
                    className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
                    disabled={busy}
                    onClick={closeDraft}
                  >
                    {t('cancel')}
                  </button>
                  <button
                    type="submit"
                    className="dshDesktopSettingsPrimary"
                    disabled={busy || !templateRequirementsMet(activeTemplate, templateValues)}
                  >
                    {t('save')}
                  </button>
                </div>
              </form>
            )}
            {draft !== undefined && activeTemplate === undefined && (
              <form className="dshDesktopSettingsPanel" onSubmit={submitDraft}>
                <div className="dshDesktopSettingsFormGrid">
                  <label className="dshDesktopSettingsField">
                    {t('serverName')}
                    <input
                      className="dshDesktopSettingsInput"
                      value={draft.serverName}
                      maxLength={32}
                      autoComplete="off"
                      disabled={busy}
                      onChange={event => { updateDraft({ serverName: event.currentTarget.value }) }}
                    />
                  </label>
                  <label className="dshDesktopSettingsField">
                    {t('transport')}
                    <select
                      className="dshDesktopSettingsSelect"
                      value={draft.transport}
                      disabled={busy}
                      onChange={event => {
                        updateDraft({
                          transport: event.currentTarget.value === 'streamable-http' ? 'streamable-http' : 'stdio',
                        })
                      }}
                    >
                      <option value="stdio">{t('transportStdio')}</option>
                      <option value="streamable-http">{t('transportHttp')}</option>
                    </select>
                  </label>
                  {draft.transport === 'stdio' && (
                    <>
                      <label className="dshDesktopSettingsField">
                        {t('command')}
                        <input
                          className="dshDesktopSettingsInput"
                          value={draft.command}
                          maxLength={MAX_MCP_TEXT_LENGTH}
                          autoComplete="off"
                          disabled={busy}
                          onChange={event => { updateDraft({ command: event.currentTarget.value }) }}
                        />
                      </label>
                      <label className="dshDesktopSettingsField">
                        {t('cwd')}
                        <input
                          className="dshDesktopSettingsInput"
                          value={draft.cwd}
                          maxLength={MAX_MCP_TEXT_LENGTH}
                          autoComplete="off"
                          disabled={busy}
                          onChange={event => { updateDraft({ cwd: event.currentTarget.value }) }}
                        />
                      </label>
                      <label className="dshDesktopSettingsField">
                        {t('args')}
                        <textarea
                          className="dshDesktopSettingsInput"
                          value={draft.argsText}
                          rows={3}
                          autoComplete="off"
                          disabled={busy}
                          onChange={event => { updateDraft({ argsText: event.currentTarget.value }) }}
                        />
                        <span className="dshDesktopSettingsHint">{t('argsHint')}</span>
                      </label>
                      <SecretFields
                        label={t('env')}
                        hint={t('envHint')}
                        edits={draft.env}
                        disabled={busy}
                        t={t}
                        onChange={updateEnv}
                      />
                    </>
                  )}
                  {draft.transport === 'streamable-http' && (
                    <>
                      <label className="dshDesktopSettingsField">
                        {t('url')}
                        <input
                          className="dshDesktopSettingsInput"
                          value={draft.url}
                          maxLength={MAX_MCP_TEXT_LENGTH}
                          autoComplete="off"
                          disabled={busy}
                          onChange={event => { updateDraft({ url: event.currentTarget.value }) }}
                        />
                      </label>
                      <SecretFields
                        label={t('headers')}
                        hint={t('headersHint')}
                        edits={draft.headers}
                        disabled={busy}
                        t={t}
                        onChange={updateHeaders}
                      />
                    </>
                  )}
                </div>
                {draftError !== undefined && <p className="dshDesktopSettingsError" role="alert">{t(draftError)}</p>}
                {testResultLine(draft.id === '' ? 'draft' : draft.id)}
                <div className="dshDesktopSettingsDeleteActions">
                  <button
                    type="button"
                    className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
                    disabled={busy}
                    onClick={() => { setDraft(undefined); setDraftError(undefined) }}
                  >
                    {t('cancel')}
                  </button>
                  <button
                    type="button"
                    className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
                    disabled={busy || testingId === (draft.id === '' ? 'draft' : draft.id)}
                    onClick={() => { runTest(rowFromDraft(draft), draft.id === '' ? undefined : draft.id) }}
                  >
                    {testingId === (draft.id === '' ? 'draft' : draft.id) ? t('testing') : t('test')}
                  </button>
                  <button type="submit" className="dshDesktopSettingsPrimary" disabled={busy}>{t('save')}</button>
                </div>
              </form>
            )}
            {importOpen && (
              <form
                className="dshDesktopSettingsPanel"
                onSubmit={event => { event.preventDefault(); applyImport() }}
              >
                <label className="dshDesktopSettingsField">
                  {t('import')}
                  <textarea
                    className="dshDesktopSettingsInput"
                    value={importText}
                    rows={6}
                    autoComplete="off"
                    spellCheck={false}
                    disabled={busy}
                    onChange={event => { setImportText(event.currentTarget.value) }}
                  />
                </label>
                <p className="dshDesktopSettingsHint">{t('importHint')}</p>
                {importStatus === 'invalid' && <p className="dshDesktopSettingsError" role="alert">{t('importInvalid')}</p>}
                {importStatus === 'duplicate' && <p className="dshDesktopSettingsNotice" role="status">{t('importDuplicate')}</p>}
                <div className="dshDesktopSettingsDeleteActions">
                  <button
                    type="button"
                    className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
                    disabled={busy}
                    onClick={() => { setImportOpen(false); setImportStatus(undefined); setImportText('') }}
                  >
                    {t('cancel')}
                  </button>
                  <button
                    type="submit"
                    className="dshDesktopSettingsPrimary"
                    disabled={busy || importText.trim().length === 0}
                  >
                    {t('importApply')}
                  </button>
                </div>
              </form>
            )}
          </>
        )}
      </section>
    </div>
  )
}
