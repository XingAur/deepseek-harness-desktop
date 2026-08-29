import { PassThrough } from 'node:stream'
import { describe, expect, it } from 'vitest'
import { PROTOCOL_VERSION, encodeProtocolFrame, type AdapterRequest } from './protocol.js'
import { runCodexCliWorker } from './codex-worker.js'
import { openCodexAppServerChannel, type CodexAppServerChannel, type CodexAppServerChannelOptions, type CodexNotification, type CodexServerRequest } from './adapters/codex-cli.js'

interface ScriptedChannel {
  channel: CodexAppServerChannel
  requests: Array<{ method: string; params: Record<string, unknown>; resolve(result: Record<string, unknown>): void; reject(error: Error): void }>
  notifications: Array<{ method: string; params: Record<string, unknown> }>
  responses: Array<{ serverRequest: CodexServerRequest; result: Record<string, unknown> }>
}

function scriptedChannel(script: { notifications: CodexNotification[]; serverRequests?: CodexServerRequest[]; threadId?: string }): ScriptedChannel {
  const state: ScriptedChannel = { channel: null as unknown as CodexAppServerChannel, requests: [], notifications: [], responses: [] }
  const threadId = script.threadId ?? 'thread-1'
  let notificationListener: ((notification: CodexNotification) => void) | null = null
  let serverRequestListener: ((serverRequest: CodexServerRequest) => void) | null = null
  const queue = (microtask: () => void) => queueMicrotask(microtask)
  state.channel = {
    request(method, params) {
      return new Promise<Record<string, unknown>>((resolve, reject) => {
        state.requests.push({ method, params, resolve, reject })
        if (method === 'thread/start') {
          queue(() => resolve({ threadId }))
        } else if (method === 'turn/start') {
          queue(() => {
            for (const serverRequest of script.serverRequests ?? []) {
              queue(() => serverRequestListener?.(serverRequest))
            }
            for (const notification of script.notifications) {
              queue(() => notificationListener?.(notification))
            }
            resolve({})
          })
        } else {
          queue(() => resolve({}))
        }
      })
    },
    notify(method, params = {}) { state.notifications.push({ method, params }) },
    respond(serverRequest, result) { state.responses.push({ serverRequest, result }) },
    async close() { /* scripted channel has no process */ },
    exited: new Promise<void>(() => undefined),
  }
  const openFake = (options: CodexAppServerChannelOptions): CodexAppServerChannel => {
    notificationListener = options.onNotification
    serverRequestListener = options.onServerRequest
    return state.channel
  }
  ;(state as ScriptedChannel & { openFake: typeof openFake }).openFake = openFake
  return state
}

function frameOf(type: string, requestId: string, sessionId = 'session', payload: Record<string, unknown> = {}, sequence = 1): string {
  return `${JSON.stringify({ protocolVersion: PROTOCOL_VERSION, requestId, sessionId, sequence, type, payload })}\n`
}

function handshakeFrame(): string {
  return frameOf('handshake', 'handshake', 'session', { adapterKind: 'codex-cli' }, 0)
}

function collectOutput(stdout: PassThrough): { frames: unknown[] } {
  const frames: unknown[] = []
  let pending = ''
  stdout.on('data', (chunk: string) => {
    pending += chunk
    let newline = pending.indexOf('\n')
    while (newline !== -1) {
      const line = pending.slice(0, newline)
      pending = pending.slice(newline + 1)
      if (line.length > 0) frames.push(JSON.parse(line))
      newline = pending.indexOf('\n')
    }
  })
  return { frames }
}

