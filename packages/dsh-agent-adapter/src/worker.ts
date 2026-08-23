import type { Readable, Writable } from 'node:stream'
import { mockSessionEvents } from './adapters/mock.js'
import { CONTROL_FRAME_MAX_BYTES, PROTOCOL_VERSION, createSessionSequenceGuard, decodeProtocolFrame, encodeProtocolFrame, isAdapterRequest, isProtocolIdentifier, type AdapterRequest, type ProtocolFrame } from './protocol.js'
import { redactDiagnostic } from './redaction.js'

export interface MockWorkerIo {
  input: Readable
  stdout: Writable
  stderr: Writable
}

export async function runMockWorker(io: MockWorkerIo): Promise<void> {
  let handshaken = false
  const outputSequences = new Map<string, number>()
  const inputSequences = createSessionSequenceGuard()
  const terminatedSessions = new Set<string>()
  for await (const inputLine of readBoundedJsonlLines(io.input)) {
    if (inputLine.oversized) {
      io.stderr.write(`${redactDiagnostic(new Error('Protocol frame exceeds 32 KiB'))}\n`)
      continue
    }
    const line = inputLine.value
    if (!line.trim()) continue
    let frame: ProtocolFrame
    try {
      frame = decodeProtocolFrame(line)
    } catch (cause) {
      const identity = extractSafeIdentity(line)
      if (identity?.sessionId) {
        terminatedSessions.add(identity.sessionId)
        if (identity.requestId) writeError(io.stdout, outputSequences, { protocolVersion: PROTOCOL_VERSION, requestId: identity.requestId, sessionId: identity.sessionId }, 'INVALID_FRAME', 'Invalid adapter protocol frame')
      }
      io.stderr.write(`${redactDiagnostic(cause)}\n`)
      continue
    }
    try {
      inputSequences.accept(frame)
    } catch (cause) {
      terminatedSessions.add(frame.sessionId)
      writeError(io.stdout, outputSequences, frame, 'INVALID_SEQUENCE', 'Adapter session sequence is not strictly increasing')
      io.stderr.write(`${redactDiagnostic(cause)}\n`)
      continue
    }
    if (!isAdapterRequest(frame)) {
      terminatedSessions.add(frame.sessionId)
      writeError(io.stdout, outputSequences, frame, 'UNEXPECTED_FRAME', 'Worker accepts requests only')
      continue
    }
    if (terminatedSessions.has(frame.sessionId)) {
      writeError(io.stdout, outputSequences, frame, 'SESSION_TERMINATED', 'Adapter session is terminated')
      continue
    }
    if (frame.type === 'handshake') {
      if (frame.payload.adapterKind !== 'mock') {
        writeError(io.stdout, outputSequences, frame, 'UNSUPPORTED_ADAPTER', 'Mock worker only supports adapterKind mock')
      } else {
        handshaken = true
        writeOk(io.stdout, outputSequences, frame)
      }
      continue
    }
    if (!handshaken && frame.type === 'session.start') {
      writeError(io.stdout, outputSequences, frame, 'HANDSHAKE_REQUIRED', 'Successful handshake is required before session.start')
      continue
    }
    if (frame.type === 'session.start') {
      writeOk(io.stdout, outputSequences, frame)
      for (const event of mockSessionEvents(frame)) writeFrame(io.stdout, outputSequences, event)
      continue
    }
    if (frame.type === 'session.cancel' || frame.type === 'approval.resolve') {
      writeOk(io.stdout, outputSequences, frame)
      continue
    }
    writeError(io.stdout, outputSequences, frame, 'UNSUPPORTED_REQUEST', `Request type ${frame.type} is not implemented by mock`)
  }
}

async function* readBoundedJsonlLines(input: Readable): AsyncGenerator<{ value: string; oversized: false } | { oversized: true }> {
  let buffered = Buffer.alloc(0)
  let discardingOversizedLine = false
  for await (const chunk of input) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
    let start = 0
    while (start < bytes.length) {
      const newline = bytes.indexOf(0x0a, start)
      const end = newline === -1 ? bytes.length : newline
      const segment = bytes.subarray(start, end)
      start = newline === -1 ? bytes.length : newline + 1
      if (discardingOversizedLine) {
        if (newline !== -1) discardingOversizedLine = false
        continue
      }
      if (buffered.length + segment.length > CONTROL_FRAME_MAX_BYTES) {
        buffered = Buffer.alloc(0)
        yield { oversized: true }
        if (newline === -1) discardingOversizedLine = true
        continue
      }
      buffered = Buffer.concat([buffered, segment])
      if (newline !== -1) {
        const line = buffered.toString('utf8').replace(/\r$/, '')
        buffered = Buffer.alloc(0)
        yield { value: line, oversized: false }
      }
    }
  }
  if (!discardingOversizedLine && buffered.length > 0) yield { value: buffered.toString('utf8').replace(/\r$/, ''), oversized: false }
}

function writeOk(stdout: Writable, sequences: Map<string, number>, request: AdapterRequest): void {
  writeFrame(stdout, sequences, {
    protocolVersion: request.protocolVersion,
    requestId: request.requestId,
    sessionId: request.sessionId,
    sequence: 0,
    type: 'response.ok',
    payload: { accepted: true },
  })
}

function writeError(stdout: Writable, sequences: Map<string, number>, request: Pick<AdapterRequest, 'protocolVersion' | 'requestId' | 'sessionId'>, code: string, message: string): void {
  writeFrame(stdout, sequences, {
    protocolVersion: request.protocolVersion,
    requestId: request.requestId,
    sessionId: request.sessionId,
    sequence: 0,
    type: 'response.error',
    payload: { code, message },
  })
}

function writeFrame(stdout: Writable, sequences: Map<string, number>, frame: ProtocolFrame): void {
  const sequence = sequences.get(frame.sessionId) ?? -1
  const next = sequence + 1
  sequences.set(frame.sessionId, next)
  stdout.write(encodeProtocolFrame({ ...frame, sequence: next } as ProtocolFrame))
}

function extractSafeIdentity(serialized: string): { requestId?: string; sessionId?: string } | undefined {
  try {
    const value: unknown = JSON.parse(serialized)
    if (typeof value !== 'object' || value === null || Array.isArray(value)) return undefined
    const frame = value as Record<string, unknown>
    const requestId = isProtocolIdentifier(frame.requestId) ? frame.requestId : undefined
    const sessionId = isProtocolIdentifier(frame.sessionId) ? frame.sessionId : undefined
    return requestId || sessionId ? { requestId, sessionId } : undefined
  } catch {
    return undefined
  }
  return undefined
}
