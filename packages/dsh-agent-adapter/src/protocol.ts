export const PROTOCOL_VERSION = 'dsh-agent-adapter/v1'
export type AgentAdapterProtocolVersion = typeof PROTOCOL_VERSION
export const CONTROL_FRAME_MAX_BYTES = 32 * 1024

const identifier = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/
const permissionProfiles = ['request-approval', 'smart-approval', 'full-access'] as const
const extensionTypes = ['plugin', 'skill', 'mcp'] as const
const adapterKinds = ['mock', 'codex-sdk', 'codex-app-server-preview', 'claude-agent-sdk', 'claude-cli-dev'] as const
const requestTypes = ['handshake', 'session.start', 'session.cancel', 'approval.resolve'] as const
const responseTypes = ['response.ok', 'response.error'] as const
const eventTypes = [
  'session.started', 'session.resumed', 'session.completed', 'session.failed', 'message.delta', 'message.completed',
  'progress.updated', 'tool.started', 'tool.output', 'tool.completed', 'command.started', 'command.output',
  'command.completed', 'file.changed', 'file.diff.available', 'approval.requested', 'approval.resolved',
  'extension.called', 'usage.updated', 'worker.interrupted', 'worker.recoverable',
] as const
const frameKeys = ['payload', 'protocolVersion', 'requestId', 'sequence', 'sessionId', 'type']

export type PermissionProfile = typeof permissionProfiles[number]
export type ExtensionType = typeof extensionTypes[number]
export type AdapterKind = typeof adapterKinds[number]
export type AdapterRequestType = typeof requestTypes[number]
export type AdapterResponseType = typeof responseTypes[number]
export type AgentEventType = typeof eventTypes[number]

export interface ProviderDescriptor {
  id: string
  name: string
}

export interface AgentDescriptor {
  id: string
  name: string
  adapterKind: AdapterKind
}

export interface AgentSessionRequest {
  permission: PermissionProfile
}

export interface OpaqueContentReference {
  id: string
  mediaType: string
  byteLength: number
  truncated: boolean
}

export interface ExtensionDescriptor {
  id: string
  name: string
  type: ExtensionType
}

interface FrameBase<Type extends string, Payload> {
  protocolVersion: AgentAdapterProtocolVersion
  requestId: string
  sessionId: string
  sequence: number
  type: Type
  payload: Payload
}

export type EmptyPayload = { [key: string]: never }
export interface HandshakePayload { adapterKind: AdapterKind }
export interface SessionStartPayload { permission: PermissionProfile }
export interface ApprovalResolvePayload { approved: boolean }
export interface ResponseOkPayload { accepted: boolean }
export interface ResponseErrorPayload { code: string; message: string }
export interface TextPayload { text: string }
export interface ContentReferencePayload { contentRef: OpaqueContentReference }

export type HandshakeRequest = FrameBase<'handshake', HandshakePayload>
export type SessionStartRequest = FrameBase<'session.start', SessionStartPayload>
export type SessionCancelRequest = FrameBase<'session.cancel', EmptyPayload>
export type ApprovalResolveRequest = FrameBase<'approval.resolve', ApprovalResolvePayload>
export type AdapterRequest = HandshakeRequest | SessionStartRequest | SessionCancelRequest | ApprovalResolveRequest

export type ResponseOk = FrameBase<'response.ok', ResponseOkPayload>
export type ResponseError = FrameBase<'response.error', ResponseErrorPayload>
export type AdapterResponse = ResponseOk | ResponseError

export type EmptyAgentEventType = Exclude<AgentEventType, 'message.delta' | 'message.completed' | 'tool.output' | 'command.output' | 'file.diff.available'>
export type EmptyAgentEvent = FrameBase<EmptyAgentEventType, EmptyPayload>
export type MessageDeltaEvent = FrameBase<'message.delta', TextPayload>
export type MessageCompletedEvent = FrameBase<'message.completed', TextPayload>
export type ToolOutputEvent = FrameBase<'tool.output', ContentReferencePayload>
export type CommandOutputEvent = FrameBase<'command.output', ContentReferencePayload>
export type FileDiffAvailableEvent = FrameBase<'file.diff.available', ContentReferencePayload>
export type AgentEvent = EmptyAgentEvent | MessageDeltaEvent | MessageCompletedEvent | ToolOutputEvent | CommandOutputEvent | FileDiffAvailableEvent
export type ProtocolFrame = AdapterRequest | AdapterResponse | AgentEvent

export function decodeProtocolFrame(serialized: string): ProtocolFrame {
  if (Buffer.byteLength(serialized, 'utf8') > CONTROL_FRAME_MAX_BYTES) throw new Error('Protocol frame exceeds 32 KiB')
  let value: unknown
  try {
    value = JSON.parse(serialized)
  } catch {
    throw new Error('Protocol frame must be valid JSON')
  }
  return validateProtocolFrame(value)
}

export function encodeProtocolFrame(frame: ProtocolFrame): string {
  const validated = validateProtocolFrame(frame)
  const serialized = JSON.stringify(validated)
  if (Buffer.byteLength(serialized, 'utf8') > CONTROL_FRAME_MAX_BYTES) {
    throw new Error('Protocol control frame exceeds 32 KiB')
  }
  return `${serialized}\n`
}

