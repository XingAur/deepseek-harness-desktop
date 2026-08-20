import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ProjectDeleteDialog } from '../src/client/ProjectDeleteDialog'

const project = {
  id: 'w-demo', title: 'demo', path: 'C:\\code\\demo', unavailable: false,
}

describe('project delete dialog', () => {
  it('defaults to unregister and requires the exact name for recycle', () => {
    render(<ProjectDeleteDialog project={project} onCancel={vi.fn()} onConfirm={vi.fn(async () => undefined)} />)

    expect(screen.getByRole('radio', { name: '仅从列表移除' })).toBeChecked()
    expect(screen.getByRole('button', { name: '确认移除' })).toBeEnabled()
    fireEvent.click(screen.getByRole('radio', { name: '移到 Windows 回收站' }))
    expect(screen.getByRole('button', { name: '移到回收站' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('输入项目名称确认'), { target: { value: project.title } })
    expect(screen.getByRole('button', { name: '移到回收站' })).toBeEnabled()
  })

  it('keeps the dialog open and shows a failed operation', async () => {
    const onConfirm = vi.fn(async () => { throw new Error('回收站不可用') })
    render(<ProjectDeleteDialog project={project} onCancel={vi.fn()} onConfirm={onConfirm} />)
    fireEvent.click(screen.getByRole('radio', { name: '移到 Windows 回收站' }))
    fireEvent.change(screen.getByLabelText('输入项目名称确认'), { target: { value: project.title } })
    fireEvent.click(screen.getByRole('button', { name: '移到回收站' }))

    expect(await screen.findByText('回收站不可用')).toBeVisible()
    expect(onConfirm).toHaveBeenCalledWith('recycle')
  })

  it('disables recycling for an unavailable registered path', () => {
    render(<ProjectDeleteDialog project={{ ...project, unavailable: true }} onCancel={vi.fn()} onConfirm={vi.fn()} />)
    expect(screen.getByRole('radio', { name: '移到 Windows 回收站' })).toBeDisabled()
  })

  it('closes with Escape', async () => {
    const onCancel = vi.fn()
    render(<ProjectDeleteDialog project={project} onCancel={onCancel} onConfirm={vi.fn()} />)
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    await waitFor(() => expect(onCancel).toHaveBeenCalledOnce())
  })
})
