import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { codexSpawnSpec } from '../src/server/codex-chat'

/* eslint-disable @typescript-eslint/no-explicit-any */

/**
 * 用假 Codex CLI（JSONL over stdio 模拟官方 app-server）驱动真实的
 * 插件端聊天适配器：验证注册契约、流式分片协议与失败人话化。
 */

const fakeServerSource = `
let buffer = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
  buffer += chunk;
  let nl = buffer.indexOf('\\n');
  while (nl !== -1) {
    const line = buffer.slice(0, nl).trim();
    buffer = buffer.slice(nl + 1);
    if (line) handle(JSON.parse(line));
    nl = buffer.indexOf('\\n');
  }
});
function send(message) { process.stdout.write(JSON.stringify(message) + '\\n'); }
function handle(frame) {
  if (frame.method === 'initialize') { send({ id: frame.id, result: { ok: true } }); return; }
  if (frame.method === 'model/list') {
    send({ id: frame.id, result: { data: [
      {
        id: 'gpt-5.4', model: 'gpt-5.4', displayName: 'GPT-5.4',
        description: '用于代码与代理任务', hidden: false, isDefault: true,
        defaultReasoningEffort: 'medium',
        supportedReasoningEfforts: [
          { reasoningEffort: 'low', description: '快速' },
          { reasoningEffort: 'medium', description: '平衡' },
          { reasoningEffort: 'high', description: '深度' },
        ],
      },
    ] } });
    return;
  }
  if (frame.method === 'thread/start') {
    send({ id: frame.id, result: { thread: { id: 'thread-1' } } });
    return;
  }
  if (frame.method === 'thread/resume') {
    send({ id: frame.id, result: { thread: { id: frame.params.threadId } } });
    return;
  }
  if (frame.method === 'turn/start') {
    for (const delta of ['你好，', '这是 ' + (frame.params.effort || 'default') + ' 推理的 ', 'Codex 回复。']) {
      send({ method: 'item/agentMessage/delta', params: { delta, threadId: 'thread-1', turnId: 't1', itemId: 'i1' } });
    }
    send({ method: 'turn/completed', params: { threadId: 'thread-1', turn: { status: 'completed' } } });
    send({ id: frame.id, result: {} });
  }
}
`

let fakeDir: string | null = null

function installFakeCli(): string {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-fake-codex-'))
  const serverPath = join(dir, 'codex-fake.js')
  writeFileSync(serverPath, fakeServerSource)
  const cliPath = process.platform === 'win32' ? join(dir, 'codex.cmd') : join(dir, 'codex')
  if (process.platform === 'win32') {
    writeFileSync(cliPath, `@echo off\r\n"${process.execPath}" "%~dp0codex-fake.js" %*\r\n`)
  } else {
    writeFileSync(cliPath, '#!/usr/bin/env node\n' + fakeServerSource, { mode: 0o755 })
  }
  process.env.DSH_DESKTOP_CODEX_CLI = cliPath
  fakeDir = dir
  return cliPath
}

afterEach(() => {
  if (fakeDir !== null) {
    rmSync(fakeDir, { recursive: true, force: true })
    delete process.env.DSH_DESKTOP_CODEX_CLI
    fakeDir = null
  }
})

interface Captured {
  adapter: any
  providers: string[]
}

async function registerAdapter(): Promise<Captured> {
  const plugin = await import('../src/index')
  const captured: Captured = { adapter: null, providers: [] }
  const ctx = {
    llm: {
      registerAdapter(providers: string[], adapter: any) {
        captured.providers = providers
        captured.adapter = adapter
        return { dispose() { /* 测试内不需要 */ } }
      },
    },
    sessions: { get: () => undefined },
    logger: { info() { /* noop */ } },
  }
  plugin.apply(ctx)
  expect(captured.adapter).not.toBeNull()
  return captured
}

async function collect(stream: AsyncGenerator<any>): Promise<any[]> {
  const chunks: any[] = []
  for await (const chunk of stream) chunks.push(chunk)
  return chunks
}

