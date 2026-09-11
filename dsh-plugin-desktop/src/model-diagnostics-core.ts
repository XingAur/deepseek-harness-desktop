/** Shared model-diagnostics core: state read, endpoint probe, default-model select. */

import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { parse, stringify } from 'yaml'
import { writeFileAtomic } from '@deepseek-ai/dsh-atomic-write'

const REQUEST_TIMEOUT_MS = 20_000

/** Renderer-safe view of the effective model configuration. */
export interface ModelDiagnosticsState {
  readonly provider: string | null
  readonly model: string | null
  readonly baseURL: string | null
  readonly keyPresent: boolean
  readonly keyPreview: string | null
}

/** Outcome of one endpoint probe or model-list fetch. */
export interface ModelProbeResult {
  readonly ok: boolean
  readonly status: number
  readonly models: readonly string[]
  readonly error: string | null
}

function section(document: Record<string, unknown>, name: string): Record<string, unknown> {
  const value = document[name]
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

async function readYaml(path: string): Promise<Record<string, unknown>> {
  try {
    const parsed = parse(await readFile(path, 'utf8'))
    return parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {}
  } catch {
    return {}
  }
}

function apiKeyOf(home: Record<string, unknown>): string | null {
  // `section` only ever yields nested maps, so the leaf value must be read
  // directly: wrapping it in section() silently turns every string key into
  // the empty-object fallback and the probe reports "no API key stored".
  const value = section(home, 'refs')['DEEPSEEK_API_KEY']
  return typeof value === 'string' && value !== '' ? value : null
}

/** Strip the Anthropic-compat suffix so `/models` hits the platform endpoint. */
export function modelsEndpoint(baseURL: string): string {
  const trimmed = baseURL.replace(/\/+$/u, '')
  const root = trimmed.endsWith('/anthropic') ? trimmed.slice(0, -'/anthropic'.length) : trimmed
  return `${root}/models`
}

/** Read the effective model configuration under one DSH home. */
export async function readModelDiagnosticsState(home: string): Promise<ModelDiagnosticsState> {
  const document = await readYaml(join(home, 'settings.yaml'))
  const credentials = await readYaml(join(home, '.credentials.yaml'))
  const key = apiKeyOf(credentials)
  const defaultModel = section(document, 'agent-default-model')
  const llm = section(document, 'llm-deepseek')
  return {
    provider: typeof defaultModel.provider === 'string' ? defaultModel.provider : null,
    model: typeof defaultModel.model === 'string' ? defaultModel.model : null,
    baseURL: typeof llm.baseURL === 'string' ? llm.baseURL : null,
    keyPresent: key !== null,
    keyPreview: key === null ? null : `${key.slice(0, 6)}…${key.slice(-4)}`,
  }
}

/** One attempt against the endpoint; a null result means the request itself failed. */
async function probeOnce(endpoint: string, key: string): Promise<ModelProbeResult | null> {
  try {
    const response = await fetch(endpoint, {
      headers: { authorization: `Bearer ${key}`, accept: 'application/json' },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    })
    if (!response.ok) {
      const detail = (await response.text().catch(() => '')).replace(/\s+/gu, ' ').trim().slice(0, 200)
      return {
        ok: false,
        status: response.status,
        models: [],
        error: detail === '' ? `HTTP ${String(response.status)}` : `HTTP ${String(response.status)}: ${detail}`,
      }
    }
    const payload = await response.json() as { data?: ReadonlyArray<{ id?: unknown }> }
    const models = (Array.isArray(payload.data) ? payload.data : [])
      .map(item => (typeof item?.id === 'string' ? item.id : null))
      .filter((id): id is string => id !== null)
      .sort()
    return { ok: true, status: response.status, models, error: null }
  } catch (cause) {
    return { ok: false, status: 0, models: [], error: cause instanceof Error ? cause.message : String(cause) }
  }
}

/** Probe the configured endpoint: connectivity, authentication, model list. */
export async function probeModels(home: string): Promise<ModelProbeResult> {
  const state = await readModelDiagnosticsState(home)
  if (state.baseURL === null) return { ok: false, status: 0, models: [], error: 'no baseURL configured' }
  const key = apiKeyOf(await readYaml(join(home, '.credentials.yaml')))
  if (key === null) return { ok: false, status: 0, models: [], error: 'no API key stored' }
  return await probeWithRetry(modelsEndpoint(state.baseURL), key)
}

/** Conventional credential reference for one pi-ai provider route. */
function deriveKeyRef(provider: string): string {
  return `${provider.toUpperCase().replace(/[^a-z0-9]+/giu, '_')}_API_KEY`
}

/** Probe one configured pi-ai provider profile (custom OpenAI-compatible routes). */
export async function probeProvider(home: string, provider: string): Promise<ModelProbeResult> {
  if (provider === '' || provider.length > 100 || !/^[a-z0-9][a-z0-9-]*$/u.test(provider)) {
    return { ok: false, status: 0, models: [], error: 'invalid provider id' }
  }
  const document = await readYaml(join(home, 'settings.yaml'))
  const profiles = section(section(document, 'llm-pi-ai'), 'providers')
  const profile = profiles[provider]
  const baseURL = profile !== null && typeof profile === 'object' && !Array.isArray(profile)
    && typeof (profile as Record<string, unknown>).baseURL === 'string'
    ? (profile as Record<string, unknown>).baseURL as string
    : null
  if (baseURL === null || baseURL === '') {
    return { ok: false, status: 0, models: [], error: `no baseURL configured for ${provider}` }
  }
  const profileRecord = profile as Record<string, unknown>
  const ref = typeof profileRecord.apiKeyEnv === 'string' && profileRecord.apiKeyEnv !== ''
    ? profileRecord.apiKeyEnv
    : deriveKeyRef(provider)
  // Leaf credential values are strings: read the ref directly, not through
  // section() (whose object-only fallback hides every scalar).
  const key = section(await readYaml(join(home, '.credentials.yaml')), 'refs')[ref]
  if (typeof key !== 'string' || key === '') {
    return { ok: false, status: 0, models: [], error: `no API key stored for ${provider}` }
  }
  return await probeWithRetry(modelsEndpoint(baseURL), key)
}

/**
 * Shared probe driver: the probe is a user-triggered diagnostic, so transport
 * hiccups and upstream 5xx get exactly one retry before the result reads as
 * failure.
 */
async function probeWithRetry(endpoint: string, key: string): Promise<ModelProbeResult> {
  const first = await probeOnce(endpoint, key)
  if (first?.ok === true) return first
  if (first !== null && first.status > 0 && first.status < 500) return first
  const second = await probeOnce(endpoint, key)
  return second ?? first ?? { ok: false, status: 0, models: [], error: 'probe failed' }
}

/** Persist one model id as the default for the next sessions. */
export async function selectModel(home: string, model: string): Promise<{ accepted: boolean }> {
  if (model === '' || model.length > 200) throw new Error('invalid model id')
  const path = join(home, 'settings.yaml')
  const document = await readYaml(path)
  document['agent-default-model'] = { ...section(document, 'agent-default-model'), model }
  await writeFileAtomic(path, `${stringify(document)}
`, { mode: 0o600, dirMode: 0o700 })
  return { accepted: true }
}
