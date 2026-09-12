import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Ban, ChevronDown, LoaderCircle, Plus, Send, Sparkles } from 'lucide-react'
import { Button } from '../components/ui/button.tsx'

/** Relative bases survive both loopback (/mobile/) and relay (/r/<pair>/mobile/) hosting. */
const API = (name: string) => new URL(`../api/desktop/mobile/${name}`, window.location.href).href
const FULL_CLIENT = () => new URL('../', window.location.href).href
const TOKEN_EXCHANGE = (token: string) => new URL(`../?token=${encodeURIComponent(token)}`, window.location.href).href
const POLL_MS = 2_500

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

type Phase = 'bootstrapping' | 'expired' | 'loading' | 'ready' | 'error'

function locale(): 'en' | 'zh' {
  return navigator.language.toLowerCase().startsWith('zh') ? 'zh' : 'en'
}

const COPY = {
  en: Object.freeze({
    appName: 'DSH',
    newSession: 'New session',
    promptPlaceholder: 'Ask DSH anything…',
    send: 'Send',
    defaultWorkspace: 'Default',
    running: 'Running',
    idle: 'Idle',
    empty: 'No sessions yet. Start one below.',
    cancel: 'Stop',
    approvalBanner: 'Awaiting desktop approval',
    approvalNote: 'Approvals are answered on the desktop client.',
    expired: 'This link is no longer valid. Regenerate it from the desktop tray.',
    error: 'State unavailable.',
    retry: 'Retry',
    fullClient: 'Full client',
    workspace: 'Workspace',
    messagePlaceholder: 'Reply…',
    justNow: 'just now',
    minutesAgo: (value: number) => `${String(value)} min ago`,
    hoursAgo: (value: number) => `${String(value)} h ago`,
    daysAgo: (value: number) => `${String(value)} d ago`,
  }),
  zh: Object.freeze({
    appName: 'DSH',
    newSession: '新会话',
    promptPlaceholder: '向 DSH 问点什么…',
    send: '发送',
    defaultWorkspace: '默认工作区',
    running: '运行中',
    idle: '空闲',
    empty: '还没有会话,在下方开始一个。',
    cancel: '停止',
    approvalBanner: '等待桌面端批准',
    approvalNote: '批准操作需要在桌面客户端完成。',
    expired: '链接已失效,请在桌面端托盘重新生成。',
    error: '状态获取失败。',
    retry: '重试',
    fullClient: '完整客户端',
    workspace: '工作区',
    messagePlaceholder: '继续对话…',
    justNow: '刚刚',
    minutesAgo: (value: number) => `${String(value)} 分钟前`,
    hoursAgo: (value: number) => `${String(value)} 小时前`,
    daysAgo: (value: number) => `${String(value)} 天前`,
  }),
} as const

