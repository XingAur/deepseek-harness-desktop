import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { messageOf } from './state'

interface IntakeSnapshot {
  ticketId?: string
  packageDir?: string
  packageStatus?: string
  pendingCount?: number
  generationStatus?: string
  generationErrorCode?: string
  generatedCount?: number
  skippedCount?: number
  modelGeneratedCount?: number
  openQuestions?: string[]
  databaseProbeStatus?: string
  databaseProbeError?: string
}

interface HarnessStatus {
  state: 'idle' | 'running' | 'completed' | 'blocked' | 'failed' | 'cancelled' | string
  pid?: number
  requestId?: string
  errorCode?: string
  intake?: IntakeSnapshot
  blockers?: string[]
}

interface HarnessTaskForm {
  yunxiaoSource: string
  taskContractPath: string
  understandingPath: string
  worktreeRoot: string
  knowledgeHome: string
  authorizationId: string
  agentBackend: string
  archiveRoot: string
  selectedModelId: string
  yunxiaoProfileId: string
  gitlabProfileId: string
  databaseProfileId: string
}

const initialForm: HarnessTaskForm = {
  yunxiaoSource: '',
  taskContractPath: '',
  understandingPath: '',
  worktreeRoot: '',
  knowledgeHome: '',
  authorizationId: '',
  agentBackend: 'host-bridge',
  archiveRoot: '',
  selectedModelId: 'codex',
  yunxiaoProfileId: '',
  gitlabProfileId: '',
  databaseProfileId: '',
}

interface ConnectionProfile { profileId: string; displayName: string; kind: 'mcp' | 'database'; providerId: 'yunxiao' | 'gitlab' | 'generic' }