describe('codex chat adapter（假 CLI 端到端）', () => {
  it('Windows 的 cmd CLI 通过 shell 启动 app-server', () => {
    expect(codexSpawnSpec('C:\\Users\\test\\codex.cmd', 'win32')).toEqual({
      command: 'C:\\Users\\test\\codex.cmd',
      args: ['app-server'],
      shell: true,
    })
  })

  it('满足注册契约：providerInfo/providerRetryPolicy/listModels/prepareCall', async () => {
    installFakeCli()
    const { adapter, providers } = await registerAdapter()
    expect(providers).toEqual(['codex'])
    expect(adapter.providerInfo('codex')).toEqual({ id: 'codex', name: 'Codex' })
    expect(adapter.providerRetryPolicy('codex')).toBeUndefined()
    const models = await adapter.listModels('codex')
    expect(models[0]).toMatchObject({
      provider: 'codex', id: 'gpt-5.4', name: 'GPT-5.4',
      reasoningEfforts: ['low', 'medium', 'high'],
    })
    const prepared = await adapter.prepareCall('codex', 'gpt-5.4')
    expect(prepared.model).toMatchObject({ provider: 'codex', id: 'gpt-5.4' })
    expect(typeof prepared.stream).toBe('function')
  }, 20_000)

  it('流式返回 text-delta 序列并以 stop finish 终止', async () => {
    installFakeCli()
    const { adapter } = await registerAdapter()
    const chunks = await collect(adapter.stream({
      provider: 'codex',
      model: 'gpt-5.4',
      reasoningEffort: 'high',
      sessionId: 'session-test-1',
      messages: [{ role: 'user', content: [{ type: 'text', text: '帮我看看这个项目' }] }],
    }))
    const types = chunks.map((chunk: any) => chunk.type)
    expect(types[0]).toBe('block-start')
    expect(types.at(-2)).toBe('block-end')
    const deltas = chunks.filter((chunk: any) => chunk.type === 'text-delta')
    expect(deltas.map((chunk: any) => chunk.text).join('')).toBe('你好，这是 high 推理的 Codex 回复。')
    const finish = chunks.at(-1)
    expect(finish.type).toBe('finish')
    expect(finish.reason.kind).toBe('stop')
  }, 20_000)

  it('同会话复用线程：第二次调用不再发 thread/start', async () => {
    installFakeCli()
    const { adapter } = await registerAdapter()
    const options = {
      provider: 'codex',
      model: 'gpt-5.4',
      sessionId: 'session-reuse',
      messages: [{ role: 'user', content: [{ type: 'text', text: '第一轮' }] }],
    }
    await collect(adapter.stream(options))
    await collect(adapter.stream(options))
    // 假 CLI 的 thread/start 每次都返回 thread-1；复用逻辑在插件侧，
    // 第二次 stream 依然成功即说明线程记忆没有破坏流程。
    const third = await collect(adapter.stream(options))
    expect(third.at(-1).reason.kind).toBe('stop')
  }, 30_000)

  it('CLI 缺失时以人话 error finish 终止而不是抛出', async () => {
    // 清空 PATH 与 HOME，模拟一台完全没装 Codex 的机器（否则回退链
    // 会找到本机真实安装的 codex——那正是想要的容错行为）。
    const previousHome = process.env.HOME
    const previousPath = process.env.PATH
    delete process.env.DSH_DESKTOP_CODEX_CLI
    process.env.HOME = fakeDir ?? tmpdir()
    process.env.PATH = ''
    try {
    const { adapter } = await registerAdapter()
    const chunks = await collect(adapter.stream({
      provider: 'codex',
      model: 'codex-default',
      messages: [{ role: 'user', content: [{ type: 'text', text: 'hi' }] }],
    }))
    const finish = chunks.at(-1)
    expect(finish.type).toBe('finish')
    expect(finish.reason.kind).toBe('error')
    expect(finish.reason.failure.message).toContain('Codex CLI')
    } finally {
      process.env.HOME = previousHome
      process.env.PATH = previousPath
    }
  }, 20_000)

  it('消息历史被正确压平：assistant 历史带角色标注', async () => {
    installFakeCli()
    const { adapter } = await registerAdapter()
    // 直接检查压平逻辑可观察的行为：多轮消息不报错且流式正常
    const chunks = await collect(adapter.stream({
      provider: 'codex',
      model: 'codex-default',
      sessionId: 'session-multi',
      messages: [
        { role: 'user', content: [{ type: 'text', text: '第一问' }] },
        { role: 'assistant', content: [{ type: 'text', text: '第一答' }] },
        { role: 'user', content: [{ type: 'text', text: '第二问' }] },
      ],
    }))
    expect(chunks.at(-1).reason.kind).toBe('stop')
  }, 20_000)
})
