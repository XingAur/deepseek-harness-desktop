import type { FixtureTlsMaterial } from './tls-fixture.mjs'

export interface AppUpdateFixtureRequest {
  readonly method: string
  readonly path: string
  readonly at: string
}

export type AppUpdateSigner = (payload: Uint8Array) => Promise<string>

export interface AppUpdateFixture {
  readonly endpoint: string
  readonly caCertificate: string
  publish(version: string, payload: Uint8Array): Promise<void>
  requests(): readonly AppUpdateFixtureRequest[]
  clearRequests(): void
  close(): Promise<void>
}

export function startAppUpdateFixture(options?: {
  tls?: FixtureTlsMaterial
  signer?: AppUpdateSigner
}): Promise<AppUpdateFixture>

export function createTauriUpdateSigner(options: {
  privateKeyPath: string
  password: string
  cliPath?: string
}): AppUpdateSigner