export function HarnessTaskPanel({ bridge }: { bridge: DesktopBridgeLike }) {
  const [form, setForm] = useState<HarnessTaskForm>(initialForm)
  const [status, setStatus] = useState<HarnessStatus>({ state: 'idle' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [profiles, setProfiles] = useState<ConnectionProfile[]>([])
  const [intakeBusy, setIntakeBusy] = useState(false)
  const [intakeResult, setIntakeResult] = useState<string | null>(null)
  const [answers, setAnswers] = useState('')
  const [answersBusy, setAnswersBusy] = useState(false)
  const [answersResult, setAnswersResult] = useState<string | null>(null)
  const appliedIntakeKey = useRef('')

  const refresh = useCallback(async () => {
    try {
      setStatus(await bridge.requestV2<HarnessStatus>('harness.status'))
    } catch (cause) {
      setError(messageOf(cause))
    }
  }, [bridge])

  useEffect(() => { void refresh() }, [refresh])

  useEffect(() => {
    void Promise.all([
      bridge.requestV2<ConnectionProfile[]>('harness.connection.list', undefined, { kind: 'mcp' }),
      bridge.requestV2<ConnectionProfile[]>('harness.connection.list', undefined, { kind: 'database' }),
    ]).then(([mcp, database]) => setProfiles([...mcp, ...database])).catch((cause) => setError(messageOf(cause)))
  }, [bridge])

  const requiredLegacyKeys: Array<keyof HarnessTaskForm> = ['taskContractPath', 'understandingPath', 'worktreeRoot', 'knowledgeHome', 'authorizationId']
  const requiredKeys = form.archiveRoot.trim() === ''
    ? requiredLegacyKeys
    : requiredLegacyKeys.filter((key) => key !== 'taskContractPath' && key !== 'understandingPath')
  const missing = useMemo(() => requiredKeys
    .filter((key) => form[key].trim() === '')
    .map((key) => key), [form])
  const running = status.state === 'running'

  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => { void refresh() }, 1500)
    return () => window.clearInterval(timer)
  }, [refresh, running])

  // 归档任务完成后把任务包目录和模型起草结果带回界面；每个结果只应用一次。
  useEffect(() => {
    const intake = status.intake
    if (intake === undefined || status.state === 'running' || status.state === 'idle') return
    const key = `${intake.ticketId ?? ''}|${intake.packageDir ?? ''}|${intake.generationStatus ?? ''}|${intake.generatedCount ?? 0}`
    if (key === appliedIntakeKey.current) return
    appliedIntakeKey.current = key
    if (typeof intake.packageDir === 'string' && intake.packageDir.trim() !== '') {
      update('archiveRoot', intake.packageDir.trim())
    }
    setIntakeResult(intakeMessage(intake))
  }, [status])

  const start = async () => {
    if (missing.length > 0 || busy) return
    setBusy(true)
    setError(null)
    try {
      setStatus(await bridge.requestV2<HarnessStatus>('harness.start', undefined, {
        worktreeRoot: form.worktreeRoot.trim(),
        knowledgeHome: form.knowledgeHome.trim(),
        authorizationId: form.authorizationId.trim(),
        ...(form.taskContractPath.trim() === '' ? {} : { taskContractPath: form.taskContractPath.trim() }),
        ...(form.understandingPath.trim() === '' ? {} : { understandingPath: form.understandingPath.trim() }),
        ...(form.archiveRoot.trim() === '' ? {} : { archiveRoot: form.archiveRoot.trim() }),
        ...(form.selectedModelId.trim() === '' ? {} : { selectedModelId: form.selectedModelId.trim() }),
        ...(form.yunxiaoProfileId.trim() === '' ? {} : { yunxiaoProfileId: form.yunxiaoProfileId.trim() }),
        ...(form.gitlabProfileId.trim() === '' ? {} : { gitlabProfileId: form.gitlabProfileId.trim() }),
        ...(form.databaseProfileId.trim() === '' ? {} : { databaseProfileId: form.databaseProfileId.trim() }),
        ...(form.agentBackend.trim() === '' ? {} : { agentBackend: form.agentBackend.trim() }),
      }))
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      setStatus(await bridge.requestV2<HarnessStatus>('harness.cancel'))
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(false)
    }
  }

  const intake = async () => {
    const source = form.yunxiaoSource.trim()
    const archiveRoot = form.archiveRoot.trim()
    if (source === '' || archiveRoot === '' || intakeBusy || busy || running) return
    setIntakeBusy(true)
    setError(null)
    setIntakeResult(null)
    appliedIntakeKey.current = ''
    try {
      await bridge.requestV2('harness.intake', undefined, {
        source,
        archiveRoot,
        includeComments: true,
        ...(form.yunxiaoProfileId.trim() === '' ? {} : { yunxiaoProfileId: form.yunxiaoProfileId.trim() }),
        ...(form.selectedModelId.trim() === '' ? {} : { selectedModelId: form.selectedModelId.trim() }),
        ...(form.agentBackend.trim() === '' ? {} : { agentBackend: form.agentBackend.trim() }),
      })
      setIntakeResult('云效归档已启动：正在收集只读证据' + (form.selectedModelId.trim() === '' ? '。' : `，并使用 ${form.selectedModelId.trim()} 起草分析文档。`))
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setIntakeBusy(false)
    }
  }

  const pickArchiveRoot = async () => {
    if (intakeBusy || busy || running) return
    setBusy(true)
    setError(null)
    try {
      const picked = await bridge.requestV2<string | null>('harness.pick-archive-root')
      if (typeof picked === 'string' && picked.trim() !== '') update('archiveRoot', picked.trim())
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(false)
    }
  }

  // 用户对业务问题的答复写入任务包后，重新执行即可带着已确认口径过理解门禁。
  const submitAnswers = async () => {
    const archiveRoot = form.archiveRoot.trim()
    const text = answers.trim()
    if (archiveRoot === '' || text === '' || answersBusy) return
    setAnswersBusy(true)
    setError(null)
    setAnswersResult(null)
    try {
      await bridge.requestV2('harness.archive-answers', undefined, { archiveRoot, answers: text })
      setAnswers('')
      setAnswersResult('业务答复已写入任务包（analysis/business_answers.md），重新点击“按 Harness 决策执行”即可。')
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setAnswersBusy(false)
    }
  }

  const update = (key: keyof HarnessTaskForm, value: string) => setForm((current) => ({ ...current, [key]: value }))
  return (
    <div className="dshModelAgentGrid">
      <article className="dshModelAgentCard dshHarnessTaskPanel">
        <div className="dshModelAgentCardHeader">
          <div><div className="dshModelAgentEyebrow">HARNESS GOVERNED EXECUTION</div><h3>Harness 任务执行</h3></div>
          <span className={`dshModelAgentStatus dshModelAgentStatus-${status.state}`}>{statusLabel(status.state)}</span>
        </div>
        <p className="dshModelAgentCardText">Harness 负责证据归档、需求理解、规划、项目分析、执行、验证和审查；当前选定模型贯穿所有阶段，内部失败由 Harness 自动回灌重决策。</p>
        <div className="dshModelAgentWarning" role="note">新任务优先选择 Harness 归档根目录和 profile；旧版任务仍可填写下方已有契约文件路径。</div>
        <label className="dshModelAgentField">云效需求 URL 或工作项 ID<input aria-label="云效需求 URL 或工作项 ID" value={form.yunxiaoSource} onChange={(event) => update('yunxiaoSource', event.target.value)} placeholder="https://devops.aliyun.com/.../DFHIS-32178 或 DFHIS-32178" /></label>
        <div className="dshModelAgentFieldRow">
          <label className="dshModelAgentField">Harness 归档根目录（建议）<input aria-label="Harness 归档根目录" value={form.archiveRoot} onChange={(event) => update('archiveRoot', event.target.value)} placeholder="/path/to/harness-archives" /></label>
          <button type="button" className="dshModelAgentSecondary" disabled={intakeBusy || busy || running} onClick={() => void pickArchiveRoot()}>选择本机目录…</button>
        </div>
        <div className="dshModelAgentActions"><button type="button" disabled={intakeBusy || busy || running || form.yunxiaoSource.trim() === '' || form.archiveRoot.trim() === ''} onClick={() => void intake()}>只读归档并起草分析文档</button></div>
        {intakeResult !== null && <p className="dshModelAgentMuted" role="status">{intakeResult}</p>}
        <label className="dshModelAgentField">当前统一模型 ID<input aria-label="当前统一模型 ID" value={form.selectedModelId} onChange={(event) => update('selectedModelId', event.target.value)} placeholder="deepseek-reasoner / gpt-5.6-sol" /></label>
        <details className="dshModelAgentAdvanced"><summary>兼容旧版：已有任务包文件路径（可选）</summary><label className="dshModelAgentField">任务契约文件绝对路径<input aria-label="任务契约文件绝对路径" value={form.taskContractPath} onChange={(event) => update('taskContractPath', event.target.value)} placeholder="/path/to/harness/engineering/task_contract.json" /></label><label className="dshModelAgentField">需求理解文件绝对路径<input aria-label="需求理解文件绝对路径" value={form.understandingPath} onChange={(event) => update('understandingPath', event.target.value)} placeholder="/path/to/harness/analysis/requirement_understanding.json" /></label></details>
        <label className="dshModelAgentField">目标项目绝对路径<input aria-label="目标项目绝对路径" value={form.worktreeRoot} onChange={(event) => update('worktreeRoot', event.target.value)} placeholder="/path/to/project" /></label>
        <label className="dshModelAgentField">知识库目录绝对路径<input aria-label="知识库目录绝对路径" value={form.knowledgeHome} onChange={(event) => update('knowledgeHome', event.target.value)} placeholder="/path/to/knowledge" /></label>
        <label className="dshModelAgentField">执行授权编号<input aria-label="执行授权编号" value={form.authorizationId} onChange={(event) => update('authorizationId', event.target.value)} placeholder="DFHIS-32178-change-1" /></label>
        <label className="dshModelAgentField">Harness 执行后端（可选）<select aria-label="模型执行后端" value={form.agentBackend} onChange={(event) => update('agentBackend', event.target.value)}><option value="host-bridge">由 Host 绑定当前模型</option><option value="codex">Codex Host</option><option value="deepseek">DeepSeek Host</option><option value="openai-compatible">OpenAI 兼容 Host（任意兼容端点）</option></select></label>
        <div className="dshModelAgentFormGrid">
          <label className="dshModelAgentField">云效 profile（可选）<select aria-label="云效 profile" value={form.yunxiaoProfileId} onChange={(event) => update('yunxiaoProfileId', event.target.value)}><option value="">未选择</option>{profiles.filter((profile) => profile.kind === 'mcp' && profile.providerId === 'yunxiao').map((profile) => <option value={profile.profileId} key={profile.profileId}>{profile.displayName}</option>)}</select></label>
          <label className="dshModelAgentField">GitLab profile（可选）<select aria-label="GitLab profile" value={form.gitlabProfileId} onChange={(event) => update('gitlabProfileId', event.target.value)}><option value="">未选择</option>{profiles.filter((profile) => profile.kind === 'mcp' && profile.providerId === 'gitlab').map((profile) => <option value={profile.profileId} key={profile.profileId}>{profile.displayName}</option>)}</select></label>
          <label className="dshModelAgentField">数据库 profile（可选）<select aria-label="数据库 profile" value={form.databaseProfileId} onChange={(event) => update('databaseProfileId', event.target.value)}><option value="">未选择</option>{profiles.filter((profile) => profile.kind === 'database').map((profile) => <option value={profile.profileId} key={profile.profileId}>{profile.displayName}</option>)}</select></label>
        </div>
        {missing.length > 0 && <p className="dshModelAgentMuted" role="status">还缺少 {missing.length} 项启动参数；这些参数应由 Harness 需求归档/理解阶段生成。</p>}
        {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}
        <div className="dshModelAgentActions">
          <button type="button" disabled={busy || running || missing.length > 0} onClick={() => void start()}>按 Harness 决策执行</button>
          <button type="button" disabled={busy || !running} onClick={() => void cancel()}>取消 Harness 任务</button>
          <button type="button" disabled={busy} onClick={() => void refresh()}>刷新状态</button>
        </div>
        {status.errorCode !== undefined && <p className="dshModelAgentCardHint">错误码：{status.errorCode}</p>}
        {(status.blockers ?? []).length > 0 && (
          <div className="dshModelAgentWarning" role="alert">
            <strong>Harness 理解门禁待确认（{status.blockers?.length ?? 0} 项）：</strong>
            <ul>
              {(status.blockers ?? []).map((blocker, index) => <li key={index}>{blocker}</li>)}
            </ul>
            <p className="dshModelAgentCardHint">业务问题请在下方补充答复后重新执行；证据缺口类问题请先补充归档或项目材料。</p>
          </div>
        )}
        {(status.blockers ?? []).length > 0 && (
          <div className="dshModelAgentFieldRow">
            <label className="dshModelAgentField">业务答复（写入任务包，最高优先级口径）<textarea aria-label="业务答复" value={answers} onChange={(event) => setAnswers(event.target.value)} rows={3} placeholder="例：重打记录需要按操作员过滤，保留最近 3 个月。" /></label>
            <button type="button" className="dshModelAgentSecondary" disabled={answersBusy || form.archiveRoot.trim() === '' || answers.trim() === ''} onClick={() => void submitAnswers()}>提交业务答复</button>
          </div>
        )}
        {answersResult !== null && <p className="dshModelAgentMuted" role="status">{answersResult}</p>}
      </article>
    </div>
  )
}

