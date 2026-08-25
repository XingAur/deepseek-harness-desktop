import { afterEach, describe, expect, it, vi } from 'vitest'
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import {
  PackagedDesktopHarness,
  assertSessionRoundTripCoverage,
  conversationAssistantReplyCountExpression,
  conversationAssistantReplyIncreaseExpression,
  conversationSendEnabledExpression,
  createE2eWebViewUserDataFolder,
  e2eEnvironment,
  normalizeE2eCdpEndpoint,
  reserveLoopbackPort,
  selectWorkbenchCdpTarget,
  summarizeCdpTargetLookup,
  summarizeCdpTargets,
} from './desktop'

describe('PackagedDesktopHarness.continueConversation', () => {
  it('在连接工作台前拒绝空白续聊消息', async () => {
    const desktop = new PackagedDesktopHarness()

    await expect(desktop.continueConversation('  \t')).rejects.toThrow('继续会话消息不能为空')
  })

  it('已有 E2E_PONG 不足以满足续聊回复，必须新增助手回复节点', () => {
    document.body.innerHTML = `
      <section data-slot="conversation.session">
        <div data-chat-flow-kind="assistant-step">E2E_PONG</div>
        <div data-chat-flow-kind="user">E2E_PONG</div>
      </section>
    `
    const previous = evaluateWorkbenchExpression<number>(conversationAssistantReplyCountExpression('E2E_PONG'))

    expect(previous).toBe(1)
    expect(evaluateWorkbenchExpression<boolean>(conversationAssistantReplyIncreaseExpression('E2E_PONG', previous))).toBe(false)

    document.querySelector('[data-slot="conversation.session"]')?.insertAdjacentHTML(
      'beforeend',
      '<div data-chat-flow-kind="assistant-step">E2E_PONG</div>',
    )

    expect(evaluateWorkbenchExpression<boolean>(conversationAssistantReplyIncreaseExpression('E2E_PONG', previous))).toBe(true)
  })

  it('续聊仅在发送按钮启用后允许发送', () => {
    document.body.innerHTML = '<button aria-label="发送消息" disabled>发送</button>'

    expect(evaluateWorkbenchExpression<boolean>(conversationSendEnabledExpression())).toBe(false)
    const sendButton = document.querySelector('button[aria-label="发送消息"]')
    if (!(sendButton instanceof HTMLButtonElement)) throw new Error('测试发送按钮不存在')
    sendButton.disabled = false
    expect(evaluateWorkbenchExpression<boolean>(conversationSendEnabledExpression())).toBe(true)
  })
})

