import { randomUUID } from 'node:crypto'
import type { Readable, Writable } from 'node:stream'
import { CONTROL_FRAME_MAX_BYTES, PROTOCOL_VERSION, decodeProtocolFrame, encodeProtocolFrame, isAdapterRequest, type AdapterRequest, type ProtocolFrame } from './protocol.js'
import { redactDiagnostic } from './redaction.js'
import {
  CODEX_APP_SERVER_CLIENT_NAME, approvalDescriptorFor,
  mapCodexNotification, openCodexAppServerChannel, permissionToCodexThreadOptions,
  type CodexAppServerChannel, type CodexServerRequest,
} from './adapters/codex-cli.js'

/**
 * The Codex worker speaks the desktop adapter protocol on stdio and drives a
 * real `codex app-server` child process. It never writes credentials to
 * diagnostics, and the secret received via adapter.init is only forwarded as
 * the child's environment.
 */

export interface CodexWorkerIo {
  input: Readable
  stdout: Writable
  stderr: Writable
}

export interface CodexWorkerOptions {
  cliPath: string
  cwd?: string
  heartbeatIntervalMs?: number
  openChannel?: typeof openCodexAppServerChannel
}

interface PendingApproval {
  serverRequest: CodexServerRequest
}

export async function runCodexCliWorker(io: CodexWorkerIo, options: CodexWorkerOptions): Promise<void> {
  const cwd = options.cwd ?? process.cwd()
  const heartbeatIntervalMs = options.heartbeatIntervalMs ?? 10_000
  const openChannel = options.openChannel ?? openCodexAppServerChannel
  const outputSequences = new Map<string, number>()
  const pendingApprovals = new Map<string, PendingApproval>()
  let handshaken = false
  let secret: string | null = null
  let credentialId = ''
  const channels: { current: CodexAppServerChannel | null } = { current: null }
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
  let activeSessionId: string | null = null
  const state = { turnFinished: false }

  const writeFrame = (frame: ProtocolFrame): void => {
    const sequence = outputSequences.get(frame.sessionId) ?? -1
    const next = sequence + 1
    outputSequences.set(frame.sessionId, next)
    io.stdout.write(encodeProtocolFrame({ ...frame, sequence: next } as ProtocolFrame))
  }

  const writeOk = (request: AdapterRequest): void => {
    writeFrame({
      protocolVersion: PROTOCOL_VERSION,
      requestId: request.requestId,
      sessionId: request.sessionId,
      sequence: 0,
      type: 'response.ok',
      payload: { accepted: true },
    })
  }

  const writeError = (
    request: Pick<AdapterRequest, 'protocolVersion' | 'requestId' | 'sessionId'>,
    code: string,
    message: string,
  ): void => {
    writeFrame({
      protocolVersion: PROTOCOL_VERSION,
      requestId: request.requestId,
      sessionId: request.sessionId,
      sequence: 0,
      type: 'response.error',
      payload: { code, message },
    })
  }

  const emit = (sessionId: string, event: { type: ProtocolFrame['type']; payload: Record<string, unknown> }): void => {
    writeFrame({
      protocolVersion: PROTOCOL_VERSION,
      requestId: `event-${randomUUID()}`,
      sessionId,
      sequence: 0,
      type: event.type,
      payload: event.payload,
    } as ProtocolFrame)
  }

  const stopHeartbeat = (): void => {
    if (heartbeatTimer !== null) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
  }

  const teardown = async (): Promise<void> => {
    stopHeartbeat()
    const active = channels.current
    channels.current = null
    if (active !== null) await active.close()
    if (secret !== null) secret = null
  }

  for await (const line of readLines(io.input)) {
    if (line.length > CONTROL_FRAME_MAX_BYTES) {
      io.stderr.write(`${redactDiagnostic(new Error('Protocol frame exceeds 32 KiB'))}\n`)
      await teardown()
      return
    }
    let frame: AdapterRequest
    try {
      frame = decodeProtocolFrame(line) as AdapterRequest
    } catch (cause) {
      io.stderr.write(`${redactDiagnostic(cause)}\n`)
      continue
    }
    if (!isAdapterRequest(frame)) {
      writeError(frame, 'UNEXPECTED_FRAME', 'Worker accepts requests only')
      continue
    }
    try {
      if (frame.type === 'handshake') {
        if (frame.payload.adapterKind !== 'codex-cli') {
          writeError(frame, 'UNSUPPORTED_ADAPTER', 'Codex worker only supports adapterKind codex-cli')
          continue
        }
        handshaken = true
        writeOk(frame)
        continue
      }

      if (frame.type === 'adapter.init') {
        if (!handshaken) {
          writeError(frame, 'HANDSHAKE_REQUIRED', 'Successful handshake is required before adapter.init')
          continue
        }
        // The secret is retained only to seed the Codex child environment and is
        // cleared as soon as the child has been spawned (or the worker ends).
        credentialId = frame.payload.credentialId
        secret = frame.payload.secret
        frame.payload.secret = ''
        writeOk(frame)
        continue
      }

      if (frame.type === 'session.start') {
        if (!handshaken) {
          writeError(frame, 'HANDSHAKE_REQUIRED', 'Successful handshake is required before session.start')
          continue
        }
        if (activeSessionId !== null || channels.current !== null) {
          writeError(frame, 'SESSION_ACTIVE', 'Codex worker runs one session per transport')
          continue
        }
        try {
          await startSession(frame, cwd, options, openChannel, {
            emit: (event) => emit(frame.sessionId, event),
            registerApproval(serverRequest) {
              const approvalId = randomUUID()
              pendingApprovals.set(approvalId, { serverRequest })
              const descriptor = approvalDescriptorFor(serverRequest)
              writeFrame({
                protocolVersion: PROTOCOL_VERSION,
                requestId: approvalId,
                sessionId: frame.sessionId,
                sequence: 0,
                type: 'approval.requested',
                payload: { capability: descriptor.capability, scope: descriptor.scope },
              })
            },
            onChannel(newChannel) { channels.current = newChannel },
            onSessionActive() { activeSessionId = frame.sessionId },
            onTurnFinished() { state.turnFinished = true },
            secretAccessor: () => secret,
            credentialIdAccessor: () => credentialId,
          })
          writeOk(frame)
          heartbeatTimer = setInterval(() => {
            if (activeSessionId === null) return
            writeFrame({
              protocolVersion: PROTOCOL_VERSION,
              requestId: `heartbeat-${Date.now()}`,
              sessionId: activeSessionId,
              sequence: 0,
              type: 'worker.heartbeat',
              payload: {},
            })
          }, heartbeatIntervalMs)
          heartbeatTimer.unref?.()
        } catch (cause) {
          await teardown()
          writeError(frame, 'CODEX_START_FAILED', humanizeStartError(String(redactDiagnostic(cause))))
          continue
        }
        continue
      }

      if (frame.type === 'approval.resolve') {
        const approvalId = frame.requestId.startsWith('approval-') ? frame.requestId.slice('approval-'.length) : ''
        const pending = pendingApprovals.get(approvalId)
        if (pending === undefined) {
          writeError(frame, 'UNKNOWN_APPROVAL', 'The approval is not pending in this worker')
          continue
        }
        pendingApprovals.delete(approvalId)
        channels.current?.respond(pending.serverRequest, {
          decision: frame.payload.approved ? 'accept' : 'decline',
        })
        emit(frame.sessionId, { type: 'approval.resolved', payload: {} })
        writeOk(frame)
        continue
      }

      if (frame.type === 'session.cancel') {
        try {
          if (channels.current !== null && activeSessionId !== null && !state.turnFinished) {
            await channels.current.request('turn/interrupt', {}).catch(() => undefined)
          }
          writeOk(frame)
        } catch (cause) {
          writeError(frame, 'CANCEL_FAILED', String(redactDiagnostic(cause)))
        }
        await teardown()
        continue
      }
    } catch (cause) {
      io.stderr.write(`${redactDiagnostic(cause)}\n`)
    }
  }
  await teardown()
}

