import {
  DESKTOP_BRIDGE_CHANNEL,
  bridgeCommandByAction,
  isBridgeRequest,
  isRecord,
  type BridgeResponse,
} from './bridge-contract'

export interface ActiveWorkbench {
  generationId: string
  origin: string
}

export interface WorkbenchBridgeDependencies {
  frame(): HTMLIFrameElement | null
  active(): ActiveWorkbench | null
  invoke(command: string, args: Record<string, unknown>): Promise<unknown>
}

export function createWorkbenchBridge(dependencies: WorkbenchBridgeDependencies) {
  return {
    async onMessage(event: MessageEvent): Promise<void> {
      const frameWindow = dependencies.frame()?.contentWindow
      const active = dependencies.active()
      if (frameWindow === null || frameWindow === undefined || active === null) return
      if (event.source !== frameWindow || !sameOrigin(event.origin, active.origin)) return
      if (!isBridgeRequest(event.data)) return

      const targetOrigin = new URL(active.origin).origin
      const request = event.data
      let response: BridgeResponse
      try {
        const payload = bridgePayload(request.action, request.payload)
        const result = await dependencies.invoke(bridgeCommandByAction[request.action], {
          ...payload,
          generationId: active.generationId,
        })
        response = {
          channel: DESKTOP_BRIDGE_CHANNEL,
          requestId: request.requestId,
          ok: true,
          result,
        }
      } catch (cause) {
        response = {
          channel: DESKTOP_BRIDGE_CHANNEL,
          requestId: request.requestId,
          ok: false,
          error: bridgeError(cause),
        }
      }
      frameWindow.postMessage(response, targetOrigin)
    },
  }
}

function bridgePayload(action: keyof typeof bridgeCommandByAction, value: unknown): Record<string, unknown> {
  const payload = isRecord(value) ? value : {}
  if (action === 'project.directory.preview') {
    if (typeof payload.idea !== 'string' || payload.idea.trim() === '') throw new Error('项目需求无效')
    return { idea: payload.idea }
  }
  if (action === 'project.directory.create') {
    if (typeof payload.projectName !== 'string' || payload.projectName.trim() === '') throw new Error('项目名称无效')
    return { projectName: payload.projectName }
  }
  if (action === 'project.directory.recycle') {
    if (typeof payload.workspaceId !== 'string') throw new Error('Workspace ID 无效')
    return { workspaceId: payload.workspaceId }
  }
  return payload
}

function sameOrigin(actual: string, expected: string): boolean {
  try {
    return new URL(actual).origin === new URL(expected).origin
  } catch {
    return false
  }
}

function bridgeError(cause: unknown): { code: string; message: string } {
  if (isRecord(cause)) {
    return {
      code: typeof cause.code === 'string' ? cause.code : 'internal',
      message: typeof cause.message === 'string' ? cause.message : '请求未能完成',
    }
  }
  return { code: 'internal', message: cause instanceof Error ? cause.message : '请求未能完成' }
}
