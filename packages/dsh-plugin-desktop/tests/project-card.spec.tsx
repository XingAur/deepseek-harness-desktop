import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ProjectCard } from '../src/client/ProjectCard'

function renderProjectCard() {
  const callbacks = {
    onSelect: vi.fn(),
    onOpen: vi.fn(async () => undefined),
    onRename: vi.fn(async () => undefined),
    onCoverChange: vi.fn(async () => undefined),
    onPinChange: vi.fn(async () => undefined),
    onDelete: vi.fn(),
  }
  render(
    <ProjectCard
      card={{
        id: 'w-demo', path: 'C:\\code\\demo', title: 'demo', sessionIds: [],
        createdAt: '2026-08-20T00:00:00Z', updatedAt: '2026-08-20T00:00:00Z',
        cover: 'aurora-blue', pinned: false,
      }}
      selected={false}
      unavailable={false}
      {...callbacks}
    />,
  )
  return callbacks
}

describe('project card', () => {
  it('selects once, opens twice and exposes the context actions', () => {
    const callbacks = renderProjectCard()
    const card = screen.getByRole('button', { name: '项目 demo' })

    fireEvent.click(card)
    expect(callbacks.onSelect).toHaveBeenCalledOnce()
    expect(callbacks.onOpen).not.toHaveBeenCalled()

    fireEvent.doubleClick(card)
    expect(callbacks.onOpen).toHaveBeenCalledOnce()

    fireEvent.contextMenu(card, { clientX: 120, clientY: 160 })
    expect(screen.getByRole('menu', { name: '项目操作' })).toBeVisible()
    expect(screen.getByRole('menuitem', { name: '修改名称' })).toBeVisible()
    expect(screen.getByRole('menuitem', { name: '修改封面' })).toBeVisible()
    expect(screen.getByRole('menuitem', { name: '置顶' })).toBeVisible()
    expect(screen.getByRole('menuitem', { name: '删除项目' })).toBeVisible()

    fireEvent.keyDown(screen.getByRole('menu', { name: '项目操作' }), { key: 'Home' })
    fireEvent.keyDown(screen.getByRole('menu', { name: '项目操作' }), { key: 'Enter' })
    expect(screen.getByRole('textbox', { name: '项目名称' })).toBeVisible()
  })

  it('supports keyboard open, inline rename and menu focus restoration', async () => {
    const callbacks = renderProjectCard()
    const card = screen.getByRole('button', { name: '项目 demo' })

    card.focus()
    fireEvent.keyDown(card, { key: 'Enter' })
    expect(callbacks.onOpen).toHaveBeenCalledOnce()

    fireEvent.keyDown(card, { key: 'F2' })
    const input = screen.getByRole('textbox', { name: '项目名称' })
    fireEvent.change(input, { target: { value: '  新名称  ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(callbacks.onRename).toHaveBeenCalledWith('新名称'))

    fireEvent.contextMenu(card)
    const menu = screen.getByRole('menu', { name: '项目操作' })
    fireEvent.keyDown(menu, { key: 'End' })
    expect(screen.getByRole('menuitem', { name: '删除项目' })).toHaveFocus()
    fireEvent.keyDown(menu, { key: 'Escape' })
    expect(card).toHaveFocus()
  })

  it('changes a built-in cover and toggles pinning from the context menu', async () => {
    const callbacks = renderProjectCard()
    const card = screen.getByRole('button', { name: '项目 demo' })

    fireEvent.contextMenu(card)
    fireEvent.click(screen.getByRole('menuitem', { name: '修改封面' }))
    fireEvent.click(screen.getByRole('menuitemradio', { name: '森林' }))
    await waitFor(() => expect(callbacks.onCoverChange).toHaveBeenCalledWith('forest'))

    fireEvent.contextMenu(card)
    fireEvent.click(screen.getByRole('menuitem', { name: '置顶' }))
    await waitFor(() => expect(callbacks.onPinChange).toHaveBeenCalledWith(true))
  })
})
