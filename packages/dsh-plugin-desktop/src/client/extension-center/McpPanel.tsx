import { useCallback, useEffect, useState } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { messageOf } from '../model-agent/state'
import {
  MCP_TARGETS, MCP_TARGET_LABELS, argsToText, deleteServer, envToText, fetchServers, fetchTargetStatus,
  importFromTarget, parseArgsText, parseEnvText, syncTarget, targetSummary, upsertServer,
  type McpServerDef, type McpTarget, type McpTargetStatus,
} from './mcp-api'

/** 新增/编辑对话框的表单草稿;id 为 null 表示新建。 */
export interface McpDraft {
  id: string | null
  name: string
  command: string
  argsText: string
  envText: string
  targets: McpTarget[]
}

export function emptyDraft(): McpDraft {
  return { id: null, name: '', command: '', argsText: '', envText: '', targets: [] }
}

export function draftFromServer(server: McpServerDef): McpDraft {
  return {
    id: server.id,
    name: server.name,
    command: server.command,
    argsText: argsToText(server.args),
    envText: envToText(server.env),
    targets: [...server.targets],
  }
}

export function McpPanel({ bridge }: { bridge: DesktopBridgeLike }) {
  const [servers, setServers] = useState<McpServerDef[]>([])
  const [statuses, setStatuses] = useState<McpTargetStatus[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState<McpDraft | null>(null)

  const load = useCallback(async () => {
    const [serverReply, statusReply] = await Promise.all([fetchServers(bridge), fetchTargetStatus(bridge)])
    setServers(serverReply)
    setStatuses(statusReply)
  }, [bridge])

  useEffect(() => {
    void load().then(() => setLoaded(true)).catch((cause: unknown) => { setError(messageOf(cause)); setLoaded(true) })
  }, [load])

  const refreshAll = useCallback(async () => {
    try { await load(); setError(null) } catch (cause: unknown) { setError(messageOf(cause)) }
  }, [load])

  const installedOf = (target: McpTarget) => statuses.find((status) => status.target === target)?.installed ?? false

  const removeServer = async (server: McpServerDef) => {
    try {
      await deleteServer(bridge, server.id)
      await refreshAll()
    } catch (cause: unknown) { setError(messageOf(cause)) }
  }

  /** 同步到目标:对该行勾选且已安装的目标逐个触发(同步按目标整批投影)。 */
  const syncRow = async (server: McpServerDef) => {
    setBusy(true)
    try {
      for (const target of server.targets.filter(installedOf)) await syncTarget(bridge, target)
      await refreshAll()
    } catch (cause: unknown) { setError(messageOf(cause)) } finally { setBusy(false) }
  }

  const importTarget = async (target: McpTarget) => {
    setBusy(true)
    try {
      await importFromTarget(bridge, target)
      await refreshAll()
    } catch (cause: unknown) { setError(messageOf(cause)) } finally { setBusy(false) }
  }

  const submitDraft = async (form: McpDraft) => {
    setBusy(true)
    try {
      await upsertServer(bridge, {
        ...(form.id ? { id: form.id } : {}),
        name: form.name,
        command: form.command,
        args: parseArgsText(form.argsText),
        env: parseEnvText(form.envText),
        targets: form.targets,
      })
      setDraft(null)
      await refreshAll()
    } catch (cause: unknown) { setError(messageOf(cause)) } finally { setBusy(false) }
  }

  return (
    <div className="dshMcp">
      <div className="dshMcpToolbar">
        <button type="button" onClick={() => setDraft(emptyDraft())}>添加服务器</button>
        <span className="dshMcpSpacer" />
        {MCP_TARGETS.map((target) => (
          <button
            key={target}
            type="button"
            disabled={!installedOf(target) || busy}
            title={installedOf(target) ? `从 ${MCP_TARGET_LABELS[target]} 配置读取既有 MCP 服务器` : `${MCP_TARGET_LABELS[target]} 未安装`}
            onClick={() => void importTarget(target)}
          >
            从 {MCP_TARGET_LABELS[target]} 导入
          </button>
        ))}
        <button type="button" onClick={() => void refreshAll()}>刷新</button>
      </div>
      <div className="dshMcpStatusRow" role="group" aria-label="目标状态">
        {statuses.map((status) => (
          <span
            key={status.target}
            className="dshMcpTargetChip"
            data-installed={status.installed ? 'true' : 'false'}
            title={status.installed ? `${MCP_TARGET_LABELS[status.target]} 已安装` : `未检测到 ${MCP_TARGET_LABELS[status.target]}(~/.${status.target === 'claude' ? 'claude' : 'codex'})`}
          >
            <span>{MCP_TARGET_LABELS[status.target]}</span>
            <span className="dshMcpTargetState">{status.installed ? '已安装' : '未安装'}</span>
          </span>
        ))}
      </div>
      {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}
      <ul className="dshMcpList" aria-label="MCP 服务器列表">
        {servers.map((server) => {
          const syncable = server.targets.some(installedOf)
          return (
            <li key={server.id} className="dshMcpListItem">
              <div className="dshMcpItemMain">
                <span>{server.name}</span>
                <span className="dshMcpMuted">{[server.command, ...server.args].join(' ')}</span>
              </div>
              <span className="dshMcpTargetBadges" aria-label="同步目标">
                {server.targets.map((target) => (
                  <span key={target} className="dshMcpTargetBadge">{MCP_TARGET_LABELS[target]}</span>
                ))}
              </span>
              <span className="dshMcpItemActions">
                <button
                  type="button"
                  disabled={!syncable || busy}
                  title={syncable ? `同步到 ${targetSummary(server.targets)}` : '目标未安装,无法同步'}
                  onClick={() => void syncRow(server)}
                >
                  同步到目标
                </button>
                <button type="button" disabled={busy} onClick={() => setDraft(draftFromServer(server))}>编辑</button>
                <button type="button" disabled={busy} onClick={() => void removeServer(server)}>删除</button>
              </span>
            </li>
          )
        })}
        {loaded && servers.length === 0 && <li className="dshMcpMuted">还没有服务器,点「添加服务器」或「从 Claude/Codex 导入」开始。</li>}
      </ul>
      {draft !== null && (
        <McpServerDialog
          draft={draft}
          busy={busy}
          installedOf={installedOf}
          onChange={setDraft}
          onClose={() => setDraft(null)}
          onSubmit={(form) => void submitDraft(form)}
        />
      )}
    </div>
  )
}

export function McpServerDialog(props: {
  draft: McpDraft
  busy: boolean
  installedOf(target: McpTarget): boolean
  onChange(draft: McpDraft): void
  onClose(): void
  onSubmit(draft: McpDraft): void
}) {
  const { draft, onChange } = props
  const toggleTarget = (target: McpTarget, checked: boolean) => {
    onChange({ ...draft, targets: checked ? [...draft.targets, target] : draft.targets.filter((entry) => entry !== target) })
  }
  const nameReady = draft.name.trim().length > 0
  const commandReady = draft.command.trim().length > 0
  const targetsReady = draft.targets.length > 0
  return (
    <div className="dshMcpDialogBackdrop" role="presentation">
      <div className="dshMcpDialog" role="dialog" aria-label={draft.id === null ? '添加服务器' : '编辑服务器'}>
        <h3>{draft.id === null ? '添加服务器' : '编辑服务器'}</h3>
        <label className="dshMcpField">
          <span>名称</span>
          <input aria-label="服务器名称" value={draft.name} onChange={(event) => onChange({ ...draft, name: event.target.value })} />
        </label>
        <label className="dshMcpField">
          <span>命令</span>
          <input aria-label="服务器命令" value={draft.command} onChange={(event) => onChange({ ...draft, command: event.target.value })} />
        </label>
        <label className="dshMcpField">
          <span>参数(按空格分词)</span>
          <textarea aria-label="服务器参数" value={draft.argsText} onChange={(event) => onChange({ ...draft, argsText: event.target.value })} />
        </label>
        <label className="dshMcpField">
          <span>环境变量(每行 KEY=VALUE)</span>
          <textarea aria-label="服务器环境变量" value={draft.envText} onChange={(event) => onChange({ ...draft, envText: event.target.value })} />
        </label>
        <fieldset className="dshMcpTargetGroup" role="group" aria-label="同步目标">
          <legend>同步目标</legend>
          {MCP_TARGETS.map((target) => (
            <label key={target}>
              <input
                type="checkbox"
                value={target}
                aria-label={MCP_TARGET_LABELS[target]}
                checked={draft.targets.includes(target)}
                onChange={(event) => toggleTarget(target, event.target.checked)}
              />
              <span>{MCP_TARGET_LABELS[target]}</span>
            </label>
          ))}
        </fieldset>
        <div className="dshMcpDialogActions">
          <button type="button" onClick={props.onClose}>取消</button>
          <button
            type="button"
            disabled={props.busy || !nameReady || !commandReady || !targetsReady}
            onClick={() => props.onSubmit(draft)}
          >
            保存
          </button>
        </div>
      </div>
    </div>
  )
}
