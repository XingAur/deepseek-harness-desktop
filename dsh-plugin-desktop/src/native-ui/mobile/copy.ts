/** Inline locale dictionary; the mobile bundle stays self-contained. */

export interface MobileCopy {
  readonly newSession: string
  readonly promptPlaceholder: string
  readonly messagePlaceholder: string
  readonly queuePlaceholder: string
  readonly send: string
  readonly defaultWorkspace: string
  readonly running: string
  readonly idle: string
  readonly thinking: string
  readonly empty: string
  readonly emptyTranscript: string
  readonly cancel: string
  readonly expired: string
  readonly error: string
  readonly retry: string
  readonly toggleTheme: string
  readonly back: string
  readonly newTaskTitle: string
  readonly sessionMenu: string
  readonly rename: string
  readonly renameTitle: string
  readonly renameSave: string
  readonly compact: string
  readonly compactDone: string
  readonly reasoning: string
  readonly reasoningDone: string
  readonly toolRunning: string
  readonly taskPlan: string
  readonly edited: string
  readonly added: string
  readonly removed: string
  readonly linesUnit: string
  readonly permission: string
  readonly permissionTitle: string
  readonly model: string
  readonly modelTitle: string
  readonly thinkingLevel: string
  readonly apply: string
  readonly contextTitle: string
  readonly contextUsed: string
  readonly contextWindowLabel: string
  readonly contextTokens: string
  readonly inputTokens: string
  readonly outputTokens: string
  readonly cacheRead: string
  readonly cacheWrite: string
  readonly queuedPrompt: string
  readonly removeQueue: string
  readonly interruptionApproval: string
  readonly interruptionQuestion: string
  readonly interruptionDelegated: string
  readonly allow: string
  readonly reject: string
  readonly answerOnDesktop: string
  readonly submitAnswer: string
  readonly otherAnswer: string
  readonly planReview: string
  readonly approvePlan: string
  readonly decisionRecorded: string
  readonly decisionConflict: string
  readonly actionFailed: string
  readonly catalogEmpty: string
  readonly selectModelFirst: string
  readonly planTab: string
  readonly terminalTab: string
  readonly noPlan: string
  readonly terminalEmpty: string
  readonly openPanel: string
  readonly imageAttachments: string
  readonly justNow: string
  readonly minutesAgo: (value: number) => string
  readonly hoursAgo: (value: number) => string
  readonly daysAgo: (value: number) => string
}

function locale(): 'en' | 'zh' {
  return navigator.language.toLowerCase().startsWith('zh') ? 'zh' : 'en'
}

