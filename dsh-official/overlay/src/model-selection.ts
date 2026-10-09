/** Model-display selection for pi-ai provider routes, persisted in the user's own DSH home. */

import { chmod, copyFile, mkdir, readFile, rename, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { parse, stringify } from 'yaml'

/** Profile patch file the official desktop profile itself documents as user-editable. */
const PATCH_RELATIVE = join('profiles', 'desktop', 'cordis.patch.yml')
const MAX_MODELS = 200

const MODEL_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/u
const PROVIDER_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,99}$/u

export function isValidProviderId(value: string): boolean {
  return PROVIDER_ID_PATTERN.test(value)
}

const XAI_DROP_RE = /(?:build|multi-agent|-[0-9]{4}(?:-|$))/iu

const EFFORTS_56 = { minimal: 'low', xhigh: 'xhigh', max: 'max' } as const
const EFFORTS_6 = {
  off: null, minimal: 'low', low: 'low', medium: 'medium', high: 'high', xhigh: 'xhigh', max: 'max',
} as const

/** Official `@earendil-works/pi-ai` openai-codex catalog rows (0.85.x). */
const OFFICIAL_CODEX: readonly PickerModel[] = [
  { id: 'gpt-5.3-codex-spark', name: 'GPT-5.3 Codex Spark', reasoningEfforts: EFFORTS_56 },
  { id: 'gpt-5.4', name: 'GPT-5.4', reasoningEfforts: EFFORTS_56 },
  { id: 'gpt-5.4-mini', name: 'GPT-5.4 mini', reasoningEfforts: EFFORTS_56 },
  { id: 'gpt-5.5', name: 'GPT-5.5', reasoningEfforts: EFFORTS_56 },
  { id: 'gpt-5.6-luna', name: 'GPT-5.6 Luna', reasoningEfforts: EFFORTS_56 },
  { id: 'gpt-5.6-sol', name: 'GPT-5.6 Sol', reasoningEfforts: EFFORTS_56 },
  { id: 'gpt-5.6-terra', name: 'GPT-5.6 Terra', reasoningEfforts: EFFORTS_56 },
  { id: 'gpt-6-astra', name: 'GPT-6 Astra', reasoningEfforts: EFFORTS_6 },
]

function canonicalizeCodexId(id: string): string {
  return id.replace(/^(gpt)-(\d+)-(\d+)\b/iu, '$1-$2.$3')
}

function displayNameFor(id: string, provider: string): string {
  if (provider === 'xai' && id.startsWith('grok-')) {
    return `Grok ${id.slice(5).replace(/-/gu, ' ')}`
  }
  const planets = id.match(/-(luna|sol|terra|astra)$/iu)
  const base = id.replace(/^gpt-/iu, 'GPT-')
  if (planets === null) return base
  const planet = planets[1]!
  return `${base.slice(0, -planet.length - 1)} ${planet.slice(0, 1).toUpperCase()}${planet.slice(1)}`
}

export interface PickerModel {
  readonly id: string
  readonly name: string
  readonly reasoningEfforts?: Readonly<Record<string, string | null>>
}

/** Keep official Codex SKUs (Luna/Sol/Terra/Astra). Drop thinking/instant/pro aliases. */
function codexSku(raw: string): PickerModel | null {
  const id = canonicalizeCodexId(raw).toLowerCase()
    .replace(/-wm$/u, '')
    .replace(/-t-mini$/u, '-mini')
  if (/-(?:thinking|instant|pro|reserve)$/u.test(id) || id === 'gpt-reserve' || id === 'research' || id === 'o3-pro') {
    return null
  }
  if (/codex-spark|^gpt-5\.3$/u.test(id) || id === 'gpt-5.3-mini') {
    return { id: 'gpt-5.3-codex-spark', name: 'GPT-5.3 Codex Spark', reasoningEfforts: EFFORTS_56 }
  }
  const official = OFFICIAL_CODEX.find(entry => entry.id === id)
  if (official !== undefined) return official
  const planet = /^(gpt-\d+(?:\.\d+)?)-(luna|sol|terra|astra)$/u.exec(id)
  if (planet !== null) {
    const efforts = planet[1]!.startsWith('gpt-6') ? EFFORTS_6 : EFFORTS_56
    return { id, name: displayNameFor(id, 'openai-codex'), reasoningEfforts: efforts }
  }
  if (/^gpt-5\.4-mini$/u.test(id)) return { id: 'gpt-5.4-mini', name: 'GPT-5.4 mini', reasoningEfforts: EFFORTS_56 }
  if (/^gpt-5\.4$/u.test(id)) return { id: 'gpt-5.4', name: 'GPT-5.4', reasoningEfforts: EFFORTS_56 }
  if (/^gpt-5\.5-mini$/u.test(id)) return { id: 'gpt-5.5-mini', name: 'GPT-5.5 mini', reasoningEfforts: EFFORTS_56 }
  if (/^gpt-5\.5$/u.test(id)) return { id: 'gpt-5.5', name: 'GPT-5.5', reasoningEfforts: EFFORTS_56 }
  if (/^gpt-5\.6-mini$/u.test(id)) return { id: 'gpt-5.6-mini', name: 'GPT-5.6 mini', reasoningEfforts: EFFORTS_56 }
  if (/^gpt-5\.6$/u.test(id)) return null
  if (/^gpt-6$/u.test(id)) return null
  return null
}

