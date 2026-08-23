import { describe, expect, it } from 'vitest'
import { PassThrough } from 'node:stream'
import type { AgentAdapterProtocolVersion } from './protocol.js'
import {
  CONTROL_FRAME_MAX_BYTES,
  PROTOCOL_VERSION,
  assertCorrelatedResponse,
  createSessionSequenceGuard,
  decodeProtocolFrame,
  encodeProtocolFrame,
} from './protocol.js'
import { runMockWorker } from './worker.js'

const request = {
  protocolVersion: PROTOCOL_VERSION,
  requestId: 'request-1',
  sessionId: 'session-1',
  sequence: 0,
  type: 'handshake',
  payload: { adapterKind: 'mock' },
} as const

describe('agent adapter protocol', () => {
  it('publishes the literal protocol version type', () => {
    const version: AgentAdapterProtocolVersion = PROTOCOL_VERSION

    expect(version).toBe('dsh-agent-adapter/v1')
  })

  it('rejects unknown and missing top-level frame fields', () => {
    expect(() => decodeProtocolFrame(JSON.stringify({ ...request, unexpected: true }))).toThrow(/unknown/i)
    const { payload: _payload, ...withoutPayload } = request
    expect(() => decodeProtocolFrame(JSON.stringify(withoutPayload))).toThrow(/payload/i)
  })

  it('rejects a protocol version mismatch', () => {
    expect(() => decodeProtocolFrame(JSON.stringify({ ...request, protocolVersion: 'dsh-agent-adapter/v0' }))).toThrow(PROTOCOL_VERSION)
  })

  it('requires response request and session identifiers to match the request', () => {
    const response = {
      protocolVersion: PROTOCOL_VERSION,
      requestId: request.requestId,
      sessionId: request.sessionId,
      sequence: 1,
      type: 'response.ok',
      payload: { accepted: true },
    } as const

    expect(() => assertCorrelatedResponse(request, response)).not.toThrow()
    expect(() => assertCorrelatedResponse(request, { ...response, sessionId: 'other-session' })).toThrow(/sessionId/i)
    expect(() => assertCorrelatedResponse(request, { ...response, requestId: 'other-request' })).toThrow(/requestId/i)
  })

  it('accepts only strictly increasing event sequence numbers per session', () => {
    const guard = createSessionSequenceGuard()

    expect(() => guard.accept({ sessionId: 'session-1', sequence: 0 })).not.toThrow()
    expect(() => guard.accept({ sessionId: 'session-1', sequence: 1 })).not.toThrow()
    expect(() => guard.accept({ sessionId: 'session-1', sequence: 1 })).toThrow(/increasing/i)
    expect(() => guard.accept({ sessionId: 'session-1', sequence: 0 })).toThrow(/increasing/i)
    expect(() => guard.accept({ sessionId: 'session-2', sequence: 0 })).not.toThrow()
  })

  it('rejects a control frame larger than 32 KiB of serialized UTF-8', () => {
    const frame = {
      ...request,
      type: 'message.delta',
      payload: { text: 'x'.repeat(CONTROL_FRAME_MAX_BYTES) },
    } as const

    expect(() => encodeProtocolFrame(frame)).toThrow(/32 KiB/i)
  })

  it('rejects an oversized inbound control frame before attempting JSON parsing', () => {
    expect(() => decodeProtocolFrame(' '.repeat(CONTROL_FRAME_MAX_BYTES + 1))).toThrow(/32 KiB/i)
  })

  it('rejects unknown fields in concrete request payloads', () => {
    expect(() => decodeProtocolFrame(JSON.stringify({
      ...request,
      payload: { adapterKind: 'mock', secret: 'must-not-pass' },
    }))).toThrow(/payload.*unknown/i)
  })

  it('requires a handshake before starting a mock session and emits ordered JSONL events', async () => {
    const input = new PassThrough()
    const stdout = new PassThrough()
    const stderr = new PassThrough()
    const lines: string[] = []
    stdout.setEncoding('utf8')
    stdout.on('data', (chunk: string) => lines.push(...chunk.split('\n').filter(Boolean)))

    const worker = runMockWorker({ input, stdout, stderr })
    input.end([
      { ...request, requestId: 'start-before-handshake', sessionId: 'session-before-handshake', type: 'session.start', payload: { permission: 'request-approval' } },
      request,
      { ...request, requestId: 'start-after-handshake', sessionId: 'session-after-handshake', type: 'session.start', payload: { permission: 'request-approval' } },
    ].map((frame) => JSON.stringify(frame)).join('\n') + '\n')
    await worker

    const frames = lines.map((line) => decodeProtocolFrame(line))
    expect(frames[0]).toMatchObject({ type: 'response.error', requestId: 'start-before-handshake' })
    const events = frames.filter((frame) => frame.sessionId === 'session-after-handshake' && frame.type.startsWith('session.'))
    expect(events.map((frame) => frame.type)).toEqual(['session.started', 'session.completed'])
    expect(events.map((frame) => frame.sequence)).toEqual([1, 2])
  })

  it('terminates only the affected mock session when an incoming sequence regresses', async () => {
    const input = new PassThrough()
    const stdout = new PassThrough()
    const stderr = new PassThrough()
    const lines: string[] = []
    stdout.setEncoding('utf8')
    stdout.on('data', (chunk: string) => lines.push(...chunk.split('\n').filter(Boolean)))

    const worker = runMockWorker({ input, stdout, stderr })
    input.end([
      request,
      { ...request, requestId: 'start', sessionId: 'sequence-session', type: 'session.start', payload: { permission: 'request-approval' } },
      { ...request, requestId: 'duplicate', sessionId: 'sequence-session', type: 'session.cancel', payload: {} },
    ].map((frame) => JSON.stringify(frame)).join('\n') + '\n')
    await worker

    const frames = lines.map((line) => decodeProtocolFrame(line))
    expect(frames.find((frame) => frame.requestId === 'duplicate')).toMatchObject({ type: 'response.error', payload: { code: 'INVALID_SEQUENCE' } })
  })

  it('bounds an unterminated worker JSONL line before its input stream ends', async () => {
    const input = new PassThrough()
    const stdout = new PassThrough()
    const stderr = new PassThrough()
    const diagnostics: string[] = []
    stderr.setEncoding('utf8')
    stderr.on('data', (chunk: string) => diagnostics.push(chunk))
    const worker = runMockWorker({ input, stdout, stderr })
    const boundedDiagnostic = new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('worker did not bound the unterminated JSONL line')), 500)
      stderr.once('data', () => {
        clearTimeout(timeout)
        resolve()
      })
    })

    input.write('x'.repeat(CONTROL_FRAME_MAX_BYTES + 1))
    await boundedDiagnostic
    input.end()
    await worker

    expect(diagnostics.join('')).toMatch(/32 KiB/i)
  })

  it('drops malformed identifiers without crashing or echoing them in worker errors', async () => {
    const input = new PassThrough()
    const stdout = new PassThrough()
    const stderr = new PassThrough()
    const lines: string[] = []
    stdout.setEncoding('utf8')
    stdout.on('data', (chunk: string) => lines.push(...chunk.split('\n').filter(Boolean)))
    const worker = runMockWorker({ input, stdout, stderr })

    input.end([
      { ...request, requestId: 'bad request id', sessionId: 'malformed-id-session' },
      { ...request, requestId: 'safe-handshake', sessionId: 'safe-session' },
    ].map((frame) => JSON.stringify(frame)).join('\n') + '\n')
    await expect(worker).resolves.toBeUndefined()

    expect(lines.join('\n')).not.toContain('bad request id')
    expect(lines.map((line) => decodeProtocolFrame(line))).toEqual(expect.arrayContaining([
      expect.objectContaining({ requestId: 'safe-handshake', sessionId: 'safe-session', type: 'response.ok' }),
    ]))
  })

  it('quarantines a known session after a version-mismatched frame', async () => {
    const input = new PassThrough()
    const stdout = new PassThrough()
    const stderr = new PassThrough()
    const lines: string[] = []
    stdout.setEncoding('utf8')
    stdout.on('data', (chunk: string) => lines.push(...chunk.split('\n').filter(Boolean)))
    const worker = runMockWorker({ input, stdout, stderr })

    input.end([
      { ...request, requestId: 'version-handshake', sessionId: 'version-session', sequence: 0 },
      { ...request, requestId: 'version-mismatch', sessionId: 'version-session', sequence: 1, protocolVersion: 'dsh-agent-adapter/v0' },
      { ...request, requestId: 'after-version-mismatch', sessionId: 'version-session', sequence: 2, type: 'session.cancel', payload: {} },
    ].map((frame) => JSON.stringify(frame)).join('\n') + '\n')
    await worker

    const frames = lines.map((line) => decodeProtocolFrame(line))
    expect(frames.find((frame) => frame.requestId === 'version-mismatch')).toMatchObject({ type: 'response.error', payload: { code: 'INVALID_FRAME' } })
    expect(frames.find((frame) => frame.requestId === 'after-version-mismatch')).toMatchObject({ type: 'response.error', payload: { code: 'SESSION_TERMINATED' } })
  })
})
