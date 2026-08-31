import { useCallback, useEffect, useMemo, useState } from 'react'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { messageOf } from '../model-agent/state'
import {
  activatePreset, deactivateTarget, deletePreset, fetchList, fetchPreset, fetchStatus,
  importTargets, MAX_PROMPT_CHARS, promptBytes, resolveConflict, savePreset, TARGET_LABELS,
  type ActivateOutcome, type PresetSummary, type PromptTarget, type SaveOutcome, type TargetStatus,
} from './prompts-api'
import { PromptsConflictDialog, type ConflictCandidateView } from './PromptsConflictDialog'

// 预览用 marked 渲染后必须过 DOMPurify,防止预设内容里的脚本注入工作台页面。
export function renderMarkdownPreview(content: string): string {
  return DOMPurify.sanitize(marked.parse(content, { async: false }))
}

export function PromptsPanel({ bridge }: { bridge: DesktopBridgeLike }) {
  const [statuses, setStatuses] = useState<TargetStatus[]>([])
  const [presets, setPresets] = useState<PresetSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [importBusy, setImportBusy] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState<{ presetId: string; title: string; content: string } | null>(null)
  const [conflict, setConflict] = useState<ConflictCandidateView[] | null>(null)

  const openPreset = async (presetId: string) => {
    try {
      const preset = await fetchPreset(bridge, presetId)
      setSelectedId(presetId)
      setDraft({ presetId: preset.id, title: preset.title, content: preset.content })
    } catch (cause: unknown) { setError(messageOf(cause)) }
  }

  const applyOutcome = async (outcome: SaveOutcome) => {
    if (outcome.kind === 'saved') {
      setDraft((current) => current === null ? current : { ...current, content: outcome.preset.content })
      setConflict(null)
      await refreshAll()
    } else {
      setConflict(outcome.candidates)
    }
  }

  const saveDraft = async () => {
    if (draft === null) return
    try {
      await applyOutcome(await savePreset(bridge, { presetId: draft.presetId, title: draft.title, content: draft.content }))
    } catch (cause: unknown) { setError(messageOf(cause)) }
  }

  const resolveDraftConflict = async (chosen: { content: string }) => {
    if (draft === null) return
    try {
      await applyOutcome(await resolveConflict(bridge, { presetId: draft.presetId, title: draft.title, content: chosen.content }))
    } catch (cause: unknown) { setError(messageOf(cause)) }
  }

  const activateCurrentTo = async (target: PromptTarget) => {
    if (draft === null) return
    try {
      const outcome: ActivateOutcome = await activatePreset(bridge, draft.presetId, target)
      if (outcome.kind === 'backfill-conflict') setConflict(outcome.candidates) // 防御:现语义不可达
      else await refreshAll()
    } catch (cause: unknown) { setError(messageOf(cause)) }
  }

  const deactivateActivatedTargets = async () => {
    if (draft === null) return
    try {
      const activated = statuses.filter((status) => status.activePresetId === draft.presetId)
      for (const status of activated) await deactivateTarget(bridge, status.target)
      await refreshAll()
    } catch (cause: unknown) { setError(messageOf(cause)) }
  }

  const removeDraft = async () => {
    if (draft === null) return
    try {
      await deletePreset(bridge, draft.presetId)
      setDraft(null)
      setSelectedId(null)
      await refreshAll()
    } catch (cause: unknown) { setError(messageOf(cause)) }
  }

  const load = useCallback(async () => {
    const [statusReply, listReply] = await Promise.all([fetchStatus(bridge), fetchList(bridge)])
    setStatuses(statusReply)
    setPresets(listReply)
  }, [bridge])

  useEffect(() => {
    void load().then(() => setLoaded(true)).catch((cause: unknown) => { setError(messageOf(cause)); setLoaded(true) })
  }, [load])

  // 首启导入:池为空且存在非空 live 文件(spec §5)
  useEffect(() => {
    if (!loaded) return
    const poolEmpty = presets.length === 0
    const hasLive = statuses.some((status) => status.liveFileExists && status.activePresetId === null && !status.oversized)
    if (poolEmpty && hasLive) setImportOpen(true)
  }, [loaded, presets.length, statuses])

  const refreshAll = useCallback(async () => {
    try { await load(); setError(null) } catch (cause: unknown) { setError(messageOf(cause)) }
  }, [load])

  const importCandidates = useMemo(
    () => statuses.filter((status) => status.installed && status.liveFileExists && !status.oversized),
    [statuses],
  )

  return (
    <div className="dshPrompts">
      <div className="dshPromptsStatusRow" role="group" aria-label="目标状态">
        {statuses.map((status) => (
          <button key={status.target} type="button" className="dshPromptsTargetChip" disabled={!status.installed}
            aria-label={TARGET_LABELS[status.target]}
            title={status.installed ? `${TARGET_LABELS[status.target]}:${status.activePresetId === null ? '未激活' : '已激活'}` : '未安装'}>
            <span>{TARGET_LABELS[status.target]}</span>
            <span className="dshPromptsTargetState">
              {status.installed ? (status.activePresetId !== null ? '已激活' : '未激活') : '未安装'}
            </span>
            {status.installed && status.activePresetId !== null && !status.matchesActivePreset && (
              <span className="dshPromptsDrift">⚠外部修改</span>
            )}
          </button>
        ))}
        <span className="dshPromptsSpacer" />
        <button type="button" onClick={() => setImportOpen(true)}>从文件导入</button>
        <button type="button" onClick={() => void refreshAll()}>刷新</button>
      </div>
      {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}
      <ul className="dshPromptsList" aria-label="预设列表">
        {presets.map((preset) => (
          <li key={preset.id}>
            <button
              type="button"
              onClick={() => void openPreset(preset.id)}
              className={preset.id === selectedId ? 'dshPromptsListItem is-active' : 'dshPromptsListItem'}
            >
              <span>{preset.title}</span>
              <span className="dshPromptsMuted">
                {preset.activatedTargets.map((target) => TARGET_LABELS[target]).join(' / ') || '未激活'} · {new Date(preset.updatedAt).toLocaleString()}
              </span>
            </button>
          </li>
        ))}
        {presets.length === 0 && <li className="dshPromptsMuted">还没有预设,点「从文件导入」开始。</li>}
      </ul>
      {draft !== null && (
        <div className="dshPromptsEditor">
          <div className="dshPromptsEditorActions">
            <button type="button" disabled={promptBytes(draft.content) > MAX_PROMPT_CHARS} onClick={() => void saveDraft()}>保存</button>
            <button type="button" onClick={() => void removeDraft()}>删除</button>
            <button type="button" onClick={() => setDraft(null)}>关闭</button>
            {promptBytes(draft.content) > MAX_PROMPT_CHARS && <span role="alert">超过 24 KiB 上限,无法保存</span>}
          </div>
          <div className="dshPromptsPanes">
            <textarea aria-label="预设内容" value={draft.content} onChange={(event) => setDraft({ ...draft, content: event.target.value })} />
            <div className="dshPromptsPreview" aria-label="实时预览">
              <span className="dshPromptsMuted">预览</span>
              <div dangerouslySetInnerHTML={{ __html: renderMarkdownPreview(draft.content) }} />
            </div>
          </div>
          <fieldset className="dshPromptsActivateGroup" role="group" aria-label="激活目标">
            <legend>激活目标</legend>
            {statuses.filter((status) => status.installed).map((status) => (
              <label key={status.target}>
                <input
                  type="checkbox"
                  value={status.target}
                  aria-label={TARGET_LABELS[status.target]}
                  checked={status.activePresetId === draft.presetId}
                  onChange={() => void activateCurrentTo(status.target)}
                />
                <span>{TARGET_LABELS[status.target]}</span>
              </label>
            ))}
          </fieldset>
          <div className="dshPromptsEditorActions">
            <button type="button" onClick={() => void deactivateActivatedTargets()}>停用已激活目标</button>
          </div>
        </div>
      )}
      {conflict !== null && draft !== null && (
        <PromptsConflictDialog
          presetTitle={draft.title}
          candidates={conflict}
          onClose={() => setConflict(null)}
          onResolve={(chosen) => void resolveDraftConflict(chosen)}
        />
      )}
      {importOpen && (
        <PromptsImportDialog
          candidates={importCandidates}
          busy={importBusy}
          onClose={() => setImportOpen(false)}
          onImport={async (targets) => {
            setImportBusy(true)
            try {
              await importTargets(bridge, targets)
              setImportOpen(false)
              await refreshAll()
            } catch (cause: unknown) { setError(messageOf(cause)) } finally { setImportBusy(false) }
          }}
        />
      )}
    </div>
  )
}

