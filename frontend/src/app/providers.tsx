import { QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { ConfirmProvider } from '@/components/app/ConfirmProvider'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'

import { queryClient } from './queryClient'

export function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={300}>
        <ConfirmProvider>
          {children}
          <Toaster position="bottom-right" closeButton />
        </ConfirmProvider>
      </TooltipProvider>
    </QueryClientProvider>
  )
}
