/** Desktop-owned native sign-in copy for the locales shipped by DSH. */

import type { DesktopLocale } from './runtime.ts'

export interface DesktopModelSigninCopy {
  /** Tray submenu label opening the provider sign-in entries. */
  readonly trayLabel: string
  /** Raised when the active Profile mounted no authorization capability. */
  readonly unavailableTitle: string
  readonly unavailableMessage: string
  /** Raised when a listed flow offers no runnable method. */
  readonly noMethodMessage: string
  /** Device-code / verification dialog shown while the attempt runs. */
  readonly deviceMessage: string
  readonly deviceCodeDetail: (code: string) => string
  readonly deviceAdvisory: string
  /** Shown while a browser callback is racing a typed-code prompt this shell cannot answer. */
  readonly browserWaitMessage: string
  readonly dismiss: string
  readonly cancelLogin: string
  /** Raised after the attempt committed the credential. */
  readonly successMessage: string
  /** Raised when another attempt already owns this provider. */
  readonly inFlightMessage: string
  /** Raised when an attempt failed for any other reason. */
  readonly failedMessage: (detail: string) => string
  /** Raised before declining a prompt the native dialogs cannot answer. */
  readonly promptUnsupportedMessage: string
}

const copy: Record<DesktopLocale, DesktopModelSigninCopy> = {
  en: {
    trayLabel: 'Model Account Sign-in',
    unavailableTitle: 'Model Account Sign-in',
    unavailableMessage: 'The active Profile mounted no model authorization capability, so provider sign-in is unavailable.',
    noMethodMessage: 'This provider offers no runnable sign-in method.',
    deviceMessage: 'Your browser opened the sign-in page. Sign in and approve the authorization there; the confirmation code is filled in for you.',
    deviceCodeDetail: code => `Confirmation code: ${code}`,
    deviceAdvisory: 'Closing this window also cancels the pending sign-in.',
    browserWaitMessage: 'Your browser opened the sign-in page. Finish signing in there; this window continues automatically.',
    dismiss: 'OK',
    cancelLogin: 'Cancel Sign-in',
    successMessage: 'The credential was saved. Later model requests use it automatically.',
    inFlightMessage: 'A sign-in for this provider is already running. Finish or cancel it first.',
    failedMessage: detail => `Sign-in failed: ${detail}`,
    promptUnsupportedMessage: 'This sign-in method needs extra text input, which this version does not support yet. Use the browser sign-in method instead.',
  },
  zh: {
    trayLabel: '模型账号登录',
    unavailableTitle: '模型账号登录',
    unavailableMessage: '当前 Profile 未挂载模型授权能力，暂时无法登录模型账号。',
    noMethodMessage: '该账号没有可用的登录方式。',
    deviceMessage: '浏览器已打开登录页面。请在页面中登录并批准授权；确认码会自动填入，如未填入请手动输入。',
    deviceCodeDetail: code => `确认码：${code}`,
    deviceAdvisory: '关闭此窗口也会取消进行中的登录。',
    browserWaitMessage: '浏览器已打开登录页面。请在页面中完成登录，完成后会自动继续。',
    dismiss: '知道了',
    cancelLogin: '取消登录',
    successMessage: '登录凭据已保存，之后的模型请求会自动使用。',
    inFlightMessage: '该账号已有登录流程在进行中，请先完成或取消它。',
    failedMessage: detail => `登录失败：${detail}`,
    promptUnsupportedMessage: '此登录方式需要额外的文本输入，当前版本暂不支持。请改用浏览器登录方式。',
  },
}

/**
 * Resolve the sign-in copy for one active locale.
 * @param locale - the locale the desktop shell currently presents.
 * @returns the copy dictionary for that locale.
 */
export function desktopModelSigninCopy(locale: DesktopLocale): DesktopModelSigninCopy {
  return copy[locale]
}
