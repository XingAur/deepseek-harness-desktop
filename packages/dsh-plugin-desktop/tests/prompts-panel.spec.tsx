import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, type Mock } from 'vitest'
import { PromptsPanel, renderMarkdownPreview } from '../src/client/extension-center/PromptsPanel'
import { parsePastedPresets } from '../src/client/extension-center/prompts-api'
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

const STATUS = [
  { target: 'claude', installed: true, liveFileExists: true, activePresetId: 'p1', liveContentSha256: 'aa', matchesActivePreset: true, oversized: false },
  { target: 'codex', installed: false, liveFileExists: false, activePresetId: null, liveContentSha256: null, matchesActivePreset: false, oversized: false },
  { target: 'dsh', installed: true, liveFileExists: false, activePresetId: null, liveContentSha256: null, matchesActivePreset: false, oversized: false },
]

const LIST = [{ id: 'p1', title: '默认提示词', updatedAt: 1, activatedTargets: ['claude' as const] }]
const PRESET = { id: 'p1', title: '默认提示词', content: '# 正文', createdAt: 0, updatedAt: 1 }

describe('PromptsPanel', () => {
  it('加载目标状态与预设列表并渲染状态条', async () => {
    const bridge = bridgeWith({
      'prompts.status': () => STATUS,
      'prompts.list': () => [{ id: 'p1', title: '默认提示词', updatedAt: 1, activatedTargets: ['claude'] }],
    })
    render(<PromptsPanel bridge={bridge} />)
    expect(await screen.findByText('Claude')).toBeInTheDocument()
    expect(await screen.findByText('默认提示词')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Codex' })).toBeDisabled()
    expect(screen.queryByText(/外部修改/)).not.toBeInTheDocument()
  })

  it('live 哈希与激活预设不一致时亮出外部修改徽标', async () => {
    const bridge = bridgeWith({
      'prompts.status': () => STATUS.map((entry) => entry.target === 'claude' ? { ...entry, matchesActivePreset: false } : entry),
      'prompts.list': () => [],
    })
    render(<PromptsPanel bridge={bridge} />)
    expect(await screen.findByText(/外部修改/)).toBeInTheDocument()
  })

  it('预设池为空且存在非空 live 文件时弹首启导入对话框', async () => {
    const bridge = bridgeWith({
      'prompts.status': () => STATUS.map((entry) => entry.target === 'claude' ? { ...entry, liveFileExists: true, activePresetId: null } : entry),
      'prompts.list': () => [],
    })
    render(<PromptsPanel bridge={bridge} />)
    expect(await screen.findByRole('dialog', { name: '导入现有提示词' })).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Claude' }))
    fireEvent.click(screen.getByRole('button', { name: '导入' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('prompts.import', undefined, { targets: ['claude'] }))
  })

  it('选择预设进入编辑器,保存走 prompts.save 并刷新', async () => {
    const bridge = bridgeWith({
      'prompts.status': () => STATUS,
      'prompts.list': () => LIST,
      'prompts.get': () => PRESET,
      'prompts.save': () => ({ kind: 'saved', preset: { ...PRESET, content: '# 新正文', updatedAt: 2 }, projected: STATUS }),
    })
    render(<PromptsPanel bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: /默认提示词/ }))
    const editor = await screen.findByRole('textbox', { name: '预设内容' })
    expect(editor).toHaveValue('# 正文')
    fireEvent.change(editor, { target: { value: '# 新正文' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('prompts.save', undefined, { presetId: 'p1', title: '默认提示词', content: '# 新正文' }))
    expect(await screen.findByText(/预览/)).toBeInTheDocument()
  })

  it('保存返回冲突时弹对话框,选定候选走 prompts.resolve-conflict', async () => {
    const bridge = bridgeWith({
      'prompts.status': () => STATUS,
      'prompts.list': () => LIST,
      'prompts.get': () => ({ ...PRESET, content: 'v1' }),
      'prompts.save': () => ({ kind: 'backfill-conflict', presetId: 'p1', candidates: [
        { target: 'claude', content: 'claude 端内容', updatedAt: 5 },
        { target: 'codex', content: 'codex 端内容', updatedAt: 6 },
      ] }),
      'prompts.resolve-conflict': () => ({ kind: 'saved', preset: { ...PRESET, content: 'claude 端内容', updatedAt: 7 }, projected: STATUS }),
    })
    render(<PromptsPanel bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: /默认提示词/ }))
    fireEvent.click(await screen.findByRole('button', { name: '保存' }))
    expect(await screen.findByRole('dialog', { name: '检测到外部修改' })).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('radio', { name: /Claude/ }))
    fireEvent.click(screen.getByRole('button', { name: '以此为准并保存' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('prompts.resolve-conflict', undefined, { presetId: 'p1', title: '默认提示词', content: 'claude 端内容' }))
  })

  it('勾选激活目标走 prompts.activate,停用走 prompts.deactivate', async () => {
    const bridge = bridgeWith({
      'prompts.status': () => STATUS.map((entry) => ({ ...entry, activePresetId: entry.target === 'claude' ? 'p1' : null })),
      'prompts.list': () => LIST,
      'prompts.get': () => PRESET,
      'prompts.activate': () => ({ kind: 'ok', status: STATUS[2] }),
      'prompts.deactivate': () => STATUS[0],
    })
    render(<PromptsPanel bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: /默认提示词/ }))
    const group = await screen.findByRole('group', { name: '激活目标' })
    fireEvent.click(group.querySelector('input[value="dsh"]') as HTMLElement)
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('prompts.activate', undefined, { presetId: 'p1', target: 'dsh' }))
    fireEvent.click(screen.getByRole('button', { name: '停用已激活目标' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('prompts.deactivate', undefined, { target: 'claude' }))
  })

  it('粘贴 JSON 导入:逐条 prompts.save 新建,完成后重新拉取列表', async () => {
    const bridge = bridgeWith({
      'prompts.status': () => STATUS,
      'prompts.list': () => LIST,
      'prompts.save': () => ({ kind: 'saved', preset: { ...PRESET, updatedAt: 9 }, projected: STATUS }),
    })
    render(<PromptsPanel bridge={bridge} />)
    expect(await screen.findByText('默认提示词')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '从文件导入' }))
    fireEvent.click(screen.getByRole('button', { name: '粘贴 JSON' }))
    fireEvent.change(
      await screen.findByRole('textbox', { name: '粘贴 JSON' }),
      { target: { value: '[{"title":"A","content":"CA"},{"name":"B","content":"CB"}]' } },
    )
    fireEvent.click(screen.getByRole('button', { name: '解析并导入' }))
    const request = bridge.requestV2 as unknown as Mock
    await waitFor(() => {
      expect(request.mock.calls.filter(([action]) => action === 'prompts.save')).toHaveLength(2)
    })
    expect(request).toHaveBeenCalledWith('prompts.save', undefined, { title: 'A', content: 'CA' })
    expect(request).toHaveBeenCalledWith('prompts.save', undefined, { title: 'B', content: 'CB' })
    // 初始挂载拉取一次,粘贴导入完成后 refreshAll 再拉取一次
    await waitFor(() => {
      expect(request.mock.calls.filter(([action]) => action === 'prompts.list')).toHaveLength(2)
    })
  })

  it('粘贴非法 JSON:对话框内显示原因且不调用 prompts.save', async () => {
    const bridge = bridgeWith({
      'prompts.status': () => STATUS,
      'prompts.list': () => LIST,
    })
    render(<PromptsPanel bridge={bridge} />)
    expect(await screen.findByText('默认提示词')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '从文件导入' }))
    fireEvent.click(screen.getByRole('button', { name: '粘贴 JSON' }))
    fireEvent.change(
      await screen.findByRole('textbox', { name: '粘贴 JSON' }),
      { target: { value: '{oops' } },
    )
    fireEvent.click(screen.getByRole('button', { name: '解析并导入' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('不是有效的 JSON')
    expect(bridge.requestV2).not.toHaveBeenCalledWith('prompts.save', expect.anything(), expect.anything())
  })
})

