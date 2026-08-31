import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { ExtensionErrorBoundary } from '../src/client/extension-center/ExtensionErrorBoundary'

function Bomb({ shouldThrow }: { shouldThrow: boolean }) {
  if (shouldThrow) throw new Error('面板爆炸了')
  return <p>正常内容</p>
}

describe('ExtensionErrorBoundary', () => {
  it('子组件崩溃时降级为错误卡而不是向上抛', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    try {
      render(<ExtensionErrorBoundary label="测试面板"><Bomb shouldThrow /></ExtensionErrorBoundary>)
      expect(screen.getByRole('alert')).toHaveTextContent('测试面板 暂时不可用')
      expect(screen.queryByText('正常内容')).not.toBeInTheDocument()
    } finally {
      spy.mockRestore()
    }
  })

  it('重试后恢复子组件渲染', () => {
    function RecoverablePanel() {
      const [shouldThrow, setShouldThrow] = useState(true)
      return (
        <>
          <button type="button" onClick={() => setShouldThrow(false)}>修复子组件</button>
          <ExtensionErrorBoundary label="测试面板">
            <Bomb shouldThrow={shouldThrow} />
          </ExtensionErrorBoundary>
        </>
      )
    }

    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    try {
      render(<RecoverablePanel />)
      expect(screen.getByRole('alert')).toBeInTheDocument()

      fireEvent.click(screen.getByRole('button', { name: '修复子组件' }))
      fireEvent.click(screen.getByRole('button', { name: '重试' }))

      expect(screen.getByText('正常内容')).toBeInTheDocument()
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    } finally {
      spy.mockRestore()
    }
  })
})
