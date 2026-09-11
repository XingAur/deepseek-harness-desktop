import { useEffect, useState, type ReactNode } from 'react'
import { CheckCircle2, CircleAlert, Cpu, Link2, LoaderCircle, PlugZap, X } from 'lucide-react'
import { Button } from '../components/ui/button.tsx'
import { DesktopFrame } from '../shared/DesktopFrame.tsx'
import { desktopModelDiagnosticsCopy } from '../../model-diagnostics-copy.ts'

const SCHEME = 'dsh-model-diagnostics:'

interface DiagnosticsState {
  readonly provider: string | null
  readonly model: string | null
  readonly baseURL: string | null
  readonly keyPresent: boolean
  readonly keyPreview: string | null
}

interface ProbeResult {
  readonly ok: boolean
  readonly status: number
  readonly models: readonly string[]
  readonly error: string | null
}

function locale(): 'en' | 'zh' {
  return new URLSearchParams(window.location.search).get('locale') === 'zh' ? 'zh' : 'en'
}

function action(name: 'probe' | 'close'): void {
  window.location.assign(`${SCHEME}//${name}`)
}

function select(model: string): void {
  const url = new URL(`${SCHEME}//select`)
  url.searchParams.set('model', model)
  window.location.assign(url.href)
}

/** One label/value row inside the status panel. */
function StateRow({ label, value }: { label: string; value: string }): ReactNode {
  return <div className="flex items-baseline justify-between gap-3">
    <span className="shrink-0 text-xs text-muted-foreground">{label}</span>
    <span className="min-w-0 break-all text-right font-mono text-[11px]">{value}</span>
  </div>
}

export function ModelDiagnosticsApp(): JSX.Element {
  const copy = desktopModelDiagnosticsCopy(locale())
  const [state, setState] = useState<DiagnosticsState | null>(null)
  const [result, setResult] = useState<ProbeResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState<string | null>(null)
  useEffect(() => {
    const report = (event: Event): void => {
      if (!(event instanceof CustomEvent)) return
      setState(event.detail as DiagnosticsState)
    }
    const reportResult = (event: Event): void => {
      if (!(event instanceof CustomEvent)) return
      setResult(event.detail as ProbeResult)
      setBusy(false)
    }
    window.addEventListener('dsh-model-diagnostics-state', report)
    window.addEventListener('dsh-model-diagnostics-result', reportResult)
    return () => {
      window.removeEventListener('dsh-model-diagnostics-state', report)
      window.removeEventListener('dsh-model-diagnostics-result', reportResult)
    }
  }, [])
  return <><DesktopFrame /><main className="dshNativeContent h-screen overflow-hidden bg-background p-5 text-foreground"><section className="mx-auto flex h-full w-full max-w-sm flex-col">
    <header className="flex items-center gap-3">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground"><Cpu aria-hidden className="size-4" /></span>
      <div className="min-w-0">
        <h1 className="text-[15px] font-semibold leading-tight tracking-tight">{copy.title}</h1>
        <p className="truncate text-xs text-muted-foreground">{state?.model ?? '…'}</p>
      </div>
      <Button aria-label={copy.close} className="ml-auto size-8 shrink-0" onClick={() => { action('close') }} size="icon" variant="ghost"><X className="size-4" /></Button>
    </header>

    <div className="mt-4 rounded-xl border border-border/60 bg-muted/40 px-4 py-3">
      {state === null
        ? <StateRow label={copy.providerLabel} value="…" />
        : <>
            <StateRow label={copy.providerLabel} value={state.provider ?? '—'} />
            <div className="mt-1.5 border-t border-border/60 pt-1.5"><StateRow label={copy.modelLabel} value={state.model ?? '—'} /></div>
            <div className="mt-1.5 border-t border-border/60 pt-1.5"><StateRow label={copy.baseURLLabel} value={state.baseURL ?? '—'} /></div>
            <div className="mt-1.5 flex items-baseline justify-between gap-3 border-t border-border/60 pt-1.5">
              <span className="shrink-0 text-xs text-muted-foreground">{copy.apiKey}</span>
              <span className={`min-w-0 truncate font-mono text-[11px] ${state.keyPresent ? '' : 'text-destructive'}`}>
                {state.keyPresent ? `${copy.apiKeyPresent} · ${state.keyPreview ?? ''}` : copy.apiKeyMissing}
              </span>
            </div>
          </>}
    </div>

    {result !== null && !result.ok
      ? <p className="mt-2.5 flex items-start gap-1.5 rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive"><CircleAlert aria-hidden className="mt-0.5 size-3.5 shrink-0" />{copy.testFailed}: {result.error ?? ''}</p>
      : null}
    {result !== null && result.ok
      ? <p aria-live="polite" className="mt-2.5 flex items-center gap-1.5 rounded-lg bg-amber-500/10 px-3 py-2 text-xs font-medium text-amber-600"><CheckCircle2 aria-hidden className="size-3.5" />{copy.testOk}{result.models.length > 0 ? ` · ${String(result.models.length)}` : ''}</p>
      : null}
    {saved !== null
      ? <p aria-live="polite" className="mt-2 flex items-center gap-1.5 text-[11px] text-muted-foreground"><CheckCircle2 aria-hidden className="size-3" />{copy.setDone}: {saved}</p>
      : null}

    <div className="mt-3.5 flex items-center gap-2">
      <Button disabled={busy} onClick={() => { setBusy(true); setResult(null); action('probe') }} size="sm" variant="outline">
        {busy ? <LoaderCircle className="size-3.5 animate-spin" /> : <PlugZap className="size-3.5" />}
        {busy ? copy.test + '…' : result?.ok === true ? copy.testOk : copy.test}
      </Button>
      {result?.ok === true && result.models.length > 0
        ? <span className="flex items-center gap-1 rounded-md border border-border/60 bg-muted/40 px-2 py-1 font-mono text-[11px] text-muted-foreground"><Link2 aria-hidden className="size-3" />{copy.fetchList} · {String(result.models.length)}</span>
        : null}
    </div>

    <section aria-label="models" className="mt-3 flex-1 space-y-1.5 overflow-y-auto">
      {busy && result === null
        ? <div className="flex size-full items-center justify-center"><LoaderCircle aria-label="loading" className="size-6 animate-spin text-muted-foreground/40" /></div>
        : null}
      {result?.ok === true && result.models.length === 0
        ? <p className="pt-4 text-center text-xs text-muted-foreground">{copy.empty}</p>
        : null}
      {result?.models.map(model => <div className="flex items-center gap-2 rounded-lg border border-border/60 bg-muted/40 px-3 py-2" key={model}>
        <span className="min-w-0 flex-1 truncate font-mono text-xs">{model}</span>
        {state?.model === model
          ? <span className="shrink-0 rounded-md border border-border/60 bg-background px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">{copy.current}</span>
          : <Button onClick={() => { select(model); setSaved(model) }} size="sm" type="button" variant="outline">{copy.setDefault}</Button>}
      </div>)}
    </section>

    <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground/80">{copy.note}</p>
  </section></main></>
}
