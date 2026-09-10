/** Caption-bar chip showing the running version and any advertised update. */

import { useCallback, useEffect, useRef, useState } from 'react'
import { createDesktopSettingsApi, type DesktopUpdateStateView } from './desktop-settings-api.ts'
import { desktopUpdateText } from './desktop-update-locales.ts'

/** Poll cadence for the lightweight status read. */
const STATE_POLL_MS = 5 * 60 * 1000

/** Props for the caption update chip. */
export interface DesktopUpdateChipProps {
  /** Installed product version shown as the chip label. */
  readonly version: string
}

/** Round chip in the desktop-owned caption strip; expands into the update panel. */
export function DesktopUpdateChip({ version }: DesktopUpdateChipProps) {
  const t = desktopUpdateText()
  const [open, setOpen] = useState(false)
  const [state, setState] = useState<State>()
  const [busy, setBusy] = useState<'check' | 'download'>()
  const [notice, setNotice] = useState<string>()
  const rootRef = useRef<HTMLDivElement>(null)

  const refresh = useCallback(async () => {
    try {
      setState({ kind: 'ready', value: await createDesktopSettingsApi().getUpdateState() })
    } catch {
      setState({ kind: 'unavailable' })
    }
  }, [])

  useEffect(() => {
    void refresh()
    const timer = setInterval(() => { void refresh() }, STATE_POLL_MS)
    return () => { clearInterval(timer) }
  }, [refresh])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent): void => {
      if (rootRef.current !== null && !rootRef.current.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => { document.removeEventListener('pointerdown', onPointerDown) }
  }, [open])

  const value = state?.kind === 'ready' ? state.value : undefined
  const updateReady = value?.availableVersion !== undefined && value.availableVersion !== null
  const downloading = value?.downloadingVersion !== undefined && value.downloadingVersion !== null

  const checkNow = async (): Promise<void> => {
    setBusy('check')
    setNotice(undefined)
    try {
      await createDesktopSettingsApi().checkForUpdates()
    } catch {
      setNotice(t.failed)
    }
    await refresh()
    setBusy(undefined)
  }

  const download = async (target: string): Promise<void> => {
    setBusy('download')
    setNotice(undefined)
    try {
      const { accepted } = await createDesktopSettingsApi().downloadUpdate(target)
      if (!accepted) setNotice(t.downloadDeclined)
    } catch {
      setNotice(t.downloadDeclined)
    }
    await refresh()
    setBusy(undefined)
  }

  return (
    <div className="dshDesktopUpdateChipRoot" ref={rootRef}>
      <button
        type="button"
        className="dshDesktopUpdateChip"
        data-update={updateReady || downloading ? 'on' : 'off'}
        aria-expanded={open}
        title={`${t.current} ${version}`}
        onClick={() => { setOpen(current => !current); void refresh() }}
      >
        <span className="dshDesktopUpdateChipVersion">{`v${version}`}</span>
        {(updateReady || downloading) && <span className="dshDesktopUpdateChipBadge" aria-hidden="true" />}
      </button>
      {open && (
        <div className="dshDesktopUpdatePanel" role="dialog" aria-label={t.checkForUpdates}>
          <p className="dshDesktopUpdatePanelHead">
            {`${t.current} v${version}`}
            {value?.channel === 'beta' ? ` · ${t.channelBeta}` : ''}
          </p>
          {downloading && value?.downloadingVersion != null && (
            <p className="dshDesktopUpdatePanelRow" data-tone="busy">{`${t.downloading} v${value.downloadingVersion}…`}</p>
          )}
          {!downloading && updateReady && value?.availableVersion != null && (
            <p className="dshDesktopUpdatePanelRow" data-tone="available">
              {`${t.available}: v${value.availableVersion}`}
            </p>
          )}
          {!downloading && !updateReady && value !== undefined && (
            <p className="dshDesktopUpdatePanelRow" data-tone="idle">{t.upToDate}</p>
          )}
          {state?.kind === 'unavailable' && <p className="dshDesktopUpdatePanelRow" data-tone="idle">{t.failed}</p>}
          {notice !== undefined && <p className="dshDesktopUpdatePanelRow" data-tone="idle">{notice}</p>}
          <div className="dshDesktopUpdatePanelActions">
            <button
              type="button"
              className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
              disabled={busy !== undefined}
              onClick={() => { void checkNow() }}
            >
              {busy === 'check' ? t.checking : t.checkForUpdates}
            </button>
            {updateReady && !downloading && value?.availableVersion != null && (
              <button
                type="button"
                className="dshDesktopSettingsPrimary"
                disabled={busy !== undefined || value.canDownload !== true}
                onClick={() => { void download(value.availableVersion as string) }}
              >
                {busy === 'download' ? t.downloading : t.download}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

type State = { readonly kind: 'ready'; readonly value: DesktopUpdateStateView } | { readonly kind: 'unavailable' }