const COPY = {
  en: Object.freeze({
    newSession: 'New session',
    promptPlaceholder: 'Describe the task for the agent…',
    messagePlaceholder: 'Reply…',
    queuePlaceholder: 'Running — type to queue the next change…',
    send: 'Send',
    defaultWorkspace: 'Default',
    running: 'Running',
    idle: 'Idle',
    thinking: 'Thinking',
    empty: 'No sessions yet. Start one from the top.',
    emptyTranscript: 'No messages yet. Say something below.',
    cancel: 'Stop',
    expired: 'This link is no longer valid. Regenerate it from the desktop tray.',
    error: 'State unavailable.',
    retry: 'Retry',
    toggleTheme: 'Toggle theme',
    back: 'Back',
    newTaskTitle: 'New session',
    sessionMenu: 'Session menu',
    rename: 'Rename',
    renameTitle: 'Rename session',
    renameSave: 'Save',
    compact: 'Compact context',
    compactDone: 'Compaction requested',
    reasoning: 'Thinking',
    reasoningDone: 'Thought',
    toolRunning: 'Working',
    taskPlan: 'Task list',
    edited: 'edited',
    added: '+',
    removed: '−',
    linesUnit: ' lines',
    permission: 'Permissions',
    permissionTitle: 'Permission mode',
    model: 'Model',
    modelTitle: 'Model & thinking',
    thinkingLevel: 'Thinking level',
    apply: 'Apply',
    contextTitle: 'Context usage',
    contextUsed: 'Context used',
    contextWindowLabel: 'Context window',
    contextTokens: 'tokens',
    inputTokens: 'Input',
    outputTokens: 'Output',
    cacheRead: 'Cache read',
    cacheWrite: 'Cache write',
    queuedPrompt: 'Queued',
    removeQueue: 'Remove queued message',
    interruptionApproval: 'Approval needed',
    interruptionQuestion: 'Question for you',
    interruptionDelegated: 'Answered on the desktop',
    allow: 'Allow',
    reject: 'Reject',
    answerOnDesktop: 'Answer on desktop',
    submitAnswer: 'Submit',
    otherAnswer: 'Other…',
    planReview: 'Plan review',
    approvePlan: 'Approve plan',
    decisionRecorded: 'Recorded',
    decisionConflict: 'Already answered elsewhere',
    actionFailed: 'Action failed, try again',
    catalogEmpty: 'No models available',
    selectModelFirst: 'Pick a model first',
    planTab: 'Plan',
    terminalTab: 'Terminal',
    noPlan: 'No plan document yet. It appears here while a plan is up for review.',
    terminalEmpty: 'No command history in this session yet.',
    openPanel: 'Open side panel',
    imageAttachments: 'image(s) attached',
    justNow: 'just now',
    minutesAgo: (value: number) => `${String(value)} min ago`,
    hoursAgo: (value: number) => `${String(value)} h ago`,
    daysAgo: (value: number) => `${String(value)} d ago`,
  }),
  zh: Object.freeze({
    newSession: '新会话',
    promptPlaceholder: '描述要交给 Agent 的任务…',
    messagePlaceholder: '继续对话…',
    queuePlaceholder: '运行中,继续输入以排队后续修改…',
    send: '发送',
    defaultWorkspace: '默认工作区',
    running: '运行中',
    idle: '空闲',
    thinking: '思考中',
    empty: '还没有会话,点击上方新建。',
    emptyTranscript: '还没有消息,在下方说点什么。',
    cancel: '停止',
    expired: '链接已失效,请在桌面端托盘重新生成。',
    error: '状态获取失败。',
    retry: '重试',
    toggleTheme: '切换深浅色',
    back: '返回',
    newTaskTitle: '新会话',
    sessionMenu: '会话菜单',
    rename: '重命名',
    renameTitle: '重命名会话',
    renameSave: '保存',
    compact: '压缩上下文',
    compactDone: '已请求压缩',
    reasoning: '思考',
    reasoningDone: '思考',
    toolRunning: '执行中',
    taskPlan: '任务清单',
    edited: '已编辑',
    added: '+',
    removed: '−',
    linesUnit: ' 行',
    permission: '权限模式',
    permissionTitle: '权限模式',
    model: '模型',
    modelTitle: '模型与思考',
    thinkingLevel: '思考等级',
    apply: '应用',
    contextTitle: '上下文用量',
    contextUsed: '已用上下文',
    contextWindowLabel: '上下文窗口',
    contextTokens: 'tokens',
    inputTokens: '输入',
    outputTokens: '输出',
    cacheRead: '缓存读取',
    cacheWrite: '缓存写入',
    queuedPrompt: '已排队',
    removeQueue: '撤销排队消息',
    interruptionApproval: '等待批准',
    interruptionQuestion: '需要你选择',
    interruptionDelegated: '已转桌面端处理',
    allow: '允许',
    reject: '拒绝',
    answerOnDesktop: '桌面端处理',
    submitAnswer: '提交',
    otherAnswer: '其他…',
    planReview: '计划确认',
    approvePlan: '同意计划',
    decisionRecorded: '已记录',
    decisionConflict: '已在别处作答',
    actionFailed: '操作失败,请重试',
    catalogEmpty: '暂无可用模型',
    selectModelFirst: '请先选择模型',
    planTab: '计划',
    terminalTab: '终端',
    noPlan: '还没有计划文档。计划提交确认时会显示在这里。',
    terminalEmpty: '本会话还没有命令历史。',
    openPanel: '打开侧边面板',
    imageAttachments: '张图片',
    justNow: '刚刚',
    minutesAgo: (value: number) => `${String(value)} 分钟前`,
    hoursAgo: (value: number) => `${String(value)} 小时前`,
    daysAgo: (value: number) => `${String(value)} 天前`,
  }),
} as const

export function copyFor(): MobileCopy {
  return locale() === 'zh' ? COPY.zh : COPY.en
}

export function relativeTime(at: number, now: number, copy: MobileCopy): string {
  const delta = Math.max(0, Math.floor((now - at) / 1000))
  if (delta < 60) return copy.justNow
  if (delta < 3_600) return copy.minutesAgo(Math.floor(delta / 60))
  if (delta < 86_400) return copy.hoursAgo(Math.floor(delta / 3_600))
  return copy.daysAgo(Math.floor(delta / 86_400))
}

export function workspaceLabel(cwd: string): string {
  const parts = cwd.split(/[\\/]/u).filter(part => part !== '')
  const tail = parts.slice(-2).join('/')
  return tail === '' ? cwd : tail
}

/** Compact token counts: 46.1万 style in zh, 461k style in en. */
export function formatTokens(value: number): string {
  if (value >= 100_000_000) return `${(value / 100_000_000).toFixed(1)}亿`
  if (value >= 10_000) return `${(value / 10_000).toFixed(1)}万`
  return value.toLocaleString('en-US')
}
