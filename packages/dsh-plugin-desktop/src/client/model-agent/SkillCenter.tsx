import { useMemo, useState, type ReactNode } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { ExtensionCenter } from '../extensions/ExtensionCenter'
import type { ExtensionReviewItem } from '../extensions/ExtensionReviewDialog'
import { PluginMarket } from '../extensions/PluginMarket'
import { messageOf } from './state'

export function SkillCenter(props: {
  bridge: DesktopBridgeLike
  items: ExtensionReviewItem[]
  onChange(item: ExtensionReviewItem): void
}) {
  const [query, setQuery] = useState('')
  const [creating, setCreating] = useState(false)
  const [showMarket, setShowMarket] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    if (needle === '') return props.items
    return props.items.filter((item) => `${item.displayName} ${item.extensionId} ${item.sourceKind}`.toLocaleLowerCase().includes(needle))
  }, [props.items, query])

  const importSkill = async () => {
    if (props.bridge.mode === 'preview') return
    setBusy(true)
    try {
      const imported = await props.bridge.requestV2<ExtensionReviewItem | null>('skill.import', undefined, {})
      if (imported !== null) props.onChange(imported)
      setError(null)
    } catch (cause) { setError(messageOf(cause)) } finally { setBusy(false) }
  }

  return <div className="dshAgentCapabilityStack">
    <div className="dshCapabilitySectionHeader dshSkillHeader">
      <div><strong>技能</strong><span>可检索已安装技能，也可以从当前 Profile 新建、导入或从插件市场安装。</span></div>
      <div className="dshSkillActions">
        <button type="button" onClick={() => setCreating(true)}>新增技能</button>
        <button type="button" disabled={busy || props.bridge.mode === 'preview'} onClick={() => void importSkill()}>{props.bridge.mode === 'preview' ? '正式桌面可导入' : '导入技能'}</button>
        <button type="button" onClick={() => setShowMarket((current) => !current)}>浏览技能市场</button>
      </div>
    </div>
    <input className="dshSkillSearch" type="search" aria-label="搜索技能" placeholder="搜索技能名称或 ID…" value={query} onChange={(event) => setQuery(event.target.value)} />
    {props.bridge.mode === 'preview' && <div className="dshPluginMarketPreview" role="note">本地预览可体验检索和新建表单；导入、保存和安装只在正式桌面的当前 Profile 中生效。</div>}
    {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}
    {query.trim() !== '' && filtered.length === 0
      ? <div className="dshModelAgentEmpty"><strong>没有匹配的技能</strong><span>换个名称或 ID 继续搜索。</span></div>
      : <ExtensionCenter bridge={props.bridge} extensions={filtered} emptyKind="技能" showMarket={false} onChange={props.onChange} />}
    {showMarket && <PluginMarket bridge={props.bridge} embedded initialCategory="skill" />}
    {creating && <CreateSkillDialog bridge={props.bridge} busy={busy} setBusy={setBusy} onClose={() => setCreating(false)} onCreated={(item) => { props.onChange(item); setCreating(false) }} onError={setError} />}
  </div>
}

function CreateSkillDialog(props: {
  bridge: DesktopBridgeLike
  busy: boolean
  setBusy(value: boolean): void
  onClose(): void
  onCreated(item: ExtensionReviewItem): void
  onError(message: string | null): void
}) {
  const [skillId, setSkillId] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [description, setDescription] = useState('')
  const [instructions, setInstructions] = useState('')
  const valid = /^[a-z0-9][a-z0-9._-]{1,63}$/.test(skillId) && displayName.trim() !== '' && instructions.trim() !== ''
  const save = async () => {
    if (!valid || props.bridge.mode === 'preview') return
    props.setBusy(true)
    try {
      const item = await props.bridge.requestV2<ExtensionReviewItem>('skill.create', undefined, { skillId, displayName: displayName.trim(), description: description.trim(), instructions: instructions.trim() })
      props.onError(null)
      props.onCreated(item)
    } catch (cause) { props.onError(messageOf(cause)) } finally { props.setBusy(false) }
  }
  return <div className="dshModelAgentDialogBackdrop" role="presentation">
    <section className="dshModelAgentDialog dshSkillEditor" role="dialog" aria-modal="true" aria-label="新增技能">
      <header><div><h3>新增技能</h3><p>创建到当前 Profile 的 Codex Skills 目录。</p></div><button type="button" aria-label="关闭新增技能" onClick={props.onClose}>×</button></header>
      <div className="dshSkillEditorFields">
        <Field label="技能 ID"><input aria-label="技能 ID" value={skillId} placeholder="code-review" onChange={(event) => setSkillId(event.target.value.toLocaleLowerCase())} /></Field>
        <Field label="技能名称"><input aria-label="技能名称" value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></Field>
        <Field label="技能描述"><input aria-label="技能描述" value={description} onChange={(event) => setDescription(event.target.value)} /></Field>
        <Field label="技能说明"><textarea aria-label="技能说明" rows={9} value={instructions} onChange={(event) => setInstructions(event.target.value)} /></Field>
      </div>
      <p className="dshModelAgentDialogHint">技能说明不会自动获得系统权限；实际工具调用仍经过 Harness 审批。其他执行器是否加载此技能取决于它的适配器能力。</p>
      <footer><button type="button" onClick={props.onClose}>取消</button><button type="button" className="dshModelAgentPrimary" disabled={!valid || props.busy || props.bridge.mode === 'preview'} onClick={() => void save()}>{props.bridge.mode === 'preview' ? '正式桌面可保存' : '保存技能'}</button></footer>
    </section>
  </div>
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="dshModelAgentField">{label}{children}</label>
}
