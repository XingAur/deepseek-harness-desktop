import { describe, expect, it } from 'vitest'
import { canStartHarnessTask, missingHarnessContext } from '../src/client/harness/harness-task-state'

describe('Harness task gate', () => {
  it('requires background, scenario, goal and a target project', () => {
    const context = { background: '', scenario: '患者退费', goal: '修复', desiredOutcome: '闭环', projectPath: '/repo' }
    expect(canStartHarnessTask(context)).toBe(false)
    expect(missingHarnessContext(context)).toContain('业务背景')
  })

  it('requires selected visual evidence when the requirement says it is needed', () => {
    const context = {
      background: '线上医保退费', scenario: '预结算失败', goal: '定位真实调用链', desiredOutcome: '精准修复', projectPath: '/repo',
      visualEvidenceRequired: true, imageEvidenceCount: 0,
    }
    expect(canStartHarnessTask(context)).toBe(false)
    expect(missingHarnessContext(context)).toContain('截图/图片证据')
  })

  it('allows intake only after all explicit context gates are satisfied', () => {
    expect(canStartHarnessTask({
      background: '线上医保退费', scenario: '预结算失败', goal: '定位真实调用链', desiredOutcome: '精准修复', projectPath: '/repo',
      visualEvidenceRequired: true, imageEvidenceCount: 1,
    })).toBe(true)
  })
})
