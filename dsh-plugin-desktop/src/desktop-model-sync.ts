/**
 * Dynamic model-catalog synchronization for catalog provider routes: fetch the
 * provider's live `/models` listing with the stored credential and merge ids
 * the installed pi-ai catalog does not know yet into the route's `models`
 * profile, so a newly released model becomes selectable without a rebuild.
 *
 * The seam is `llm-pi-ai` settings: a route's `models` list replaces the
 * served catalog, a bare `{ id }` entry inherits every catalog field, and an
 * unknown id must spell out the capacities a request needs (the resolver
 * refuses `modelOverrides` naming catalog-external ids, so `models` is the
 * only way to introduce a genuinely new model id).
 *
 * @module dsh-plugin-desktop/model-sync
 */

import { type Context, Service } from '@deepseek-ai/cordis'
import { credentialKey, credentialKeyId, credentialKeyScope } from '@deepseek-ai/dsh-credentials'
import type { CredentialRecord } from '@deepseek-ai/dsh-credentials'
import { builtinProviders } from '@earendil-works/pi-ai/providers/all'
import type { Model } from '@earendil-works/pi-ai'
import { LLM_SIGNIN_SCOPE } from './desktop-signin-interaction.ts'

/** Structural slice of pi-ai's OAuth auth this module drives, untyped payloads. */
interface OAuthLike {
  refresh(credential: unknown, signal: AbortSignal): Promise<unknown>
  toAuth(credential: unknown): Promise<{ apiKey?: string, headers?: Record<string, string> }>
}

/** One provider's sync outcome, renderer-safe. */
export interface DesktopModelSyncProviderResult {
  readonly provider: string
  readonly status: 'ok' | 'error' | 'skipped'
  readonly discovered: readonly string[]
  readonly detail?: string
}

/** Whole-catalog sync outcome, renderer-safe. */
export interface DesktopModelSyncResult {
  readonly results: readonly DesktopModelSyncProviderResult[]
}

/** One `models` profile entry as written into `llm-pi-ai` settings. */
type ModelInput = 'text' | 'image'
type ReasoningLevel = 'off' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max'
type ReasoningEfforts = Readonly<Partial<Record<ReasoningLevel, string | null>>>

export interface DesktopModelProfileEntry {
  readonly id: string
  readonly name?: string
  readonly contextWindow?: number
  readonly maxTokens?: number
  readonly input?: readonly ModelInput[]
  readonly reasoningEfforts?: false | ReasoningEfforts
}

/** Upper bound for one provider's `/models` request. */
const MODELS_FETCH_TIMEOUT_MS = 15_000

/** Access tokens this close to expiry are refreshed before the request. */
const TOKEN_EXPIRY_MARGIN_MS = 60_000

/**
 * The catalog model a discovered id inherits its capacities from: the widest
 * context window, latest on ties (generated catalogs append newer models).
 * @param models - the provider's installed catalog models.
 * @returns the template model, or `undefined` for an empty catalog.
 */
export function pickTemplateModel(models: readonly Model<never>[]): Model<never> | undefined {
  let best: Model<never> | undefined
  for (const model of models) {
    if (best === undefined
      || model.contextWindow > best.contextWindow
      || (model.contextWindow === best.contextWindow && model.id > best.id)) {
      best = model
    }
  }
  return best
}

/** Non-chat model families returned by broad provider catalog endpoints. */
const NON_TEXT_MODEL = /(?:image|imagine|video|audio|speech|tts|embedding|moderation|realtime|transcri)/iu

/**
 * Keep model ids that can plausibly serve Harness text/code conversations.
 * Provider endpoints often mix chat, image, video, and internal deployments;
 * only the chat/code subset belongs in llm-pi-ai's text-model selector.
 * @param provider - catalog provider route.
 * @param ids - raw ids returned by the live endpoint.
 * @returns unique, sorted text/code model ids.
 */
