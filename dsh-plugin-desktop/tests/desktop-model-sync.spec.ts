import type { Model } from '@earendil-works/pi-ai'
import { describe, expect, it } from 'vitest'
import {
  composeNextModels,
  filterLiveModelIds,
  liveReasoningEfforts,
  pickTemplateModel,
  shouldUseCodexSse,
  type DesktopModelProfileEntry,
} from '../src/desktop-model-sync.ts'

const template = {
  id: 'grok-4.6',
  reasoning: true,
  input: ['text', 'image'],
  contextWindow: 500_000,
  maxTokens: 100_000,
  thinkingLevelMap: { off: null, minimal: null, low: 'low', medium: 'medium', high: 'high', xhigh: 'xhigh', max: null },
} as unknown as Model<never>

describe('pickTemplateModel', () => {
  it('prefers the widest context window, latest id on ties', () => {
    const models = [
      { id: 'grok-4.3', contextWindow: 100_000, maxTokens: 30_000 },
      { id: 'grok-4.6', contextWindow: 500_000, maxTokens: 100_000 },
      { id: 'grok-4.5', contextWindow: 500_000, maxTokens: 90_000 },
    ] as never[]
    expect((pickTemplateModel(models as never)?.id)).toBe('grok-4.6')
  })

  it('returns undefined for an empty catalog', () => {
    expect(pickTemplateModel([])).toBeUndefined()
  })
})

describe('filterLiveModelIds', () => {
  it('keeps stable xAI chat models and drops media or internal deployments', () => {
    expect(filterLiveModelIds('xai', [
      'grok-4.7',
      'grok-4.20-0309-reasoning',
      'grok-4.20-multi-agent-0309',
      'grok-build-0.1',
      'grok-imagine-image-2.0',
      'grok-imagine-video',
    ])).toEqual(['grok-4.7'])
  })

  it('keeps Codex text/code families and drops media models', () => {
    expect(filterLiveModelIds('openai-codex', [
      'gpt-6-sol', 'gpt-6-luna', 'codex-next', 'gpt-image-2', 'whisper-2',
      'codex-auto-review', 'gpt-reserve',
    ])).toEqual(['codex-next', 'gpt-6-luna', 'gpt-6-sol'])
  })
})

describe('live provider capabilities', () => {
  it('reads xAI reasoning efforts from nested capabilities without inventing off', () => {
    const row = { capabilities: { reasoning_effort: ['low', 'medium', 'high', 'xhigh'], default_reasoning_effort: 'high' } }
    expect(liveReasoningEfforts('xai', row)).toEqual({ low: 'low', medium: 'medium', high: 'high', xhigh: 'xhigh' })
  })

  it('uses SSE for proxied Codex unless a transport was explicitly selected', () => {
    const desktop = { modelProxyUrl: 'http://proxy.local:8001', modelProxyProviders: ['xai', 'openai-codex'] }
    expect(shouldUseCodexSse('openai-codex', desktop, undefined)).toBe(true)
    expect(shouldUseCodexSse('xai', desktop, undefined)).toBe(false)
    expect(shouldUseCodexSse('openai-codex', desktop, 'websocket')).toBe(false)
    expect(shouldUseCodexSse('openai-codex', { ...desktop, modelProxyProviders: ['xai'] }, undefined)).toBe(false)
  })
})

