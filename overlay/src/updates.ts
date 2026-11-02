/** Version and update discovery for the overlay: official baseline plus the local portable build. */

import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-connection'
import type {} from '@deepseek-ai/dsh-host-webserver'
import { compareBuildVersions, compareOfficialVersions, currentBuildMeta, type BuildMeta } from './build-meta.ts'
import { resolveDshHome } from './dsh-home.ts'
import { readOverlayConfigSync } from './overlay-config.ts'
import { rejectUnauthorized, sameOriginMutatingRequest, writeJson } from './http.ts'

const OFFICIAL_REPO = 'https://api.github.com/repos/deepseek-ai/deepseek-harness'
const BUILD_REPO = 'https://api.github.com/repos/XingAur/deepseek-harness-desktop'
const CACHE_MS = 30 * 60 * 1000
const FETCH_TIMEOUT_MS = 10_000
const NOTES_LIMIT = 1600

export interface UpdateCandidate {
  readonly version: string
  readonly url: string
  readonly name: string
  readonly notes: string
  readonly publishedAt: string | null
}

export interface UpdateState {
  readonly current: BuildMeta
  readonly official: UpdateCandidate | null
  readonly build: UpdateCandidate | null
  readonly officialUpdate: boolean
  readonly buildUpdate: boolean
  readonly checkedAt: string | null
  readonly error: string | null
}

interface GithubRelease {
  readonly tag_name?: unknown
  readonly html_url?: unknown
  readonly name?: unknown
  readonly body?: unknown
  readonly published_at?: unknown
  readonly draft?: unknown
}

function trimNotes(body: unknown): string {
  if (typeof body !== 'string') return ''
  const withoutChangelog = body.split(/Full Changelog:/u)[0] ?? body
  const text = withoutChangelog.replace(/\r\n/gu, '\n').replace(/\n{3,}/gu, '\n\n').trim()
  return text.length > NOTES_LIMIT ? `${text.slice(0, NOTES_LIMIT)}…` : text
}

async function fetchJson(url: string): Promise<unknown> {
  const response = await fetch(url, {
    headers: { accept: 'application/vnd.github+json', 'user-agent': 'dsh-overlay-custom' },
    redirect: 'error',
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  })
  if (!response.ok) throw new Error(`HTTP ${String(response.status)}`)
  return response.json()
}

function parseReleases(value: unknown): readonly GithubRelease[] {
  return Array.isArray(value) ? (value as readonly GithubRelease[]) : []
}

function candidateOf(release: GithubRelease, prefix: string): UpdateCandidate | null {
  if (typeof release.tag_name !== 'string' || !release.tag_name.startsWith(prefix)) return null
  if (typeof release.html_url !== 'string') return null
  return {
    version: release.tag_name.slice(prefix.length),
    url: release.html_url,
    name: typeof release.name === 'string' && release.name !== '' ? release.name : release.tag_name,
    notes: trimNotes(release.body),
    publishedAt: typeof release.published_at === 'string' ? release.published_at : null,
  }
}

function isDraft(release: GithubRelease): boolean {
  return release.draft === true
}

async function latestOfficial(): Promise<UpdateCandidate | null> {
  const releases = parseReleases(await fetchJson(`${OFFICIAL_REPO}/releases?per_page=30`))
  let best: UpdateCandidate | null = null
  for (const release of releases) {
    if (isDraft(release)) continue
    const candidate = candidateOf(release, 'dsh-v')
    if (candidate === null) continue
    if (best === null || compareOfficialVersions(candidate.version, best.version) > 0) best = candidate
  }
  return best
}

async function latestBuild(): Promise<UpdateCandidate | null> {
  const releases = parseReleases(await fetchJson(`${BUILD_REPO}/releases?per_page=30`))
  let best: UpdateCandidate | null = null
  for (const release of releases) {
    if (isDraft(release)) continue
    const candidate = candidateOf(release, 'official-v')
    if (candidate === null) continue
    if (best === null || compareBuildVersions(candidate.version, best.version) > 0) best = candidate
  }
  return best
}

/** Public releases of the official app and of this repository, compared with the running build. */
export async function discoverUpdates(current: BuildMeta): Promise<UpdateState> {
  const [official, build] = await Promise.all([latestOfficial(), latestBuild()]).catch(() => [null, null] as const)
  return {
    current,
    official,
    build,
    officialUpdate: official !== null && compareOfficialVersions(official.version, current.officialVersion) > 0,
    buildUpdate: build !== null && compareBuildVersions(build.version, current.buildVersion) > 0,
    checkedAt: new Date().toISOString(),
    error: official === null && build === null ? 'network' : null,
  }
}

/** Read once, then reuse a cached answer for half an hour. */
export function applyOverlayUpdates(ctx: Context): void {
  const current = currentBuildMeta()
  const config = readOverlayConfigSync(resolveDshHome())
  const updates = config.updates !== null && typeof config.updates === 'object' && !Array.isArray(config.updates)
    ? config.updates as Record<string, unknown>
    : {}
  const autoCheck = updates.autoCheck !== false
  let cache: { readonly at: number; readonly state: UpdateState } | null = null
  let inflight: Promise<UpdateState> | null = null

  const resolve = async (): Promise<UpdateState> => {
    const state = await discoverUpdates(current)
    cache = { at: Date.now(), state }
    return state
  }

  const refresh = async (): Promise<UpdateState> => {
    if (inflight !== null) return inflight
    inflight = resolve().finally(() => { inflight = null })
    return inflight
  }

  const state = async (force: boolean): Promise<UpdateState> => {
    if (!force && cache !== null && Date.now() - cache.at < CACHE_MS) return cache.state
    try {
      return await refresh()
    } catch (cause) {
      return {
        current,
        official: null,
        build: null,
        officialUpdate: false,
        buildUpdate: false,
        checkedAt: new Date().toISOString(),
        error: cause instanceof Error ? cause.message : 'failed',
      }
    }
  }

  const reject = (req: IncomingMessage, res: ServerResponse): boolean =>
    rejectUnauthorized(req, res, ctx.connection.requestRejection(req))

  ctx.effect(() => ctx.webServer.register({
    kind: 'exact',
    path: '/api/desktop/updates/state',
    handler: (req, res) => {
      if (reject(req, res)) return
      if (req.method === 'GET') {
        void state(false).then(value => { writeJson(res, 200, { ok: true, ...value }) })
        return
      }
      if (req.method !== 'POST') {
        res.writeHead(405, { allow: 'GET, POST', 'cache-control': 'no-store' })
        res.end('method not allowed')
        return
      }
      if (!sameOriginMutatingRequest(req)) {
        res.writeHead(403, { 'cache-control': 'no-store' })
        res.end('forbidden')
        return
      }
      void state(true).then(value => { writeJson(res, 200, { ok: true, ...value }) })
    },
  }), 'dsh-overlay-custom: update state route')

  if (!autoCheck) return
  ctx.effect(() => {
    const timer = setTimeout(() => {
      void state(false).catch(() => { ctx.logger.warn('dsh-overlay-custom: update check failed') })
    }, 5_000)
    timer.unref?.()
    return () => { clearTimeout(timer) }
  }, 'dsh-overlay-custom: startup update check')
}
