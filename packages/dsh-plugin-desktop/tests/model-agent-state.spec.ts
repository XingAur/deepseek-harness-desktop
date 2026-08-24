import { describe, expect, it } from 'vitest'
import { deriveProviderState, type ProviderMetadata } from '../src/client/model-agent/state'

const codex: ProviderMetadata = {
  providerId: 'codex',
  displayName: 'Codex',
  cliCommand: 'codex',
  adapterProtocol: 'dsh-agent-adapter/v1',
  credentialSupported: true,
  developerOnly: false,
  credentialStatus: 'not-configured',
}

describe('model and agent center state', () => {
  it('distinguishes empty, configured, and secure-store-only credentials', () => {
    expect(deriveProviderState(codex, null, {})).toMatchObject({ kind: 'not-configured', label: '未配置' })
    expect(deriveProviderState({ ...codex, credentialStatus: 'configured' }, null, {})).toMatchObject({ kind: 'configured-unverified', label: '已配置，未验证' })
    expect(deriveProviderState({ ...codex, credentialStatus: 'configured' }, null, {
      credentialTest: { tested: true, testKind: 'secure-store-presence' },
    })).toMatchObject({ kind: 'configured-unverified', label: '已配置，未联网验证' })
  })

  it('maps bounded diagnostics to actionable states', () => {
    const configured = { ...codex, credentialStatus: 'configured' as const }
    expect(deriveProviderState(configured, { code: 'invalid-key', message: '凭证无效' }, {})).toMatchObject({ kind: 'invalid-credential' })
    expect(deriveProviderState(configured, { code: 'quota-exhausted', message: '额度已用尽' }, {})).toMatchObject({ kind: 'quota-exhausted' })
    expect(deriveProviderState(configured, { code: 'missing-cli', message: '未找到 CLI' }, {})).toMatchObject({ kind: 'missing-cli' })
    expect(deriveProviderState(configured, { code: 'version-too-old', message: '版本过低' }, {})).toMatchObject({ kind: 'incompatible' })
    expect(deriveProviderState(configured, null, { online: false })).toMatchObject({ kind: 'offline', label: '离线' })
  })

  it('keeps developer-only providers out of stable mode', () => {
    const developer = { ...codex, providerId: 'claude-cli-dev', developerOnly: true }
    expect(deriveProviderState(developer, null, { developerMode: false })).toMatchObject({ kind: 'developer-only', label: '开发者专用' })
    expect(deriveProviderState(developer, null, { developerMode: true })).not.toMatchObject({ kind: 'developer-only' })
  })
})
