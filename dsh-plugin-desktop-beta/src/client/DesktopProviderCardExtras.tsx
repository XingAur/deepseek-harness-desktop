/** DeepSeek provider-card extension: connection test and live model list. */

import { useCallback, useState, type ReactNode } from 'react'
import { CheckCircle2, CircleAlert, ListChecks, LoaderCircle, PlugZap } from 'lucide-react'
import type { PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type {} from '@deepseek-ai/dsh-client-ui-settings-models/client'
import type { DesktopModelLocaleKey } from './desktop-model-locales.ts'

/** Translator bound by the Desktop registration to the model locale namespace. */
export type DesktopModelTranslate = (key: DesktopModelLocaleKey) => string

/** Renderer-composed props for the provider-card extension. */
export interface DesktopProviderCardExtrasProps extends PropsRuntime<'settings.models.provider-card'> {
  readonly t: DesktopModelTranslate
}

interface ModelProbeView {
  readonly ok: boolean
  readonly status: number
  readonly models: readonly string[]
  readonly error: string | null
}

const PROBE_PATH = '/api/desktop/models/probe'

/** Ask the vendored editor to replace its catalog rows with the fetched ids. */
function dispatchFill(props: DesktopProviderCardExtrasProps, models: readonly string[]): void {
  window.dispatchEvent(new CustomEvent('dsh-desktop-fill-models', {
    detail: {
      provider: props.provider.provider,
      settingsPath: props.provider.settingsPath,
      models,
    },
  }))
}

/**
 * The two provider-card actions requested on the Models page: test the
 * configured connection and pull the live model list. Fetched ids are filled
 * straight into the card's native model catalog (never rendered here, so no
 * second list ever appears).
 */
export function DesktopProviderCardExtras(props: DesktopProviderCardExtrasProps): ReactNode {
  const { t } = props
  const [probe, setProbe] = useState<ModelProbeView | undefined>()
  const [filled, setFilled] = useState<number | undefined>()
  const [busy, setBusy] = useState<'probe' | 'fetch' | undefined>()

  const runProbe = useCallback(async (mode: 'probe' | 'fetch'): Promise<void> => {
    if (busy !== undefined) return
    setBusy(mode)
    setProbe(undefined)
    setFilled(undefined)
    try {
      const response = await fetch(PROBE_PATH, {
        method: 'POST',
        credentials: 'same-origin',
        redirect: 'error',
        headers: { accept: 'application/json', 'content-type': 'application/json' },
        body: JSON.stringify({ settingsNs: props.provider.settingsNs, provider: props.provider.provider }),
      })
      const payload: unknown = await response.json().catch(() => undefined)
      if (payload !== null && typeof payload === 'object' && 'ok' in payload) {
        const view = payload as ModelProbeView
        setProbe(view)
        if (mode === 'fetch' && view.ok && view.models.length > 0) {
          dispatchFill(props, view.models)
          setFilled(view.models.length)
        }
        return
      }
      throw new Error(`HTTP ${String(response.status)}`)
    } catch (cause) {
      setProbe({ ok: false, status: 0, models: [], error: cause instanceof Error ? cause.message : String(cause) })
    } finally {
      setBusy(undefined)
    }
  }, [busy, props.provider.provider, props.provider.settingsNs, props.provider.settingsPath])

  return <div className="dshDesktopProviderExtras">
    <div className="dshDesktopProviderExtrasActions">
      <button
        type="button"
        className="dshDesktopProviderExtrasButton"
        disabled={busy !== undefined}
        onClick={() => { void runProbe('probe') }}
      >
        {busy === 'probe' ? <LoaderCircle aria-hidden className="size-3.5 animate-spin" /> : <PlugZap aria-hidden className="size-3.5" />}
        {busy === 'probe' ? t('testing') : probe?.ok === true ? t('testOk') : t('test')}
      </button>
      <button
        type="button"
        className="dshDesktopProviderExtrasButton"
        disabled={busy !== undefined}
        onClick={() => { void runProbe('fetch') }}
      >
        {busy === 'fetch' ? <LoaderCircle aria-hidden className="size-3.5 animate-spin" /> : <ListChecks aria-hidden className="size-3.5" />}
        {t('fetchList')}
      </button>
      {filled !== undefined
        ? <span className="dshDesktopProviderExtrasMeta"><CheckCircle2 aria-hidden className="size-3.5" />{`${t('filled')} · ${String(filled)}`}</span>
        : null}
    </div>
    {probe !== undefined && !probe.ok
      ? <p className="dshDesktopProviderExtrasError" role="alert">
          <CircleAlert aria-hidden className="size-3.5" />{t('testFailed')}: {probe.error ?? ''}
        </p>
      : null}
  </div>
}
