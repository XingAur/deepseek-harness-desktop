import type { RequestListener } from 'node:http'

export interface FixtureTlsMaterial {
  readonly key: string
  readonly cert: string
  readonly caCertificate: string
  readonly fingerprint: string
}

export interface LoopbackHttpsServer {
  readonly url: string
  readonly port: number
  readonly tls: FixtureTlsMaterial
  close(): Promise<void>
}

export function createFixtureTlsMaterial(): FixtureTlsMaterial
export function startLoopbackHttps(
  handler: RequestListener,
  tls?: FixtureTlsMaterial,
): Promise<LoopbackHttpsServer>
