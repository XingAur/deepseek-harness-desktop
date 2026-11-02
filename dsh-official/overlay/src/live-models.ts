/** Live model lists for OAuth-logged providers, using the current DSH home's stored tokens. */

import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { parse } from 'yaml'
import { type OauthLoginProvider } from './oauth-providers.ts'

const REQUEST_TIMEOUT_MS = 15_000

interface OAuthCredential {
  readonly access: string
  readonly accountId: string | null
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

/**
 * Read one provider's stored OAuth token from the active home's credential
 * store. Only the current DSH_HOME is read; the 2.0.x `~/.dsh` store is never
 * touched.
 */
export async function readOAuthCredential(home: string, provider: OauthLoginProvider): Promise<OAuthCredential | null> {
  let document: unknown
  try {
    document = parse(await readFile(join(home, '.credentials.yaml'), 'utf8'))
  } catch {
    return null
  }
  const record = asRecord(asRecord(asRecord(document)['records'])[`llm-pi-ai/${provider}`])
  const payload = asRecord(record.payload)
  const credential = asRecord(payload.credential)
  const access = typeof payload.access === 'string' ? payload.access
    : typeof credential.access === 'string' ? credential.access : null
  if (access === null || access === '') return null
  const accountId = typeof payload.accountId === 'string' ? payload.accountId : null
  return { access, accountId }
}

/** Extract model ids from an OpenAI-style `{data:[{id}]}` or enriched `{models:{id:{}}}` payload. */
export function extractModelIds(payload: unknown): string[] {
  const record = asRecord(payload)
  const ids = new Set<string>()
  const rows: unknown[] = Array.isArray(payload) ? payload
    : Array.isArray(record.data) ? record.data
      : Array.isArray(record.models) ? record.models
        : []
  for (const item of rows) {
    if (typeof item === 'string' && item !== '') {
      ids.add(item)
      continue
    }
    const row = asRecord(item)
    const id = [row.id, row.slug, row.model].find(value => typeof value === 'string' && value !== '')
    if (typeof id === 'string') ids.add(id)
  }
  const models = asRecord(record.models)
  for (const key of Object.keys(models)) ids.add(key)
  return [...ids].sort()
}

/** Model-list endpoints per OAuth provider, most preferred first. */
function endpointsFor(provider: OauthLoginProvider): readonly { readonly url: string; readonly accountHeader: boolean }[] {
  if (provider === 'xai') {
    return [{ url: 'https://api.x.ai/v1/models', accountHeader: false }]
  }
  return [
    { url: 'https://chatgpt.com/backend-api/codex/models?client_version=1.0.0', accountHeader: true },
    { url: 'https://chatgpt.com/backend-api/models?client_version=1.0.0', accountHeader: true },
    { url: 'https://api.openai.com/v1/models', accountHeader: false },
  ]
}

/**
 * Fetch the newest model ids for an OAuth-logged provider. Degrades to an
 * empty list on any failure — the caller merges configured ids regardless.
 */
async function loadJson(
  url: string,
  headers: Record<string, string>,
): Promise<{ readonly status: number; readonly body: unknown }> {
  const response = await fetch(url, { headers, signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) })
  return { status: response.status, body: await response.json().catch(() => null) }
}

export async function fetchLiveModelIds(
  provider: OauthLoginProvider,
  credential: OAuthCredential,
): Promise<{ readonly ids: string[]; readonly error: string | null }> {
  let lastError: string | null = null
  for (const endpoint of endpointsFor(provider)) {
    try {
      const headers: Record<string, string> = {
        authorization: `Bearer ${credential.access}`,
        accept: 'application/json',
        originator: 'pi',
        'user-agent': 'pi',
      }
      if (endpoint.accountHeader && credential.accountId !== null) {
        headers['chatgpt-account-id'] = credential.accountId
      }
      const response = await loadJson(endpoint.url, headers)
      if (response.status < 200 || response.status >= 300) {
        lastError = `HTTP ${String(response.status)}`
        continue
      }
      const models = extractModelIds(response.body)
      if (models.length > 0) return { ids: models, error: null }
      lastError = 'empty-list'
    } catch {
      lastError = 'network'
    }
  }
  return { ids: [], error: lastError ?? 'live-empty' }
}
