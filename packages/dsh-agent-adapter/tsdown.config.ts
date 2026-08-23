import { defineConfig } from 'tsdown'

export default defineConfig({
  entry: {
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
