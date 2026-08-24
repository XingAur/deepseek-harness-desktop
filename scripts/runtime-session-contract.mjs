export const CONTRACT_STAGES = Object.freeze([
  'runtime-start',
  'runtime-ready',
  'workspace-create',
  'session-create',
  'session-binding',
  'session-prompt',
  'session-open',
  'session-event',
  'session-close',
  'cleanup',
])

export const FAILURE_CATEGORIES = Object.freeze([
  'timeout',
  'process-exited',
  'protocol-mismatch',
  'binding-missing',
  'event-missing',
  'cleanup-failed',
  'internal',
])

const FAILURE_CATEGORY_SET = new Set(FAILURE_CATEGORIES)

export class RuntimeSessionContractError extends Error {
  constructor(category, message, options) {
    super(message, options)
    if (!FAILURE_CATEGORY_SET.has(category)) throw new TypeError(`未知 Session 契约错误类别：${String(category)}`)
    this.name = 'RuntimeSessionContractError'
    this.category = category
  }
}

export async function runRuntimeSessionContract(driver, options = {}) {
  const timeoutMs = positiveTimeout(options.timeoutMs ?? 30_000)
  const stages = []
  const startedAt = Date.now()
  let failure
  let workspaceId
  let sessionId

  try {
    await runStage(stages, 'runtime-start', timeoutMs, () => driver.start())
    await runStage(stages, 'runtime-ready', timeoutMs, () => driver.ready())
    workspaceId = await runStage(stages, 'workspace-create', timeoutMs, () => driver.createWorkspace())
    sessionId = await runStage(stages, 'session-create', timeoutMs, () => driver.createSession(workspaceId))
    await runStage(stages, 'session-binding', timeoutMs, () => driver.requireBinding(sessionId))
    await runStage(stages, 'session-prompt', timeoutMs, () => driver.prompt(sessionId))
    await runStage(stages, 'session-open', timeoutMs, () => driver.open(sessionId))
    await runStage(stages, 'session-event', timeoutMs, () => driver.waitForEvents(sessionId))
    await runStage(stages, 'session-close', timeoutMs, () => driver.closeSession(sessionId))
  } catch (error) {
    failure = error
  }

  let cleanupFailure
  try {
    await runStage(stages, 'cleanup', timeoutMs, () => driver.cleanup(), 'cleanup-failed')
  } catch (error) {
    cleanupFailure = error
  }

  const durationMs = Math.max(0, Date.now() - startedAt)
  if (failure === undefined && cleanupFailure === undefined) return { ok: true, durationMs, stages }

  const primary = failure ?? cleanupFailure
  const result = {
    ok: false,
    durationMs,
    stages,
    failedStage: primary.stage,
    category: primary.category,
  }
  if (failure !== undefined && cleanupFailure !== undefined) {
    result.cleanupFailure = { category: 'cleanup-failed' }
  }
  return result
}

async function runStage(stages, stage, timeoutMs, action, fallbackCategory = 'internal') {
  const startedAt = Date.now()
  try {
    const value = await withTimeout(action(), timeoutMs, stage)
    stages.push({ stage, ok: true, durationMs: Math.max(0, Date.now() - startedAt) })
    return value
  } catch (error) {
    const category = error instanceof RuntimeSessionContractError ? error.category : fallbackCategory
    stages.push({ stage, ok: false, durationMs: Math.max(0, Date.now() - startedAt), category })
    throw Object.assign(new RuntimeSessionContractError(category, `Session contract stage failed: ${stage}`), { stage })
  }
}

function withTimeout(promise, timeoutMs, stage) {
  let timer
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      reject(new RuntimeSessionContractError('timeout', `Session contract stage timed out: ${stage}`))
    }, timeoutMs)
  })
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer))
}

function positiveTimeout(value) {
  if (!Number.isSafeInteger(value) || value < 1) throw new TypeError('Session 契约阶段超时必须是正整数')
  return value
}
