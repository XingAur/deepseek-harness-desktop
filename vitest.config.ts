import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    // Several packaging tests build the shared workspace output under packages/*/lib.
    // Keep test files serial so one test cannot clean that output while another consumes it.
    fileParallelism: false,
    include: [
      'src/**/*.test.{ts,tsx}',
      'scripts/**/*.test.ts',
      'packages/dsh-agent-adapter/src/**/*.test.ts',
      'e2e/support/**/*.test.ts',
    ],
    css: true,
  },
})
