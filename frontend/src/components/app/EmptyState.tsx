import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: LucideIcon
  title: string
  description?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="grid justify-items-center gap-3 px-6 py-14 text-center">
      <div className="grid size-11 place-items-center rounded-full border bg-background/60 text-muted-foreground">
        <Icon className="size-5" />
      </div>
      <div className="grid gap-1">
        <p className="font-medium">{title}</p>
        {description ? <p className="max-w-sm text-sm text-balance text-muted-foreground">{description}</p> : null}
      </div>
      {action}
    </div>
  )
}
