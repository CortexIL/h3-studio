import { Clapperboard, ListX, SearchX, WifiOff } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { useClearFinished, useReorderQueue } from '@/api/mutations'
import { useJobs } from '@/api/queries'
import type { Job } from '@/api/types'
import { useConfirm } from '@/components/app/confirm'
import { EmptyState } from '@/components/app/EmptyState'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { useViewerList } from '@/features/viewer/useClipViewer'
import { cn } from '@/lib/utils'

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
  const reorder = useReorderQueue()
  // The drag's identity lives in a ref, not in state: the drop handler must know
  // it the instant it fires, and the state below exists only to paint.
  const draggingRef = useRef<string | null>(null)
  const [dragging, setDragging] = useState<string | null>(null)
  const [over, setOver] = useState<string | null>(null)
  const endDrag = () => {
    draggingRef.current = null
    setDragging(null)
    setOver(null)
  }
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
  const queuedIds = useMemo(
    () => visible.filter((j) => j.status === 'queued').map((j) => j.id),
    [visible],
  )

  /** Put `id` where `target` sits in the list, and send the queue's own order. */
  const moveTo = (id: string, target: string) => {
    if (id === target) return
    const order = queuedIds.filter((x) => x !== id)
    const at = order.indexOf(target)
    order.splice(at < 0 ? order.length : at, 0, id)
    reorder.mutate([...order].reverse())
  }

  /** Alt + arrow keys, because a drag is not reachable from a keyboard. */
  const nudge = (id: string, by: -1 | 1) => {
    const from = queuedIds.indexOf(id)
    const to = from + by
    if (from < 0 || to < 0 || to >= queuedIds.length) return
    const order = [...queuedIds]
    order.splice(to, 0, ...order.splice(from, 1))
    reorder.mutate([...order].reverse())
  }

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
            {visible.map((job) => {
              const queued = job.status === 'queued'
              return (
                <div
                  key={job.id}
                  draggable={queued}
                  tabIndex={queued ? 0 : undefined}
                  aria-label={queued ? `Reorder ${job.prompt.slice(0, 60)}` : undefined}
                  aria-describedby={queued ? 'reorder-hint' : undefined}
                  onDragStart={(e) => {
                    if (!queued) return
                    draggingRef.current = job.id
                    // Firefox will not start a drag at all unless something is
                    // put on the dataTransfer.
                    e.dataTransfer.setData('text/plain', job.id)
                    e.dataTransfer.effectAllowed = 'move'
                    // Deferred by a frame on purpose. Setting it now would re-render
                    // the card faded *before* the browser photographs it for the
                    // drag image, and the thing following the cursor would be a
                    // half-transparent ghost of a dark card on a dark page - which
                    // is to say, nothing you can see.
                    requestAnimationFrame(() => setDragging(job.id))
                  }}
                  onDragOver={(e) => {
                    const from = draggingRef.current
                    if (!queued || !from || from === job.id) return
                    e.preventDefault()
                    e.dataTransfer.dropEffect = 'move'
                    setOver(job.id)
                  }}
                  onDrop={(e) => {
                    const from = draggingRef.current
                    if (!queued || !from) return
                    e.preventDefault()
                    moveTo(from, job.id)
                    endDrag()
                  }}
                  onDragEnd={endDrag}
                  data-drop-target={over === job.id && dragging !== job.id ? '' : undefined}
                  onKeyDown={(e) => {
                    if (!queued || !e.altKey) return
                    if (e.key === 'ArrowUp') {
                      e.preventDefault()
                      nudge(job.id, -1)
                    } else if (e.key === 'ArrowDown') {
                      e.preventDefault()
                      nudge(job.id, 1)
                    }
                  }}
                  className={cn(
                    'relative rounded-lg outline-none transition-opacity',
                    queued && 'cursor-grab focus-visible:ring-[3px] focus-visible:ring-ring/50 active:cursor-grabbing',
                    dragging === job.id && 'opacity-40',
                    // Where it will land, drawn in the gap above the card so
                    // nothing moves until the drop actually happens.
                    over === job.id && dragging !== job.id &&
                      'before:absolute before:inset-x-0 before:-top-1.5 before:h-0.5 before:rounded-full before:bg-primary before:content-[\'\']',
                  )}
                >
                  <JobCard job={job} />
                </div>
              )
            })}
            {queuedIds.length > 1 ? (
              <p id="reorder-hint" className="px-1 text-center text-2xs text-faint">
                Drag a waiting clip to change what renders next, or focus it and press Alt with the arrow keys.
              </p>
            ) : null}
          </div>
        )}
      </div>
    </section>
  )
}
