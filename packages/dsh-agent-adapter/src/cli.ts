import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'
import { runMockWorker, type MockWorkerIo } from './worker.js'
import { redactDiagnostic } from './redaction.js'

export async function runAgentAdapter(io: MockWorkerIo = {
  input: process.stdin,
  stdout: process.stdout,
  stderr: process.stderr,
}): Promise<void> {
  await runMockWorker(io)
}

const isMain = process.argv[1] !== undefined && fileURLToPath(import.meta.url) === resolve(process.argv[1])

if (isMain) {
  runAgentAdapter().catch((cause) => {
    process.stderr.write(`${redactDiagnostic(cause)}\n`)
    process.exitCode = 1
  })
}