export function filterLiveModelIds(provider: string, ids: readonly string[]): string[] {
  return [...new Set(ids)]
    .filter(id => id.length > 0 && !NON_TEXT_MODEL.test(id))
    .filter((id) => {
      if (provider === 'xai') {
        return id.startsWith('grok-')
          && !/(?:build|multi-agent|-[0-9]{4}(?:-|$))/iu.test(id)
      }
      if (provider === 'openai-codex') {
        return /^(?:gpt-|codex-|o[0-9])/iu.test(id)
          && !/(?:auto-review|reserve)/iu.test(id)
      }
      return true
    })
    .sort()
}

/** Turn a stable provider id into a readable fallback display name. */
function modelDisplayName(provider: string, id: string, advertised: string | undefined): string {
  if (advertised !== undefined && advertised.length > 0 && advertised !== id) return advertised
  const parts = id.split('-').filter(part => part.length > 0)
  const titled = parts.map((part, index) => {
    if (index === 0 && provider === 'openai-codex' && part.toLowerCase() === 'gpt') return 'GPT'
    if (index === 0 && provider === 'xai' && part.toLowerCase() === 'grok') return 'Grok'
    if (/^[0-9.]+$/u.test(part)) return part
    return `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`
  })
  if (titled.length <= 2) return titled.join('-')
  return `${titled[0]}-${titled[1]} ${titled.slice(2).join(' ')}`
}

const REASONING_LEVELS = new Set<ReasoningLevel>(['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'])

/** Convert a provider's advertised effort rows into profile wire values. */
function advertisedReasoningEfforts(value: unknown): ReasoningEfforts | undefined {
  if (!Array.isArray(value)) return undefined
  const efforts: Partial<Record<ReasoningLevel, string | null>> = {}
  for (const raw of value) {
    const effort = typeof raw === 'string'
      ? raw
      : raw !== null && typeof raw === 'object' ? (raw as { effort?: unknown }).effort : undefined
    if (typeof effort !== 'string' || !REASONING_LEVELS.has(effort as ReasoningLevel)) continue
    efforts[effort as ReasoningLevel] = effort
  }
  return Object.keys(efforts).length === 0 ? undefined : efforts
}

/** Resolve the provider's actual advertised reasoning levels, not a sibling's guesses. */
export function liveReasoningEfforts(provider: string, row: Record<string, unknown>): ReasoningEfforts | undefined {
  if (provider === 'xai') {
    const capabilities = row.capabilities
    if (capabilities === null || typeof capabilities !== 'object' || Array.isArray(capabilities)) return undefined
    return advertisedReasoningEfforts((capabilities as { reasoning_effort?: unknown }).reasoning_effort)
  }
  return advertisedReasoningEfforts(row.supported_reasoning_levels)
}

/** Materialize reasoning levels for an unknown model from its closest catalog peer. */
function templateReasoningEfforts(template: Model<never> | undefined): false | ReasoningEfforts | undefined {
  if (template === undefined) return undefined
  if (!template.reasoning) return false
  const efforts: Partial<Record<ReasoningLevel, string | null>> = {}
  for (const level of REASONING_LEVELS) {
    const mapped = template.thinkingLevelMap?.[level]
    if (mapped === null) continue
    if (typeof mapped === 'string') {
      efforts[level] = mapped
    } else if (level === 'off') {
      efforts.off = null
    } else if (level !== 'xhigh' && level !== 'max') {
      efforts[level] = level
    }
  }
  return efforts
}

/** Prefer the proven HTTP stream when this Codex route uses a model HTTP proxy. */
export function shouldUseCodexSse(
  route: string,
  desktop: unknown,
  configuredTransport: unknown,
): boolean {
  if (route !== 'openai-codex' || configuredTransport !== undefined) return false
  if (desktop === null || typeof desktop !== 'object' || Array.isArray(desktop)) return false
  const config = desktop as { modelProxyUrl?: unknown, modelProxyProviders?: unknown }
  return typeof config.modelProxyUrl === 'string' && config.modelProxyUrl.length > 0
    && (config.modelProxyProviders === undefined
      || Array.isArray(config.modelProxyProviders) && config.modelProxyProviders.includes(route))
}

/** Positive integer capability value from a provider row. */
function positiveInteger(...values: readonly unknown[]): number | undefined {
  return values.find((value): value is number => typeof value === 'number' && Number.isInteger(value) && value > 0)
}

