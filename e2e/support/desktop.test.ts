import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  PackagedDesktopHarness,
  assertSessionRoundTripCoverage,
  conversationAssistantReplyCountExpression,
  conversationAssistantReplyIncreaseExpression,
  conversationSendEnabledExpression,
  selectWorkbenchCdpTarget,
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

    await expect(readCdpTargets(new PackagedDesktopHarness())).resolves.toEqual([])
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
  return (desktop as unknown as { cdpTargets(): Promise<unknown> }).cdpTargets()
}