/** Collapse Codex planet/mode SKUs into one picker row with a reasoning slider. */
export function foldPickerModels(provider: string, models: readonly string[]): PickerModel[] {
  if (provider === 'xai') {
    return [...new Set(models)]
      .filter(id => id.startsWith('grok-') && !XAI_DROP_RE.test(id))
      .sort()
      .map(id => ({ id, name: displayNameFor(id, provider) }))
  }
  if (provider !== 'openai-codex') {
    return [...new Set(models)].sort().map(id => ({ id, name: displayNameFor(id, provider) }))
  }
  const byId = new Map<string, PickerModel>()
  for (const entry of OFFICIAL_CODEX) byId.set(entry.id, entry)
  for (const raw of models) {
    const sku = codexSku(raw)
    if (sku === null) continue
    byId.set(sku.id, sku)
  }
  return [...byId.values()].sort((left, right) => left.id.localeCompare(right.id))
}

/** Map an explicit user selection to picker rows. Does not re-inject the full official catalog. */
export function profilesForIds(provider: string, models: readonly string[]): PickerModel[] {
  if (provider === 'xai') {
    return [...new Set(models)]
      .filter(id => id.startsWith('grok-') && !XAI_DROP_RE.test(id))
      .sort()
      .map(id => ({ id, name: displayNameFor(id, provider) }))
  }
  if (provider !== 'openai-codex') {
    return [...new Set(models)].sort().map(id => ({ id, name: displayNameFor(id, provider) }))
  }
  const byId = new Map<string, PickerModel>()
  for (const raw of models) {
    const sku = codexSku(raw)
    if (sku === null) continue
    byId.set(sku.id, sku)
  }
  return [...byId.values()].sort((left, right) => left.id.localeCompare(right.id))
}

/** Normalize a user-submitted model id list: validate, dedupe, sort, cap. */
export function normalizeModelIds(models: readonly unknown[]): string[] {
  const seen = new Set<string>()
  for (const value of models) {
    if (typeof value !== 'string' || !MODEL_ID_PATTERN.test(value)) {
      throw new Error(`invalid model id: ${String(value)}`)
    }
    seen.add(value)
    if (seen.size > MAX_MODELS) throw new Error(`too many models (max ${String(MAX_MODELS)})`)
  }
  return [...seen].sort()
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

async function readYaml(path: string): Promise<unknown> {
  try {
    return parse(await readFile(path, 'utf8'))
  } catch {
    return null
  }
}

/** Read `[{id}]`-shaped or bare-string model lists under one provider config. */
function modelIdsOf(profile: Record<string, unknown>): string[] {
  const raw = profile.models
  if (!Array.isArray(raw)) return []
  const ids: string[] = []
  for (const entry of raw) {
    if (typeof entry === 'string') ids.push(entry)
    else {
      const record = asRecord(entry)
      if (typeof record.id === 'string') ids.push(record.id)
    }
  }
  return ids
}

/** Configured models for one route: settings.yaml user layer plus the desktop profile patch (patch wins). */
export async function readConfiguredModels(home: string, provider: string): Promise<string[]> {
  if (!isValidProviderId(provider)) return []
  const settings = asRecord(await readYaml(join(home, 'settings.yaml')))
  const settingsProviders = asRecord(asRecord(settings['llm-pi-ai'])['providers'])
  const merged = new Set(modelIdsOf(asRecord(settingsProviders[provider])))
  const patch = await readYaml(join(home, PATCH_RELATIVE))
  if (Array.isArray(patch)) {
    for (const entry of patch) {
      const record = asRecord(entry)
      if (record.id !== 'llm-pi-ai') continue
      const providers = asRecord(asRecord(record.config)['providers'])
      for (const id of modelIdsOf(asRecord(providers[provider]))) merged.add(id)
    }
  }
  return [...merged].sort()
}

/**
 * Persist the visible-model selection into the desktop profile patch layer.
 * The patch file is the user-owned composition surface, so an official-tree
 * sync never conflicts with these writes; a running desktop picks them up on
 * its next profile load.
 */
export async function writeModelSelection(home: string, provider: string, models: readonly unknown[]): Promise<string[]> {
  if (!isValidProviderId(provider)) throw new Error('invalid provider id')
  const normalized = normalizeModelIds(models)
  const folded = profilesForIds(provider, normalized)
  const path = join(home, PATCH_RELATIVE)
  const parsed = await readYaml(path)
  const entries: unknown[] = Array.isArray(parsed) ? parsed : []
  let entry = entries.find(candidate => asRecord(candidate).id === 'llm-pi-ai')
  if (entry === undefined) {
    entry = { id: 'llm-pi-ai', name: '@deepseek-ai/dsh-llm-pi-ai', config: { providers: {} } }
    entries.push(entry)
  }
  const record = asRecord(entry)
  const config = asRecord(record.config)
  const providers = asRecord(config.providers)
  const profile = asRecord(providers[provider])
  providers[provider] = {
    ...profile,
    models: folded.map(entry => ({
      id: entry.id,
      name: entry.name,
      ...(entry.reasoningEfforts === undefined ? {} : { reasoningEfforts: entry.reasoningEfforts }),
    })),
  }
  record.config = { ...config, providers }
  await mkdir(dirname(path), { recursive: true, mode: 0o700 })
  const temporary = `${path}.overlay-tmp`
  await writeFile(temporary, `${stringify(entries)}\n`, { encoding: 'utf8', mode: 0o600 })
  try {
    await rename(temporary, path)
  } catch {
    // Rename across a fresh directory can race a first-run desktop; fall back
    // to a same-directory copy so the write still lands atomically enough.
    await copyFile(temporary, path)
    await chmod(path, 0o600)
  }
  return folded.map(entry => entry.id)
}
