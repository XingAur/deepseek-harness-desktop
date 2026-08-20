import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MigrationPrompt } from './MigrationPrompt'

describe('MigrationPrompt', () => {
  it('requires confirmation before migrating a discovered legacy root', async () => {
    const confirm = vi.fn(async () => undefined)
    render(<MigrationPrompt migration={{
      phase: 'candidate', source: 'C:\\旧数据', target: 'C:\\新数据',
      bytes: 4096, profiles: 2, workspaces: 3,
    }} onConfirm={confirm} onDefer={vi.fn(async () => undefined)} />)
    expect(screen.getByText('发现旧版桌面数据')).toBeInTheDocument()
    expect(screen.getByText(/源目录会保留完整备份/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '备份并迁移' }))
    await waitFor(() => expect(confirm).toHaveBeenCalled())
  })
})
