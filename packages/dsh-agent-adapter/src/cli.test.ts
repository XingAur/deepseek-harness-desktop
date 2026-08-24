import { PassThrough } from 'node:stream'
import { describe, expect, it } from 'vitest'
import { PROTOCOL_VERSION } from './protocol.js'
import { runAgentAdapter } from './cli.js'

describe('private agent adapter entrypoint', () => {
  it('connects the process stdio contract to the bounded worker', async () => {
    const input = new PassThrough()
    const stdout = new PassThrough()
    const stderr = new PassThrough()
    const output: Buffer[] = []
    stdout.on('data', (chunk) => output.push(Buffer.from(chunk)))

    const worker = runAgentAdapter({ input, stdout, stderr })
    input.end(`${JSON.stringify({
      protocolVersion: PROTOCOL_VERSION,
      requestId: 'handshake',
      sessionId: 'session',
      sequence: 0,
      type: 'handshake',
      payload: { adapterKind: 'mock' },
    })}\n`)
    await worker

    const response = JSON.parse(Buffer.concat(output).toString('utf8')) as { type: string; payload: { accepted: boolean } }
    expect(response).toMatchObject({ type: 'response.ok', payload: { accepted: true } })
  })
})
