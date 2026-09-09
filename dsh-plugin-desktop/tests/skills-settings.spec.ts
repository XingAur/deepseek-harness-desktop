import type { IncomingMessage, ServerResponse } from 'node:http'
import { describe, expect, it, vi } from 'vitest'
import DesktopSettingsController, {
  type DesktopSettingsControllerBootstrap,
} from '../src/desktop-settings-controller.ts'
import { handleDesktopSkillsListRequest } from '../src/desktop-settings-route.ts'
import {
  createDesktopSettingsApi,
  parseDesktopSkillsView,
} from '../src/client/desktop-settings-api.ts'

const ORIGIN = 'http://127.0.0.1:43120'

function getRequest(origin = ORIGIN): IncomingMessage {
  return {
    method: 'GET',
    headers: { origin, host: '127.0.0.1:43120' },
    socket: { remoteAddress: '127.0.0.1' },
  } as unknown as IncomingMessage
}

function response(): ServerResponse & {
  body: string
  end: ReturnType<typeof vi.fn>
  setHeader: ReturnType<typeof vi.fn>
} {
  const res = {
    body: '',
    statusCode: 200,
    setHeader: vi.fn(),
    end: vi.fn((body?: string) => { res.body = body ?? '' }),
  }
  return res as unknown as ServerResponse & typeof res
}

const CATALOG = {
  available: true,
  skills: [
    {
      name: 'ygt',
      description: 'YGT platform workflow skills.',
      whenToUse: null,
      modelInvocable: true,
      userInvocable: true,
      source: 'user-agents',
    },
    {
      name: 'release-gate',
      description: 'Release verification flow.',
      whenToUse: 'Before cutting a release.',
      modelInvocable: false,
      userInvocable: true,
      source: 'project-dsh',
    },
  ],
}

/** Overrides keep each property's type but never add `undefined` (exactOptionalPropertyTypes). */
function baseBootstrap(): DesktopSettingsControllerBootstrap {
  return {
    profiles: {
      current: { name: 'desktop', dir: '/profiles/desktop' },
      list: () => [],
      create: () => ({
        name: 'created',
        dir: '/profiles/created',
        exists: true,
        bundles: [],
        webCapable: true,
      }),
      prepareSelection: () => Promise.resolve({ restartRequired: false, restart: async () => undefined }),
    },
    readMarket: () => ({ requested: 'disabled', effective: 'disabled', legacyDefaulted: false }),
    selectMarket: async () => ({ requested: 'disabled', effective: 'disabled', legacyDefaulted: false }),
    readWeb: () => ({
      localUrl: 'http://127.0.0.1:43120',
      lanUrls: [],
      lanState: 'inactive',
      lanError: null,
      lanCaFingerprint: null,
      lanCaUrls: [],
    }),
    scheduleRestart: () => undefined,
    scheduleRecoveryRestart: () => undefined,
    openTerminal: () => undefined,
    reloadRenderer: () => undefined,
    toggleDeveloperTools: () => undefined,
    exportDiagnostics: () => undefined,
  }
}

describe('desktop skills controller', () => {
  it('returns an unavailable projection when the composition mounts no registry', async () => {
    const controller = new DesktopSettingsController(baseBootstrap())
    await expect(controller.listSkills()).resolves.toEqual({ available: false, skills: [] })
  })

  it('projects the catalog returned by the launcher read closure', async () => {
    const controller = new DesktopSettingsController({
      ...baseBootstrap(),
      readSkills: async () => CATALOG,
    })
    await expect(controller.listSkills()).resolves.toEqual(CATALOG)
  })
})

describe('desktop skills route', () => {
  it('serves the catalog over loopback same-origin GET', async () => {
    const controller = new DesktopSettingsController({
      ...baseBootstrap(),
      readSkills: async () => CATALOG,
    })
    const res = response()
    await handleDesktopSkillsListRequest(getRequest(), res, ORIGIN, controller)
    expect(res.statusCode).toBe(200)
    expect(JSON.parse(res.body)).toEqual(CATALOG)
  })

  it('rejects cross-origin and non-GET requests without reading the catalog', async () => {
    const readSkills = vi.fn(async () => CATALOG)
    const controller = new DesktopSettingsController({ ...baseBootstrap(), readSkills })
    for (const req of [getRequest('https://example.com'), { method: 'POST', headers: { origin: ORIGIN } } as IncomingMessage]) {
      const res = response()
      await handleDesktopSkillsListRequest(req, res, ORIGIN, controller)
      expect(res.statusCode).toBe(req.method === 'GET' ? 403 : 405)
    }
    expect(readSkills).not.toHaveBeenCalled()
  })

  it('maps controller failure to a generic 500 without native paths', async () => {
    const controller = new DesktopSettingsController({
      ...baseBootstrap(),
      readSkills: async () => { throw new Error('/private/secret collapsed') },
    })
    const res = response()
    await handleDesktopSkillsListRequest(getRequest(), res, ORIGIN, controller)
    expect(res.statusCode).toBe(500)
    expect(JSON.parse(res.body)).toEqual({ error: 'skill catalog unavailable' })
  })
})

describe('skill catalog response parsing', () => {
  it('accepts a valid catalog', () => {
    expect(parseDesktopSkillsView(CATALOG)).toEqual(CATALOG)
  })

  it('accepts an unavailable catalog', () => {
    expect(parseDesktopSkillsView({ available: false, skills: [] })).toEqual({ available: false, skills: [] })
  })

  it('rejects duplicate skill names', () => {
    expect(() => parseDesktopSkillsView({ available: true, skills: [CATALOG.skills[0], CATALOG.skills[0]] }))
      .toThrow(/duplicate skill/u)
  })

  it('rejects malformed entries and extra keys', () => {
    expect(() => parseDesktopSkillsView({ available: true, skills: [{ ...CATALOG.skills[0], name: '' }] }))
      .toThrow(/invalid skill entry/u)
    expect(() => parseDesktopSkillsView({ ...CATALOG, extra: true })).toThrow(/invalid Desktop skill catalog/u)
    expect(() => parseDesktopSkillsView({ available: 'yes', skills: [] })).toThrow(/invalid Desktop skill catalog/u)
  })
})

describe('desktop skills client api', () => {
  it('parses a successful GET from the skills endpoint', async () => {
    const fetcher = vi.fn(async (_path: string) => new Response(JSON.stringify(CATALOG), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })) as unknown as (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
    const api = createDesktopSettingsApi(fetcher)
    await expect(api.listSkills()).resolves.toEqual(CATALOG)
    expect(fetcher).toHaveBeenCalledWith('/api/desktop/skills', expect.objectContaining({ method: 'GET' }))
  })
})
