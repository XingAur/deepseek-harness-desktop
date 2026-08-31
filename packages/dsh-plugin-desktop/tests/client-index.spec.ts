import { describe, expect, it } from 'vitest'
import { inject } from '../src/client/index'

describe('desktop client module contract', () => {
  it('声明官方工作台启动所需的宿主服务', () => {
    // 场景：远程最新工作台在 root slot 读取当前模型。
    // 条件：桌面插件声明启动阶段需要注入的宿主服务。
    // 客户端启动图没有 llm 服务；声明它会阻止插件注册 layout。
    expect(inject).toEqual(['slots', 'sessions', 'theme', 'workspaces'])
  })
})
