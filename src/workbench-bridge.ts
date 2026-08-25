import {
  DESKTOP_BRIDGE_CHANNEL,
  DESKTOP_BRIDGE_V2_CHANNEL,
  CONTENT_REFERENCE_MAX_BYTES,
  bridgeCommandByActionV2,
  containsSecretShape,
  bridgeCommandByAction,
  isBridgeRequest,
  isVersionedBridgePayload,
  isVersionedBridgeResponse,
  isVersionedBridgeRequest,
  isRecord,
  type VersionedBridgeRequest,
  type BridgeResponse,
} from './bridge-contract'
import { isAgentEventEnvelope } from './agent-events'

export interface ActiveWorkbench {
  generationId: string
  origin: string
  sessionId?: string
}

export interface WorkbenchBridgeDependencies {
  frame(): HTMLIFrameElement | null
  active(): ActiveWorkbench | null
  invoke(command: string, args: Record<string, unknown>): Promise<unknown>
}

export function createWorkbenchBridge(dependencies: WorkbenchBridgeDependencies) {
  let boundSessionId = dependencies.active()?.sessionId
  return {
    async onMessage(event: MessageEvent): Promise<void> {
      const frameWindow = dependencies.frame()?.contentWindow
      const active = dependencies.active()
      if (frameWindow === null || frameWindow === undefined || active === null) return
      if (event.source !== frameWindow || !sameOrigin(event.origin, active.origin)) return
      if (isVersionedBridgeRequest(event.data)) {
        if (boundSessionId === undefined) boundSessionId = event.data.sessionId
        if (boundSessionId !== event.data.sessionId) return
        await forwardVersionedRequest(event.data, frameWindow, { ...active, sessionId: boundSessionId }, dependencies.invoke)
        return
      }
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
    onAgentEvent(event: unknown): void {
      const frameWindow = dependencies.frame()?.contentWindow
      const active = dependencies.active()
      if (frameWindow === null || frameWindow === undefined || active === null) return
      if (!isAgentEventEnvelope(event)
        || event.generationId !== active.generationId
        || (active.sessionId !== undefined && event.sessionId !== active.sessionId)
        || (boundSessionId !== undefined && event.sessionId !== boundSessionId)) return
      frameWindow.postMessage(event, new URL(active.origin).origin)
    },
  }
}

async function forwardVersionedRequest(
  request: VersionedBridgeRequest,
  frameWindow: Window,
  active: ActiveWorkbench,
  invoke: WorkbenchBridgeDependencies['invoke'],
): Promise<void> {
  if (request.generationId !== active.generationId
    || (active.sessionId !== undefined && request.sessionId !== active.sessionId)) return
  const targetOrigin = new URL(active.origin).origin
  let response: Record<string, unknown>
  try {
    const payload = bridgePayloadV2(request.action, request.payload)
    const invokeArgs = request.action === 'approval.list' || request.action === 'approval.resolve'
      ? { input: { ...payload, generationId: active.generationId, sessionId: request.sessionId } }
      : { ...payload, generationId: active.generationId, sessionId: request.sessionId }
    const result = await invoke(bridgeCommandByActionV2[request.action], invokeArgs)
    if (containsSecretShape(result)) throw new Error('bridge result contains a secret-shaped field')
    response = {
      channel: DESKTOP_BRIDGE_V2_CHANNEL,
      requestId: request.requestId,
      generationId: active.generationId,
      sessionId: request.sessionId,
      ok: true,
      result,
    }
  } catch (cause) {
    response = {
      channel: DESKTOP_BRIDGE_V2_CHANNEL,
      requestId: request.requestId,
      generationId: active.generationId,
      sessionId: request.sessionId,
      ok: false,
      error: bridgeError(cause),
    }
  }
  if (!isVersionedBridgeResponse(response)) {
    response = {
      channel: DESKTOP_BRIDGE_V2_CHANNEL,
      requestId: request.requestId,
      generationId: active.generationId,
      sessionId: request.sessionId,
      ok: false,
      error: { code: 'response-too-large', message: 'Agent 响应超过桥接上限' },
    }
  }
  frameWindow.postMessage(response, targetOrigin)
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
  if (action === 'app.launch' || action === 'app.stop') {
    if (typeof payload.workspaceId !== 'string' || payload.workspaceId.trim() === '') throw new Error('Workspace ID 无效')
    return { workspaceId: payload.workspaceId }
  }
  return payload
}

function bridgePayloadV2(
  action: keyof typeof bridgeCommandByActionV2,
  value: unknown,
): Record<string, unknown> {
  const payload = isRecord(value) ? value : {}
  if (!isVersionedBridgePayload(action, payload)) throw new Error('Agent 请求参数无效')
  if (action === 'task.create') {
    requireId(payload.workspaceId, 'Workspace ID')
    if (typeof payload.prompt !== 'string' || payload.prompt.trim() === '') throw new Error('任务提示无效')
    requirePermission(payload.permission)
    const providerId = payload.providerId
    const agentId = payload.agentId
    if ((providerId === undefined) !== (agentId === undefined)) throw new Error('Provider 与 Agent 必须成对提供')
    if (providerId !== undefined) {
      requireId(providerId, 'Provider ID')
      requireId(agentId, 'Agent ID')
    }
    return {
      workspaceId: payload.workspaceId,
      prompt: payload.prompt,
      permission: payload.permission,
      ...(providerId === undefined ? {} : { providerId, agentId }),
    }
  }
  if (action === 'task.start' || action === 'task.cancel' || action === 'task.resume') {
    requireId(payload.taskId, 'Task ID')
    return { taskId: payload.taskId }
  }
  if (action === 'task.list') {
    requireId(payload.workspaceId, 'Workspace ID')
    return { workspaceId: payload.workspaceId }
  }
  if (action === 'task.recover') {
    requireId(payload.workspaceId, 'Workspace ID')
    requireId(payload.taskId, 'Task ID')
    requireId(payload.sourceSessionId, '原会话 ID')
    return { workspaceId: payload.workspaceId, taskId: payload.taskId, sourceSessionId: payload.sourceSessionId }
  }
  if (action === 'approval.list') {
    requireId(payload.taskId, 'Task ID')
    return { taskId: payload.taskId }
  }
  if (action === 'approval.resolve') {
    requireId(payload.approvalId, 'Approval ID')
    requireId(payload.taskId, 'Task ID')
    if (!['allow-once', 'allow-for-task', 'deny'].includes(String(payload.decision))) {
      throw new Error('审批决定无效')
    }
    return {
      approvalId: payload.approvalId,
      taskId: payload.taskId,
      decision: payload.decision,
    }
  }
  if (action === 'content-reference.read') {
    requireId(payload.contentRefId, '内容引用 ID')
    requireId(payload.taskId, 'Task ID')
    const offset = typeof payload.offset === 'number' ? payload.offset : 0
    const length = typeof payload.length === 'number' ? payload.length : CONTENT_REFERENCE_MAX_BYTES
    if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(length) || length < 0 || length > CONTENT_REFERENCE_MAX_BYTES) {
      throw new Error('内容引用范围无效')
    }
    return { contentRefId: payload.contentRefId, taskId: payload.taskId, offset, length }
  }
  if (action === 'credential.put') {
    if (payload.credentialId !== undefined) requireId(payload.credentialId, 'Credential ID')
    if (payload.providerId !== undefined) requireId(payload.providerId, 'Provider ID')
    if (typeof payload.secret !== 'string' || payload.secret.length === 0 || payload.secret.length > 16 * 1024) {
      throw new Error('凭证内容无效')
    }
    return {
      ...(payload.credentialId === undefined ? {} : { credentialId: payload.credentialId }),
      ...(payload.providerId === undefined ? {} : { providerId: payload.providerId }),
      secret: payload.secret,
    }
  }
  if (action === 'credential.delete' || action === 'credential.status' || action === 'credential.test') {
    requireId(payload.credentialId, 'Credential ID')
    return { credentialId: payload.credentialId }
  }
  if (action === 'cli.path.select' || action === 'cli.path.status'
    || action === 'cli.install.status' || action === 'cli.install.start'
    || action === 'cli.login.status' || action === 'cli.login.start') {
    requireId(payload.providerId, 'Provider ID')
    if (action === 'cli.path.select') return { providerId: payload.providerId, path: payload.path }
    return { providerId: payload.providerId }
  }
  if (action === 'extension.install' || action === 'extension.enable' || action === 'extension.disable' || action === 'extension.uninstall') {
    requireId(payload.extensionId, 'Extension ID')
    return { extensionId: payload.extensionId }
  }
  return {}
}

function requireId(value: unknown, label: string): asserts value is string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)) {
    throw new Error(label + ' 无效')
  }
}

function requirePermission(value: unknown): asserts value is string {
  if (value !== 'request-approval' && value !== 'smart-approval' && value !== 'full-access') {
    throw new Error('权限模式无效')
  }
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