/**
 * Compose the route's next `models` profile listing. Automatically discovered
 * id-named unknown entries can be removed when the provider stops reporting
 * them or filtering classifies them as non-text/internal. User entries with a
 * distinct name remain untouched.
 * @param builtinIds - the installed catalog's model ids for the route.
 * @param discoveredIds - filtered live text/code model ids.
 * @param existing - the route's current `models` profile entries, if any.
 * @param template - the catalog model a new id inherits capacities from.
 * @param discoveredProfiles - live capability metadata keyed by discovered id.
 * @returns the next listing, or `undefined` when no write is warranted.
 */
export function composeNextModels(
  builtinIds: readonly string[],
  discoveredIds: readonly string[],
  existing: readonly DesktopModelProfileEntry[] | undefined,
  template: Model<never> | undefined,
  discoveredProfiles: Readonly<Record<string, Omit<DesktopModelProfileEntry, 'id'>>> = {},
): DesktopModelProfileEntry[] | undefined {
  const known = new Set(builtinIds)
  const liveUnknown = [...new Set(discoveredIds)].filter(id => !known.has(id)).sort()
  const entries: DesktopModelProfileEntry[] = existing === undefined
    ? builtinIds.map(id => ({ id }))
    : existing.filter((entry) => {
      if (known.has(entry.id)) return true
      const liveName = discoveredProfiles[entry.id]?.name
      // An automatically assigned readable name must not turn a disappeared
      // catalog entry into an immortal "custom" model on the next sync.
      const normalized = (value: string) => value.toLowerCase().replaceAll(/[\s-]/gu, '')
      const generated = entry.name === entry.id
        || entry.name !== undefined && normalized(entry.name) === normalized(entry.id)
        || entry.name !== undefined && entry.name === liveName
      return !generated
    })
  const retained = new Set(entries.map(entry => entry.id))
  for (const id of liveUnknown) {
    if (retained.has(id)) continue
    const live = discoveredProfiles[id]
    const reasoningEfforts = live?.reasoningEfforts ?? templateReasoningEfforts(template)
    const contextWindow = live?.contextWindow ?? template?.contextWindow
    const maxTokens = live?.maxTokens ?? template?.maxTokens
    const input = live?.input ?? template?.input
    entries.push({
      id,
      name: live?.name ?? id,
      ...(contextWindow === undefined ? {} : { contextWindow }),
      ...(maxTokens === undefined ? {} : { maxTokens }),
      ...(input === undefined ? {} : { input }),
      ...(reasoningEfforts === undefined ? {} : { reasoningEfforts }),
    })
  }
  if (existing !== undefined && JSON.stringify(entries) === JSON.stringify(existing)) return undefined
  if (existing === undefined && liveUnknown.length === 0) return undefined
  return entries.length > 0 ? entries : undefined
}

/** Resolved request headers for one provider's live listing. */
interface ResolvedBearer {
  readonly headers: Readonly<Record<string, string>>
  readonly refreshedRecord: CredentialRecord | undefined
}

/**
 * Derive bearer headers from one stored record, refreshing an expired OAuth
 * grant through the provider's own OAuth module when possible.
 * @param provider - the builtin provider object carrying OAuth metadata.
 * @param record - the route's stored credential record.
 * @returns the request headers plus a refreshed record to persist.
 */
async function resolveBearer(
  oauth: OAuthLike | undefined,
  record: CredentialRecord,
): Promise<ResolvedBearer> {
  if (record.kind === 'api-key') {
    const key = (record as { key?: unknown }).key
    if (typeof key !== 'string' || key.length === 0) throw new Error('stored api-key credential carries no key')
    return { headers: { authorization: `Bearer ${key}` }, refreshedRecord: undefined }
  }
  const payload = record.payload as {
    access?: unknown, expires?: unknown, refresh?: unknown,
  } | undefined
  if (payload === null || typeof payload !== 'object' || typeof payload.access !== 'string') {
    throw new Error('stored grant carries no access token')
  }
  let credential: unknown = payload
  let refreshed: CredentialRecord | undefined
  const expires = typeof payload.expires === 'number' ? payload.expires : Number.POSITIVE_INFINITY
  if (expires < Date.now() + TOKEN_EXPIRY_MARGIN_MS && oauth !== undefined && typeof payload.refresh === 'string') {
    credential = await oauth.refresh(credential, AbortSignal.timeout(MODELS_FETCH_TIMEOUT_MS))
    refreshed = { ...record, payload: credential }
  }
  const auth = oauth !== undefined ? await oauth.toAuth(credential) : { apiKey: payload.access }
  const apiKey = auth.apiKey ?? payload.access
  return {
    headers: {
      authorization: `Bearer ${apiKey}`,
      ...(auth.headers ?? {}),
    },
    refreshedRecord: refreshed,
  }
}