describe('codex worker protocol loop', () => {
  it('runs a session end to end and maps notifications to protocol events', async () => {
    const scripted = scriptedChannel({
      notifications: [
        { method: 'item/agentMessage/delta', params: { delta: '你好', itemId: 'i1', threadId: 't', turnId: 'u' } },
        { method: 'item/completed', params: { item: { type: 'agentMessage', text: '完成' }, threadId: 't', turnId: 'u' } },
        { method: 'turn/completed', params: { threadId: 't', turn: { status: 'completed' } } },
      ],
    })
    const input = new PassThrough()
    const stdout = new PassThrough()
    const output = collectOutput(stdout).frames
    const worker = runCodexCliWorker({ input, stdout, stderr: new PassThrough() }, {
      cliPath: '/usr/bin/codex',
      heartbeatIntervalMs: 3_600_000,
      openChannel: (scripted as ScriptedChannel & { openFake: typeof openCodexAppServerChannel }).openFake,
    })
    input.write(handshakeFrame())
    input.write(frameOf('session.start', 'session-start', 'session', { permission: 'request-approval', prompt: '检查项目' }))
    await waitFor(() => output.some((frame) => (frame as { type: string }).type === 'session.completed'))
    input.end()
    await worker

    const types = output.map((frame) => (frame as { type: string }).type)
    expect(types).toContain('response.ok')
    expect(types).toContain('session.started')
    expect(types).toContain('message.delta')
    expect(types).toContain('message.completed')
    expect(types).toContain('session.completed')
    expect(scripted.notifications).toContainEqual({ method: 'initialized', params: {} })
    const startRequest = scripted.requests.find((request) => request.method === 'thread/start')
    expect(startRequest?.params).toMatchObject({ sandbox: 'workspace-write', approvalPolicy: 'untrusted' })
    const turnRequest = scripted.requests.find((request) => request.method === 'turn/start')
    expect(turnRequest?.params.input).toEqual([{ type: 'text', text: '检查项目' }])
  })

  it('surfaces approval requests and forwards resolutions to the app-server', async () => {
    const scripted = scriptedChannel({
      notifications: [
        { method: 'turn/completed', params: { threadId: 't', turn: { status: 'completed' } } },
      ],
      serverRequests: [{
        id: 41,
        method: 'item/commandExecution/requestApproval',
        params: { command: 'rm -rf /tmp/x', threadId: 't', turnId: 'u', itemId: 'i9' },
      }],
    })
    const input = new PassThrough()
    const stdout = new PassThrough()
    const output = collectOutput(stdout).frames
    const worker = runCodexCliWorker({ input, stdout, stderr: new PassThrough() }, {
      cliPath: '/usr/bin/codex',
      heartbeatIntervalMs: 3_600_000,
      openChannel: (scripted as ScriptedChannel & { openFake: typeof openCodexAppServerChannel }).openFake,
    })
    input.write(handshakeFrame())
    input.write(frameOf('session.start', 'session-start', 'session', { permission: 'request-approval', prompt: '干活' }))
    const approvalFrame = await waitFor(() => output.find((frame) => (frame as { type: string }).type === 'approval.requested')) as { requestId: string; payload: { capability: string; scope: string } }
    expect(approvalFrame.payload.capability).toBe('terminal')
    expect(approvalFrame.payload.scope).toContain('rm -rf')

    input.write(frameOf('approval.resolve', `approval-${approvalFrame.requestId}`, 'session', { approved: true }))
    await waitFor(() => scripted.responses.length > 0)
    expect(scripted.responses[0].result).toEqual({ decision: 'accept' })
    input.end()
    await worker
  })

  it('keeps the adapter.init secret out of frames and exposes it only to the child env hook', async () => {
    const scripted = scriptedChannel({
      notifications: [
        { method: 'turn/completed', params: { threadId: 't', turn: { status: 'completed' } } },
      ],
    })
    const input = new PassThrough()
    const stdout = new PassThrough()
    const output = collectOutput(stdout).frames
    let childEnv: Record<string, string> | null = null
    const scriptedOpen = (scripted as ScriptedChannel & { openFake: typeof openCodexAppServerChannel }).openFake
    const openFake = (options: CodexAppServerChannelOptions): CodexAppServerChannel => {
      childEnv = options.env
      return scriptedOpen(options)
    }
    const worker = runCodexCliWorker({ input, stdout, stderr: new PassThrough() }, {
      cliPath: '/usr/bin/codex',
      heartbeatIntervalMs: 3_600_000,
      openChannel: openFake,
    })
    input.write(handshakeFrame())
    input.write(frameOf('adapter.init', 'adapter-init', 'private-init', { credentialId: 'cred-1', secret: 'sk-live-secret' }, 1))
    input.write(frameOf('session.start', 'session-start', 'session', { permission: 'full-access', prompt: '干活' }))
    await waitFor(() => output.some((frame) => (frame as { type: string }).type === 'session.completed'))
    input.end()
    await worker

    expect(childEnv).toMatchObject({ OPENAI_API_KEY: 'sk-live-secret' })
    const serialized = JSON.stringify(output)
    expect(serialized).not.toContain('sk-live-secret')
  })

  it('fails the session when the app-server exits before completing the turn', async () => {
    let exitNotify: (() => void) | null = null
    const pending: Array<{ reject(error: Error): void }> = []
    const channel: CodexAppServerChannel = {
      request: () => new Promise((_resolve, reject) => { pending.push({ reject }) }),
      notify: () => undefined,
      respond: () => undefined,
      async close() { /* no process */ },
      exited: new Promise<void>((resolve) => {
        exitNotify = () => {
          for (const waiter of pending) waiter.reject(new Error('server closed'))
          resolve()
        }
      }),
    }
    const input = new PassThrough()
    const stdout = new PassThrough()
    const output = collectOutput(stdout).frames
    const worker = runCodexCliWorker({ input, stdout, stderr: new PassThrough() }, {
      cliPath: '/usr/bin/codex',
      heartbeatIntervalMs: 3_600_000,
      openChannel: () => channel,
    })
    input.write(handshakeFrame())
    input.write(frameOf('session.start', 'session-start', 'session', { permission: 'smart-approval', prompt: '干活' }))
    await new Promise((resolve) => setTimeout(resolve, 20))
    exitNotify?.()
    await waitFor(() => output.some((frame) => (frame as { type: string }).type === 'session.failed'))
    input.end()
    await worker
  })
})

