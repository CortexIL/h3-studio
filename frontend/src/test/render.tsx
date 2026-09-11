import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderOptions } from '@testing-library/react'
import type { ReactElement, ReactNode } from 'react'
import { MemoryRouter } from 'react-router'

import { ConfirmProvider } from '@/components/app/ConfirmProvider'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: 0 } },
  })
}

export function renderWithProviders(
  ui: ReactElement,
  { route = '/', client = makeQueryClient(), ...options }: RenderOptions & { route?: string; client?: QueryClient } = {},
) {
  function Wrapper({ children }: { children: ReactNode }) {
    return (
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
    )
  }
  return { client, ...render(ui, { wrapper: Wrapper, ...options }) }
}