/**
 * Live model-catalog synchronization service. One `syncAll()` run covers every
 * configured catalog route with a stored credential; each provider resolves
 * independently, and one provider's failure never blocks the others.
 */
export interface DesktopModelSyncOptions {
  /** Client version sent to versioned provider catalog endpoints. */
  readonly clientVersion: string
}

export class DesktopModelSyncService extends Service {
  constructor(ctx: Context, private readonly options: DesktopModelSyncOptions) {
    super(ctx, 'desktopModelSync')
  }

  /**
   * Sync every configured catalog provider route.
   * @returns one renderer-safe outcome per attempted provider.
   */
  async syncAll(): Promise<DesktopModelSyncResult> {
    const routes = [...new Set([...this.configuredRoutes(), ...await this.credentialedRoutes()])]
    const results = await Promise.all(routes.map(async route => await this.syncProvider(route)))
    return { results: results.filter((result): result is DesktopModelSyncProviderResult => result !== null) }
  }

  /** @returns provider routes configured on the Models page. */
  private configuredRoutes(): readonly string[] {
    const settings = this.ctx.get('settings')
    if (settings === undefined) return []
    const section = settings.get(LLM_SIGNIN_SCOPE)
    if (section === null || typeof section !== 'object' || Array.isArray(section)) return []
    const providers = (section as { providers?: unknown }).providers
    if (providers === null || typeof providers !== 'object' || Array.isArray(providers)) return []
    return Object.keys(providers)
  }

  /** @returns provider routes with a stored credential under the LLM scope. */
  private async credentialedRoutes(): Promise<readonly string[]> {
    const credentials = this.ctx.get('credentials')
    if (credentials === undefined) return []
    const routes: string[] = []
    for (const record of await credentials.listRecords()) {
      if (credentialKeyScope(record.key) !== LLM_SIGNIN_SCOPE) continue
      routes.push(credentialKeyId(record.key))
    }
    return routes
  }

