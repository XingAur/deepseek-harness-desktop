import { describe, expect, it } from 'vitest'
import { projectCards, projectDraft } from '../src/client/project-model'

describe('project model', () => {
  it('maps workspaces to recent-first project cards without inventing another id', () => {
    const cards = projectCards([
      { workspaceId: 'w-1', path: 'C:\\项目\\旧', title: '旧', sessionIds: [], createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z' },
      { workspaceId: 'w-2', path: 'C:\\项目\\新', title: '新', sessionIds: ['s-2'], createdAt: '2026-01-02T00:00:00Z', updatedAt: '2026-01-03T00:00:00Z' },
    ])

    expect(cards.map((card) => card.id)).toEqual(['w-2', 'w-1'])
    expect(cards[0].path).toBe('C:\\项目\\新')
    expect(cards[0].sessionIds).toEqual(['s-2'])
  })

  it('sorts pinned projects first and keeps recent order inside each group', () => {
    const workspaces = [
      { workspaceId: 'w-old', path: 'C:\\项目\\旧', title: '旧', sessionIds: [], createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z' },
      { workspaceId: 'w-new', path: 'C:\\项目\\新', title: '新', sessionIds: [], createdAt: '2026-01-02T00:00:00Z', updatedAt: '2026-01-03T00:00:00Z' },
    ]

    const cards = projectCards(workspaces, {
      'w-old': { cover: 'forest', pinned: true, updatedAt: '2026-08-20T00:00:00Z' },
    })

    expect(cards.map((card) => card.id)).toEqual(['w-old', 'w-new'])
    expect(cards[0].cover).toBe('forest')
    expect(cards[1].cover).toMatch(/^(aurora-blue|sunset|forest|graphite|violet)$/)
  })

  it('builds a fixed-permission draft from a backend-prepared location', () => {
    expect(projectDraft({
      idea: '  做一个记账应用  ',
      profileId: 'p-1',
      location: {
        projectName: '记账应用',
        suggestedPath: 'C:\\Users\\test\\Documents\\DeepSeek Harness\\Projects\\记账应用',
      },
    })).toMatchObject({
      idea: '做一个记账应用',
      proposedName: '记账应用',
      normalizedPath: 'C:\\Users\\test\\Documents\\DeepSeek Harness\\Projects\\记账应用',
      permissionMode: 'workspace-write',
      createDirectory: true,
    })
  })

  it('rejects an empty idea, profile, or malformed prepared location', () => {
    const location = {
      projectName: 'demo',
      suggestedPath: 'C:\\Users\\test\\Documents\\DeepSeek Harness\\Projects\\demo',
    }
    expect(() => projectDraft({ idea: '', profileId: 'p-1', location })).toThrow('描述')
    expect(() => projectDraft({ idea: 'demo', profileId: '', location })).toThrow('Profile')
    expect(() => projectDraft({ idea: 'demo', profileId: 'p-1', location: { ...location, projectName: '' } })).toThrow('项目名称')
    expect(() => projectDraft({ idea: 'demo', profileId: 'p-1', location: { ...location, suggestedPath: '.\\demo' } })).toThrow('绝对路径')
  })
})
