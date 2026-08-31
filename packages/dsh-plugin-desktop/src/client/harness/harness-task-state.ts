export type HarnessTaskStage =
  | 'collecting'
  | 'understanding'
  | 'deciding'
  | 'executing'
  | 'verifying'
  | 'replanning'
  | 'completed'
  | 'needs-user'

export interface HarnessTaskContext {
  background: string
  scenario: string
  goal: string
  desiredOutcome: string
  projectPath: string
  visualEvidenceRequired?: boolean
  imageEvidenceCount?: number
  conversationEvidenceRequired?: boolean
  conversationEvidenceCount?: number
}

export function missingHarnessContext(context: Partial<HarnessTaskContext>): string[] {
  const missing: string[] = []
  if (!nonEmpty(context.background)) missing.push('业务背景')
  if (!nonEmpty(context.scenario)) missing.push('使用场景')
  if (!nonEmpty(context.goal)) missing.push('目标')
  if (!nonEmpty(context.desiredOutcome)) missing.push('期望结果')
  if (!isAbsolutePath(context.projectPath)) missing.push('目标项目')
  if (context.visualEvidenceRequired === true && !(context.imageEvidenceCount && context.imageEvidenceCount > 0)) missing.push('截图/图片证据')
  if (context.conversationEvidenceRequired === true && !(context.conversationEvidenceCount && context.conversationEvidenceCount > 0)) missing.push('对话证据')
  return missing
}

export function canStartHarnessTask(context: Partial<HarnessTaskContext>): boolean {
  return missingHarnessContext(context).length === 0
}

export function nextHarnessStage(stage: HarnessTaskStage, outcome?: 'success' | 'failure' | 'blocked'): HarnessTaskStage {
  if (stage === 'collecting') return 'understanding'
  if (stage === 'understanding') return 'deciding'
  if (stage === 'deciding') return 'executing'
  if (stage === 'executing') return outcome === 'failure' ? 'replanning' : 'verifying'
  if (stage === 'verifying') return outcome === 'success' ? 'completed' : outcome === 'failure' ? 'replanning' : 'needs-user'
  if (stage === 'replanning') return 'deciding'
  return stage
}

function nonEmpty(value: unknown): value is string {
  return typeof value === 'string' && value.trim() !== ''
}

function isAbsolutePath(value: unknown): value is string {
  return typeof value === 'string' && value.startsWith('/') && value.length > 1 && !value.includes('\u0000')
}
