import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'node',
    include: ['e2e/specs/**/*.installer.e2e.ts'],
    fileParallelism: false,
    testTimeout: 15 * 60_000,
    hookTimeout: 15 * 60_000,
  },
})
