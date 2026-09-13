import { QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Direction } from 'radix-ui'

import { AppearanceEffect } from '@/components/app/AppearanceEffect'
import { ConfirmProvider } from '@/components/app/ConfirmProvider'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'

import { isRtl, useLocale } from '@/i18n'

import { queryClient } from './queryClient'

export function Providers({ children }: { children: ReactNode }) {
  // Radix menus and selects read the writing direction from here: keyboard
  // arrows, submenu sides and alignment all follow it.
  const dir = isRtl(useLocale((s) => s.locale)) ? 'rtl' : 'ltr'
  return (
    <QueryClientProvider client={queryClient}>
      <Direction.Provider dir={dir}>
        <TooltipProvider delayDuration={300}>
          <ConfirmProvider>
            <AppearanceEffect />
            {children}
            <Toaster position={dir === 'rtl' ? 'bottom-left' : 'bottom-right'} dir={dir} closeButton />
          </ConfirmProvider>
        </TooltipProvider>
      </Direction.Provider>
    </QueryClientProvider>
  )
}
