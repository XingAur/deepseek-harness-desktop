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

// ---- 粘贴 JSON 导入(spec §4.2 / §7:仅接受 {title, content} 或 cc-switch prompts 形状)----

export interface ParsedPresetDraft { title: string; content: string }
export type PasteParseResult =
  | { ok: true; presets: ParsedPresetDraft[]; skipped: number }
  | { ok: false; reason: string }

// 标题 trim 后 1-200 字符;超长截断到 200 而非拒收(按 UTF-16 码元计)。
const MAX_PASTE_TITLE_CHARS = 200

// 宽容映射:title 取 title ?? name,content 取 content ?? prompt ?? value(cc-switch 兼容)。
function mapPastedEntry(raw: unknown): ParsedPresetDraft | null {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null
  const record = raw as Record<string, unknown>
  const rawTitle = record.title ?? record.name
  const rawContent = record.content ?? record.prompt ?? record.value
  if (typeof rawTitle !== 'string' || typeof rawContent !== 'string') return null
  const title = rawTitle.trim().slice(0, MAX_PASTE_TITLE_CHARS)
  if (title.length === 0) return null
  return { title, content: rawContent }
}

export function parsePastedPresets(text: string): PasteParseResult {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return { ok: false, reason: '不是有效的 JSON' }
  }
  const entries: unknown[] = Array.isArray(parsed) ? parsed : [parsed]
  const presets: ParsedPresetDraft[] = []
  const seenContents = new Set<string>()
  let skipped = 0
  for (const entry of entries) {
    const mapped = mapPastedEntry(entry)
    if (mapped === null) continue // 无法映射出 title+content 的元素跳过
    if (promptBytes(mapped.content) > MAX_PROMPT_CHARS) { skipped += 1; continue }
    if (seenContents.has(mapped.content)) continue // content 相同的重复条目去重
    seenContents.add(mapped.content)
    presets.push(mapped)
  }
  if (presets.length === 0) {
    return skipped > 0
      ? { ok: false, reason: `全部 ${skipped} 条条目超过 24 KiB 上限,已跳过` }
      : { ok: false, reason: '没有可识别的 {标题, 内容} 条目' }
  }
  return { ok: true, presets, skipped }
}
