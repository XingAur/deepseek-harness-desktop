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

  it('rejects a relative or reserved Windows project draft', () => {
    expect(() => projectDraft({ idea: '博客', path: '.\\blog', profileId: 'p-1', permissionMode: 'workspace-write' })).toThrow('绝对路径')
    expect(() => projectDraft({ idea: '博客', path: 'C:\\CON', profileId: 'p-1', permissionMode: 'workspace-write' })).toThrow('保留名称')
    expect(() => projectDraft({ idea: '博客', path: 'C:\\', profileId: 'p-1', permissionMode: 'workspace-write' })).toThrow('根目录')
  })

  it('normalizes a confirmed draft without touching the filesystem', () => {
    expect(projectDraft({
      idea: '  构建一个博客  ', path: ' C:/code/blog/ ', profileId: 'p-1', permissionMode: 'workspace-write',
    })).toEqual({
      idea: '构建一个博客',
      normalizedPath: 'C:\\code\\blog',
      profileId: 'p-1',
      permissionMode: 'workspace-write',
      proposedName: '构建一个博客',
      commandCategories: ['package-manager', 'build', 'test'],
      createDirectory: false,
    })
  })
})
