import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { UsagePanel } from '../src/client/extension-center/UsagePanel'
import {
  barWidthPercent, fetchUsageSummary, formatTokens, groupByDay, groupByModel,
  type UsageEntry, type UsageSummary,
} from '../src/client/extension-center/usage-api'
import type { DesktopBridgeLike } from '../src/client/desktop-bridge'

function bridgeWith(handlers: Record<string, (payload?: Record<string, unknown>) => unknown>): DesktopBridgeLike {
  return {
    request: vi.fn().mockRejectedValue(new Error('v1 不可用')),
    requestV2: vi.fn().mockImplementation((action: string, _context?: unknown, payload?: Record<string, unknown>) => {
      const handler = handlers[action]
      if (handler === undefined) return Promise.reject(new Error(`未模拟的动作 ${action}`))
      return Promise.resolve(handler(payload))
    }),
    dispose: () => undefined,
  }
}

const ENTRY = (day: string, model: string, requests: number, inputTokens: number, outputTokens: number): UsageEntry => ({
  day, model, requests, inputTokens, outputTokens, cacheCreationTokens: 0, cacheReadTokens: 0,
})

const SUMMARY: UsageSummary = {
  entries: [
    ENTRY('2026-08-29', 'deepseek-chat', 2, 100, 20),
    ENTRY('2026-08-30', 'deepseek-chat', 3, 200, 40),
    ENTRY('2026-08-30', 'deepseek-reasoner', 1, 50, 40),
  ],
  totals: { day: '', model: '', requests: 6, inputTokens: 350, outputTokens: 100, cacheCreationTokens: 5, cacheReadTokens: 6 },
  sessionsScanned: 3,
  filesScanned: 4,
  failures: [],
}

describe('UsagePanel', () => {
  it('渲染汇总卡并走 usage.summary 拉取数据', async () => {
    const bridge = bridgeWith({ 'usage.summary': () => SUMMARY })
    render(<UsagePanel bridge={bridge} />)
    const cards = await screen.findByRole('group', { name: '用量汇总' })
    expect(cards).toHaveTextContent('请求数')
    expect(cards).toHaveTextContent('输入 tokens')
    expect(cards).toHaveTextContent('输出 tokens')
    expect(cards).toHaveTextContent('扫描会话数')
    expect(cards).toHaveTextContent('6') // 请求数
    expect(cards).toHaveTextContent('350') // 输入 tokens(1 万以内原样展示)
    expect(cards).toHaveTextContent('100') // 输出 tokens
    expect(cards).toHaveTextContent('3') // 扫描会话数
    expect(screen.queryByRole('alert')).toBeNull()
    const calls = (bridge.requestV2 as ReturnType<typeof vi.fn>).mock.calls
    expect(calls[0][0]).toBe('usage.summary')
  })

  it('按日条目按输出 tokens 归一化渲染条形,按模型表格降序', async () => {
    const bridge = bridgeWith({ 'usage.summary': () => SUMMARY })
    const { container } = render(<UsagePanel bridge={bridge} />)
    const daySection = await screen.findByRole('region', { name: '按日用量' })
    // 两天跨模型合并:08-29 输出 20,08-30 输出 40+40=80,归一化后 25% 与 100%。
    expect(daySection).toHaveTextContent('2026-08-29')
    expect(daySection).toHaveTextContent('2026-08-30')
    const bars = within(daySection).getAllByRole('listitem')
    expect(bars).toHaveLength(2)
    const widths = [...container.querySelectorAll('.dshUsageDayBar')].map((bar) => (bar as HTMLElement).style.width)
    expect(widths).toEqual(['25%', '100%'])

    const modelSection = screen.getByRole('region', { name: '按模型用量' })
    const rows = within(modelSection).getAllByRole('row').slice(1) // 跳过表头
    expect(rows).toHaveLength(2)
    // 输出 tokens 相同(40)时按请求数降序:deepseek-chat(3) 在前。
    expect(rows[0]).toHaveTextContent('deepseek-chat')
    expect(rows[0]).toHaveTextContent('3')
    expect(rows[1]).toHaveTextContent('deepseek-reasoner')
  })

  it('failures 非空时显示警告条,空数据时显示空态', async () => {
    const failing: UsageSummary = { ...SUMMARY, failures: ['broken.jsonl：拒绝访问', 'huge.jsonl：读取失败', 'x.jsonl：IO'] }
    const bridge = bridgeWith({ 'usage.summary': () => failing })
    render(<UsagePanel bridge={bridge} />)
    const alerts = await screen.findAllByRole('alert')
    expect(alerts).toHaveLength(1)
    expect(alerts[0]).toHaveTextContent('部分3个文件无法统计')
    expect(alerts[0]).toHaveTextContent('broken.jsonl：拒绝访问')

    const emptyBridge = bridgeWith({ 'usage.summary': () => ({ ...SUMMARY, entries: [], totals: { ...SUMMARY.totals, requests: 0, inputTokens: 0, outputTokens: 0 } }) })
    render(<UsagePanel bridge={emptyBridge} />)
    await waitFor(() => expect(screen.getAllByText('还没有可用量数据;开始会话后点「刷新」。').length).toBeGreaterThan(0))
  })

  it('刷新按钮重新拉取汇总', async () => {
    const bridge = bridgeWith({ 'usage.summary': () => SUMMARY })
    render(<UsagePanel bridge={bridge} />)
    await screen.findByRole('group', { name: '用量汇总' })
    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() => {
      const calls = (bridge.requestV2 as ReturnType<typeof vi.fn>).mock.calls.filter(([action]) => action === 'usage.summary')
      expect(calls).toHaveLength(2)
    })
  })
})

describe('usage-api helpers', () => {
  it('groupByDay 跨模型合并并按日期升序,跳过汇总行', () => {
    const days = groupByDay(SUMMARY.entries)
    expect(days).toEqual([
      { day: '2026-08-29', requests: 2, inputTokens: 100, outputTokens: 20 },
      { day: '2026-08-30', requests: 4, inputTokens: 250, outputTokens: 80 },
    ])
    expect(groupByDay([{ ...ENTRY('2026-08-30', 'm', 1, 1, 1), day: '' }])).toEqual([])
  })

  it('groupByModel 按输出 tokens 降序并列时按请求数降序', () => {
    expect(groupByModel(SUMMARY.entries)).toEqual([
      { model: 'deepseek-chat', requests: 5, inputTokens: 300, outputTokens: 60 },
      { model: 'deepseek-reasoner', requests: 1, inputTokens: 50, outputTokens: 40 },
    ])
  })

  it('barWidthPercent 归一化到 2-100 且零最大值恒 0', () => {
    expect(barWidthPercent(50, 100)).toBe(50)
    expect(barWidthPercent(100, 100)).toBe(100)
    expect(barWidthPercent(1, 1000)).toBe(2)
    expect(barWidthPercent(10, 0)).toBe(0)
  })

  it('formatTokens 缩写 k/M', () => {
    expect(formatTokens(0)).toBe('0')
    expect(formatTokens(9999)).toBe('9999')
    expect(formatTokens(12_345)).toBe('12.3k')
    expect(formatTokens(1_000_000)).toBe('1M')
    expect(formatTokens(2_500_000)).toBe('2.5M')
  })

  it('fetchUsageSummary 走 usage.summary', async () => {
    const bridge = bridgeWith({ 'usage.summary': () => SUMMARY })
    await expect(fetchUsageSummary(bridge)).resolves.toEqual(SUMMARY)
  })
})
