import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  Folder,
  LoaderCircle,
  Plus,
  RefreshCw,
  Send,
  Smartphone,
  Sparkles,
} from 'lucide-react'
import { Button } from '../components/ui/button.tsx'

/** Relative bases survive both loopback (/mobile/) and relay (/r/<pair>/mobile/) hosting. */
const API = (name: string) => new URL(`../api/desktop/mobile/${name}`, window.location.href).href
const TOKEN_EXCHANGE = (token: string) => new URL(`../?token=${encodeURIComponent(token)}`, window.location.href).href
const POLL_MS = 2_500

interface SessionRow {
  readonly id: string
  readonly title: string | null
  readonly running: boolean
  readonly updatedAt: number
  readonly cwd: string | null
}

interface WorkspaceGroup {
  readonly name: string
  readonly path: string
  readonly sessionIds: readonly string[]
}

interface PendingApproval {
  readonly sessionId: string | null
  readonly toolName: string
  readonly callId: string | null
  readonly reason: string | null
  readonly at: number
}

interface StateResponse {
  readonly sessions: readonly SessionRow[]
  readonly groups: readonly WorkspaceGroup[]
  readonly approvals: readonly PendingApproval[]
  readonly relay: { readonly active: boolean }
}

interface TranscriptLine {
  readonly role: 'user' | 'assistant'
  readonly text: string
  readonly time: number
}

type Phase = 'bootstrapping' | 'expired' | 'ready'

function locale(): 'en' | 'zh' {
  return navigator.language.toLowerCase().startsWith('zh') ? 'zh' : 'en'
}

const COPY = {
  en: Object.freeze({
    title: 'DSH remote control',
    connected: 'Connected to the current desktop window',
    connecting: 'Connecting to the desktop…',
    disconnected: 'Desktop tunnel is offline. Keep this page open.',
    stale: 'Live state is stale. Showing the last successful snapshot.',
    section: 'Workspaces and tasks on this device',
    stats: (workspaces: number, tasks: number) => `${String(workspaces)} workspaces · ${String(tasks)} tasks`,
    local: 'Local',
    tasks: (count: number) => `${String(count)} tasks`,
    updated: 'Updated',
    running: 'Running',
    idle: 'Idle',
    empty: 'No sessions yet. Tap + on a workspace to start one.',
    newSession: 'New session',
    sendFailed: 'Message was not sent. Try again.',
    promptPlaceholder: 'Ask DSH anything…',
    replyPlaceholder: 'Continue the conversation…',
    send: 'Send',
    expired: 'This link is no longer valid. Regenerate it from the desktop.',
    retry: 'Retry',
    justNow: 'just now',
    minutesAgo: (value: number) => `${String(value)} min ago`,
    hoursAgo: (value: number) => `${String(value)} h ago`,
    daysAgo: (value: number) => `${String(value)} d ago`,
    back: 'Back',
    approvalBanner: 'Awaiting desktop approval',
    approvalNote: 'Approvals are answered on the desktop client.',
    emptyThread: 'No messages yet. Send the first one below.',
    defaultWorkspace: 'Default',
    thinking: 'Thinking',
  }),
  zh: Object.freeze({
    title: 'DSH 远程控制',
    connected: '已连接到当前桌面窗口',
    connecting: '正在连接桌面…',
    disconnected: '桌面隧道离线，请保持此页打开。',
    stale: '实时状态暂时失败，显示上次成功的快照。',
    section: '当前设备上的工作区和任务',
    stats: (workspaces: number, tasks: number) => `${String(workspaces)} 个工作区 · ${String(tasks)} 个任务`,
    local: '本地',
    tasks: (count: number) => `${String(count)} 个任务`,
    updated: '更新于',
    running: '运行中',
    idle: '空闲',
    empty: '还没有会话。点工作区上的 + 开始。',
    newSession: '新会话',
    sendFailed: '发送失败，请再试一次。',
    promptPlaceholder: '向 DSH 问点什么…',
    replyPlaceholder: '继续对话…',
    send: '发送',
    expired: '链接已失效，请在桌面端重新生成。',
    retry: '重试',
    justNow: '刚刚',
    minutesAgo: (value: number) => `${String(value)} 分钟前`,
    hoursAgo: (value: number) => `${String(value)} 小时前`,
    daysAgo: (value: number) => `${String(value)} 天前`,
    back: '返回',
    approvalBanner: '等待桌面端批准',
    approvalNote: '批准操作需要在桌面客户端完成。',
    emptyThread: '还没有消息，在下方发送第一条。',
    defaultWorkspace: '默认工作区',
    thinking: '思考',
  }),
} as const

