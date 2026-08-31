import { describe, expect, it } from 'vitest'
import { inject } from '../src/client/index'

describe('desktop client module contract', () => {
  it('声明官方工作台启动所需的宿主服务', () => {
    // 场景：远程最新工作台在 root slot 读取当前模型。
    // 条件：桌面插件声明启动阶段需要注入的宿主服务。
    // 预期：保留官方 llm 注入，避免启动阶段白屏。
    expect(inject).toEqual(['slots', 'sessions', 'theme', 'workspaces', 'llm'])
  })
})
