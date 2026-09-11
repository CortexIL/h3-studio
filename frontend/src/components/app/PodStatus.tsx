import { useStatus } from '@/api/queries'
import type { PodState } from '@/api/types'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { POD_STATE_LABEL } from '@/lib/format'
import { cn } from '@/lib/utils'

const DOT: Record<PodState, string> = {
  off: 'bg-faint',
  booting: 'bg-warn animate-pulse',
  ready: 'bg-ok shadow-[0_0_0_3px_rgb(79_208_138/0.18)]',
  stopping: 'bg-warn',
  error: 'bg-destructive',
}

export function PodStatus() {
  const { data } = useStatus()
  const state = data?.pod.state
  // "Checking", not "Off", before the first answer: they mean different things.
  const label = state ? POD_STATE_LABEL[state] : 'Checking'
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          tabIndex={0}
          aria-label={`GPU ${label}`}
          className="flex h-8 shrink-0 items-center gap-2 rounded-full border bg-background/40 px-2.5 text-xs sm:px-3"
        >
          <span className={cn('size-2 shrink-0 rounded-full', state ? DOT[state] : 'animate-pulse bg-faint')} />
          <span className="hidden text-muted-foreground sm:inline">GPU</span>
          {/* On a phone the dot carries the state; the label and tooltip still say it. */}
          <span className="hidden font-medium sm:inline">{label}</span>
        </div>
      </TooltipTrigger>
      <TooltipContent side="bottom" align="end" className="max-w-72">
        {data ? (
          <div className="grid gap-1">
            <span>{data.pod.detail}</span>
            <span className="text-muted-foreground">
              {data.queue.total_queued} in the shared queue · {data.queue.total_running} generating
            </span>
          </div>
        ) : (
          'Checking the GPU…'
        )}
      </TooltipContent>
    </Tooltip>
  )
}
