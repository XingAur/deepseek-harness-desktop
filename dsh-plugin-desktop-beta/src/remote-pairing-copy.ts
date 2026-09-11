/** Copy for the remote-control pairing window, by locale. */

import type { DesktopLocale } from './runtime.ts'

/** One locale's complete pairing-window copy. */
export interface RemotePairingCopy {
  readonly title: string
  readonly ready: string
  readonly waitingPhone: string
  readonly connected: string
  readonly statusTitle: string
  readonly connecting: string
  readonly failed: string
  readonly disabled: string
  readonly off: string
  readonly enable: string
  readonly scanTip: string
  readonly securityNote: string
  readonly expiresNote: string
  readonly copyLink: string
  readonly copied: string
  readonly regenerate: string
  readonly close: string
}

const EN: RemotePairingCopy = Object.freeze({
  title: 'Remote Control',
  ready: 'Scan with your phone to open the mobile task page.',
  waitingPhone: 'Waiting for remote connection…',
  connected: 'Remote connected',
  statusTitle: 'Remote relay',
  connecting: 'Connecting to the relay…',
  failed: 'The relay tunnel failed. Check the relay server and the configured origin.',
  disabled: 'Remote control is off. Set dsh-desktop.remoteRelayOrigin in settings.yaml to your relay origin and restart.',
  off: 'Remote control stays off until you enable it.',
  enable: 'Enable remote control',
  scanTip: 'Scan with WeChat, the system camera, or a browser.',
  securityNote: 'Traffic passes through your relay server; its operator can observe it. The link stops working when Desktop restarts.',
  expiresNote: 'One phone page at a time is recommended. Regenerating invalidates the previous link.',
  copyLink: 'Copy link',
  copied: 'Copied',
  regenerate: 'Regenerate',
  close: 'Close',
})

const ZH: RemotePairingCopy = Object.freeze({
  title: '远程控制',
  ready: '用手机扫码,打开手机任务页。',
  waitingPhone: '等待远程连接…',
  connected: '远程已连接',
  statusTitle: '远程中继',
  connecting: '正在连接中继服务器…',
  failed: '中继隧道连接失败。请检查中继服务器与配置的 origin。',
  disabled: '远程控制未开启。在 settings.yaml 中把 dsh-desktop.remoteRelayOrigin 设为中继地址后重启应用。',
  off: '远程控制保持关闭,点击下方按钮开启。',
  enable: '开启远程控制',
  scanTip: '请用微信、系统相机或浏览器等应用扫码打开。',
  securityNote: '流量经过你的中继服务器,服务器管理员可见。应用重启后链接失效。',
  expiresNote: '建议同一时间只打开一个手机页面。重新生成会使旧链接失效。',
  copyLink: '复制链接',
  copied: '已复制',
  regenerate: '重新生成',
  close: '关闭',
})

/** Resolve the pairing-window copy for one locale. */
export function desktopRemotePairingCopy(locale: DesktopLocale): RemotePairingCopy {
  return locale === 'zh' ? ZH : EN
}