interface SessionHooks {
  emit(event: { type: ProtocolFrame['type']; payload: Record<string, unknown> }): void
  registerApproval(serverRequest: CodexServerRequest): void
  onChannel(channel: CodexAppServerChannel): void
  onSessionActive(): void
  onTurnFinished(): void
  secretAccessor(): string | null
  credentialIdAccessor(): string
}

async function startSession(
  frame: AdapterRequest & { type: 'session.start' },
  cwd: string,
  options: CodexWorkerOptions,
  openChannel: typeof openCodexAppServerChannel,
  hooks: SessionHooks,
): Promise<void> {
  const secret = hooks.secretAccessor()
  const env: Record<string, string> = buildChildEnvironment(secret)
  const channel = openChannel({
    cliPath: options.cliPath,
    cwd,
    env,
    onNotification(notification) {
      for (const event of mapCodexNotification(notification)) hooks.emit(event)
      if (notification.method === 'turn/completed' || notification.method === 'error') {
        finished = true
        hooks.onTurnFinished()
      }
    },
    onServerRequest(serverRequest) {
      hooks.registerApproval(serverRequest)
    },
  })
  let finished = false
  hooks.onChannel(channel)
  channel.exited.then(() => {
    if (!finished) {
      finished = true
      hooks.onTurnFinished()
      hooks.emit({ type: 'session.failed', payload: {} })
    }
  }).catch(() => undefined)
  try {
    await channel.request('initialize', {
      clientInfo: { name: CODEX_APP_SERVER_CLIENT_NAME, version: '0.1.0' },
    })
    const threadOptions = permissionToCodexThreadOptions(frame.payload.permission)
    const thread = await channel.request('thread/start', {
      cwd,
      sandbox: threadOptions.sandbox,
      approvalPolicy: threadOptions.approvalPolicy,
    })
    // The thread id lives at result.thread.id in the 0.149 protocol.
    const threadRecord = typeof thread.thread === 'object' && thread.thread !== null
      ? thread.thread as Record<string, unknown>
      : {}
    const threadId = typeof thread.threadId === 'string'
      ? thread.threadId
      : typeof threadRecord.id === 'string'
        ? threadRecord.id
        : ''
    if (threadId === '') throw new Error('Codex app-server did not return a thread id')
    await channel.request('turn/start', {
      threadId,
      input: [{ type: 'text', text: frame.payload.prompt ?? '' }],
    })
    hooks.emit({ type: 'session.started', payload: {} })
    hooks.onSessionActive()
  } catch (cause) {
    await channel.close()
    throw cause
  }
}

