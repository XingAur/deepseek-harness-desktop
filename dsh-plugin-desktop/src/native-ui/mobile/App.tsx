import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowLeft, Check, Eye, ListFilter, LoaderCircle, Moon, MoreHorizontal, PencilLine, Plus, ShieldAlert, Sparkles, SquareTerminal, Sun } from 'lucide-react'
import { copyFor, formatTokens, relativeTime, workspaceLabel } from './copy.ts'
import { ThinkingRow, TranscriptView } from './Transcript.tsx'
import { ContextSheet, ModelSheet, PermissionSheet, RenameSheet, Sheet } from './Sheets.tsx'
import { InterruptionCard, type AnswerDraft } from './InterruptionCard.tsx'
import { Composer, type ComposerAttachment } from './Composer.tsx'
import { SidePanel, type PanelTab } from './SidePanel.tsx'
import type { ModelCatalog, ModelSelectionValue, PermissionOption, PermissionPresetsResponse, SessionFacts, SessionInfo, SessionRow, StateResponse, TranscriptItem, TranscriptResponse } from './types.ts'

/** Relative bases survive both loopback (/mobile/) and relay (/r/<pair>/mobile/) hosting. */
const API = (name: string) => new URL(`../api/desktop/mobile/${name}`, window.location.href).href
const TOKEN_EXCHANGE = (token: string) => new URL(`../?token=${encodeURIComponent(token)}`, window.location.href).href
const POLL_MS = 2_500
/** Within this distance of the bottom the view still auto-follows new lines. */
const NEAR_BOTTOM_PX = 120
const THEME_KEY = 'dsh-mobile-theme'
const CLIENT_KEY = 'dsh-mobile-client'
const DETAIL_MODE_KEY = 'dsh-mobile-detail-mode'

/** One stable id per browser profile: the host binds the pairing link to the
 * first client it sees and rejects every other device until regeneration. */
