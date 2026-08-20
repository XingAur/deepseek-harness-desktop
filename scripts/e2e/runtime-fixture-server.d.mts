import type { KeyObject } from 'node:crypto'
import type { FixtureTlsMaterial } from './tls-fixture.mjs'

export type RuntimeScenario =
  | 'success'
  | 'bad-signature'
  | 'tampered-archive'
  | 'wrong-target'
  | 'http-redirect'
  | 'unknown-host'
  | 'disconnect-once'
  | 'delayed'
  | 'probe-exit'

export interface FixtureRequest {
  readonly method: string
  readonly path: string
  readonly range?: string
  readonly at: string
}

export interface RuntimeFixture {
  readonly version: string
  readonly url: string
  readonly manifestUrl: string
  readonly publicKey: string
  readonly caCertificate: string
  setScenario(scenario: RuntimeScenario): void
  requests(): readonly FixtureRequest[]
  clearRequests(): void
  close(): Promise<void>
}

export interface RuntimeFixtureOptions {
  archive?: Uint8Array
  signature?: string
  version?: string
  delayMs?: number
  tls?: FixtureTlsMaterial
  signing?: { privateKey: KeyObject; publicKey: string }
  healthPath?: string
}

export function startRuntimeFixture(options?: RuntimeFixtureOptions): Promise<RuntimeFixture>