describe('PackagedDesktopHarness CDP target discovery', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('将 CDP 端口尚未监听的 ECONNREFUSED 交给调用方轮询', async () => {
    const cause = Object.assign(new Error('connect ECONNREFUSED 127.0.0.1:9229'), { code: 'ECONNREFUSED' })
    const connectionFailure = Object.assign(new TypeError('fetch failed'), { cause })
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(connectionFailure))

    await expect(readCdpTargets(new PackagedDesktopHarness())).resolves.toEqual({
      state: 'connection-refused',
      targets: [],
    })
  })

  it('不吞掉 CDP 返回的 JSON 解析异常', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockRejectedValue(new SyntaxError('invalid CDP target payload')),
    }))

    await expect(readCdpTargets(new PackagedDesktopHarness())).rejects.toThrow('invalid CDP target payload')
  })

  it('不将 CDP 的 HTTP 失败误判为服务尚未就绪', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }))

    await expect(readCdpTargets(new PackagedDesktopHarness())).rejects.toThrow('CDP target endpoint 返回 503')
  })

  it('区分连接拒绝与 HTTP 200 的空 target 列表，且摘要不含 URL 查询值', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: vi.fn().mockResolvedValue([]) }))

    await expect(readCdpTargets(new PackagedDesktopHarness())).resolves.toEqual({ state: 'ready', targets: [] })
    expect(summarizeCdpTargetLookup({ state: 'connection-refused', targets: [] })).toBe(
      '连接被拒绝（请检查 WebView2 是否收到 --remote-debugging-port 参数）',
    )
    expect(summarizeCdpTargetLookup({ state: 'ready', targets: [] })).toBe('endpoint HTTP 200，空 target 列表')
  })

  it('将本次 launch 分配的端口注入 WebView2 调试参数', () => {
    expect(e2eEnvironment(31_337).WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS).toBe('--remote-debugging-port=31337')
    expect(() => e2eEnvironment(0)).toThrow('CDP 端口无效')
  })

  it('为同一测试进程的多个 launch 分配不同的 loopback 端口', async () => {
    const first = await reserveLoopbackPort()
    const second = await reserveLoopbackPort()

    expect(first).toBeGreaterThan(0)
    expect(second).toBeGreaterThan(0)
    expect(second).not.toBe(first)
  })

  it('仅允许既有 WebDriver session 使用显式 loopback CDP endpoint', () => {
    expect(normalizeE2eCdpEndpoint('http://127.0.0.1:31337')).toBe('http://127.0.0.1:31337')
    expect(normalizeE2eCdpEndpoint('http://[::1]:31337')).toBe('http://[::1]:31337')
    expect(() => normalizeE2eCdpEndpoint('http://localhost:31337')).toThrow('loopback HTTP 地址')
    expect(() => normalizeE2eCdpEndpoint('https://127.0.0.1:31337')).toThrow('loopback HTTP 地址')
    expect(() => normalizeE2eCdpEndpoint('http://127.0.0.1:31337/?token=secret')).toThrow('loopback HTTP 地址')
  })

  it('既有 WebDriver session 未指定 endpoint 时兼容默认 9229，指定时使用安全覆盖', () => {
    const original = process.env.DSH_E2E_CDP_ENDPOINT
    try {
      delete process.env.DSH_E2E_CDP_ENDPOINT
      const legacy = new PackagedDesktopHarness({} as WebdriverIO.Browser)
      expect((legacy as unknown as { cdpEndpoint?: string }).cdpEndpoint).toBe('http://127.0.0.1:9229')

      process.env.DSH_E2E_CDP_ENDPOINT = 'http://127.0.0.1:31337'
      const configured = new PackagedDesktopHarness({} as WebdriverIO.Browser)
      expect((configured as unknown as { cdpEndpoint?: string }).cdpEndpoint).toBe('http://127.0.0.1:31337')
    } finally {
      if (original === undefined) delete process.env.DSH_E2E_CDP_ENDPOINT
      else process.env.DSH_E2E_CDP_ENDPOINT = original
    }
  })

  it('每次 launch 都在受控 E2E root 下创建独立 WebView2 用户数据目录，并由 root 范围清理', () => {
    const root = mkdtempSync(join(tmpdir(), 'dsh-e2e-webview2-'))
    try {
      const first = createE2eWebViewUserDataFolder(root)
      const second = createE2eWebViewUserDataFolder(root)
      expect(first).not.toBe(second)
      expect(first.startsWith(join(root, 'webview2'))).toBe(true)
      expect(second.startsWith(join(root, 'webview2'))).toBe(true)
      expect(existsSync(first)).toBe(true)
      expect(existsSync(second)).toBe(true)
      expect(e2eEnvironment(31_337, first).WEBVIEW2_USER_DATA_FOLDER).toBe(first)
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  it('允许 CDP 将工作台 iframe 以 page target 暴露，仍按完整 URL 选中', () => {
    const frameUrl = 'http://127.0.0.1:64785/?dsh-desktop-token=expected&dsh-desktop-generation-id=current'
    const target = {
      type: 'page',
      url: frameUrl,
      webSocketDebuggerUrl: 'ws://127.0.0.1:9229/devtools/page/current',
    }

    expect(selectWorkbenchCdpTarget([target], frameUrl)).toBe(target)
  })

  it('仍拒绝 URL 不同的 CDP target，并在摘要中隐藏查询值', () => {
    const frameUrl = 'http://127.0.0.1:64785/?dsh-desktop-token=expected'
    const other = {
      type: 'iframe',
      url: 'http://127.0.0.1:64785/?dsh-desktop-token=other-secret&dsh-desktop-session-id=old-session',
      webSocketDebuggerUrl: 'ws://127.0.0.1:9229/devtools/page/old',
    }

    expect(selectWorkbenchCdpTarget([other], frameUrl)).toBeUndefined()
    expect(summarizeCdpTargets([other])).toBe(
      'iframe http://127.0.0.1:64785/?dsh-desktop-session-id&dsh-desktop-token',
    )
    expect(summarizeCdpTargets([other])).not.toContain('other-secret')
  })
})

describe('assertSessionRoundTripCoverage', () => {
  const first = 'E2E 第一会话 Ω'
  const second = 'E2E 第二会话 二'
  const continuation = 'E2E 升级后继续 Ω'

  it('允许两条会话行覆盖三个 marker，其中一条包含续聊', () => {
    expect(() => assertSessionRoundTripCoverage(
      [first, second, continuation],
      new Map([[first, 0], [second, 1], [continuation, 1]]),
    )).not.toThrow()
  })

  it('仍拒绝未在实际会话内容中命中的 marker', () => {
    expect(() => assertSessionRoundTripCoverage(
      [first, second, continuation],
      new Map([[first, 0], [second, 1]]),
    )).toThrow(`找不到包含正文标记的会话：${continuation}`)
  })
})

function evaluateWorkbenchExpression<T>(expression: string): T {
  return Function(`return (${expression})`)() as T
}

function readCdpTargets(desktop: PackagedDesktopHarness): Promise<unknown> {
  ;(desktop as unknown as { cdpEndpoint?: string }).cdpEndpoint = 'http://127.0.0.1:31337'
  return (desktop as unknown as { cdpTargets(): Promise<unknown> }).cdpTargets()
}
