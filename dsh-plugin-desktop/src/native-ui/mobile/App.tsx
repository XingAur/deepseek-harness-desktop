import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { ArrowLeft, Ban, LoaderCircle, Moon, Plus, Send, Sparkles, Sun } from 'lucide-react'

/** Relative bases survive both loopback (/mobile/) and relay (/r/<pair>/mobile/) hosting. */
const API = (name: string) => new URL(`../api/desktop/mobile/${name}`, window.location.href).href
const TOKEN_EXCHANGE = (token: string) => new URL(`../?token=${encodeURIComponent(token)}`, window.location.href).href
const POLL_MS = 2_500
const THEME_KEY = 'dsh-mobile-theme'

interface SessionRow {
  readonly id: string
  readonly title: string | null
  readonly running: boolean
  readonly updatedAt: number
  readonly cwd: string | null
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
  readonly groups: readonly { readonly name: string; readonly sessionIds: readonly string[] }[]
  readonly approvals: readonly PendingApproval[]
  readonly relay: { readonly active: boolean }
}

interface TranscriptLine {
  readonly role: 'user' | 'assistant'
  readonly text: string
  readonly time: number
}

type Phase = 'bootstrapping' | 'expired' | 'loading' | 'ready' | 'error'
type View = { kind: 'list' } | { kind: 'session'; id: string } | { kind: 'new' }
type ThemeMode = 'light' | 'dark'

function locale(): 'en' | 'zh' {
  return navigator.language.toLowerCase().startsWith('zh') ? 'zh' : 'en'
}

const COPY = {
  en: Object.freeze({
    newSession: 'New session',
    promptPlaceholder: 'Describe the task for the agent…',
    messagePlaceholder: 'Reply…',
    send: 'Send',
    defaultWorkspace: 'Default',
    running: 'Running',
    idle: 'Idle',
    thinking: 'Thinking',
    empty: 'No sessions yet. Start one from the top.',
    emptyTranscript: 'No messages yet. Say something below.',
    cancel: 'Stop',
    approvalBanner: 'Awaiting desktop approval',
    approvalNote: 'Approvals are answered on the desktop client.',
    expired: 'This link is no longer valid. Regenerate it from the desktop tray.',
    error: 'State unavailable.',
    retry: 'Retry',
    toggleTheme: 'Toggle theme',
    back: 'Back',
    newTaskTitle: 'New session',
    justNow: 'just now',
    minutesAgo: (value: number) => `${String(value)} min ago`,
    hoursAgo: (value: number) => `${String(value)} h ago`,
    daysAgo: (value: number) => `${String(value)} d ago`,
  }),
  zh: Object.freeze({
    newSession: '新会话',
    promptPlaceholder: '描述要交给 Agent 的任务…',
    messagePlaceholder: '继续对话…',
    send: '发送',
    defaultWorkspace: '默认工作区',
    running: '运行中',
    idle: '空闲',
    thinking: '思考中',
    empty: '还没有会话,点击上方新建。',
    emptyTranscript: '还没有消息,在下方说点什么。',
    cancel: '停止',
    approvalBanner: '等待桌面端批准',
    approvalNote: '批准操作需要在桌面客户端完成。',
    expired: '链接已失效,请在桌面端托盘重新生成。',
    error: '状态获取失败。',
    retry: '重试',
    toggleTheme: '切换深浅色',
    back: '返回',
    newTaskTitle: '新会话',
    justNow: '刚刚',
    minutesAgo: (value: number) => `${String(value)} 分钟前`,
    hoursAgo: (value: number) => `${String(value)} 小时前`,
    daysAgo: (value: number) => `${String(value)} 天前`,
  }),
} as const

interface MobileCopy {
  readonly newSession: string
  readonly promptPlaceholder: string
  readonly messagePlaceholder: string
  readonly send: string
  readonly defaultWorkspace: string
  readonly running: string
  readonly idle: string
  readonly thinking: string
  readonly empty: string
  readonly emptyTranscript: string
  readonly cancel: string
  readonly approvalBanner: string
  readonly approvalNote: string
  readonly expired: string
  readonly error: string
  readonly retry: string
  readonly toggleTheme: string
  readonly back: string
  readonly newTaskTitle: string
  readonly justNow: string
  readonly minutesAgo: (value: number) => string
  readonly hoursAgo: (value: number) => string
  readonly daysAgo: (value: number) => string
}

