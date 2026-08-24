import { defineConfig } from 'tsdown'

export default defineConfig({
  entry: {
    'agent-worker': 'src/cli.ts',
    adapters: 'src/adapters/index.ts',
    mcp: 'src/mcp/client.ts',
    'mcp-oauth': 'src/mcp/oauth.ts',
    protocol: 'src/protocol.ts',
    redaction: 'src/redaction.ts',
    worker: 'src/worker.ts',
  },
  outDir: 'lib',
  format: 'esm',
  platform: 'node',
  target: 'node22',
  fixedExtension: false,
  dts: false,
  clean: true,
  sourcemap: true,
})
