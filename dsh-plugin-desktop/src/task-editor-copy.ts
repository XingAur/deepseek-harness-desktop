/** Localized copy for the native task editor window. */

import type { DesktopLocale } from './runtime.ts'

/** Every renderer-facing string of the task editor. */
export interface TaskEditorCopy {
  readonly title: string
  readonly newTask: string
  readonly editTask: string
  readonly name: string
  readonly namePlaceholder: string
  readonly prompt: string
  readonly promptPlaceholder: string
  readonly scheduleModes: readonly [string, string, string]
  readonly templates: readonly {
    readonly id: string
    readonly label: string
    readonly rule: { kind: 'every'; seconds: number } | { kind: 'cron'; expression: string }
  }[]
  readonly nlPlaceholder: string
  readonly nlButton: string
  readonly nlBusy: string
  readonly nlUnavailable: string
  readonly cronPlaceholder: string
  readonly cronInvalid: string
  readonly previewTitle: string
  readonly previewEmpty: string
  readonly sessionPolicy: string
  readonly sessionFresh: string
  readonly sessionContinue: string
  readonly workspace: string
  readonly workspaceBrowse: string
  readonly permissionPreset: string
  readonly presetNone: string
  readonly notify: string
  readonly save: string
  readonly cancel: string
  readonly delete: string
  readonly runNow: string
  readonly pause: string
  readonly resume: string
  readonly duplicate: string
  readonly lastRuns: string
  readonly statusRunning: string
  readonly statusCompleted: string
  readonly statusFailed: string
  readonly statusSkipped: string
  readonly statusCancelled: string
  readonly paused: string
  readonly nextRun: string
  readonly emptyTitle: string
  readonly emptyTemplates: string
  readonly emptySentence: string
  readonly emptyManual: string
  readonly saved: string
  readonly saveFailed: string
  readonly runQueued: string
  readonly close: string
  readonly everyMinutes: (n: number) => string
  readonly everyHours: (n: number) => string
  readonly everyDays: (n: number) => string
}

const ZH: TaskEditorCopy = {
  title: '定时任务',
  newTask: '新建任务',
  editTask: '编辑任务',
  name: '任务名称',
  namePlaceholder: '例如：每日工作区日报',
  prompt: '任务指令',
  promptPlaceholder: '告诉助手到达触发时间后要做什么',
  scheduleModes: ['模板', '说一句', 'cron 表达式'],
  templates: [
    { id: 'hourly', label: '每小时', rule: { kind: 'cron', expression: '0 * * * *' } },
    { id: 'daily-9', label: '每天 09:00', rule: { kind: 'cron', expression: '0 9 * * *' } },
    { id: 'weekdays-18', label: '工作日 18:00', rule: { kind: 'cron', expression: '0 18 * * 1-5' } },
    { id: 'weekly-mon', label: '每周一 09:00', rule: { kind: 'cron', expression: '0 9 * * 1' } },
    { id: 'minutes-30', label: '每 30 分钟', rule: { kind: 'every', seconds: 1_800 } },
    { id: 'daily-23', label: '每天 23:00', rule: { kind: 'cron', expression: '0 23 * * *' } },
  ],
  nlPlaceholder: '例如：每个工作日早上九点，或每天晚上十一点',
  nlButton: '解释',
  nlBusy: '正在理解…',
  nlUnavailable: '暂时无法理解这句话，请改用模板或直接填写 cron 表达式。',
  cronPlaceholder: '0 9 * * *',
  cronInvalid: 'cron 表达式无效',
  previewTitle: '接下来 3 次触发',
  previewEmpty: '填写有效计划后显示',
  sessionPolicy: '会话方式',
  sessionFresh: '每次新会话',
  sessionContinue: '延续同一会话',
  workspace: '工作目录（可选）',
  workspaceBrowse: '选择…',
  permissionPreset: '权限预设',
  presetNone: '跟随默认',
  notify: '完成后系统通知',
  save: '保存',
  cancel: '取消',
  delete: '删除',
  runNow: '立即运行',
  pause: '暂停',
  resume: '启用',
  duplicate: '复制',
  lastRuns: '最近运行',
  statusRunning: '运行中',
  statusCompleted: '已完成',
  statusFailed: '失败',
  statusSkipped: '跳过（上次未结束）',
  statusCancelled: '已取消',
  paused: '已暂停',
  nextRun: '下次触发',
  emptyTitle: '还没有定时任务',
  emptyTemplates: '从模板开始',
  emptySentence: '说一句自动生成',
  emptyManual: '手动填写 cron',
  saved: '已保存',
  saveFailed: '保存失败',
  runQueued: '已加入运行队列',
  close: '关闭',
  everyMinutes: n => `每 ${String(n)} 分钟`,
  everyHours: n => `每 ${String(n)} 小时`,
  everyDays: n => `每 ${String(n)} 天`,
}

