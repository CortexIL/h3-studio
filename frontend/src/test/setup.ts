import '@testing-library/jest-dom/vitest'

import { cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll } from 'vitest'

import { resetRedirectGuard } from '@/api/client'

import { server } from './server'

// Tripwire: the app must never fall back to the browser's own dialogs. Any
// code path that calls one fails its test instead of passing silently.
for (const name of ['alert', 'confirm', 'prompt'] as const) {
  Object.defineProperty(window, name, {
    configurable: true,
    value: () => {
      throw new Error(`window.${name}() is banned - use the app's dialogs and toasts`)
    },
  })
}

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => {
  cleanup()
  server.resetHandlers()
  resetRedirectGuard()
})
afterAll(() => server.close())
