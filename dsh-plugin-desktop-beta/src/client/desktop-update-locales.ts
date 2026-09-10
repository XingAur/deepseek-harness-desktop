/** Bilingual copy for the caption-bar update chip. */

/** Copy vocabulary for one renderer language. */
export interface DesktopUpdateCopy {
  readonly current: string
  readonly channelBeta: string
  readonly checkForUpdates: string
  readonly checking: string
  readonly upToDate: string
  readonly available: string
  readonly download: string
  readonly downloading: string
  readonly downloadDeclined: string
  readonly openSettings: string
  readonly failed: string
}

const zh: DesktopUpdateCopy = {
  current: '当前版本',
  channelBeta: '测试渠道',
  checkForUpdates: '检查更新',
  checking: '检查中…',
  upToDate: '已是最新版本',
  available: '发现新版本',
  download: '下载新版本',
  downloading: '正在下载',
  downloadDeclined: '暂时无法开始下载',
  openSettings: '打开设置查看详情',
  failed: '暂时无法获取更新状态',
}

const en: DesktopUpdateCopy = {
  current: 'Current version',
  channelBeta: 'beta channel',
  checkForUpdates: 'Check for updates',
  checking: 'Checking…',
  upToDate: 'Up to date',
  available: 'Update available',
  download: 'Download update',
  downloading: 'Downloading',
  downloadDeclined: 'The download could not start',
  openSettings: 'Open settings for details',
  failed: 'Update status is unavailable right now',
}

/** Resolve copy from the browser languages, preferring Chinese. */
export function desktopUpdateText(
  languages: readonly string[] = typeof navigator === 'undefined' ? ['en'] : navigator.languages,
): DesktopUpdateCopy {
  return languages.some(language => language.toLowerCase().startsWith('zh')) ? zh : en
}