  /**
   * Sync one provider route: fetch its live listing, merge new ids into the
   * route's settings profile, and report what happened.
   * @param route - the provider route id, such as `xai` or `openai-codex`.
   * @returns the outcome, or `null` when the route has nothing to sync.
   */
  private async syncProvider(route: string): Promise<DesktopModelSyncProviderResult | null> {
    const provider = builtinProviders().find(candidate => candidate.id === route)
    if (provider === undefined) return null
    const catalog = provider.getModels()
    if (catalog.length === 0) return null
    const baseUrl = provider.baseUrl
    if (baseUrl === undefined) {
      return { provider: route, status: 'skipped', discovered: [], detail: 'no baseUrl' }
    }
    const credentials = this.ctx.get('credentials')
    if (credentials === undefined) {
      return { provider: route, status: 'skipped', discovered: [], detail: 'no credentials service' }
    }
    try {
      const record = await credentials.readRecord(credentialKey(LLM_SIGNIN_SCOPE, route))
      if (record === undefined) {
        return { provider: route, status: 'skipped', discovered: [], detail: 'no stored credential' }
      }
      const bearer = await resolveBearer(provider.auth.oauth as OAuthLike | undefined, record)
      if (bearer.refreshedRecord !== undefined) {
        await credentials.modifyRecord(credentialKey(LLM_SIGNIN_SCOPE, route), async () => bearer.refreshedRecord)
      }
      const effectiveRecord = bearer.refreshedRecord ?? record
      const headers: Record<string, string> = { ...bearer.headers }
      let modelsUrl = `${baseUrl.replace(/\/$/u, '')}/models`
      if (route === 'openai-codex') {
        const payload = effectiveRecord.kind === 'grant' && effectiveRecord.payload !== null
          && typeof effectiveRecord.payload === 'object'
          ? effectiveRecord.payload as { accountId?: unknown }
          : undefined
        if (typeof payload?.accountId !== 'string' || payload.accountId.length === 0) {
          return { provider: route, status: 'error', discovered: [], detail: 'credential carries no ChatGPT account id' }
        }
        modelsUrl = `${baseUrl.replace(/\/$/u, '')}/codex/models?client_version=${encodeURIComponent(this.options.clientVersion)}`
        headers['chatgpt-account-id'] = payload.accountId
        headers.originator = 'pi'
        headers['user-agent'] = 'pi'
      }
      const response = await fetch(modelsUrl, {
        headers,
        signal: AbortSignal.timeout(MODELS_FETCH_TIMEOUT_MS),
      })
      if (!response.ok) {
        // Never forward the provider's arbitrary body: a proxy can reflect bearer credentials.
        return { provider: route, status: 'error', discovered: [], detail: `HTTP ${String(response.status)}` }
      }
      const body = await response.json() as unknown
      const recordBody = body !== null && typeof body === 'object' && !Array.isArray(body)
        ? body as { data?: unknown, models?: unknown }
        : undefined
      const rows = Array.isArray(body)
        ? body
        : Array.isArray(recordBody?.data) ? recordBody.data
          : Array.isArray(recordBody?.models) ? recordBody.models : []
      type ListedModel = {
        id: string
        eligible: boolean
        profile: Omit<DesktopModelProfileEntry, 'id'>
      }
      const listed = rows
        .map((entry): ListedModel | undefined => {
          if (typeof entry === 'string') {
            return { id: entry, eligible: true, profile: { name: modelDisplayName(route, entry, undefined) } }
          }
          if (entry === null || typeof entry !== 'object') return undefined
          const row = entry as {
            id?: unknown, slug?: unknown, model?: unknown, name?: unknown,
            display_name?: unknown, displayName?: unknown, title?: unknown,
            context_window?: unknown, context_length?: unknown,
            max_output_tokens?: unknown, max_tokens?: unknown,
            input_modalities?: unknown, supported_reasoning_levels?: unknown,
            capabilities?: unknown, supported_in_api?: unknown, visibility?: unknown,
          }
          const id = [row.id, row.slug, row.model, row.name].find(value => typeof value === 'string')
          if (typeof id !== 'string') return undefined
          const advertisedName = [row.display_name, row.displayName, row.title, row.name]
            .find(value => typeof value === 'string' && value !== id)
          const input = Array.isArray(row.input_modalities)
            ? row.input_modalities.filter((value): value is ModelInput => value === 'text' || value === 'image')
            : undefined
          const reasoningEfforts = liveReasoningEfforts(route, row as Record<string, unknown>)
          const contextWindow = positiveInteger(row.context_window, row.context_length)
          const maxTokens = positiveInteger(row.max_output_tokens, row.max_tokens)
          const visibility = typeof row.visibility === 'string' ? row.visibility.toLowerCase() : undefined
          return {
            id,
            eligible: row.supported_in_api !== false && visibility !== 'hide' && visibility !== 'hidden',
            profile: {
              name: modelDisplayName(route, id, typeof advertisedName === 'string' ? advertisedName : undefined),
              ...(contextWindow === undefined ? {} : { contextWindow }),
              ...(maxTokens === undefined ? {} : { maxTokens }),
              ...(input === undefined || input.length === 0 ? {} : { input }),
              ...(reasoningEfforts === undefined ? {} : { reasoningEfforts }),
            },
          }
        })
        .filter((entry): entry is ListedModel => entry !== undefined)
      const discovered = filterLiveModelIds(route, listed.filter(entry => entry.eligible).map(entry => entry.id))
      const discoveredSet = new Set(discovered)
      const discoveredProfiles = Object.fromEntries(listed
        .filter(entry => discoveredSet.has(entry.id))
        .map(entry => [entry.id, entry.profile]))
      const catalogIds = catalog.map(model => model.id)
      const selectableCatalogIds = filterLiveModelIds(route, catalogIds)
      const blockedCatalogIds = new Set(catalogIds.filter(id => !selectableCatalogIds.includes(id)))
      const originalExisting = this.currentModelsProfile(route)
      const existing = originalExisting?.filter(entry => !blockedCatalogIds.has(entry.id))
      const template = pickTemplateModel(catalog as readonly Model<never>[])
      let next = composeNextModels(
        selectableCatalogIds,
        discovered,
        existing,
        template,
        discoveredProfiles,
      )
      if (next === undefined && originalExisting !== undefined && existing?.length !== originalExisting.length) {
        next = existing
      }
      if (next === undefined && originalExisting === undefined && selectableCatalogIds.length !== catalogIds.length) {
        next = selectableCatalogIds.map(id => ({ id }))
      }
      const settings = this.ctx.get('settings')
      if (settings === undefined) {
        return { provider: route, status: 'error', discovered: [], detail: 'no settings service' }
      }
      const configured = settings.get(LLM_SIGNIN_SCOPE)
      const providerProfiles = configured !== null && typeof configured === 'object'
        ? (configured as { providers?: Record<string, { transport?: unknown }> }).providers
        : undefined
      const preferSse = shouldUseCodexSse(route, settings.get('dsh-desktop'), providerProfiles?.[route]?.transport)
      if (next === undefined && !preferSse) {
        return { provider: route, status: 'ok', discovered: [] }
      }
      await settings.update(LLM_SIGNIN_SCOPE, {
        providers: { [route]: {
          ...(preferSse ? { transport: 'sse' } : {}),
          ...(next === undefined ? {} : { models: next.map(entry => ({
            id: entry.id,
            ...(entry.name === undefined ? {} : { name: entry.name }),
            ...(entry.contextWindow === undefined ? {} : { contextWindow: entry.contextWindow }),
            ...(entry.maxTokens === undefined ? {} : { maxTokens: entry.maxTokens }),
            ...(entry.input === undefined || entry.input.length === 0 ? {} : { input: [...entry.input] }),
            ...(entry.reasoningEfforts === undefined ? {} : { reasoningEfforts: entry.reasoningEfforts }),
          })) }),
        } },
      })
      const builtin = new Set(catalog.map(model => model.id))
      return {
        provider: route,
        status: 'ok',
        discovered: discovered.filter(id => !builtin.has(id)),
      }
    } catch (cause) {
      const detail = cause instanceof Error ? cause.message : String(cause)
      return { provider: route, status: 'error', discovered: [], detail }
    }
  }

