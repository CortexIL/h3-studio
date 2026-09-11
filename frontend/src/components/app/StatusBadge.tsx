import type { JobStatus } from '@/api/types'
import { JOB_STATUS_LABEL } from '@/lib/format'
import { cn } from '@/lib/utils'

const TONE: Record<JobStatus, string> = {
  queued: 'bg-faint',
  running: 'bg-info animate-pulse',
  done: 'bg-ok',
  failed: 'bg-destructive',
  cancelled: 'bg-faint',
}

export function StatusBadge({ status }: { status: JobStatus }) {
  return (
    <span className="inline-flex h-6 items-center gap-1.5 rounded-full border px-2 text-2xs font-medium">
      <span className={cn('size-1.5 rounded-full', TONE[status])} />
      {JOB_STATUS_LABEL[status]}
    </span>
  )
}
