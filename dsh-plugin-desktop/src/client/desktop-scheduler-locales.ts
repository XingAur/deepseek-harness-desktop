/** Copy for the Desktop scheduler settings page and the in-app task modal, keyed by {@link DesktopSchedulerLocaleKey}. */

/** Locale keys owned by the Desktop scheduler surfaces. */
export type DesktopSchedulerLocaleKey =
  | 'nav'
  | 'title'
  | 'description'
  | 'loading'
  | 'unavailable'
  | 'retry'
  | 'empty'
  | 'nextRun'
  | 'paused'
  | 'failing'
  | 'manage'
  | 'refresh'
  | 'everyPrefix'
  | 'minutesUnit'
  | 'hoursUnit'
  | 'daysUnit'
  | 'cronLabel'
  | 'close'
  | 'newTask'
  | 'editTask'
  | 'nameLabel'
  | 'namePlaceholder'
  | 'promptLabel'
  | 'promptPlaceholder'
  | 'scheduleLabel'
  | 'intervalOption'
  | 'cronOption'
  | 'intervalMinutes'
  | 'cronExpression'
  | 'sessionPolicyLabel'
  | 'policyFresh'
  | 'policyContinue'
  | 'presetLabel'
  | 'presetDefault'
  | 'notifyLabel'
  | 'enabledLabel'
  | 'previewButton'
  | 'previewTitle'
  | 'noPreview'
  | 'save'
  | 'saving'
  | 'cancel'
  | 'editAction'
  | 'deleteAction'
  | 'confirmDelete'
  | 'runNow'
  | 'triggered'
  | 'enableAction'
  | 'disableAction'
  | 'savedDone'
  | 'deletedDone'
  | 'actionFailed'

/** Chinese copy. */
export const zh: Record<DesktopSchedulerLocaleKey, string> = {
  nav: '定时任务',
  title: '定时任务',
  description: '到达触发时间后，DSH 会在应用内自动开启会话并执行任务指令。',
  loading: '正在读取任务…',
  unavailable: '无法读取定时任务，请重试。',
  retry: '重试',
  empty: '还没有定时任务，点击下方按钮创建。',
  nextRun: '下次触发',
  paused: '已暂停',
  failing: '连续失败',
  manage: '管理任务',
  refresh: '刷新',
  everyPrefix: '每',
  minutesUnit: '分钟',
  hoursUnit: '小时',
  daysUnit: '天',
  cronLabel: '计划',
  close: '关闭',
  newTask: '新建任务',
  editTask: '编辑任务',
  nameLabel: '任务名称',
  namePlaceholder: '例如：每日站会提醒',
  promptLabel: '任务指令',
  promptPlaceholder: '到达触发时间后要执行的内容',
  scheduleLabel: '触发规则',
  intervalOption: '固定间隔',
  cronOption: 'Cron 表达式',
  intervalMinutes: '间隔（分钟）',
  cronExpression: 'Cron 表达式',
  sessionPolicyLabel: '会话方式',
  policyFresh: '每次开启新会话',
  policyContinue: '延续同一个会话',
  presetLabel: '权限预设',
  presetDefault: '跟随默认',
  notifyLabel: '完成后发送系统通知',
  enabledLabel: '启用任务',
  previewButton: '预览下次触发',
  previewTitle: '接下来触发',
  noPreview: '无法计算触发时间',
  save: '保存',
  saving: '保存中…',
  cancel: '取消',
  editAction: '编辑',
  deleteAction: '删除',
  confirmDelete: '确认删除',
  runNow: '立即运行',
  triggered: '已触发运行',
  enableAction: '启用',
  disableAction: '停用',
  savedDone: '已保存。',
  deletedDone: '已删除。',
  actionFailed: '操作失败，请重试。',
}

/** English copy. */
export const en: Record<DesktopSchedulerLocaleKey, string> = {
  nav: 'Scheduled Tasks',
  title: 'Scheduled Tasks',
  description: 'At each trigger, DSH starts an in-app session and runs the task instructions automatically.',
  loading: 'Loading tasks…',
  unavailable: 'Could not load scheduled tasks. Please retry.',
  retry: 'Retry',
  empty: 'No scheduled tasks yet. Use the button below to create one.',
  nextRun: 'Next trigger',
  paused: 'Paused',
  failing: 'failing repeatedly',
  manage: 'Manage tasks',
  refresh: 'Refresh',
  everyPrefix: 'Every',
  minutesUnit: 'minutes',
  hoursUnit: 'hours',
  daysUnit: 'days',
  cronLabel: 'schedule',
  close: 'Close',
  newTask: 'New task',
  editTask: 'Edit task',
  nameLabel: 'Task name',
  namePlaceholder: 'e.g. Daily standup reminder',
  promptLabel: 'Task prompt',
  promptPlaceholder: 'What DSH should run at each trigger',
  scheduleLabel: 'Schedule',
  intervalOption: 'Fixed interval',
  cronOption: 'Cron expression',
  intervalMinutes: 'Interval (minutes)',
  cronExpression: 'Cron expression',
  sessionPolicyLabel: 'Session mode',
  policyFresh: 'Fresh session each run',
  policyContinue: 'Continue one session',
  presetLabel: 'Permission preset',
  presetDefault: 'Profile default',
  notifyLabel: 'System notification on completion',
  enabledLabel: 'Task enabled',
  previewButton: 'Preview next runs',
  previewTitle: 'Upcoming runs',
  noPreview: 'Cannot compute upcoming runs',
  save: 'Save',
  saving: 'Saving…',
  cancel: 'Cancel',
  editAction: 'Edit',
  deleteAction: 'Delete',
  confirmDelete: 'Confirm delete',
  runNow: 'Run now',
  triggered: 'Run triggered',
  enableAction: 'Enable',
  disableAction: 'Disable',
  savedDone: 'Saved.',
  deletedDone: 'Deleted.',
  actionFailed: 'The operation failed. Please retry.',
}
