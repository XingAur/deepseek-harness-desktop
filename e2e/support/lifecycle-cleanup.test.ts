import { describe, expect, it } from 'vitest'
import { closeCleanupAndStage } from './lifecycle-cleanup'

describe('closeCleanupAndStage', () => {
  it('always closes, then cleans up, then stages, without losing cleanup errors', async () => {
    const calls: string[] = []
    await expect(closeCleanupAndStage({
      close: async () => { calls.push('close'); throw new Error('close failed') },
      cleanup: async () => { calls.push('cleanup'); throw new Error('cleanup failed') },
      stage: () => { calls.push('stage') },
    })).rejects.toThrow('E2E 生命周期收尾失败')
    expect(calls).toEqual(['close', 'cleanup', 'stage'])
  })
})
