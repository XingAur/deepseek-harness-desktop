import {
  DESKTOP_BRIDGE_V2_CHANNEL,
  isVersionedBridgeResponse,
  type VersionedBridgeAction,
} from './bridge-contract'

export const DESKTOP_BRIDGE_CHANNEL = 'dsh-desktop/v1' as const

export type DesktopBridgeAction =
  | 'profile.list'
  | 'profile.create'
  | 'profile.update'
  | 'profile.duplicate'
  | 'profile.delete'
  | 'profile.switch'
  | 'project.metadata.list'
  | 'project.metadata.patch'
  | 'project.metadata.remove'
  | 'project.directory.preview'
  | 'project.directory.create'
  | 'project.directory.recycle'
  | 'app.launch'
  | 'app.stop'
  | 'app.status'
  | 'external.open'
  | 'diagnostics.export'

interface DesktopBridgeResponse {
  channel: typeof DESKTOP_BRIDGE_CHANNEL
  requestId: string
  ok: boolean
  result?: unknown
  error?: { code: string; message: string }
}

interface BridgeWindow {
  addEventListener(type: 'message', listener: (event: MessageEvent) => void): void
  removeEventListener(type: 'message', listener: (event: MessageEvent) => void): void
}

interface ParentWindow {
  postMessage(message: unknown, targetOrigin: string): void
}

export interface DesktopBridgeOptions {
  host?: BridgeWindow
  parent?: ParentWindow
  targetOrigin?: string
  context?: { generationId: string; sessionId: string }
  timeoutMs?: number
  createRequestId?: () => string
}

export interface DesktopBridgeLike {
  readonly mode?: 'managed' | 'preview'
  request<T = unknown>(action: DesktopBridgeAction, payload?: Record<string, unknown>): Promise<T>
  requestV2<T = unknown>(
    action: VersionedBridgeAction,
    context?: { generationId: string; sessionId: string },
    payload?: Record<string, unknown>,
  ): Promise<T>
  dispose(): void
}

export function createDesktopBridge(options: DesktopBridgeOptions = {}): DesktopBridgeLike {
  const host: BridgeWindow = options.host ?? (window as unknown as BridgeWindow)
  const parent: ParentWindow = options.parent ?? window.parent
  const targetOrigin = options.targetOrigin ?? parentOrigin(document.referrer)
  const timeoutMs = options.timeoutMs ?? 15_000
  const createRequestId = options.createRequestId ?? (() => crypto.randomUUID())
  const pending = new Map<string, {
    resolve(value: unknown): void
    reject(cause: Error): void
    timer: ReturnType<typeof setTimeout>
    channel: string
    generationId?: string
    sessionId?: string
  }>()
  let disposed = false

  const onMessage = (event: MessageEvent) => {
    if (targetOrigin === null || event.source !== parent || event.origin !== targetOrigin) return
    const responseV2 = isVersionedBridgeResponse(event.data) ? event.data : null
    const responseV1 = responseV2 === null && isResponse(event.data) ? event.data : null
    if (responseV2 === null && responseV1 === null) return
    const requestId = responseV2?.requestId ?? responseV1?.requestId
    const request = requestId === undefined ? undefined : pending.get(requestId)
    if (request === undefined) return
    if (responseV2 !== null) {
      if (request.channel !== DESKTOP_BRIDGE_V2_CHANNEL
        || request.generationId !== responseV2.generationId
        || request.sessionId !== responseV2.sessionId) return
    } else if (request.channel === DESKTOP_BRIDGE_V2_CHANNEL) {
      return
    }
    const response = responseV2 ?? responseV1
    if (response === null) return
    clearTimeout(request.timer)
    pending.delete(requestId as string)
    if (response.ok) request.resolve(response.result)
    else request.reject(new Error(response.error?.message ?? '桌面请求失败'))
  }
  host.addEventListener('message', onMessage)

  return {
    mode: 'managed',
    request<T = unknown>(action: DesktopBridgeAction, payload: Record<string, unknown> = {}): Promise<T> {
      if (disposed) return Promise.reject(new Error('桌面桥已关闭'))
      if (targetOrigin === null) return Promise.reject(new Error('无法确定桌面壳层来源'))
      const requestId = createRequestId()
      return new Promise<T>((resolve, reject) => {
        const timer = setTimeout(() => {
          pending.delete(requestId)
          reject(new Error('请求超时：请稍后重试，或点「重新检测」'))
        }, timeoutMs)
        pending.set(requestId, {
          resolve: (value) => resolve(value as T),
          reject,
          timer,
          channel: DESKTOP_BRIDGE_CHANNEL,
        })
        parent.postMessage({
          channel: DESKTOP_BRIDGE_CHANNEL,
          requestId,
          action,
          payload,
        }, targetOrigin)
      })
    },
    requestV2<T = unknown>(
      action: VersionedBridgeAction,
      context: { generationId: string; sessionId: string } = options.context ?? { generationId: '', sessionId: '' },
      payload: Record<string, unknown> = {},
    ): Promise<T> {
      if (disposed) return Promise.reject(new Error('桌面桥已关闭'))
      if (targetOrigin === null) return Promise.reject(new Error('无法确定桌面壳层来源'))
      if (context.generationId === '' || context.sessionId === '') return Promise.reject(new Error('桌面会话上下文不可用'))
      const requestId = createRequestId()
      return new Promise<T>((resolve, reject) => {
        const timer = setTimeout(() => {
          pending.delete(requestId)
          reject(new Error('请求超时：请稍后重试，或点「重新检测」'))
        }, timeoutMs)
        pending.set(requestId, {
          resolve: (value) => resolve(value as T),
          reject,
          timer,
          channel: DESKTOP_BRIDGE_V2_CHANNEL,
          generationId: context.generationId,
          sessionId: context.sessionId,
        })
        parent.postMessage({
          channel: DESKTOP_BRIDGE_V2_CHANNEL,
          requestId,
          generationId: context.generationId,
          sessionId: context.sessionId,
          action,
          payload,
        }, targetOrigin)
      })
    },
    dispose(): void {
      if (disposed) return
      disposed = true
      host.removeEventListener('message', onMessage)
      for (const request of pending.values()) {
        clearTimeout(request.timer)
        request.reject(new Error('桌面桥已关闭'))
      }
      pending.clear()
    },
  }
}

function parentOrigin(referrer: string): string | null {
  if (referrer.length === 0) return null
  try {
    const url = new URL(referrer)
    if (url.origin !== 'null') return url.origin
    if (url.protocol === 'tauri:' && url.hostname !== '' && url.username === '' && url.password === '') {
      return `${url.protocol}//${url.host}`
    }
  } catch {
    // 桌面桥保持不可用，但宿主未提供 referrer 不应阻断插件加载。
  }
  return null
}

function isResponse(value: unknown): value is DesktopBridgeResponse {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Partial<DesktopBridgeResponse>
  return candidate.channel === DESKTOP_BRIDGE_CHANNEL
    && typeof candidate.requestId === 'string'
    && typeof candidate.ok === 'boolean'
}
