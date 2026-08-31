import { useEffect, useState, type ReactNode } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { messageOf } from '../model-agent/state'

type CapabilityId = 'yunxiao' | 'gitlab' | 'database'
type ConnectionProfile = {
  profileId: string
  kind: 'mcp' | 'database'
  providerId: CapabilityId | 'generic'
  displayName: string
  readOnly: boolean
  enabled: boolean
}

type HarnessRunStatus = {
  state: string
  errorCode?: string
  intake?: {
    packageDir?: string
    ticketId?: string
  }
  blockers?: string[]
}

const capabilities: Array<{ id: CapabilityId; label: string; description: string; kind: 'mcp' | 'database' }> = [
  { id: 'yunxiao', label: '云效需求', description: '读取需求正文、评论、图片和附件，形成 Harness 原始证据。', kind: 'mcp' },
  { id: 'gitlab', label: 'GitLab 代码', description: '读取目标仓库、分支和代码证据，供项目理解与修改验证使用。', kind: 'mcp' },
  { id: 'database', label: '数据库维护', description: '使用已维护的数据库连接；默认只读，具体写操作仍需审批。', kind: 'database' },
]

export interface HarnessChatSurfaceProps {
  bridge: DesktopBridgeLike
  workspaceId?: string
  renderConversation: () => ReactNode
}

