import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ExtensionReviewDialog } from '../src/client/extensions/ExtensionReviewDialog'

describe('extension review dialog', () => {
  it('uses a compact official-style information hierarchy and accessible actions', () => {
    const onClose = vi.fn()
    render(<ExtensionReviewDialog extension={{
      extensionId: 'skill.review', extensionKind: 'skill', displayName: '代码审查技能', sourceKind: 'official', status: 'enabled', updatedAt: '2026-08-30T00:00:00Z',
    }} onClose={onClose} />)

    expect(screen.getByRole('heading', { name: '扩展审核详情' })).toBeVisible()
    expect(screen.getAllByText('代码审查技能')[0]).toBeVisible()
    expect(screen.getByText('基本信息')).toBeVisible()
    expect(screen.getByText('安全与兼容')).toBeVisible()
    expect(screen.getByText('已启用')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '关闭审核详情' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
