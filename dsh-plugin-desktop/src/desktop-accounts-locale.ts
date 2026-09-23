/** Desktop-owned model-account-management copy for the locales shipped by DSH. */

import type { DesktopLocale } from './runtime.ts'

export interface DesktopModelAccountCopy {
  /** Tray submenu label opening the per-provider account entries. */
  readonly trayLabel: string
  /** Raised when the active Profile mounted no credential capability. */
  readonly unavailableTitle: string
  readonly unavailableMessage: string
  /** Tray command starting one OAuth sign-in that joins the library. */
  readonly addAccount: string
  /** Tray command removing one saved account. */
  readonly removeAccount: (label: string) => string
  /** Tray command deleting the active credential. */
  readonly logoutAccount: string
  /** Appended to a provider label while its OAuth attempt runs. */
  readonly inFlightSuffix: string
  /** Raised after a saved account joined the library and the previous stayed active. */
  readonly addedKeptActiveMessage: string
  /** Raised after the library's first account, which is also the active one. */
  readonly addedActiveMessage: string
  /** Raised after switching the active credential. */
  readonly switchedMessage: (label: string) => string
  /** Raised after deleting the active credential. */
  readonly loggedOutMessage: string
  /** Raised after deleting one saved account. */
  readonly removedMessage: (label: string) => string
  /** Buttons of the restart recommendation dialog. */
  readonly restartNow: string
  readonly restartLater: string
  /** Raised when another attempt already owns this provider. */
  readonly inFlightMessage: string
  /** Raised when any account operation failed for any other reason. */
  readonly failedMessage: (detail: string) => string
  readonly dismiss: string
  /** Label fallback for an active grant that carries no account id. */
  readonly activeFallback: string
  /** Label fallback for a saved slot that carries no account id. */
  readonly savedFallback: (n: number) => string
  /** Tray header row while the provider has no active credential. */
  readonly notSignedIn: string
}

const copy: Record<DesktopLocale, DesktopModelAccountCopy> = {
  en: {
    trayLabel: 'Model Accounts',
    unavailableTitle: 'Model Accounts',
    unavailableMessage: 'The active Profile mounted no model credential capability, so account management is unavailable.',
    addAccount: 'Add Account…',
    removeAccount: label => `Remove "${label}"`,
    logoutAccount: 'Sign Out Active Account',
    inFlightSuffix: '…',
    addedKeptActiveMessage: 'The new account was saved. The previous account stays active.',
    addedActiveMessage: 'The account was added and is now active.',
    switchedMessage: label => `Switched to "${label}". Restart to apply it everywhere.`,
    loggedOutMessage: 'Signed out. Restart to apply it everywhere.',
    removedMessage: label => `Removed account "${label}".`,
    restartNow: 'Restart Now',
    restartLater: 'Later',
    inFlightMessage: 'A sign-in for this provider is already running. Finish or cancel it first.',
    failedMessage: detail => `Account operation failed: ${detail}`,
    dismiss: 'OK',
    activeFallback: 'Active account',
    savedFallback: n => `Account ${String(n)}`,
    notSignedIn: 'Not signed in',
  },
  zh: {
    trayLabel: '模型账号管理',
    unavailableTitle: '模型账号管理',
    unavailableMessage: '当前 Profile 未挂载模型凭据能力，暂时无法管理模型账号。',
    addAccount: '添加账号…',
    removeAccount: label => `移除「${label}」`,
    logoutAccount: '退出当前账号',
    inFlightSuffix: '…',
    addedKeptActiveMessage: '新账号已保存，原账号保持生效。',
    addedActiveMessage: '账号已添加并生效。',
    switchedMessage: label => `已切换到「${label}」。建议重启后完全生效。`,
    loggedOutMessage: '已退出当前账号。建议重启后完全生效。',
    removedMessage: label => `已移除账号「${label}」。`,
    restartNow: '立即重启',
    restartLater: '稍后',
    inFlightMessage: '该账号已有登录流程在进行中，请先完成或取消它。',
    failedMessage: detail => `账号操作失败：${detail}`,
    dismiss: '知道了',
    activeFallback: '当前账号',
    savedFallback: n => `账号 ${String(n)}`,
    notSignedIn: '未登录',
  },
}

/**
 * Resolve the model-account copy for one active locale.
 * @param locale - the locale the desktop shell currently presents.
 * @returns the copy dictionary for that locale.
 */
export function desktopModelAccountCopy(locale: DesktopLocale): DesktopModelAccountCopy {
  return copy[locale]
}