export function HarnessChatSurface({ bridge, workspaceId, renderConversation }: HarnessChatSurfaceProps) {
  const [open, setOpen] = useState(false)
  const [prompt, setPrompt] = useState('')
  const [archiveRoot, setArchiveRoot] = useState<string | undefined>()
  const [yunxiaoSource, setYunxiaoSource] = useState('')
  const [evidencePaths, setEvidencePaths] = useState<string[]>([])
  const [profiles, setProfiles] = useState<ConnectionProfile[]>([])
  const [selectedProfiles, setSelectedProfiles] = useState<Partial<Record<CapabilityId, string>>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [runStatus, setRunStatus] = useState<HarnessRunStatus | null>(null)
  const [answers, setAnswers] = useState('')
  const [answersBusy, setAnswersBusy] = useState(false)
  const [pendingResumeRoot, setPendingResumeRoot] = useState<string | undefined>()

  useEffect(() => {
    if (!open) return
    let cancelled = false
    void Promise.all([
      bridge.requestV2<ConnectionProfile[]>('harness.connection.list', undefined, { kind: 'mcp' }),
      bridge.requestV2<ConnectionProfile[]>('harness.connection.list', undefined, { kind: 'database' }),
    ]).then(([mcp, database]) => {
      if (!cancelled) {
        const unique = new Map([...mcp, ...database].filter((profile) => profile.enabled).map((profile) => [profile.profileId, profile]))
        setProfiles([...unique.values()])
      }
    }).catch((cause) => {
      if (!cancelled) setError(messageOf(cause))
    })
    return () => { cancelled = true }
  }, [bridge, open])

  const refreshRunStatus = async () => {
    try {
      setRunStatus(await bridge.requestV2<HarnessRunStatus>('harness.status'))
    } catch (cause) {
      setError(messageOf(cause))
    }
  }

  useEffect(() => {
    if (!open || runStatus?.state !== 'running') return
    const timer = window.setInterval(() => { void refreshRunStatus() }, 1500)
    return () => window.clearInterval(timer)
  }, [open, runStatus?.state])

  const chooseArchiveRoot = async () => {
    try {
      const selected = await bridge.requestV2<string | null>('harness.pick-archive-root')
      if (selected !== null && selected !== '') setArchiveRoot(selected)
    } catch (cause) {
      setError(messageOf(cause))
    }
  }

  const chooseEvidenceFiles = async () => {
    try {
      const selected = await bridge.requestV2<string[]>('harness.pick-evidence-files')
      setEvidencePaths(selected)
      setError(null)
    } catch (cause) {
      setError(messageOf(cause))
    }
  }

  const startPayload = (value: string, selectedArchiveRoot = archiveRoot) => ({
    prompt: value,
    ...(workspaceId === undefined ? {} : { workspaceId }),
    ...(selectedArchiveRoot === undefined ? {} : { archiveRoot: selectedArchiveRoot }),
    ...(yunxiaoSource.trim() === '' ? {} : { yunxiaoSource: yunxiaoSource.trim() }),
    ...(evidencePaths.length === 0 ? {} : { evidencePaths }),
    ...(selectedProfiles.yunxiao === undefined ? {} : { yunxiaoProfileId: selectedProfiles.yunxiao }),
    ...(selectedProfiles.gitlab === undefined ? {} : { gitlabProfileId: selectedProfiles.gitlab }),
    ...(selectedProfiles.database === undefined ? {} : { databaseProfileId: selectedProfiles.database }),
  })

  const start = async () => {
    const value = prompt.trim()
    if (value === '' || busy) return
    setBusy(true)
    setError(null)
    setStatus(null)
    try {
      const result = await bridge.requestV2<HarnessRunStatus>('harness.chat.start', undefined, startPayload(value))
      setRunStatus(result)
      setPendingResumeRoot(undefined)
      setStatus('Harness 已从当前聊天接收任务，正在按当前模型执行。')
      void refreshRunStatus()
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(false)
    }
  }

  const resumeArchivedDecision = async (archivePath: string) => {
    const taskPrompt = prompt.trim()
    try {
      const result = await bridge.requestV2<HarnessRunStatus>('harness.chat.start', undefined, startPayload(taskPrompt, archivePath))
      setRunStatus(result)
      setPendingResumeRoot(undefined)
      setStatus('业务确认已保存，Harness 正在按最新口径重新决策。')
      void refreshRunStatus()
    } catch (cause) {
      setError(`业务确认已安全保存，但 Harness 重新决策启动失败：${messageOf(cause)}`)
    }
  }

  const submitAnswers = async () => {
    const archivePath = runStatus?.intake?.packageDir ?? archiveRoot
    const value = answers.trim()
    const taskPrompt = prompt.trim()
    if (archivePath === undefined || value === '' || taskPrompt === '' || answersBusy) return
    setAnswersBusy(true)
    setError(null)
    try {
      await bridge.requestV2('harness.archive-answers', undefined, { archiveRoot: archivePath, answers: value })
      setAnswers('')
      setPendingResumeRoot(archivePath)
      setStatus('业务确认已安全保存，正在重新决策。')
      await resumeArchivedDecision(archivePath)
    } catch (cause) {
      setError(`业务确认保存失败：${messageOf(cause)}`)
    } finally {
      setAnswersBusy(false)
    }
  }

  const retryDecisionResume = async () => {
    if (pendingResumeRoot === undefined || answersBusy || prompt.trim() === '') return
    setAnswersBusy(true)
    setError(null)
    setStatus('业务确认已安全保存，正在重新决策。')
    try {
      await resumeArchivedDecision(pendingResumeRoot)
    } finally {
      setAnswersBusy(false)
    }
  }

  const profileFor = (capability: CapabilityId) => profiles.filter((profile) => {
    if (capability === 'database') return profile.kind === 'database'
    return profile.kind === 'mcp' && profile.providerId === capability
  })

  return (
    <div className="dshHarnessConversationSurface">
      <div className="dshHarnessConversationToolbar">
        <div>
          <strong>当前对话</strong>
          <span>普通聊天和 Harness 任务都从这里开始</span>
        </div>
        <button type="button" onClick={() => { setOpen((current) => !current); setError(null) }}>
          {open ? '返回普通聊天' : '开始 Harness 任务'}
        </button>
      </div>
      {open && <section className="dshHarnessChatComposer" aria-label="Harness 任务">
        <header>
          <div><p className="dshModelAgentEyebrow">GOVERNED TASK</p><h2>告诉 Harness 你要完成什么</h2><p>使用当前对话模型完成需求理解、规划、代码修改和验证；Harness 会自动整理能读取到的证据。</p></div>
          <span className="dshHarnessCurrentModel">使用当前对话模型</span>
        </header>
        <label className="dshHarnessChatPrompt">任务描述<textarea aria-label="任务描述" value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="例如：修复住院结算页面的金额显示问题，并补充对应测试。" /></label>
        <div className="dshHarnessChatOptions dshHarnessSourceOptions"><label>关联云效需求（可选）<input aria-label="关联云效需求" value={yunxiaoSource} onChange={(event) => setYunxiaoSource(event.target.value)} placeholder="工作项 ID 或 https://..." /></label><div><button type="button" onClick={() => void chooseEvidenceFiles()}>添加需求图片 / 文档 / 附件</button><span>{evidencePaths.length === 0 ? '未添加本地材料' : `已添加 ${evidencePaths.length} 个文件`}</span></div></div>
        <div className="dshHarnessCapabilities"><div><h3>本次任务可用能力</h3><p>选择已经维护好的业务连接；不需要填写 MCP 地址或凭证。</p></div><div className="dshHarnessCapabilityGrid">{capabilities.map((capability) => {
          const available = profileFor(capability.id)
          return <article className="dshHarnessCapability" key={capability.id}><div><strong>{capability.label}</strong><p>{capability.description}</p></div><label>{available.length === 0 ? <span className="dshHarnessCapabilityEmpty">尚未维护</span> : <select aria-label={`${capability.label} profile`} value={selectedProfiles[capability.id] ?? ''} onChange={(event) => setSelectedProfiles((current) => ({ ...current, [capability.id]: event.target.value || undefined }))}><option value="">自动选择</option>{available.map((profile) => <option key={profile.profileId} value={profile.profileId}>{profile.displayName}</option>)}</select>}</label></article>
        })}</div></div>
        <div className="dshHarnessChatOptions"><span>{archiveRoot === undefined ? '归档目录：自动创建任务目录' : `归档目录：${archiveRoot}`}</span><button type="button" onClick={() => void chooseArchiveRoot()}>选择归档位置（可选）</button></div>
        {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}
        {status !== null && <div className="dshModelAgentSuccess" role="status">{status}</div>}
        {pendingResumeRoot !== undefined && <div className="dshHarnessRunStatus"><strong>业务确认已经安全写入任务包</strong><span>仅重新启动决策，不会重复保存答复。</span><button type="button" disabled={answersBusy} onClick={() => void retryDecisionResume()}>{answersBusy ? '正在重新启动…' : '重新启动决策'}</button></div>}
        {runStatus !== null && <div className="dshHarnessRunStatus" role="status"><strong>Harness 状态：{runStatusLabel(runStatus.state)}</strong>{runStatus.errorCode !== undefined && <span>错误码：{runStatus.errorCode}</span>}{runStatus.intake?.packageDir !== undefined && <span>任务包：{runStatus.intake.packageDir}</span>}</div>}
        {(runStatus?.blockers ?? []).length > 0 && <div className="dshModelAgentWarning" role="alert"><strong>需要你确认的业务问题</strong><ul>{(runStatus?.blockers ?? []).map((blocker, index) => <li key={index}>{blocker}</li>)}</ul><p>只有 Harness 无法从现有证据推断、且会影响业务结果的问题才会出现在这里。</p><label className="dshHarnessAnswer">业务确认<textarea aria-label="业务确认" rows={3} value={answers} onChange={(event) => setAnswers(event.target.value)} placeholder="补充最终业务口径，Harness 会把它写入任务包并重新决策。" /></label><button type="button" disabled={answersBusy || answers.trim() === '' || (runStatus?.intake?.packageDir === undefined && archiveRoot === undefined)} onClick={() => void submitAnswers()}>提交业务确认</button></div>}
        <div className="dshHarnessChatActions"><button className="dshHarnessPrimary" type="button" disabled={busy || prompt.trim() === ''} onClick={() => void start()}>{busy ? '正在接收…' : '开始执行'}</button></div>
      </section>}
      {renderConversation()}
    </div>
  )
}

function runStatusLabel(value: string): string {
  return ({ idle: '待启动', running: '执行中', completed: '已完成', blocked: '已阻断', failed: '失败', cancelled: '已取消' } as Record<string, string>)[value] ?? value
}
