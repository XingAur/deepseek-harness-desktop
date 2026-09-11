/** Locale dictionary for the sidebar remote-control entry. */

export type DesktopRemoteLocaleKey =
  | 'label'
  | 'waiting'
  | 'connected'
  | 'openFailed'

export const DESKTOP_REMOTE_LOCALE_NAMESPACE = 'desktop.remote'

export const en: Record<DesktopRemoteLocaleKey, string> = {
  label: 'Mobile',
  waiting: 'Waiting for remote connection…',
  connected: 'Remote connected',
  openFailed: 'Could not open the pairing window.',
}

export const zh: Record<DesktopRemoteLocaleKey, string> = {
  label: '手机连接',
  waiting: '等待远程连接…',
  connected: '远程已连接',
  openFailed: '配对窗口打开失败。',
}