function copyFor(): MobileCopy {
  return locale() === 'zh' ? COPY.zh : COPY.en
}

function relativeTime(at: number, now: number, copy: MobileCopy): string {
  const delta = Math.max(0, Math.floor((now - at) / 1000))
  if (delta < 60) return copy.justNow
  if (delta < 3_600) return copy.minutesAgo(Math.floor(delta / 60))
  if (delta < 86_400) return copy.hoursAgo(Math.floor(delta / 3_600))
  return copy.daysAgo(Math.floor(delta / 86_400))
}

function workspaceLabel(cwd: string): string {
  const parts = cwd.split(/[\\/]/u).filter(part => part !== '')
  const tail = parts.slice(-2).join('/')
  return tail === '' ? cwd : tail
}

async function apiCall(input: string, init?: RequestInit): Promise<Response> {
  return await fetch(input, {
    ...init,
    credentials: 'same-origin',
    redirect: 'error',
    headers: { accept: 'application/json', ...(init?.headers ?? {}) },
  })
}

/** The client's thinking row: pulsing loader, bouncing dots, dimmed label. */
function ThinkingRow({ label }: { readonly label: string }): ReactNode {
  return <div className="flex items-center gap-2.5 px-1 py-3 text-sm text-muted-foreground" aria-live="polite">
    <LoaderCircle aria-hidden className="size-4 animate-spin text-muted-foreground/70" />
    <span className="flex items-center gap-1">
      <span className="dshMobileThinkingDot size-1.5 rounded-full bg-muted-foreground/70" />
      <span className="dshMobileThinkingDot size-1.5 rounded-full bg-muted-foreground/70" />
      <span className="dshMobileThinkingDot size-1.5 rounded-full bg-muted-foreground/70" />
    </span>
    {label}…
  </div>
}

/** One chat balloon: user on the right in primary, assistant on the left in card. */
function ChatLine({ line }: { readonly line: TranscriptLine }): ReactNode {
  const mine = line.role === 'user'
  return <div className={`flex w-full ${mine ? 'justify-end' : 'justify-start'}`}>
    <div className={`max-w-[86%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2.5 text-[15px] leading-6 shadow-sm ${mine
      ? 'rounded-br-md bg-primary text-primary-foreground'
      : 'rounded-bl-md border border-border/60 bg-card text-foreground'}`}>
      {line.text}
    </div>
  </div>
}

