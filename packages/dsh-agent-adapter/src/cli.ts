import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'
import type { Readable, Writable } from 'node:stream'
import { runMockWorker, type MockWorkerIo } from './worker.js'
import { runCodexCliWorker } from './codex-worker.js'
import { redactDiagnostic } from './redaction.js'

export type AdapterWorkerIo = MockWorkerIo

export interface RunAdapterOptions {
  input?: Readable
  stdout?: Writable
  stderr?: Writable
  env?: NodeJS.ProcessEnv
  argv?: string[]
}

/**
 * Dispatch to the mock preview worker or the real Codex CLI worker.
 *
 * `--dsh-codex-cli=<absolute path>` selects the real Codex adapter and points
 * at the discovered Codex CLI executable. Without the flag the protocol mock
 * worker keeps its preview behaviour.
 */
export async function runAgentAdapter(options: RunAdapterOptions = {}): Promise<void> {
  const io: AdapterWorkerIo = {
    input: options.input ?? process.stdin,
    stdout: options.stdout ?? process.stdout,
    stderr: options.stderr ?? process.stderr,
  }
  const argv = options.argv ?? process.argv
  const codexCli = parseCodexCliArgument(argv, options.env ?? process.env)
  if (codexCli !== null) {
    await runCodexCliWorker(io, { cliPath: codexCli })
    return
  }
  await runMockWorker(io)
}

export function parseCodexCliArgument(argv: string[], env: NodeJS.ProcessEnv): string | null {
  const prefix = '--dsh-codex-cli='
  for (const argument of argv) {
    if (!argument.startsWith(prefix)) continue
    const value = argument.slice(prefix.length)
    if (value.length === 0 || value.length > 4096) return null
    if (!value.startsWith('/')) return null
    return value
  }
  return env.DSH_AGENT_CODEX_CLI !== undefined && env.DSH_AGENT_CODEX_CLI.startsWith('/')
    ? env.DSH_AGENT_CODEX_CLI
    : null
}

const isMain = process.argv[1] !== undefined && fileURLToPath(import.meta.url) === resolve(process.argv[1])

if (isMain) {
  runAgentAdapter().catch((cause) => {
    process.stderr.write(`${redactDiagnostic(cause)}\n`)
    process.exitCode = 1
  })
}
