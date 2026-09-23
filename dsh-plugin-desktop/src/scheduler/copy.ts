/** Localized scheduler copy: submitted prompt headers and native notifications. */

import type { DesktopLocale, DesktopNotification } from '../runtime.ts'

/** Localized rendering of one run's submitted prompt content. */
export function scheduledPromptContent(
  locale: DesktopLocale,
  taskName: string,
  localTime: string,
  prompt: string,
): string {
  if (locale === 'zh') {
    return `【定时任务】${taskName}（${localTime} 自动触发）\n请执行以下任务指令：\n\n${prompt}`
  }
  return `[Scheduled task] ${taskName} (auto-triggered at ${localTime})\nRun the task instructions below:\n\n${prompt}`
}

/** Terminal run outcomes the desktop notifies about. */
export type SchedulerNoticeKind =
  | 'task-completed'
  | 'task-failed'
  | 'task-paused'
  | 'task-skipped'

/** Localized native notification copy for one outcome kind. */
export function schedulerNotice(
  locale: DesktopLocale,
  kind: SchedulerNoticeKind,
  taskName: string,
): DesktopNotification {
  if (locale === 'zh') {
    const copy: Record<SchedulerNoticeKind, DesktopNotification> = {
      'task-completed': { title: '定时任务已完成', body: `“${taskName}”已完成本次运行。` },
      'task-failed': { title: '定时任务失败', body: `“${taskName}”本次运行失败，请打开 DSH Desktop 查看详情。` },
      'task-paused': { title: '定时任务已暂停', body: `“${taskName}”连续失败已被自动暂停，请打开 DSH Desktop 重新启用。` },
      'task-skipped': { title: '定时任务已跳过', body: `“${taskName}”的上一次运行尚未结束，本次触发已跳过。` },
    }
    return copy[kind]
  }
  const copy: Record<SchedulerNoticeKind, DesktopNotification> = {
    'task-completed': { title: 'Scheduled Task Completed', body: `"${taskName}" has finished this run.` },
    'task-failed': { title: 'Scheduled Task Failed', body: `"${taskName}" failed this run. Open DSH Desktop for details.` },
    'task-paused': { title: 'Scheduled Task Paused', body: `"${taskName}" was paused after repeated failures. Open DSH Desktop to re-enable it.` },
    'task-skipped': { title: 'Scheduled Task Skipped', body: `"${taskName}" was skipped because the previous run is still active.` },
  }
  return copy[kind]
}

/** Localized tray label opening the task editor. */
export function schedulerTrayLabel(locale: DesktopLocale): string {
  return locale === 'zh' ? '定时任务…' : 'Scheduled Tasks…'
}