  /**
   * Read one route's current `models` profile entries, when a listing exists.
   * @param route - the provider route id.
   * @returns the configured entries, or `undefined` when the route serves the
   *   installed catalog unchanged.
   */
  private currentModelsProfile(route: string): DesktopModelProfileEntry[] | undefined {
    const settings = this.ctx.get('settings')
    if (settings === undefined) return undefined
    const section = settings.get(LLM_SIGNIN_SCOPE)
    if (section === null || typeof section !== 'object' || Array.isArray(section)) return undefined
    const providers = (section as { providers?: unknown }).providers
    if (providers === null || typeof providers !== 'object' || Array.isArray(providers)) return undefined
    const profile = (providers as Record<string, unknown>)[route]
    if (profile === null || typeof profile !== 'object' || Array.isArray(profile)) return undefined
    const models = (profile as { models?: unknown }).models
    if (!Array.isArray(models) || models.length === 0) return undefined
    const entries: DesktopModelProfileEntry[] = []
    for (const entry of models) {
      if (entry === null || typeof entry !== 'object') continue
      const id = (entry as { id?: unknown }).id
      if (typeof id !== 'string') continue
      entries.push(entry as DesktopModelProfileEntry)
    }
    return entries
  }
}

declare module '@deepseek-ai/cordis' {
  interface Context {
    /** Live model-catalog synchronization for catalog provider routes. */
    desktopModelSync: DesktopModelSyncService
  }
}

export default DesktopModelSyncService
