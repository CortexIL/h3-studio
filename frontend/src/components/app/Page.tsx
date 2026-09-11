import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

/** The standard page frame: a readable column with a title and a one-line purpose. */
export function Page({
  title,
  description,
  actions,
  width = 'narrow',
  children,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
  width?: 'narrow' | 'wide'
  children: ReactNode
}) {
  return (
    <div className={cn('mx-auto w-full px-4 py-8 sm:px-6', width === 'wide' ? 'max-w-6xl' : 'max-w-3xl')}>
      <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
          {description ? <p className="mt-1 text-muted-foreground">{description}</p> : null}
        </div>
        {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
      </header>
      <div className="flex flex-col gap-6">{children}</div>
    </div>
  )
}
