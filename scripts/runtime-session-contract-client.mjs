import { existsSync } from 'node:fs'
import { createRequire } from 'node:module'
import { isAbsolute, join, relative, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { RuntimeSessionContractError } from './runtime-session-contract.mjs'

const CANDIDATE_CLIENT_FILES = Object.freeze({
  cordis: ['@deepseek-ai/cordis', 'lib/index.js'],
  connection: ['@deepseek-ai/dsh-client-connection', 'lib/client.js'],
  typert: ['@deepseek-ai/dsh-typert-registry', 'lib/client.js'],
  gateway: ['@deepseek-ai/dsh-api-gateway', 'lib/client.js'],
  remotes: ['@deepseek-ai/dsh-api-remotes', 'lib/client.js'],
  runtime: ['@deepseek-ai/dsh-client-runtime', 'lib/client.js'],
})

let bundleNonce = 0

export function resolveCandidateClientPaths(appDirectory) {
  if (typeof appDirectory !== 'string' || !isAbsolute(appDirectory)) {
    throw new RuntimeSessionContractError('protocol-mismatch', '候选 Runtime app 目录必须是绝对路径')
  }
  const nodeModules = resolve(appDirectory, 'node_modules')
  const paths = {}
  for (const [key, [packageName, entrypoint]] of Object.entries(CANDIDATE_CLIENT_FILES)) {
    const file = resolve(nodeModules, ...packageName.split('/'), entrypoint)
    const fromRoot = relative(nodeModules, file)
    if (fromRoot.startsWith('..') || isAbsolute(fromRoot) || !existsSync(file)) {
      throw new RuntimeSessionContractError(
        'protocol-mismatch',
        `候选 Runtime 缺少客户端契约包：${packageName}`,
      )
    }
    paths[key] = file
  }
  return Object.freeze(paths)
}

export function createCandidateSessionOperations(options) {
  const {
    sessions,
    workspaces,
    workspacePath,
    promptMarker,
    replyMarker,
    eventTimeoutMs = 25_000,
  } = options
  if (!Number.isSafeInteger(eventTimeoutMs) || eventTimeoutMs < 1) {
    throw new TypeError('Session 事件超时必须是正整数')
  }
  const bindings = new Map()

  return Object.freeze({
    async createWorkspace() {
      const workspace = await workspaces.create({ path: workspacePath })
      if (typeof workspace?.workspaceId !== 'string' || workspace.workspaceId === '') {
        throw new RuntimeSessionContractError('protocol-mismatch', 'workspace.create 未返回 workspaceId')
      }
      return workspace.workspaceId
    },

    async createSession(workspaceId) {
      const sessionId = await workspaces.connectWorkspace(workspaceId)
      if (typeof sessionId !== 'string' || sessionId === '') {
        throw new RuntimeSessionContractError('protocol-mismatch', 'connectWorkspace 未返回 sessionId')
      }
      return sessionId
    },

    async requireBinding(sessionId) {
      const binding = sessions.binding(sessionId)
      if (binding === undefined) {
        throw new RuntimeSessionContractError('binding-missing', '候选 Session binding 不可同步解析')
      }
      bindings.set(sessionId, binding)
    },

    async prompt(sessionId) {
      const binding = requiredBinding(bindings, sessionId)
      const result = await binding.session.prompt([{ type: 'text', text: promptMarker }], 'queue')
      if (result?.ok !== true || result.value?.accepted !== true) {
        throw new RuntimeSessionContractError('protocol-mismatch', '候选 Session 拒绝确定性 prompt')
      }
    },

    async open(sessionId) {
      requiredBinding(bindings, sessionId)
      sessions.open(sessionId)
    },

    async waitForEvents(sessionId) {
      const binding = requiredBinding(bindings, sessionId)
      await waitForSnapshotMarkers(binding.session, promptMarker, replyMarker, eventTimeoutMs)
    },

    async closeSession(sessionId) {
      requiredBinding(bindings, sessionId)
      sessions.clear()
      bindings.delete(sessionId)
    },
  })
}

export function createCandidateSessionDriver(options) {
  const lifecycle = options.lifecycle ?? {
    start: async () => undefined,
    ready: async () => undefined,
    cleanup: async () => undefined,
  }
  let modules
  let ctx
  let loop
  let disposeRemotes
  let operations
  let restoreGlobals
  let cleanupPromise

  return Object.freeze({
    async start() {
      await lifecycle.start()
      modules = await loadCandidateClientModules(options.appDirectory)
    },

    async ready() {
      await lifecycle.ready()
      restoreGlobals = installClientGlobals(options.origin, options.appDirectory)
      const initialized = await initializeCandidateClient(modules)
      ctx = initialized.ctx
      loop = initialized.loop
      disposeRemotes = initialized.disposeRemotes
      operations = createCandidateSessionOperations({
        sessions: initialized.sessions,
        workspaces: initialized.workspaces,
        workspacePath: options.workspacePath,
        promptMarker: options.promptMarker,
        replyMarker: options.replyMarker,
        eventTimeoutMs: options.eventTimeoutMs,
      })
      await initialized.connected
    },

    createWorkspace() {
      return requireOperations(operations).createWorkspace()
    },

    createSession(workspaceId) {
      return requireOperations(operations).createSession(workspaceId)
    },

    requireBinding(sessionId) {
      return requireOperations(operations).requireBinding(sessionId)
    },

    prompt(sessionId) {
      return requireOperations(operations).prompt(sessionId)
    },

    open(sessionId) {
      return requireOperations(operations).open(sessionId)
    },

    waitForEvents(sessionId) {
      return requireOperations(operations).waitForEvents(sessionId)
    },

    closeSession(sessionId) {
      return requireOperations(operations).closeSession(sessionId)
    },

    cleanup() {
      cleanupPromise ??= cleanupCandidateClient({ ctx, loop, disposeRemotes, restoreGlobals, lifecycle })
      return cleanupPromise
    },
  })
}

export async function loadCandidateClientModules(appDirectory) {
  const paths = resolveCandidateClientPaths(appDirectory)
  const cordis = await import(pathToFileURL(paths.cordis).href)
  const connection = await materializeClientBundle(paths.connection, {})
  const typert = await materializeClientBundle(paths.typert, { '@deepseek-ai/cordis': cordis })
  const gateway = await materializeClientBundle(paths.gateway, { '@deepseek-ai/cordis': cordis })
  const remotes = await materializeClientBundle(paths.remotes, {})
  const runtime = await materializeClientBundle(paths.runtime, {
    '@deepseek-ai/cordis': cordis,
    '@deepseek-ai/dsh-client-ui-slots': { SlotCore: class CandidateContractUnusedSlotCore {} },
  })
  return Object.freeze({ cordis, connection, typert, gateway, remotes, runtime })
}

async function materializeClientBundle(file, dependencies) {
  let registration
  const restoreWindow = replaceGlobal('window', {
    __ModuleLoader__: {
      load(value) {
        if (registration !== undefined) {
          throw new RuntimeSessionContractError('protocol-mismatch', '候选 client bundle 重复注册')
        }
        registration = value
      },
    },
  })
  try {
    bundleNonce += 1
    await import(`${pathToFileURL(file).href}?session-contract=${bundleNonce}`)
  } finally {
    restoreWindow()
  }
  if (typeof registration?.factory !== 'function') {
    throw new RuntimeSessionContractError('protocol-mismatch', '候选 client bundle 未注册工厂')
  }
  return registration.factory((specifier) => {
    if (!Object.hasOwn(dependencies, specifier)) {
      throw new RuntimeSessionContractError('protocol-mismatch', `候选 client bundle 依赖未授权：${specifier}`)
    }
    return dependencies[specifier]
  })
}

async function initializeCandidateClient(modules) {
  const ctx = new modules.cordis.Context()
  modules.connection.apply(ctx)
  modules.typert.apply(ctx)
  modules.gateway.apply(ctx)
  const disposeRemotes = await modules.remotes.apply(ctx)
  const sessions = new modules.runtime.SessionRuntime(ctx, ctx.connection.api, ctx.remote)
  ctx.typert.contexts.registerClient('agent', { identity: (candidate) => sessions.scopeOf(candidate) })
  const workspaces = new modules.runtime.WorkspaceRuntime(ctx, ctx.connection.api, sessions)
  let resolveConnected
  let rejectConnected
  const connected = new Promise((resolvePromise, rejectPromise) => {
    resolveConnected = resolvePromise
    rejectConnected = rejectPromise
  })
  const loop = ctx.connection.start({
    onMuxEnvelope: (envelope) => sessions.handleMuxEnvelope(envelope),
    onHostEnvelope: (envelope) => {
      sessions.handleHostEnvelope(envelope)
      workspaces.handleHostEnvelope(envelope)
      const frame = envelope.payload
      if (frame?.type === 'host/remote-event') ctx.remote.$dispatch(frame.event, frame.args)
    },
    onConnected: () => {
      try {
        sessions.handleConnected()
        workspaces.handleConnected()
        ctx.emit('connection/reset')
        resolveConnected()
      } catch (error) {
        rejectConnected(error)
      }
    },
    onStateChange: (state) => {
      if (state === 'reconnecting') sessions.handleDisconnected()
    },
  })
  return { ctx, loop, disposeRemotes, sessions, workspaces, connected }
}

async function cleanupCandidateClient({ ctx, loop, disposeRemotes, restoreGlobals, lifecycle }) {
  const failures = []
  for (const action of [
    async () => loop?.stop(),
    async () => disposeRemotes?.(),
    async () => ctx?.fiber?.dispose(),
    async () => restoreGlobals?.(),
    async () => lifecycle.cleanup(),
  ]) {
    try {
      await action()
    } catch (error) {
      failures.push(error)
    }
  }
  if (failures.length > 0) throw new AggregateError(failures, '候选 Session 客户端清理失败')
}

function installClientGlobals(origin, appDirectory) {
  const restorers = [replaceGlobal('location', new URL(origin))]
  if (typeof globalThis.WebSocket !== 'function') {
    const require = createRequire(join(appDirectory, 'package.json'))
    const ws = require('ws')
    restorers.push(replaceGlobal('WebSocket', ws.WebSocket ?? ws))
  }
  return () => {
    for (const restore of restorers.reverse()) restore()
  }
}

function replaceGlobal(name, value) {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, name)
  Object.defineProperty(globalThis, name, { configurable: true, writable: true, value })
  return () => {
    if (descriptor === undefined) delete globalThis[name]
    else Object.defineProperty(globalThis, name, descriptor)
  }
}

