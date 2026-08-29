import { defineConfig } from 'tsdown'

export default defineConfig({
  entry: {
    'agent-worker': 'src/cli.ts',
    adapters: 'src/adapters/index.ts',
    'providers/openai-compatible': 'src/providers/openai-compatible.ts',
    mcp: 'src/mcp/client.ts',
    'mcp-oauth': 'src/mcp/oauth.ts',
    'harness-bridge': 'src/harness-bridge.ts',
    'harness-host-handler': 'src/harness-host-handler.ts',
    'harness-task-session': 'src/harness-task-session.ts',
    'harness-capability-map': 'src/harness-capability-map.ts',
    'deepseek-harness-executor': 'src/deepseek-harness-executor.ts',
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
