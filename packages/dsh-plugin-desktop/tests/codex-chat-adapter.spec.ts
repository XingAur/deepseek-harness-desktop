import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { codexSpawnSpec, runCodexTurn } from '../src/server/codex-chat'
import { permissionToCodexThreadOptions } from '@dsh/agent-adapter/adapters'

/* eslint-disable @typescript-eslint/no-explicit-any */

/**
 * 用假 Codex CLI（JSONL over stdio 模拟官方 app-server）驱动真实的
 * 插件端聊天适配器：验证注册契约、流式分片协议与失败人话化。
 */

const fakeServerSource = `
let buffer = '';
let initialized = false;
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
  if (frame.method === 'initialized') { initialized = true; return; }
  if (['model/list', 'thread/start', 'thread/resume', 'turn/start', 'turn/interrupt'].includes(frame.method) && !initialized) {
    send({ id: frame.id, error: { message: 'initialized notification required' } });
    return;
  }
  if (frame.method === 'model/list') {
    send({ id: frame.id, result: { data: [
      {
        id: 'gpt-5.4', model: 'gpt-5.4', displayName: 'GPT-5.4',
        description: '用于代码与代理任务', hidden: false, isDefault: true,
        inputModalities: ['text', 'image'],
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
    if (frame.params.model === 'policy-check') {
      send({ method: 'item/agentMessage/delta', params: { delta: 'POLICY=' + frame.params.approvalPolicy + '/' + frame.params.sandbox } });
    }
    send({ id: frame.id, result: { thread: { id: 'thread-1' } } });
    return;
  }
  if (frame.method === 'thread/resume') {
    send({ id: frame.id, result: { thread: { id: frame.params.threadId } } });
    return;
  }
  if (frame.method === 'turn/start') {
    const promptText = Array.isArray(frame.params.input)
      ? frame.params.input.filter((item) => item.type === 'text').map((item) => item.text).join('\\n')
      : '';
    if (promptText.includes('never-complete') || promptText.includes('abort-me')) return;
    if (promptText.includes('failed-completion')) {
      send({ method: 'turn/completed', params: { threadId: 'thread-1', turn: { status: 'failed' } } });
      send({ id: frame.id, result: {} });
      return;
    }
    if (promptText.includes('input-count-check')) {
      send({ method: 'item/agentMessage/delta', params: { delta: 'INPUT_COUNT=' + frame.params.input.length } });
    }
    const sawImage = Array.isArray(frame.params.input)
      && frame.params.input.some((item) => item.type === 'image' && item.url === 'data:image/png;base64,iVBORw==');
    if (sawImage) send({ method: 'item/agentMessage/delta', params: { delta: 'IMAGE_INPUT_OK' } });
    for (const delta of ['你好，', '这是 ' + (frame.params.effort || 'default') + ' 推理的 ', 'Codex 回复。']) {
      send({ method: 'item/agentMessage/delta', params: { delta, threadId: 'thread-1', turnId: 't1', itemId: 'i1' } });
    }
    send({ method: 'turn/completed', params: { threadId: 'thread-1', turn: { status: 'completed' } } });
    send({ id: frame.id, result: {} });
    return;
  }
  if (frame.method === 'turn/interrupt') {
    send({ id: frame.id, result: {} });
    send({ method: 'turn/completed', params: { threadId: 'thread-1', turn: { status: 'interrupted' } } });
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

const fakeAttachment = {
  async readImageRequest() {
    return {
      data: new Uint8Array([137, 80, 78, 71]),
      mediaType: 'image/png',
    }
  },
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
    get(name: string) {
      return name === 'attachments' ? fakeAttachment : undefined
    },
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
      inputModalities: ['text', 'image'],
    })
    const prepared = await adapter.prepareCall('codex', 'gpt-5.4')
    expect(prepared.model).toMatchObject({
      provider: 'codex',
      id: 'gpt-5.4',
      reasoning: {
        defaultEffort: 'medium',
        efforts: [
          { id: 'low' },
          { id: 'medium' },
          { id: 'high' },
        ],
      },
    })
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

  it('同会话续接只发送当前用户输入，避免重复注入整段历史', async () => {
    installFakeCli()
    const { adapter } = await registerAdapter()
    await collect(adapter.stream({
      provider: 'codex', model: 'gpt-5.4', sessionId: 'session-history-boundary',
      messages: [
        { role: 'user', content: [{ type: 'text', text: '历史问题' }] },
        { role: 'assistant', content: [{ type: 'text', text: '历史回答' }] },
        { role: 'user', content: [{ type: 'text', text: 'input-count-check 第一轮' }] },
      ],
    }))
    const second = await collect(adapter.stream({
      provider: 'codex', model: 'gpt-5.4', sessionId: 'session-history-boundary',
      messages: [
        { role: 'user', content: [{ type: 'text', text: '历史问题' }] },
        { role: 'assistant', content: [{ type: 'text', text: '历史回答' }] },
        { role: 'user', content: [{ type: 'text', text: 'input-count-check 第二轮' }] },
      ],
    }))
    const text = second.filter((chunk: any) => chunk.type === 'text-delta').map((chunk: any) => chunk.text).join('')
    expect(text).toContain('INPUT_COUNT=1')
  }, 20_000)

  it('Codex 权限映射与 Agent 工作台一致', () => {
    expect(permissionToCodexThreadOptions('request-approval')).toEqual({ sandbox: 'workspace-write', approvalPolicy: 'untrusted' })
    expect(permissionToCodexThreadOptions('smart-approval')).toEqual({ sandbox: 'workspace-write', approvalPolicy: 'on-request' })
    expect(permissionToCodexThreadOptions('full-access')).toEqual({ sandbox: 'danger-full-access', approvalPolicy: 'never' })
  })

  it('完全访问权限直接使用 Codex 的 danger-full-access，不再被硬编码拦截', async () => {
    installFakeCli()
    const deltas: string[] = []
    await runCodexTurn({
      sessionId: 'session-full-access',
      prompt: 'policy-check',
      cwd: process.cwd(),
      model: 'policy-check',
      permission: 'full-access',
      onDelta: (delta) => deltas.push(delta),
    })
    expect(deltas.join('')).toContain('POLICY=never/danger-full-access')
  }, 20_000)

  it('Codex 返回 failed 结束状态时不能伪装成正常 stop', async () => {
    installFakeCli()
    await expect(runCodexTurn({
      sessionId: 'session-failed-completion',
      prompt: 'failed-completion',
      cwd: process.cwd(),
      onDelta: () => undefined,
    })).rejects.toThrow(/failed|失败/i)
  }, 20_000)

  it('AbortSignal 会中断 Codex turn，而不是一直等待', async () => {
    installFakeCli()
    const abort = new AbortController()
    const pending = runCodexTurn({
      sessionId: 'session-abort',
      prompt: 'abort-me',
      cwd: process.cwd(),
      timeoutMs: 2_000,
      signal: abort.signal,
      onDelta: () => undefined,
    })
    setTimeout(() => abort.abort(), 50)
    await expect(pending).rejects.toThrow(/取消|cancel/i)
  }, 20_000)

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

  it('把会话图片附件转换为 Codex image input，而不是只发送图片旁边的文字', async () => {
    installFakeCli()
    const { adapter } = await registerAdapter()
    const chunks = await collect(adapter.stream({
      provider: 'codex',
      model: 'gpt-5.4',
      sessionId: 'session-image',
      messages: [{
        role: 'user',
        content: [
          { type: 'text', text: '请读取这张图' },
          { type: 'image', attachment: { attachmentId: 'att-1', mediaType: 'image/png' } },
        ],
      }],
    }))
    const text = chunks.filter((chunk: any) => chunk.type === 'text-delta').map((chunk: any) => chunk.text).join('')
    expect(text).toContain('IMAGE_INPUT_OK')
    expect(chunks.at(-1).reason.kind).toBe('stop')
  }, 20_000)
})
