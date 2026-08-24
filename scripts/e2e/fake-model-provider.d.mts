import type { FixtureTlsMaterial } from './tls-fixture.mjs'

export type FakeProviderFamily = 'deepseek' | 'openai' | 'claude' | 'compatible'

export interface ModelFixtureRequest {
  readonly method: string
  readonly path: string
  readonly body: string
  readonly at: string
}

export interface FakeModelProviderFixture {
  readonly family: FakeProviderFamily
  readonly url: string
  readonly caCertificate: string
  requests(): readonly ModelFixtureRequest[]
  clearRequests(): void
  close(): Promise<void>
}

export function startFakeModelProvider(options?: {
  family?: FakeProviderFamily
  text?: string
  status?: number
  includeUsage?: boolean
  tls?: FixtureTlsMaterial
}): Promise<FakeModelProviderFixture>
