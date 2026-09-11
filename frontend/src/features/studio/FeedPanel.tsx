import { Clapperboard, ListX, SearchX, WifiOff } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { useClearFinished } from '@/api/mutations'
import { useJobs } from '@/api/queries'
import type { Job } from '@/api/types'
import { useConfirm } from '@/components/app/confirm'
import { EmptyState } from '@/components/app/EmptyState'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { useViewerList } from '@/features/viewer/useClipViewer'

import { JobCard } from './JobCard'

type Filter = 'all' | 'active' | 'ready' | 'failed'

const MATCH: Record<Filter, (j: Job) => boolean> = {
  all: () => true,
  active: (j) => j.status === 'queued' || j.status === 'running',
  ready: (j) => j.status === 'done',
  failed: (j) => j.status === 'failed' || j.status === 'cancelled',
}

const LABEL: Record<Filter, string> = { all: 'All', active: 'Active', ready: 'Ready', failed: 'Failed' }

export function FeedPanel() {
  const jobs = useJobs()
  const clear = useClearFinished()
  const confirm = useConfirm()
  const [filter, setFilter] = useState<Filter>('all')

  const list = jobs.data?.jobs
  const counts = useMemo(() => {
    const all = list ?? []
    return {
      all: all.length,
      active: all.filter(MATCH.active).length,
      ready: all.filter(MATCH.ready).length,
      failed: all.filter(MATCH.failed).length,
    }
  }, [list])
  const visible = useMemo(() => (list ?? []).filter(MATCH[filter]), [list, filter])
  // The viewer's arrows step through the finished clips in this list.
  useEffect(() => {
    useViewerList.getState().setIds(visible.filter((j) => j.status === 'done' && j.video_url).map((j) => j.id))
  }, [visible])
  const finished = counts.ready + counts.failed

  const onClear = () =>
    void confirm({
      title: 'Clear finished jobs?',
      description: 'Finished clips leave this list but stay in your Archive. Failed and cancelled jobs are removed.',
      confirmLabel: 'Clear',
      action: () => clear.mutateAsync(),
    })

  return (
    <section aria-label="Queue and history" className="flex min-h-0 flex-1 flex-col rounded-xl border bg-card">
      <header className="flex min-h-12 shrink-0 flex-wrap items-center gap-2 border-b px-4 py-2">
        <h2 className="text-sm font-semibold">Queue &amp; history</h2>
        <div className="flex-1" />
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          value={filter}
          onValueChange={(v) => v && setFilter(v as Filter)}
          aria-label="Filter the list"
          // On a phone the filters take their own row under the title.
          className="order-last sm:order-none"
        >
          {(Object.keys(LABEL) as Filter[]).map((f) => (
            <ToggleGroupItem key={f} value={f} className="gap-1.5 px-2.5 text-xs">
              {LABEL[f]}
              <span className="text-2xs text-muted-foreground tabular-nums">{counts[f]}</span>
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <Button size="sm" variant="ghost" onClick={onClear} disabled={!finished || clear.isPending} aria-label="Clear finished">
          <ListX /> <span className="hidden sm:inline">Clear finished</span>
        </Button>
      </header>

      <div className="@container min-h-0 flex-1 overflow-y-auto p-3">
        {jobs.isPending ? (
          <div className="grid gap-3" aria-label="Loading your jobs">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-40 w-full rounded-lg" />
            ))}
          </div>
        ) : jobs.isError && !list ? (
          <EmptyState
            icon={WifiOff}
            title="Couldn't load your jobs"
            description="The server didn't answer. It will keep trying on its own."
            action={<Button size="sm" variant="outline" onClick={() => void jobs.refetch()}>Try now</Button>}
          />
        ) : visible.length === 0 ? (
          counts.all === 0 ? (
            <EmptyState
              icon={Clapperboard}
              title="Nothing here yet"
              description="Write a prompt on the left and add it to the queue. Finished clips also land in your Archive."
            />
          ) : (
            <EmptyState icon={SearchX} title={`Nothing ${LABEL[filter].toLowerCase()} right now`} action={
              <Button size="sm" variant="outline" onClick={() => setFilter('all')}>Show everything</Button>
            } />
          )
        ) : (
          <div className="grid gap-3">
            {visible.map((job) => (
              <JobCard key={job.id} job={job} />
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
