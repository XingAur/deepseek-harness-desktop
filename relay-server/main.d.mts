/** Type surface for embedding the relay in the desktop integration tests. */

import type { Server } from 'node:http'

interface WebSocketLike { readonly readyState: number }
interface WebSocketServerLike {
  handleUpgrade(request: unknown, socket: unknown, head: unknown, callback: (ws: WebSocketLike) => void): void
}

export interface RelayApp {
  readonly httpServer: Server
  readonly pairings: Map<string, unknown>
  stop(): void
}

export function createRelayApp(options: {
  WebSocketServer: new (options: { noServer: boolean }) => WebSocketServerLike
  WebSocket: { readonly OPEN: number }
}): RelayApp
