import { useCallback, useEffect, useState } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { messageOf } from '../model-agent/state'
import {
  SKILL_TARGETS, SKILL_TARGET_LABELS, fetchTargetSkills, installSkillsZip, otherTarget, shaShort,
  syncSkill, uninstallSkill,
  type InstalledSkill, type SkillTarget, type TargetSkills,
} from './skills-api'

const EMPTY_GROUPS: Record<SkillTarget, TargetSkills> = {
  claude: { installed: false, skills: [] },
  codex: { installed: false, skills: [] },
}

export function SkillsPanel({ bridge }: { bridge: DesktopBridgeLike }) {
  const [groups, setGroups] = useState<Record<SkillTarget, TargetSkills>>(EMPTY_GROUPS)
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [installing, setInstalling] = useState(false)

  const load = useCallback(async () => {
    const [claude, codex] = await Promise.all([
      fetchTargetSkills(bridge, 'claude'),
      fetchTargetSkills(bridge, 'codex'),
    ])
    setGroups({ claude, codex })
  }, [bridge])

  useEffect(() => {
    void load().then(() => setLoaded(true)).catch((cause: unknown) => { setError(messageOf(cause)); setLoaded(true) })
  }, [load])

  const refreshAll = useCallback(async () => {
    try { await load(); setError(null) } catch (cause: unknown) { setError(messageOf(cause)) }
  }, [load])

  const runAction = useCallback(async (action: () => Promise<unknown>): Promise<boolean> => {
    setBusy(true)
    try {
      await action()
      await refreshAll()
      return true
    } catch (cause: unknown) { setError(messageOf(cause)); return false } finally { setBusy(false) }
  }, [refreshAll])

  const submitInstall = useCallback((zipPath: string, targets: SkillTarget[]) => {
    void runAction(() => installSkillsZip(bridge, zipPath, targets)).then((succeeded) => {
      if (succeeded) setInstalling(false)
    })
  }, [bridge, runAction])

  return (
    <div className="dshSkills">
      <div className="dshSkillsToolbar">
        <button type="button" disabled={busy} onClick={() => setInstalling(true)}>从 ZIP 安装</button>
        <span className="dshSkillsSpacer" />
        <button type="button" disabled={busy} onClick={() => void refreshAll()}>刷新</button>
      </div>
      {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}
      {SKILL_TARGETS.map((target) => {
        const state = groups[target]
        const label = SKILL_TARGET_LABELS[target]
        const other = otherTarget(target)
        const syncHint = state.installed
          ? `同步到 ${SKILL_TARGET_LABELS[other]}`
          : `未检测到 ${label}(~/${target}/skills),无法读写该目标`
        return (
          <section key={target} className="dshSkillsGroup" aria-label={`${label} Skills`}>
            <header className="dshSkillsGroupHeader">
              <span>{label}</span>
              <span className="dshSkillsTargetState" data-installed={state.installed ? 'true' : 'false'}>
                {state.installed ? '已安装' : '未安装'}
              </span>
              <span className="dshSkillsTargetPath" title={syncHint}>~/{target}/skills</span>
            </header>
            {state.installed ? (
              <ul className="dshSkillsList" aria-label={`${label} Skills 列表`}>
                {state.skills.map((skill) => (
                  <SkillRow
                    key={skill.name}
                    skill={skill}
                    target={target}
                    busy={busy}
                    syncable={groups[other].installed}
                    syncLabel={`同步到 ${SKILL_TARGET_LABELS[other]}`}
                    onUninstall={() => void runAction(() => uninstallSkill(bridge, target, skill.name))}
                    onSync={() => void runAction(() => syncSkill(bridge, target, other, skill.name))}
                  />
                ))}
                {loaded && state.skills.length === 0 && (
                  <li className="dshSkillsMuted">还没有 skill,点「从 ZIP 安装」开始。</li>
                )}
              </ul>
            ) : (
              <p className="dshSkillsMuted">未检测到 {label}(~/{target}/skills):目标未安装,安装与同步已禁用。</p>
            )}
          </section>
        )
      })}
      {installing && (
        <SkillsInstallDialog
          installedOf={(target) => groups[target].installed}
          busy={busy}
          onClose={() => setInstalling(false)}
          onSubmit={submitInstall}
        />
      )}
    </div>
  )
}

function SkillRow(props: {
  skill: InstalledSkill
  target: SkillTarget
  busy: boolean
  syncable: boolean
  syncLabel: string
  onUninstall(): void
  onSync(): void
}) {
  const { skill } = props
  return (
    <li className="dshSkillsListItem" data-skill-name={skill.name}>
      <div className="dshSkillsItemMain">
        <span>{skill.name}</span>
        <span className="dshSkillsMuted" title={`SKILL.md SHA-256:${skill.skillMdSha256}`}>{shaShort(skill)}</span>
      </div>
      <span className="dshSkillsItemActions">
        <button
          type="button"
          disabled={props.busy || !props.syncable}
          title={props.syncable ? props.syncLabel : `${SKILL_TARGET_LABELS[otherTarget(props.target)]} 未安装,无法同步`}
          onClick={props.onSync}
        >
          {props.syncLabel}
        </button>
        <button type="button" disabled={props.busy} title={`从 ${SKILL_TARGET_LABELS[props.target]} 卸载 ${skill.name}`} onClick={props.onUninstall}>
          卸载
        </button>
      </span>
    </li>
  )
}

export function SkillsInstallDialog(props: {
  installedOf(target: SkillTarget): boolean
  busy: boolean
  onClose(): void
  onSubmit(zipPath: string, targets: SkillTarget[]): void
}) {
  const [zipPath, setZipPath] = useState('')
  const [targets, setTargets] = useState<SkillTarget[]>([])
  const toggleTarget = (target: SkillTarget, checked: boolean) => {
    setTargets(checked ? [...targets, target] : targets.filter((entry) => entry !== target))
  }
  const ready = zipPath.trim().length > 0 && targets.length > 0
  return (
    <div className="dshSkillsDialogBackdrop" role="presentation">
      <div className="dshSkillsDialog" role="dialog" aria-label="从 ZIP 安装 Skill">
        <h3>从 ZIP 安装 Skill</h3>
        <label className="dshSkillsField">
          <span>ZIP 文件路径(本机绝对路径)</span>
          <input
            aria-label="ZIP 文件路径"
            placeholder="C:\\Users\\me\\Downloads\\pdf-tools.zip"
            value={zipPath}
            onChange={(event) => setZipPath(event.target.value)}
          />
        </label>
        <fieldset className="dshSkillsTargetGroup" role="group" aria-label="安装目标">
          <legend>安装目标</legend>
          {SKILL_TARGETS.map((target) => {
            const installed = props.installedOf(target)
            return (
              <label key={target}>
                <input
                  type="checkbox"
                  value={target}
                  aria-label={SKILL_TARGET_LABELS[target]}
                  disabled={!installed}
                  checked={targets.includes(target)}
                  onChange={(event) => toggleTarget(target, event.target.checked)}
                />
                <span>{SKILL_TARGET_LABELS[target]}</span>
              </label>
            )
          })}
        </fieldset>
        <p className="dshSkillsMuted">未安装的目标已禁用;覆盖安装同名 skill 前会先备份到目标目录的 .trash- 文件夹。</p>
        <div className="dshSkillsDialogActions">
          <button type="button" onClick={props.onClose}>取消</button>
          <button type="button" disabled={props.busy || !ready} onClick={() => props.onSubmit(zipPath.trim(), targets)}>
            安装
          </button>
        </div>
      </div>
    </div>
  )
}