interface MobileCopy {
  readonly title: string
  readonly connected: string
  readonly connecting: string
  readonly disconnected: string
  readonly stale: string
  readonly section: string
  readonly stats: (workspaces: number, tasks: number) => string
  readonly local: string
  readonly tasks: (count: number) => string
  readonly updated: string
  readonly running: string
  readonly idle: string
  readonly empty: string
  readonly newSession: string
  readonly sendFailed: string
  readonly promptPlaceholder: string
  readonly replyPlaceholder: string
  readonly send: string
  readonly expired: string
  readonly retry: string
  readonly justNow: string
  readonly minutesAgo: (value: number) => string
  readonly hoursAgo: (value: number) => string
  readonly daysAgo: (value: number) => string
  readonly back: string
  readonly approvalBanner: string
  readonly approvalNote: string
  readonly emptyThread: string
  readonly defaultWorkspace: string
  readonly thinking: string
}

function copyFor(): MobileCopy {
  return locale() === 'zh' ? COPY.zh : COPY.en
}

function relativeTime(at: number, now: number, copy: MobileCopy): string {
  if (at <= 0) return copy.justNow
  const delta = Math.max(0, Math.floor((now - at) / 1000))
  if (delta < 60) return copy.justNow
  if (delta < 3_600) return copy.minutesAgo(Math.floor(delta / 60))
  if (delta < 86_400) return copy.hoursAgo(Math.floor(delta / 3_600))
  return copy.daysAgo(Math.floor(delta / 86_400))
}

async function apiCall(input: string, init?: RequestInit): Promise<Response> {
  return await fetch(input, {
    ...init,
    credentials: 'same-origin',
    redirect: 'error',
    headers: { accept: 'application/json', ...(init?.headers ?? {}) },
  })
}

function Composer({
  placeholder,
  disabled,
  onSend,
  autoFocus = false,
}: {
  readonly placeholder: string
  readonly disabled: boolean
  readonly onSend: (text: string) => void
  readonly autoFocus?: boolean
}): ReactNode {
  const field = useRef<HTMLTextAreaElement | null>(null)
  const [filled, setFilled] = useState(false)
  const send = (): void => {
    const value = field.current?.value.trim() ?? ''
    if (value === '' || disabled) return
    if (field.current !== null) field.current.value = ''
    setFilled(false)
    onSend(value)
  }
  return <div className="sticky bottom-0 border-t border-white/8 bg-[#111315] px-3 pb-[max(1rem,env(safe-area-inset-bottom))] pt-2.5">
    <div className="flex items-end gap-2 rounded-2xl border border-white/10 bg-white/5 px-3 py-2">
      <textarea
        autoCapitalize="sentences"
        autoComplete="off"
        autoCorrect="on"
        autoFocus={autoFocus}
        className="max-h-32 min-h-[44px] min-w-0 flex-1 resize-none bg-transparent py-2 text-[16px] leading-snug text-foreground outline-none placeholder:text-muted-foreground/70"
        enterKeyHint="send"
        onChange={event => { setFilled(event.target.value.trim().length > 0) }}
        onKeyDown={event => {
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault()
            send()
          }
        }}
        placeholder={placeholder}
        ref={field}
        rows={1}
      />
      <button aria-label="send" className={`mb-0.5 flex size-9 shrink-0 items-center justify-center rounded-full ${!filled || disabled ? 'bg-white/8 text-muted-foreground/50' : 'bg-sky-500 text-white'}`} disabled={disabled || !filled} onClick={send} type="button">
        {disabled ? <LoaderCircle aria-hidden className="size-4 animate-spin" /> : <Send aria-hidden className="size-4" />}
      </button>
    </div>
  </div>
}

