/** Optional bridge for a local account-menu patch; clean builds keep the official footer entries. */

import { useEffect, useState } from 'react'

export const MENU_ACTION_EVENT = 'dsh-overlay:menu-action'
export const ACCOUNT_MENU_PATCH_EVENT = 'dsh-overlay:account-menu-patch'
export const ACCOUNT_MENU_PATCH_FLAG = '__DSH_ACCOUNT_MENU_PATCH__'

type WindowFlags = Window & {
  __DSH_ACCOUNT_MENU_PATCH__?: boolean
  __DSH_OVERLAY_UPDATE_PENDING__?: boolean
}

function flags(): WindowFlags | undefined {
  return typeof window === 'undefined' ? undefined : (window as WindowFlags)
}

/** True when the local patch has moved the entries into the official account menu. */
export function accountMenuPatched(): boolean {
  return flags()?.__DSH_ACCOUNT_MENU_PATCH__ === true
}

/** Track the patch flag across module-load order and HMR-style remounts. */
export function useAccountMenuPatched(): boolean {
  const [patched, setPatched] = useState(accountMenuPatched)
  useEffect(() => {
    const handler = (): void => { setPatched(accountMenuPatched()) }
    window.addEventListener(ACCOUNT_MENU_PATCH_EVENT, handler)
    return () => { window.removeEventListener(ACCOUNT_MENU_PATCH_EVENT, handler) }
  }, [])
  return patched
}

/** Open this overlay surface when the patched account menu dispatches an action. */
export function useMenuAction(action: 'updates' | 'remote', open: () => void): void {
  useEffect(() => {
    const handler = (event: Event): void => {
      if ((event as CustomEvent<unknown>).detail === action) open()
    }
    window.addEventListener(MENU_ACTION_EVENT, handler)
    return () => { window.removeEventListener(MENU_ACTION_EVENT, handler) }
  }, [action, open])
}

/** Publish the build-update state for the optional account-menu dot. */
export function publishUpdatePending(pending: boolean): void {
  const target = flags()
  if (target === undefined) return
  target.__DSH_OVERLAY_UPDATE_PENDING__ = pending
  window.dispatchEvent(new CustomEvent('dsh-overlay:update-pending', { detail: pending }))
}