export function assertCorrelatedResponse(request: AdapterRequest, response: AdapterResponse): void {
  if (request.requestId !== response.requestId) throw new Error('response requestId does not match request')
  if (request.sessionId !== response.sessionId) throw new Error('response sessionId does not match request')
}

export function createSessionSequenceGuard() {
  const lastSequences = new Map<string, number>()
  return {
    accept(frame: Pick<ProtocolFrame, 'sessionId' | 'sequence'>): void {
      const previous = lastSequences.get(frame.sessionId)
      if (previous !== undefined && frame.sequence <= previous) {
        throw new Error('Protocol sequence must be strictly increasing for each session')
      }
      lastSequences.set(frame.sessionId, frame.sequence)
    },
  }
}

export function isAdapterRequest(frame: ProtocolFrame): frame is AdapterRequest {
  return requestTypes.includes(frame.type as AdapterRequestType)
}

function validateProtocolFrame(value: unknown): ProtocolFrame {
  const frame = asRecord(value, 'Protocol frame')
  const keys = Object.keys(frame).sort()
  if (keys.length !== frameKeys.length || keys.some((key, index) => key !== frameKeys[index])) {
    const unknown = keys.filter((key) => !frameKeys.includes(key))
    const missing = frameKeys.filter((key) => !keys.includes(key))
    throw new Error(unknown.length > 0 ? `Protocol frame has unknown fields: ${unknown.join(', ')}` : `Protocol frame is missing required fields: ${missing.join(', ')}`)
  }
  if (frame.protocolVersion !== PROTOCOL_VERSION) throw new Error(`Protocol version must be ${PROTOCOL_VERSION}`)
  assertIdentifier(frame.requestId, 'requestId')
  assertIdentifier(frame.sessionId, 'sessionId')
  if (!Number.isSafeInteger(frame.sequence) || (frame.sequence as number) < 0) throw new Error('Protocol sequence must be a non-negative safe integer')
  if (typeof frame.type !== 'string' || ![...requestTypes, ...responseTypes, ...eventTypes].includes(frame.type as AdapterRequestType | AdapterResponseType | AgentEventType)) {
    throw new Error('Protocol frame type is unsupported')
  }
  validatePayload(frame.type, frame.payload)
  return frame as unknown as ProtocolFrame
}

function validatePayload(type: string, value: unknown): void {
  const payload = asRecord(value, `${type} payload`)
  if (type === 'handshake') {
    assertExactKeys(payload, ['adapterKind'], type)
    if (typeof payload.adapterKind !== 'string' || !adapterKinds.includes(payload.adapterKind as AdapterKind)) throw new Error('handshake payload adapterKind is unsupported')
    return
  }
  if (type === 'session.start') {
    assertExactKeys(payload, ['permission'], type)
    if (typeof payload.permission !== 'string' || !permissionProfiles.includes(payload.permission as PermissionProfile)) throw new Error('session.start payload permission is unsupported')
    return
  }
  if (type === 'approval.resolve') {
    assertExactKeys(payload, ['approved'], type)
    if (typeof payload.approved !== 'boolean') throw new Error('approval.resolve payload approved must be boolean')
    return
  }
  if (type === 'response.ok') {
    assertExactKeys(payload, ['accepted'], type)
    if (typeof payload.accepted !== 'boolean') throw new Error('response.ok payload accepted must be boolean')
    return
  }
  if (type === 'response.error') {
    assertExactKeys(payload, ['code', 'message'], type)
    if (typeof payload.code !== 'string' || typeof payload.message !== 'string') throw new Error('response.error payload must contain code and message')
    return
  }
  if (type === 'message.delta' || type === 'message.completed') {
    assertExactKeys(payload, ['text'], type)
    if (typeof payload.text !== 'string') throw new Error(`${type} payload text must be string`)
    return
  }
  if (type === 'tool.output' || type === 'command.output' || type === 'file.diff.available') {
    assertExactKeys(payload, ['contentRef'], type)
    validateContentReference(payload.contentRef)
    return
  }
  assertExactKeys(payload, [], type)
}

function validateContentReference(value: unknown): void {
  const reference = asRecord(value, 'contentRef')
  assertExactKeys(reference, ['byteLength', 'id', 'mediaType', 'truncated'], 'contentRef')
  if (typeof reference.id !== 'string' || typeof reference.mediaType !== 'string' || !Number.isSafeInteger(reference.byteLength) || (reference.byteLength as number) < 0 || typeof reference.truncated !== 'boolean') {
    throw new Error('contentRef is invalid')
  }
}

function asRecord(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Error(`${label} must be an object`)
  return value as Record<string, unknown>
}

function assertIdentifier(value: unknown, field: string): void {
  if (!isProtocolIdentifier(value)) throw new Error(`Protocol ${field} is invalid`)
}

export function isProtocolIdentifier(value: unknown): value is string {
  return typeof value === 'string' && identifier.test(value)
}

function assertExactKeys(value: Record<string, unknown>, expected: string[], label: string): void {
  const keys = Object.keys(value).sort()
  const expectedKeys = [...expected].sort()
  if (keys.length !== expectedKeys.length || keys.some((key, index) => key !== expectedKeys[index])) {
    const unknown = keys.filter((key) => !expectedKeys.includes(key))
    throw new Error(unknown.length > 0 ? `${label} payload has unknown fields: ${unknown.join(', ')}` : `${label} payload is missing required fields`)
  }
}
