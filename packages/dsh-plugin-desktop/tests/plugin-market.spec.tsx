import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PluginMarket } from '../src/client/extensions/PluginMarket'
import type { DesktopBridgeLike } from '../src/client/desktop-bridge'

interface Fixture {
  page: {
    total: number
    offset: number
    categories: Array<{ id: string; count: number }>
    entries: Array<Record<string, unknown>>
  }
  jobs: Record<string, unknown>
  calls: Array<{ action: string; payload?: Record<string, unknown> }>
}

function bridgeFixture(fixture: Fixture): DesktopBridgeLike {
  return {
    requestV2: (async (action: string, _context: unknown, payload?: Record<string, unknown>) => {
      fixture.calls.push({ action, payload })
      if (action === 'plugin.catalog.list') return fixture.page
      if (action === 'plugin.install.start') {
        const id = String(payload?.pluginId)
        if (fixture.jobs[id] !== undefined) return fixture.jobs[id]
        fixture.jobs[id] = { jobRunning: true, jobOutput: [] }
        return fixture.jobs[id]
      }
      if (action === 'plugin.install.status') {
        const id = String(payload?.pluginId)
        return fixture.jobs[id] ?? { jobRunning: false, jobOutput: [] }
      }
      throw new Error(`unexpected action: ${action}`)
    }) as unknown as DesktopBridgeLike['requestV2'],
    request: (async () => { throw new Error('v1 unused') }) as unknown as DesktopBridgeLike['request'],
    dispose: () => undefined,
  }
}

const entry = (id: string, zh: string, category = 'tools') => ({
  id, displayName: id, repo: `https://github.com/${id}`, category, descriptionZh: zh, descriptionEn: '',
})

describe('PluginMarket', () => {
  it('renders the catalog with the security warning and featured dsh-market card', async () => {
    const fixture: Fixture = {
      page: {
        total: 2,
        offset: 0,
        categories: [{ id: 'tools', count: 2 }],
        entries: [
          entry('dsh-market/dsh-market', '完整插件市场'),
          entry('a/b', '支持余额查询'),
        ],
      },
      jobs: {},
      calls: [],
    }
    render(<PluginMarket bridge={bridgeFixture(fixture)} />)
    expect(await screen.findByText(/第三方代码/)).toBeInTheDocument()
    expect(await screen.findByText(/dsh-market —— 完整市场插件/)).toBeInTheDocument()
    expect(await screen.findByText(/2 个社区插件/)).toBeInTheDocument()
    expect(await screen.findByText('支持余额查询')).toBeInTheDocument()
  })

  it('searches through the bridge with a bounded page payload', async () => {
    const fixture: Fixture = {
      page: { total: 0, offset: 0, categories: [], entries: [] },
      jobs: {},
      calls: [],
    }
    render(<PluginMarket bridge={bridgeFixture(fixture)} />)
    await screen.findByPlaceholderText('搜索插件：名称、分类或描述…')
    fireEvent.change(screen.getByPlaceholderText('搜索插件：名称、分类或描述…'), { target: { value: '余额' } })
    await waitFor(() => {
      const search = fixture.calls.find((call) => call.action === 'plugin.catalog.list' && call.payload?.query === '余额')
      expect(search).toBeDefined()
      expect(search?.payload?.limit).toBe(30)
    })
  })

  it('starts an install and reflects the finished state', async () => {
    const fixture: Fixture = {
      page: { total: 1, offset: 0, categories: [], entries: [entry('a/b', '工具')] },
      jobs: {},
      calls: [],
    }
    const bridge = bridgeFixture(fixture)
    render(<PluginMarket bridge={bridge} />)
    fireEvent.click(await screen.findByText('安装'))
    await waitFor(() => {
      const start = fixture.calls.find((call) => call.action === 'plugin.install.start')
      expect(start?.payload).toEqual({ pluginId: 'a/b' })
    })
    // 模拟轮询到任务完成（组件每 2s 轮询一次运行中的任务）
    fixture.jobs['a/b'] = { jobRunning: false, jobFinished: true, jobSuccess: true, jobOutput: [] }
    await waitFor(() => screen.getByText('已安装 · 下次会话生效'), { timeout: 4000 })
  })

  it('offers retry with the captured failure log when an install fails', async () => {
    const fixture: Fixture = {
      page: { total: 1, offset: 0, categories: [], entries: [entry('x/y', '工具')] },
      jobs: { 'x/y': { jobRunning: false, jobFinished: true, jobSuccess: false, jobOutput: ['安装超时（15 分钟）。可以稍后重试'] } },
      calls: [],
    }
    render(<PluginMarket bridge={bridgeFixture(fixture)} />)
    expect(await screen.findByText(/安装超时/)).toBeInTheDocument()
    expect(screen.getByText('重试')).toBeInTheDocument()
  })

  it('shows load-more only when more pages remain', async () => {
    const entries = Array.from({ length: 30 }, (_, index) => entry(`o/p-${index}`, 'x'))
    const fixture: Fixture = {
      page: { total: 45, offset: 0, categories: [], entries },
      jobs: {},
      calls: [],
    }
    render(<PluginMarket bridge={bridgeFixture(fixture)} />)
    expect(await screen.findByText(/加载更多（还有 15 个）/)).toBeInTheDocument()
  })

  it('刷新按钮请求 Rust 重新读取目录，而不是继续使用旧缓存', async () => {
    const fixture: Fixture = {
      page: { total: 1, offset: 0, categories: [], entries: [entry('a/b', '工具')] },
      jobs: {},
      calls: [],
    }
    render(<PluginMarket bridge={bridgeFixture(fixture)} />)
    await screen.findAllByText('a/b')
    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() => {
      expect(fixture.calls.some((call) => call.action === 'plugin.catalog.list' && call.payload?.refresh === true)).toBe(true)
    })
  })
})
