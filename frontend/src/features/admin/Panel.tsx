import { AlertTriangle, Info } from 'lucide-react'
import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

export function Panel({
  title,
  description,
  actions,
  children,
  className,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={cn('rounded-xl border bg-card p-5', className)}>
      <header className="mb-4 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-semibold">{title}</h2>
          {description ? <p className="mt-1 text-sm text-muted-foreground">{description}</p> : null}
        </div>
        {actions}
      </header>
      {children}
    </section>
  )
}

const TONE = {
  warn: 'border-warn/30 bg-warn/10 [&_svg]:text-warn',
  destructive: 'border-destructive/30 bg-destructive/10 [&_svg]:text-destructive',
  info: 'border-info/30 bg-info/10 [&_svg]:text-info',
} as const

export function Callout({
  tone,
  title,
  children,
  className,
}: {
  tone: keyof typeof TONE
  title: string
  children?: ReactNode
  className?: string
}) {
  const Icon = tone === 'info' ? Info : AlertTriangle
  return (
    <div role={tone === 'info' ? 'status' : 'alert'} className={cn('flex gap-3 rounded-lg border p-3 text-sm', TONE[tone], className)}>
      <Icon className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0">
        <p className="font-medium">{title}</p>
        {children ? <div className="mt-0.5 break-words text-muted-foreground">{children}</div> : null}
      </div>
    </div>
  )
}
