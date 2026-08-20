import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

if (globalThis.ResizeObserver === undefined) globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver

afterEach(cleanup)
