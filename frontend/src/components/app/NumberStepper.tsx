import { Minus, Plus } from 'lucide-react'
import { useState } from 'react'

import { cn } from '@/lib/utils'
import { useT } from '@/i18n'

/** A small number field with - and + buttons; typed values are clamped on commit. */
export function NumberStepper({
  label,
  value,
  min,
  max,
  onChange,
  className,
}: {
  label: string
  value: number
  min: number
  max: number
  onChange: (value: number) => void
  className?: string
}) {
  // What the user is typing, or null when they aren't - then the field shows
  // the value itself, so a change from the buttons or elsewhere shows at once.
  const t = useT()
  const [draft, setDraft] = useState<string | null>(null)

  const commit = (raw: string) => {
    const n = Number.parseInt(raw, 10)
    const next = Number.isNaN(n) ? value : Math.min(max, Math.max(min, n))
    setDraft(null)
    if (next !== value) onChange(next)
  }

  const button = 'grid h-full w-8 place-items-center text-muted-foreground transition-colors hover:text-foreground disabled:pointer-events-none disabled:opacity-35'

  return (
    <div
      role="group"
      aria-label={label}
      className={cn('flex h-9 items-center rounded-md border bg-field focus-within:border-ring', className)}
    >
      <button type="button" className={button} aria-label={t('stepper.decrease', { label: label.toLowerCase() })} disabled={value <= min} onClick={() => onChange(value - 1)}>
        <Minus className="size-3.5" />
      </button>
      <input
        aria-label={label}
        inputMode="numeric"
        value={draft ?? String(value)}
        onChange={(e) => setDraft(e.target.value.replace(/[^0-9]/g, ''))}
        onBlur={(e) => commit(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit(e.currentTarget.value)
          if (e.key === 'ArrowUp') {
            e.preventDefault()
            onChange(Math.min(max, value + 1))
          }
          if (e.key === 'ArrowDown') {
            e.preventDefault()
            onChange(Math.max(min, value - 1))
          }
        }}
        className="w-8 min-w-0 flex-1 bg-transparent text-center text-sm tabular-nums outline-none focus-visible:outline-none"
      />
      <button type="button" className={button} aria-label={t('stepper.increase', { label: label.toLowerCase() })} disabled={value >= max} onClick={() => onChange(value + 1)}>
        <Plus className="size-3.5" />
      </button>
    </div>
  )
}