export function MobileApp(): JSX.Element {
  const copy = copyFor()
  const [phase, setPhase] = useState<Phase>('bootstrapping')
  const [state, setState] = useState<StateResponse | null>(null)
  const [view, setView] = useState<View>({ kind: 'list' })
  const [transcript, setTranscript] = useState<readonly TranscriptLine[]>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(0)
  const [theme, setTheme] = useState<ThemeMode>(() =>
    window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
  const [now, setNow] = useState(() => Date.now())
  const bootstrapped = useRef(false)
  const composer = useRef<HTMLInputElement | null>(null)
  const scroller = useRef<HTMLDivElement | null>(null)

  // Manual theme: an explicit choice persists and overrides the system scheme.
  useEffect(() => {
    const stored = window.localStorage.getItem(THEME_KEY)
    const initial = stored === 'light' || stored === 'dark'
      ? stored
      : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    setTheme(initial)
    document.documentElement.classList.toggle('dshMobileManualDark', initial === 'dark')
  }, [])
  const toggleTheme = (): void => {
    const next = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    window.localStorage.setItem(THEME_KEY, next)
    document.documentElement.classList.toggle('dshMobileManualDark', next === 'dark')
  }

  const poll = useCallback(async () => {
    if (document.visibilityState === 'hidden') return
    try {
      const response = await apiCall(API('state'))
      if (response.status === 401) {
        setPhase('expired')
        return
      }
      if (!response.ok) throw new Error(`HTTP ${String(response.status)}`)
      setState(await response.json() as StateResponse)
      setPhase('ready')
    } catch {
      setPhase('error')
    }
  }, [])

  const pollTranscript = useCallback(async (sessionId: string) => {
    try {
      const response = await apiCall(`${API('transcript')}?sessionId=${encodeURIComponent(sessionId)}`)
      if (!response.ok) return
      const payload = await response.json() as { messages?: readonly TranscriptLine[] }
      setTranscript(payload.messages ?? [])
    } catch { /* transient; the next tick retries */ }
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
      if (view.kind === 'session') void pollTranscript(view.id)
    }, POLL_MS)
    const onVisible = (): void => {
      if (document.visibilityState !== 'visible') return
      void poll()
      if (view.kind === 'session') void pollTranscript(view.id)
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [poll, pollTranscript, view])

  // Fresh transcript whenever the detail view opens.
  useEffect(() => {
    if (view.kind === 'session') void pollTranscript(view.id)
    else setTranscript([])
  }, [view, pollTranscript])

  // Keep the chat pinned to the newest line.
  useEffect(() => {
    const el = scroller.current
    if (el !== null) el.scrollTop = el.scrollHeight
  }, [transcript, sent])

  const openSession = (id: string): void => {
    setView({ kind: 'session', id })
  }

  const createTask = async (): Promise<void> => {
    const content = draft.trim()
    if (content.length === 0 || busy) return
    setBusy(true)
    try {
      const response = await apiCall(API('create'), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ content }),
      })
      if (response.ok) {
        const created = await response.json() as { sessionId?: string }
        setDraft('')
        await poll()
        if (typeof created.sessionId === 'string') {
          setView({ kind: 'session', id: created.sessionId })
          setSent(Date.now())
          void pollTranscript(created.sessionId)
        }
      }
    } finally {
      setBusy(false)
    }
  }

  const sendToSession = async (): Promise<void> => {
    if (view.kind !== 'session') return
    const sessionId = view.id
    const content = draft.trim()
    if (content.length === 0 || busy) return
    setBusy(true)
    setDraft('')
    setSent(Date.now())
    // Optimistic echo, replaced by the polled transcript.
    setTranscript(current => [...current, { role: 'user', text: content, time: Date.now() }])
    try {
      await apiCall(API('prompt'), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ sessionId, content }),
      })
      await pollTranscript(sessionId)
      await poll()
    } finally {
      setBusy(false)
    }
  }

  const cancelSession = async (): Promise<void> => {
    if (view.kind !== 'session' || busy) return
    setBusy(true)
    try {
      await apiCall(API('cancel'), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ sessionId: view.id }),
      })
      await poll()
    } finally {
      setBusy(false)
    }
  }

  if (phase === 'bootstrapping') {
    return <main className="flex min-h-screen items-center justify-center bg-background text-foreground"><LoaderCircle aria-label="loading" className="size-7 animate-spin text-muted-foreground" /></main>
  }

  if (phase === 'expired') {
    return <main className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center gap-4 bg-background px-6 text-center text-foreground">
      <Sparkles aria-hidden className="size-8 text-muted-foreground/50" />
      <p className="text-sm text-muted-foreground">{copy.expired}</p>
      <button className="rounded-full border border-border px-4 py-2 text-sm text-foreground active:bg-muted" onClick={() => { void poll() }} type="button">{copy.retry}</button>
    </main>
  }

  const sessions = [...(state?.sessions ?? [])].sort((left, right) => right.updatedAt - left.updatedAt)
  const approvals = state?.approvals ?? []
  const byId = new Map(sessions.map(session => [session.id, session]))
  const serverGroups = state?.groups ?? []
  const groups: Array<[string, SessionRow[]]> = serverGroups.length > 0
    ? serverGroups.map(group => [
        group.name === '' ? copy.defaultWorkspace : group.name,
        group.sessionIds
          .map(id => byId.get(id))
          .filter((session): session is SessionRow => session !== undefined),
      ])
    : (() => {
      const fallback: Array<[string, SessionRow[]]> = []
      for (const session of sessions) {
        const name = session.cwd !== null && session.cwd !== '' ? workspaceLabel(session.cwd) : copy.defaultWorkspace
        const last = fallback.at(-1)
        if (last !== undefined && last[0] === name) last[1].push(session)
        else fallback.push([name, [session]])
      }
      return fallback
    })()
  const runningCount = sessions.filter(session => session.running).length

  // ---- Detail view: transcript, thinking state, chat composer ----
  if (view.kind === 'session' || view.kind === 'new') {
    const isNew = view.kind === 'new'
    const session = isNew ? undefined : byId.get(view.id)
    const running = session?.running === true
    const title = isNew ? copy.newTaskTitle : (session?.title ?? view.id.slice(0, 8))
    const waiting = isNew
      ? false
      : running || (sent > 0 && Date.now() - sent < 120_000 && busy)
    const send = isNew ? createTask : sendToSession
    return <main className="mx-auto flex h-screen max-w-md flex-col bg-background text-foreground">
      <header className="sticky top-0 z-10 flex items-center gap-3 border-b border-border/60 bg-background/95 px-3 py-3 backdrop-blur">
        <button aria-label={copy.back} className="flex size-9 items-center justify-center rounded-full text-foreground active:bg-muted" onClick={() => { setView({ kind: 'list' }) }} type="button">
          <ArrowLeft aria-hidden className="size-5" />
        </button>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[15px] font-semibold leading-6 text-foreground">{title}</p>
          {isNew
            ? <p className="text-xs leading-4 text-muted-foreground">{copy.promptPlaceholder}</p>
            : <p className="flex items-center gap-1.5 text-xs leading-4 text-muted-foreground">
                {running ? <span className="size-1.5 animate-pulse rounded-full bg-amber-500" /> : null}
                {session !== undefined ? relativeTime(session.updatedAt, now, copy) : ''}
                {session?.cwd ? ` · ${workspaceLabel(session.cwd)}` : ''}
              </p>}
        </div>
        {running
          ? <button aria-label={copy.cancel} className="flex h-8 items-center gap-1.5 rounded-full border border-border px-3 text-xs text-foreground active:bg-muted" disabled={busy} onClick={() => { void cancelSession() }} type="button">
              <Ban aria-hidden className="size-3.5" />{copy.cancel}
            </button>
          : null}
        <button aria-label={copy.toggleTheme} className="flex size-9 items-center justify-center rounded-full text-muted-foreground active:bg-muted" onClick={toggleTheme} type="button">
          {theme === 'dark' ? <Sun aria-hidden className="size-4.5" /> : <Moon aria-hidden className="size-4.5" />}
        </button>
      </header>

      {approvals.length > 0
        ? <p aria-live="polite" className="mx-3 mt-3 rounded-lg bg-amber-500/15 px-3 py-2 text-xs font-medium leading-5 text-amber-500">
            {copy.approvalBanner}: {approvals.map(item => item.toolName).join(', ')} — {copy.approvalNote}
          </p>
        : null}

      <div className="flex-1 overflow-y-auto px-3 py-4" ref={scroller}>
        {isNew && transcript.length === 0
          ? <div className="pt-20 text-center">
              <Sparkles aria-hidden className="mx-auto size-8 text-muted-foreground/40" />
              <p className="mx-6 mt-3 text-sm leading-6 text-muted-foreground">{copy.promptPlaceholder}</p>
            </div>
          : null}
        {!isNew && transcript.length === 0
          ? <p className="pt-16 text-center text-sm text-muted-foreground">{copy.emptyTranscript}</p>
          : null}
        <div className="flex flex-col gap-2.5">
          {transcript.map((line, index) => <ChatLine key={`${String(line.time)}-${String(index)}`} line={line} />)}
          {waiting ? <ThinkingRow label={copy.thinking} /> : null}
        </div>
      </div>

      <form
        className="sticky bottom-0 border-t border-border/60 bg-background/95 px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3 backdrop-blur"
        onSubmit={event => { event.preventDefault(); void send() }}
      >
        <div className="flex items-center gap-2 rounded-2xl border border-border bg-card px-3.5 py-1.5 shadow-sm focus-within:border-ring focus-within:ring-1 focus-within:ring-ring/40">
          <input
            className="h-11 min-w-0 flex-1 bg-transparent text-base text-foreground outline-none placeholder:text-muted-foreground/70"
            onChange={event => { setDraft(event.target.value) }}
            placeholder={isNew ? copy.promptPlaceholder : copy.messagePlaceholder}
            ref={composer}
            value={draft}
          />
          <button
            aria-label={copy.send}
            className={`flex size-11 shrink-0 items-center justify-center rounded-full transition-all ${draft.trim().length === 0 || busy ? 'bg-muted text-muted-foreground/40' : 'bg-primary text-primary-foreground shadow-sm active:scale-95'}`}
            disabled={busy || draft.trim().length === 0}
            type="submit"
          >
            {busy ? <LoaderCircle aria-hidden className="size-4 animate-spin" /> : <Send aria-hidden className="size-4" />}
          </button>
        </div>
      </form>
    </main>
  }

  // ---- List view: workspace groups of session cards ----
  return <main className="mx-auto flex h-screen max-w-md flex-col bg-muted/40 text-foreground">
    <header className="sticky top-0 z-10 border-b border-border/60 bg-background/95 backdrop-blur">
      <div className="flex items-center justify-between px-4 pb-3 pt-4">
        <button className="flex items-center gap-2.5 active:opacity-80" onClick={() => { setView({ kind: 'new' }); setTimeout(() => composer.current?.focus(), 60) }} type="button">
          <span className="flex size-8 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-sm"><Plus aria-hidden className="size-4" strokeWidth={2} /></span>
          <span className="text-[15px] font-semibold tracking-tight text-foreground">{copy.newSession}</span>
        </button>
        <span className="flex items-center gap-2">
          {runningCount > 0
            ? <span className="flex items-center gap-1.5 rounded-full bg-amber-500/15 px-2.5 py-1 text-[11px] font-medium text-amber-500"><span className="size-1.5 animate-pulse rounded-full bg-amber-500" />{`${String(runningCount)} ${copy.running}`}</span>
            : null}
          <button aria-label={copy.toggleTheme} className="flex size-9 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground" onClick={toggleTheme} type="button">
            {theme === 'dark' ? <Sun aria-hidden className="size-4.5" /> : <Moon aria-hidden className="size-4.5" />}
          </button>
        </span>
      </div>
      {approvals.length > 0
        ? <p aria-live="polite" className="mx-4 mb-3 rounded-lg bg-amber-500/15 px-3 py-2 text-xs font-medium leading-5 text-amber-500">
            {copy.approvalBanner}: {approvals.map(item => item.toolName).join(', ')} — {copy.approvalNote}
          </p>
        : null}
    </header>
    <section aria-label="sessions" className="flex-1 overflow-y-auto px-3 pb-4">
      {phase === 'ready' && sessions.length === 0
        ? <div className="pt-24 text-center">
            <Sparkles aria-hidden className="mx-auto size-8 text-muted-foreground/40" />
            <p className="mt-3 text-sm text-muted-foreground">{copy.empty}</p>
          </div>
        : null}
      {phase === 'error' ? <p className="pt-10 text-center text-sm text-muted-foreground">{copy.error}</p> : null}
      {groups.map(([name, rows]) => <div key={name} className="mt-4">
        <h2 className="flex items-center gap-2 px-2 pb-2">
          <span className="truncate text-xs font-semibold uppercase tracking-wide text-muted-foreground">{name}</span>
          <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium leading-4 text-muted-foreground tabular-nums">{String(rows.length)}</span>
        </h2>
        <div className="flex flex-col gap-2">
          {rows.map(session => <button
            className="flex w-full items-start gap-3 rounded-xl border border-border/70 bg-card px-4 py-3.5 text-left shadow-sm transition-colors active:bg-muted/70"
            key={session.id}
            onClick={() => { openSession(session.id) }}
            type="button"
          >
            <span className={`mt-[9px] size-2 shrink-0 rounded-full ${session.running ? 'animate-pulse bg-amber-500' : 'bg-muted-foreground/35'}`} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[15px] font-medium leading-6 text-foreground">
                {session.title ?? session.id.slice(0, 8)}
              </span>
              <span className="mt-0.5 block text-xs leading-5 text-muted-foreground">
                {relativeTime(session.updatedAt, now, copy)}
                {session.cwd ? ` · ${workspaceLabel(session.cwd)}` : ''}
              </span>
            </span>
            <span className={`mt-1 shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium leading-4 ${session.running ? 'bg-amber-500/15 text-amber-500' : 'bg-muted text-muted-foreground'}`}>
              {session.running ? copy.running : copy.idle}
            </span>
          </button>)}
        </div>
      </div>)}
    </section>
  </main>
}
