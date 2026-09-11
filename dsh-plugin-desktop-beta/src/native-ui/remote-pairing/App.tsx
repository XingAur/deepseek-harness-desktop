import { useEffect, useState } from 'react'
import { CircleAlert, Copy, Phone, RefreshCw, ShieldAlert, X } from 'lucide-react'
import { Button } from '../components/ui/button.tsx'
import { DesktopFrame } from '../shared/DesktopFrame.tsx'
import { desktopRemotePairingCopy } from '../../remote-pairing-copy.ts'

const SCHEME = 'dsh-remote-pairing:'

interface PairingState {
  readonly state: 'disabled' | 'off' | 'connecting' | 'ready' | 'failed'
  readonly relayOrigin: string | null
  readonly pairUrl: string | null
  readonly qrSvg: string | null
  readonly error: string | null
  readonly connected: boolean
}

function locale(): 'en' | 'zh' {
  return new URLSearchParams(window.location.search).get('locale') === 'zh' ? 'zh' : 'en'
}

function platform(): string {
  const value = new URLSearchParams(window.location.search).get('platform') ?? ''
  return value === 'win32' ? 'win32 · x64' : value === 'darwin' ? 'macOS · arm64' : value
}

function action(name: 'copy' | 'regenerate' | 'enable' | 'close', link?: string): void {
  const url = new URL(`${SCHEME}//${name}`)
  if (link !== undefined) url.searchParams.set('link', link)
  window.location.assign(url.href)
}

export function RemotePairingApp(): JSX.Element {
  const copy = desktopRemotePairingCopy(locale())
  const [state, setState] = useState<PairingState | null>(null)
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    const report = (event: Event): void => {
      if (!(event instanceof CustomEvent)) return
      setState(event.detail as PairingState)
    }
    const copiedEvent = (): void => {
      setCopied(true)
      window.setTimeout(() => { setCopied(false) }, 2_000)
    }
    window.addEventListener('dsh-remote-pairing-state', report)
    window.addEventListener('dsh-remote-pairing-copied', copiedEvent)
    return () => {
      window.removeEventListener('dsh-remote-pairing-state', report)
      window.removeEventListener('dsh-remote-pairing-copied', copiedEvent)
    }
  }, [])
  const status = state?.state ?? 'connecting'
  const connected = status === 'ready' && state?.connected === true
  const statusLabel = status === 'ready'
    ? connected ? copy.connected : copy.waitingPhone
    : status === 'connecting' ? copy.waitingPhone
    : status === 'failed' ? copy.failed
    : status === 'off' ? copy.off
    : copy.disabled
  return <><DesktopFrame /><main className="dshNativeContent h-screen overflow-hidden bg-background p-5 text-foreground"><section className="mx-auto flex h-full w-full max-w-sm flex-col">
    <header className="flex items-center gap-3">
      <span className={`flex size-9 shrink-0 items-center justify-center rounded-lg ${connected ? 'bg-emerald-500/15 text-emerald-500' : 'bg-muted text-muted-foreground'}`}><Phone aria-hidden className="size-4" /></span>
      <div className="min-w-0">
        <h1 className="text-[15px] font-semibold leading-tight tracking-tight">{copy.title}</h1>
        <p className={`truncate text-xs ${connected ? 'text-emerald-500' : 'text-muted-foreground'}`}>{statusLabel}</p>
      </div>
      <Button aria-label={copy.close} className="ml-auto size-8 shrink-0" onClick={() => { action('close') }} size="icon" variant="ghost"><X className="size-4" /></Button>
    </header>

    <div className="mt-4 rounded-xl border border-border/60 bg-muted/40 px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <span className="flex min-w-0 items-center gap-2 text-[13px]">
          <span className={`size-2 shrink-0 rounded-full ${connected ? 'bg-emerald-500' : status === 'ready' || status === 'connecting' ? 'animate-pulse bg-amber-500' : status === 'failed' ? 'bg-destructive' : 'bg-muted-foreground/40'}`} />
          <span className={`truncate ${connected ? 'text-emerald-500' : ''}`}>{statusLabel}</span>
        </span>
        <span className="shrink-0 rounded-md border border-border/60 bg-background px-2 py-0.5 font-mono text-[11px] text-muted-foreground">{platform()}</span>
      </div>
    </div>

    {status === 'failed' && state?.error != null
      ? <p className="mt-2.5 flex items-start gap-1.5 rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive"><CircleAlert aria-hidden className="mt-0.5 size-3.5 shrink-0" />{state.error}</p>
      : null}

    <div className="mt-4 flex flex-1 flex-col items-center justify-center">
      {status === 'off'
        ? <div className="flex flex-col items-center gap-4">
            <div aria-label="pairing off" className="flex size-[232px] items-center justify-center rounded-xl border border-dashed border-border bg-muted/30">
              <Phone aria-hidden className="size-10 text-muted-foreground/40" />
            </div>
            <Button className="h-10 rounded-full px-6" onClick={() => { action('enable') }} size="default"><Phone className="size-4" />{copy.enable}</Button>
          </div>
        : status === 'ready' && state?.qrSvg != null
          ? <div aria-label="pairing qr" className="rounded-xl bg-white p-3 shadow-sm"><div dangerouslySetInnerHTML={{ __html: state.qrSvg }} /></div>
          : <div aria-label="pairing qr pending" className="flex size-[232px] items-center justify-center rounded-xl border border-dashed border-border bg-muted/30">
              <span className="size-8 animate-pulse rounded-lg bg-muted-foreground/20" />
            </div>}
      {status === 'ready' ? <p className="mt-3 max-w-[300px] text-center text-[12px] leading-relaxed text-muted-foreground">{copy.scanTip}</p> : null}
    </div>

    <p className="mt-3 flex items-start gap-1.5 text-[11px] leading-relaxed text-muted-foreground"><ShieldAlert aria-hidden className="mt-0.5 size-3.5 shrink-0" />{copy.securityNote}</p>

    <footer className="mt-3.5 flex items-center justify-between gap-2">
      <span className="flex items-center gap-2">
        <Button disabled={status !== 'ready' && status !== 'failed'} onClick={() => { action('regenerate') }} size="sm" variant="outline"><RefreshCw className="size-3.5" />{copy.regenerate}</Button>
        {status === 'ready' && state?.pairUrl != null
          ? <Button disabled={copied} onClick={() => { action('copy', state.pairUrl ?? undefined) }} size="sm" variant="outline"><Copy className="size-3.5" />{copied ? copy.copied : copy.copyLink}</Button>
          : null}
      </span>
      <span className="font-mono text-[11px] text-muted-foreground/70">{copy.expiresNote}</span>
    </footer>
  </section></main></>
}
