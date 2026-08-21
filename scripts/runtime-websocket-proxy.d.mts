import type { Server } from 'node:http'

export const RUNTIME_EVENT_PATHS: ReadonlySet<string>

export function attachRuntimeWebSocketProxy(
  server: Server,
  options: { hostname?: string; port: number },
): () => void
