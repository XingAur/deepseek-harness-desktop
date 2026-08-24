import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { describe, expect, it, vi } from 'vitest'
import {
  createCandidateSessionOperations,
  resolveCandidateClientPaths,
} from './runtime-session-contract-client.mjs'

const CLIENT_PACKAGES = [
  ['@deepseek-ai/cordis', 'lib/index.js'],
  ['@deepseek-ai/dsh-client-connection', 'lib/client.js'],
  ['@deepseek-ai/dsh-typert-registry', 'lib/client.js'],
  ['@deepseek-ai/dsh-api-gateway', 'lib/client.js'],
  ['@deepseek-ai/dsh-api-remotes', 'lib/client.js'],
  ['@deepseek-ai/dsh-client-runtime', 'lib/client.js'],
] as const

function candidateFixture() {
  const root = mkdtempSync(join(tmpdir(), 'dsh-session-contract-client-'))
  const appDirectory = join(root, 'app')
  for (const [name, entrypoint] of CLIENT_PACKAGES) {
    const file = join(appDirectory, 'node_modules', ...name.split('/'), entrypoint)
    mkdirSync(dirname(file), { recursive: true })
    writeFileSync(file, '')
  }
  return { root, appDirectory }
}

function sessionFixture(bindingValue: unknown = undefined) {
  return {
    list: { getSnapshot: vi.fn(), subscribe: vi.fn() },
    binding: vi.fn(() => bindingValue),
    open: vi.fn(),
    clear: vi.fn(),
  }
}

describe('resolveCandidateClientPaths', () => {
  it('resolves every client implementation from the candidate app root', () => {
    const fixture = candidateFixture()
    try {
      const paths = resolveCandidateClientPaths(fixture.appDirectory)

      expect(Object.values(paths)).toHaveLength(CLIENT_PACKAGES.length)
      for (const file of Object.values(paths)) {
        expect(file.startsWith(resolve(fixture.appDirectory, 'node_modules'))).toBe(true)
      }
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })

  it('rejects relative candidate roots', () => {
    expect(() => resolveCandidateClientPaths('runtime/app')).toThrow(/绝对路径/)
  })

  it('rejects a missing candidate package without falling back to repository dependencies', () => {
    const fixture = candidateFixture()
    try {
      rmSync(join(fixture.appDirectory, 'node_modules', '@deepseek-ai', 'dsh-client-runtime'), {
        recursive: true,
        force: true,
      })

      expect(() => resolveCandidateClientPaths(fixture.appDirectory)).toThrow(/候选 Runtime 缺少客户端契约包/)
    } finally {
      rmSync(fixture.root, { recursive: true, force: true })
    }
  })
})

describe('createCandidateSessionOperations', () => {
  it('fails after one synchronous binding lookup', async () => {
    const sessions = sessionFixture()
    const workspaces = {
      create: vi.fn(async () => ({ workspaceId: 'w-1' })),
      connectWorkspace: vi.fn(async () => 's-1'),
    }
    const operations = createCandidateSessionOperations({
      sessions,
      workspaces,
      workspacePath: 'C:\\fixture\\工作区 Ω',
      promptMarker: 'SESSION_CONTRACT_PROMPT',
      replyMarker: 'SESSION_CONTRACT_PONG',
      eventTimeoutMs: 50,
    })

    await expect(operations.requireBinding('s-1')).rejects.toMatchObject({
      category: 'binding-missing',
    })
    expect(sessions.binding).toHaveBeenCalledTimes(1)
    expect(workspaces.connectWorkspace).not.toHaveBeenCalled()
  })

  it('uses prompt-before-open and observes both markers from the official event projection', async () => {
    let snapshot: unknown = { turns: [] }
    const listeners = new Set<() => void>()
    const session = {
      prompt: vi.fn(async () => ({ ok: true, value: { accepted: true } })),
      getSnapshot: vi.fn(() => snapshot),
      subscribe: vi.fn((listener: () => void) => {
        listeners.add(listener)
        return () => listeners.delete(listener)
      }),
    }
    const sessions = sessionFixture({ sessionId: 's-1', session })
    const workspaces = {
      create: vi.fn(async () => ({ workspaceId: 'w-1' })),
      connectWorkspace: vi.fn(async () => 's-1'),
    }
    const operations = createCandidateSessionOperations({
      sessions,
      workspaces,
      workspacePath: 'C:\\fixture\\工作区 Ω',
      promptMarker: 'SESSION_CONTRACT_PROMPT',
      replyMarker: 'SESSION_CONTRACT_PONG',
      eventTimeoutMs: 100,
    })

    const workspaceId = await operations.createWorkspace()
    const sessionId = await operations.createSession(workspaceId)
    await operations.requireBinding(sessionId)
    await operations.prompt(sessionId)
    await operations.open(sessionId)
    const waiting = operations.waitForEvents(sessionId)
    snapshot = {
      views: {
        get: () => new Map([
          ['prompt', { content: [{ text: 'SESSION_CONTRACT_PROMPT' }] }],
          ['reply', { content: new Set(['SESSION_CONTRACT_PONG']) }],
        ]),
      },
    }
    for (const listener of listeners) listener()
    await waiting
    await operations.closeSession(sessionId)

    expect(workspaces.create).toHaveBeenCalledWith({ path: 'C:\\fixture\\工作区 Ω' })
    expect(workspaces.connectWorkspace).toHaveBeenCalledWith('w-1')
    expect(session.prompt).toHaveBeenCalledWith(
      [{ type: 'text', text: 'SESSION_CONTRACT_PROMPT' }],
      'queue',
    )
    expect(session.prompt.mock.invocationCallOrder[0]).toBeLessThan(sessions.open.mock.invocationCallOrder[0])
    expect(sessions.open).toHaveBeenCalledWith('s-1')
    expect(sessions.clear).toHaveBeenCalledTimes(1)
  })

  it('classifies a missing reply event without exposing snapshot content', async () => {
    const session = {
      prompt: vi.fn(async () => ({ ok: true, value: { accepted: true } })),
      getSnapshot: vi.fn(() => ({ text: 'SESSION_CONTRACT_PROMPT private content' })),
      subscribe: vi.fn(() => () => undefined),
    }
    const sessions = sessionFixture({ sessionId: 's-1', session })
    const operations = createCandidateSessionOperations({
      sessions,
      workspaces: { create: vi.fn(), connectWorkspace: vi.fn() },
      workspacePath: 'C:\\fixture\\工作区 Ω',
      promptMarker: 'SESSION_CONTRACT_PROMPT',
      replyMarker: 'SESSION_CONTRACT_PONG',
      eventTimeoutMs: 5,
    })
    await operations.requireBinding('s-1')

    const error = await operations.waitForEvents('s-1').catch((cause: unknown) => cause)

    expect(error).toMatchObject({ category: 'event-missing' })
    expect(String(error)).not.toContain('private content')
  })
})