/** 把 Codex 启动失败的原因翻译成人话与下一步。 */
function humanizeStartError(raw: string): string {
  if (raw.includes('sqlite state runtime')) {
    return 'Codex 启动失败：本机 Codex 状态库被其他程序占用（比如 ChatGPT 桌面版）。重新开始任务会自动使用隔离的运行目录，一般即可解决。'
  }
  if (raw.includes('not logged in') || raw.includes('login required') || raw.includes('auth')) {
    return 'Codex 启动失败：还没有登录。回到 Agent 页完成「登录官方账号」后重试。'
  }
  if (raw.includes('ENOENT') || raw.includes('no such file')) {
    return 'Codex 启动失败：找不到 CLI 文件。回到 Agent 页「重新检测」，或在高级设置里手动指定路径。'
  }
  const bounded = raw.slice(0, 300)
  return `Codex 启动失败：${bounded}`
}

function buildChildEnvironment(secret: string | null): Record<string, string> {
  const env: Record<string, string> = {}
  for (const name of ['HOME', 'PATH', 'LANG', 'LC_ALL', 'TZ', 'TMPDIR', 'USER', 'XDG_CONFIG_HOME', 'SSL_CERT_FILE', 'CODEX_HOME']) {
    const value = process.env[name]
    if (value !== undefined && value !== '') env[name] = value
  }
  if (env.PATH === undefined) env.PATH = '/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin'
  if (secret !== null && secret.length > 0) env.OPENAI_API_KEY = secret
  return env
}

async function* readLines(input: Readable): AsyncGenerator<string> {
  let buffer = ''
  for await (const chunk of input) {
    const text = typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString('utf8')
    buffer += text
    if (buffer.length > CONTROL_FRAME_MAX_BYTES * 2) {
      yield buffer.slice(0, CONTROL_FRAME_MAX_BYTES + 1)
      buffer = ''
      continue
    }
    let newline = buffer.indexOf('\n')
    while (newline !== -1) {
      const line = buffer.slice(0, newline).replace(/\r$/, '')
      buffer = buffer.slice(newline + 1)
      if (line.length > 0) yield line
      newline = buffer.indexOf('\n')
    }
  }
  if (buffer.trim().length > 0) yield buffer.trim()
}
