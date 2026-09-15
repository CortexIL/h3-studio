import { useStatus } from '@/api/queries'
import type { PodState } from '@/api/types'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useT } from '@/i18n'
import { podStateLabel } from '@/lib/format'
import { cn } from '@/lib/utils'

const DOT: Record<PodState, string> = {
  off: 'bg-faint',
  booting: 'bg-warn animate-pulse',
  ready: 'bg-ok shadow-[0_0_0_3px_rgb(79_208_138/0.18)]',
  stopping: 'bg-warn',
  error: 'bg-destructive',
}

export function PodStatus() {
  const t = useT()
  const { data } = useStatus()
  const state = data?.pod.state
  // "Checking", not "Off", before the first answer: they mean different things.
  const ready = data?.pod.ready ?? 0
  const label = !state ? t('pod.checking') : ready > 1 ? t('pod.readyMany', { n: ready }) : podStateLabel(state)
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          tabIndex={0}
          aria-label={t('pod.aria', { label })}
          className="flex h-8 shrink-0 items-center gap-2 rounded-full border bg-background/40 px-2.5 text-xs sm:px-3"
        >
          <span className={cn('size-2 shrink-0 rounded-full', state ? DOT[state] : 'animate-pulse bg-faint')} />
          <span className="hidden text-muted-foreground sm:inline">{t('pod.gpu')}</span>
          {/* On a phone the dot carries the state; the label and tooltip still say it. */}
          <span className="hidden font-medium sm:inline">{label}</span>
        </div>
      </TooltipTrigger>
      <TooltipContent side="bottom" align="end" className="max-w-72">
        {data ? (
          <div className="grid gap-1">
            <span>{data.pod.detail}</span>
            {(data.pod.starting ?? 0) > 0 && ready > 0 ? (
              <span className="text-muted-foreground">{t('pod.startingMore', { n: data.pod.starting ?? 0 })}</span>
            ) : null}
            <span className="text-muted-foreground">
              {t('pod.queue', { queued: data.queue.total_queued, running: data.queue.total_running })}
            </span>
          </div>
        ) : (
          t('pod.checkingGpu')
        )}
      </TooltipContent>
    </Tooltip>
  )
}
