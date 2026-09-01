import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SkillsPanel } from '../src/client/extension-center/SkillsPanel'
import { otherTarget, shaShort, type InstalledSkill, type SkillTarget } from '../src/client/extension-center/skills-api'
import type { DesktopBridgeLike } from '../src/client/desktop-bridge'

const SKILL: InstalledSkill = {
  name: 'pdf-tools',
  target: 'claude',
  path: 'C:/Users/me/.claude/skills/pdf-tools',
  skillMdSha256: 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
}

function notInstalled(target: SkillTarget): Error {
  return new Error(`skills_target_not_installed: ${target}`)
}

/** claude 恒已装;codex 按参数决定是否已装(未装时壳层上抛 skills_target_not_installed)。 */
function bridgeWith(options: {
  claudeSkills?: InstalledSkill[]
  codexInstalled?: boolean
  handlers?: Record<string, (payload?: Record<string, unknown>) => unknown>
}): DesktopBridgeLike {
  const claudeSkills = options.claudeSkills ?? [SKILL]
  const codexInstalled = options.codexInstalled ?? true
  return {
    request: vi.fn().mockRejectedValue(new Error('v1 不可用')),
    requestV2: vi.fn().mockImplementation((action: string, _context?: unknown, payload?: Record<string, unknown>) => {
      const handler = options.handlers?.[action]
      if (handler !== undefined) return Promise.resolve(handler(payload))
      if (action === 'skills.list') {
        if (payload?.target === 'claude') return Promise.resolve(claudeSkills)
        return codexInstalled ? Promise.resolve([]) : Promise.reject(notInstalled('codex'))
      }
      return Promise.reject(new Error(`未模拟的动作 ${action}`))
    }),
    dispose: () => undefined,
  }
}

describe('SkillsPanel', () => {
  it('按目标分组渲染已装 skills 与 sha 前 8 位', async () => {
    const bridge = bridgeWith({ claudeSkills: [SKILL], codexInstalled: false })
    render(<SkillsPanel bridge={bridge} />)
    const claudeGroup = await screen.findByRole('region', { name: 'Claude Skills' })
    expect(claudeGroup).toHaveTextContent('pdf-tools')
    expect(claudeGroup).toHaveTextContent(SKILL.skillMdSha256.slice(0, 8))
    const codexGroup = screen.getByRole('region', { name: 'Codex Skills' })
    expect(codexGroup).toHaveTextContent('未检测到 Codex')
    // 目标未装 → 该组无列表、同步按钮禁用
    expect(screen.queryByRole('list', { name: 'Codex Skills 列表' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '同步到 Codex' })).toBeDisabled()
  })

  it('从 ZIP 安装:未安装目标的勾选被禁用,提交走 skills.install.zip 并刷新', async () => {
    const install = vi.fn(() => [SKILL])
    const bridge = bridgeWith({
      codexInstalled: false,
      handlers: { 'skills.install.zip': install },
    })
    render(<SkillsPanel bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: '从 ZIP 安装' }))
    const dialog = await screen.findByRole('dialog', { name: '从 ZIP 安装 Skill' })
    expect(screen.getByRole('checkbox', { name: 'Codex' })).toBeDisabled()

    const installButton = screen.getByRole('button', { name: '安装' })
    expect(installButton).toBeDisabled()
    fireEvent.change(screen.getByRole('textbox', { name: 'ZIP 文件路径' }), { target: { value: 'C:/downloads/pdf-tools.zip' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Claude' }))
    expect(installButton).toBeEnabled()
    fireEvent.click(installButton)

    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('skills.install.zip', undefined, {
      zipPath: 'C:/downloads/pdf-tools.zip',
      targets: ['claude'],
    }))
    await waitFor(() => expect(dialog).not.toBeInTheDocument())
  })

  it('卸载走 skills.uninstall 并携带目标与名称', async () => {
    const bridge = bridgeWith({ handlers: { 'skills.uninstall': () => undefined } })
    render(<SkillsPanel bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: '卸载' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('skills.uninstall', undefined, {
      target: 'claude',
      name: 'pdf-tools',
    }))
  })

  it('同步到另一目标走 skills.sync;目标未安装时按钮禁用并提示', async () => {
    const bridge = bridgeWith({
      claudeSkills: [SKILL],
      handlers: { 'skills.sync': () => undefined },
    })
    render(<SkillsPanel bridge={bridge} />)
    const sync = await screen.findByRole('button', { name: '同步到 Codex' })
    expect(sync).toBeEnabled()
    fireEvent.click(sync)
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('skills.sync', undefined, {
      srcTarget: 'claude',
      dstTarget: 'codex',
      name: 'pdf-tools',
    }))
  })

  it('Codex 未安装时同步按钮禁用并给出未安装提示', async () => {
    const bridge = bridgeWith({ claudeSkills: [SKILL], codexInstalled: false })
    render(<SkillsPanel bridge={bridge} />)
    const sync = await screen.findByRole('button', { name: '同步到 Codex' })
    expect(sync).toBeDisabled()
    expect(sync.getAttribute('title')).toBe('Codex 未安装,无法同步')
  })

  it('安装动作失败时错误可见且对话框保留', async () => {
    const bridge = bridgeWith({
      handlers: {
        'skills.install.zip': () => { throw new Error('skills_zip_error: 坏包') },
      },
    })
    render(<SkillsPanel bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: '从 ZIP 安装' }))
    await screen.findByRole('dialog', { name: '从 ZIP 安装 Skill' })
    fireEvent.change(screen.getByRole('textbox', { name: 'ZIP 文件路径' }), { target: { value: 'C:/x.zip' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Claude' }))
    fireEvent.click(screen.getByRole('button', { name: '安装' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('skills_zip_error')
    expect(screen.getByRole('dialog', { name: '从 ZIP 安装 Skill' })).toBeInTheDocument()
  })
})

describe('skills-api helpers', () => {
  it('shaShort 取前 8 位,otherTarget 返回另一目标', () => {
    expect(shaShort(SKILL)).toBe('ba7816bf')
    expect(otherTarget('claude')).toBe('codex')
    expect(otherTarget('codex')).toBe('claude')
  })
})