async function waitFor<T>(probe: () => T | undefined): Promise<T> {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    const value = probe()
    if (value) return value
    await new Promise((resolve) => setTimeout(resolve, 5))
  }
  throw new Error('condition was not met in time')
}

describe('codex notification mapping', () => {
  it('splits long deltas into byte bounded chunks', async () => {
    const { mapCodexNotification, splitDelta } = await import('./adapters/codex-cli.js')
    const events = mapCodexNotification({ method: 'item/agentMessage/delta', params: { delta: '很长'.repeat(3000) } })
    expect(events.length).toBeGreaterThan(1)
    for (const event of events) {
      expect(Buffer.byteLength(JSON.stringify(event), 'utf8')).toBeLessThan(32 * 1024)
    }
    expect(splitDelta('abc')).toEqual(['abc'])
  })

  it('maps item completions and turn failures', async () => {
    const { mapCodexNotification } = await import('./adapters/codex-cli.js')
    expect(mapCodexNotification({ method: 'item/completed', params: { item: { type: 'commandExecution' } } })[0].type).toBe('command.completed')
    expect(mapCodexNotification({ method: 'item/completed', params: { item: { type: 'fileChange' } } })[0].type).toBe('file.changed')
    expect(mapCodexNotification({ method: 'turn/completed', params: { turn: { status: 'failed' } } })[0].type).toBe('session.failed')
    expect(mapCodexNotification({ method: 'error', params: { willRetry: true } })).toEqual([])
    expect(mapCodexNotification({ method: 'thread/started', params: {} })).toEqual([])
  })

  it('maps permissions to codex sandbox and approval policy', async () => {
    const { permissionToCodexThreadOptions } = await import('./adapters/codex-cli.js')
    expect(permissionToCodexThreadOptions('request-approval')).toEqual({ sandbox: 'workspace-write', approvalPolicy: 'untrusted' })
    expect(permissionToCodexThreadOptions('smart-approval')).toEqual({ sandbox: 'workspace-write', approvalPolicy: 'on-request' })
    expect(permissionToCodexThreadOptions('full-access')).toEqual({ sandbox: 'danger-full-access', approvalPolicy: 'never' })
  })
})

describe('approval payload protocol extension', () => {
  it('accepts optional capability and scope on approval.requested frames', async () => {
    const { decodeProtocolFrame } = await import('./protocol.js')
    const frame = decodeProtocolFrame(JSON.stringify({
      protocolVersion: PROTOCOL_VERSION,
      requestId: '0b91a2b3-c4d5-4e6f-8a9b-0c1d2e3f4a5b',
      sessionId: 'session',
      sequence: 3,
      type: 'approval.requested',
      payload: { capability: 'terminal', scope: 'npm install' },
    })) as AdapterRequest & { type: 'approval.requested' }
    expect(frame.payload).toEqual({ capability: 'terminal', scope: 'npm install' })
  })

  it('rejects unknown or oversized approval payload fields', async () => {
    const { decodeProtocolFrame } = await import('./protocol.js')
    const base = { protocolVersion: PROTOCOL_VERSION, requestId: 'req-1', sessionId: 'session', sequence: 3 }
    expect(() => decodeProtocolFrame(JSON.stringify({ ...base, type: 'approval.requested', payload: { extra: 1 } }))).toThrow()
    expect(() => decodeProtocolFrame(JSON.stringify({ ...base, type: 'approval.requested', payload: { capability: 'x'.repeat(65) } }))).toThrow()
    expect(() => decodeProtocolFrame(JSON.stringify({ ...base, type: 'approval.requested', payload: {} }))).not.toThrow()
  })

  it('encodes approval frames within the control frame budget', async () => {
    const { encodeProtocolFrame } = await import('./protocol.js')
    const frame = encodeProtocolFrame({
      protocolVersion: PROTOCOL_VERSION,
      requestId: 'req-2',
      sessionId: 'session',
      sequence: 4,
      type: 'approval.requested',
      payload: { capability: 'terminal', scope: 's'.repeat(512) },
    } as never)
    expect(Buffer.byteLength(frame, 'utf8')).toBeLessThanOrEqual(32 * 1024 + 1)
  })
})