describe('renderMarkdownPreview', () => {
  it('渲染 markdown 并剥离脚本注入', () => {
    const html = renderMarkdownPreview('# 标题\n\n<script>window.__xss=1</script>\n\n<img src=x onerror="window.__y=1">')
    expect(html).toContain('<h1>')
    expect(html).not.toContain('<script')
    expect(html).not.toContain('onerror')
  })
})

describe('parsePastedPresets', () => {
  const OVER_LIMIT_CONTENT = 'x'.repeat(24 * 1024 + 1)

  interface PasteCase {
    name: string
    text: string
    want:
      | { ok: true; titles: string[]; skipped?: number }
      | { ok: false; reason: string }
  }

  const cases: PasteCase[] = [
    {
      name: '单对象 {title, content}(title 去 trim)',
      text: '{"title":"  你好  ","content":"CA"}',
      want: { ok: true, titles: ['你好'] },
    },
    {
      name: '对象数组逐条解析',
      text: '[{"title":"A","content":"CA"},{"title":"B","content":"CB"}]',
      want: { ok: true, titles: ['A', 'B'] },
    },
    {
      name: 'cc-switch name 形态',
      text: '[{"name":"A","content":"CA"}]',
      want: { ok: true, titles: ['A'] },
    },
    {
      name: 'cc-switch 宽容映射:name+prompt 与 title+value',
      text: '[{"name":"A","prompt":"PA"},{"title":"B","value":"VB"}]',
      want: { ok: true, titles: ['A', 'B'] },
    },
    {
      name: '坏 JSON 返回固定原因',
      text: 'not json{{',
      want: { ok: false, reason: '不是有效的 JSON' },
    },
    {
      name: '全部无法映射出 title+content',
      text: '[{"foo":1},{"title":123},{"content":"只有内容"}]',
      want: { ok: false, reason: '没有可识别的 {标题, 内容} 条目' },
    },
    {
      name: '空数组视为没有可识别条目',
      text: '[]',
      want: { ok: false, reason: '没有可识别的 {标题, 内容} 条目' },
    },
    {
      name: '顶层标量形状拒绝',
      text: '"just a string"',
      want: { ok: false, reason: '没有可识别的 {标题, 内容} 条目' },
    },
    {
      name: '超限条目跳过并计入 skipped',
      text: `[{"title":"big","content":"${OVER_LIMIT_CONTENT}"},{"title":"ok","content":"small"}]`,
      want: { ok: true, titles: ['ok'], skipped: 1 },
    },
    {
      name: '全部超限时失败并汇总条数',
      text: `[{"title":"big","content":"${OVER_LIMIT_CONTENT}"}]`,
      want: { ok: false, reason: '全部 1 条条目超过 24 KiB 上限,已跳过' },
    },
    {
      name: 'content 相同的重复条目去重(取首条)',
      text: '[{"title":"A","content":"same"},{"name":"B","content":"same"}]',
      want: { ok: true, titles: ['A'] },
    },
    {
      name: '空标题条目跳过,其余照常',
      text: '[{"title":"   ","content":"CA"},{"title":"B","content":"CB"}]',
      want: { ok: true, titles: ['B'] },
    },
    {
      name: '标题超长截断到 200 字符而非拒收',
      text: JSON.stringify([{ title: 't'.repeat(250), content: 'C' }]),
      want: { ok: true, titles: ['t'.repeat(200)] },
    },
  ]

  it.each(cases)('$name', ({ text, want }) => {
    const result = parsePastedPresets(text)
    if (want.ok) {
      expect(result).toMatchObject({ ok: true, skipped: want.skipped ?? 0 })
      if (!result.ok) return
      expect(result.presets.map((preset) => preset.title)).toEqual(want.titles)
      expect(result.presets.map((preset) => typeof preset.content)).toEqual(want.titles.map(() => 'string'))
    } else {
      expect(result).toEqual({ ok: false, reason: want.reason })
    }
  })
})
