import type { DesktopBridgeLike } from '../desktop-bridge'

export type PromptTarget = 'claude' | 'codex' | 'dsh'

export interface PromptPreset { id: string; title: string; content: string; createdAt: number; updatedAt: number }
export interface PresetSummary { id: string; title: string; updatedAt: number; activatedTargets: PromptTarget[] }
export interface TargetStatus {
  target: PromptTarget
  installed: boolean
  liveFileExists: boolean
  activePresetId: string | null
  liveContentSha256: string | null
  matchesActivePreset: boolean
  oversized: boolean
}
export type SaveOutcome =
  | { kind: 'saved'; preset: PromptPreset; projected: TargetStatus[] }
  | { kind: 'backfill-conflict'; presetId: string; candidates: Array<{ target: PromptTarget; content: string; updatedAt: number }> }
export type ActivateOutcome =
  | { kind: 'ok'; status: TargetStatus }
  | { kind: 'backfill-conflict'; presetId: string; candidates: Array<{ target: PromptTarget; content: string; updatedAt: number }> }

export const TARGET_LABELS: Record<PromptTarget, string> = { claude: 'Claude', codex: 'Codex', dsh: 'DSH' }
// 与 Rust 端 MAX_PROMPT_BYTES 对齐:上限按 UTF-8 字节计,而非 UTF-16 字符数。
export const MAX_PROMPT_CHARS = 24 * 1024
export function promptBytes(content: string): number {
  return new TextEncoder().encode(content).length
}

export async function fetchStatus(bridge: DesktopBridgeLike): Promise<TargetStatus[]> {
  return bridge.requestV2<TargetStatus[]>('prompts.status')
}
export async function fetchList(bridge: DesktopBridgeLike): Promise<PresetSummary[]> {
  return bridge.requestV2<PresetSummary[]>('prompts.list')
}
export async function fetchPreset(bridge: DesktopBridgeLike, presetId: string): Promise<PromptPreset> {
  return bridge.requestV2<PromptPreset>('prompts.get', undefined, { presetId })
}
export async function savePreset(bridge: DesktopBridgeLike, input: { presetId?: string; title: string; content: string }): Promise<SaveOutcome> {
  return bridge.requestV2<SaveOutcome>('prompts.save', undefined, input)
}
export async function resolveConflict(bridge: DesktopBridgeLike, input: { presetId: string; title: string; content: string }): Promise<SaveOutcome> {
  return bridge.requestV2<SaveOutcome>('prompts.resolve-conflict', undefined, input)
}
export async function deletePreset(bridge: DesktopBridgeLike, presetId: string): Promise<void> {
  await bridge.requestV2('prompts.delete', undefined, { presetId })
}
export async function activatePreset(bridge: DesktopBridgeLike, presetId: string, target: PromptTarget): Promise<ActivateOutcome> {
  return bridge.requestV2<ActivateOutcome>('prompts.activate', undefined, { presetId, target })
}
export async function deactivateTarget(bridge: DesktopBridgeLike, target: PromptTarget): Promise<TargetStatus> {
  return bridge.requestV2<TargetStatus>('prompts.deactivate', undefined, { target })
}
export async function importTargets(bridge: DesktopBridgeLike, targets: PromptTarget[]): Promise<PresetSummary[]> {
  return bridge.requestV2<PresetSummary[]>('prompts.import', undefined, { targets })
}