const EN: TaskEditorCopy = {
  title: 'Scheduled Tasks',
  newTask: 'New Task',
  editTask: 'Edit Task',
  name: 'Name',
  namePlaceholder: 'e.g. Daily workspace report',
  prompt: 'Instructions',
  promptPlaceholder: 'What the assistant should do at each trigger',
  scheduleModes: ['Templates', 'Describe it', 'cron expression'],
  templates: [
    { id: 'hourly', label: 'Hourly', rule: { kind: 'cron', expression: '0 * * * *' } },
    { id: 'daily-9', label: 'Daily 09:00', rule: { kind: 'cron', expression: '0 9 * * *' } },
    { id: 'weekdays-18', label: 'Weekdays 18:00', rule: { kind: 'cron', expression: '0 18 * * 1-5' } },
    { id: 'weekly-mon', label: 'Mondays 09:00', rule: { kind: 'cron', expression: '0 9 * * 1' } },
    { id: 'minutes-30', label: 'Every 30 minutes', rule: { kind: 'every', seconds: 1_800 } },
    { id: 'daily-23', label: 'Daily 23:00', rule: { kind: 'cron', expression: '0 23 * * *' } },
  ],
  nlPlaceholder: 'e.g. every weekday morning at nine, or nightly at eleven',
  nlButton: 'Interpret',
  nlBusy: 'Interpreting…',
  nlUnavailable: 'Could not interpret that sentence; use a template or enter a cron expression.',
  cronPlaceholder: '0 9 * * *',
  cronInvalid: 'Invalid cron expression',
  previewTitle: 'Next 3 triggers',
  previewEmpty: 'Shown once the schedule is valid',
  sessionPolicy: 'Sessions',
  sessionFresh: 'Fresh each run',
  sessionContinue: 'Continue one thread',
  workspace: 'Workspace (optional)',
  workspaceBrowse: 'Browse…',
  permissionPreset: 'Permission preset',
  presetNone: 'Follow default',
  notify: 'Notify when finished',
  save: 'Save',
  cancel: 'Cancel',
  delete: 'Delete',
  runNow: 'Run now',
  pause: 'Pause',
  resume: 'Enable',
  duplicate: 'Duplicate',
  lastRuns: 'Recent runs',
  statusRunning: 'Running',
  statusCompleted: 'Completed',
  statusFailed: 'Failed',
  statusSkipped: 'Skipped (previous still active)',
  statusCancelled: 'Cancelled',
  paused: 'Paused',
  nextRun: 'Next trigger',
  emptyTitle: 'No scheduled tasks yet',
  emptyTemplates: 'Start from a template',
  emptySentence: 'Describe it in one sentence',
  emptyManual: 'Enter a cron expression',
  saved: 'Saved',
  saveFailed: 'Save failed',
  runQueued: 'Queued for immediate run',
  close: 'Close',
  everyMinutes: n => `Every ${String(n)} minutes`,
  everyHours: n => `Every ${String(n)} hours`,
  everyDays: n => `Every ${String(n)} days`,
}

/** Localized task-editor copy for one UI locale. */
export function taskEditorCopy(locale: DesktopLocale): TaskEditorCopy {
  return locale === 'zh' ? ZH : EN
}