describe('composeNextModels', () => {
  const builtin = ['grok-4.3', 'grok-4.5', 'grok-4.6']

  it('returns undefined when nothing new was discovered against a live listing', () => {
    expect(composeNextModels(builtin, ['grok-4.6', 'grok-4.3'], undefined, template)).toBeUndefined()
  })

  it('keeps an existing listing untouched when it already covers everything discovered', () => {
    const existing: DesktopModelProfileEntry[] = [{ id: 'grok-4.6' }, { id: 'my-grok', name: 'custom model', contextWindow: 1, maxTokens: 1 }]
    expect(composeNextModels(builtin, ['grok-4.6', 'my-grok'], existing, template)).toBeUndefined()
  })

  it('materializes the catalog plus discovered ids on first sync', () => {
    const next = composeNextModels(builtin, ['grok-4.6', 'grok-5'], undefined, template)
    expect(next).toBeDefined()
    expect(next?.map(entry => entry.id)).toEqual(['grok-4.3', 'grok-4.5', 'grok-4.6', 'grok-5'])
    // Catalog ids stay bare so they keep inheriting metadata across upgrades.
    expect(next?.[0]).toEqual({ id: 'grok-4.3' })
    // A discovered id inherits the template's complete usable capability set.
    expect(next?.[3]).toMatchObject({
      id: 'grok-5',
      name: 'grok-5',
      contextWindow: 500_000,
      maxTokens: 100_000,
      input: ['text', 'image'],
      reasoningEfforts: { low: 'low', medium: 'medium', high: 'high', xhigh: 'xhigh' },
    })
  })

  it('uses the provider display name for a discovered model', () => {
    const next = composeNextModels(
      builtin,
      ['grok-4.7'],
      undefined,
      template,
      { 'grok-4.7': { name: 'Grok-4.7' } },
    )
    expect(next?.at(-1)?.name).toBe('Grok-4.7')
  })

  it('uses live input, capacity, and reasoning levels instead of template guesses', () => {
    const next = composeNextModels(
      ['gpt-5.6-sol'],
      ['gpt-6-sol'],
      undefined,
      template,
      {
        'gpt-6-sol': {
          name: 'GPT-6-Sol',
          contextWindow: 272_000,
          input: ['text', 'image'],
          reasoningEfforts: { low: 'low', medium: 'medium', high: 'high', xhigh: 'xhigh', max: 'max' },
        },
      },
    )
    expect(next?.at(-1)).toMatchObject({
      name: 'GPT-6-Sol',
      contextWindow: 272_000,
      maxTokens: 100_000,
      input: ['text', 'image'],
      reasoningEfforts: { low: 'low', medium: 'medium', high: 'high', xhigh: 'xhigh', max: 'max' },
    })
  })

  it('appends only new ids to an existing listing, preserving user entries verbatim', () => {
    const existing: DesktopModelProfileEntry[] = [
      { id: 'grok-4.6', name: 'workhorse', contextWindow: 8, maxTokens: 4 },
    ]
    const next = composeNextModels(builtin, ['grok-4.6', 'grok-4.7', 'grok-5'], existing, template)
    expect(next?.map(entry => entry.id)).toEqual(['grok-4.6', 'grok-4.7', 'grok-5'])
    expect(next?.[0]).toEqual(existing[0])
    expect(next?.[1]?.contextWindow).toBe(500_000)
  })

  it('removes previously discovered entries absent from the filtered live set', () => {
    const existing: DesktopModelProfileEntry[] = [
      { id: 'grok-4.6' },
      { id: 'grok-imagine-video', name: 'grok-imagine-video', contextWindow: 1, maxTokens: 1 },
      { id: 'grok-4.7', name: 'grok-4.7', contextWindow: 1, maxTokens: 1 },
    ]
    expect(composeNextModels(builtin, ['grok-4.7'], existing, template)?.map(entry => entry.id))
      .toEqual(['grok-4.6', 'grok-4.7'])
  })

  it('prunes formerly discovered human-readable model names when they disappear', () => {
    const existing: DesktopModelProfileEntry[] = [
      { id: 'gpt-5.6-sol' },
      { id: 'gpt-6-sol', name: 'GPT-6-Sol', reasoningEfforts: { low: 'low' } },
      { id: 'custom-model', name: 'My Deployment' },
    ]
    expect(composeNextModels(['gpt-5.6-sol'], [], existing, template)?.map(entry => entry.id))
      .toEqual(['gpt-5.6-sol', 'custom-model'])
  })

  it('ignores empty and duplicate discovered ids', () => {
    const next = composeNextModels(builtin, ['', 'grok-5', 'grok-5'], undefined, template)
    expect(next?.filter(entry => entry.id === 'grok-5')).toHaveLength(1)
  })
})
