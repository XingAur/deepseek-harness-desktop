import type { FixtureTlsMaterial } from './tls-fixture.mjs'

export interface ModelFixtureRequest {
  readonly method: string
  readonly path: string
  readonly body: string
  readonly at: string
}

export interface FakeDeepSeekFixture {
  readonly url: string
  readonly caCertificate: string
  requests(): readonly ModelFixtureRequest[]
  clearRequests(): void
  close(): Promise<void>
}

export function startFakeDeepSeek(options?: {
  text?: string
  tls?: FixtureTlsMaterial
}): Promise<FakeDeepSeekFixture>
