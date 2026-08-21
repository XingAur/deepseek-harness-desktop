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
  timeoutMs?: number
  createRequestId?: () => string
}

export interface DesktopBridgeLike {
  request<T = unknown>(action: DesktopBridgeAction, payload?: Record<string, unknown>): Promise<T>
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
  }>()
  let disposed = false

  const onMessage = (event: MessageEvent) => {
    if (event.source !== parent || event.origin !== targetOrigin || !isResponse(event.data)) return
    const request = pending.get(event.data.requestId)
    if (request === undefined) return
    clearTimeout(request.timer)
    pending.delete(event.data.requestId)
    if (event.data.ok) request.resolve(event.data.result)
    else request.reject(new Error(event.data.error?.message ?? '桌面请求失败'))
  }
  host.addEventListener('message', onMessage)

  return {
    request<T = unknown>(action: DesktopBridgeAction, payload: Record<string, unknown> = {}): Promise<T> {
      if (disposed) return Promise.reject(new Error('桌面桥已关闭'))
      const requestId = createRequestId()
      return new Promise<T>((resolve, reject) => {
        const timer = setTimeout(() => {
          pending.delete(requestId)
          reject(new Error('桌面请求超时'))
        }, timeoutMs)
        pending.set(requestId, {
          resolve: (value) => resolve(value as T),
          reject,
          timer,
        })
        parent.postMessage({
          channel: DESKTOP_BRIDGE_CHANNEL,
          requestId,
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

function parentOrigin(referrer: string): string {
  if (referrer.length === 0) throw new Error('无法确定桌面壳层来源')
  return new URL(referrer).origin
}

function isResponse(value: unknown): value is DesktopBridgeResponse {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Partial<DesktopBridgeResponse>
  return candidate.channel === DESKTOP_BRIDGE_CHANNEL
    && typeof candidate.requestId === 'string'
    && typeof candidate.ok === 'boolean'
}