export function PromptsImportDialog(props: {
  candidates: TargetStatus[]
  busy: boolean
  onClose(): void
  onImport(targets: PromptTarget[]): Promise<void> | void
}) {
  // 默认不勾选,由用户显式选择要导入的目标(jsdom/真实浏览器中点击已勾选框会先取消勾选)。
  const [selected, setSelected] = useState<PromptTarget[]>([])
  return (
    <div className="dshPromptsDialogBackdrop" role="presentation">
      <div className="dshPromptsDialog" role="dialog" aria-label="导入现有提示词">
        <h3>导入现有提示词</h3>
        <p>把各目标当前的全局提示词文件导入为预设,并保持激活状态。</p>
        {props.candidates.map((candidate) => (
          <label key={candidate.target} className="dshPromptsImportRow">
            <input
              type="checkbox"
              aria-label={TARGET_LABELS[candidate.target]}
              checked={selected.includes(candidate.target)}
              onChange={(event) => {
                setSelected((current) => event.target.checked
                  ? [...current, candidate.target]
                  : current.filter((target) => target !== candidate.target))
              }}
            />
            <span>{TARGET_LABELS[candidate.target]}</span>
            <span className="dshPromptsMuted">{candidate.activePresetId === null ? '未激活' : '已激活'}</span>
          </label>
        ))}
        <div className="dshPromptsDialogActions">
          <button type="button" onClick={props.onClose}>取消</button>
          <button type="button" disabled={props.busy || selected.length === 0} onClick={() => void props.onImport(selected)}>导入</button>
        </div>
      </div>
    </div>
  )
}

export function targetSummary(targets: PromptTarget[]): string {
  return targets.map((target) => TARGET_LABELS[target]).join(' / ')
}
