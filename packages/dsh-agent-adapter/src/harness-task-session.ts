import {
  HarnessBridgeClient,
  createHarnessProcessTransport,
  type HarnessAgentRequest,
  type HarnessProcessTransportOptions,
  type HarnessTaskResult,
  type HarnessTaskStartPayload,
  type HarnessTransport,
} from './harness-bridge.js'
import {
  createHarnessHostHandler,
  type HarnessAgentExecution,
  type HarnessAgentExecutionContext,
  type HarnessAgentExecutor,
  type HarnessEventSink,
} from './harness-host-handler.js'

export interface HarnessTaskSessionOptions {
  /** Injected in tests or when the host already owns the sidecar transport. */
  transport?: HarnessTransport
  /** Used by desktop/CLI hosts when they want the adapter to spawn the sidecar. */
  sidecar?: HarnessProcessTransportOptions
  execute: HarnessAgentExecutor
  taskTimeoutMs?: number
}

export interface HarnessTaskSession {
  start(payload: HarnessTaskStartPayload, requestId?: string): Promise<HarnessTaskResult>
  cancel(requestId: string): void | Promise<void>
  onEvent(listener: HarnessEventSink): () => void
  dispose(): void
}

/**
 * Bind one Harness decision session to one host model executor.
 *
 * The host may execute a request and report facts, but it has no replan API.
 * Replanning is therefore only possible when the sidecar sends another
 * execute-only request after it has evaluated the previous result.
 */
export function createHarnessTaskSession(options: HarnessTaskSessionOptions): HarnessTaskSession {
  if (options?.transport === undefined && options?.sidecar === undefined) {
    throw new Error('Harness 必须提供 Sidecar 传输')
  }
  if (typeof options?.execute !== 'function') throw new Error('Harness 模型执行器无效')

  const transport = options.transport ?? createHarnessProcessTransport(options.sidecar as HarnessProcessTransportOptions)
  const bridge = new HarnessBridgeClient(transport)
  const handler = createHarnessHostHandler({ execute: options.execute })
  const taskTimeoutMs = options.taskTimeoutMs ?? 3_600_000
  let disposed = false
  const offAgentRequests = bridge.onAgentRequest(async (request, requestId) => {
    if (disposed) return
    const result = await handler(request, (payload) => {
      try { void bridge.sendEvent(requestId, payload) } catch { /* sidecar closure is reported by task result */ }
    })
    try { await bridge.sendAgentResult(requestId, result) } catch { /* pending task observes the closure */ }
  })

  return {
    start(payload, requestId = crypto.randomUUID()) {
      if (disposed) return Promise.reject(new Error('Harness 会话已关闭'))
      let pending: Promise<HarnessTaskResult>
      try {
        pending = bridge.awaitTaskResult(requestId, taskTimeoutMs)
        bridge.startTask(payload, requestId)
      } catch (cause) {
        bridge.dispose()
        return Promise.reject(cause instanceof Error ? cause : new Error('Harness 任务启动失败'))
      }
      return pending
    },
    cancel(requestId) {
      return bridge.cancelTask(requestId)
    },
    onEvent(listener) {
      return bridge.onEvent(listener)
    },
    dispose() {
      if (disposed) return
      disposed = true
      offAgentRequests()
      bridge.dispose()
    },
  }
}

export type { HarnessAgentRequest, HarnessAgentExecution, HarnessAgentExecutionContext }
