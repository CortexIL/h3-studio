import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderOptions } from '@testing-library/react'
import { StrictMode, type ReactElement, type ReactNode } from 'react'
import { MemoryRouter } from 'react-router'

import { ConfirmProvider } from '@/components/app/ConfirmProvider'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: 0 } },
  })
}

// StrictMode, as in main.tsx: it runs effects twice on purpose, so a cleanup
// that breaks a re-run (a revoked object URL, say) fails a test instead of
// only failing in the browser.
export function renderWithProviders(
  ui: ReactElement,
  { route = '/', client = makeQueryClient(), ...options }: RenderOptions & { route?: string; client?: QueryClient } = {},
) {
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <StrictMode>
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={[route]}>
            <TooltipProvider delayDuration={0}>
              <ConfirmProvider>
                {children}
                <Toaster />
              </ConfirmProvider>
            </TooltipProvider>
          </MemoryRouter>
        </QueryClientProvider>
      </StrictMode>
    )
  }
  return { client, ...render(ui, { wrapper: Wrapper, ...options }) }
}