interface MobileCopy {
  readonly appName: string
  readonly newSession: string
  readonly promptPlaceholder: string
  readonly send: string
  readonly defaultWorkspace: string
  readonly running: string
  readonly idle: string
  readonly empty: string
  readonly cancel: string
  readonly approvalBanner: string
  readonly approvalNote: string
  readonly expired: string
  readonly error: string
  readonly retry: string
  readonly fullClient: string
  readonly workspace: string
  readonly messagePlaceholder: string
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

/**
 * Mobile mirror of the desktop sidebar's session list, styled with the same
 * theme tokens: workspace groups with count badges, elevated session cards
 * with status dots and relative times, and the client's rounded composer.
 */
function SessionCard(props: {
  readonly session: SessionRow
  readonly now: number
  readonly copy: MobileCopy
  readonly open: boolean
  readonly busy: boolean
  readonly draft: string
  readonly onToggle: () => void
  readonly onDraft: (value: string) => void
  readonly onSend: () => void
  readonly onCancel: () => void
}): ReactNode {
  const { session, now, copy } = props
  return <div className={`overflow-hidden rounded-xl border border-border/70 bg-card shadow-sm ${session.running ? 'shadow-primary/5' : ''}`}>
    <button
      className="flex w-full items-start gap-3 px-4 py-3.5 text-left transition-colors active:bg-muted/70"
      onClick={props.onToggle}
      type="button"
    >
      <span className={`mt-[9px] size-2 shrink-0 rounded-full ${session.running ? 'animate-pulse bg-amber-500' : 'bg-muted-foreground/35'}`} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[15px] font-medium leading-6 text-foreground">
          {session.title ?? session.id.slice(0, 8)}
        </span>
        <span className="mt-0.5 block text-xs leading-5 text-muted-foreground">
          {relativeTime(session.updatedAt, now, copy)}
        </span>
      </span>
      <span className={`mt-1 shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium leading-4 ${session.running ? 'bg-amber-500/15 text-amber-600 dark:text-amber-400' : 'bg-muted text-muted-foreground'}`}>
        {session.running ? copy.running : copy.idle}
      </span>
      <ChevronDown aria-hidden className={`mt-1.5 size-4 shrink-0 text-muted-foreground/60 transition-transform ${props.open ? 'rotate-180' : ''}`} />
    </button>
    {props.open
      ? <div className="flex items-center gap-2 border-t border-border/60 bg-muted/40 px-3 py-2.5">
        <input
          className="h-10 min-w-0 flex-1 rounded-lg border border-border bg-background px-3 text-[15px] text-foreground outline-none placeholder:text-muted-foreground/70 focus-visible:border-ring focus-visible:ring-1 focus-visible:ring-ring/40"
          onChange={event => { props.onDraft(event.target.value) }}
          placeholder={copy.messagePlaceholder}
          value={props.draft}
        />
        <Button aria-label={copy.send} className="size-10 shrink-0 rounded-full p-0" disabled={props.busy || props.draft.trim().length === 0} onClick={props.onSend} size="icon" type="button"><Send className="size-4" /></Button>
        {session.running
          ? <Button aria-label={copy.cancel} className="size-10 shrink-0 rounded-full p-0" disabled={props.busy} onClick={props.onCancel} size="icon" type="button" variant="outline"><Ban className="size-4" /></Button>
          : null}
      </div>
      : null}
  </div>
}

export function MobileApp(): JSX.Element {
  const copy = copyFor()
  const [phase, setPhase] = useState<Phase>('bootstrapping')
  const [state, setState] = useState<StateResponse | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [openSession, setOpenSession] = useState<string | null>(null)
  const [sessionDrafts, setSessionDrafts] = useState<Record<string, string>>({})
  const [now, setNow] = useState(() => Date.now())
  const bootstrapped = useRef(false)
  const composer = useRef<HTMLInputElement | null>(null)

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
    }, POLL_MS)
    const onVisible = (): void => { if (document.visibilityState === 'visible') void poll() }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [poll])

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
        setDraft('')
        await poll()
      }
    } finally {
      setBusy(false)
    }
  }

  const sendToSession = async (): Promise<void> => {
    const sessionId = openSession
    const content = (sessionDrafts[sessionId ?? ''] ?? '').trim()
    if (sessionId === null || content.length === 0 || busy) return
    setBusy(true)
    try {
      const response = await apiCall(API('prompt'), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ sessionId, content }),
      })
      if (response.ok) {
        setSessionDrafts(current => ({ ...current, [sessionId]: '' }))
        await poll()
      }
    } finally {
      setBusy(false)
    }
  }

  const cancelSession = async (sessionId: string): Promise<void> => {
    if (busy) return
    setBusy(true)
    try {
      await apiCall(API('cancel'), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ sessionId }),
      })
      await poll()
    } finally {
      setBusy(false)
    }
  }

  const startNewSession = (): void => {
    setOpenSession(null)
    composer.current?.focus()
    composer.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }

  if (phase === 'bootstrapping') {
    return <main className="flex min-h-screen items-center justify-center bg-background text-foreground"><LoaderCircle aria-label="loading" className="size-7 animate-spin text-muted-foreground" /></main>
  }

  if (phase === 'expired') {
    return <main className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center gap-4 bg-background px-6 text-center text-foreground">
      <Sparkles aria-hidden className="size-8 text-muted-foreground/50" />
      <p className="text-sm text-muted-foreground">{copy.expired}</p>
      <Button onClick={() => { void poll() }} variant="outline">{copy.retry}</Button>
    </main>
  }

  const sessions = [...(state?.sessions ?? [])].sort((left, right) => right.updatedAt - left.updatedAt)
  const approvals = state?.approvals ?? []
  // Workspace groups come from the same registry the desktop sidebar reads
  // (title + manual session order); the cwd fallback only covers a server
  // that predates server-side grouping.
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

  return <main className="mx-auto flex h-screen max-w-md flex-col bg-muted/40 text-foreground">
    <header className="sticky top-0 z-10 border-b border-border/60 bg-background/95 backdrop-blur">
      <div className="flex items-center justify-between px-4 pb-3 pt-4">
        <button className="flex items-center gap-2.5 active:opacity-80" onClick={startNewSession} type="button">
          <span className="flex size-8 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-sm"><Plus aria-hidden className="size-4" strokeWidth={2} /></span>
          <span className="text-[15px] font-semibold tracking-tight text-foreground">{copy.newSession}</span>
        </button>
        <span className="flex items-center gap-2">
          {runningCount > 0
            ? <span className="flex items-center gap-1.5 rounded-full bg-amber-500/15 px-2.5 py-1 text-[11px] font-medium text-amber-600 dark:text-amber-400"><span className="size-1.5 animate-pulse rounded-full bg-amber-500" />{`${String(runningCount)} ${copy.running}`}</span>
            : null}
          <a className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline" href={FULL_CLIENT()}>{copy.fullClient}</a>
        </span>
      </div>
      {approvals.length > 0
        ? <p aria-live="polite" className="mx-4 mb-3 rounded-lg bg-amber-500/15 px-3 py-2 text-xs font-medium leading-5 text-amber-600 dark:text-amber-400">
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
          {rows.map(session => <SessionCard
            key={session.id}
            busy={busy}
            copy={copy}
            draft={sessionDrafts[session.id] ?? ''}
            now={now}
            onCancel={() => { void cancelSession(session.id) }}
            onDraft={value => { setSessionDrafts(current => ({ ...current, [session.id]: value })) }}
            onSend={() => { void sendToSession() }}
            onToggle={() => { setOpenSession(current => current === session.id ? null : session.id) }}
            open={openSession === session.id}
            session={session}
          />)}
        </div>
      </div>)}
    </section>
    <form
      className="sticky bottom-0 border-t border-border/60 bg-background/95 px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3 backdrop-blur"
      onSubmit={event => { event.preventDefault(); void createTask() }}
    >
      <div className="flex items-center gap-2 rounded-2xl border border-border bg-card px-3.5 py-1.5 shadow-sm focus-within:border-ring focus-within:ring-1 focus-within:ring-ring/40">
        <input
          className="h-11 min-w-0 flex-1 bg-transparent text-base text-foreground outline-none placeholder:text-muted-foreground/70"
          onChange={event => { setDraft(event.target.value) }}
          placeholder={copy.promptPlaceholder}
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