function requiredBinding(bindings, sessionId) {
  const binding = bindings.get(sessionId)
  if (binding === undefined) {
    throw new RuntimeSessionContractError('protocol-mismatch', 'Session 操作发生在 binding 验证之前')
  }
  return binding
}

function requireOperations(operations) {
  if (operations === undefined) {
    throw new RuntimeSessionContractError('protocol-mismatch', '候选客户端尚未完成连接')
  }
  return operations
}

function waitForSnapshotMarkers(session, promptMarker, replyMarker, timeoutMs) {
  return new Promise((resolvePromise, rejectPromise) => {
    let settled = false
    let unsubscribe = () => undefined
    const finish = (error) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      unsubscribe()
      if (error === undefined) resolvePromise()
      else rejectPromise(error)
    }
    const inspect = () => {
      try {
        const serialized = JSON.stringify(session.getSnapshot())
        if (serialized.includes(promptMarker) && serialized.includes(replyMarker)) finish()
      } catch {
        finish(new RuntimeSessionContractError('protocol-mismatch', 'Session snapshot 无法序列化'))
      }
    }
    const timer = setTimeout(() => {
      finish(new RuntimeSessionContractError('event-missing', '候选 Session 未观察到确定性事件'))
    }, timeoutMs)
    unsubscribe = session.subscribe(inspect)
    if (settled) unsubscribe()
    inspect()
  })
}
