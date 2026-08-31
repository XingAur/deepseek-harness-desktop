import { useCallback, useEffect, useMemo, useState } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { messageOf } from '../model-agent/state'
import {
  fetchList, fetchStatus, importTargets, TARGET_LABELS,
  type PresetSummary, type PromptTarget, type TargetStatus,
} from './prompts-api'

export function PromptsPanel({ bridge }: { bridge: DesktopBridgeLike }) {
  const [statuses, setStatuses] = useState<TargetStatus[]>([])
  const [presets, setPresets] = useState<PresetSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [importOpen, setImportOpen] = useState(false)

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
            <button type="button" onClick={() => { /* Task 15: 打开编辑器 */ }}>
              <span>{preset.title}</span>
              <span className="dshPromptsMuted">
                {preset.activatedTargets.map((target) => TARGET_LABELS[target]).join(' / ') || '未激活'} · {new Date(preset.updatedAt).toLocaleString()}
              </span>
            </button>
          </li>
        ))}
        {presets.length === 0 && <li className="dshPromptsMuted">还没有预设,点「从文件导入」开始。</li>}
      </ul>
      {importOpen && (
        <PromptsImportDialog
          candidates={importCandidates}
          busy={false}
          onClose={() => setImportOpen(false)}
          onImport={async (targets) => {
            try {
              await importTargets(bridge, targets)
              setImportOpen(false)
              await refreshAll()
            } catch (cause: unknown) { setError(messageOf(cause)) }
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