const CLIENT_ID: string = (() => {
  let id = window.localStorage.getItem(CLIENT_KEY)
  if (id === null || id === '') {
    id = typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`
    window.localStorage.setItem(CLIENT_KEY, id)
  }
  return id
})()

type Phase = 'bootstrapping' | 'expired' | 'in-use' | 'loading' | 'ready' | 'error'
type View = { kind: 'list' } | { kind: 'session'; id: string } | { kind: 'new' }
type ThemeMode = 'light' | 'dark'
/** Key-info mode trims the timeline to chat bubbles + todos, hiding
 * reasoning and tool rows; details stay reachable via the side panel. */
type DetailMode = 'all' | 'key'
type SheetKind = 'menu' | 'permission' | 'model' | 'effort' | 'context' | 'rename' | null

async function apiCall(input: string, init?: RequestInit): Promise<Response> {
  return await fetch(input, {
    ...init,
    credentials: 'same-origin',
    redirect: 'error',
    headers: {
      accept: 'application/json',
      'x-dsh-mobile-client': CLIENT_ID,
      ...(init?.headers ?? {}),
    },
  })
}

async function postJson(name: string, body: Record<string, unknown>): Promise<Response> {
  return await apiCall(API(name), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** Polls while the link is held by another device; auto-enters once it frees. */
function InUseRetry({ onFree }: { readonly onFree: () => Promise<void> }): JSX.Element {
  const [waiting, setWaiting] = useState(true)
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'hidden') return
      setWaiting(true)
      void onFree().finally(() => { setWaiting(false) })
    }, 5_000)
    return () => { window.clearInterval(timer) }
  }, [onFree])
  return <p className="flex items-center gap-2 text-xs text-muted-foreground">
    {waiting ? <LoaderCircle aria-hidden className="size-3.5 animate-spin" /> : null}
  </p>
}

export function MobileApp(): JSX.Element {
  const copy = copyFor()
  const [phase, setPhase] = useState<Phase>('bootstrapping')
  const [state, setState] = useState<StateResponse | null>(null)
  const [view, setView] = useState<View>({ kind: 'list' })
  const [transcript, setTranscript] = useState<readonly TranscriptItem[]>([])
  const [sessionInfo, setSessionInfo] = useState<SessionInfo | null>(null)
  const [facts, setFacts] = useState<SessionFacts | null>(null)
  const [presetOptions, setPresetOptions] = useState<readonly PermissionOption[] | null>(null)
  const [presetDefault, setPresetDefault] = useState<string | null>(null)
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null)
  const [draft, setDraft] = useState('')
  const [attachments, setAttachments] = useState<readonly ComposerAttachment[]>([])
  const [busy, setBusy] = useState(false)
  const [acting, setActing] = useState(false)
  const [sent, setSent] = useState(0)
  const [sheet, setSheet] = useState<SheetKind>(null)
  const [panel, setPanel] = useState<PanelTab | null>(null)
  const [planDraft, setPlanDraft] = useState<string | null>(null)
  const [flash, setFlash] = useState<string | null>(null)
  const [theme, setTheme] = useState<ThemeMode>(() =>
    window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
  const [detailMode, setDetailMode] = useState<DetailMode>(() =>
    window.localStorage.getItem(DETAIL_MODE_KEY) === 'key' ? 'key' : 'all')
  const [now, setNow] = useState(() => Date.now())
  const bootstrapped = useRef(false)
  const composer = useRef<HTMLInputElement | null>(null)
  const scroller = useRef<HTMLDivElement | null>(null)
  /** True while the transcript is scrolled near the bottom; polled updates
   * only auto-follow when this holds, so reading history is not yanked away. */
  const pinned = useRef(true)
  const [atBottom, setAtBottom] = useState(true)
  const lastPanelTab = useRef<PanelTab>('plan')

  // Manual theme: an explicit choice persists and overrides the system scheme.
  // Both directions carry a class so manual light also beats a dark system.
  const applyManualThemeClass = (mode: ThemeMode): void => {
    document.documentElement.classList.toggle('dshMobileManualDark', mode === 'dark')
    document.documentElement.classList.toggle('dshMobileManualLight', mode === 'light')
  }
  useEffect(() => {
    const stored = window.localStorage.getItem(THEME_KEY)
    const initial = stored === 'light' || stored === 'dark'
      ? stored
      : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    setTheme(initial)
    applyManualThemeClass(initial)
  }, [])
  const toggleTheme = (): void => {
    const next = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    window.localStorage.setItem(THEME_KEY, next)
    applyManualThemeClass(next)
  }
  const selectDetailMode = (mode: DetailMode): void => {
    setDetailMode(mode)
    window.localStorage.setItem(DETAIL_MODE_KEY, mode)
  }

  useEffect(() => {
    if (flash === null) return
    const timer = window.setTimeout(() => { setFlash(null) }, 2_600)
    return () => { window.clearTimeout(timer) }
  }, [flash])

  // The plan tab keeps showing the latest reviewed plan even after it is answered.
  useEffect(() => {
    const pending = [...(state?.interruptions ?? [])]
      .filter(item => item.kind === 'question')
      .sort((left, right) => right.at - left.at)
    for (const item of pending) {
      const detail = (item.questions ?? [])
        .find(question => question.intent?.kind === 'plan-review' && question.detail !== undefined && question.detail !== '')?.detail
      if (detail !== undefined) {
        setPlanDraft(detail)
        return
      }
    }
  }, [state?.interruptions])

  const poll = useCallback(async () => {
    if (document.visibilityState === 'hidden') return
    try {
      const response = await apiCall(API('state'))
      if (response.status === 401) {
        setPhase('expired')
        return
      }
      if (response.status === 403) {
        setPhase('in-use')
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
      const payload = await response.json() as TranscriptResponse
      setTranscript(payload.messages ?? [])
      setFacts(payload.facts ?? null)
    } catch { /* transient; the next tick retries */ }
  }, [])

  const ensurePresetOptions = useCallback(async (): Promise<void> => {
    if (presetOptions !== null) return
    try {
      const response = await apiCall(API('permission-presets'))
      if (!response.ok) return
      const payload = await response.json() as PermissionPresetsResponse
      setPresetOptions(payload.options ?? [])
      setPresetDefault(payload.defaultPreset ?? null)
    } catch { /* the pill simply stays hidden */ }
  }, [presetOptions])

  const pollSessionInfo = useCallback(async (sessionId: string) => {
    try {
      const response = await apiCall(`${API('session-info')}?sessionId=${encodeURIComponent(sessionId)}`)
      if (!response.ok) return
      setSessionInfo(await response.json() as SessionInfo)
    } catch { /* transient; the next tick retries */ }
  }, [])

  const ensureCatalog = useCallback(async (): Promise<void> => {
    try {
      const response = await apiCall(API('model-catalog'))
      if (response.ok) setCatalog(await response.json() as ModelCatalog)
    } catch { /* surfaced by the empty sheet */ }
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
      if (view.kind === 'session') {
        void pollTranscript(view.id)
        void pollSessionInfo(view.id)
      }
    }, POLL_MS)
    const onVisible = (): void => {
      if (document.visibilityState !== 'visible') return
      void poll()
      if (view.kind === 'session') {
        void pollTranscript(view.id)
        void pollSessionInfo(view.id)
      }
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [poll, pollTranscript, pollSessionInfo, view])

  // Fresh transcript whenever the detail view opens. The catalog is prefetched
  // so the effort pill renders immediately, not only after opening the model
  // sheet once.
  useEffect(() => {
    pinned.current = true
    setAtBottom(true)
    if (view.kind === 'session') {
      void pollTranscript(view.id)
      void pollSessionInfo(view.id)
      void ensurePresetOptions()
      void ensureCatalog()
    } else {
      setTranscript([])
      setSessionInfo(null)
      setFacts(null)
    }
  }, [view, pollTranscript, pollSessionInfo, ensurePresetOptions, ensureCatalog])

  const onScrollerScroll = useCallback((): void => {
    const el = scroller.current
    if (el === null) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX
    pinned.current = nearBottom
    setAtBottom(nearBottom)
  }, [])

  const jumpToLatest = useCallback((): void => {
    const el = scroller.current
    if (el === null) return
    pinned.current = true
    setAtBottom(true)
    el.scrollTop = el.scrollHeight
  }, [])

  // Follow the newest line only while the user is (or just chose to be) at the
  // bottom; otherwise the 2.5s poll would keep snapping history reading away.
  useEffect(() => {
    const el = scroller.current
    if (el !== null && pinned.current) el.scrollTop = el.scrollHeight
  }, [transcript, sent])

  const openSession = (id: string): void => {
    setView({ kind: 'session', id })
  }

  const createTask = async (): Promise<void> => {
    const content = draft.trim()
    if ((content.length === 0 && attachments.length === 0) || busy) return
    setBusy(true)
    try {
      const response = await postJson('create', { content, images: attachments })
      if (response.ok) {
        const created = await response.json() as { sessionId?: string }
        setDraft('')
        setAttachments([])
        await poll()
        if (typeof created.sessionId === 'string') {
          setView({ kind: 'session', id: created.sessionId })
          pinned.current = true
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
    if (content.length === 0 && attachments.length === 0) return
    if (busy) return
    setBusy(true)
    setDraft('')
    pinned.current = true
    setSent(Date.now())
    const sentImages = attachments
    setAttachments([])
    // Optimistic echo, replaced by the polled transcript.
    if (content !== '') setTranscript(current => [...current, { kind: 'user', text: content, time: Date.now(), imageCount: sentImages.length }])
    try {
      await postJson('prompt', { sessionId, content, images: sentImages })
      await pollTranscript(sessionId)
      await pollSessionInfo(sessionId)
      await poll()
    } finally {
      setBusy(false)
    }
  }

  const cancelSession = async (): Promise<void> => {
    if (view.kind !== 'session' || busy) return
    setBusy(true)
    try {
      await postJson('cancel', { sessionId: view.id })
      await poll()
    } finally {
      setBusy(false)
    }
  }

  const decideInterruption = async (key: string, action: 'allow' | 'reject' | 'delegate'): Promise<void> => {
    if (acting) return
    setActing(true)
    try {
      const response = await postJson('approve', { key, action })
      if (response.ok) setFlash(copy.decisionRecorded)
      else if (response.status === 409) setFlash(copy.decisionConflict)
      else setFlash(`${copy.actionFailed}(${String(response.status)})`)
      await poll()
    } catch {
      setFlash(`${copy.actionFailed}(网络)`)
    } finally {
      setActing(false)
    }
  }

  const answerInterruption = async (key: string, answers: readonly AnswerDraft[]): Promise<void> => {
    if (acting) return
    setActing(true)
    try {
      const response = await postJson('answer', { key, answers })
      if (response.ok) setFlash(copy.decisionRecorded)
      else if (response.status === 409) setFlash(copy.decisionConflict)
      else setFlash(`${copy.actionFailed}(${String(response.status)})`)
      await poll()
    } catch {
      setFlash(`${copy.actionFailed}(网络)`)
    } finally {
      setActing(false)
    }
  }

  const removeQueueItem = async (itemId: string): Promise<void> => {
    if (view.kind !== 'session') return
    try {
      await postJson('queue-remove', { sessionId: view.id, itemId })
      await pollSessionInfo(view.id)
    } catch {
      setFlash(`${copy.actionFailed}(网络)`)
    }
  }

  const applyPermission = async (preset: string): Promise<void> => {
    if (view.kind !== 'session') return
    setActing(true)
    try {
      const response = await postJson('permission', { sessionId: view.id, preset })
      setFlash(response.ok ? copy.decisionRecorded : `${copy.actionFailed}(${String(response.status)})`)
      if (response.ok) setSheet(null)
      if (view.kind === 'session') await pollSessionInfo(view.id)
    } catch {
      setFlash(`${copy.actionFailed}(网络)`)
    } finally {
      setActing(false)
    }
  }

  const applyModel = async (selection: ModelSelectionValue): Promise<void> => {
    if (view.kind !== 'session') return
    setActing(true)
    try {
      const response = await postJson('select-model', { sessionId: view.id, ...selection })
      setFlash(response.ok ? copy.decisionRecorded : `${copy.actionFailed}(${String(response.status)})`)
      if (response.ok) setSheet(null)
      if (view.kind === 'session') await pollSessionInfo(view.id)
    } catch {
      setFlash(`${copy.actionFailed}(网络)`)
    } finally {
      setActing(false)
    }
  }

  const doCompact = async (): Promise<void> => {
    if (view.kind !== 'session') return
    setActing(true)
    try {
      const response = await postJson('compact', { sessionId: view.id })
      setFlash(response.ok ? copy.compactDone : `${copy.actionFailed}(${String(response.status)})`)
      setSheet(null)
      await poll()
    } catch {
      setFlash(`${copy.actionFailed}(网络)`)
    } finally {
      setActing(false)
    }
  }

  const doRename = async (title: string): Promise<void> => {
    if (view.kind !== 'session') return
    setActing(true)
    try {
      const response = await postJson('rename', { sessionId: view.id, title })
      setFlash(response.ok ? copy.decisionRecorded : `${copy.actionFailed}(${String(response.status)})`)
      if (response.ok) setSheet(null)
      await poll()
    } catch {
      setFlash(`${copy.actionFailed}(网络)`)
    } finally {
      setActing(false)
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

  if (phase === 'in-use') {
    return <main className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center gap-4 bg-background px-6 text-center text-foreground">
      <ShieldAlert aria-hidden className="size-8 text-orange-500/70" />
      <p className="text-sm text-muted-foreground">{copy.linkInUse}</p>
      <InUseRetry onFree={async () => { await poll() }} />
    </main>
  }

  const sessions = [...(state?.sessions ?? [])].sort((left, right) => right.updatedAt - left.updatedAt)
  const interruptions = state?.interruptions ?? []
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

  const flashBar = flash === null ? null
    : <p aria-live="polite" className="fixed left-1/2 top-3 z-40 -translate-x-1/2 rounded-full bg-foreground/90 px-4 py-1.5 text-xs font-medium text-background shadow-lg">{flash}</p>

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
    const viewInterruptions = isNew ? [] : interruptions.filter(item => item.sessionId === null || item.sessionId === view.id)
    // Key-info mode trims reasoning/tool rows from the timeline; the empty-state
    // checks and the side panel below still use the unfiltered transcript.
    const visibleItems = detailMode === 'key'
      ? transcript.filter(item => item.kind !== 'reasoning' && item.kind !== 'tool')
      : transcript
    // Current values prefer the log-derived facts: the control projection only
    // covers live sessions reliably, the durable log covers every session.
    const permissionCurrentValue = sessionInfo?.permissions?.currentValue
      ?? facts?.permissionPreset
      ?? presetDefault
      ?? null
    const permissionOptions = sessionInfo?.permissions?.options ?? presetOptions
    const permissionLabel = permissionCurrentValue === null || permissionOptions === null || permissionOptions.length === 0
      ? null
      : (permissionOptions.find(option => option.value === permissionCurrentValue)?.name ?? permissionCurrentValue)
    const modelLabel = facts?.model?.model ?? sessionInfo?.model?.model ?? null
    // The effort pill only needs the current selection; the catalog feeds the
    // picker sheet, not the pill, so it shows even before the catalog loads.
    const currentModel = facts?.model ?? sessionInfo?.model ?? null
    const effortLabel = currentModel?.reasoningEffort ?? null
    const effortChoices = currentModel === null
      ? []
      : (catalog?.groups ?? [])
          .find(group => group.id === currentModel.provider)?.models
          .find(row => row.id === currentModel.model)?.reasoning?.efforts ?? []
    const permissionDanger = permissionCurrentValue === 'danger-full-access'
    return <main className="mx-auto flex h-screen max-w-md flex-col bg-background text-foreground">
      <header className="sticky top-0 z-10 flex items-center gap-2 border-b border-border/60 bg-background/95 px-3 py-3 backdrop-blur">
        <button aria-label={copy.back} className="flex size-9 shrink-0 items-center justify-center rounded-full text-foreground active:bg-muted" onClick={() => { setView({ kind: 'list' }) }} type="button">
          <ArrowLeft aria-hidden className="size-5" />
        </button>
        <div className="flex min-w-0 flex-1 items-center gap-1.5">
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
          {!isNew
            ? <button aria-label={copy.sessionMenu} className="flex size-8 shrink-0 items-center justify-center rounded-full text-muted-foreground active:bg-muted" onClick={() => { setSheet('menu') }} type="button">
                <MoreHorizontal aria-hidden className="size-5" />
              </button>
            : null}
        </div>
      </header>

      {viewInterruptions.length > 0
        ? <div className="flex max-h-[55vh] max-h-[55dvh] shrink-0 flex-col gap-2 overflow-y-auto overscroll-contain px-3 pt-3">
            <InterruptionCard busy={acting} copy={copy} onAnswer={answerInterruption} onDecide={decideInterruption} record={viewInterruptions[0]} />
            {viewInterruptions.length > 1
              ? <p className="px-1 text-xs text-muted-foreground">{copy.pendingMore.replace('{n}', String(viewInterruptions.length - 1))}</p>
              : null}
          </div>
        : null}

      <div className="relative min-h-0 flex-1">
        <div className="absolute inset-0 overflow-y-auto px-3 py-4" onScroll={onScrollerScroll} ref={scroller}>
          {isNew && transcript.length === 0
            ? <div className="pt-20 text-center">
                <Sparkles aria-hidden className="mx-auto size-8 text-muted-foreground/40" />
                <p className="mx-6 mt-3 text-sm leading-6 text-muted-foreground">{copy.promptPlaceholder}</p>
              </div>
            : null}
          {!isNew && transcript.length === 0
            ? <p className="pt-16 text-center text-sm text-muted-foreground">{copy.emptyTranscript}</p>
            : null}
          <TranscriptView copy={copy} items={visibleItems} />
          {waiting ? <ThinkingRow label={copy.thinking} /> : null}
        </div>
        {atBottom || transcript.length === 0
          ? null
          : <button aria-label={copy.jumpLatest} className="absolute bottom-2 left-1/2 z-20 flex size-9 -translate-x-1/2 items-center justify-center rounded-full border border-border/70 bg-card text-muted-foreground shadow-lg active:bg-muted" onClick={jumpToLatest} type="button">
              <ArrowDown aria-hidden className="size-4" />
            </button>}
      </div>

      <Composer
        attachments={attachments}
        busy={busy}
        contextPercent={sessionInfo?.context?.percent ?? null}
        copy={copy}
        draft={draft}
        modelLabel={modelLabel}
        onAttachments={setAttachments}
        onDraft={setDraft}
        effortLabel={effortLabel}
        permissionDanger={permissionDanger}
        onOpenContext={() => { setSheet('context') }}
        onOpenModel={() => { void ensureCatalog(); setSheet('model') }}
        onOpenEffort={() => { void ensureCatalog(); setSheet('effort') }}
        onOpenPermissions={() => { void ensurePresetOptions(); setSheet('permission') }}
        onRemoveQueue={removeQueueItem}
        onSend={() => { void send() }}
        onStop={() => { void cancelSession() }}
        permissionLabel={permissionLabel}
        placeholder={isNew ? copy.promptPlaceholder : (running ? copy.queuePlaceholder : copy.messagePlaceholder)}
        queue={isNew ? [] : (sessionInfo?.queue ?? [])}
        running={running}
      />

      {sheet === 'menu' && view.kind === 'session'
        ? <Sheet onClose={() => { setSheet(null) }} title={copy.sessionMenu}>
            <div className="flex flex-col gap-1.5 pb-2">
              <button className="flex w-full items-center gap-3 rounded-xl border border-border/70 bg-card px-3.5 py-3 text-left text-sm font-medium text-foreground active:bg-muted/70" onClick={() => { selectDetailMode('all'); setSheet(null) }} type="button">
                <Eye aria-hidden className="size-4 shrink-0 text-muted-foreground" />
                {copy.showAllDetails}
                {detailMode === 'all' ? <Check aria-hidden className="ml-auto size-4 shrink-0 text-primary" /> : null}
              </button>
              <button className="flex w-full items-center gap-3 rounded-xl border border-border/70 bg-card px-3.5 py-3 text-left text-sm font-medium text-foreground active:bg-muted/70" onClick={() => { selectDetailMode('key'); setSheet(null) }} type="button">
                <ListFilter aria-hidden className="size-4 shrink-0 text-muted-foreground" />
                {copy.keyInfoOnly}
                {detailMode === 'key' ? <Check aria-hidden className="ml-auto size-4 shrink-0 text-primary" /> : null}
              </button>
              <div aria-hidden className="h-px bg-border/60" />
              <button className="flex w-full items-center gap-3 rounded-xl border border-border/70 bg-card px-3.5 py-3 text-left text-sm font-medium text-foreground active:bg-muted/70" onClick={() => { toggleTheme(); setSheet(null) }} type="button">
                {theme === 'dark' ? <Sun aria-hidden className="size-4 shrink-0 text-muted-foreground" /> : <Moon aria-hidden className="size-4 shrink-0 text-muted-foreground" />}
                {theme === 'dark' ? copy.themeToLight : copy.themeToDark}
              </button>
              <button className="flex w-full items-center gap-3 rounded-xl border border-border/70 bg-card px-3.5 py-3 text-left text-sm font-medium text-foreground active:bg-muted/70" onClick={() => { setSheet(null); setPanel(lastPanelTab.current) }} type="button">
                <SquareTerminal aria-hidden className="size-4 shrink-0 text-muted-foreground" />
                {copy.sidePanel}
              </button>
              <div aria-hidden className="h-px bg-border/60" />
              <button className="flex w-full items-center gap-3 rounded-xl border border-border/70 bg-card px-3.5 py-3 text-left text-sm font-medium text-foreground active:bg-muted/70" onClick={() => { setSheet('rename') }} type="button">
                <PencilLine aria-hidden className="size-4 shrink-0 text-muted-foreground" />
                {copy.rename}
              </button>
              <button className="flex w-full items-center gap-3 rounded-xl border border-border/70 bg-card px-3.5 py-3 text-left text-sm font-medium text-foreground active:bg-muted/70" disabled={acting} onClick={() => { void doCompact() }} type="button">
                <span aria-hidden className="text-base leading-none">⌥</span>
                {copy.compact}
              </button>
            </div>
          </Sheet>
        : null}
      {sheet === 'rename' && view.kind === 'session'
        ? <Sheet onClose={() => { setSheet(null) }} title={copy.renameTitle}>
            <RenameSheet busy={acting} copy={copy} initial={session?.title ?? ''} onSave={title => { void doRename(title) }} />
          </Sheet>
        : null}
      {sheet === 'permission' && permissionOptions !== null && permissionOptions.length > 0 && permissionCurrentValue !== null
        ? <Sheet onClose={() => { setSheet(null) }} title={copy.permissionTitle}>
            <PermissionSheet busy={acting} currentValue={permissionCurrentValue} onSelect={preset => { void applyPermission(preset) }} options={permissionOptions} />
          </Sheet>
        : null}
      {sheet === 'model'
        ? <Sheet onClose={() => { setSheet(null) }} title={copy.modelTitle}>
            <ModelSheet busy={acting} catalog={catalog} copy={copy} current={facts?.model ?? sessionInfo?.model ?? null} onApply={selection => { void applyModel(selection) }} />
          </Sheet>
        : null}
      {sheet === 'effort'
        ? <Sheet onClose={() => { setSheet(null) }} title={copy.thinkingLevel}>
            {currentModel === null || effortChoices.length === 0
              ? <p className="py-8 text-center text-sm text-muted-foreground">{copy.catalogEmpty}</p>
              : <div className="flex flex-col gap-1.5 pb-2">
                  {effortChoices.map(level => {
                    const selected = (currentModel.reasoningEffort ?? effortChoices.find(row => row.id === 'high')?.id) === level.id
                    return <button
                      className={`flex w-full items-center gap-2.5 rounded-xl border px-3.5 py-3 text-left transition-colors active:bg-muted/70 ${selected ? 'border-primary/60 bg-primary/5' : 'border-border/70 bg-card'}`}
                      disabled={acting}
                      key={level.id}
                      onClick={() => { void applyModel({ provider: currentModel.provider, model: currentModel.model, reasoningEffort: level.id }) }}
                      type="button"
                    >
                      <span className={`flex size-4 shrink-0 items-center justify-center rounded-full border ${selected ? 'border-primary bg-primary' : 'border-muted-foreground/40'}`} />
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium leading-5 text-foreground">{level.name}</span>
                        {level.description !== undefined ? <span className="mt-0.5 block text-xs leading-4 text-muted-foreground">{level.description}</span> : null}
                      </span>
                    </button>
                  })}
                </div>}
          </Sheet>
        : null}
      {sheet === 'context' && sessionInfo !== null
        ? <Sheet onClose={() => { setSheet(null) }} title={copy.contextTitle}>
            <ContextSheet copy={copy} formatTokens={formatTokens} info={sessionInfo} />
          </Sheet>
        : null}
      {panel !== null && !isNew
        ? <SidePanel
            copy={copy}
            onClose={() => { setPanel(null) }}
            onTab={tab => { lastPanelTab.current = tab; setPanel(tab) }}
            planMarkdown={planDraft}
            tab={panel}
            transcript={transcript}
          />
        : null}
      {flashBar}
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
      {interruptions.length > 0
        ? <div className="flex flex-col gap-2 px-4 pb-3">
            {interruptions.slice(0, 2).map(record => <InterruptionCard busy={acting} copy={copy} key={record.key} onAnswer={answerInterruption} onDecide={decideInterruption} record={record} />)}
            {interruptions.length > 2 ? <p className="px-1 text-xs text-muted-foreground">+{String(interruptions.length - 2)}</p> : null}
          </div>
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
    {flashBar}
  </main>
}