function statusLabel(value: string): string {
  return ({ idle: '待启动', running: '执行中', completed: '已完成', blocked: '已阻断', failed: '失败', cancelled: '已取消' } as Record<string, string>)[value] ?? value
}

function intakeMessage(intake: IntakeSnapshot): string {
  const ticket = intake.ticketId ?? '需求'
  const parts: string[] = [`已归档 ${ticket}，任务包目录已就绪`]
  if (intake.generationStatus === 'generated') {
    parts.push(`当前模型已起草 ${intake.generatedCount ?? 0} 篇分析文档（标记 model_generated，未当作已确认事实）`)
  } else if (intake.generationStatus === 'failed' || intake.generationStatus === 'skipped_no_evidence') {
    parts.push(`模型起草未完成（${intake.generationErrorCode || intake.generationStatus}），分析文档保持待生成，可重试归档`)
  } else if (intake.generationStatus !== undefined) {
    parts.push(`分析文档起草状态：${intake.generationStatus}`)
  } else {
    parts.push('未选择当前模型，分析文档保持待生成')
  }
  const questions = (intake.openQuestions ?? []).filter((question) => question.trim() !== '')
  if (questions.length > 0) parts.push(`待你确认的业务问题：${questions.join('；')}`)
  if (intake.databaseProbeStatus === 'connected') parts.push('数据库只读探测已连通，证据已写入 engineering/database_probe.json')
  else if (intake.databaseProbeStatus === 'failed') parts.push(`数据库只读探测失败（${intake.databaseProbeError || '原因见证据文件'}）`)
  return parts.join('。') + '。'
}