function ThinkingRow({ copy }: { readonly copy: MobileCopy }): ReactNode {
  return <article aria-live="polite" className="flex max-w-[92%] items-center gap-2 rounded-2xl bg-white/6 px-3.5 py-2.5 text-[13px]">
    <Sparkles aria-hidden className="size-3.5 shrink-0 animate-pulse text-sky-300" />
    <span className="font-medium text-foreground/90">{copy.thinking}</span>
    <span aria-hidden className="inline-flex items-center gap-0.5 pt-0.5">
      <span className="size-1 animate-bounce rounded-full bg-muted-foreground [animation-delay:-0.3s]" />
      <span className="size-1 animate-bounce rounded-full bg-muted-foreground [animation-delay:-0.15s]" />
      <span className="size-1 animate-bounce rounded-full bg-muted-foreground" />
    </span>
  </article>
}

function StatusPill({ running, copy }: { readonly running: boolean; readonly copy: MobileCopy }): ReactNode {
  return <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${running ? 'bg-sky-500/15 text-sky-300' : 'bg-emerald-500/15 text-emerald-300'}`}>
    {running ? <LoaderCircle aria-hidden className="size-3 animate-spin" /> : null}
    {running ? copy.running : copy.idle}
  </span>
}

function TaskRow({
  session,
  now,
  copy,
  onOpen,
}: {
  readonly session: SessionRow
  readonly now: number
  readonly copy: MobileCopy
  readonly onOpen: () => void
}): ReactNode {
  return <button className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left hover:bg-white/5 active:bg-white/8" onClick={onOpen} type="button">
    <span className="min-w-0 flex-1">
      <span className="block truncate text-[15px] font-medium text-foreground">{session.title ?? session.id.slice(0, 8)}</span>
      <span className="mt-0.5 block text-xs text-muted-foreground">{relativeTime(session.updatedAt, now, copy)}</span>
    </span>
    <StatusPill copy={copy} running={session.running} />
  </button>
}

function WorkspaceCard({
  name,
  path,
  sessions,
  open,
  now,
  copy,
  onToggle,
  onOpenSession,
  onCreate,
}: {
  readonly name: string
  readonly path: string
  readonly sessions: readonly SessionRow[]
  readonly open: boolean
  readonly now: number
  readonly copy: MobileCopy
  readonly onToggle: () => void
  readonly onOpenSession: (id: string) => void
  readonly onCreate: () => void
}): ReactNode {
  const latest = sessions.reduce((max, session) => Math.max(max, session.updatedAt), 0)
  return <section className="overflow-hidden rounded-2xl border border-white/8 bg-white/4">
    <div className="flex items-start gap-3 px-3 py-3">
      <button className="flex min-w-0 flex-1 items-start gap-3 text-left" onClick={onToggle} type="button">
        <span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-xl bg-white/8 text-muted-foreground">
          <Folder aria-hidden className="size-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="truncate text-[15px] font-semibold">{name}</span>
            <span className="rounded-md border border-white/10 px-1.5 py-px text-[10px] font-medium text-muted-foreground">{copy.local}</span>
          </span>
          {path !== '' ? <span className="mt-0.5 block truncate font-mono text-[11px] text-muted-foreground">{path}</span> : null}
          <span className="mt-1 block text-[11px] text-muted-foreground">{copy.updated} {relativeTime(latest, now, copy)}</span>
        </span>
      </button>
      <div className="flex shrink-0 items-center gap-1 pt-1">
        <span className="mr-1 text-xs text-muted-foreground">{copy.tasks(sessions.length)}</span>
        <button aria-label={open ? 'collapse' : 'expand'} className="flex size-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-white/8" onClick={onToggle} type="button">
          {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
        </button>
        <button aria-label={copy.newSession} className="flex size-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-white/8" onClick={onCreate} type="button">
          <Plus className="size-4" />
        </button>
      </div>
    </div>
    {open
      ? <div className="border-t border-white/8 px-1 py-1">
        {sessions.map(session => <TaskRow copy={copy} key={session.id} now={now} onOpen={() => { onOpenSession(session.id) }} session={session} />)}
      </div>
      : null}
  </section>
}

export function MobileApp(): JSX.Element {
  const copy = copyFor()
  const [phase, setPhase] = useState<Phase>('bootstrapping')
  const [state, setState] = useState<StateResponse | null>(null)
  const [stale, setStale] = useState(false)
  const [busy, setBusy] = useState(false)
  const [openWorkspaces, setOpenWorkspaces] = useState<ReadonlySet<string>>(new Set())
  const [activeId, setActiveId] = useState<string | null>(null)
  const [composeCwd, setComposeCwd] = useState<string | null>(null)
  const [composing, setComposing] = useState(false)
  const [thread, setThread] = useState<readonly TranscriptLine[]>([])
  const [sendError, setSendError] = useState<string | null>(null)
  const [expectingReply, setExpectingReply] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const bootstrapped = useRef(false)
  const threadEnd = useRef<HTMLDivElement | null>(null)

  const poll = useCallback(async () => {
    if (document.visibilityState === 'hidden') return
    try {
      const response = await apiCall(API('state'))
      if (response.status === 401) {
        setPhase('expired')
        return
      }
      if (!response.ok) throw new Error(`HTTP ${String(response.status)}`)
      const next = await response.json() as StateResponse
      setState(next)
      setStale(false)
      setPhase('ready')
      setOpenWorkspaces(current => {
        if (current.size > 0) return current
        const first = next.groups[0]
        return first === undefined ? current : new Set([first.path || first.name])
      })
    } catch {
      setStale(true)
      setPhase(current => current === 'bootstrapping' ? 'ready' : current)
    }
  }, [])

  const loadThread = useCallback(async (sessionId: string) => {
    try {
      const response = await apiCall(`${API('transcript')}?sessionId=${encodeURIComponent(sessionId)}`)
      if (!response.ok) return
      const payload = await response.json() as { messages?: readonly TranscriptLine[] }
      setThread(payload.messages ?? [])
    } catch {
      // Keep the last thread; the next poll retries.
    }
  }, [])

  useEffect(() => {
    if (window.location.pathname.endsWith('/mobile')) {
      const normalized = new URL(window.location.href)
      normalized.pathname = `${window.location.pathname}/`
      window.history.replaceState(null, '', normalized)
    }
    const params = new URLSearchParams(window.location.search)
    const token = params.get('token')
    const bootstrap = async (): Promise<void> => {
      if (bootstrapped.current) return
      bootstrapped.current = true
      if (token !== null) {
        try {
          await apiCall(TOKEN_EXCHANGE(token))
        } catch {
          // The exchange fetch following its 303 may fail on redirects; the cookie may still be set.
        }
        const cleaned = new URL(window.location.href)
        cleaned.searchParams.delete('token')
        window.history.replaceState(null, '', cleaned)
      }
      await poll()
    }
    void bootstrap()
  }, [poll])

  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(Date.now())
      void poll()
      if (activeId !== null) void loadThread(activeId)
    }, POLL_MS)
    const onVisible = (): void => { if (document.visibilityState === 'visible') void poll() }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [activeId, loadThread, poll])

  useEffect(() => {
    threadEnd.current?.scrollIntoView({ block: 'end' })
  }, [thread, activeId, expectingReply])

  useEffect(() => {
    if (!expectingReply) return
    const lastUser = [...thread].reverse().find(line => line.role === 'user')
    const lastAssistant = [...thread].reverse().find(line => line.role === 'assistant')
    if (lastUser !== undefined && lastAssistant !== undefined && lastAssistant.time >= lastUser.time) {
      setExpectingReply(false)
    }
  }, [expectingReply, thread])

  const sessions = state?.sessions ?? []
  const byId = useMemo(() => new Map(sessions.map(session => [session.id, session])), [sessions])
  const groups = useMemo(() => {
    const server = state?.groups ?? []
    if (server.length === 0) {
      const fallback = new Map<string, SessionRow[]>()
      for (const session of [...sessions].sort((left, right) => right.updatedAt - left.updatedAt)) {
        const key = session.cwd ?? ''
        const bucket = fallback.get(key) ?? []
        bucket.push(session)
        fallback.set(key, bucket)
      }
      return [...fallback.entries()].map(([path, rows]) => ({
        name: path === '' ? copy.defaultWorkspace : (path.split(/[\\/]/u).filter(Boolean).at(-1) ?? copy.defaultWorkspace),
        path,
        sessions: rows,
      }))
    }
    return server.map(group => ({
      name: group.name === '' ? copy.defaultWorkspace : group.name,
      path: group.path,
      sessions: group.sessionIds.map(id => byId.get(id)).filter((session): session is SessionRow => session !== undefined),
    }))
  }, [byId, copy.defaultWorkspace, sessions, state?.groups])

  const active = activeId === null ? undefined : byId.get(activeId)
  const connected = state?.relay.active === true && !stale
  const approvals = state?.approvals ?? []

  const postJson = async (name: string, body: Record<string, unknown>): Promise<boolean> => {
    const response = await apiCall(API(name), {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
    return response.ok
  }

  const createTask = async (content: string): Promise<void> => {
    if (content.length === 0 || busy) return
    setBusy(true)
    setSendError(null)
    try {
      const response = await apiCall(API('create'), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(composeCwd ? { content, cwd: composeCwd } : { content }),
      })
      if (!response.ok) {
        setSendError(copy.sendFailed)
        return
      }
      const payload = await response.json() as { sessionId?: string }
      setComposing(false)
      setComposeCwd(null)
      await poll()
      if (typeof payload.sessionId === 'string' && payload.sessionId !== '') {
        setActiveId(payload.sessionId)
        setThread([{ role: 'user', text: content, time: Date.now() }])
        setExpectingReply(true)
        void loadThread(payload.sessionId)
      }
    } finally {
      setBusy(false)
    }
  }

  const sendReply = async (content: string): Promise<void> => {
    if (activeId === null || content.length === 0 || busy) return
    setBusy(true)
    setSendError(null)
    setExpectingReply(true)
    setThread(current => [...current, { role: 'user', text: content, time: Date.now() }])
    try {
      const ok = await postJson('prompt', { sessionId: activeId, content })
      if (!ok) {
        setSendError(copy.sendFailed)
        setExpectingReply(false)
        setThread(current => current.filter((line, index) => !(index === current.length - 1 && line.text === content)))
      }
      await poll()
      await loadThread(activeId)
    } finally {
      setBusy(false)
    }
  }

  const openSession = (id: string): void => {
    setComposing(false)
    setComposeCwd(null)
    setSendError(null)
    setExpectingReply(false)
    setActiveId(id)
    setThread([])
    void loadThread(id)
  }

  const startInWorkspace = (path: string): void => {
    setActiveId(null)
    setSendError(null)
    setComposeCwd(path === '' ? null : path)
    setComposing(true)
  }

  const backToList = (): void => {
    setActiveId(null)
    setComposing(false)
    setComposeCwd(null)
    setSendError(null)
    setExpectingReply(false)
  }

  if (phase === 'bootstrapping') {
    return <main className="flex min-h-screen items-center justify-center bg-[#111315] text-foreground"><LoaderCircle aria-label="loading" className="size-7 animate-spin text-muted-foreground" /></main>
  }

  if (phase === 'expired') {
    return <main className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center gap-4 bg-[#111315] px-6 text-center text-foreground">
      <Smartphone aria-hidden className="size-8 text-muted-foreground/50" />
      <p className="text-sm text-muted-foreground">{copy.expired}</p>
      <Button onClick={() => { void poll() }} variant="outline">{copy.retry}</Button>
    </main>
  }

  if (active !== undefined) {
    return <main className="mx-auto flex h-screen max-w-md flex-col bg-[#111315] text-foreground">
      <header className="sticky top-0 z-10 border-b border-white/8 bg-[#111315]/95 px-3 py-3 backdrop-blur">
        <div className="flex items-center gap-2">
          <button aria-label={copy.back} className="flex size-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-white/8" onClick={backToList} type="button">
            <ArrowLeft className="size-4" />
          </button>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-[16px] font-semibold">{active.title ?? active.id.slice(0, 8)}</h1>
            <p className="truncate text-xs text-muted-foreground">{connected ? copy.connected : copy.connecting}</p>
          </div>
          <StatusPill copy={copy} running={active.running} />
        </div>
        {approvals.some(item => item.sessionId === active.id)
          ? <p className="mt-2 rounded-lg bg-amber-500/10 px-3 py-1.5 text-xs text-amber-400">{copy.approvalBanner} — {copy.approvalNote}</p>
          : null}
      </header>
      <section className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {thread.length === 0
          ? <p className="pt-16 text-center text-sm text-muted-foreground">{copy.emptyThread}</p>
          : thread.map((line, index) => <article className={`max-w-[92%] rounded-2xl px-3.5 py-2.5 text-[14px] leading-relaxed ${line.role === 'user' ? 'ml-auto bg-sky-500/20 text-foreground' : 'bg-white/6 text-foreground/95'}`} key={`${line.role}:${String(line.time)}:${String(index)}`}>
            <p className="whitespace-pre-wrap break-words">{line.text}</p>
          </article>)}
        {expectingReply || active.running ? <ThinkingRow copy={copy} /> : null}
        <div ref={threadEnd} />
      </section>
      {sendError !== null ? <p className="px-4 pb-1 text-center text-xs text-amber-400">{sendError}</p> : null}
      <Composer disabled={busy} onSend={text => { void sendReply(text) }} placeholder={copy.replyPlaceholder} />
    </main>
  }

  if (composing) {
    return <main className="mx-auto flex h-screen max-w-md flex-col bg-[#111315] text-foreground">
      <header className="sticky top-0 z-10 border-b border-white/8 bg-[#111315]/95 px-3 py-3 backdrop-blur">
        <div className="flex items-center gap-2">
          <button aria-label={copy.back} className="flex size-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-white/8" onClick={backToList} type="button">
            <ArrowLeft className="size-4" />
          </button>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-[16px] font-semibold">{copy.newSession}</h1>
            <p className="truncate text-xs text-muted-foreground">{composeCwd ?? copy.defaultWorkspace}</p>
          </div>
        </div>
      </header>
      <section className="flex-1" />
      {sendError !== null ? <p className="px-4 pb-1 text-center text-xs text-amber-400">{sendError}</p> : null}
      <Composer autoFocus disabled={busy} onSend={text => { void createTask(text) }} placeholder={copy.promptPlaceholder} />
    </main>
  }

  return <main className="mx-auto flex h-screen max-w-md flex-col bg-[#111315] text-foreground">
    <header className="sticky top-0 z-10 border-b border-white/8 bg-[#111315]/95 px-4 pb-3 pt-4 backdrop-blur">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-[17px] font-semibold tracking-tight">{copy.title}</h1>
          <p className={`mt-0.5 text-xs ${connected ? 'text-emerald-400' : 'text-muted-foreground'}`}>{connected ? copy.connected : copy.connecting}</p>
        </div>
        <button aria-label="refresh" className="flex size-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-white/8" onClick={() => { void poll() }} type="button">
          <RefreshCw className="size-4" />
        </button>
      </div>
      <div className="mt-4 flex items-end justify-between gap-3">
        <div>
          <p className="text-sm font-medium">{copy.section}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">{copy.stats(groups.length, sessions.length)}</p>
        </div>
      </div>
      {stale ? <p className="mt-2 rounded-lg bg-amber-500/10 px-3 py-1.5 text-xs text-amber-400">{copy.stale}</p> : null}
      {approvals.length > 0
        ? <p className="mt-2 rounded-lg bg-amber-500/10 px-3 py-1.5 text-xs text-amber-400">{copy.approvalBanner}: {approvals.map(item => item.toolName).join(', ')} — {copy.approvalNote}</p>
        : null}
    </header>
    <section className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
      {sessions.length === 0
        ? <p className="pt-16 text-center text-sm text-muted-foreground">{copy.empty}</p>
        : groups.map(group => {
          const key = group.path || group.name
          return <WorkspaceCard
            copy={copy}
            key={key}
            name={group.name}
            now={now}
            onCreate={() => { startInWorkspace(group.path) }}
            onOpenSession={openSession}
            onToggle={() => {
              setOpenWorkspaces(current => {
                const next = new Set(current)
                if (next.has(key)) next.delete(key)
                else next.add(key)
                return next
              })
            }}
            open={openWorkspaces.has(key)}
            path={group.path}
            sessions={group.sessions}
          />
        })}
    </section>
  </main>
}
