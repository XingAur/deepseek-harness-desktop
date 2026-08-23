import { execFileSync, spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = resolve(packageRoot, '..', '..')

describe('@dsh/agent-adapter package exports', () => {
  it('lets a TypeScript consumer resolve every public export', () => {
    const consumerRoot = mkdtempSync(join(tmpdir(), 'dsh-agent-adapter-consumer-'))
    try {
      execFileSync('npm', ['run', 'agent:build'], { cwd: repositoryRoot, stdio: 'pipe' })
      const packageLink = join(consumerRoot, 'node_modules', '@dsh', 'agent-adapter')
      mkdirSync(dirname(packageLink), { recursive: true })
      symlinkSync(packageRoot, packageLink, 'dir')
      const nodeTypesLink = join(consumerRoot, 'node_modules', '@types', 'node')
      mkdirSync(dirname(nodeTypesLink), { recursive: true })
      symlinkSync(join(repositoryRoot, 'node_modules', '@types', 'node'), nodeTypesLink, 'dir')
      writeFileSync(join(consumerRoot, 'tsconfig.json'), JSON.stringify({
        compilerOptions: { strict: true, noEmit: true, module: 'NodeNext', moduleResolution: 'NodeNext', types: ['node'] },
        include: ['index.ts'],
      }))
      writeFileSync(join(consumerRoot, 'index.ts'), [
        "import { PROTOCOL_VERSION, type AgentAdapterProtocolVersion, type ProtocolFrame } from '@dsh/agent-adapter'",
        "import { runMockWorker, type MockWorkerIo } from '@dsh/agent-adapter/worker'",
        'const version: AgentAdapterProtocolVersion = PROTOCOL_VERSION',
        'const frame = null as unknown as ProtocolFrame',
        'const io = null as unknown as MockWorkerIo',
        'void version; void frame; void runMockWorker(io)',
      ].join('\n'))

      const typecheck = spawnSync(process.execPath, [join(repositoryRoot, 'node_modules', 'typescript', 'bin', 'tsc'), '-p', join(consumerRoot, 'tsconfig.json')], { cwd: consumerRoot, encoding: 'utf8' })
      expect(typecheck.status, `${typecheck.stdout}${typecheck.stderr}`).toBe(0)
    } finally {
      rmSync(consumerRoot, { recursive: true, force: true })
      rmSync(join(packageRoot, 'lib'), { recursive: true, force: true })
    }
  })
})
