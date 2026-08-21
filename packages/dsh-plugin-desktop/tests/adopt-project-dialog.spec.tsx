import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AdoptProjectDialog } from '../src/client/AdoptProjectDialog'

describe('adopt project dialog', () => {
  it('lists candidates and adopts on selection', async () => {
    const onAdopt = vi.fn(async () => undefined)
    render(
      <AdoptProjectDialog
        candidates={[{ id: 'w-9', title: 'legacy', path: 'D:\\code\\legacy' }]}
        busy={false}
        onAdopt={onAdopt}
        onClose={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /legacy/ }))
    await waitFor(() => expect(onAdopt).toHaveBeenCalledWith('w-9'))
  })

  it('shows an empty hint when nothing can be adopted', () => {
    render(<AdoptProjectDialog candidates={[]} busy={false} onAdopt={vi.fn()} onClose={vi.fn()} />)
    expect(screen.getByText('当前 Profile 没有可收录的项目')).toBeInTheDocument()
  })

  it('closes on Escape and backdrop click', () => {
    const onClose = vi.fn()
    const { container } = render(
      <AdoptProjectDialog
        candidates={[{ id: 'w-9', title: 'legacy', path: 'D:\\code\\legacy' }]}
        busy={false}
        onAdopt={vi.fn()}
        onClose={onClose}
      />,
    )
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
    fireEvent.pointerDown(container.querySelector('.dshDesktopProjectDialogBackdrop')!)
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('blocks Escape, backdrop clicks and candidate buttons while busy', () => {
    const onClose = vi.fn()
    const onAdopt = vi.fn()
    const { container } = render(
      <AdoptProjectDialog
        candidates={[{ id: 'w-9', title: 'legacy', path: 'D:\\code\\legacy' }]}
        busy={true}
        onAdopt={onAdopt}
        onClose={onClose}
      />,
    )
    const candidate = screen.getByRole('button', { name: /legacy/ })
    expect(candidate).toBeDisabled()
    fireEvent.click(candidate)
    expect(onAdopt).not.toHaveBeenCalled()

    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.pointerDown(container.querySelector('.dshDesktopProjectDialogBackdrop')!)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('renders the adopt error inline with role alert', () => {
    render(
      <AdoptProjectDialog
        candidates={[{ id: 'w-9', title: 'legacy', path: 'D:\\code\\legacy' }]}
        busy={false}
        error="收录失败，请重试"
        onAdopt={vi.fn()}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('收录失败，请重试')
  })
})
