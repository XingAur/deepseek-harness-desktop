import { createInterface } from 'node:readline'
import type { Readable, Writable } from 'node:stream'
import { mockSessionEvents } from './adapters/mock.js'
import { createSessionSequenceGuard, decodeProtocolFrame, encodeProtocolFrame, isAdapterRequest, type AdapterRequest, type ProtocolFrame } from './protocol.js'
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
  const lines = createInterface({ input: io.input, crlfDelay: Infinity })

  for await (const line of lines) {
    if (!line.trim()) continue
    let frame: ProtocolFrame
    try {
      frame = decodeProtocolFrame(line)
    } catch (cause) {
      const identity = extractIdentity(line)
      if (identity) {
        terminatedSessions.add(identity.sessionId)
        writeError(io.stdout, outputSequences, identity, 'INVALID_FRAME', 'Invalid adapter protocol frame')
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

function extractIdentity(serialized: string): Pick<AdapterRequest, 'protocolVersion' | 'requestId' | 'sessionId'> | undefined {
  try {
    const value = JSON.parse(serialized) as Record<string, unknown>
    if (value.protocolVersion === 'dsh-agent-adapter/v1' && typeof value.requestId === 'string' && typeof value.sessionId === 'string') {
      return { protocolVersion: value.protocolVersion, requestId: value.requestId, sessionId: value.sessionId }
    }
  } catch {
    return undefined
  }
  return undefined
}
